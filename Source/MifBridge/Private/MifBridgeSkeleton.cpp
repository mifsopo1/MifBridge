// MifBridge — SKELETON BONES (read).
//
// Nothing in this bridge could name a bone. describe_animation reports curves, notifies and sync
// markers but no tracks; list_sockets reports sockets, which ATTACH to bones without enumerating
// them; and reflection cannot help, because USkeleton::ReferenceSkeleton is a plain C++ member and
// not a UPROPERTY. get_property on a Skeleton reaches BoneTree, which holds per-bone RETARGETING
// MODES and no names at all. So "what bones does this skeleton have" had no answer.
//
// That is a gap on its own — every question about attaching, constraining or retargeting starts with
// a bone name — and it is a hard prerequisite for IK Rig work, where a retarget chain is defined as
// (name, startBone, endBone) and cannot be authored against a skeleton whose bones you cannot list.
//
// A MESH IS ACCEPTED AS WELL AS A SKELETON, and the distinction is reported rather than smoothed
// over, because the two do not always agree. A SkeletalMesh carries its own FReferenceSkeleton, and a
// mesh imported against a skeleton can legitimately hold FEWER bones than the skeleton defines. Which
// one was read decides whether a bone name will resolve at runtime, so `source` says which it was and
// `skeletonBoneCount` is reported alongside the mesh's own count when they differ. list_sockets made
// the opposite mistake once — it read only mesh sockets and reported an honest, correct and useless
// zero for every DDS2 character, because the sockets live on the shared skeleton.
//
// Read-only: nothing is loaded for writing, nothing is dirtied.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"
#include "Rendering/SkeletalMeshRenderData.h"
#include "Rendering/SkeletalMeshLODRenderData.h"
#include "Rendering/SkinWeightVertexBuffer.h"

#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "ReferenceSkeleton.h"

namespace MifBridge
{
	namespace
	{
		/** Prefixed rather than called RetargetModeName, because this module builds as a unity blob and
		 *  a colliding short helper name is the C2084 PM-005 records. */
		const TCHAR* SkelRetargetModeName(EBoneTranslationRetargetingMode::Type Mode)
		{
			switch (Mode)
			{
			case EBoneTranslationRetargetingMode::Animation:             return TEXT("Animation");
			case EBoneTranslationRetargetingMode::Skeleton:              return TEXT("Skeleton");
			case EBoneTranslationRetargetingMode::AnimationScaled:       return TEXT("AnimationScaled");
			case EBoneTranslationRetargetingMode::AnimationRelative:     return TEXT("AnimationRelative");
			case EBoneTranslationRetargetingMode::OrientAndScale:        return TEXT("OrientAndScale");
			default:                                                     return TEXT("Unknown");
			}
		}
	}

