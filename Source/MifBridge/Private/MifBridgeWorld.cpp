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
		UWorld* EditorWorld()
		{
			return GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
		}

		AActor* FindWorldActor(UWorld* World, const FString& Query)
		{
			if (!World || Query.IsEmpty()) { return nullptr; }
			for (TActorIterator<AActor> It(World); It; ++It)
			{
				AActor* A = *It;
				if (!A || !IsValid(A)) { continue; }
				if (A->GetPathName() == Query || A->GetName() == Query || A->GetActorLabel() == Query)
				{
					return A;
				}
			}
			return nullptr;
		}

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

		bool ReadVec(const TSharedRef<FJsonObject>& Obj, FVector& Out)
		{
			Out = FVector(JNum(Obj, TEXT("x")), JNum(Obj, TEXT("y")), JNum(Obj, TEXT("z")));
			return true;
		}
	}

	// --- new_level ----------------------------------------------------------
	//   in:  { partitioned? (default false) }   out: { world }
	// bPromptUserToSave is forced FALSE: an unattended agent cannot dismiss a modal, and a "save your
	// changes?" dialog here blocks the game thread — which also blocks this HTTP server.
	void H_new_level(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		AActor* Actor = FindWorldActor(World, JStrAny(In, { TEXT("actorPath"), TEXT("actor") }));
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
		if (!In->TryGetArrayField(TEXT("points"), Points) || !Points || Points->Num() < 2)
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

		Actor->Modify();
		Spline->Modify();
		Spline->ClearSplinePoints(/*bUpdateSpline*/ false);

		int32 Added = 0, Snapped = 0;
		for (const TSharedPtr<FJsonValue>& Val : *Points)
		{
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (!Val.IsValid() || !Val->TryGetObject(Obj) || !Obj) { continue; }
			FVector P;
			ReadVec(Obj->ToSharedRef(), P);

			if (bSnap && bWorld)
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
		Actor->PostEditChange();

		Out->SetNumberField(TEXT("pointCount"), Added);
		Out->SetNumberField(TEXT("snappedToGround"), Snapped);
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
		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		AActor* Actor = FindWorldActor(World, JStrAny(In, { TEXT("actorPath"), TEXT("actor") }));
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
		const TArray<TSharedPtr<FJsonValue>>* Paths = nullptr;
		if (In->TryGetArrayField(TEXT("actorPaths"), Paths) && Paths)
		{
			for (const TSharedPtr<FJsonValue>& V : *Paths)
			{
				FString P;
				if (V.IsValid() && V->TryGetString(P) && !P.IsEmpty())
				{
					if (AActor* A = FindWorldActor(World, P)) { Targets.Add(A); }
				}
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
