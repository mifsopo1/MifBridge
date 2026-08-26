// MifBridge — LEVEL STREAMING control: sublevel composition (editor) and level instances (PIE).
//
// The gap this closes, in the user's words: "No level streaming control. I can't load or unload a
// level instance from the bridge, which is why in-game test setup needs a Lua command instead."
// Before this file the bridge could make a whole map (new_level/save_level_as/load_level) and it
// could place actors in it — but it could not COMPOSE maps out of sublevels, and it could not stream
// a level in or out while PIE was running. Test setup therefore had to leave the bridge entirely.
//
// Two halves, deliberately sharing one read endpoint:
//
//   * EDITOR sublevels (UEditorLevelUtils) — list / add / remove / visibility / streaming-class /
//     set-current. This is the Levels panel, driven over HTTP.
//   * RUNTIME level instances (ULevelStreamingDynamic::LoadLevelInstance) — load/unload a level into
//     the LIVE PIE world. This is the actual "test setup" need.
//
// `list_sublevels {world:"editor"|"pie"}` reads BOTH, so there is exactly one poll endpoint for
// streaming state rather than two that drift (docs/00_ARCHITECTURE.md, "one source of truth").
//
// ── Why so much of this file is pre-validation ────────────────────────────────────────────────
// FHttpServerModule is an FTSTickerObjectBase ticked on the GAME thread (docs/02_GOTCHAS.md §8).
// A modal window spins its own loop, the tick stops, and the bridge stops reading the socket — every
// call then times out with no response, indistinguishable from a crash, and in an unattended run it
// never recovers. That bit this project live on 2026-07-27 (a third-party plugin's welcome popup).
//
// The engine functions this file wraps contain FOUR reachable ways to hang or kill the editor, all
// verified in D:/UE532 source and all made UNREACHABLE by a pre-check here:
//
//   1. AddLevelToWorld_Internal        EditorLevelUtils.cpp:441-451  FSuppressableWarningDialog::
//                                      ShowModal() when the package is already in the world or IS
//                                      the persistent level.
//   2. MakeLevelCurrent(ULevel*,bool)  EditorLevelUtils.cpp:555-588  FMessageDialog::Open when the
//                                      level is locked and bEvenIfLocked is false.
//   3. RemoveLevelsFromWorld           EditorLevelUtils.cpp:830-834  FMessageDialog::Open when any
//                                      level is locked; and :894-897 a second FMessageDialog::Open
//                                      when the package unload fails (only reachable for a DIRTY
//                                      package — PackageTools.cpp:390 is the sole writer of
//                                      OutErrorMessage).
//   4. SetStreamingClassForLevel       EditorLevelUtils.cpp:524-525  check(Level) on
//                                      InLevel->GetLoadedLevel() — a hard assert, not an error, if
//                                      the sublevel is not loaded.
//
// Every one of those is pre-checked below and answered with a structured error, so the engine's
// dialog/assert branch is never entered. The pre-checks are re-run inside the deferred lambdas too:
// state can change between the HTTP call and the next tick, and "we checked a frame ago" is not a
// guarantee.
//
// ── NOT four: three more exist on the wrapped paths, and are NOT all closed ────────────────────
// The list above claimed to be exhaustive and was not. Verified against D:/UE532:
//
//   5. AddLevelToWorld itself         EditorLevelUtils.cpp:387-388  unconditional
//                                     FScopedSlowTask ... MakeDialog(). Reached by add_sublevel and
//                                     by set_sublevel_streaming (via SetStreamingClassForLevel,
//                                     :531). It is a PROGRESS window, not a user-blocking modal, but
//                                     while it is up FFeedbackContextEditor ticks Slate ONLY
//                                     (FeedbackContextEditor.cpp:419-441) and never FTSTicker — so
//                                     the HTTP server is unreachable for the whole level load. Both
//                                     call sites are deferred, so no response is left pending, but
//                                     CONCURRENT requests stall. Not closable from here; declared.
//
//   6. check(Level->OwningWorld)      EditorLevelUtils.cpp:527, inside SetStreamingClassForLevel.
//                                     The bridge pre-checked check(InLevel) (:516) and check(Level)
//                                     (:525) but not this third one. CLOSED — the deferred lambda in
//                                     set_sublevel_streaming now tests OwningWorld too.
//
//   7. check(Level->bIsVisible == bShouldBeVisible)
//                                     EditorLevelUtils.cpp:1237/:1255, immediately after the flush,
//                                     reachable in principle from set_sublevel_visibility. It holds
//                                     for editor worlds (World.cpp:3121 —
//                                     bConsiderTimeLimit &= bMatchStarted && bIsGameWorld, so
//                                     AddToWorld never returns partial in-editor), so it is not a
//                                     live crash. Consequence worth knowing: the graceful
//                                     "SetLevelVisibility did not take" branch below is DEAD CODE —
//                                     the engine asserts on that exact condition before it can
//                                     return. It is marked as such at the site; do not treat it as a
//                                     safety net.
//
// ── Blocking, declared rather than closed ─────────────────────────────────────────────────────
// set_sublevel_visibility and set_current_sublevel call UEditorLevelUtils::SetLevelVisibility, which
// opens its OWN FScopedTransaction (EditorLevelUtils.cpp:1198) and runs
// Level->OwningWorld->FlushLevelStreaming() -> FlushAsyncLoading() inside a
// while (bLevelsPendingVisibility) loop (World.cpp:4533, :4544-4554). The ticker is stopped for its
// duration. Both endpoints are now SELF-MANAGED (MifBridgeCommon.cpp) so that cascade is no longer
// captured by RunEndpoint's blanket transaction — that was the correctness bug. The flush itself is
// bounded in an editor world and both endpoints still answer synchronously; they are NOT routed
// through the op log, because turning a working synchronous verb into a poll-based one is a contract
// change, not a bug fix. If a caller sees the bridge pause on these, this is why.
//
// ── Why the world-mutating verbs defer a tick ─────────────────────────────────────────────────
// new_level/load_level already defer via GEditor->GetTimerManager()->SetTimerForNextTick
// (MifBridgeWorld.cpp:144 and :204) because swapping a UWorld from inside a tick trips
// `Assertion failed: !LevelList.Contains(TickTaskLevel)` (TickTaskManager.cpp:1458). add_sublevel /
// remove_sublevel / set_sublevel_streaming are the same hazard class — they add or destroy a ULevel
// in the open world — and remove_sublevel is worse: RemoveLevelsFromWorld ends in
// GEditor->Cleanse (a forced GC, EditorLevelUtils.cpp:909) and then a stale-reference sweep that is
// **FATAL** when the transaction buffer was reset (EditorLevelUtils.cpp:929-937,
// EPrintStaleReferencesOptions::Fatal). Running that with our own call frame still on the stack is
// exactly the situation that sweep exists to kill the editor over. So: validate synchronously,
// mutate on the next tick, report through the op log.
//
// ── Why there is an op log ────────────────────────────────────────────────────────────────────
// A deferred mutation cannot put its result in its own HTTP response. Silently dropping it would
// reproduce the failure docs/02_GOTCHAS.md warns about ("Never silence a mutating call"), so every
// deferred verb returns an `opId` and records its outcome into a small ring that `list_sublevels`
// reports as `ops[]`. Poll until the entry for your opId has completed:true, then read its ok/error.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Editor.h"                                  // GEditor
#include "EditorLevelUtils.h"                        // UEditorLevelUtils (all UNREALED_API)
#include "Engine/Engine.h"                           // GEngine->GetWorldContexts (PIE worlds)
#include "Engine/Level.h"
#include "Engine/LevelStreaming.h"
#include "Engine/LevelStreamingAlwaysLoaded.h"
#include "Engine/LevelStreamingDynamic.h"
#include "Engine/World.h"
#include "WorldPartition/DataLayer/DataLayerInstance.h"
#include "WorldPartition/DataLayer/DataLayerManager.h"
#include "GameFramework/Actor.h"                     // AActor must be COMPLETE for IsValid()'s UObject* conversion
#include "LevelUtils.h"                              // FLevelUtils::FindStreamingLevel / IsLevelLocked
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "TimerManager.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"                  // IsValid

namespace MifBridge
{
	namespace
	{
		// ── World resolution ──────────────────────────────────────────────────────────────────

		// EditorWorld() moved to MifBridgeCommon.cpp (declared in MifBridgeHandlers.h). It was defined
		// verbatim here AND in MifBridgeWorld.cpp, both inside `namespace MifBridge { namespace { } }`,
		// surviving only because file sizes put Streaming at the end of unity blob 2 and World in
		// blob 3. ~8 KB of source growth anywhere in blob 1 moves that boundary and turns the pair into
		// `error C2084: function already has a body`. Do NOT re-add a file-local copy.

		// MifBridge::CollectPIEWorlds (MifBridgeHandlers.h, defined in MifBridgeCommon.cpp) is the ONE
		// implementation, shared with list_pie_actors — do NOT re-add a file-local copy here. Same rule
		// it encodes: with RunUnderOneProcess and >1 client there are SEVERAL PIE worlds and
		// GEditor->PlayWorld is only ever ONE of them, so streaming a level into "the" PIE world
		// without saying which is a silent wrong answer.

