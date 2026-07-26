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
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Editor.h"
#include "Engine/Engine.h"
#include "Engine/SceneCapture2D.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Components/SceneCaptureComponent2D.h"
#include "GameFramework/Actor.h"
#include "Kismet/KismetRenderingLibrary.h"
#include "CollisionQueryParams.h"
#include "Misc/Paths.h"
#include "HAL/PlatformFileManager.h"

namespace MifBridge
{
	namespace
	{
		UWorld* SpatialWorld()
		{
			// Prefer the PIE world when playing so queries match what is actually running; fall back
			// to the editor world for level-building, which is the normal case here.
			if (GEditor && GEditor->PlayWorld) { return GEditor->PlayWorld; }
			return GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
		}

		AActor* FindActorByPathOrLabel(UWorld* World, const FString& Query)
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

		TSharedRef<FJsonObject> Vec3(const FVector& V)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetNumberField(TEXT("x"), V.X); J->SetNumberField(TEXT("y"), V.Y); J->SetNumberField(TEXT("z"), V.Z);
			return J;
		}

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
	}

	// --- get_actor_bounds ---------------------------------------------------
	//   in:  { actorPath }   out: { origin, extent, size, min, max, hasBounds }
	void H_get_actor_bounds(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWorld* World = SpatialWorld();
		AActor* A = FindActorByPathOrLabel(World, JStrAny(In, { TEXT("actorPath"), TEXT("actor"), TEXT("path") }));
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
		UWorld* World = SpatialWorld();
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

		AActor* Target = Single.IsEmpty() ? nullptr : FindActorByPathOrLabel(World, Single);
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
		UWorld* World = SpatialWorld();
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
		if (AActor* Ignore = FindActorByPathOrLabel(World, JStrAny(In, { TEXT("ignoreActor"), TEXT("actorPath") })))
		{
			Params.AddIgnoredActor(Ignore);
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
	//   in:  { location:{x,y,z}, rotation:{x,y,z}, lookAt?:{x,y,z}, fov?, width?, height?, name? }
	//   out: { path, width, height }
	//
	// Renders from an ARBITRARY viewpoint via a temporary SceneCapture2D — deliberately NOT the
	// user's viewport, so inspecting a building doesn't yank their camera around while they work.
	//
    // CaptureScene() is synchronous (it enqueues and flushes), and ExportRenderTarget writes the file
	// before returning, so the path handed back already exists — no poll-then-fetch dance.
	void H_capture_camera(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWorld* World = SpatialWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		FVector Loc(JNum(In, TEXT("x"), 0.0), JNum(In, TEXT("y"), 0.0), JNum(In, TEXT("z"), 500.0));
		const TSharedPtr<FJsonObject>* LocObj = nullptr;
		if (In->TryGetObjectField(TEXT("location"), LocObj) && LocObj)
		{
			const TSharedRef<FJsonObject> L = LocObj->ToSharedRef();
			Loc = FVector(JNum(L, TEXT("x")), JNum(L, TEXT("y")), JNum(L, TEXT("z")));
		}

		FRotator Rot(-25.0, 0.0, 0.0);
		const TSharedPtr<FJsonObject>* RotObj = nullptr;
		if (In->TryGetObjectField(TEXT("rotation"), RotObj) && RotObj)
		{
			const TSharedRef<FJsonObject> R = RotObj->ToSharedRef();
			Rot = FRotator(JNum(R, TEXT("x")), JNum(R, TEXT("y")), JNum(R, TEXT("z")));
		}
		// lookAt is far easier to drive from a script than pitch/yaw — "frame that building" is a
		// point, not an angle.
		const TSharedPtr<FJsonObject>* LookObj = nullptr;
		if (In->TryGetObjectField(TEXT("lookAt"), LookObj) && LookObj)
		{
			const TSharedRef<FJsonObject> L = LookObj->ToSharedRef();
			const FVector Target(JNum(L, TEXT("x")), JNum(L, TEXT("y")), JNum(L, TEXT("z")));
			Rot = (Target - Loc).Rotation();
		}

		const int32 W = FMath::Clamp(JInt(In, TEXT("width"), 1280), 64, 4096);
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

		USceneCaptureComponent2D* Comp = Cap->GetCaptureComponent2D();
		if (!Comp) { Cap->Destroy(); Fail(Out, TEXT("capture component missing")); return; }
		Comp->TextureTarget = RT;
		Comp->FOVAngle = (float)JNum(In, TEXT("fov"), 75.0);
		Comp->CaptureSource = SCS_FinalColorLDR;         // lit, tonemapped — what the eye would see
		Comp->bCaptureEveryFrame = false;
		Comp->bCaptureOnMovement = false;
		Comp->ShowFlags.SetAtmosphere(true);
		Comp->ShowFlags.SetFog(true);
		Comp->CaptureScene();

		FString Name = JStr(In, TEXT("name"), TEXT("MifShot"));
		Name = FPaths::MakeValidFileName(Name);
		const FString Dir = FPaths::ProjectSavedDir() / TEXT("MifBridge");
		IPlatformFile& PF = FPlatformFileManager::Get().GetPlatformFile();
		PF.CreateDirectoryTree(*Dir);

		UKismetRenderingLibrary::ExportRenderTarget(World, RT, Dir, Name + TEXT(".png"));
		Cap->Destroy();

		const FString FullPath = FPaths::ConvertRelativePathToFull(Dir / (Name + TEXT(".png")));
		Out->SetStringField(TEXT("path"), FullPath);
		Out->SetBoolField(TEXT("exists"), PF.FileExists(*FullPath));   // verified, not assumed
		Out->SetNumberField(TEXT("width"), W);
		Out->SetNumberField(TEXT("height"), Ht);
		Out->SetObjectField(TEXT("location"), Vec3(Loc));
		Out->SetObjectField(TEXT("rotation"), Vec3(FVector(Rot.Pitch, Rot.Yaw, Rot.Roll)));
		UE_LOG(LogMifBridge, Log, TEXT("capture_camera -> %s"), *FullPath);
	}

	// --- scene_report -------------------------------------------------------
	//   out: { actorCount, bounds, overlaps, floating[], sunken[], scaleOutliers[] }
	// The single call that would have caught every mistake in the blind build. Run it after placing.
	void H_scene_report(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWorld* World = SpatialWorld();
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
