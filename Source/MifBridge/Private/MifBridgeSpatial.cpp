// MifBridge — SPATIAL AWARENESS and VISUAL FEEDBACK for level building.
//
// Written after a 70-actor scene was built blind and came out wrong in every avoidable way:
// cliff-sized rocks swallowing buildings, thigh-high grass, palms inside walls, ground planes
// visibly floating. Every one of those is detectable from data — none of them needed a human eye.
//
// The lesson encoded here: NUMBERS FOR CORRECTNESS, PIXELS FOR TASTE.
//   get_actor_bounds / check_overlaps / trace_ground answer "is this wrong?" in milliseconds and
//   are scriptable in a placement loop. capture_camera answers "does this look good?", which is the
//   only question that actually needs rendering.
//
// A note on why bounds are read from the PLACED ACTOR and not the mesh asset: the asset's
// ExtendedBounds ignores the actor's scale. Reading SM_Large_Rock_01's asset bounds says 1312u tall;
// placed at scale 2.4 it is 3150u, and it was that gap that buried a building. AActor::GetActorBounds
// returns the real world-space extent, scale included.
//
// A note on capture_camera and the USER'S viewport: they are, and always were, two different cameras.
// set_viewport_camera (MifBridgeViewport.cpp) moves what the user sees; capture_camera spawns its own
// transient ASceneCapture2D. Nothing was wired between them, so "point the camera, then capture" shot
// from (0,0,500) while both endpoints answered ok:true - docs/06_OPEN_ISSUES_FROM_USE.md #7. There is now
// an OPT-IN useViewportCamera:true, and cameraSource is echoed on EVERY capture so the split is visible
// in the JSON instead of only in the picture.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Editor.h"
#include "EditorViewportClient.h"   // FEditorViewportClient - GetViewLocation/GetViewRotation/ViewFOV
#include "Engine/Engine.h"
#include "Engine/SceneCapture2D.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Components/SceneCaptureComponent2D.h"
#include "GameFramework/Actor.h"
#include "Kismet/KismetRenderingLibrary.h"
#include "LevelEditorViewport.h"     // FLevelEditorViewportClient - the useViewportCamera seed
#include "CollisionQueryParams.h"
#include "Misc/Paths.h"
#include "HAL/PlatformFileManager.h"

namespace MifBridge
{
	namespace
	{
		// ActiveWorld() is MifBridge::ActiveWorld() now — same policy (prefer the PIE world when
		// playing so queries match what is actually running, fall back to the editor world for
		// level-building), one definition instead of five near-copies with two different answers.

		// The actor finder moved to MifBridgeCommon.cpp as MifBridge::FindActorInWorld (declared in
		// MifBridgeHandlers.h). FIVE byte-identical copies existed under five different names
		// (FindActor, FindNavActor, FindActorByPathOrLabel, FindVpActor, FindWorldActor) — different
		// names are not a build error, which is exactly why they survived, but it meant a fix to the
		// path/name/label matching rule landed in one of five places. Do NOT add a sixth.

		// Vec3 moved to MifBridgeCommon.cpp (declared in MifBridgeHandlers.h). This file's FVector form
		// and MifBridgeStreaming.cpp's 3-double form were BOTH in unity blob 2 already and compiled
		// only because their arities differed — so they shared one cross-file overload set and any
		// signature change on either side was an instant C2084. Both overloads now exist once.

		// World-space AABB of a placed actor. bOnlyColliding=false so meshes WITHOUT collision still
		// report their visual size — editor-world collision is unreliable for imported props, and a
		// silent zero-extent box would defeat the whole point of overlap checking.
		bool ActorBox(AActor* A, FBox& OutBox)
		{
			if (!A) { return false; }
			FVector Origin, Extent;
			A->GetActorBounds(/*bOnlyCollidingComponents*/ false, Origin, Extent, /*bIncludeFromChildActors*/ true);
			if (Extent.IsNearlyZero()) { return false; }
			OutBox = FBox(Origin - Extent, Origin + Extent);
			return true;
		}