	// --- list_bones ----------------------------------------------------------
	//   in:  { path, nameContains?, includeTransforms? }
	//   out: { skeleton, source, boneCount, bones:[{ name, index, parent, parentIndex, depth, … }] }
	//
	// The bone hierarchy of a Skeleton or a SkeletalMesh. See the file header for why the source is
	// reported rather than assumed - a mesh and its skeleton can hold different bones.
	void H_list_bones(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("skeleton"), TEXT("mesh"),
			  TEXT("nameContains"), TEXT("includeTransforms"), TEXT("root") },
			TEXT("path (aliases: assetPath, skeleton, mesh) of a Skeleton or SkeletalMesh; "
				 "nameContains to filter; root to list only one bone and its descendants; "
				 "includeTransforms for the reference pose"),
			{ { TEXT("socket"), TEXT("sockets are list_sockets - this lists BONES") },
			  { TEXT("depth"), TEXT("depth is reported per bone; there is no depth limit parameter") } }))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("skeleton"), TEXT("mesh") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a Skeleton or SkeletalMesh asset."));
			return;
		}
		UObject* Asset = LoadObject<UObject>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("no asset at %s"), *Path));
			return;
		}

		// Which FReferenceSkeleton is being read is a real decision, not a detail - the mesh's and the
		// skeleton's can differ - so it is made explicitly here and reported below.
		const FReferenceSkeleton* Ref = nullptr;
		USkeleton* Skeleton = Cast<USkeleton>(Asset);
		USkeletalMesh* Mesh = Cast<USkeletalMesh>(Asset);
		FString Source;
		if (Mesh)
		{
			Ref = &Mesh->GetRefSkeleton();
			Source = TEXT("skeletalMesh");
			Skeleton = Mesh->GetSkeleton();
		}
		else if (Skeleton)
		{
			Ref = &Skeleton->GetReferenceSkeleton();
			Source = TEXT("skeleton");
		}
		else
		{
			Fail(Out, FString::Printf(
				TEXT("%s is a %s. This reads a Skeleton or a SkeletalMesh; nothing else has a bone "
					 "hierarchy."), *Path, *Asset->GetClass()->GetName()));
			return;
		}
		const int32 Num = Ref->GetNum();
		if (Num == 0)
		{
			// An empty answer that says WHICH empty it is. A cooked mesh whose render data was stripped
			// and a genuinely boneless asset are different problems.
			Fail(Out, FString::Printf(
				TEXT("%s has a reference skeleton with no bones. For a cooked asset this usually means "
					 "the data was stripped rather than that the asset is boneless."), *Path));
			return;
		}

		// Depth is computed by walking to the root rather than assumed from index order. Parent indices
		// are always lower than child indices in a valid FReferenceSkeleton, but relying on that would
		// turn a malformed asset into a silently wrong tree instead of a visible one.
		TArray<int32> Depth;
		Depth.SetNumUninitialized(Num);
		for (int32 i = 0; i < Num; ++i)
		{
			int32 D = 0;
			for (int32 P = Ref->GetParentIndex(i); P != INDEX_NONE && D <= Num; P = Ref->GetParentIndex(P))
			{
				++D;
			}
			Depth[i] = D;
		}

		const FString Filter = JStr(In, TEXT("nameContains"));
		const FString RootName = JStr(In, TEXT("root"));
		const bool bTransforms = JBool(In, TEXT("includeTransforms"), false);
		int32 RootIndex = INDEX_NONE;
		if (!RootName.IsEmpty())
		{
			RootIndex = Ref->FindBoneIndex(FName(*RootName));
			if (RootIndex == INDEX_NONE)
			{
				Fail(Out, FString::Printf(
					TEXT("no bone named '%s' on this %s. Call this without 'root' to see the bones it "
						 "does have."), *RootName, *Source));
				return;
			}
		}

		const TArray<FTransform>& Pose = Ref->GetRefBonePose();
		TArray<TSharedPtr<FJsonValue>> Bones;
		int32 Shown = 0;
		for (int32 i = 0; i < Num; ++i)
		{
			if (RootIndex != INDEX_NONE)
			{
				bool bUnder = (i == RootIndex);
				for (int32 P = Ref->GetParentIndex(i); !bUnder && P != INDEX_NONE; P = Ref->GetParentIndex(P))
				{
					bUnder = (P == RootIndex);
				}
				if (!bUnder) { continue; }
			}
			const FName Name = Ref->GetBoneName(i);
			if (!Filter.IsEmpty() && !Name.ToString().Contains(Filter)) { continue; }
			++Shown;

			TSharedRef<FJsonObject> B = MakeShared<FJsonObject>();
			B->SetStringField(TEXT("name"), Name.ToString());
			B->SetNumberField(TEXT("index"), i);
			B->SetNumberField(TEXT("depth"), Depth[i]);
			const int32 Parent = Ref->GetParentIndex(i);
			B->SetNumberField(TEXT("parentIndex"), Parent);
			// The NAME as well as the index, because a caller building a chain works in names and
			// resolving the index itself is a lookup this already did.
			B->SetStringField(TEXT("parent"), Parent == INDEX_NONE
				? FString() : Ref->GetBoneName(Parent).ToString());
			if (Parent == INDEX_NONE) { B->SetBoolField(TEXT("isRoot"), true); }

			// Per-bone retargeting mode, which is the one thing get_property COULD already reach (via
			// BoneTree) and could not attach a name to. It only exists on the skeleton, so a mesh-sourced
			// read still reports it when the mesh has one.
			if (Skeleton)
			{
				const int32 TreeIdx = Skeleton->GetReferenceSkeleton().FindBoneIndex(Name);
				if (TreeIdx != INDEX_NONE)
				{
					B->SetStringField(TEXT("translationRetargeting"),
						SkelRetargetModeName(Skeleton->GetBoneTranslationRetargetingMode(TreeIdx)));
				}
			}
			if (bTransforms && Pose.IsValidIndex(i))
			{
				// PARENT-RELATIVE, and said so: a caller treating these as world space would place
				// everything on top of the root.
				const FTransform& T = Pose[i];
				TSharedRef<FJsonObject> Xf = MakeShared<FJsonObject>();
				Xf->SetObjectField(TEXT("location"), Vec3(T.GetLocation()));
				const FRotator R = T.GetRotation().Rotator();
				Xf->SetObjectField(TEXT("rotation"), Vec3(FVector(R.Pitch, R.Yaw, R.Roll)));
				Xf->SetObjectField(TEXT("scale"), Vec3(T.GetScale3D()));
				B->SetObjectField(TEXT("refPose"), Xf);
			}
			Bones.Add(MakeShared<FJsonValueObject>(B));
		}

		Out->SetStringField(TEXT("path"), Asset->GetPathName());
		Out->SetStringField(TEXT("assetKind"), Asset->GetClass()->GetName());
		// WHICH reference skeleton was read. See the header: a mesh and its skeleton can hold different
		// bones, and which one you read decides whether a name resolves at runtime.
		Out->SetStringField(TEXT("source"), Source);
		Out->SetStringField(TEXT("skeleton"), Skeleton ? Skeleton->GetPathName() : FString());
		Out->SetNumberField(TEXT("boneCount"), Num);
		Out->SetNumberField(TEXT("count"), Shown);
		if (Skeleton && Mesh)
		{
			const int32 SkelNum = Skeleton->GetReferenceSkeleton().GetNum();
			Out->SetNumberField(TEXT("skeletonBoneCount"), SkelNum);
			if (SkelNum != Num)
			{
				// Stated only when they disagree, because that is when it changes what a caller should do.
				Out->SetStringField(TEXT("sourceNote"), FString::Printf(
					TEXT("this MESH has %d bones and its SKELETON defines %d. The bones listed are the "
						 "mesh's. A name present on the skeleton but not here will not resolve on this "
						 "mesh; pass the skeleton's path to list those instead."), Num, SkelNum));
			}
		}
		if (bTransforms)
		{
			Out->SetStringField(TEXT("transformNote"),
				TEXT("refPose is the reference pose and is PARENT-RELATIVE, not world space. The root "
					 "bone's transform is the only one already in component space."));
		}
		Out->SetArrayField(TEXT("bones"), Bones);
		UE_LOG(LogMifBridge, Log, TEXT("list_bones: %d of %d on %s"), Shown, Num, *Asset->GetName());
	}

	// --- analyze_skeletal_split -----------------------------------------------------------------
	//   in:  { path (aliases: assetPath, mesh, skeletalMesh), lod? = 0 }
	//   out: { sections[ { index, vertices, triangles, bones[], maxBoneInfluences } ],
	//          bones[ { name, index, sections[] } ], separable[], verdict }
	// Bucket: READ. Loads the mesh; changes nothing.
	//
	// THE READ HALF of the mesh splitter Andre asked for after seeing the competitor's "Mesh Splitter -
	// split a skeletal mesh at bone boundaries into separate mesh assets". Splitting CREATES ASSETS,
	// which this bridge deliberately cannot do, so that half is a separate decision. This half needs no
	// save path and answers the question you have to answer first: WOULD a split work, and where?
	//
	// SECTIONS, NOT PER-VERTEX WEIGHTS, and that is a deliberate choice rather than the easy one.
	//
	// The obvious implementation walks the skin weight buffer, finds each vertex's dominant bone, and
	// buckets vertices by bone. FSkinWeightVertexBuffer's CPU copy CAN be discarded - it is kept only
	// when bAllowCPUAccess was set at import - so GetNeedsCPUAccess() is checked and reported rather
	// than assumed, and the primary answer does not depend on it.
	//
	// I WROTE HERE THAT COOKED MESHES USUALLY LOSE THAT COPY, AND THEN MEASURED IT. Across 40 DDS2
	// skeletal meshes under /Game: 40 CPU-readable, 0 GPU-only. Being cooked did not cost it once.
	// The guard stays because the engine genuinely can drop the buffer and a splitter that discovered
	// that at split time would have already promised - but the pessimism was mine, not the data's.
	//
	// The same sweep says something that matters more for the splitter: 24 of those 40 meshes have
	// exactly ONE section. Section boundaries cannot split those at all, so a real splitter needs the
	// per-vertex path for most of this project - and per-vertex turns out to be available.
	//
	// A render SECTION already carries what matters: its own vertex and triangle counts, and a BoneMap
	// listing every bone its vertices are skinned to. Sections are also the boundary a real splitter
	// would cut on - they are already separate draw calls with their own material. So this reports the
	// structure that exists rather than inferring one that might.
	//
	// Verified in BOTH trees before writing:
	//   USkeletalMesh::GetResourceForRendering   present in both (SkeletalMesh.h)
	//   FSkelMeshRenderSection::BoneMap          5.3 SkeletalMeshLODRenderData.h:67   5.7 :68
	//   ::NumVertices / ::NumTriangles / ::MaxBoneInfluences   same struct, both trees
	//   FSkinWeightVertexBuffer::GetNeedsCPUAccess             present in both
	// The only difference is FORCEINLINE vs inline on the accessors - declaration-side, no guard.
	void H_analyze_skeletal_split(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("skeletalMesh"), TEXT("lod") },
			TEXT("path (aliases: assetPath, mesh, skeletalMesh) - a SkeletalMesh asset; lod (default 0)"),
			{ { TEXT("bone"), TEXT("this reports EVERY bone and which sections use it - filter the result rather than the query") },
			  { TEXT("split"), TEXT("this only ANALYSES. Splitting creates assets, which this bridge does not do.") } }))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"),
										   TEXT("mesh"), TEXT("skeletalMesh") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a SkeletalMesh asset."));
			return;
		}
		USkeletalMesh* Mesh = LoadObject<USkeletalMesh>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Mesh)
		{
			Fail(Out, FString::Printf(
				TEXT("no SkeletalMesh at '%s'. list_bones takes the same path if you want to check it "
					 "resolves."), *Path));
			return;
		}

		FSkeletalMeshRenderData* Render = Mesh->GetResourceForRendering();
		if (!Render || Render->LODRenderData.Num() == 0)
		{
			Fail(Out, TEXT("this mesh has no render data, so it has no sections to analyse. That "
						   "usually means it failed to build rather than that it is empty."));
			return;
		}

		const int32 LodCount = Render->LODRenderData.Num();
		int32 Lod = (int32)JNum(In, TEXT("lod"), 0.0);
		if (Lod < 0 || Lod >= LodCount)
		{
			// Refused, not clamped - the same rule as get_collision. A clamped index answers about a
			// different LOD under the number the caller asked for.
			Fail(Out, FString::Printf(
				TEXT("lod %d does not exist - this mesh has %d LOD(s), so valid indices are 0..%d."),
				Lod, LodCount, LodCount - 1));
			return;
		}

		const FSkeletalMeshLODRenderData& Data = Render->LODRenderData[Lod];
		const FReferenceSkeleton& Ref = Mesh->GetRefSkeleton();

		// bone index -> the sections that use it. Built while walking sections so the two views are
		// guaranteed consistent; deriving one from the other afterwards is how they drift.
		TMap<int32, TArray<int32>> SectionsForBone;

		TArray<TSharedPtr<FJsonValue>> Sections;
		int32 TotalVerts = 0, TotalTris = 0;
		for (int32 s = 0; s < Data.RenderSections.Num(); ++s)
		{
			const FSkelMeshRenderSection& Sec = Data.RenderSections[s];
			TotalVerts += (int32)Sec.NumVertices;
			TotalTris  += (int32)Sec.NumTriangles;

			TArray<TSharedPtr<FJsonValue>> BoneNames;
			for (const FBoneIndexType B : Sec.BoneMap)
			{
				SectionsForBone.FindOrAdd((int32)B).AddUnique(s);
				// The NAME, not the index. A caller deciding where to cut a character thinks in
				// "spine_03", and an index is only meaningful against this exact skeleton.
				BoneNames.Add(MakeShared<FJsonValueString>(
					Ref.IsValidIndex((int32)B) ? Ref.GetBoneName((int32)B).ToString()
											   : FString::Printf(TEXT("(bad index %d)"), (int32)B)));
			}

			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetNumberField(TEXT("index"), s);
			J->SetNumberField(TEXT("vertices"), (int32)Sec.NumVertices);
			J->SetNumberField(TEXT("triangles"), (int32)Sec.NumTriangles);
			J->SetNumberField(TEXT("materialIndex"), (int32)Sec.MaterialIndex);
			J->SetNumberField(TEXT("maxBoneInfluences"), Sec.MaxBoneInfluences);
			J->SetNumberField(TEXT("boneCount"), Sec.BoneMap.Num());
			J->SetArrayField(TEXT("bones"), BoneNames);
			Sections.Add(MakeShared<FJsonValueObject>(J));
		}

		// Per bone, which sections it reaches. A bone used by ONE section can be cut cleanly; a bone
		// spanning several cannot, because splitting on it would divide every one of them.
		TArray<TSharedPtr<FJsonValue>> Bones;
		TArray<TSharedPtr<FJsonValue>> Separable;
		for (int32 b = 0; b < Ref.GetNum(); ++b)
		{
			const TArray<int32>* Used = SectionsForBone.Find(b);
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), Ref.GetBoneName(b).ToString());
			J->SetNumberField(TEXT("index"), b);
			TArray<TSharedPtr<FJsonValue>> SecList;
			if (Used)
			{
				for (int32 S : *Used) { SecList.Add(MakeShared<FJsonValueNumber>(S)); }
			}
			J->SetArrayField(TEXT("sections"), SecList);
			// A bone influencing NOTHING is not a defect - skeletons carry attachment and IK bones on
			// purpose - but it is the difference between "this bone is unused" and "this bone is
			// missing", and only one of those is a problem.
			J->SetBoolField(TEXT("influencesGeometry"), Used != nullptr);
			Bones.Add(MakeShared<FJsonValueObject>(J));

			if (Used && Used->Num() == 1)
			{
				Separable.Add(MakeShared<FJsonValueString>(Ref.GetBoneName(b).ToString()));
			}
		}

		Out->SetStringField(TEXT("assetPath"), Mesh->GetPathName());
		Out->SetNumberField(TEXT("lod"), Lod);
		Out->SetNumberField(TEXT("lodCount"), LodCount);
		Out->SetArrayField(TEXT("sections"), Sections);
		Out->SetNumberField(TEXT("sectionCount"), Sections.Num());
		Out->SetNumberField(TEXT("totalVertices"), TotalVerts);
		Out->SetNumberField(TEXT("totalTriangles"), TotalTris);
		Out->SetArrayField(TEXT("bones"), Bones);
		Out->SetNumberField(TEXT("boneCount"), Ref.GetNum());
		Out->SetArrayField(TEXT("cleanlySeparableBones"), Separable);

		// Whether a per-VERTEX split is even possible on this asset, reported rather than assumed.
		// This is the whole reason the analysis is section-based: on a cooked mesh the answer is
		// usually no, and a tool that discovered that only at split time would have already promised.
		const bool bCpu = Data.SkinWeightVertexBuffer.GetNeedsCPUAccess();
		Out->SetBoolField(TEXT("skinWeightsReadableOnCPU"), bCpu);
		Out->SetStringField(TEXT("perVertexNote"), bCpu
			? TEXT("this mesh keeps its skin weights CPU-readable, so a per-vertex split by dominant "
				   "bone is possible as well as a per-section one.")
			: TEXT("skin weights are GPU-only on this asset - the CPU copy was discarded, which "
				   "happens when a mesh was imported without bAllowCPUAccess. A per-vertex split is "
				   "NOT possible here; the section boundaries reported above are. Measured across 40 "
				   "DDS2 meshes this was rare - all 40 kept CPU access - so treat it as a property of "
				   "the asset rather than of being cooked."));

		Out->SetStringField(TEXT("verdict"), Sections.Num() <= 1
			? TEXT("ONE section: there is no section boundary to split on. Splitting this mesh would "
				   "mean cutting by vertex weight, which needs CPU-readable skin weights - see "
				   "skinWeightsReadableOnCPU.")
			: FString::Printf(
				TEXT("%d sections, %d of %d bones influence exactly one section and could be cut "
					 "cleanly. Sections are already separate draw calls with their own material, so "
					 "they are the natural split boundary."),
				Sections.Num(), Separable.Num(), Ref.GetNum()));
	}

}