		/** Pick a PIE world by net role, mirroring list_pie_actors' netMode contract exactly. */
		UWorld* ResolvePIEWorld(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
		{
			TArray<UWorld*> PIEWorlds;
			CollectPIEWorlds(PIEWorlds);
			if (PIEWorlds.Num() == 0)
			{
				Fail(Out, TEXT("no PIE world — not playing. start_pie, then poll pie_status until state=='running'."));
				return nullptr;
			}
			const FString WantRole = JStr(In, TEXT("netMode"), TEXT("server")).ToLower();
			if (WantRole != TEXT("server") && WantRole != TEXT("client") && WantRole != TEXT("any"))
			{
				Fail(Out, FString::Printf(
					TEXT("unknown netMode '%s' — accepted: server, client, any"), *WantRole));
				return nullptr;
			}
			for (UWorld* W : PIEWorlds)
			{
				const bool bIsServer = (W->GetNetMode() != NM_Client);
				if (WantRole == TEXT("any")
					|| (WantRole == TEXT("server") && bIsServer)
					|| (WantRole == TEXT("client") && !bIsServer))
				{
					return W;
				}
			}
			Fail(Out, FString::Printf(
				TEXT("no PIE world matching netMode '%s' (accepted: server, client, any)"), *WantRole));
			return nullptr;
		}

		// ── Package path grammar ──────────────────────────────────────────────────────────────

		// "/Game/Maps/Foo.Foo" or "/Game/Maps/Foo.umap" -> "/Game/Maps/Foo". Siblings this
		// deliberately duplicates: PackagePathToMapFilename (MifBridgeWorld.cpp:58). That one is
		// file-local in a file this batch does not own; when MifBridgeWorld.cpp is next edited the
		// two should collapse into one declaration in MifBridgeHandlers.h.
		bool NormalizeLevelPackagePath(const FString& Raw, FString& OutPackageName, FString& OutError)
		{
			FString Clean = Raw.TrimStartAndEnd();
			Clean.RemoveFromEnd(TEXT(".umap"));
			int32 Dot = INDEX_NONE;
			if (Clean.FindChar(TEXT('.'), Dot))
			{
				Clean = Clean.Left(Dot);
			}
			if (!Clean.StartsWith(TEXT("/")))
			{
				OutError = FString::Printf(
					TEXT("'%s' is not a package path — expected something like /Game/Maps/MyLevel"), *Raw);
				return false;
			}
			FText Reason;
			if (!FPackageName::IsValidLongPackageName(Clean, /*bIncludeReadOnlyRoots*/ false, &Reason))
			{
				OutError = FString::Printf(TEXT("'%s' is not a valid package path: %s"), *Raw, *Reason.ToString());
				return false;
			}
			OutPackageName = Clean;
			return true;
		}

		/** Reads the path parameter under any accepted spelling and normalizes it. */
		bool ReadLevelPath(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
			const TCHAR* Example, FString& OutPackageName)
		{
			const FString Raw = JStrAny(In, { TEXT("path"), TEXT("packagePath"), TEXT("level") });
			if (Raw.IsEmpty())
			{
				Fail(Out, FString::Printf(TEXT("path is required, e.g. \"%s\" (aliases: packagePath, level)"), Example));
				return false;
			}
			FString Error;
			if (!NormalizeLevelPackagePath(Raw, OutPackageName, Error))
			{
				Fail(Out, Error);
				return false;
			}
			return true;
		}

		// ── Streaming class grammar ───────────────────────────────────────────────────────────

		bool ParseStreamingClass(const FString& Raw, TSubclassOf<ULevelStreaming>& OutClass, FString& OutError)
		{
			const FString L = Raw.TrimStartAndEnd().ToLower();
			if (L == TEXT("alwaysloaded") || L == TEXT("always_loaded") || L == TEXT("always"))
			{
				OutClass = ULevelStreamingAlwaysLoaded::StaticClass();
				return true;
			}
			if (L == TEXT("dynamic"))
			{
				OutClass = ULevelStreamingDynamic::StaticClass();
				return true;
			}
			OutError = FString::Printf(
				TEXT("unknown streamingClass '%s' — accepted: alwaysloaded, dynamic"), *Raw);
			return false;
		}

		const TCHAR* StreamingClassName(const UClass* Class)
		{
			if (Class == ULevelStreamingAlwaysLoaded::StaticClass()) { return TEXT("alwaysloaded"); }
			if (Class == ULevelStreamingDynamic::StaticClass())      { return TEXT("dynamic"); }
			return TEXT("other");
		}

		// ── Sublevel resolution ───────────────────────────────────────────────────────────────

		// Primary match goes through the SAME call the engine's own already-present test uses
		// (FLevelUtils::FindStreamingLevel, LevelUtils.h:44), so "the bridge thinks it isn't there
		// but AddLevelToWorld thinks it is" — the modal at EditorLevelUtils.cpp:441-451 — cannot
		// happen. The leaf-name fallback is a convenience only, and refuses to guess when ambiguous.
		ULevelStreaming* FindSublevel(UWorld* World, const FString& PackageName, FString& OutError)
		{
			if (!World)
			{
				OutError = TEXT("no world");
				return nullptr;
			}
			if (ULevelStreaming* Exact = FLevelUtils::FindStreamingLevel(World, *PackageName))
			{
				return Exact;
			}
			const FString Leaf = FPackageName::GetShortName(PackageName);
			TArray<ULevelStreaming*> LeafHits;
			for (ULevelStreaming* LS : World->GetStreamingLevels())
			{
				if (!LS) { continue; }
				const FString Candidate = LS->GetWorldAssetPackageFName().ToString();
				if (FPackageName::GetShortName(Candidate).Equals(Leaf, ESearchCase::IgnoreCase))
				{
					LeafHits.Add(LS);
				}
			}
			if (LeafHits.Num() == 1)
			{
				return LeafHits[0];
			}
			if (LeafHits.Num() > 1)
			{
				TArray<FString> Names;
				for (ULevelStreaming* LS : LeafHits) { Names.Add(LS->GetWorldAssetPackageFName().ToString()); }
				OutError = FString::Printf(
					TEXT("'%s' is ambiguous — %d sublevels share that leaf name: %s. Pass the full package path."),
					*PackageName, LeafHits.Num(), *FString::Join(Names, TEXT(", ")));
				return nullptr;
			}
			OutError = FString::Printf(
				TEXT("'%s' is not a sublevel of the open world — list_sublevels shows what is"), *PackageName);
			return nullptr;
		}

		// ── Deferred-op log ───────────────────────────────────────────────────────────────────
		// A deferred mutation runs after its HTTP response has already been sent, so its result has
		// nowhere to go. Dropping it would be exactly the silent-mutating-call failure
		// docs/02_GOTCHAS.md warns about, so it lands here and list_sublevels reports it.
		// Game-thread only (RunEndpoint and the editor timer manager both run there), so no lock.

		struct FStreamingOp
		{
			int32   OpId = 0;
			FString Endpoint;
			FString Path;
			bool    bCompleted = false;
			bool    bOk = false;
			FString Error;
			FString Detail;
		};

		TArray<FStreamingOp>& OpLog()
		{
			static TArray<FStreamingOp> Log;
			return Log;
		}

		int32 BeginOp(const TCHAR* Endpoint, const FString& Path)
		{
			static int32 Counter = 0;
			FStreamingOp Op;
			Op.OpId = ++Counter;
			Op.Endpoint = Endpoint;
			Op.Path = Path;
			OpLog().Add(Op);
			// Bounded so a long editor session cannot grow this without limit. It used to evict the
			// OLDEST entry unconditionally, which could drop an op whose lambda had not run yet: its
			// outcome then vanished, list_sublevels.ops[] never contained it, and the caller polled
			// forever — the "never silence a mutating call" failure, in the very mechanism that exists
			// to prevent it. Only COMPLETED entries are evictable; if 16 are all still pending the ring
			// grows instead, because a pending op is data nobody else has.
			for (int32 i = 0; OpLog().Num() > 16 && i < OpLog().Num(); )
			{
				if (OpLog()[i].bCompleted) { OpLog().RemoveAt(i); }
				else { ++i; }
			}
			return Op.OpId;
		}

		void FinishOp(int32 OpId, bool bOk, const FString& Error, const FString& Detail = FString())
		{
			// Walks the ring and returns when the id is gone. With the eviction rule above that should
			// now be impossible; log loudly if it ever happens rather than dropping a mutating call's
			// outcome on the floor, which is what it used to do in silence.
			bool bFound = false;
			for (FStreamingOp& Op : OpLog())
			{
				if (Op.OpId == OpId)
				{
					bFound = true;
					Op.bCompleted = true;
					Op.bOk = bOk;
					Op.Error = Error;
					Op.Detail = Detail;
					UE_LOG(LogMifBridge, Log, TEXT("%s op %d (%s): %s%s"),
						*Op.Endpoint, OpId, *Op.Path, bOk ? TEXT("ok") : TEXT("FAILED "), *Error);
					return;
				}
			}
			if (!bFound)
			{
				UE_LOG(LogMifBridge, Error,
					TEXT("streaming op %d completed (%s%s) but its ring entry is GONE — its result cannot be reported ")
					TEXT("through list_sublevels.ops[] and any caller polling for it will poll forever"),
					OpId, bOk ? TEXT("ok") : TEXT("FAILED "), *Error);
			}
		}

		// ── Serialization ─────────────────────────────────────────────────────────────────────

		// Vec3 moved to MifBridgeCommon.cpp (declared in MifBridgeHandlers.h) — see the note in
		// MifBridgeSpatial.cpp: this 3-double form and Spatial's FVector form were already sharing
		// unity blob 2 and one overload set.

		int32 CountValidActors(ULevel* Level)
		{
			if (!Level) { return 0; }
			int32 N = 0;
			for (AActor* A : Level->Actors)
			{
				if (A && IsValid(A)) { ++N; }
			}
			return N;
		}

		TSharedRef<FJsonObject> SerializeSublevel(ULevelStreaming* LS)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			ULevel* Level = LS->GetLoadedLevel();

			J->SetStringField(TEXT("packageName"), LS->GetWorldAssetPackageFName().ToString());
			// The ULevelStreaming object path is the handle set_property takes for LevelTransform,
			// EditorStreamingVolumes and the rest — see docs/audit/work/F_world_level.md
			// "Compositions". set_sublevel_streaming REPLACES this object, so it is reported both
			// before and after that call.
			J->SetStringField(TEXT("objectPath"), LS->GetPathName());
			J->SetStringField(TEXT("streamingClass"), StreamingClassName(LS->GetClass()));
			J->SetStringField(TEXT("streamingClassPath"), LS->GetClass()->GetPathName());
			if (!LS->PackageNameToLoad.IsNone())
			{
				// For a PIE level instance this is the on-disk source map; packageName is the
				// per-instance (PIE-renamed) name. Without both, an unload call cannot be addressed.
				J->SetStringField(TEXT("sourcePackage"), LS->PackageNameToLoad.ToString());
			}

			J->SetBoolField(TEXT("loaded"), LS->IsLevelLoaded());
			J->SetBoolField(TEXT("visible"), LS->IsLevelVisible());
			J->SetBoolField(TEXT("shouldBeLoaded"), LS->ShouldBeLoaded());
			J->SetBoolField(TEXT("shouldBeVisible"), LS->GetShouldBeVisibleFlag());
#if WITH_EDITORONLY_DATA
			J->SetBoolField(TEXT("shouldBeVisibleInEditor"), LS->GetShouldBeVisibleInEditor());
#endif
			J->SetBoolField(TEXT("requestingUnload"), LS->GetIsRequestingUnloadAndRemoval());
			// ::EnumToString(ELevelStreamingState) is ENGINE_API, LevelStreaming.h:119 — the engine's
			// own name for the state, so a caller can match it against engine logs verbatim. Called
			// fully qualified: it is a GLOBAL free function and we are inside namespace MifBridge.
			J->SetStringField(TEXT("state"), ::EnumToString(LS->GetLevelStreamingState()));
			// The single number that answers "is the async work finished for this level".
			J->SetBoolField(TEXT("pending"), LS->IsStreamingStatePending());
			J->SetBoolField(TEXT("locked"), LS->bLocked);

			if (Level)
			{
				J->SetBoolField(TEXT("isCurrent"), Level->IsCurrentLevel());
				J->SetBoolField(TEXT("lightingScenario"), Level->bIsLightingScenario);
				J->SetNumberField(TEXT("actorCount"), CountValidActors(Level));
				if (const UPackage* Pkg = Level->GetOutermost())
				{
					J->SetBoolField(TEXT("dirty"), Pkg->IsDirty());
				}
			}
			else
			{
				J->SetBoolField(TEXT("isCurrent"), false);
			}

			const FTransform& T = LS->LevelTransform;
			TSharedRef<FJsonObject> Xf = MakeShared<FJsonObject>();
			const FVector Loc = T.GetLocation();
			const FRotator Rot = T.Rotator();
			const FVector Scale = T.GetScale3D();
			Xf->SetObjectField(TEXT("location"), Vec3(Loc.X, Loc.Y, Loc.Z));
			Xf->SetObjectField(TEXT("rotation"), Vec3(Rot.Pitch, Rot.Yaw, Rot.Roll));
			Xf->SetObjectField(TEXT("scale"), Vec3(Scale.X, Scale.Y, Scale.Z));
			J->SetObjectField(TEXT("transform"), Xf);

			return J;
		}
	}

