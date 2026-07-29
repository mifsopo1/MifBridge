// MifBridge — NAVIGATION: nav-mesh bounds, building the mesh, and driving actors along it.
//
// This is what turns a static diorama into somewhere people live. Without a nav mesh an NPC cannot
// path anywhere; with one, "walk up and down the street" is a two-point patrol.
//
// Sizing note that matters: ANavMeshBoundsVolume is an AVolume, and a volume's size comes from its
// BRUSH, not from a size property. The default builder brush is 200x200x200 centred on the actor, so
// the actor's SCALE is the knob — a 100x60x8 scale gives 20000x12000x1600 units of coverage. Getting
// this wrong produces a volume that silently covers nothing and a nav build that reports success
// with zero tiles, which is why nav_status reports the tile count rather than just "ok".
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Editor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "GameFramework/Controller.h"
#include "GameFramework/Pawn.h"
#include "NavMesh/NavMeshBoundsVolume.h"
#include "NavMesh/RecastNavMesh.h"
#include "NavigationSystem.h"
#include "Blueprint/AIBlueprintHelperLibrary.h"
#include "Components/BrushComponent.h"

namespace MifBridge
{
	namespace
	{
		// ActiveWorld() was one of five file-local "current world" helpers, two of which preferred PIE and
		// three of which did not. It is MifBridge::ActiveWorld() now (PIE-preferring, which is what nav
		// wants: move_actor_to must drive the live pawn, not an editor stand-in).

		// The actor finder moved to MifBridgeCommon.cpp as MifBridge::FindActorInWorld (declared in
		// MifBridgeHandlers.h). FIVE byte-identical copies existed under five different names
		// (FindActor, FindNavActor, FindActorByPathOrLabel, FindVpActor, FindWorldActor) — different
		// names are not a build error, which is exactly why they survived, but it meant a fix to the
		// path/name/label matching rule landed in one of five places. Do NOT add a sixth.
	}

