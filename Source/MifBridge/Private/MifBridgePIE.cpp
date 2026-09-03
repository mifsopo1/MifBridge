// MifBridge — Play-In-Editor control and runtime observation.
//
// This is what takes the bridge from "it compiles" to "it runs". Everything before this could prove
// a graph was structurally correct and compiled clean; nothing could prove it DID anything.
//
// ── The deadlock constraint, which shapes every endpoint here ──────────────────────────────────
// MifBridgeServer runs each handler inline on the game thread, from the core ticker — so a handler
// body executes between frames, and nothing else advances while it runs. PIE startup is DEFERRED: the
// engine consumes the queued request on a later editor tick (UEditorEngine::IsPlayingSessionInEditor
// is documented as "false ... even if we would start next tick"). So a start_pie that waited for PIE
// to come up would be blocking the very ticks that bring it up. That is an unconditional deadlock.
// Running inline rather than deferring does not soften this: we still hold the game thread.
//
// Therefore: start_pie REQUESTS and returns immediately. The caller polls pie_status. Same for stop —
// EndPlayMap() is unsafe from inside a stack frame like ours, and the engine says so, so we use
// RequestEndPlayMap() and let the next tick action it.
//
// ── The world trap ─────────────────────────────────────────────────────────────────────────────
// During PIE there are TWO worlds. The editor world holds the actors you placed; the PIE world holds
// the live copies actually running. They have different actor paths. list_level_actors goes through
// UEditorActorSubsystem, which serves the EDITOR world — so it keeps reporting placed actors while
// PIE runs, which is a trap if you are trying to observe runtime state. list_pie_actors exists
// specifically to be the other one, and pie_status reports both world names so the difference is
// visible rather than inferred.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Editor.h"                       // GEditor
#include "Editor/EditorEngine.h"          // RequestPlaySession / RequestEndPlayMap / PlayWorld
#include "EngineUtils.h"                  // TActorIterator
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Components/StaticMeshComponent.h"  // spawn_actor_in_pie's mesh support (parity with spawn_actor_in_level)
#include "Engine/StaticMesh.h"               // LoadObject<UStaticMesh>
#include "GameFramework/Actor.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/Pawn.h"
#include "PlayInEditorDataTypes.h"        // FRequestPlaySessionParams
#include "Settings/LevelEditorPlaySettings.h"  // multiplayer PIE topology (clients / net mode)
#include "Misc/OutputDevice.h"
#include "Misc/OutputDeviceRedirector.h"
#include "HAL/CriticalSection.h"

namespace MifBridge
{
	namespace
	{
		// The authoritative PIE world. PlayWorld is set once the session is actually up; it is null
		// while a request is merely queued, which is exactly the window pie_status must report honestly.
		UWorld* GetPIEWorld()
		{
			return GEditor ? GEditor->PlayWorld : nullptr;
		}

		// GetEditorWorld() was the third byte-identical copy of the same two lines (the others were in
		// MifBridgeStreaming.cpp and MifBridgeWorld.cpp, both named EditorWorld). It is now
		// MifBridge::EditorWorld(), defined once in MifBridgeCommon.cpp — a differently-NAMED copy is
		// not a build error, which is precisely why it survived three audits.

		// With RunUnderOneProcess and >1 client there are SEVERAL PIE worlds in this process — a server
		// and one per client — and GEditor->PlayWorld is only ever ONE of them. Spawning a replicated
		// actor into a CLIENT world yields an actor that replicates nowhere, which is a silent wrong
		// answer, so callers must be able to pick the world by net role.
		const TCHAR* NetModeName(ENetMode Mode)
		{
			switch (Mode)
			{
			case NM_Standalone:      return TEXT("standalone");
			case NM_ListenServer:    return TEXT("listenServer");
			case NM_DedicatedServer: return TEXT("dedicatedServer");
			case NM_Client:          return TEXT("client");
			default:                 return TEXT("unknown");
			}
		}