		// The active level viewport, falling back to the first PERSPECTIVE one, then to index 0.
		// DELIBERATE NEAR-COPY of MifBridgeViewport.cpp's ActiveLevelViewport (same policy, same
		// fallback order) under a distinctly prefixed name: a unity build merges every unnamed
		// namespace in a translation unit, so sharing the spelling is a hard C2084 the moment the two
		// files land in one blob — and this file may not edit MifBridgeViewport.cpp or the shared
		// header, which is where the one true copy belongs.
		//
		// EVICTION CLAUSE. This is now the SECOND copy, which is exactly the promotion trigger this
		// codebase already applies (EmitAssetIdentity, CollectPIEWorlds, FindActorInWorld). Promote it
		// to MifBridge::ActiveLevelViewport in MifBridgeHandlers.h / MifBridgeCommon.cpp on the next
		// integrator pass and delete BOTH file-local copies. Until then the two MUST agree: if they
		// ever pick different clients, set_viewport_camera and capture_camera are back to sharing no
		// state, which is the precise defect capture_camera was changed to close. That is why the
		// chosen client's INDEX is echoed in the response — a caller can diff it against
		// get_viewport_camera instead of trusting that the two copies still match.
		FLevelEditorViewportClient* MifSpatialActiveLevelViewport(int32& OutIndex, int32& OutCount)
		{
			OutIndex = INDEX_NONE;
			OutCount = 0;
			if (!GEditor) { return nullptr; }
			const TArray<FLevelEditorViewportClient*>& Clients = GEditor->GetLevelViewportClients();
			OutCount = Clients.Num();
			FLevelEditorViewportClient* FirstPerspective = nullptr;
			int32 FirstPerspectiveIndex = INDEX_NONE;
			for (int32 i = 0; i < Clients.Num(); ++i)
			{
				FLevelEditorViewportClient* Client = Clients[i];
				if (!Client) { continue; }
				if (!FirstPerspective && Client->IsPerspective())
				{
					FirstPerspective = Client;
					FirstPerspectiveIndex = i;
				}
				if (Client->Viewport && Client->Viewport == GEditor->GetActiveViewport())
				{
					OutIndex = i;
					return Client;
				}
			}
			if (FirstPerspective) { OutIndex = FirstPerspectiveIndex; return FirstPerspective; }
			if (Clients.Num() > 0 && Clients[0]) { OutIndex = 0; return Clients[0]; }
			return nullptr;
		}
	}

