// The performance view — what in this level actually costs something.
//
// Andre: "a performance dashboard to show whats the most fps consuming and stuff".
//
// ============================================================================================
// WHAT THIS CAN AND CANNOT TELL YOU, said first because a performance number that is quietly
// measuring the wrong thing is worse than no number.
// ============================================================================================
//
// get_perf_stats already reports editor frame time and RHI draw calls, and its own caveat is blunt
// about them: they describe THE EDITOR rendering its own viewport, with UI, gizmos and selection
// outlines included. They are not the game's performance. Ranking actors by "FPS cost" measured that
// way would be inventing precision.
//
// So this ranks by STATIC COST - the properties of the content itself, which are reproducible and are
// what an artist can actually act on:
//
//   triangles   - LOD0 triangles across the actor's mesh components
//   components  - how many primitive components it drags along
//   materials   - material slots, which is a draw-call proxy
//   drawEst     - components x material slots: a rough draw-call estimate
//
// That is a CENSUS, not a profile. It will not find a Blueprint burning milliseconds in Tick, and it
// does not pretend to. For that you need Unreal Insights, and this endpoint says so rather than
// implying it is a substitute.
//
// The honest framing matters because the whole competitor comparison here is a Performance tab that
// reads Insights traces. Matching the tab is easy; matching what it MEASURES is not, and shipping a
// worse thing under the same name is how a tool loses trust.

#include "MifBridgeHandlers.h"

#include "EngineUtils.h"                          // TActorIterator
#include "Rendering/SkeletalMeshRenderData.h"     // FSkeletalMeshRenderData::LODRenderData

#include "Components/PrimitiveComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/SkeletalMesh.h"
#include "GameFramework/Actor.h"

namespace MifBridge
{
	// LOD0 triangle count for whatever mesh a component carries. Returns 0 for components that are not
	// mesh-bearing, which is most of them.
	//
	// SHARED, not file-local: the perf PANEL needs exactly this number, and a second copy would be a
	// second definition of what "triangles" means. Two counters that disagree is worse than one that is
	// imperfect.
	int32 PerfTrianglesFor(UPrimitiveComponent* Comp)
	{
		if (const UStaticMeshComponent* SMC = Cast<UStaticMeshComponent>(Comp))
		{
			if (UStaticMesh* Mesh = SMC->GetStaticMesh())
			{
				// RenderData can be null on a cooked-but-unloaded mesh, and dereferencing it is the
				// gotchas 6c family of crash. Checked, not assumed.
				if (Mesh->GetRenderData() && Mesh->GetRenderData()->LODResources.Num() > 0)
				{
					return Mesh->GetRenderData()->LODResources[0].GetNumTriangles();
				}
			}
		}
		else if (const USkeletalMeshComponent* SkC = Cast<USkeletalMeshComponent>(Comp))
		{
			if (USkeletalMesh* Mesh = SkC->GetSkeletalMeshAsset())
			{
				if (const FSkeletalMeshRenderData* RD = Mesh->GetResourceForRendering())
				{
					if (RD->LODRenderData.Num() > 0)
					{
						return RD->LODRenderData[0].GetTotalFaces();
					}
				}
			}
		}
	return 0;
	}

	// --- perf_heavy_actors ----------------------------------------------------
	//   in:  { limit?, sortBy? }
	//   out: { world, actorsExamined, totals{...}, actors:[{...}], caveat }
	void H_perf_heavy_actors(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("limit"), TEXT("sortBy") },
			TEXT("limit (default 40); sortBy one of triangles|components|materials|drawEst "
				 "(default triangles)"),
			{ { TEXT("fps"), TEXT("this measures STATIC content cost, not frame time - get_perf_stats reports editor timing, and its caveat explains why that is not the game's fps") },
			  { TEXT("profile"), TEXT("this is a census of the level, not a profiler. Unreal Insights is the profiler; nothing here replaces it") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 40), 1, 500);
		const FString SortBy = JStr(In, TEXT("sortBy"), TEXT("triangles"));

