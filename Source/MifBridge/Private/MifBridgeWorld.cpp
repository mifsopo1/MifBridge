// MifBridge — WORLD: level lifecycle, spline authoring, and ground snapping.
//
// Three capabilities that each blocked a whole class of work:
//
//   * Level lifecycle. Without new_level/save_level_as/load_level an agent cannot persist anything —
//     a town built over an hour evaporates the moment the editor restarts, which happened three
//     times before this file existed. new_level also passes bPromptUserToSave=false, because a modal
//     "save your changes?" dialog stalls an unattended run forever.
//
//   * Splines. The shipped game walks its NPCs along BP_SegmentedPathTaskMarker, whose PathSpline is
//     a USplineComponent. No spline authoring meant no patrol routes, which meant no walking NPCs —
//     the single most-requested missing piece of "liveliness".
//
//   * Ground snapping. Placing on terrain means one downward trace per actor. Doing that over HTTP
//     was 189 round-trips for one small town, and tracing at an actor's own XY hits the actor itself
//     unless it is excluded — which is why this belongs in C++ where the ignore-list is free.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Components/SplineComponent.h"
#include "Editor.h"
#include "TimerManager.h"
#include "Engine/Level.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "FileHelpers.h"
#include "GameFramework/Actor.h"
#include "LandscapeProxy.h"
#include "Misc/PackageName.h"
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
	namespace
	{
		// EditorWorld() moved to MifBridgeCommon.cpp (declared in MifBridgeHandlers.h) — this copy and
		// MifBridgeStreaming.cpp's were byte-identical and one unity-blob shift apart from a C2084.
		// Do NOT re-add a file-local copy.

		// The actor finder moved to MifBridgeCommon.cpp as MifBridge::FindActorInWorld (declared in
		// MifBridgeHandlers.h). FIVE byte-identical copies existed under five different names
		// (FindActor, FindNavActor, FindActorByPathOrLabel, FindVpActor, FindWorldActor) — different
		// names are not a build error, which is exactly why they survived, but it meant a fix to the
		// path/name/label matching rule landed in one of five places. Do NOT add a sixth.

		// /Game/Maps/Foo -> <project>/Content/Maps/Foo.umap. Callers speak package paths like every
		// other MifBridge endpoint; the filesystem path never leaks out.
		bool PackagePathToMapFilename(const FString& PackagePath, FString& OutFilename, FString& OutError)
		{
			FString Clean = PackagePath;
			Clean.RemoveFromEnd(TEXT(".umap"));
			// Accept "/Game/Maps/Foo.Foo" (object path) as well as "/Game/Maps/Foo".
			int32 Dot = INDEX_NONE;
			if (Clean.FindChar(TEXT('.'), Dot)) { Clean = Clean.Left(Dot); }

			if (!Clean.StartsWith(TEXT("/")))
			{
				OutError = FString::Printf(
					TEXT("'%s' is not a package path — expected something like /Game/Maps/MyLevel"), *PackagePath);
				return false;
			}
			FText Reason;
			if (!FPackageName::IsValidLongPackageName(Clean, /*bIncludeReadOnlyRoots*/ false, &Reason))
			{
				OutError = FString::Printf(TEXT("'%s' is not a valid package path: %s"),
					*PackagePath, *Reason.ToString());
				return false;
			}
			OutFilename = FPackageName::LongPackageNameToFilename(Clean, FPackageName::GetMapPackageExtension());
			return true;
		}

		ESplinePointType::Type ParseSplinePointType(const FString& In)
		{
			const FString L = In.ToLower();
			if (L == TEXT("linear"))            { return ESplinePointType::Linear; }
			if (L == TEXT("constant"))          { return ESplinePointType::Constant; }
			if (L == TEXT("curveclamped"))      { return ESplinePointType::CurveClamped; }
			if (L == TEXT("curvecustomtangent")){ return ESplinePointType::CurveCustomTangent; }
			return ESplinePointType::Curve;
		}

		const TCHAR* SplinePointTypeName(ESplinePointType::Type T)
		{
			switch (T)
			{
			case ESplinePointType::Linear:             return TEXT("linear");
			case ESplinePointType::Constant:           return TEXT("constant");
			case ESplinePointType::CurveClamped:       return TEXT("curveClamped");
			case ESplinePointType::CurveCustomTangent: return TEXT("curveCustomTangent");
			default:                                   return TEXT("curve");
			}
		}

		USplineComponent* FindSpline(AActor* Actor, const FString& ComponentName)
		{
			if (!Actor) { return nullptr; }
			TArray<USplineComponent*> Splines;
			Actor->GetComponents<USplineComponent>(Splines);
			if (Splines.Num() == 0) { return nullptr; }
			if (ComponentName.IsEmpty()) { return Splines[0]; }
			for (USplineComponent* S : Splines)
			{
				if (S && (S->GetName() == ComponentName || S->GetFName().ToString() == ComponentName))
				{
					return S;
				}
			}
			return nullptr;
		}

		// ReadVec is GONE — MifBridge::ReadVectorObject (MifBridgeCommon.cpp) now. It was a second
		// copy of the reader that produced Batch L defect 1: JNum returns its default for a component
		// that is PRESENT but not a number, so a spline point {"x":"oops","y":1,"z":2} became
		// (0,1,2) and the spline was rebuilt through a point the caller never gave.
	}

	// --- new_level ----------------------------------------------------------
	//   in:  { partitioned? (default false) }   out: { world }
	// bPromptUserToSave is forced FALSE: an unattended agent cannot dismiss a modal, and a "save your
	// changes?" dialog here blocks the game thread — which also blocks this HTTP server.
	void H_new_level(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("partitioned") },
			TEXT("partitioned (bool, default false) - the only parameter; new_level takes no path"),
			{ { TEXT("path"), TEXT("new_level does not take a path - it creates an unsaved transient map; pass path to save_level_as afterwards") },
			  { TEXT("name"), TEXT("new_level does not name the map - the name comes from the path you give save_level_as") } }))
		{
			return;
		}

		if (!GEditor) { Fail(Out, TEXT("no GEditor")); return; }

		const bool bPartitioned = JBool(In, TEXT("partitioned"), false);

		// DEFERRED, exactly like start_pie/stop_pie. This handler runs ON the game thread from inside
		// the engine tick, and CreateNewMapForEditing tears the UWorld down and builds a new one. Doing
		// that while FTickTaskManager is still iterating the level list trips
		//   Assertion failed: !LevelList.Contains(TickTaskLevel)  (TickTaskManager.cpp:1458)
		// and takes the editor with it. Scheduling for the next tick lets the current tick unwind first.
		GEditor->GetTimerManager()->SetTimerForNextTick(FTimerDelegate::CreateLambda([bPartitioned]()
		{
			if (GEditor) { GEditor->CreateNewMapForEditing(/*bPromptUserToSave*/ false, bPartitioned); }
		}));

		Out->SetBoolField(TEXT("requested"), true);
		Out->SetBoolField(TEXT("partitioned"), bPartitioned);
		Out->SetStringField(TEXT("note"),
			TEXT("DEFERRED to the next tick — this call does NOT block. Wait ~1s (or poll scene_report) before "
				 "issuing further edits, or they land in the world that is about to be destroyed. Unsaved and "
				 "transient: call save_level_as or the level is lost on restart."));
	}

	// --- save_level_as ------------------------------------------------------
	//   in:  { path:"/Game/Maps/MyLevel" }   out: { savedTo, packagePath }
	void H_save_level_as(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("packagePath"), TEXT("assetPath") },
			TEXT("path (aliases: packagePath, assetPath) - the package path to save the open level to, e.g. \"/Game/Maps/MyLevel\""),
			{ { TEXT("level"), TEXT("use path - 'level' is the sublevel selector on the streaming endpoints; save_level_as always saves the OPEN persistent level") },
			  { TEXT("filename"), TEXT("use path with a package path like \"/Game/Maps/MyLevel\" - the .umap filename is derived from it and is never passed in") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }
		ULevel* Level = World->PersistentLevel;
		if (!Level) { Fail(Out, TEXT("world has no persistent level")); return; }

		const FString PackagePath = JStrAny(In, { TEXT("path"), TEXT("packagePath"), TEXT("assetPath") });
		if (PackagePath.IsEmpty())
		{
			Fail(Out, TEXT("path is required, e.g. \"/Game/Maps/MyLevel\""));
			return;
		}
		FString Filename, Error;
		if (!PackagePathToMapFilename(PackagePath, Filename, Error)) { Fail(Out, Error); return; }

		FString SavedFilename;
		if (!FEditorFileUtils::SaveLevel(Level, Filename, &SavedFilename))
		{
			Fail(Out, FString::Printf(TEXT("SaveLevel failed for '%s' (target %s)"), *PackagePath, *Filename));
			return;
		}

		Out->SetStringField(TEXT("savedTo"), SavedFilename.IsEmpty() ? Filename : SavedFilename);
		Out->SetStringField(TEXT("packagePath"), PackagePath);
		Out->SetStringField(TEXT("world"), World->GetPathName());
	}

	// --- load_level ---------------------------------------------------------
	//   in:  { path:"/Game/Maps/MyLevel" }   out: { world }
	// Discards unsaved changes without asking, for the same reason new_level does.
	void H_load_level(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("packagePath"), TEXT("assetPath") },
			TEXT("path (aliases: packagePath, assetPath) - the package path of the map to open, e.g. \"/Game/Maps/MyLevel\""),
			{ { TEXT("level"), TEXT("use path - 'level' is the sublevel selector on the streaming endpoints; load_level opens a whole map") },
			  { TEXT("filename"), TEXT("use path with a package path like \"/Game/Maps/MyLevel\" - the .umap filename is derived from it and is never passed in") } }))
		{
			return;
		}

		const FString PackagePath = JStrAny(In, { TEXT("path"), TEXT("packagePath"), TEXT("assetPath") });
		if (PackagePath.IsEmpty()) { Fail(Out, TEXT("path is required, e.g. \"/Game/Maps/MyLevel\"")); return; }

		FString Filename, Error;
		if (!PackagePathToMapFilename(PackagePath, Filename, Error)) { Fail(Out, Error); return; }
		if (!FPaths::FileExists(Filename))
		{
			Fail(Out, FString::Printf(TEXT("no map file at '%s' (from %s)"), *Filename, *PackagePath));
			return;
		}

		// Deferred for the same reason as new_level — LoadMap also swaps the UWorld.
		GEditor->GetTimerManager()->SetTimerForNextTick(FTimerDelegate::CreateLambda([Filename]()
		{
			FEditorFileUtils::LoadMap(Filename, /*LoadAsTemplate*/ false, /*bShowProgress*/ false);
		}));

		Out->SetBoolField(TEXT("requested"), true);
		Out->SetStringField(TEXT("packagePath"), PackagePath);
		Out->SetStringField(TEXT("note"),
			TEXT("DEFERRED to the next tick — does NOT block. Wait ~1s before issuing further edits."));
	}

	// --- set_spline_points --------------------------------------------------
	//   in:  { actorPath, component?, points:[{x,y,z},...], space? ("world"|"local"),
	//          pointType? ("curve"|"linear"|"constant"|...), closedLoop?, snapToGround?, groundOffset? }
	//   out: { pointCount, component, length }
	//
	// This is what makes NPCs walk: BP_SegmentedPathTaskMarker drives its route from PathSpline.
	// snapToGround traces each point down onto the terrain, because a patrol route authored at a flat
	// Z either floats or buries itself the moment the ground is not level.
	void H_set_spline_points(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("component"), TEXT("componentName"),
			  TEXT("points"), TEXT("space"), TEXT("pointType"),
			  TEXT("closedLoop"), TEXT("closed"), TEXT("loop"),
			  TEXT("snapToGround"), TEXT("groundOffset"), TEXT("skipPostEditChange") },
			TEXT("actorPath (alias: actor), component (alias: componentName), points:[{x,y,z},...] (at least 2), space (\"world\"|\"local\"), pointType (\"curve\"|\"linear\"|\"constant\"|\"curveClamped\"|\"curveCustomTangent\"), closedLoop (aliases: closed, loop), snapToGround (bool, needs space:\"world\"), groundOffset (number), skipPostEditChange (bool - do NOT re-run the owning actor's construction script; REQUIRED on blueprints that rebuild their own spline)"),
			{ { TEXT("offset"), TEXT("use groundOffset - 'offset' is snap_actors_to_ground's name for the same idea") },
			  { TEXT("type"), TEXT("use pointType - it sets the interpolation type of every point written by this call") },
			  { TEXT("tangents"), TEXT("not implemented - set_spline_points writes point LOCATIONS only; pointType:\"curveCustomTangent\" is accepted but the tangents themselves cannot be supplied here") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		AActor* Actor = FindActorInWorld(World, JStrAny(In, { TEXT("actorPath"), TEXT("actor") }));
		if (!Actor) { Fail(Out, TEXT("actor not found")); return; }

		const FString CompName = JStrAny(In, { TEXT("component"), TEXT("componentName") });
		USplineComponent* Spline = FindSpline(Actor, CompName);
		if (!Spline)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no USplineComponent%s"), *Actor->GetActorLabel(),
				CompName.IsEmpty() ? TEXT("") : *FString::Printf(TEXT(" named '%s'"), *CompName)));
			return;
		}

		const TArray<TSharedPtr<FJsonValue>>* Points = nullptr;
		if (!JArray(In, TEXT("points"), Points) || !Points || Points->Num() < 2)
		{
			Fail(Out, TEXT("points:[{x,y,z},...] is required and needs at least 2 entries"));
			return;
		}

		const bool bWorld = JStr(In, TEXT("space"), TEXT("world")).ToLower() != TEXT("local");
		const ESplineCoordinateSpace::Type Space = bWorld ? ESplineCoordinateSpace::World : ESplineCoordinateSpace::Local;
		const ESplinePointType::Type PointType = ParseSplinePointType(JStr(In, TEXT("pointType"), TEXT("curve")));
		const bool bClosed = JBoolAny(In, { TEXT("closedLoop"), TEXT("closed"), TEXT("loop") }, false);
		const bool bSnap = JBool(In, TEXT("snapToGround"), false);
		const double GroundOffset = JNum(In, TEXT("groundOffset"), 0.0);

		// snapToGround only ever worked in world space — the trace is a world-space line trace — and
		// the local-space case silently ignored it. A patrol route that was supposed to sit on the
		// terrain and does not is exactly the kind of wrong this endpoint must not answer ok:true to.
		if (bSnap && !bWorld)
		{
			Fail(Out, TEXT("snapToGround needs space:\"world\" — the ground trace is a world-space line trace, and in ")
				TEXT("local space it was silently ignored. Pass world-space points with snapToGround, or drop snapToGround."));
			return;
		}

		// PARSE EVERY POINT FIRST. ClearSplinePoints used to run before any point was validated, and a
		// non-object entry was skipped in silence — so the obvious guess points:[[0,0,0],[100,0,0]]
		// (arrays instead of {x,y,z}) returned ok:true, pointCount:0 WITH THE EXISTING ROUTE DESTROYED.
		// PM-003's shape on live NPC patrol routes. Nothing below touches the spline until every entry
		// has been accepted.
		TArray<FVector> Parsed;
		Parsed.Reserve(Points->Num());
		for (int32 i = 0; i < Points->Num(); ++i)
		{
			const TSharedPtr<FJsonValue>& Val = (*Points)[i];
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (!Val.IsValid() || !Val->TryGetObject(Obj) || !Obj)
			{
				Fail(Out, FString::Printf(
					TEXT("points[%d] is not an object — every point must be {\"x\":..,\"y\":..,\"z\":..}. ")
					TEXT("A bare [x,y,z] array is not accepted. The existing spline was NOT modified."), i));
				return;
			}
			// Validated for EVERY point before ClearSplinePoints below — the existing spline is
			// destroyed by that call, so a point that cannot be read has to stop us here.
			FVector P = FVector::ZeroVector;
			FString PointError;
			if (!ReadVectorObject(Obj->ToSharedRef(), FString::Printf(TEXT("points[%d]"), i), P, PointError))
			{
				Fail(Out, FString::Printf(TEXT("%s The existing spline was NOT modified."), *PointError));
				return;
			}
			Parsed.Add(P);
		}

		Actor->Modify();
		Spline->Modify();
		Spline->ClearSplinePoints(/*bUpdateSpline*/ false);

		int32 Added = 0, Snapped = 0;
		for (FVector P : Parsed)
		{
			if (bSnap)
			{
				FHitResult Hit;
				FCollisionQueryParams Params(SCENE_QUERY_STAT(MifBridgeSplineSnap), /*bTraceComplex*/ true);
				Params.AddIgnoredActor(Actor);
				const FVector Start(P.X, P.Y, P.Z + 10000.0);
				const FVector End(P.X, P.Y, P.Z - 10000.0);
				if (World->LineTraceSingleByChannel(Hit, Start, End, ECC_WorldStatic, Params))
				{
					P.Z = Hit.ImpactPoint.Z + GroundOffset;
					++Snapped;
				}
			}

			Spline->AddSplinePoint(P, Space, /*bUpdateSpline*/ false);
			Spline->SetSplinePointType(Added, PointType, /*bUpdateSpline*/ false);
			++Added;
		}

		Spline->SetClosedLoop(bClosed, /*bUpdateSpline*/ false);
		// One rebuild at the end: UpdateSpline reparameterises the whole curve, so doing it per point
		// is O(n^2) for no benefit.
		Spline->UpdateSpline();
		Spline->MarkRenderStateDirty();

		// PostEditChange() re-runs the owning actor's construction script. On every DDS2 blueprint whose
		// construction script rebuilds its own spline (BP_CarRoadSpline, BP_SplineSidewalk,
		// BP_QuestNPCWalkPath, BP_SegmentedPathTaskMarker) that DISCARDS the points just written - the
		// call reports pointCount:N and an immediate read-back returns 2. skipPostEditChange:true keeps
		// the authored points. The spline is already updated and its render state dirtied above, so the
		// component is visually correct either way; what is skipped is only the actor-wide rebuild.
		const bool bSkipPEC = JBool(In, TEXT("skipPostEditChange"), false);
		if (!bSkipPEC)
		{
			Actor->PostEditChange();
		}
		Out->SetBoolField(TEXT("skippedPostEditChange"), bSkipPEC);

		// Read back from the component, not from the loop counter: pointCount must describe the spline,
		// not our intent. (AddSplinePoint cannot silently drop, but the same rule applies everywhere.)
		Out->SetNumberField(TEXT("pointCount"), Spline->GetNumberOfSplinePoints());
		Out->SetNumberField(TEXT("pointsRequested"), Parsed.Num());
		Out->SetNumberField(TEXT("snappedToGround"), Snapped);
		if (bSnap && Snapped < Added)
		{
			// A trace that hit nothing leaves the point at its supplied Z. Silence here reads as
			// "snapped" to a caller that asked for snapping.
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("%d of %d points found no ground below them and kept their supplied Z (nothing was hit by the ")
				TEXT("downward trace — check the points are above collision geometry)"), Added - Snapped, Added));
		}
		Out->SetStringField(TEXT("component"), Spline->GetName());
		Out->SetNumberField(TEXT("length"), Spline->GetSplineLength());
		Out->SetBoolField(TEXT("closedLoop"), bClosed);
		Out->SetStringField(TEXT("pointType"), SplinePointTypeName(PointType));
		Out->SetStringField(TEXT("actor"), Actor->GetActorLabel());
	}

	// --- get_spline_points --------------------------------------------------
	//   in:  { actorPath, component?, space? }   out: { points[], length, closedLoop }
	void H_get_spline_points(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("component"), TEXT("componentName"), TEXT("space") },
			TEXT("actorPath (alias: actor), component (alias: componentName), space (\"world\"|\"local\", default world)"),
			{ { TEXT("index"), TEXT("not supported - get_spline_points returns EVERY point; index into the returned points[] array") },
			  { TEXT("points"), TEXT("not a parameter of this endpoint - points[] is what it RETURNS; use set_spline_points to write them") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		AActor* Actor = FindActorInWorld(World, JStrAny(In, { TEXT("actorPath"), TEXT("actor") }));
		if (!Actor) { Fail(Out, TEXT("actor not found")); return; }
		USplineComponent* Spline = FindSpline(Actor, JStrAny(In, { TEXT("component"), TEXT("componentName") }));
		if (!Spline) { Fail(Out, TEXT("actor has no matching USplineComponent")); return; }

		const bool bWorld = JStr(In, TEXT("space"), TEXT("world")).ToLower() != TEXT("local");
		const ESplineCoordinateSpace::Type Space = bWorld ? ESplineCoordinateSpace::World : ESplineCoordinateSpace::Local;

		TArray<TSharedPtr<FJsonValue>> Arr;
		const int32 Count = Spline->GetNumberOfSplinePoints();
		for (int32 i = 0; i < Count; ++i)
		{
			const FVector P = Spline->GetLocationAtSplinePoint(i, Space);
			TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetNumberField(TEXT("x"), P.X);
			O->SetNumberField(TEXT("y"), P.Y);
			O->SetNumberField(TEXT("z"), P.Z);
			Arr.Add(MakeShared<FJsonValueObject>(O));
		}

		Out->SetArrayField(TEXT("points"), Arr);
		Out->SetNumberField(TEXT("pointCount"), Count);
		Out->SetNumberField(TEXT("length"), Spline->GetSplineLength());
		Out->SetBoolField(TEXT("closedLoop"), Spline->IsClosedLoop());
		Out->SetStringField(TEXT("component"), Spline->GetName());
	}

	// --- snap_actors_to_ground ----------------------------------------------
	//   in:  { actorPaths?:[...], folder?, labelContains?, all?, offset?, alignToNormal?, traceHeight? }
	//   out: { snapped, missed, moved:[{actor, fromZ, toZ}] }
	//
	// Each actor is traced with ITSELF excluded — the reason this cannot be done by calling
	// trace_ground from outside is that a trace at a building's own XY hits the building's roof and
	// "snaps" it onto itself, climbing further each call.
	void H_snap_actors_to_ground(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPaths"), TEXT("folder"), TEXT("labelContains"), TEXT("all"),
			  TEXT("offset"), TEXT("traceHeight"), TEXT("alignToNormal"),
			  TEXT("groundActor"), TEXT("ground"), TEXT("allowAnyHit") },
			TEXT("actorPaths:[...], folder, labelContains, all (bool), offset (number), traceHeight (number), alignToNormal (bool), groundActor (alias: ground), allowAnyHit (bool)"),
			{ { TEXT("actorPath"), TEXT("use actorPaths:[...] - this endpoint snaps a SET, so the parameter is plural even for a single actor") },
			  { TEXT("groundOffset"), TEXT("use offset - 'groundOffset' is set_spline_points' name for the same idea") },
			  { TEXT("snapToGround"), TEXT("not a parameter - snapping IS what this endpoint does; choose the actors with actorPaths[], folder, labelContains or all:true") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		const double Offset = JNum(In, TEXT("offset"), 0.0);
		const double TraceHeight = JNum(In, TEXT("traceHeight"), 100000.0);
		const bool bAlign = JBool(In, TEXT("alignToNormal"), false);
		const FString Folder = JStr(In, TEXT("folder"));
		const FString LabelContains = JStr(In, TEXT("labelContains"));
		const bool bAll = JBool(In, TEXT("all"), false);
		// By default only a Landscape counts as ground. groundActor nominates something else (a
		// platform mesh, a deck); allowAnyHit restores the naive "first blocking hit" behaviour for
		// scenes that genuinely have no landscape.
		const FString GroundActorName = JStrAny(In, { TEXT("groundActor"), TEXT("ground") });
		const bool bAllowAnyHit = JBool(In, TEXT("allowAnyHit"), false);

		// Build the target set. Explicit paths win; otherwise filter, but never snap EVERYTHING by
		// accident — an empty selector with all=false is an error, not a no-op that reports success.
		TArray<AActor*> Targets;
		TArray<TSharedPtr<FJsonValue>> NotFound;   // never let an unresolved path vanish
		const TArray<TSharedPtr<FJsonValue>>* Paths = nullptr;
		if (JArray(In, TEXT("actorPaths"), Paths) && Paths)
		{
			// An entry that did not resolve used to be dropped in silence. With EVERY path bogus the
			// else-branch below is never reached either, so Targets stayed empty and the response was
			// ok:true, considered:0, snapped:0, missed:0 — a total no-op reported as success.
			// select_level_actors already had the right shape (notFound[]); copied.
			for (const TSharedPtr<FJsonValue>& V : *Paths)
			{
				FString P;
				if (!V.IsValid() || !V->TryGetString(P) || P.IsEmpty())
				{
					NotFound.Add(MakeShared<FJsonValueString>(TEXT("<non-string entry in actorPaths[]>")));
					continue;
				}
				if (AActor* A = FindActorInWorld(World, P)) { Targets.Add(A); }
				else { NotFound.Add(MakeShared<FJsonValueString>(P)); }
			}
			if (Targets.Num() == 0)
			{
				TArray<FString> Names;
				for (const TSharedPtr<FJsonValue>& V : NotFound) { Names.Add(V->AsString()); }
				Fail(Out, FString::Printf(
					TEXT("none of the %d actorPaths[] entries resolved to an actor in the editor world (%s) — nothing to snap. ")
					TEXT("list_level_actors shows what is placed; paths, names and labels are all accepted."),
					NotFound.Num(), *FString::Join(Names, TEXT(", "))));
				return;
			}
		}
		else if (bAll || !Folder.IsEmpty() || !LabelContains.IsEmpty())
		{
			for (TActorIterator<AActor> It(World); It; ++It)
			{
				AActor* A = *It;
				if (!A || !IsValid(A)) { continue; }
				if (!Folder.IsEmpty() && !A->GetFolderPath().ToString().StartsWith(Folder)) { continue; }
				if (!LabelContains.IsEmpty() && !A->GetActorLabel().Contains(LabelContains)) { continue; }
				Targets.Add(A);
			}
		}
		else
		{
			Fail(Out, TEXT("pass actorPaths[], or folder/labelContains, or all:true — refusing to guess the target set"));
			return;
		}

		if (Targets.Num() == 0)
		{
			// Reachable from the filter branch: folder/labelContains that match nothing.
			Fail(Out, TEXT("the selector matched no actors — nothing to snap. Check folder / labelContains against list_level_actors."));
			return;
		}

		int32 Snapped = 0, Missed = 0;
		TArray<TSharedPtr<FJsonValue>> Moved;
		int32 SkippedGround = 0;
		for (AActor* A : Targets)
		{
			// Never snap the ground itself. A landscape traced against the rest of the scene lands on
			// whatever happens to be under it, which drags the whole world with it.
			if (A->IsA<ALandscapeProxy>())
			{
				++SkippedGround;
				continue;
			}

			FVector Origin, Extent;
			A->GetActorBounds(/*bOnlyCollidingComponents*/ false, Origin, Extent);
			const FVector Loc = A->GetActorLocation();
			// Distance from the pivot down to the bottom of the bounds — preserved so the actor SITS
			// on the hit rather than centring its pivot there.
			const double PivotToBottom = Loc.Z - (Origin.Z - Extent.Z);

			FCollisionQueryParams Params(SCENE_QUERY_STAT(MifBridgeGroundSnap), /*bTraceComplex*/ true);
			Params.AddIgnoredActor(A);
			TArray<AActor*> Attached;
			A->GetAttachedActors(Attached);
			for (AActor* Child : Attached) { Params.AddIgnoredActor(Child); }

			const FVector Start(Loc.X, Loc.Y, Loc.Z + TraceHeight);
			const FVector End(Loc.X, Loc.Y, Loc.Z - TraceHeight);

			// MULTI-trace, then take the first hit that is actually GROUND. A single trace returns the
			// nearest blocking hit, which is routinely another prop — a palm above a shack snaps onto
			// its roof, a fence snaps onto the palm, and the scene walks upward a layer per call.
			// "Snapped 309, missed 0" is then a completely truthful report of a completely wrong scene.
			TArray<FHitResult> Hits;
			World->LineTraceMultiByChannel(Hits, Start, End, ECC_WorldStatic, Params);

			const FHitResult* Ground = nullptr;
			for (const FHitResult& H : Hits)
			{
				AActor* HitActor = H.GetActor();
				if (!HitActor) { continue; }
				if (!GroundActorName.IsEmpty())
				{
					if (HitActor->GetActorLabel() == GroundActorName ||
						HitActor->GetName() == GroundActorName ||
						HitActor->GetPathName() == GroundActorName) { Ground = &H; break; }
					continue;
				}
				if (HitActor->IsA<ALandscapeProxy>()) { Ground = &H; break; }
				if (bAllowAnyHit) { Ground = &H; break; }
			}
			if (!Ground)
			{
				// Deliberately leave the actor alone rather than dropping it to Z=0 or onto a prop.
				++Missed;
				continue;
			}
			const FHitResult& Hit = *Ground;

			const double NewZ = Hit.ImpactPoint.Z + PivotToBottom + Offset;
			if (!FMath::IsNearlyEqual(NewZ, Loc.Z, 0.01))
			{
				TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
				M->SetStringField(TEXT("actor"), A->GetActorLabel());
				M->SetNumberField(TEXT("fromZ"), Loc.Z);
				M->SetNumberField(TEXT("toZ"), NewZ);
				Moved.Add(MakeShared<FJsonValueObject>(M));
			}

			A->Modify();
			A->SetActorLocation(FVector(Loc.X, Loc.Y, NewZ));
			if (bAlign)
			{
				// Keep yaw, take pitch/roll from the surface. Straight FromZ would spin the actor.
				const FRotator Cur = A->GetActorRotation();
				FRotator Aligned = FRotationMatrix::MakeFromZX(Hit.ImpactNormal, A->GetActorForwardVector()).Rotator();
				Aligned.Yaw = Cur.Yaw;
				A->SetActorRotation(Aligned);
			}
			++Snapped;
		}

		Out->SetNumberField(TEXT("snapped"), Snapped);
		Out->SetNumberField(TEXT("missed"), Missed);
		Out->SetNumberField(TEXT("considered"), Targets.Num());
		Out->SetNumberField(TEXT("skippedGround"), SkippedGround);
		Out->SetArrayField(TEXT("moved"), Moved);
		Out->SetStringField(TEXT("groundRule"), GroundActorName.IsEmpty()
			? (bAllowAnyHit ? TEXT("first blocking hit (allowAnyHit)") : TEXT("landscape only"))
			: *GroundActorName);
		if (Missed > 0)
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("%d actor(s) found no GROUND below them and were left untouched — not dropped to Z=0 and not stacked onto whatever prop happened to be under them. If this scene has no landscape, pass groundActor or allowAnyHit."),
				Missed));
		}
	}
}