		TSharedRef<FJsonObject> DescribePIEWorld(UWorld* W)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("world"), W->GetName());
			J->SetStringField(TEXT("netMode"), NetModeName(W->GetNetMode()));
			J->SetBoolField(TEXT("isServer"), W->GetNetMode() != NM_Client);
			J->SetBoolField(TEXT("hasBegunPlay"), W->HasBegunPlay());
			int32 Count = 0;
			for (TActorIterator<AActor> It(W); It; ++It)
			{
				++Count;
			}
			J->SetNumberField(TEXT("actorCount"), Count);
			return J;
		}

		void WritePieStateInto(const TSharedRef<FJsonObject>& Out)
		{
			// "running" MUST mean "the world exists and BeginPlay has happened", not merely "a session
			// was requested". UEditorEngine::IsPlayingSessionInEditor() only reports that
			// PlayInEditorSessionInfo is set, which happens BEFORE any world is created — so polling
			// on it returns running while GetPIEWorld() is still null and every actor query comes back
			// "object not found". UWorld::HasBegunPlay() is the real readiness signal.
			UWorld* PIEWorld = GetPIEWorld();
			const bool bSessionActive = GEditor && GEditor->IsPlayingSessionInEditor();
			const bool bRunning = PIEWorld != nullptr && PIEWorld->HasBegunPlay();
			// Session started but the world isn't up yet — still "starting" from a caller's point of view.
			const bool bQueued = GEditor && GEditor->IsPlaySessionInProgress() && !bRunning;

			Out->SetBoolField(TEXT("running"), bRunning);
			Out->SetBoolField(TEXT("startPending"), bQueued);
			// Exposed separately so the distinction is visible rather than folded away.
			Out->SetBoolField(TEXT("sessionActive"), bSessionActive);
			Out->SetBoolField(TEXT("worldHasBegunPlay"), bRunning);
			Out->SetBoolField(TEXT("stopPending"), GEditor && GEditor->ShouldEndPlayMap());
			Out->SetBoolField(TEXT("simulating"), GEditor && GEditor->bIsSimulatingInEditor);

			// A single word the caller can branch on without recombining three booleans.
			const TCHAR* State = bRunning ? TEXT("running") : bQueued ? TEXT("starting") : TEXT("stopped");
			Out->SetStringField(TEXT("state"), State);

			// Reuse the world resolved above rather than re-fetching (and shadowing) it.
			if (PIEWorld)
			{
				Out->SetStringField(TEXT("pieWorld"), PIEWorld->GetName());
				Out->SetNumberField(TEXT("timeSeconds"), PIEWorld->GetTimeSeconds());
				// Actor count is the cheapest "is anything actually alive in there" signal.
				int32 ActorCount = 0;
				for (TActorIterator<AActor> It(PIEWorld); It; ++It) { ++ActorCount; }
				Out->SetNumberField(TEXT("pieActorCount"), ActorCount);
			}
			// Local is NOT named EditorWorld: that is now the shared function's name, and a variable
			// declared with that spelling would shadow it inside its own initialiser.
			if (UWorld* EdWorld = EditorWorld())
			{
				// Named alongside pieWorld so the two-world split is visible, not something you
				// discover by wondering why your actor edits did nothing.
				Out->SetStringField(TEXT("editorWorld"), EdWorld->GetName());
			}
		}

		// ── Log capture ────────────────────────────────────────────────────────────────────────
		// FOutputDeviceRedirector serializes from ANY thread, so the sink must be internally locked;
		// a bare TArray append would be a data race the moment a task thread logs. The device is
		// registered for the duration of one Exec and removed in the destructor, so an early return
		// or an exception inside Exec cannot leave a dangling device registered with GLog.
		class FScopedLogCapture : public FOutputDevice
		{
		public:
			FScopedLogCapture()
			{
				if (GLog) { GLog->AddOutputDevice(this); }
			}
			virtual ~FScopedLogCapture()
			{
				if (GLog) { GLog->RemoveOutputDevice(this); }
			}

			virtual void Serialize(const TCHAR* V, ELogVerbosity::Type Verbosity, const FName& Category) override
			{
				FScopeLock Lock(&Mutex);
				if (Lines.Num() < MaxLines)
				{
					Lines.Add(FString::Printf(TEXT("[%s] %s"), *Category.ToString(), V));
				}
				else
				{
					++Dropped;
				}
			}

			// FOutputDevice requires this for a device that is not thread-safe by default; ours is.
			virtual bool CanBeUsedOnAnyThread() const override { return true; }
			virtual bool CanBeUsedOnMultipleThreads() const override { return true; }

			void Emit(const TSharedRef<FJsonObject>& Out, const FString& Filter)
			{
				FScopeLock Lock(&Mutex);
				TArray<TSharedPtr<FJsonValue>> Arr;
				for (const FString& Line : Lines)
				{
					if (Filter.IsEmpty() || Line.Contains(Filter))
					{
						Arr.Add(MakeShared<FJsonValueString>(Line));
					}
				}
				Out->SetArrayField(TEXT("output"), Arr);
				if (Dropped > 0)
				{
					// Never let a cap look like "that was all of it".
					Out->SetNumberField(TEXT("droppedLines"), Dropped);
				}
			}

		private:
			static constexpr int32 MaxLines = 5000;
			FCriticalSection Mutex;
			TArray<FString> Lines;
			int32 Dropped = 0;
		};
	}

	// --- start_pie ----------------------------------------------------------
	//   in:  { simulate?: false, startLocation?: {x,y,z}, startRotation?: {x,y,z} }
	//   out: { requested, state, ... }
	// REQUESTS the session and returns. Poll pie_status until state=="running".
	void H_start_pie(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("simulate"), TEXT("startLocation"), TEXT("startRotation"), TEXT("players"),
			  TEXT("netMode"), TEXT("oneProcess"), TEXT("width"), TEXT("height") },
			TEXT("simulate, startLocation {x,y,z}, startRotation {x,y,z}, players (1-8), ")
			TEXT("netMode (standalone|listen|client; default listen when players>1), oneProcess (default true), ")
			TEXT("width, height (client window size, multiplayer only)"),
			{ { TEXT("location"), TEXT("use startLocation — and note startRotation is only read when startLocation is supplied too") },
			  { TEXT("rotation"), TEXT("use startRotation; it is only read when startLocation is supplied as well") },
			  { TEXT("clients"), TEXT("use players (clamped to 1-8)") },
			  { TEXT("level"), TEXT("PIE plays whatever level is already open — call load_level first, then start_pie") },
			  { TEXT("map"), TEXT("PIE plays whatever level is already open — call load_level first, then start_pie") },
			  { TEXT("wait"), TEXT("not supported: this handler runs on the game thread, so waiting for PIE would deadlock the ticks that start it. Poll pie_status until state=='running'") } }))
		{
			return;
		}

		if (!GEditor)
		{
			Fail(Out, TEXT("no editor"));
			return;
		}
		if (GEditor->IsPlaySessionInProgress())
		{
			Fail(Out, TEXT("a play session is already running or queued — call stop_pie first, or poll pie_status"));
			return;
		}
		if (!EditorWorld())
		{
			Fail(Out, TEXT("no editor world is open to play"));
			return;
		}

		FRequestPlaySessionParams Params;
		Params.SessionDestination = EPlaySessionDestinationType::InProcess;
		// "Simulate" runs the world without possessing a pawn — the right mode for observing systems
		// tick, since it needs no player start and cannot fail on a missing GameMode.
		Params.WorldType = JBool(In, TEXT("simulate"), false)
			? EPlaySessionWorldType::SimulateInEditor
			: EPlaySessionWorldType::PlayInEditor;

		const TSharedPtr<FJsonObject>* LocObj = nullptr;
		if (In->TryGetObjectField(TEXT("startLocation"), LocObj) && LocObj)
		{
			const TSharedRef<FJsonObject> L = LocObj->ToSharedRef();
			Params.StartLocation = FVector(JNum(L, TEXT("x")), JNum(L, TEXT("y")), JNum(L, TEXT("z")));
			const TSharedPtr<FJsonObject>* RotObj = nullptr;
			if (In->TryGetObjectField(TEXT("startRotation"), RotObj) && RotObj)
			{
				const TSharedRef<FJsonObject> R = RotObj->ToSharedRef();
				Params.StartRotation = FRotator(JNum(R, TEXT("x")), JNum(R, TEXT("y")), JNum(R, TEXT("z")));
			}
		}

		// ── Multiplayer topology ───────────────────────────────────────────────────────────────────
		// The client count and net mode are NOT fields on FRequestPlaySessionParams — they live on
		// ULevelEditorPlaySettings. The naive recipe is to mutate GetMutableDefault<>() (i.e. the user's
		// actual Editor Preferences) and restore it in an OnEndPIE delegate. We don't: the params struct
		// carries `EditorPlaySettings` ("nullptr means use the CDO"), and PlayLevel.cpp duplicates
		// whatever it is handed into the transient package anyway (PlayLevel.cpp:953-962). So we pass a
		// DUPLICATE of the CDO with our overrides on it — the session gets the topology and the user's
		// saved preferences are never written to.
		const int32 Players = FMath::Clamp(JInt(In, TEXT("players"), 1), 1, 8);
		const FString NetModeStr = JStr(In, TEXT("netMode")).ToLower();
		const bool bWantsMulti = (Players > 1) || !NetModeStr.IsEmpty();

		// REFUSE THE MULTIPLAYER-ONLY OPTIONS ON A SINGLE-PLAYER SESSION.
		//
		// MODE-PARAMS-OK: oneProcess/width/height are refused when the session is not multiplayer
		//
		// All three are read INSIDE the block below, so {"oneProcess":false, "width":1280,
		// "height":720} with players=1 and no netMode skipped the whole thing: the window opened at
		// whatever size the editor preferences say, oneProcess was ignored, and the response did not
		// even echo the values back to disagree with. The accepted summary says "multiplayer only"
		// and nothing enforced it - documentation is not a guard.
		//
		// THE GATE IS NOT netMode ALONE, which is what audit_mode_params saw. It is
		// `players > 1 || netMode`, so a caller can enable these EITHER by asking for more players or
		// by naming a net mode - and the refusal says both, because "multiplayer only" without
		// saying how to get there sends people to the docs.
		if (!bWantsMulti)
		{
			static const TCHAR* const kMultiOnly[] = { TEXT("oneProcess"), TEXT("width"),
													   TEXT("height") };
			for (const TCHAR* Key : kMultiOnly)
			{
				if (!In->HasField(Key))
				{
					continue;
				}
				Fail(Out, FString::Printf(
					TEXT("%s only applies to a MULTIPLAYER play session and this one is single-player, "
						 "so it would have been ignored and the window opened at the editor's own "
						 "size. Pass players > 1 or netMode (standalone|listen|client) to make it "
						 "multiplayer, or drop %s. NOTHING was started."), Key, Key));
				return;
			}
		}

		if (bWantsMulti)
		{
			ULevelEditorPlaySettings* Settings = DuplicateObject<ULevelEditorPlaySettings>(
				GetDefault<ULevelEditorPlaySettings>(), GetTransientPackage());
			if (!Settings)
			{
				Fail(Out, TEXT("could not duplicate ULevelEditorPlaySettings for a multiplayer session"));
				return;
			}

			// Default to listen server when more than one client was asked for: that is the topology a
			// co-op mod actually ships into (one player hosts), and it needs no separate server process.
			EPlayNetMode NetMode = EPlayNetMode::PIE_ListenServer;
			if (NetModeStr == TEXT("standalone"))                                      { NetMode = EPlayNetMode::PIE_Standalone; }
			else if (NetModeStr == TEXT("client") || NetModeStr == TEXT("dedicated"))   { NetMode = EPlayNetMode::PIE_Client; }
			else if (!NetModeStr.IsEmpty()
				  && NetModeStr != TEXT("listen") && NetModeStr != TEXT("listenserver"))
			{
				Fail(Out, FString::Printf(
					TEXT("unknown netMode '%s' — use 'standalone', 'listen' (listen server, the default ")
					TEXT("when players>1) or 'client' (a dedicated server is spawned for you)"), *NetModeStr));
				return;
			}

			Settings->SetPlayNumberOfClients(Players);
			Settings->SetPlayNetMode(NetMode);
			// One process = all client windows in this editor. Far faster to start, and it keeps every
			// client's log in the SAME output — which is the whole point for us, since a co-op bug is
			// usually "host did X, client didn't".
			Settings->SetRunUnderOneProcess(JBool(In, TEXT("oneProcess"), true));
			Settings->NewWindowWidth  = FMath::Clamp(JInt(In, TEXT("width"),  640), 64, 4096);
			Settings->NewWindowHeight = FMath::Clamp(JInt(In, TEXT("height"), 360), 64, 4096);

			Params.EditorPlaySettings = Settings;

			const UEnum* ModeEnum = StaticEnum<EPlayNetMode>();
			Out->SetNumberField(TEXT("players"), Players);
			Out->SetStringField(TEXT("netMode"),
				ModeEnum ? ModeEnum->GetNameStringByValue((int64)NetMode) : TEXT("?"));
			Out->SetBoolField(TEXT("oneProcess"), JBool(In, TEXT("oneProcess"), true));
			// Say explicitly that we did NOT touch saved preferences — otherwise the only way to know is
			// to go and look at Editor Preferences.
			Out->SetStringField(TEXT("settingsScope"),
				TEXT("per-session duplicate — Editor Preferences were NOT modified"));
		}

		GEditor->RequestPlaySession(Params);

		Out->SetBoolField(TEXT("requested"), true);
		// Say it plainly: nothing has started yet at the moment this returns.
		Out->SetStringField(TEXT("note"),
			TEXT("PIE start is deferred to the next editor tick — this call does NOT block. Poll pie_status until state=='running' before asserting on runtime state."));
		WritePieStateInto(Out);
		UE_LOG(LogMifBridge, Log, TEXT("start_pie: requested (%s, players=%d%s)"),
			JBool(In, TEXT("simulate"), false) ? TEXT("simulate") : TEXT("play"),
			Players, bWantsMulti ? TEXT(", multiplayer") : TEXT(""));
	}

	// --- stop_pie -----------------------------------------------------------
	void H_stop_pie(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {}, TEXT("(none - this endpoint takes no parameters)"),
			{ { TEXT("wait"), TEXT("not supported: the stop is deferred to the next editor tick and this handler holds the game thread. Poll pie_status until state=='stopped'") },
			  { TEXT("force"), TEXT("not supported: RequestEndPlayMap is the only safe teardown from inside the ticker; EndPlayMap here would tear the world down under this callstack") } }))
		{
			return;
		}

		if (!GEditor)
		{
			Fail(Out, TEXT("no editor"));
			return;
		}
		if (!GEditor->IsPlaySessionInProgress())
		{
			Out->SetBoolField(TEXT("wasRunning"), false);
			WritePieStateInto(Out);
			return;
		}
		// RequestEndPlayMap, NOT EndPlayMap: the engine documents the former as the one to use when
		// it is "not safe to directly call EndPlayMap in your stack frame", and we are inside a
		// game-thread task mid-frame. Calling EndPlayMap here would tear the world down underneath
		// the very callstack iterating it.
		GEditor->RequestEndPlayMap();

		Out->SetBoolField(TEXT("wasRunning"), true);
		Out->SetStringField(TEXT("note"), TEXT("stop is deferred to the next editor tick — poll pie_status until state=='stopped'"));
		WritePieStateInto(Out);
	}

	// --- pie_status ---------------------------------------------------------
	void H_pie_status(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {}, TEXT("(none - this endpoint takes no parameters)"),
			{ { TEXT("netMode"), TEXT("this endpoint always reports GEditor->PlayWorld; use list_pie_actors {netMode:server|client|any} to address a specific PIE world") },
			  { TEXT("waitFor"), TEXT("not supported: nothing can block here without stalling the ticks PIE needs. Call pie_status repeatedly instead") } }))
		{
			return;
		}

		WritePieStateInto(Out);

		// Who is actually playing, when there is someone.
		if (UWorld* PIEWorld = GetPIEWorld())
		{
			if (APlayerController* PC = PIEWorld->GetFirstPlayerController())
			{
				Out->SetStringField(TEXT("playerController"), PC->GetPathName());
				if (APawn* Pawn = PC->GetPawn())
				{
					Out->SetStringField(TEXT("pawn"), Pawn->GetPathName());
					Out->SetStringField(TEXT("pawnClass"), Pawn->GetClass()->GetPathName());
				}
			}
		}
	}

	// --- list_pie_actors ----------------------------------------------------
	//   in:  { classFilter?, nameContains?, limit? }
	//   out: { world, count, matched, truncated, actors:[{actorPath, name, class, location}] }
	// The PIE-world counterpart to list_level_actors. The returned actorPath is a LIVE object, so
	// get_property against it reads the running value — which is what makes runtime assertions work
	// without any new inspection machinery.
	void H_list_pie_actors(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("classFilter"), TEXT("nameContains"), TEXT("limit"), TEXT("netMode") },
			TEXT("classFilter, nameContains, limit (1-5000, default 200), netMode (server|client|any; default server)"),
			{ { TEXT("class"), TEXT("use classFilter — a SUBSTRING matched against the actor's class and every super, not an exact class path") },
			  { TEXT("world"), TEXT("use netMode (server|client|any) to pick which PIE world answers; the returned 'worlds' array shows what is running") },
			  { TEXT("actorClass"), TEXT("use classFilter (substring match)") } }))
		{
			return;
		}

		// With RunUnderOneProcess and >1 client there are SEVERAL PIE worlds. This used to always serve
		// GEditor->PlayWorld and SILENTLY IGNORE netMode, so asking for the client returned the server's
		// actors — a confident wrong answer, and with co-op that is the whole measurement. Select by role
		// like spawn_actor_in_pie does, and always report which world actually answered.
		TArray<UWorld*> PIEWorlds;
		CollectPIEWorlds(PIEWorlds);
		if (PIEWorlds.Num() == 0)
		{
			Fail(Out, TEXT("no PIE world — not playing. start_pie, then poll pie_status until state=='running'."));
			return;
		}
		{
			TArray<TSharedPtr<FJsonValue>> Arr;
			for (UWorld* W : PIEWorlds) { Arr.Add(MakeShared<FJsonValueObject>(DescribePIEWorld(W))); }
			Out->SetArrayField(TEXT("worlds"), Arr);
		}

		// Default "server" keeps every existing caller on the world they already got (PlayWorld is the
		// server in a listen-server session), so this is additive, not a behaviour change under the feet.
		const FString WantRole = JStr(In, TEXT("netMode"), TEXT("server")).ToLower();
		UWorld* PIEWorld = nullptr;
		for (UWorld* W : PIEWorlds)
		{
			const bool bIsServer = (W->GetNetMode() != NM_Client);
			if (WantRole == TEXT("any")
				|| (WantRole == TEXT("server") && bIsServer)
				|| (WantRole == TEXT("client") && !bIsServer))
			{
				PIEWorld = W;
				break;
			}
		}
		if (!PIEWorld)
		{
			Fail(Out, FString::Printf(
				TEXT("no PIE world matching netMode '%s' — see the 'worlds' array (valid: server, client, any)"),
				*WantRole));
			return;
		}
		// Report the role that answered, not just the world NAME — every PIE world here is called
		// "Untitled", so the name alone cannot tell a caller which one they got.
		Out->SetStringField(TEXT("netMode"), NetModeName(PIEWorld->GetNetMode()));
		Out->SetBoolField(TEXT("isServer"), PIEWorld->GetNetMode() != NM_Client);

		const FString ClassFilter  = JStr(In, TEXT("classFilter"));
		const FString NameContains = JStr(In, TEXT("nameContains"));
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 200), 1, 5000);

		TArray<TSharedPtr<FJsonValue>> Arr;
		int32 Matched = 0;
		bool bTruncated = false;
		for (TActorIterator<AActor> It(PIEWorld); It; ++It)
		{
			AActor* Actor = *It;
			if (!Actor || !IsValid(Actor))
			{
				continue;
			}
			if (!ClassFilter.IsEmpty())
			{
				bool bMatch = false;
				for (UClass* C = Actor->GetClass(); C; C = C->GetSuperClass())
				{
					if (C->GetName().Contains(ClassFilter)) { bMatch = true; break; }
				}
				if (!bMatch) { continue; }
			}
			if (!NameContains.IsEmpty() && !Actor->GetName().Contains(NameContains))
			{
				continue;
			}
			++Matched;
			if (Arr.Num() >= Limit) { bTruncated = true; continue; }

			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("actorPath"), Actor->GetPathName());
			J->SetStringField(TEXT("name"), Actor->GetName());
			J->SetStringField(TEXT("class"), Actor->GetClass()->GetPathName());
			const FVector Loc = Actor->GetActorLocation();
			TSharedRef<FJsonObject> V = MakeShared<FJsonObject>();
			V->SetNumberField(TEXT("x"), Loc.X); V->SetNumberField(TEXT("y"), Loc.Y); V->SetNumberField(TEXT("z"), Loc.Z);
			J->SetObjectField(TEXT("location"), V);
			Arr.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetStringField(TEXT("world"), PIEWorld->GetName());
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetNumberField(TEXT("matched"), Matched);
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("actors"), Arr);
	}

	// --- run_console_captured -----------------------------------------------
	//   in:  { command, filter? }   out: { command, executed, output[], droppedLines? }
	// run_console returns only a bool, because GEngine->Exec's return says whether a handler CLAIMED
	// the command, not what it printed — and mif.kr.* commands are FConsoleCommandWithArgsDelegate
	// handlers that UE_LOG rather than writing to the Exec archive. So the only way to see their
	// output is to sit on GLog for the duration of the call.
	void H_run_console_captured(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("command"), TEXT("filter") },
			TEXT("command, filter (substring; only log lines containing it are returned)"),
			{ { TEXT("cmd"), TEXT("use command — the 'cmd' alias exists only on run_console, not here") },
			  { TEXT("world"), TEXT("not selectable here: this endpoint runs against the PIE world when playing and the editor world otherwise. Use run_console {world:editor|pie|active} to choose") },
			  { TEXT("captureOutput"), TEXT("capture is unconditional here — that is what this endpoint is for; run_console has the toggle") } }))
		{
			return;
		}

		const FString Cmd = JStr(In, TEXT("command"));
		if (Cmd.IsEmpty())
		{
			Fail(Out, TEXT("command is required"));
			return;
		}

		UWorld* World = GetPIEWorld();
		if (!World) { World = EditorWorld(); }

		bool bExecuted = false;
		FString ExecText;
		{
			FScopedLogCapture Capture;   // registered here, removed on scope exit whatever happens
			// Batch O: routed through MifBridge::RunEngineExec (MifBridgeCommon.cpp) so this module has
			// exactly ONE UEngine::Exec call site — run_console is the other caller and a third copy is
			// the PM-005 bug class. Behaviour is unchanged for `output`: the tee forwards every
			// Serialize to GLog, which is where the default Ar (*GLog) sent it before, so FScopedLogCapture
			// still sees precisely what it saw. `execOutput` is new and additive: the command's OWN
			// output-device text, which is a different thing from the log lines bracketed here.
			bExecuted = RunEngineExec(World, Cmd, &ExecText);
			Capture.Emit(Out, JStr(In, TEXT("filter")));
		}
		Out->SetStringField(TEXT("execOutput"), ExecText);

		Out->SetStringField(TEXT("command"), Cmd);
		// false means no handler claimed it — not necessarily an error, and not a claim about output.
		Out->SetBoolField(TEXT("executed"), bExecuted);
		Out->SetStringField(TEXT("world"), World ? World->GetName() : TEXT("<none>"));
		// Only output logged SYNCHRONOUSLY during Exec is captured. A command that kicks off async
		// work reports nothing here; tail the log instead.
		Out->SetBoolField(TEXT("synchronousOnly"), true);
	}

	// --- spawn_actor_in_pie -------------------------------------------------
	//   in:  { actorClass|class, location?, rotation?, scale?, label?, netMode? = "server" }
	//   out: { actor:{...}, targetWorld:{...}, worlds:[...] }
	//
	// Why this exists: spawn_actor_in_level goes through UEditorActorSubsystem, which serves the EDITOR
	// world — it cannot put an actor into a running game. This mod's real bootstrap is UE4SS spawning a
	// ModActor at runtime, and UE4SS does not run in the editor, so without this there is no way to
	// exercise the mod's actual BeginPlay under PIE. Placing the actor in the map instead does NOT work:
	// DDS2 travels off the opened map on play (IslaSombra -> OpenWorld) and placed actors do not survive
	// the travel.
	void H_spawn_actor_in_pie(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// The unfixed sibling of the spawn_actor_in_level postmortem (docs/01_POSTMORTEMS.md): that
		// endpoint's `mesh` fix and its strict-params guard were never swept across to this one, so
		// {actorClass:"StaticMeshActor", mesh:"/Game/..."} reproduced the postmortem exactly — a bare
		// AStaticMeshActor, ok:true, empty bounds, and the caller debugging the mesh asset.
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorClass"), TEXT("class"), TEXT("location"), TEXT("rotation"), TEXT("scale"),
			  TEXT("mesh"), TEXT("staticMesh"), TEXT("label"), TEXT("netMode") },
			TEXT("actorClass (alias: class), location, rotation, scale, mesh (alias: staticMesh), label, ")
			TEXT("netMode (server|client|any; default server)"),
			{ { TEXT("material"), TEXT("not supported here — spawn the actor, then set_property on the mesh component's OverrideMaterials") },
			  { TEXT("folder"), TEXT("folders are an editor-outliner concept; a PIE-spawned actor has none") } }))
		{
			return;
		}

		TArray<UWorld*> Worlds;
		CollectPIEWorlds(Worlds);
		// Always report what WAS available: on failure this is the difference between "no PIE running"
		// and "PIE is up but not in the role you asked for".
		{
			TArray<TSharedPtr<FJsonValue>> Arr;
			for (UWorld* W : Worlds)
			{
				Arr.Add(MakeShared<FJsonValueObject>(DescribePIEWorld(W)));
			}
			Out->SetArrayField(TEXT("worlds"), Arr);
		}
		if (Worlds.Num() == 0)
		{
			Fail(Out, TEXT("no PIE world — not playing. start_pie, then poll pie_status until state=='running'."));
			return;
		}

		// Default to the SERVER: a replicated actor spawned there reaches every client, which is the
		// entire point of a co-op test. "client" exists only for deliberately asymmetric checks.
		const FString Want = JStr(In, TEXT("netMode"), TEXT("server")).ToLower();
		UWorld* Target = nullptr;
		for (UWorld* W : Worlds)
		{
			const bool bIsServer = (W->GetNetMode() != NM_Client);
			if (Want == TEXT("any")
				|| (Want == TEXT("server") && bIsServer)
				|| (Want == TEXT("client") && !bIsServer))
			{
				Target = W;
				break;
			}
		}
		if (!Target)
		{
			Fail(Out, FString::Printf(
				TEXT("no PIE world matching netMode '%s' — see the 'worlds' array for what is running ")
				TEXT("(valid: server, client, any)"), *Want));
			return;
		}
		if (!Target->HasBegunPlay())
		{
			Fail(Out, TEXT("target PIE world has not begun play — poll pie_status until state=='running'"));
			return;
		}

		UClass* ActorClass = ResolveClassStrictField(In, { TEXT("actorClass"), TEXT("class") },
			nullptr, Out);
		if (!ActorClass)
		{
			return; // ResolveClassStrictField already wrote the failure
		}
		if (!ActorClass->IsChildOf(AActor::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not an Actor class: '%s'"), *ActorClass->GetName()));
			return;
		}
		if (ActorClass->HasAnyClassFlags(CLASS_Abstract))
		{
			Fail(Out, FString::Printf(TEXT("'%s' is abstract and cannot be spawned"), *ActorClass->GetName()));
			return;
		}

		FVector Location = FVector::ZeroVector;
		FRotator Rotation = FRotator::ZeroRotator;
		FVector Scale = FVector::OneVector;
		bool bHasScale = false;
		const TSharedPtr<FJsonObject>* Obj = nullptr;
		if (In->TryGetObjectField(TEXT("location"), Obj) && Obj)
		{
			const TSharedRef<FJsonObject> L = Obj->ToSharedRef();
			Location = FVector(JNum(L, TEXT("x")), JNum(L, TEXT("y")), JNum(L, TEXT("z")));
		}
		if (In->TryGetObjectField(TEXT("rotation"), Obj) && Obj)
		{
			// x/y/z = pitch/yaw/roll, matching spawn_actor_in_level.
			const TSharedRef<FJsonObject> R = Obj->ToSharedRef();
			Rotation = FRotator(JNum(R, TEXT("x")), JNum(R, TEXT("y")), JNum(R, TEXT("z")));
		}
		if (In->TryGetObjectField(TEXT("scale"), Obj) && Obj)
		{
			const TSharedRef<FJsonObject> S = Obj->ToSharedRef();
			Scale = FVector(JNum(S, TEXT("x"), 1.0), JNum(S, TEXT("y"), 1.0), JNum(S, TEXT("z"), 1.0));
			bHasScale = true;
		}

		FActorSpawnParameters SpawnParams;
		SpawnParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
		AActor* Actor = Target->SpawnActor<AActor>(ActorClass, Location, Rotation, SpawnParams);
		if (!Actor)
		{
			Fail(Out, FString::Printf(TEXT("SpawnActor returned null for '%s' in %s"),
				*ActorClass->GetName(), *Target->GetName()));
			return;
		}
		if (bHasScale)
		{
			Actor->SetActorScale3D(Scale);
		}

		// Ported verbatim in intent from spawn_actor_in_level: a mesh path that is accepted and dropped
		// spawns an EMPTY StaticMeshActor and reports ok, and the caller only finds out when the actor
		// has no bounds. Honour it, or say why it cannot be honoured — never neither.
		const FString MeshPath = JStrAny(In, { TEXT("mesh"), TEXT("staticMesh") });
		if (!MeshPath.IsEmpty())
		{
			UStaticMeshComponent* MeshComp = Actor->FindComponentByClass<UStaticMeshComponent>();
			if (!MeshComp)
			{
				Actor->Destroy();
				Fail(Out, FString::Printf(
					TEXT("'%s' has no StaticMeshComponent, so mesh '%s' cannot be applied — spawn a StaticMeshActor instead"),
					*ActorClass->GetName(), *MeshPath));
				return;
			}
			UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, *MeshPath);
			if (!Mesh)
			{
				Actor->Destroy();
				Fail(Out, FString::Printf(TEXT("could not load static mesh '%s'"), *MeshPath));
				return;
			}
			// Spawned StaticMeshActors default to Static mobility, which refuses SetStaticMesh.
			const EComponentMobility::Type OldMobility = MeshComp->Mobility;
			MeshComp->SetMobility(EComponentMobility::Movable);
			MeshComp->SetStaticMesh(Mesh);
			MeshComp->SetMobility(OldMobility);
			if (MeshComp->GetStaticMesh() != Mesh)
			{
				// Read back rather than assume: mobility rules or a native setter can refuse.
				Actor->Destroy();
				Fail(Out, FString::Printf(TEXT("mesh '%s' did not take on '%s' (the component still holds a different mesh); the actor was destroyed rather than left empty"),
					*MeshPath, *ActorClass->GetName()));
				return;
			}
		}

		const FString Label = JStr(In, TEXT("label"));
		if (!Label.IsEmpty())
		{
#if WITH_EDITOR
			{
				FString ActualLabel, LabelNote;
				SetActorLabelChecked(Actor, Label, ActualLabel, LabelNote);
				Out->SetStringField(TEXT("labelActual"), ActualLabel);
				if (!LabelNote.IsEmpty()) { Out->SetStringField(TEXT("labelNote"), LabelNote); }
			}
#endif
		}

		TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
		A->SetStringField(TEXT("class"), ActorClass->GetPathName());
		A->SetStringField(TEXT("name"), Actor->GetName());
		A->SetStringField(TEXT("actorPath"), Actor->GetPathName());
		// Report these rather than let the caller assume: a co-op test turns on exactly this.
		A->SetBoolField(TEXT("hasAuthority"), Actor->HasAuthority());
		A->SetBoolField(TEXT("replicates"), Actor->GetIsReplicated());
		if (!MeshPath.IsEmpty()) { A->SetStringField(TEXT("mesh"), MeshPath); }
		Out->SetObjectField(TEXT("actor"), A);
		Out->SetObjectField(TEXT("targetWorld"), DescribePIEWorld(Target));
		// A runtime spawn's BeginPlay fires immediately, unlike a placed actor's — say so, because the
		// caller's next move is normally to assert on whatever BeginPlay was meant to do.
		Out->SetStringField(TEXT("note"),
			TEXT("spawned into the running PIE world; BeginPlay has already fired. Not saved to any map — "
			     "it disappears when PIE stops."));
		UE_LOG(LogMifBridge, Log, TEXT("spawn_actor_in_pie: %s -> %s (%s, authority=%d)"),
			*ActorClass->GetName(), *Target->GetName(), NetModeName(Target->GetNetMode()),
			Actor->HasAuthority() ? 1 : 0);
	}

}