		struct FRow
		{
			FString Name, Class, Path;
			int32 Tris = 0, Comps = 0, Mats = 0;
			int32 DrawEst() const { return Comps * FMath::Max(Mats, 1); }
		};
		TArray<FRow> Rows;
		int64 TotalTris = 0;
		int32 TotalComps = 0, Examined = 0;

		for (TActorIterator<AActor> It(World); It; ++It)
		{
			AActor* Actor = *It;
			if (!IsValid(Actor)) { continue; }
			++Examined;

			FRow R;
			R.Name = Actor->GetActorLabel();
			R.Class = Actor->GetClass()->GetName();
			R.Path = Actor->GetPathName();

			TArray<UPrimitiveComponent*> Prims;
			Actor->GetComponents<UPrimitiveComponent>(Prims);
			for (UPrimitiveComponent* P : Prims)
			{
				if (!IsValid(P)) { continue; }
				++R.Comps;
				R.Mats += P->GetNumMaterials();
				R.Tris += PerfTrianglesFor(P);
			}
			TotalTris += R.Tris;
			TotalComps += R.Comps;
			// Actors with no primitive components cost nothing to DRAW, and listing hundreds of them
			// would bury the ones that do. They are still counted in actorsExamined.
			if (R.Comps > 0) { Rows.Add(R); }
		}

		if (SortBy == TEXT("components"))
		{
			Rows.Sort([](const FRow& A, const FRow& B) { return A.Comps > B.Comps; });
		}
		else if (SortBy == TEXT("materials"))
		{
			Rows.Sort([](const FRow& A, const FRow& B) { return A.Mats > B.Mats; });
		}
		else if (SortBy == TEXT("drawEst"))
		{
			Rows.Sort([](const FRow& A, const FRow& B) { return A.DrawEst() > B.DrawEst(); });
		}
		else
		{
			Rows.Sort([](const FRow& A, const FRow& B) { return A.Tris > B.Tris; });
		}

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (int32 i = 0; i < Rows.Num() && i < Limit; ++i)
		{
			const FRow& R = Rows[i];
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), R.Name);
			J->SetStringField(TEXT("class"), R.Class);
			J->SetStringField(TEXT("actorPath"), R.Path);
			J->SetNumberField(TEXT("triangles"), R.Tris);
			J->SetNumberField(TEXT("components"), R.Comps);
			J->SetNumberField(TEXT("materials"), R.Mats);
			J->SetNumberField(TEXT("drawEst"), R.DrawEst());
			// The share of the level's triangles this one actor accounts for. A rank is only actionable
			// next to a proportion: first place out of a flat distribution is not worth touching.
			J->SetNumberField(TEXT("trianglePercent"),
				TotalTris > 0 ? (double)R.Tris * 100.0 / (double)TotalTris : 0.0);
			Arr.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetStringField(TEXT("world"), World->GetName());
		Out->SetStringField(TEXT("sortedBy"), SortBy);
		Out->SetNumberField(TEXT("actorsExamined"), Examined);
		Out->SetNumberField(TEXT("actorsWithGeometry"), Rows.Num());
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetBoolField(TEXT("truncated"), Rows.Num() > Arr.Num());

		TSharedRef<FJsonObject> Totals = MakeShared<FJsonObject>();
		Totals->SetNumberField(TEXT("triangles"), (double)TotalTris);
		Totals->SetNumberField(TEXT("primitiveComponents"), TotalComps);
		Out->SetObjectField(TEXT("totals"), Totals);
		Out->SetArrayField(TEXT("actors"), Arr);

		Out->SetStringField(TEXT("caveat"),
			TEXT("This is a CENSUS of static content cost - triangles, components and material slots - "
				 "not a profile. It cannot see a Blueprint burning milliseconds in Tick, and it is not "
				 "frame time: get_perf_stats reports editor timing, and its own caveat explains why that "
				 "is the editor drawing its viewport rather than the game's fps. For real frame "
				 "attribution use Unreal Insights. Nothing here replaces it."));
		Out->SetStringField(TEXT("drawEstNote"),
			TEXT("drawEst is components x material slots - a rough draw-call proxy, not a measurement. "
				 "Instancing, nanite and merged sections all break the assumption; treat it as a way to "
				 "rank actors against each other, never as a count."));
	}
}