	// --- get_actor_bounds ---------------------------------------------------
	//   in:  { actorPath }   out: { origin, extent, size, min, max, hasBounds }
	void H_get_actor_bounds(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("path") },
			TEXT("actorPath (aliases: actor, path) — the PLACED actor to measure, given as an object path, object name or label"),
			{ { TEXT("assetPath"), TEXT("bounds are read from the PLACED actor, not the mesh asset — the asset's ExtendedBounds ignore the actor's scale. Pass the actor as actorPath") },
			  { TEXT("label"), TEXT("actorPath already accepts a label, an object name or a full path — use it") },
			  { TEXT("onlyColliding"), TEXT("not a parameter — bounds always include non-colliding components, because editor-world collision is unreliable for imported props") } }))
		{
			return;
		}

		UWorld* World = ActiveWorld();
		AActor* A = FindActorInWorld(World, JStrAny(In, { TEXT("actorPath"), TEXT("actor"), TEXT("path") }));
		if (!A) { Fail(Out, TEXT("actor not found (accepts actorPath, name or label)")); return; }

		FVector Origin, Extent;
		A->GetActorBounds(false, Origin, Extent, true);
		Out->SetStringField(TEXT("actorPath"), A->GetPathName());
		Out->SetObjectField(TEXT("origin"), Vec3(Origin));
		Out->SetObjectField(TEXT("extent"), Vec3(Extent));
		Out->SetObjectField(TEXT("size"), Vec3(Extent * 2.0));   // what you actually compare against
		Out->SetObjectField(TEXT("min"), Vec3(Origin - Extent));
		Out->SetObjectField(TEXT("max"), Vec3(Origin + Extent));
		Out->SetBoolField(TEXT("hasBounds"), !Extent.IsNearlyZero());
	}

	// --- check_overlaps -----------------------------------------------------
	//   in:  { actorPath? , nameContains?, ignoreGround?, tolerance? }
	//   out: { pairs:[{a,b,overlapVolume}], count }
	// With no actorPath this is a WHOLE-SCENE audit — the "what did I get wrong" endpoint.
	// Pure AABB math on cached bounds, no world collision queries, so it works on meshes that have
	// no collision at all (which is most imported props in an editor world).
	void H_check_overlaps(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("nameContains"), TEXT("ignoreGround"), TEXT("tolerance") },
			TEXT("actorPath (alias: actor) to test ONE actor, or omit both for a whole-scene audit; nameContains, ignoreGround, tolerance"),
			{ { TEXT("path"), TEXT("this endpoint takes actorPath (alias: actor) only — 'path' is accepted by get_actor_bounds, not here") },
			  { TEXT("name"), TEXT("use nameContains for a substring filter over object names and labels, or actorPath to test a single actor") },
			  { TEXT("depth"), TEXT("'depth' is an OUTPUT field on each reported pair — the input threshold is 'tolerance' (default 25)") } }))
		{
			return;
		}

		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		const FString Single = JStrAny(In, { TEXT("actorPath"), TEXT("actor") });
		const FString NameFilter = JStr(In, TEXT("nameContains"));
		// Small overlaps are normal and desirable — foliage should touch the ground, props should
		// touch walls. Only report intersections deeper than this on the smallest axis.
		const double Tolerance = JNum(In, TEXT("tolerance"), 25.0);

		struct FEntry { AActor* Actor; FBox Box; };
		TArray<FEntry> Entries;
		for (TActorIterator<AActor> It(World); It; ++It)
		{
			AActor* A = *It;
			if (!A || !IsValid(A)) { continue; }
			if (!NameFilter.IsEmpty() && !A->GetName().Contains(NameFilter) && !A->GetActorLabel().Contains(NameFilter)) { continue; }
			// The ground plane overlaps everything by design; excluding it is the default.
			if (JBool(In, TEXT("ignoreGround"), true) && A->GetActorLabel().Contains(TEXT("Ground"))) { continue; }
			FBox B;
			if (ActorBox(A, B)) { Entries.Add({ A, B }); }
		}

		// A NOT-FOUND actor must not degrade into a whole-scene audit. FindActorInWorld returns null
		// both for "you did not ask for one" and for "the one you asked for is not here", and the
		// filter below is skipped on null - so a mistyped actorPath silently widened the question from
		// "does THIS actor overlap" to "list every overlap in the level", and answered ok:true with
		// 108 pairs that had nothing to do with the request.
		AActor* Target = nullptr;
		if (!Single.IsEmpty())
		{
			Target = FindActorInWorld(World, Single);
			if (!Target)
			{
				Fail(Out, FString::Printf(
					TEXT("actor not found: '%s'. Nothing was tested. Omit actorPath entirely for a "
						 "whole-scene audit - leaving it in with a name that does not resolve would "
						 "otherwise return every overlap in the level as though it were this actor's."),
					*Single));
				return;
			}
		}
		TArray<TSharedPtr<FJsonValue>> Pairs;
		for (int32 i = 0; i < Entries.Num(); ++i)
		{
			if (Target && Entries[i].Actor != Target) { continue; }
			for (int32 j = 0; j < Entries.Num(); ++j)
			{
				if (i == j) { continue; }
				if (!Target && j <= i) { continue; }   // each unordered pair once
				const FBox Inter = Entries[i].Box.Overlap(Entries[j].Box);
				if (!Inter.IsValid) { continue; }
				const FVector S = Inter.GetSize();
				// Depth on the SHALLOWEST axis — two boxes that merely touch have a near-zero axis.
				const double MinAxis = FMath::Min3(S.X, S.Y, S.Z);
				if (MinAxis < Tolerance) { continue; }

				TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
				P->SetStringField(TEXT("a"), Entries[i].Actor->GetActorLabel());
				P->SetStringField(TEXT("b"), Entries[j].Actor->GetActorLabel());
				P->SetStringField(TEXT("aPath"), Entries[i].Actor->GetPathName());
				P->SetStringField(TEXT("bPath"), Entries[j].Actor->GetPathName());
				P->SetNumberField(TEXT("depth"), MinAxis);
				Pairs.Add(MakeShared<FJsonValueObject>(P));
			}
		}

		Out->SetNumberField(TEXT("actorsTested"), Entries.Num());
		Out->SetNumberField(TEXT("count"), Pairs.Num());
		Out->SetArrayField(TEXT("pairs"), Pairs);
	}

	// --- trace_ground -------------------------------------------------------
	//   in:  { x, y, fromZ?, ignoreActor? }   out: { hit, z, actorPath?, normal? }
	// Editor-world collision is NOT guaranteed for imported meshes, so a miss is reported honestly
	// rather than silently returning 0 — a caller that treats "no hit" as "ground at z=0" is exactly
	// how things end up floating.
	void H_trace_ground(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("x"), TEXT("y"), TEXT("fromZ"), TEXT("toZ"), TEXT("location"),
			  TEXT("ignoreActor"), TEXT("actorPath") },
			TEXT("x, y (or location:{x,y,z}, whose z seeds fromZ), fromZ, toZ, ignoreActor (alias: actorPath)"),
			{ { TEXT("z"), TEXT("there is no top-level z — the trace START is 'fromZ' (default 100000) and the END is 'toZ' (default -100000). location:{x,y,z} also seeds fromZ from its z") },
			  { TEXT("ignore"), TEXT("the key is 'ignoreActor' (alias: actorPath); it accepts an object path, object name or label") },
			  { TEXT("channel"), TEXT("not a parameter — this always traces ECC_WorldStatic with complex collision") } }))
		{
			return;
		}

		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		// Accept BOTH {x,y} and {location:{x,y,z}}. Every other endpoint in this bridge takes a
		// location object, so callers naturally send one here too — and when this handler read only
		// the top-level keys it silently traced at the world ORIGIN for every request, returning a
		// confident hit for a point nowhere near the one asked about. Same silent-ignore class as the
		// naming traps: honour the spelling the caller reasonably used.
		double X = JNum(In, TEXT("x"));
		double Y = JNum(In, TEXT("y"));
		double FromZ = JNum(In, TEXT("fromZ"), 100000.0);
		const double ToZ = JNum(In, TEXT("toZ"), -100000.0);

		const TSharedPtr<FJsonObject>* LocObj = nullptr;
		if (In->TryGetObjectField(TEXT("location"), LocObj) && LocObj)
		{
			const TSharedRef<FJsonObject> L = LocObj->ToSharedRef();
			X = JNum(L, TEXT("x"));
			Y = JNum(L, TEXT("y"));
			// A location's Z is where to trace FROM, unless fromZ was given explicitly.
			if (!JHasAny(In, { TEXT("fromZ") }) && L->HasField(TEXT("z")))
			{
				FromZ = JNum(L, TEXT("z"));
			}
		}

		FCollisionQueryParams Params(SCENE_QUERY_STAT(MifBridgeTraceGround), /*bTraceComplex*/ true);

		// AN ignoreActor THAT DOES NOT RESOLVE IS A REFUSAL, NOT A SHRUG.
		//
		// This used to be `if (AActor* Ignore = FindActorInWorld(...)) { AddIgnoredActor(Ignore); }`.
		// When the name resolved to nothing the `if` simply did not fire, the trace ran WITHOUT
		// ignoring anything, and the caller got a confident hit:true - quite possibly against the very
		// actor they asked to exclude, which is the one answer they had ruled out.
		//
		// Same silent-ignore class as invoke_editor_tab's mode-dependent 'asset', and found the same
		// way: the endpoint sweep's ghost probe handed it a path that does not exist and it answered
		// ok:true. Note the XY case a few lines above already had this reasoning applied to it; the
		// ignore path did not.
		const FString IgnoreName = JStrAny(In, { TEXT("ignoreActor"), TEXT("actorPath") });
		if (!IgnoreName.IsEmpty())
		{
			AActor* Ignore = FindActorInWorld(World, IgnoreName);
			if (!Ignore)
			{
				Fail(Out, FString::Printf(
					TEXT("ignoreActor '%s' does not resolve to an actor in this world, so the trace "
						 "would have run WITHOUT ignoring it and could have hit the very actor you "
						 "asked to exclude. NOTHING was traced. Check the name with "
						 "list_level_actors, or omit ignoreActor."), *IgnoreName));
				return;
			}
			Params.AddIgnoredActor(Ignore);
			Out->SetStringField(TEXT("ignoredActor"), Ignore->GetPathName());
		}

		FHitResult Hit;
		const bool bHit = World->LineTraceSingleByChannel(
			Hit, FVector(X, Y, FromZ), FVector(X, Y, ToZ), ECC_WorldStatic, Params);

		Out->SetBoolField(TEXT("hit"), bHit);
		if (bHit)
		{
			Out->SetNumberField(TEXT("z"), Hit.ImpactPoint.Z);
			// Echo the point actually traced, so a caller can never again believe a result belongs to
			// an XY it did not sample.
			Out->SetObjectField(TEXT("traced"), Vec3(FVector(X, Y, FromZ)));
			Out->SetObjectField(TEXT("normal"), Vec3(Hit.ImpactNormal));
			if (AActor* HitActor = Hit.GetActor())
			{
				Out->SetStringField(TEXT("actorPath"), HitActor->GetPathName());
				Out->SetStringField(TEXT("label"), HitActor->GetActorLabel());
			}
		}
		else
		{
			Out->SetStringField(TEXT("note"),
				TEXT("no hit — the ground mesh may have no collision in the editor world. Do NOT assume z=0; place from known bounds instead."));
		}
	}

	// --- capture_camera -----------------------------------------------------
	//   in:  { location:{x,y,z} (or top-level x,y,z), rotation:{x,y,z}, lookAt?:{x,y,z},
	//          useViewportCamera? (aliases: useViewport, fromViewport),
	//          fov?, width?, height?, name? }
	//   out: { path, exists, wroteFile, width, height, location, rotation, fov,
	//          cameraSource, locationSource, rotationSource, fovSource, viewport? }
	//   bucket: READ-ONLY (listed in MifBridgeCommon.cpp) — the ASceneCapture2D is RF_Transient and is
	//           destroyed inside the call, so nothing is dirtied and no transaction is opened.
	//
	// Renders from an ARBITRARY viewpoint via a temporary SceneCapture2D — deliberately NOT the
	// user's viewport, so inspecting a building doesn't yank their camera around while they work.
	//
	// THE COMPOSITION GAP THIS CLOSES (docs/06_OPEN_ISSUES_FROM_USE.md §7, task #21).
	// set_viewport_camera drives FLevelEditorViewportClient (MifBridgeViewport.cpp:84-141) and answers
	// ok:true. capture_camera spawned its OWN ASceneCapture2D at a Loc/Rot taken solely from its own
	// location/rotation params — defaulting to (0,0,500) and (-25,0,0) — and never read the viewport.
	// Both endpoints were individually truthful; the caller's reasonable model, "point the camera, then
	// capture", spanned two endpoints that shared no state, and nothing in either response said so.
	//   * useViewportCamera:true seeds Loc/Rot/FOV from the active level viewport client. OPT-IN, never
	//     the default: the (0,0,500)/(-25,0,0) defaults are load-bearing for existing callers, and
	//     silently re-aiming them would be the SAME breakage class being fixed here.
	//   * cameraSource is echoed ALWAYS — "explicit" | "viewport" | "default" — including when
	//     useViewportCamera is absent. That is the point: a caller who never learned about the opt-in
	//     reads cameraSource:"default" in the JSON instead of discovering it in the pixels.
	// locationSource / rotationSource / fovSource are echoed alongside it because the three can
	// legitimately disagree (useViewportCamera:true with an explicit fov, say) and one summary word
	// would then have to lie about one of them.
	//
	// What is still NOT shared, stated rather than implied: the viewport's VIEW MODE, SHOW FLAGS and
	// RESOLUTION. This always renders lit + tonemapped (SCS_FinalColorLDR) with Atmosphere and Fog on,
	// at width/height, so set_view_mode wireframe/unlit does NOT reach this image. See the KeyNotes on
	// the guard below, which say so to any caller who tries to pass showFlags/viewMode.
	//
	// CaptureScene() is synchronous (it enqueues and flushes), and ExportRenderTarget writes the file
	// before returning, so the path handed back already exists — no poll-then-fetch dance.
	void H_capture_camera(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("x"), TEXT("y"), TEXT("z"), TEXT("location"), TEXT("rotation"), TEXT("lookAt"),
			  TEXT("useViewportCamera"), TEXT("useViewport"), TEXT("fromViewport"),
			  TEXT("fov"), TEXT("width"), TEXT("height"), TEXT("name") },
			TEXT("x, y, z (or location:{x,y,z}), rotation:{x,y,z} = pitch/yaw/roll, lookAt:{x,y,z}, useViewportCamera (aliases: useViewport, fromViewport), fov, width, height, name"),
			{ { TEXT("showFlags"), TEXT("not implemented — capture_camera always renders lit/tonemapped with Atmosphere+Fog on and does NOT read the level viewport's show flags; set_view_mode does not reach this image") },
			  { TEXT("viewMode"),  TEXT("not implemented — same gap as showFlags: the viewport's view mode is not consumed here") },
			  { TEXT("actorPath"), TEXT("not a parameter of this endpoint — to frame an actor, read get_actor_bounds and pass its origin as lookAt, or focus_viewport the actor and then capture with useViewportCamera:true") } }))
		{
			return;
		}

		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		// Presence is tested on the OBJECT, not with JHasAny: FJsonObject::HasField answers true for an
		// explicit JSON null, and a null location reported as an "explicit" camera would be a brand new
		// lie in the very field added to stop lying about where the shot came from.
		const TSharedPtr<FJsonObject>* LocObj = nullptr;
		const bool bLocObj = In->TryGetObjectField(TEXT("location"), LocObj) && LocObj;
		const bool bLocXYZ = JHasAny(In, { TEXT("x"), TEXT("y"), TEXT("z") });
		const TSharedPtr<FJsonObject>* RotObj = nullptr;
		const bool bRotObj = In->TryGetObjectField(TEXT("rotation"), RotObj) && RotObj;
		const TSharedPtr<FJsonObject>* LookObj = nullptr;
		const bool bLookObj = In->TryGetObjectField(TEXT("lookAt"), LookObj) && LookObj;
		const bool bFovGiven = JHasAny(In, { TEXT("fov") });
		const bool bWantViewport = JBoolAny(In,
			{ TEXT("useViewportCamera"), TEXT("useViewport"), TEXT("fromViewport") }, false);

		// Seed order is default -> viewport -> explicit, and every stage records WHICH source it was,
		// so the labels reported below are produced by the same reads that produced the numbers rather
		// than re-derived afterwards from a second look at the payload.
		FVector  Loc(0.0, 0.0, 500.0);
		FRotator Rot(-25.0, 0.0, 0.0);
		double   Fov = 75.0;
		const TCHAR* LocSource = TEXT("default");
		const TCHAR* RotSource = TEXT("default");
		const TCHAR* FovSource = TEXT("default");

		if (bWantViewport)
		{
			int32 VpIndex = INDEX_NONE, VpCount = 0;
			FLevelEditorViewportClient* Client = MifSpatialActiveLevelViewport(VpIndex, VpCount);
			if (!Client)
			{
				// The caller asked for the viewport BY NAME. Quietly falling back to (0,0,500)/(-25,0,0)
				// would render a confident picture of somewhere nobody asked about — the exact failure
				// this parameter exists to close, reintroduced one level down.
				Fail(Out, TEXT("useViewportCamera was requested but no level editor viewport is available ")
					TEXT("(get_viewport_camera answers the same question and will fail the same way). Open a level ")
					TEXT("viewport, or pass location/rotation explicitly."));
				return;
			}
			Loc = Client->GetViewLocation();
			Rot = Client->GetViewRotation();
			Fov = Client->ViewFOV;
			LocSource = TEXT("viewport");
			RotSource = TEXT("viewport");
			FovSource = TEXT("viewport");

			// Echo the client that answered, INDEX included, so a caller can diff this against
			// get_viewport_camera rather than trust that two file-local copies of "which viewport is
			// active" still agree (see the eviction clause on MifSpatialActiveLevelViewport).
			TSharedRef<FJsonObject> Vp = MakeShared<FJsonObject>();
			Vp->SetObjectField(TEXT("location"), Vec3(Loc));
			Vp->SetObjectField(TEXT("rotation"), Vec3(FVector(Rot.Pitch, Rot.Yaw, Rot.Roll)));
			Vp->SetNumberField(TEXT("fov"), Client->ViewFOV);
			Vp->SetBoolField(TEXT("perspective"), Client->IsPerspective());
			Vp->SetNumberField(TEXT("index"), VpIndex);
			Vp->SetNumberField(TEXT("count"), VpCount);
			Out->SetObjectField(TEXT("viewport"), Vp);

			if (!Client->IsPerspective())
			{
				// An orthographic client's view location is a projection origin, not an eye position,
				// and a SceneCapture2D renders PERSPECTIVE. The seed is still honoured — the caller
				// asked for it — but an image that quietly answers a different question than the screen
				// is the same defect in a different medium, so it is named.
				AddWarning(Out, TEXT("the active level viewport is ORTHOGRAPHIC: its location/rotation were used, but ")
					TEXT("this capture is a PERSPECTIVE render and will not match what is on screen. Switch it with ")
					TEXT("set_viewport_camera {ortho:\"perspective\"} first, or pass location/rotation explicitly."));
			}
			if (World != EditorWorld())
			{
				AddWarning(Out, TEXT("seeded from the level editor viewport, which always shows the EDITOR world, while ")
					TEXT("capturing the PIE world — the pose was COPIED across two different worlds, not shared with them."));
			}
		}
		// True only past the failure above, so everything downstream can ask "did the viewport seed us?"
		// without re-testing the parameter.
		const bool bSeeded = bWantViewport;

		if (bLocObj || bLocXYZ)
		{
			// A PARTIAL location keeps the BASELINE for the components it omits. Baseline is the
			// viewport pose when one seeded us, and otherwise the historical default: location:{x:1}
			// has always meant (1,0,0) and top-level x:1 has always meant (1,0,500). Changing either
			// would silently re-aim existing callers, which is the thing being fixed, not repeated.
			if (bLocObj)
			{
				const FVector Base = bSeeded ? Loc : FVector::ZeroVector;
				const TSharedRef<FJsonObject> L = LocObj->ToSharedRef();
				Loc = FVector(JNum(L, TEXT("x"), Base.X), JNum(L, TEXT("y"), Base.Y), JNum(L, TEXT("z"), Base.Z));
			}
			else
			{
				const FVector Base = bSeeded ? Loc : FVector(0.0, 0.0, 500.0);
				Loc = FVector(JNum(In, TEXT("x"), Base.X), JNum(In, TEXT("y"), Base.Y), JNum(In, TEXT("z"), Base.Z));
			}
			LocSource = TEXT("explicit");
		}

		if (bRotObj)
		{
			const FRotator Base = bSeeded ? Rot : FRotator::ZeroRotator;
			const TSharedRef<FJsonObject> R = RotObj->ToSharedRef();
			Rot = FRotator(JNum(R, TEXT("x"), Base.Pitch), JNum(R, TEXT("y"), Base.Yaw), JNum(R, TEXT("z"), Base.Roll));
			RotSource = TEXT("explicit");
		}

		// lookAt is far easier to drive from a script than pitch/yaw — "frame that building" is a
		// point, not an angle. Applied AFTER the location so it aims from wherever we actually ended
		// up, viewport-seeded location included.
		if (bLookObj)
		{
			const TSharedRef<FJsonObject> L = LookObj->ToSharedRef();
			const FVector Target(JNum(L, TEXT("x")), JNum(L, TEXT("y")), JNum(L, TEXT("z")));
			if (Target.Equals(Loc, 0.01))
			{
				// (Target - Loc).Rotation() on a zero vector is ZeroRotator, i.e. "looking down +X" —
				// a confident answer to an unanswerable question.
				Fail(Out, FString::Printf(
					TEXT("lookAt (%s) is the camera location itself, so there is no direction to look. Move one of them."),
					*Target.ToString()));
				return;
			}
			if (bRotObj)
			{
				AddWarning(Out, TEXT("both rotation and lookAt were supplied; lookAt wins (aiming at a point is the more useful of the two)."));
			}
			Rot = (Target - Loc).Rotation();
			RotSource = TEXT("explicit");
		}

		if (bFovGiven)
		{
			Fov = JNum(In, TEXT("fov"), Fov);
			FovSource = TEXT("explicit");
		}
		if (!(Fov > 0.0 && Fov < 180.0))
		{
			// Reachable from the viewport seed as well as from the caller, which is why the source is
			// quoted: a degenerate FOV renders a black or smeared frame and used to answer ok:true.
			Fail(Out, FString::Printf(
				TEXT("fov %.3f is out of range (source: %s) — a perspective field of view must be greater than 0 and less than 180 degrees."),
				Fov, FovSource));
			return;
		}

		// ALWAYS reported, even with no viewport involved: this single word is what makes the
		// composition gap visible in JSON. "explicit" whenever the caller named any part of the
		// transform, because those win over a viewport seed.
		const bool bAnyExplicit = (bLocObj || bLocXYZ || bRotObj || bLookObj);
		const TCHAR* CameraSource = bAnyExplicit ? TEXT("explicit")
			: (bSeeded ? TEXT("viewport") : TEXT("default"));
		if (bAnyExplicit && bSeeded)
		{
			AddWarning(Out, TEXT("useViewportCamera:true AND an explicit location/rotation/lookAt were both supplied — ")
				TEXT("the explicit values win. locationSource/rotationSource/fovSource say which each one came from."));
		}

		const int32 W  = FMath::Clamp(JInt(In, TEXT("width"), 1280), 64, 4096);
		const int32 Ht = FMath::Clamp(JInt(In, TEXT("height"), 720), 64, 4096);

		UTextureRenderTarget2D* RT = NewObject<UTextureRenderTarget2D>(GetTransientPackage());
		RT->RenderTargetFormat = RTF_RGBA8;
		RT->ClearColor = FLinearColor::Black;
		RT->bAutoGenerateMips = false;
		RT->InitAutoFormat(W, Ht);
		RT->UpdateResourceImmediate(true);

		FActorSpawnParameters SpawnParams;
		SpawnParams.ObjectFlags |= RF_Transient;   // never dirties the level
		ASceneCapture2D* Cap = World->SpawnActor<ASceneCapture2D>(Loc, Rot, SpawnParams);
		if (!Cap) { Fail(Out, TEXT("failed to spawn capture camera")); return; }

		// Verify the pose BEFORE rendering. An image is only as truthful as the transform it was shot
		// from, and echoing the REQUESTED numbers beside a picture taken from somewhere else is exactly
		// the class of silent success this endpoint was reopened to kill.
		//
		// The ORIENTATION is compared as quaternions, not component-wise as FRotator::Equals would do.
		// An actor stores an FQuat, so GetActorRotation() is a round trip, and at gimbal lock that round
		// trip returns a DIFFERENT Euler triple for the SAME orientation - (-90, 45, 0) comes back as
		// (-90, 0, -45). A component-wise check would therefore reject the straight-down shot, which is
		// the single most common lookAt in this bridge (top-down framing). |dot| handles q and -q being
		// the same rotation; 0.9999 is ~0.8 degrees.
		const FVector  ActualLoc = Cap->GetActorLocation();
		const FRotator ActualRot = Cap->GetActorRotation();
		const double   AimDot    = FMath::Abs(ActualRot.Quaternion() | Rot.Quaternion());
		if (!ActualLoc.Equals(Loc, 0.1) || AimDot < 0.9999)
		{
			const FString Detail = FString::Printf(
				TEXT("capture camera did not land on the requested transform: asked loc %s rot %s, got loc %s rot %s. No image was written."),
				*Loc.ToString(), *Rot.ToString(), *ActualLoc.ToString(), *ActualRot.ToString());
			Cap->Destroy();
			Fail(Out, Detail);
			return;
		}

		USceneCaptureComponent2D* Comp = Cap->GetCaptureComponent2D();
		if (!Comp) { Cap->Destroy(); Fail(Out, TEXT("capture component missing")); return; }
		Comp->TextureTarget = RT;
		Comp->FOVAngle = (float)Fov;
		Comp->CaptureSource = SCS_FinalColorLDR;         // lit, tonemapped — what the eye would see
		Comp->bCaptureEveryFrame = false;
		Comp->bCaptureOnMovement = false;
		Comp->ShowFlags.SetAtmosphere(true);
		Comp->ShowFlags.SetFog(true);
		Comp->CaptureScene();
		// Read back off the component rather than echoing the request: same rule as the transform above.
		const double ActualFov = Comp->FOVAngle;

		FString Name = JStr(In, TEXT("name"), TEXT("MifShot"));
		Name = FPaths::MakeValidFileName(Name);
		const FString Dir = FPaths::ProjectSavedDir() / TEXT("MifBridge");
		IPlatformFile& PF = FPlatformFileManager::Get().GetPlatformFile();
		PF.CreateDirectoryTree(*Dir);
		const FString FullPath = FPaths::ConvertRelativePathToFull(Dir / (Name + TEXT(".png")));

		// ExportRenderTarget returns void, so a bare FileExists() afterwards answers "yes" for an export
		// that did nothing whenever an EARLIER capture left a file of the same name behind — a stale PNG
		// reported as this call's output. Snapshot stamp+size first and compare.
		const bool     bExistedBefore = PF.FileExists(*FullPath);
		const FDateTime BeforeStamp   = bExistedBefore ? PF.GetTimeStamp(*FullPath) : FDateTime::MinValue();
		const int64     BeforeSize    = bExistedBefore ? PF.FileSize(*FullPath) : -1;

		UKismetRenderingLibrary::ExportRenderTarget(World, RT, Dir, Name + TEXT(".png"));
		Cap->Destroy();

		const bool bExists = PF.FileExists(*FullPath);
		const bool bFresh  = bExists && (!bExistedBefore
			|| PF.GetTimeStamp(*FullPath) != BeforeStamp
			|| PF.FileSize(*FullPath) != BeforeSize);

		Out->SetStringField(TEXT("path"), FullPath);
		Out->SetBoolField(TEXT("exists"), bExists);       // verified, not assumed
		Out->SetBoolField(TEXT("wroteFile"), bFresh);     // and verified to be THIS call's file
		Out->SetNumberField(TEXT("width"), W);
		Out->SetNumberField(TEXT("height"), Ht);
		Out->SetObjectField(TEXT("location"), Vec3(ActualLoc));
		Out->SetObjectField(TEXT("rotation"), Vec3(FVector(ActualRot.Pitch, ActualRot.Yaw, ActualRot.Roll)));
		Out->SetNumberField(TEXT("fov"), ActualFov);
		Out->SetStringField(TEXT("cameraSource"), CameraSource);
		Out->SetStringField(TEXT("locationSource"), LocSource);
		Out->SetStringField(TEXT("rotationSource"), RotSource);
		Out->SetStringField(TEXT("fovSource"), FovSource);
		Out->SetBoolField(TEXT("useViewportCamera"), bWantViewport);

		if (!bExists)
		{
			Fail(Out, FString::Printf(
				TEXT("render target export wrote no file at %s — ExportRenderTarget reports nothing, so check that '%s' is writable and that the render target initialised."),
				*FullPath, *Dir));
			return;
		}
		if (!bFresh)
		{
			AddWarning(Out, FString::Printf(
				TEXT("%s already existed and its timestamp and size are unchanged — this response may be describing an EARLIER capture of the same name. Pass a distinct 'name' per shot."),
				*FullPath));
		}
		if (FCString::Strcmp(CameraSource, TEXT("default")) == 0)
		{
			// The discoverability half of the fix: the caller who hit docs/06_OPEN_ISSUES_FROM_USE.md §7
			// got ok:true and a picture of nowhere. Now they get told, in the response, where it came
			// from and how to shoot from the viewport they just moved.
			AddWarning(Out, TEXT("cameraSource is \"default\": no location/rotation/lookAt was given and useViewportCamera was not set, ")
				TEXT("so this image is from (0,0,500) looking down 25 degrees — NOT from the editor viewport, which set_viewport_camera ")
				TEXT("and focus_viewport drive independently of this endpoint. Pass useViewportCamera:true to shoot from where they left it."));
		}

		UE_LOG(LogMifBridge, Log, TEXT("capture_camera (%s) -> %s"), CameraSource, *FullPath);
	}

	// --- scene_report -------------------------------------------------------
	//   out: { actorCount, bounds, overlaps, floating[], sunken[], scaleOutliers[] }
	// The single call that would have caught every mistake in the blind build. Run it after placing.
	void H_scene_report(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("groundZ"), TEXT("floatTolerance"), TEXT("tallWarnZ") },
			TEXT("groundZ, floatTolerance, tallWarnZ — all optional; the scan itself always covers every actor in the active world"),
			{ { TEXT("tolerance"), TEXT("the float/sunken threshold here is 'floatTolerance' (default 30) — 'tolerance' is check_overlaps' overlap-depth threshold") },
			  { TEXT("nameContains"), TEXT("not supported — scene_report always scans the whole world; filter its floating/sunken/tooTall arrays caller-side, or use check_overlaps which does take nameContains") },
			  { TEXT("actorPath"), TEXT("scene_report is whole-scene by design; for one actor use get_actor_bounds, or check_overlaps with actorPath") } }))
		{
			return;
		}

		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		const double GroundZ = JNum(In, TEXT("groundZ"), 0.0);
		const double FloatTol = JNum(In, TEXT("floatTolerance"), 30.0);

		int32 Count = 0;
		FBox Total(ForceInit);
		TArray<TSharedPtr<FJsonValue>> Floating, Sunken, Big;
		for (TActorIterator<AActor> It(World); It; ++It)
		{
			AActor* A = *It;
			if (!A || !IsValid(A)) { continue; }
			FBox B;
			if (!ActorBox(A, B)) { continue; }
			++Count;
			Total += B;

			const FString Label = A->GetActorLabel();
			if (Label.Contains(TEXT("Ground")) || Label.Contains(TEXT("Sky"))) { continue; }

			// Floating: the actor's underside sits clearly above the ground plane.
			if (B.Min.Z > GroundZ + FloatTol)
			{
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("label"), Label);
				J->SetNumberField(TEXT("gap"), B.Min.Z - GroundZ);
				Floating.Add(MakeShared<FJsonValueObject>(J));
			}
			// Sunken: more than half the actor is below ground.
			const double Height = B.Max.Z - B.Min.Z;
			if (Height > 1.0 && B.Max.Z < GroundZ + Height * 0.5)
			{
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("label"), Label);
				J->SetNumberField(TEXT("buriedTo"), B.Max.Z);
				Sunken.Add(MakeShared<FJsonValueObject>(J));
			}
			// Scale outlier: anything taller than a 3-storey building is probably a mistake in a town.
			if (Height > JNum(In, TEXT("tallWarnZ"), 1500.0))
			{
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("label"), Label);
				J->SetNumberField(TEXT("height"), Height);
				J->SetObjectField(TEXT("scale"), Vec3(A->GetActorScale3D()));
				Big.Add(MakeShared<FJsonValueObject>(J));
			}
		}

		Out->SetNumberField(TEXT("actorCount"), Count);
		if (Total.IsValid)
		{
			Out->SetObjectField(TEXT("sceneMin"), Vec3(Total.Min));
			Out->SetObjectField(TEXT("sceneMax"), Vec3(Total.Max));
			Out->SetObjectField(TEXT("sceneSize"), Vec3(Total.GetSize()));
		}
		Out->SetArrayField(TEXT("floating"), Floating);
		Out->SetArrayField(TEXT("sunken"), Sunken);
		Out->SetArrayField(TEXT("tooTall"), Big);
		Out->SetNumberField(TEXT("floatingCount"), Floating.Num());
		Out->SetNumberField(TEXT("sunkenCount"), Sunken.Num());
		Out->SetNumberField(TEXT("tooTallCount"), Big.Num());
	}
}