	// --- list_sublevels ------------------------------------------------------
	//   in:  { world?: "editor"|"pie", netMode?: "server"|"client"|"any" }
	//   out: { world, worldName, isPartitioned, currentLevel, persistent{...}, count, loadedCount,
	//          visibleCount, pendingCount, ready, sublevels[...], ops[...] }
	//
	// The read half every mutation in this file verifies against, AND the poll endpoint for all of
	// them — streaming state changes land across frames, so nothing here blocks and `pending`/
	// `ready` are how a caller learns the transition finished.

	// EDataLayerRuntimeState has no engine-provided string form that is stable across versions, so it is
	// spelled out here rather than reflected - a UEnum lookup would report the C++ identifier and change
	// shape if the enum ever moves.
	static const TCHAR* MifDataLayerStateName(EDataLayerRuntimeState State)
	{
		switch (State)
		{
		case EDataLayerRuntimeState::Unloaded:  return TEXT("unloaded");
		case EDataLayerRuntimeState::Loaded:    return TEXT("loaded");
		case EDataLayerRuntimeState::Activated: return TEXT("activated");
		default:                                return TEXT("unknown");
		}
	}
	void H_list_sublevels(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("world"), TEXT("netMode") },
			TEXT("world (\"editor\"|\"pie\"), netMode (\"server\"|\"client\"|\"any\", only meaningful with world:\"pie\")")))
		{
			return;
		}

		const FString WantWorld = JStr(In, TEXT("world"), TEXT("editor")).ToLower();
		UWorld* World = nullptr;
		if (WantWorld == TEXT("editor"))
		{
			World = EditorWorld();
			if (!World)
			{
				Fail(Out, TEXT("no editor world"));
				return;
			}
		}
		else if (WantWorld == TEXT("pie"))
		{
			World = ResolvePIEWorld(In, Out);
			if (!World)
			{
				return;   // ResolvePIEWorld already wrote the failure
			}
		}
		else
		{
			Fail(Out, FString::Printf(
				TEXT("unknown world '%s' — accepted: editor, pie"), *WantWorld));
			return;
		}

		Out->SetStringField(TEXT("world"), WantWorld);
		Out->SetStringField(TEXT("worldName"), World->GetName());
		// A World Partition world legitimately has ZERO streaming levels in-editor (its cells are
		// not ULevelStreaming), so count:0 must not read as "the query failed" — say which it is.
		Out->SetBoolField(TEXT("isPartitioned"), World->IsPartitionedWorld());

		if (ULevel* Persistent = World->PersistentLevel)
		{
			TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
			P->SetStringField(TEXT("packageName"), Persistent->GetOutermost()->GetName());
			P->SetNumberField(TEXT("actorCount"), CountValidActors(Persistent));
			P->SetBoolField(TEXT("isCurrent"), Persistent->IsCurrentLevel());
			P->SetBoolField(TEXT("locked"), FLevelUtils::IsLevelLocked(Persistent));
			P->SetBoolField(TEXT("dirty"), Persistent->GetOutermost()->IsDirty());
			P->SetBoolField(TEXT("lightingScenario"), Persistent->bIsLightingScenario);
			Out->SetObjectField(TEXT("persistent"), P);
		}
		if (ULevel* Current = World->GetCurrentLevel())
		{
			Out->SetStringField(TEXT("currentLevel"), Current->GetOutermost()->GetName());
		}

		TArray<TSharedPtr<FJsonValue>> Arr;
		int32 LoadedCount = 0, VisibleCount = 0, PendingCount = 0;
		for (ULevelStreaming* LS : World->GetStreamingLevels())
		{
			if (!LS) { continue; }
			if (LS->IsLevelLoaded())            { ++LoadedCount; }
			if (LS->IsLevelVisible())           { ++VisibleCount; }
			if (LS->IsStreamingStatePending())  { ++PendingCount; }
			Arr.Add(MakeShared<FJsonValueObject>(SerializeSublevel(LS)));
		}

		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetNumberField(TEXT("loadedCount"), LoadedCount);
		Out->SetNumberField(TEXT("visibleCount"), VisibleCount);
		Out->SetNumberField(TEXT("pendingCount"), PendingCount);
		// One boolean to poll on, so a caller does not have to recombine three counts.
		Out->SetBoolField(TEXT("ready"), PendingCount == 0);
		Out->SetArrayField(TEXT("sublevels"), Arr);

		// Deferred verbs (add_sublevel / remove_sublevel / set_sublevel_streaming) return an opId
		// and finish on a later tick; this is where their result surfaces.
		TArray<TSharedPtr<FJsonValue>> Ops;
		int32 PendingOps = 0;
		for (const FStreamingOp& Op : OpLog())
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetNumberField(TEXT("opId"), Op.OpId);
			J->SetStringField(TEXT("endpoint"), Op.Endpoint);
			J->SetStringField(TEXT("path"), Op.Path);
			J->SetBoolField(TEXT("completed"), Op.bCompleted);
			J->SetBoolField(TEXT("ok"), Op.bOk);
			if (!Op.Error.IsEmpty())  { J->SetStringField(TEXT("error"), Op.Error); }
			if (!Op.Detail.IsEmpty()) { J->SetStringField(TEXT("detail"), Op.Detail); }
			if (!Op.bCompleted) { ++PendingOps; }
			Ops.Add(MakeShared<FJsonValueObject>(J));
		}
		Out->SetArrayField(TEXT("ops"), Ops);
		Out->SetNumberField(TEXT("pendingOps"), PendingOps);
	}

	// --- add_sublevel --------------------------------------------------------
	//   in:  { path, streamingClass?, location?, rotation? }
	//   out: { requested, deferred, opId, packagePath, streamingClass, pollWith, note }
	//        or { alreadyPresent:true, changed:false, ... } with NO engine call at all
	//
	// SELF-MANAGED + deferred. AddLevelToWorld loads the level synchronously, broadcasts
	// LevelAdded, flushes level streaming and calls SetCurrentLevel — a registration cascade that
	// must not ride RunEndpoint's blanket transaction (a half-registered ULevel on undo), and that
	// must not run with our HTTP call frame on the stack (the new_level/load_level precedent,
	// MifBridgeWorld.cpp:144/204).
	void H_add_sublevel(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("streamingClass"), TEXT("class"),
			  TEXT("location"), TEXT("rotation") },
			TEXT("path (packagePath, level), streamingClass (class: \"alwaysloaded\"|\"dynamic\"), location {x,y,z}, rotation {x,y,z}")))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		FString PackageName;
		if (!ReadLevelPath(In, Out, TEXT("/Game/Maps/TownDistrict"), PackageName)) { return; }

		TSubclassOf<ULevelStreaming> StreamingClass;
		FString ClassError;
		if (!ParseStreamingClass(JStrAny(In, { TEXT("streamingClass"), TEXT("class") }, TEXT("alwaysloaded")),
			StreamingClass, ClassError))
		{
			Fail(Out, ClassError);
			return;
		}

		// MODAL GUARD 1 of 2 (EditorLevelUtils.cpp:441-451). AddLevelToWorld_Internal pops
		// FSuppressableWarningDialog::ShowModal() when the package IS the persistent level. The
		// engine's test is a string compare against the persistent package name — replicated
		// verbatim so the two can never disagree.
		if (World->PersistentLevel && World->PersistentLevel->GetOutermost()->GetName() == PackageName)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' IS the persistent level of the open world — it cannot also be a sublevel of itself"),
				*PackageName));
			return;
		}

		// MODAL GUARD 2 of 2 (same dialog, already-present branch). Same call the engine uses, so a
		// disagreement is impossible. The spec asks for alreadyPresent:true rather than an error —
		// honoured, but reported as an explicit changed:false so it can never read as work done.
		if (ULevelStreaming* Existing = FLevelUtils::FindStreamingLevel(World, *PackageName))
		{
			Out->SetBoolField(TEXT("alreadyPresent"), true);
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetStringField(TEXT("packagePath"), PackageName);
			Out->SetStringField(TEXT("objectPath"), Existing->GetPathName());
			Out->SetStringField(TEXT("streamingClass"), StreamingClassName(Existing->GetClass()));
			Out->SetStringField(TEXT("note"),
				TEXT("already a sublevel of the open world — no engine call was made (calling AddLevelToWorld here "
					 "would open a modal dialog and block the bridge). Use set_sublevel_streaming to change its class."));
			return;
		}

		// A cooked .pak-mounted map has no loose .umap, so there is nothing for AddLevelToWorld to
		// load and it would return nullptr with no explanation. Check for the file the way
		// load_level does (MifBridgeWorld.cpp:197) and say which of the two it is.
		const FString MapFilename = FPackageName::LongPackageNameToFilename(
			PackageName, FPackageName::GetMapPackageExtension());
		if (!FPaths::FileExists(MapFilename))
		{
			Fail(Out, FString::Printf(
				TEXT("no loose map file for '%s' (expected %s) — either the package does not exist, or it is cooked "
					 ".pak content, which cannot be added as an editable sublevel (describe_package to tell which)"),
				*PackageName, *MapFilename));
			return;
		}

		FVector Location = FVector::ZeroVector;
		FVector RotVec = FVector::ZeroVector;
		const TSharedPtr<FJsonObject>* Obj = nullptr;
		if (In->TryGetObjectField(TEXT("location"), Obj) && Obj)
		{
			const TSharedRef<FJsonObject> O = Obj->ToSharedRef();
			Location = FVector(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
		}
		if (In->TryGetObjectField(TEXT("rotation"), Obj) && Obj)
		{
			const TSharedRef<FJsonObject> O = Obj->ToSharedRef();
			RotVec = FVector(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
		}
		// x/y/z read as pitch/yaw/roll, matching what SerializeSublevel emits and what
		// spawn_actor_in_level already accepts.
		const FRotator Rotation(RotVec.X, RotVec.Y, RotVec.Z);

		const int32 OpId = BeginOp(TEXT("add_sublevel"), PackageName);
		TWeakObjectPtr<UWorld> WeakWorld(World);
		// Location/Rotation are captured rather than a built FTransform: FTransform is 16-byte
		// aligned and a delegate's payload allocation is not the place to rely on that.
		MifDeferToNextTick(
			[WeakWorld, PackageName, StreamingClass, Location, Rotation, OpId]()
		{
			UWorld* W = WeakWorld.Get();
			if (!W)
			{
				FinishOp(OpId, false, TEXT("the editor world was replaced before the deferred add ran"));
				return;
			}
			// Re-run BOTH modal guards. A frame passed; the world may have changed under us, and
			// "we checked last tick" is not a guarantee that the dialog branch is unreachable now.
			if (W->PersistentLevel && W->PersistentLevel->GetOutermost()->GetName() == PackageName)
			{
				FinishOp(OpId, false, TEXT("became the persistent level before the deferred add ran"));
				return;
			}
			if (FLevelUtils::FindStreamingLevel(W, *PackageName))
			{
				FinishOp(OpId, false, TEXT("already present by the time the deferred add ran (no engine call made)"));
				return;
			}
			const FTransform LevelTransform(Rotation, Location, FVector::OneVector);
			// UNREALED_API, EditorLevelUtils.h:223.
			ULevelStreaming* Added = UEditorLevelUtils::AddLevelToWorld(W, *PackageName, StreamingClass, LevelTransform);
			if (!Added)
			{
				FinishOp(OpId, false, FString::Printf(
					TEXT("AddLevelToWorld returned null for '%s' — package missing or not a ULevel"), *PackageName));
				return;
			}
			FinishOp(OpId, true, FString(), Added->GetPathName());
		});

		Out->SetBoolField(TEXT("requested"), true);
		Out->SetBoolField(TEXT("deferred"), true);
		Out->SetNumberField(TEXT("opId"), OpId);
		Out->SetStringField(TEXT("packagePath"), PackageName);
		Out->SetStringField(TEXT("streamingClass"), StreamingClassName(StreamingClass.Get()));
		Out->SetStringField(TEXT("pollWith"), TEXT("list_sublevels"));
		Out->SetStringField(TEXT("note"),
			TEXT("DEFERRED to the next tick — this call does NOT block. Poll list_sublevels until the ops[] entry "
				 "with this opId has completed:true, then read its ok/error; the new sublevel's objectPath is in "
				 "its detail. AddLevelToWorld also makes the new level CURRENT and deactivates Landscape mode."));
	}

	// --- remove_sublevel -----------------------------------------------------
	//   in:  { path, discardUnsaved? }
	//   out: { requested, deferred, opId, packagePath, wasCurrent, undoBufferReset, pollWith, note }
	//
	// SELF-MANAGED + deferred, for a stronger reason than add_sublevel: RemoveLevelsFromWorld resets
	// the transaction buffer (EditorLevelUtils.cpp:886-889) — which would destroy the outer
	// transaction under RunEndpoint's feet — then runs GEditor->Cleanse, a forced GC (:909), then a
	// stale-reference sweep that is **FATAL** when the buffer was reset (:929-937,
	// EPrintStaleReferencesOptions::Fatal). None of that may happen with our call frame live.
	void H_remove_sublevel(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("discardUnsaved") },
			TEXT("path (packagePath, level), discardUnsaved (bool)")))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		FString PackageName;
		if (!ReadLevelPath(In, Out, TEXT("/Game/Maps/TownDistrict"), PackageName)) { return; }

		if (World->PersistentLevel && World->PersistentLevel->GetOutermost()->GetName() == PackageName)
		{
			Fail(Out, TEXT("cannot remove the persistent level — use load_level or new_level to change the open map"));
			return;
		}

		FString FindError;
		ULevelStreaming* LS = FindSublevel(World, PackageName, FindError);
		if (!LS) { Fail(Out, FindError); return; }
		// Re-read the canonical name: the caller may have addressed it by leaf name.
		const FString ResolvedName = LS->GetWorldAssetPackageFName().ToString();

		ULevel* Level = LS->GetLoadedLevel();
		if (!Level)
		{
			// RemoveLevelFromWorld takes a ULevel*, and there isn't one. Passing null makes the
			// engine's Algo::AnyOf treat it as "cannot be removed" and return false with no reason.
			Fail(Out, FString::Printf(
				TEXT("sublevel '%s' has no loaded ULevel — RemoveLevelFromWorld needs one. Call "
					 "set_sublevel_visibility {path:\"%s\", shouldBeLoaded:true}, poll list_sublevels until it is "
					 "loaded, then retry."), *ResolvedName, *ResolvedName));
			return;
		}

		// MODAL GUARD (EditorLevelUtils.cpp:830-834). A locked level makes RemoveLevelsFromWorld pop
		// FMessageDialog::Open and then return false — the dialog blocks the whole bridge, so the
		// engine must never reach that branch.
		if (FLevelUtils::IsLevelLocked(Level))
		{
			Fail(Out, FString::Printf(
				TEXT("sublevel '%s' is locked — the engine would open a modal dialog and block the bridge. Unlock it "
					 "in the Levels panel first (the bEvenIfLocked bypass is deliberately not exposed)."),
				*ResolvedName));
			return;
		}

		// MODAL GUARD (EditorLevelUtils.cpp:894-897). The only thing that makes UnloadPackages set
		// OutErrorMessage — and therefore the only way that second dialog fires — is a DIRTY package
		// (PackageTools.cpp:362-390). RemoveLevelsFromWorld normally clears the dirty flag itself in
		// PrivateDestroyLevel, which is also how it silently DISCARDS unsaved sublevel edits. Both
		// facts point the same way: refuse a dirty sublevel by default, and require an explicit
		// opt-in that says out loud what is being thrown away.
		UPackage* LevelPackage = Level->GetOutermost();
		const bool bDirty = LevelPackage && LevelPackage->IsDirty();
		const bool bDiscardUnsaved = JBool(In, TEXT("discardUnsaved"), false);
		if (bDirty && !bDiscardUnsaved)
		{
			Fail(Out, FString::Printf(
				TEXT("sublevel '%s' has UNSAVED changes — removing it discards them permanently. Call save_package "
					 "{path:\"%s\"} first, or pass discardUnsaved:true to throw them away deliberately."),
				*ResolvedName, *ResolvedName));
			return;
		}

		const bool bWasCurrent = Level->IsCurrentLevel();
		// NOTE — the F-axis spec suggested calling MakeLevelCurrent(persistent) first when removing
		// the current level. Do NOT: MakeLevelCurrent(ULevel*, bEvenIfLocked=false) is itself a modal
		// (EditorLevelUtils.cpp:588) if the persistent level is locked, and RemoveLevelsFromWorld
		// already does exactly this internally with bEvenIfLocked=TRUE (:869-873). Following the
		// spec here would ADD the dialog the rest of this handler exists to avoid.

		const int32 OpId = BeginOp(TEXT("remove_sublevel"), ResolvedName);
		TWeakObjectPtr<UWorld> WeakWorld(World);
		MifDeferToNextTick(
			[WeakWorld, ResolvedName, bDiscardUnsaved, OpId]()
		{
			UWorld* W = WeakWorld.Get();
			if (!W)
			{
				FinishOp(OpId, false, TEXT("the editor world was replaced before the deferred remove ran"));
				return;
			}
			// Re-resolve from the package NAME rather than capturing pointers: a captured ULevel*
			// across a tick is exactly the kind of stale reference the engine's own post-remove
			// sweep is willing to fatal over.
			FString Err;
			ULevelStreaming* Streaming = FindSublevel(W, ResolvedName, Err);
			if (!Streaming) { FinishOp(OpId, false, Err); return; }
			ULevel* L = Streaming->GetLoadedLevel();
			if (!L) { FinishOp(OpId, false, TEXT("sublevel unloaded before the deferred remove ran")); return; }
			if (FLevelUtils::IsLevelLocked(L))
			{
				FinishOp(OpId, false, TEXT("sublevel became locked before the deferred remove ran (modal avoided)"));
				return;
			}
			if (UPackage* Pkg = L->GetOutermost())
			{
				if (Pkg->IsDirty())
				{
					if (!bDiscardUnsaved)
					{
						FinishOp(OpId, false, TEXT("sublevel became dirty before the deferred remove ran (modal avoided)"));
						return;
					}
					// Clear the flag ourselves, exactly as PrivateDestroyLevel does
					// (EditorLevelUtils.cpp:1043-1046), so the dirty branch of UnloadPackages — the
					// sole writer of the message behind the :894-897 dialog — is provably dead.
					Pkg->SetDirtyFlag(false);
				}
			}
			// UNREALED_API, EditorLevelUtils.h:247. Defaults kept on purpose: bResetTransBuffer=true
			// is the engine's own choice here and fighting it leaves stale references behind.
			const bool bRemoved = UEditorLevelUtils::RemoveLevelFromWorld(L);
			FinishOp(OpId, bRemoved,
				bRemoved ? FString() : FString(TEXT("RemoveLevelFromWorld returned false")),
				bRemoved ? FString(TEXT("undo buffer was reset")) : FString());
		});

		Out->SetBoolField(TEXT("requested"), true);
		Out->SetBoolField(TEXT("deferred"), true);
		Out->SetNumberField(TEXT("opId"), OpId);
		Out->SetStringField(TEXT("packagePath"), ResolvedName);
		Out->SetBoolField(TEXT("wasCurrent"), bWasCurrent);
		Out->SetBoolField(TEXT("discardedUnsaved"), bDirty && bDiscardUnsaved);
		// The engine nukes the undo stack itself (bResetTransBuffer defaults to true); say so rather
		// than letting the caller discover that Ctrl-Z stopped working.
		Out->SetBoolField(TEXT("undoBufferReset"), true);
		Out->SetStringField(TEXT("pollWith"), TEXT("list_sublevels"));
		Out->SetStringField(TEXT("note"),
			TEXT("DEFERRED to the next tick — does NOT block. Poll list_sublevels until the ops[] entry with this "
				 "opId has completed:true. The level ASSET stays on disk; only the attachment is removed. This also "
				 "RESETS THE UNDO BUFFER and forces a GC, so nothing before it can be undone afterwards."));
	}

	// --- set_sublevel_visibility ---------------------------------------------
	//   in:  { path, visible?, shouldBeLoaded?, shouldBeVisible?, lightingScenario? }
	//   out: { packagePath, changed{<field>:<read-back value>}, ignored[{field,requested,actual,reason}],
	//          sublevel{...}, pending, editorVisibilityDeferred? }
	//        every field that did NOT take is in `ignored`, and a call where NOTHING took is an ERROR
	//
	// TRANSACTED (the default bucket): these are property-level flips with Modify support —
	// SetLevelVisibility's ModifyOnChange mode participates in the transaction, so Ctrl-Z works.
	void H_set_sublevel_visibility(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("visible"), TEXT("editorVisible"),
			  TEXT("shouldBeLoaded"), TEXT("shouldBeVisible"), TEXT("lightingScenario") },
			TEXT("path (packagePath, level), visible (editorVisible), shouldBeLoaded, shouldBeVisible, lightingScenario")))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		FString PackageName;
		if (!ReadLevelPath(In, Out, TEXT("/Game/Maps/TownDistrict"), PackageName)) { return; }

		FString FindError;
		ULevelStreaming* LS = FindSublevel(World, PackageName, FindError);
		if (!LS) { Fail(Out, FindError); return; }
		const FString ResolvedName = LS->GetWorldAssetPackageFName().ToString();

		const bool bHasVisible   = JHasAny(In, { TEXT("visible"), TEXT("editorVisible") });
		const bool bHasLoaded    = JHasAny(In, { TEXT("shouldBeLoaded") });
		const bool bHasRtVisible = JHasAny(In, { TEXT("shouldBeVisible") });
		const bool bHasLighting  = JHasAny(In, { TEXT("lightingScenario") });
		if (!bHasVisible && !bHasLoaded && !bHasRtVisible && !bHasLighting)
		{
			Fail(Out, TEXT("nothing to change — pass at least one of visible, shouldBeLoaded, shouldBeVisible, lightingScenario"));
			return;
		}

		// EVERY write below is READ BACK and compared. That is not belt-and-braces: the base
		// ULevelStreaming::SetShouldBeLoaded is an EMPTY function body (LevelStreaming.cpp) and
		// ULevelStreamingAlwaysLoaded::ShouldBeLoaded() is hardcoded `return true`
		// (LevelStreamingAlwaysLoaded.h:27) — so `shouldBeLoaded:false` on an always-loaded sublevel
		// (which is what add_sublevel creates by default) does NOTHING AT ALL. Echoing the request
		// back would be the exact ok:true-having-done-nothing failure the house rules forbid.
		TSharedRef<FJsonObject> Applied = MakeShared<FJsonObject>();   // setter took: read-back == request
		TArray<TSharedPtr<FJsonValue>> Ignored;                        // setter did not take, with reason
		TArray<FString> IgnoredReasons;                                // same, flattened for the error string

		auto NoteIgnored = [&Ignored, &IgnoredReasons](const TCHAR* Field, bool bRequested, bool bActual, const FString& Why)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("field"), Field);
			J->SetBoolField(TEXT("requested"), bRequested);
			J->SetBoolField(TEXT("actual"), bActual);
			J->SetStringField(TEXT("reason"), Why);
			Ignored.Add(MakeShared<FJsonValueObject>(J));
			IgnoredReasons.Add(FString::Printf(TEXT("%s: %s"), Field, *Why));
		};

		// Runtime flags first: they are what MAKES a level loaded, and the editor-visibility call
		// below needs a loaded ULevel.
		if (bHasLoaded)
		{
			const bool bWant = JBool(In, TEXT("shouldBeLoaded"), true);
			LS->SetShouldBeLoaded(bWant);            // ENGINE_API virtual, LevelStreaming.h:427
			const bool bActual = LS->ShouldBeLoaded();
			if (bActual == bWant)
			{
				Applied->SetBoolField(TEXT("shouldBeLoaded"), bActual);
			}
			else
			{
				NoteIgnored(TEXT("shouldBeLoaded"), bWant, bActual, FString::Printf(
					TEXT("streaming class '%s' hardcodes ShouldBeLoaded() and ignores the flag — only \"dynamic\" "
						 "honours it. Call set_sublevel_streaming {path:\"%s\", streamingClass:\"dynamic\"} first."),
					StreamingClassName(LS->GetClass()), *ResolvedName));
			}
		}
		if (bHasRtVisible)
		{
			const bool bWant = JBool(In, TEXT("shouldBeVisible"), true);
			LS->SetShouldBeVisible(bWant);           // ENGINE_API, LevelStreaming.h:414
			const bool bActual = LS->GetShouldBeVisibleFlag();
			if (bActual == bWant)
			{
				Applied->SetBoolField(TEXT("shouldBeVisible"), bActual);
			}
			else
			{
				NoteIgnored(TEXT("shouldBeVisible"), bWant, bActual,
					TEXT("SetShouldBeVisible did not take on this streaming level"));
			}
		}

		ULevel* Level = LS->GetLoadedLevel();

		if (bHasVisible)
		{
			const bool bWant = JBoolAny(In, { TEXT("visible"), TEXT("editorVisible") }, true);
			if (!Level)
			{
				// Not a hard error when the caller asked for shouldBeLoaded in the SAME call: the load
				// lands on a later frame, so editor visibility genuinely cannot be applied yet.
				Out->SetBoolField(TEXT("editorVisibilityDeferred"), true);
				NoteIgnored(TEXT("visible"), bWant, false,
					bHasLoaded
						? TEXT("the level is not in memory yet — poll list_sublevels until loaded:true, then call "
							   "again with visible")
						: TEXT("sublevel has no loaded ULevel — set shouldBeLoaded:true first, poll list_sublevels "
							   "until loaded:true, then call again with visible"));
			}
			else
			{
				// UNREALED_API, EditorLevelUtils.h:282. ModifyOnChange is what makes this undoable;
				// bForceLayersVisible stays false so hidden layers are not silently revealed.
				UEditorLevelUtils::SetLevelVisibility(Level, bWant, /*bForceLayersVisible*/ false,
					ELevelVisibilityDirtyMode::ModifyOnChange);
				const bool bActual = LS->IsLevelVisible();
				if (bActual == bWant)
				{
					Applied->SetBoolField(TEXT("visible"), bActual);
				}
				else
				{
					// NOTE: believed UNREACHABLE for an editor world. EditorLevelUtils.cpp:1237/:1255
					// asserts check(Level->bIsVisible == bShouldBeVisible) immediately after its flush,
					// and AddToWorld never returns partial in-editor (World.cpp:3121 —
					// bConsiderTimeLimit &= bMatchStarted && bIsGameWorld). Kept because reporting a
					// mismatch is strictly better than asserting our own belief, but do NOT treat it as
					// the safety net: if SetLevelVisibility fails, the engine ensures/asserts first.
					NoteIgnored(TEXT("visible"), bWant, bActual,
						TEXT("SetLevelVisibility did not take (the level streaming flush did not complete this frame)"));
				}
			}
		}

		if (bHasLighting)
		{
			const bool bWant = JBool(In, TEXT("lightingScenario"), true);
			if (!Level)
			{
				Fail(Out, FString::Printf(
					TEXT("lightingScenario needs a loaded ULevel; sublevel '%s' is not loaded — set shouldBeLoaded:true first"),
					*ResolvedName));
				return;
			}
			Level->Modify();
			// ENGINE_API, Level.h:1090. Folded in here rather than given its own endpoint because it
			// is one setter on an object this endpoint has already resolved — see the "Compositions"
			// note in docs/audit/work/F_world_level.md.
			Level->SetLightingScenario(bWant);
			const bool bActual = Level->bIsLightingScenario;
			if (bActual == bWant)
			{
				Applied->SetBoolField(TEXT("lightingScenario"), bActual);
			}
			else
			{
				NoteIgnored(TEXT("lightingScenario"), bWant, bActual, TEXT("SetLightingScenario did not take"));
			}
		}

		// If NOTHING took, this call did nothing — and reporting ok:true for that is the failure mode
		// docs/02_GOTCHAS.md ("Never silence a mutating call") exists to prevent.
		if (Applied->Values.Num() == 0)
		{
			Out->SetArrayField(TEXT("ignored"), Ignored);
			Fail(Out, FString::Printf(TEXT("nothing was changed on '%s' — %s"),
				*ResolvedName, *FString::Join(IgnoredReasons, TEXT("; "))));
			return;
		}

		// Re-read everything from the live objects (SerializeSublevel re-fetches GetLoadedLevel);
		// never echo the request back as if it were state.
		Out->SetStringField(TEXT("packagePath"), ResolvedName);
		Out->SetObjectField(TEXT("changed"), Applied);
		Out->SetArrayField(TEXT("ignored"), Ignored);
		Out->SetObjectField(TEXT("sublevel"), SerializeSublevel(LS));
		Out->SetBoolField(TEXT("pending"), LS->IsStreamingStatePending());
		if (LS->IsStreamingStatePending())
		{
			Out->SetStringField(TEXT("note"),
				TEXT("streaming state change is IN FLIGHT — poll list_sublevels until this level's pending:false"));
		}
	}

	// --- set_current_sublevel ------------------------------------------------
	//   in:  { path }   ("persistent" selects the persistent level)
	//   out: { currentLevel, previousLevel, changed }
	//
	// TRANSACTED. Without this, sublevels are decoration: spawn_actor_in_level and spawn_many always
	// land in whatever level is current.
	void H_set_current_sublevel(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("path"), TEXT("packagePath"), TEXT("level") },
			TEXT("path (packagePath, level) — a package path, or the literal \"persistent\"")))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		const FString Raw = JStrAny(In, { TEXT("path"), TEXT("packagePath"), TEXT("level") });
		if (Raw.IsEmpty())
		{
			Fail(Out, TEXT("path is required — a package path like \"/Game/Maps/TownDistrict\", or \"persistent\""));
			return;
		}

		ULevel* Target = nullptr;
		FString ResolvedName;
		if (Raw.TrimStartAndEnd().Equals(TEXT("persistent"), ESearchCase::IgnoreCase))
		{
			Target = World->PersistentLevel;
			if (!Target) { Fail(Out, TEXT("world has no persistent level")); return; }
			ResolvedName = Target->GetOutermost()->GetName();
		}
		else
		{
			FString PackageName, PathError;
			if (!NormalizeLevelPackagePath(Raw, PackageName, PathError))
			{
				Fail(Out, PathError);
				return;
			}
			FString FindError;
			ULevelStreaming* LS = FindSublevel(World, PackageName, FindError);
			if (!LS) { Fail(Out, FindError); return; }
			ResolvedName = LS->GetWorldAssetPackageFName().ToString();
			Target = LS->GetLoadedLevel();
			if (!Target)
			{
				Fail(Out, FString::Printf(
					TEXT("sublevel '%s' has no loaded ULevel — set_sublevel_visibility {shouldBeLoaded:true} first, "
						 "then poll list_sublevels until loaded:true"), *ResolvedName));
				return;
			}
		}

		// MODAL GUARD (EditorLevelUtils.cpp:555-588). MakeLevelCurrent opens FMessageDialog::Open on
		// a locked level when bEvenIfLocked is false — and bEvenIfLocked is deliberately NOT exposed,
		// because "make a read-only level current" then silently drops every spawn into it.
		if (FLevelUtils::IsLevelLocked(Target))
		{
			Out->SetBoolField(TEXT("lockedBypassed"), false);
			Fail(Out, FString::Printf(
				TEXT("level '%s' is locked — the engine would open a modal dialog and block the bridge. Unlock it in "
					 "the Levels panel first (bEvenIfLocked is deliberately not exposed)."), *ResolvedName));
			return;
		}

		ULevel* Previous = World->GetCurrentLevel();
		const FString PreviousName = Previous ? Previous->GetOutermost()->GetName() : FString();
		if (Previous == Target)
		{
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetStringField(TEXT("currentLevel"), ResolvedName);
			Out->SetStringField(TEXT("previousLevel"), PreviousName);
			Out->SetStringField(TEXT("note"), TEXT("already the current level — nothing was changed"));
			return;
		}

		// UNREALED_API, EditorLevelUtils.h:86. Also forces the level visible and deselects builder
		// brushes; both are the editor's own behaviour, not ours.
		UEditorLevelUtils::MakeLevelCurrent(Target, /*bEvenIfLocked*/ false);

		ULevel* NowCurrent = World->GetCurrentLevel();
		const FString NowName = NowCurrent ? NowCurrent->GetOutermost()->GetName() : FString();
		// MakeLevelCurrent returns void, so the ONLY proof it worked is reading the world back.
		if (NowCurrent != Target)
		{
			Fail(Out, FString::Printf(
				TEXT("MakeLevelCurrent did not take effect — current level is still '%s'"), *NowName));
			return;
		}
		Out->SetBoolField(TEXT("changed"), true);
		Out->SetStringField(TEXT("currentLevel"), NowName);
		Out->SetStringField(TEXT("previousLevel"), PreviousName);
		Out->SetStringField(TEXT("note"),
			TEXT("spawn_actor_in_level / spawn_many now place actors into this level"));
	}

	// --- set_sublevel_streaming ----------------------------------------------
	//   in:  { path, streamingClass }
	//   out: { requested, deferred, opId, packagePath, fromClass, toClass, oldObjectPath, pollWith }
	//
	// SELF-MANAGED + deferred. SetStreamingClassForLevel does not edit a property — it REMOVES the
	// ULevelStreaming and re-adds the level through AddLevelToWorld, returning a NEW object
	// (EditorLevelUtils.cpp:514-548). Undoing an object-identity swap mid-array is not a property
	// revert, and the re-add is the same registration cascade add_sublevel defers for.
	void H_set_sublevel_streaming(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("streamingClass"), TEXT("class") },
			TEXT("path (packagePath, level), streamingClass (class: \"alwaysloaded\"|\"dynamic\")")))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		FString PackageName;
		if (!ReadLevelPath(In, Out, TEXT("/Game/Maps/TownDistrict"), PackageName)) { return; }

		const FString ClassRaw = JStrAny(In, { TEXT("streamingClass"), TEXT("class") });
		if (ClassRaw.IsEmpty())
		{
			Fail(Out, TEXT("streamingClass is required (alias: class) — accepted: alwaysloaded, dynamic"));
			return;
		}
		TSubclassOf<ULevelStreaming> NewClass;
		FString ClassError;
		if (!ParseStreamingClass(ClassRaw, NewClass, ClassError)) { Fail(Out, ClassError); return; }

		FString FindError;
		ULevelStreaming* LS = FindSublevel(World, PackageName, FindError);
		if (!LS) { Fail(Out, FindError); return; }
		const FString ResolvedName = LS->GetWorldAssetPackageFName().ToString();

		// CRASH GUARD (EditorLevelUtils.cpp:524-525). SetStreamingClassForLevel runs
		// `check(Level)` on InLevel->GetLoadedLevel() — an unloaded sublevel is a hard assert that
		// takes the editor down, not an error it returns.
		if (!LS->GetLoadedLevel())
		{
			Fail(Out, FString::Printf(
				TEXT("sublevel '%s' is not loaded — SetStreamingClassForLevel asserts (check(Level), "
					 "EditorLevelUtils.cpp:525) on an unloaded level. Call set_sublevel_visibility "
					 "{path:\"%s\", shouldBeLoaded:true}, poll list_sublevels until loaded:true, then retry."),
				*ResolvedName, *ResolvedName));
			return;
		}

		const FString FromClass = StreamingClassName(LS->GetClass());
		const FString ToClass = StreamingClassName(NewClass.Get());
		if (LS->GetClass() == NewClass.Get())
		{
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetStringField(TEXT("packagePath"), ResolvedName);
			Out->SetStringField(TEXT("streamingClass"), FromClass);
			Out->SetStringField(TEXT("objectPath"), LS->GetPathName());
			Out->SetStringField(TEXT("note"), TEXT("already this streaming class — no engine call was made"));
			return;
		}

		const int32 OpId = BeginOp(TEXT("set_sublevel_streaming"), ResolvedName);
		const FString OldObjectPath = LS->GetPathName();
		TWeakObjectPtr<UWorld> WeakWorld(World);
		MifDeferToNextTick(
			[WeakWorld, ResolvedName, NewClass, OpId]()
		{
			UWorld* W = WeakWorld.Get();
			if (!W) { FinishOp(OpId, false, TEXT("the editor world was replaced before the deferred swap ran")); return; }
			FString Err;
			ULevelStreaming* Streaming = FindSublevel(W, ResolvedName, Err);
			if (!Streaming) { FinishOp(OpId, false, Err); return; }
			// Re-run the crash guard: an unload between ticks would turn this into an assert.
			if (!Streaming->GetLoadedLevel())
			{
				FinishOp(OpId, false, TEXT("sublevel unloaded before the deferred swap ran (check(Level) assert avoided)"));
				return;
			}
			// THIRD assert on the same engine path, previously unguarded: EditorLevelUtils.cpp:527
			// is check(Level->OwningWorld), separate from the check(InLevel) at :516 and the
			// check(Level) at :525 that the two guards above cover. A loaded ULevel with a null
			// OwningWorld is rare but is exactly the state a mid-teardown world leaves behind, and
			// an assert here kills the editor rather than failing the op.
			if (!Streaming->GetLoadedLevel()->OwningWorld)
			{
				FinishOp(OpId, false, TEXT("the loaded sublevel has no OwningWorld (world torn down between ticks) — "
					"SetStreamingClassForLevel would assert (check(Level->OwningWorld), EditorLevelUtils.cpp:527)"));
				return;
			}
			ULevelStreaming* Replacement = UEditorLevelUtils::SetStreamingClassForLevel(Streaming, NewClass);
			if (!Replacement)
			{
				FinishOp(OpId, false, TEXT("SetStreamingClassForLevel returned null — the level was not re-added"));
				return;
			}
			FinishOp(OpId, true, FString(), Replacement->GetPathName());
		});

		Out->SetBoolField(TEXT("requested"), true);
		Out->SetBoolField(TEXT("deferred"), true);
		Out->SetNumberField(TEXT("opId"), OpId);
		Out->SetStringField(TEXT("packagePath"), ResolvedName);
		Out->SetStringField(TEXT("fromClass"), FromClass);
		Out->SetStringField(TEXT("toClass"), ToClass);
		Out->SetStringField(TEXT("oldObjectPath"), OldObjectPath);
		Out->SetStringField(TEXT("pollWith"), TEXT("list_sublevels"));
		Out->SetStringField(TEXT("note"),
			TEXT("DEFERRED to the next tick — does NOT block. The ULevelStreaming object is REPLACED, so oldObjectPath "
				 "dies: poll list_sublevels until the ops[] entry with this opId has completed:true and read the NEW "
				 "objectPath from its detail. Transform, streaming volumes, colour and keywords are copied across."));
	}

	// --- pie_load_level_instance ---------------------------------------------
	//   in:  { path, location?, rotation?, visible?, netMode?, nameOverride?, tempPackage? }
	//   out: { requested, instanceName, sourcePath, objectPath, loaded, visible, state, pollWith }
	//
	// THE reported gap: stream a level into the LIVE PIE world for test setup, without a Lua command.
	// SELF-MANAGED — it adds a level to a world, and the ULevelStreamingDynamic it creates is
	// RF_Transient inside a PIE world that will be torn down; an undo step holding that is meaningless
	// and a later Ctrl-Z would restore a pointer into a dead world.
	//
	// Engine API: ULevelStreamingDynamic::LoadLevelInstance — `static ENGINE_API`,
	// LevelStreamingDynamic.h:80. The implementation (LevelStreaming.cpp) does an asset-registry
	// existence check, then NewObject + SetShouldBeLoaded(true) + World->AddStreamingLevel. There is
	// no dialog and no blocking load anywhere in that path (bShouldBlockOnLoad is set false), which
	// is why this one runs inline and can hand back the real handle immediately.
	void H_pie_load_level_instance(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("location"), TEXT("rotation"),
			  TEXT("visible"), TEXT("netMode"), TEXT("nameOverride"), TEXT("tempPackage") },
			TEXT("path (packagePath, level), location {x,y,z}, rotation {x,y,z}, visible (bool), "
				 "netMode (\"server\"|\"client\"|\"any\"), nameOverride (string), tempPackage (bool)")))
		{
			return;
		}

		UWorld* World = ResolvePIEWorld(In, Out);
		if (!World) { return; }

		FString PackageName;
		if (!ReadLevelPath(In, Out, TEXT("/Game/Maps/TestRoom"), PackageName)) { return; }

		FVector Location = FVector::ZeroVector;
		FVector RotVec = FVector::ZeroVector;
		const TSharedPtr<FJsonObject>* Obj = nullptr;
		if (In->TryGetObjectField(TEXT("location"), Obj) && Obj)
		{
			const TSharedRef<FJsonObject> O = Obj->ToSharedRef();
			Location = FVector(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
		}
		if (In->TryGetObjectField(TEXT("rotation"), Obj) && Obj)
		{
			const TSharedRef<FJsonObject> O = Obj->ToSharedRef();
			RotVec = FVector(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
		}
		const FRotator Rotation(RotVec.X, RotVec.Y, RotVec.Z);

		const FString NameOverride = JStr(In, TEXT("nameOverride"));
		const bool bTempPackage = JBool(In, TEXT("tempPackage"), false);
		const bool bWantVisible = JBool(In, TEXT("visible"), true);

		// COLLISION CHECK BEFORE THE CALL, because the engine cannot tell us about it afterwards.
		// ULevelStreamingDynamic::LoadLevelInstance sets bOutSuccess=false on ENTRY
		// (LevelStreaming.cpp:2495) and LoadLevelInstance_Internal returns nullptr on the
		// already-exists branch (:2547-2552) WITHOUT setting it. bOutSuccess was therefore ALWAYS false
		// whenever Instance was null, so the "already exists" arm of the old error was unreachable and
		// a caller who passed a colliding nameOverride was told the package is missing from the asset
		// registry — a confident, wrong cause that sends them to check the wrong thing.
		//
		// The predicate below is not a paraphrase: it replicates GetLevelInstancePackageName
		// (LevelStreaming.cpp:2585-2619) and the uniqueness test (:2538-2553) exactly —
		//   [/Temp] + GetLongPackagePath(LongPackageName) + "/" + nameOverride,
		//   then ConvertToPIEPackageName with the world context's PIEInstance,
		//   compared against GetWorldAssetPackageFName().
		// The engine only runs that test when a nameOverride was supplied (bNeedsUniqueTest), so this
		// does too — an auto-generated name is unique by construction.
		if (!NameOverride.IsEmpty())
		{
			FString CandidateName;
			if (bTempPackage) { CandidateName += TEXT("/Temp"); }
			CandidateName += FPackageName::GetLongPackagePath(PackageName);
			CandidateName += TEXT("/");
			CandidateName += NameOverride;

			if (World->IsPlayInEditor())
			{
				if (const FWorldContext* Ctx = GEngine ? GEngine->GetWorldContextFromWorld(World) : nullptr)
				{
					CandidateName = UWorld::ConvertToPIEPackageName(CandidateName, Ctx->PIEInstance);
				}
			}

			const FName CandidateFName(*CandidateName);
			const bool bCollides = World->GetStreamingLevels().ContainsByPredicate(
				[&CandidateFName](ULevelStreaming* Existing)
				{
					return Existing && Existing->GetWorldAssetPackageFName() == CandidateFName;
				});
			if (bCollides)
			{
				Fail(Out, FString::Printf(
					TEXT("a level instance named '%s' already exists in this PIE world (package '%s') — pass a different ")
					TEXT("nameOverride, or omit it to let the engine mint a unique name"),
					*NameOverride, *CandidateName));
				return;
			}
		}

		bool bFound = false;
		// Default-constructed (null) rather than a literal nullptr, so the TSubclassOf conversion is
		// unambiguous at the call site.
		const TSubclassOf<ULevelStreamingDynamic> NoStreamingClassOverride;
		ULevelStreamingDynamic* Instance = ULevelStreamingDynamic::LoadLevelInstance(
			World, PackageName, Location, Rotation, bFound, NameOverride,
			NoStreamingClassOverride, bTempPackage);

		if (!Instance)
		{
			// Reaching here with the collision already excluded above means the package itself is the
			// problem. bFound is reported for diagnosis but is NOT used to pick the message any more.
			Out->SetBoolField(TEXT("engineReportedSuccess"), bFound);
			Fail(Out, FString::Printf(
				TEXT("no level package '%s' could be loaded as a level instance — find_assets to check the path ")
				TEXT("(a level instance must be a real .umap, cooked or loose)"), *PackageName));
			return;
		}

		if (!bWantVisible)
		{
			// LoadLevelInstance hardcodes bInitiallyVisible=true on this overload; honour the
			// parameter rather than accepting it and silently ignoring it.
			Instance->SetShouldBeVisible(false);
		}

		Out->SetBoolField(TEXT("requested"), true);
		// The PIE-renamed per-instance package name. THIS is the handle pie_unload_level_instance
		// takes — the source path is not unique when several instances of one map are loaded.
		Out->SetStringField(TEXT("instanceName"), Instance->GetWorldAssetPackageFName().ToString());
		Out->SetStringField(TEXT("sourcePath"), PackageName);
		Out->SetStringField(TEXT("objectPath"), Instance->GetPathName());
		Out->SetStringField(TEXT("world"), World->GetName());
		Out->SetBoolField(TEXT("loaded"), Instance->IsLevelLoaded());
		Out->SetBoolField(TEXT("visible"), Instance->IsLevelVisible());
		Out->SetBoolField(TEXT("shouldBeVisible"), Instance->GetShouldBeVisibleFlag());
		Out->SetStringField(TEXT("state"), ::EnumToString(Instance->GetLevelStreamingState()));
		Out->SetBoolField(TEXT("pending"), Instance->IsStreamingStatePending());
		Out->SetStringField(TEXT("pollWith"), TEXT("list_sublevels {\"world\":\"pie\"}"));
		Out->SetStringField(TEXT("note"),
			TEXT("streaming is ASYNCHRONOUS — this call does NOT block and loaded/visible are almost always false on "
				 "return. Poll list_sublevels {world:\"pie\"} until the entry whose packageName equals instanceName "
				 "reports loaded:true (and visible:true if you asked for it), or simply until ready:true."));
	}

	// --- pie_unload_level_instance -------------------------------------------
	//   in:  { instanceName | objectPath | path, netMode? }
	//   out: { requested, instanceName, objectPath, pollWith }
	//
	// SELF-MANAGED, same reasoning as the load half. The engine's own unload route is
	// ULevelStreaming::SetIsRequestingUnloadAndRemoval (ENGINE_API, LevelStreaming.h:458): it flips
	// the flag and calls UpdateStreamingLevelShouldBeConsidered, so the level is torn down by the
	// streaming update over the following frames. Nothing here blocks and nothing here can dialog.
	void H_pie_unload_level_instance(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("instanceName"), TEXT("name"), TEXT("path"), TEXT("packagePath"), TEXT("level"),
			  TEXT("objectPath"), TEXT("netMode") },
			TEXT("instanceName (name) from pie_load_level_instance, or objectPath, or path (packagePath, level) "
				 "naming the SOURCE map; netMode (\"server\"|\"client\"|\"any\")")))
		{
			return;
		}

		UWorld* World = ResolvePIEWorld(In, Out);
		if (!World) { return; }

		const FString Wanted = JStrAny(In, {
			TEXT("instanceName"), TEXT("name"), TEXT("objectPath"), TEXT("path"), TEXT("packagePath"), TEXT("level") });
		if (Wanted.IsEmpty())
		{
			Fail(Out, TEXT("instanceName is required — pass the instanceName pie_load_level_instance returned "
						   "(aliases: name, objectPath, path/packagePath/level for the source map)"));
			return;
		}

		// Three ways to address it, because three are genuinely useful: the per-instance package name
		// (unique, the intended handle), the object path (unique), and the SOURCE map path (which may
		// match several instances — refused rather than guessed).
		// Normalized ONCE, outside the loop: the source form is the only one that needs the package
		// grammar applied, and an unparseable value simply never matches rather than erroring here.
		FString SourceCandidate, IgnoredPathError;
		if (!NormalizeLevelPackagePath(Wanted, SourceCandidate, IgnoredPathError))
		{
			SourceCandidate = Wanted;
		}

		TArray<ULevelStreaming*> Matches;
		for (ULevelStreaming* LS : World->GetStreamingLevels())
		{
			if (!LS) { continue; }
			const FString InstanceName = LS->GetWorldAssetPackageFName().ToString();
			if (InstanceName.Equals(Wanted, ESearchCase::IgnoreCase)
				|| LS->GetPathName().Equals(Wanted, ESearchCase::IgnoreCase))
			{
				Matches.Reset();
				Matches.Add(LS);
				break;   // an exact unique handle wins outright
			}
			if (!LS->PackageNameToLoad.IsNone()
				&& LS->PackageNameToLoad.ToString().Equals(SourceCandidate, ESearchCase::IgnoreCase))
			{
				Matches.Add(LS);
			}
		}

		if (Matches.Num() == 0)
		{
			Fail(Out, FString::Printf(
				TEXT("no streaming level '%s' in the PIE world '%s' — list_sublevels {world:\"pie\"} shows what is "
					 "loaded (match on packageName, or on sourcePackage for the map it came from)"),
				*Wanted, *World->GetName()));
			return;
		}
		if (Matches.Num() > 1)
		{
			TArray<FString> Names;
			for (ULevelStreaming* LS : Matches) { Names.Add(LS->GetWorldAssetPackageFName().ToString()); }
			Fail(Out, FString::Printf(
				TEXT("'%s' matches %d level instances (%s) — pass one instanceName; unloading them all would be a "
					 "guess this endpoint will not make"),
				*Wanted, Matches.Num(), *FString::Join(Names, TEXT(", "))));
			return;
		}

		ULevelStreaming* Target = Matches[0];
		const FString InstanceName = Target->GetWorldAssetPackageFName().ToString();
		const FString ObjectPath = Target->GetPathName();

		if (Target->GetIsRequestingUnloadAndRemoval())
		{
			Out->SetBoolField(TEXT("alreadyUnloading"), true);
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetStringField(TEXT("instanceName"), InstanceName);
			Out->SetStringField(TEXT("objectPath"), ObjectPath);
			Out->SetStringField(TEXT("state"), ::EnumToString(Target->GetLevelStreamingState()));
			Out->SetStringField(TEXT("note"), TEXT("an unload was already requested for this instance — nothing changed"));
			return;
		}

		// Both flags on purpose: SetShouldBeLoaded(false) is what makes the streaming update tear the
		// level down, SetIsRequestingUnloadAndRemoval(true) is what removes the ULevelStreaming from
		// the world afterwards. Setting only the second leaves a level that never actually unloads.
		Target->SetShouldBeLoaded(false);
		Target->SetIsRequestingUnloadAndRemoval(true);

		Out->SetBoolField(TEXT("requested"), true);
		Out->SetBoolField(TEXT("changed"), true);
		Out->SetStringField(TEXT("instanceName"), InstanceName);
		Out->SetStringField(TEXT("objectPath"), ObjectPath);
		Out->SetStringField(TEXT("world"), World->GetName());
		Out->SetStringField(TEXT("state"), ::EnumToString(Target->GetLevelStreamingState()));
		Out->SetBoolField(TEXT("pending"), Target->IsStreamingStatePending());
		Out->SetStringField(TEXT("pollWith"), TEXT("list_sublevels {\"world\":\"pie\"}"));
		Out->SetStringField(TEXT("note"),
			TEXT("unloading is ASYNCHRONOUS — this call does NOT block. Poll list_sublevels {world:\"pie\"} until no "
				 "entry has packageName equal to instanceName (the streaming level is removed from the world once the "
				 "teardown completes)."));
	}
	// --- list_data_layers ----------------------------------------------------
	//   in:  { }
	//   out: { partitioned, count, dataLayers:[{ name, shortName, fullName, runtime, initialState,
	//          effectiveState?, debugColor }], note? }
	// Data Layers are how a World Partition map is organised, and nothing in this bridge could see them:
	// list_sublevels answers about streaming levels, which is a different mechanism entirely and is empty
	// on a partitioned map. A caller asking "what is in this world" had no way to find out.
	//
	// Read through UDataLayerManager, NOT UDataLayerSubsystem. The subsystem's GetDataLayerInstances is
	// UE_DEPRECATED(5.3) pointing at exactly this class, and MifBridge has to build on 5.7 as well as
	// 5.3 - a deprecated-in-5.3 call is a 5.7 build break waiting to happen, which is not hypothetical:
	// IsPendingKillOrUnreachable was removed by 5.7 and broke exactly that way. UDataLayerManager and
	// every accessor used here exist unchanged in both.
	void H_list_data_layers(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { },
			TEXT("(none - this endpoint takes no parameters; it reports the Data Layers of the world the "
				 "editor currently has open)"),
			{ { TEXT("world"), TEXT("this always reads the EDITOR world - stop_pie if you want its state to settle") },
			  { TEXT("level"), TEXT("Data Layers belong to the World Partition map, not to a sublevel - use list_sublevels for those") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		const bool bPartitioned = World->IsPartitionedWorld();
		Out->SetStringField(TEXT("world"), World->GetName());
		Out->SetBoolField(TEXT("partitioned"), bPartitioned);

		UDataLayerManager* Manager = UDataLayerManager::GetDataLayerManager(World);
		if (!Manager)
		{
			// Not a failure. A non-partitioned map has no Data Layers by construction, and saying so is
			// more useful than an error a caller has to interpret.
			Out->SetNumberField(TEXT("count"), 0);
			Out->SetArrayField(TEXT("dataLayers"), TArray<TSharedPtr<FJsonValue>>());
			Out->SetStringField(TEXT("note"), bPartitioned
				? TEXT("this world is partitioned but has no DataLayerManager yet - nothing has created a Data Layer in it")
				: TEXT("this is not a World Partition map, so it has no Data Layers. Sublevels are the equivalent here - use list_sublevels."));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Rows;
		Manager->ForEachDataLayerInstance([&Rows](UDataLayerInstance* Instance)
			{
				if (!Instance) { return true; }
				TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
				Row->SetStringField(TEXT("name"), Instance->GetDataLayerFName().ToString());
				Row->SetStringField(TEXT("shortName"), Instance->GetDataLayerShortName());
				Row->SetStringField(TEXT("fullName"), Instance->GetDataLayerFullName());
				// runtime vs editor-only decides whether the layer can be streamed at all, which is the
				// first thing anyone asks about a Data Layer.
				Row->SetBoolField(TEXT("runtime"), Instance->IsRuntime());
				Row->SetStringField(TEXT("initialState"), MifDataLayerStateName(Instance->GetInitialRuntimeState()));
				const FColor C = Instance->GetDebugColor();
				Row->SetStringField(TEXT("debugColor"), FString::Printf(TEXT("#%02X%02X%02X"), C.R, C.G, C.B));
				Rows.Add(MakeShared<FJsonValueObject>(Row));
				return true;
			});

		Out->SetNumberField(TEXT("count"), Rows.Num());
		Out->SetArrayField(TEXT("dataLayers"), Rows);
		if (Rows.Num() == 0)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("the world has a DataLayerManager but no Data Layer instances - none have been created yet."));
		}
	}
}