	// --- add_nav_volume -----------------------------------------------------
	//   in:  { location:{x,y,z}, size:{x,y,z} (world units), label? }
	//   out: { actorPath, coverage:{x,y,z} }
	// Places the region the nav mesh will be generated inside.
	void H_add_nav_volume(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		FVector Loc(0, 0, 0);
		const TSharedPtr<FJsonObject>* L = nullptr;
		if (In->TryGetObjectField(TEXT("location"), L) && L)
		{
			const TSharedRef<FJsonObject> O = L->ToSharedRef();
			Loc = FVector(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
		}
		// Desired coverage in WORLD UNITS; converted to brush scale below.
		FVector Size(20000, 20000, 2000);
		const TSharedPtr<FJsonObject>* S = nullptr;
		if (In->TryGetObjectField(TEXT("size"), S) && S)
		{
			const TSharedRef<FJsonObject> O = S->ToSharedRef();
			Size = FVector(JNum(O, TEXT("x"), 20000), JNum(O, TEXT("y"), 20000), JNum(O, TEXT("z"), 2000));
		}

		ANavMeshBoundsVolume* Volume = World->SpawnActor<ANavMeshBoundsVolume>(Loc, FRotator::ZeroRotator);
		if (!Volume) { Fail(Out, TEXT("failed to spawn ANavMeshBoundsVolume")); return; }
		Volume->SetActorLabel(JStr(In, TEXT("label"), TEXT("NavBounds")));

		// The default builder brush is a 200-unit cube, so scale = desired / 200.
		const FVector Scale(Size.X / 200.0, Size.Y / 200.0, Size.Z / 200.0);
		Volume->SetActorScale3D(Scale);
		if (UBrushComponent* Brush = Volume->GetBrushComponent())
		{
			Brush->UpdateBounds();
			Brush->MarkRenderStateDirty();
		}
		// Tell the nav system the region changed, or the next build ignores it.
		if (UNavigationSystemV1* Nav = FNavigationSystem::GetCurrent<UNavigationSystemV1>(World))
		{
			Nav->OnNavigationBoundsUpdated(Volume);
		}

		Out->SetStringField(TEXT("actorPath"), Volume->GetPathName());
		Out->SetStringField(TEXT("label"), Volume->GetActorLabel());
		TSharedRef<FJsonObject> Cov = MakeShared<FJsonObject>();
		Cov->SetNumberField(TEXT("x"), Size.X); Cov->SetNumberField(TEXT("y"), Size.Y); Cov->SetNumberField(TEXT("z"), Size.Z);
		Out->SetObjectField(TEXT("coverage"), Cov);
		Out->SetStringField(TEXT("note"), TEXT("call build_navmesh next, then nav_status to confirm tiles were actually generated"));
	}

	// --- build_navmesh ------------------------------------------------------
	// Kicks off generation. Building is ASYNC (tiles are cooked over subsequent frames) and this
	// handler runs on the game thread, so it must NOT wait — poll nav_status, exactly like PIE.
	void H_build_navmesh(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		UNavigationSystemV1* Nav = FNavigationSystem::GetCurrent<UNavigationSystemV1>(World);
		if (!Nav)
		{
			Fail(Out, TEXT("no navigation system in this world — add a NavMeshBoundsVolume first (add_nav_volume)"));
			return;
		}

		int32 Volumes = 0;
		for (TActorIterator<ANavMeshBoundsVolume> It(World); It; ++It) { ++Volumes; }
		if (Volumes == 0)
		{
			Fail(Out, TEXT("no NavMeshBoundsVolume in the level — nav would cover nothing. Call add_nav_volume first."));
			return;
		}

		Nav->Build();
		Out->SetBoolField(TEXT("requested"), true);
		Out->SetNumberField(TEXT("boundsVolumes"), Volumes);
		Out->SetStringField(TEXT("note"),
			TEXT("generation is asynchronous — this call does NOT block. Poll nav_status until building=false and tiles>0."));
	}

	// --- nav_status ---------------------------------------------------------
	//   out: { hasNavSystem, boundsVolumes, navMeshActors, tiles, building, ready }
	// Reports the TILE COUNT, not just success: a mis-sized bounds volume builds "successfully"
	// with zero tiles, and every subsequent pathing call then fails for no visible reason.
	void H_nav_status(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		UNavigationSystemV1* Nav = FNavigationSystem::GetCurrent<UNavigationSystemV1>(World);
		Out->SetBoolField(TEXT("hasNavSystem"), Nav != nullptr);

		int32 Volumes = 0;
		for (TActorIterator<ANavMeshBoundsVolume> It(World); It; ++It) { ++Volumes; }
		Out->SetNumberField(TEXT("boundsVolumes"), Volumes);

		int32 NavMeshes = 0, Tiles = 0;
		for (TActorIterator<ARecastNavMesh> It(World); It; ++It)
		{
			++NavMeshes;
			if (ARecastNavMesh* Recast = *It)
			{
				Tiles += Recast->GetNavMeshTilesCount();
			}
		}
		const bool bBuilding = Nav && Nav->IsNavigationBuildInProgress();
		Out->SetNumberField(TEXT("navMeshActors"), NavMeshes);
		Out->SetNumberField(TEXT("tiles"), Tiles);
		Out->SetBoolField(TEXT("building"), bBuilding);
		Out->SetBoolField(TEXT("ready"), !bBuilding && Tiles > 0);
		Out->SetStringField(TEXT("world"), World->GetName());
		if (!bBuilding && Tiles == 0 && Volumes > 0)
		{
			Out->SetStringField(TEXT("warning"),
				TEXT("bounds volume exists but ZERO tiles were generated — the volume probably does not overlap any walkable geometry (check its size/Z range against the ground)"));
		}
	}

	// --- move_actor_to ------------------------------------------------------
	//   in:  { actorPath, location:{x,y,z} }   out: { moving }
	// Issues a nav-driven move. Requires PIE (an AIController only exists at runtime) and a built
	// nav mesh — both failure modes are reported distinctly so you know which one bit you.
	void H_move_actor_to(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }
		if (!GEditor || !GEditor->PlayWorld)
		{
			Fail(Out, TEXT("move_actor_to needs a running PIE session — AI controllers only exist at runtime. start_pie first."));
			return;
		}

		AActor* Actor = FindActorInWorld(World, JStrAny(In, { TEXT("actorPath"), TEXT("actor") }));
		if (!Actor) { Fail(Out, TEXT("actor not found in the PIE world")); return; }

		APawn* Pawn = Cast<APawn>(Actor);
		if (!Pawn) { Fail(Out, FString::Printf(TEXT("'%s' is not a Pawn — only pawns can path"), *Actor->GetActorLabel())); return; }
		AController* Controller = Pawn->GetController();
		if (!Controller)
		{
			Fail(Out, TEXT("pawn has no controller — it needs an AIController (check the pawn's AutoPossessAI setting)"));
			return;
		}

		FVector Goal(0, 0, 0);
		const TSharedPtr<FJsonObject>* L = nullptr;
		if (In->TryGetObjectField(TEXT("location"), L) && L)
		{
			const TSharedRef<FJsonObject> O = L->ToSharedRef();
			Goal = FVector(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
		}

		UAIBlueprintHelperLibrary::SimpleMoveToLocation(Controller, Goal);
		Out->SetBoolField(TEXT("moving"), true);
		Out->SetStringField(TEXT("actor"), Actor->GetActorLabel());
		Out->SetStringField(TEXT("controller"), Controller->GetClass()->GetName());
	}
}
