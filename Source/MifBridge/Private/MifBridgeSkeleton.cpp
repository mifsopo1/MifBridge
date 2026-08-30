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
#include "ScopedTransaction.h"
#include "MifBridgeLog.h"
#include "UObject/Package.h"   // PKG_Cooked - tested BEFORE any editor-only accessor
#if WITH_EDITORONLY_DATA
#include "Rendering/SkeletalMeshModel.h"   // FSkeletalMeshModel - editor-only source data
#endif
#include "Rendering/SkeletalMeshRenderData.h"
#include "Rendering/SkeletalMeshLODRenderData.h"
#include "Rendering/SkinWeightVertexBuffer.h"

#include "Animation/Skeleton.h"
#include "Animation/MorphTarget.h"
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

	// --- list_virtual_bones ----------------------------------------------------------
	//   in:  { path (aliases: assetPath, skeleton, mesh) }
	//   out: { skeleton, count, virtualBones:[{ name, source, target }] }
	//
	// Virtual bones are new links a rigger adds BETWEEN two existing bones (Skeleton Editor's "Add
	// Virtual Bone") and are baked into every animation on that skeleton at playback - list_bones does
	// not report them, because they are not in the ReferenceSkeleton it walks; they live in a separate
	// array (USkeleton::VirtualBones) that list_bones has no reason to touch.
	//
	// SKELETON-ONLY DATA, MESH ACCEPTED ANYWAY. Same resolution list_bones already uses: a
	// SkeletalMesh's own Skeleton is looked up via GetSkeleton(), because "which skeleton does this
	// mesh's virtual bone set come from" is the same question list_bones already answers for the
	// reference skeleton, and forcing a caller to resolve it themselves first would just move the
	// lookup, not remove it.
	//
	// Verified in BOTH trees before writing: USkeleton::GetVirtualBones() and the plain,
	// non-editor-only UPROPERTY() FVirtualBone{SourceBoneName,TargetBoneName,VirtualBoneName} are
	// identical on 5.3 and 5.7 (Skeleton.h). No version guard needed.
	void H_list_virtual_bones(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("skeleton"), TEXT("mesh") },
			TEXT("path (aliases: assetPath, skeleton, mesh) - a Skeleton, or a SkeletalMesh whose "
				 "assigned Skeleton will be read"),
			{ { TEXT("bone"), TEXT("this lists ALL virtual bones - filter the result rather than the query") } }))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("skeleton"), TEXT("mesh") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a Skeleton, or a SkeletalMesh whose Skeleton will be read."));
			return;
		}
		UObject* Asset = LoadObject<UObject>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("no asset at %s"), *Path));
			return;
		}

		USkeleton* Skeleton = Cast<USkeleton>(Asset);
		if (!Skeleton)
		{
			if (USkeletalMesh* Mesh = Cast<USkeletalMesh>(Asset))
			{
				Skeleton = Mesh->GetSkeleton();
				if (!Skeleton)
				{
					Fail(Out, FString::Printf(
						TEXT("%s has no Skeleton assigned, so there is no virtual bone set to read."), *Path));
					return;
				}
			}
			else
			{
				Fail(Out, FString::Printf(
					TEXT("%s is a %s. This reads a Skeleton, or a SkeletalMesh (via its assigned "
						 "Skeleton); nothing else has virtual bones."), *Path, *Asset->GetClass()->GetName()));
				return;
			}
		}

		const TArray<FVirtualBone>& VBones = Skeleton->GetVirtualBones();
		TArray<TSharedPtr<FJsonValue>> Rows;
		for (const FVirtualBone& VB : VBones)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), VB.VirtualBoneName.ToString());
			J->SetStringField(TEXT("source"), VB.SourceBoneName.ToString());
			J->SetStringField(TEXT("target"), VB.TargetBoneName.ToString());
			Rows.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetStringField(TEXT("skeleton"), Skeleton->GetPathName());
		Out->SetNumberField(TEXT("count"), Rows.Num());
		Out->SetArrayField(TEXT("virtualBones"), Rows);
		if (Rows.Num() == 0)
		{
			// A real, common answer, not a defect - most skeletons never need one. Said explicitly so a
			// caller does not read an empty array as "the read failed".
			Out->SetStringField(TEXT("note"),
				TEXT("this skeleton defines no virtual bones. That is normal, not an error - most "
					 "skeletons never need one."));
		}
		UE_LOG(LogMifBridge, Log, TEXT("list_virtual_bones: %d on %s"), Rows.Num(), *Skeleton->GetName());
	}

	// --- list_morph_targets ----------------------------------------------------------
	//   in:  { path (aliases: assetPath, mesh, skeletalMesh), lod? = 0 }
	//   out: { assetPath, count, lod, morphTargets:[{ name, path, hasDataForLod, vertexCount? }] }
	//
	// Morph target NAMES were unreachable the same way bone names were: USkeletalMesh::MorphTargets is
	// a UPROPERTY (so reflection COULD walk it) but holds object references, not names, and the
	// engine's own convenience function - K2_GetAllMorphTargetNames(), Blueprint-exposed for exactly
	// this - is the API this handler uses rather than re-deriving the same list by hand.
	//
	// NOT THE ImportedModel TRAP. analyze_skeletal_split's postmortem (this file, above) crashed the
	// editor calling an editor-only accessor on a cooked mesh. MorphTargets is a DIFFERENT property:
	// morph targets are RUNTIME data - a cooked build needs them to actually deform a face at play
	// time - so unlike ImportedModel there is no WITH_EDITORONLY_DATA guard on the declaration (see
	// this file's own earlier read of SkeletalMesh.h), and GetMorphTargets()'s
	// WaitUntilAsyncPropertyReleased call is waiting for the engine's async BUILD/load task, not for
	// editor-only source data that a cooked asset never had. Confirmed against real COOKED DDS2
	// content before this was trusted, not assumed from the header alone - see the spec entry for the
	// live-verification result.
	//
	// hasDataForLod is reported per target because a morph target CAN exist with no data at a given
	// LOD (it was authored for LOD0 and the reduction settings dropped it, or it was declared but
	// never sculpted) - that is a real, different answer from "this target does nothing", and
	// vertexCount is included only when there is data, rather than reported as a confusing 0 either way.
	void H_list_morph_targets(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("skeletalMesh"), TEXT("lod") },
			TEXT("path (aliases: assetPath, mesh, skeletalMesh) - a SkeletalMesh asset; lod (default 0) "
				 "- which LOD's data presence to report per target"),
			{ { TEXT("name"), TEXT("this lists ALL morph targets - filter the result rather than the query") } }))
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

		const int32 Lod = (int32)JNum(In, TEXT("lod"), 0.0);
		if (Lod < 0)
		{
			Fail(Out, FString::Printf(TEXT("lod %d is invalid - lod must be 0 or greater."), Lod));
			return;
		}

		const TArray<TObjectPtr<UMorphTarget>>& Targets = Mesh->GetMorphTargets();
		TArray<TSharedPtr<FJsonValue>> Rows;
		for (const UMorphTarget* MT : Targets)
		{
			if (!MT) { continue; }   // a null entry would be a real defect elsewhere; skip rather than crash reporting it
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), MT->GetFName().ToString());
			J->SetStringField(TEXT("path"), MT->GetPathName());
			const bool bHasData = MT->HasDataForLOD(Lod);
			J->SetBoolField(TEXT("hasDataForLod"), bHasData);
			if (bHasData)
			{
				const TArray<FMorphTargetLODModel>& LodModels = MT->GetMorphLODModels();
				if (LodModels.IsValidIndex(Lod))
				{
					J->SetNumberField(TEXT("vertexCount"), LodModels[Lod].NumVertices);
				}
			}
			Rows.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetStringField(TEXT("assetPath"), Mesh->GetPathName());
		Out->SetNumberField(TEXT("lod"), Lod);
		Out->SetNumberField(TEXT("count"), Rows.Num());
		Out->SetArrayField(TEXT("morphTargets"), Rows);
		if (Rows.Num() == 0)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("this mesh has no morph targets. That is normal for most meshes - only ones "
					 "authored for facial or blend-shape animation need them."));
		}
		UE_LOG(LogMifBridge, Log, TEXT("list_morph_targets: %d on %s"), Rows.Num(), *Mesh->GetName());
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

		// CAN A NEW MESH BE BUILT FROM THIS ONE AT ALL? The question the splitter actually turns on,
		// and it is not the same as whether the skin weights are readable.
		//
		// Splitting means CREATING a skeletal mesh, and the engine builds one from a
		// FSkeletalMeshModel / FMeshDescription - editor-only data, reached through GetImportedModel()
		// and GetMeshDescription(), both inside #if WITH_EDITORONLY_DATA. An editor BUILD has that API.
		// A COOKED ASSET loaded into that editor does not necessarily carry the DATA: cooking strips
		// it, which is the whole subject of docs/02 section 6c.
		//
		// So this is reported per mesh rather than assumed either way. Without an imported model a
		// splitter can read what a split would look like - the section and bone analysis above - and
		// cannot produce the result.
		// THE COOKED CHECK COMES FIRST, AND IT IS NOT AN OPTIMISATION - IT IS THE FIX.
		//
		// The first version of this called GetImportedModel() unconditionally and KILLED THE EDITOR on
		// the first cooked mesh it touched. The crash journal named the endpoint (analyze_skeletal_split
		// started, never finished), which is exactly what it exists for.
		//
		// GetImportedModel() is not a plain getter: it calls
		// WaitUntilAsyncPropertyReleased(ESkeletalMeshAsyncProperties::ImportedModel) first. On a
		// COOKED asset that property was stripped at cook time, and asking the engine to wait for
		// something that will never exist takes the process down rather than returning null.
		//
		// So the package flag is tested BEFORE the API is touched. A cooked mesh gets its answer from
		// the flag alone - which is the same answer, arrived at without the call that crashes.
		//
		// The general lesson, and docs/02 section 6c says it about other systems: on a cooked asset,
		// "does this editor-only accessor return null?" is the wrong question. The right one is "is
		// this cooked?", asked first, because the accessor may not survive being asked.
		const UPackage* Pkg = Mesh->GetPackage();
		const bool bCooked = Pkg && Pkg->HasAnyPackageFlags(PKG_Cooked);
		Out->SetBoolField(TEXT("cooked"), bCooked);

		if (bCooked)
		{
			Out->SetBoolField(TEXT("hasImportedModel"), false);
			Out->SetStringField(TEXT("buildNote"),
				TEXT("this mesh is COOKED, so its editor-only source data was stripped and a splitter "
					 "could not build a new mesh from it - there is nothing to build FROM. The section "
					 "and bone analysis above is still accurate. Note this is decided from the package "
					 "flag WITHOUT calling GetImportedModel(), which does not survive being called on a "
					 "cooked asset."));
		}
		else
		{
#if WITH_EDITORONLY_DATA
			const FSkeletalMeshModel* Imported = Mesh->GetImportedModel();
			const bool bHasImported = Imported != nullptr && Imported->LODModels.Num() > 0;
			Out->SetBoolField(TEXT("hasImportedModel"), bHasImported);
			Out->SetStringField(TEXT("buildNote"), bHasImported
				? TEXT("uncooked and keeps its imported model, so a splitter could BUILD new mesh "
					   "assets from this one.")
				: TEXT("uncooked, but no imported model is present - unusual, and worth reporting."));
#else
			Out->SetBoolField(TEXT("hasImportedModel"), false);
			Out->SetStringField(TEXT("buildNote"),
				TEXT("this MifBridge was built without editor-only data."));
#endif
		}

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

	// =======================================================================
	// VIRTUAL BONE AUTHORING - and the phantom bone the engine will happily make
	// =======================================================================
	//
	// THE GUARD THAT MATTERS MOST: AddNewVirtualBone DOES NOT CHECK THAT THE BONES EXIST.
	// Skeleton.cpp:1795-1806 rejects exactly one thing - a duplicate source/target PAIR - and then
	// adds the entry. Nothing anywhere asks whether either bone is in the reference skeleton.
	//
	// What a typo produces is therefore not an error. RebuildRefSkeleton silently skips the entry,
	// gated on `ParentIndex != INDEX_NONE && TargetIndex != INDEX_NONE`
	// (ReferenceSkeleton.cpp:487-488), so:
	//
	//     AddNewVirtualBone returns TRUE
	//     the entry sits in VirtualBones forever
	//     list_virtual_bones reports it, because it is really there
	//     and it exists in NO reference skeleton and drives NO animation
	//
	// A bone that is present in every listing and does nothing at all is far worse than a refusal:
	// there is nothing to notice. So both names are checked against GetReferenceSkeleton() here and
	// a bad one is refused before the engine is touched.
	//
	// NAMING IS VERSION-SPLIT, which the survey said it was not. The engine builds the name itself
	// as "VB <source>_<target>" (the prefix is Skeleton.cpp:112) and the out-param overload REPORTS
	// that name rather than accepting one. The overload that takes a name is AddNewNamedVirtualBone,
	// and it exists only on 5.6 (Skeleton.h:460) and 5.7 (:473) - it is ABSENT from 5.3 entirely.
	// So `name` uses the named overload where there is one and add-then-rename where there is not,
	// and either way the response echoes the name the skeleton actually holds.
	//
	// REMOVAL REPARENTS OTHER BONES. RemoveVirtualBones rewires every virtual bone whose source was
	// the one being removed to point at the removed bone's own source (Skeleton.cpp:1836-1841). So
	// deleting one bone silently edits others, and the response says which - that is not something a
	// caller can be expected to know.
	//
	// RENAME IS A VOID SILENT NO-OP. RenameVirtualBone (Skeleton.cpp:1868-1885) sets bModified only
	// when something matched and tells the caller nothing either way, so the original is verified to
	// exist first and the result is confirmed by reading the list back.

	static USkeleton* ResolveSkeletonForWrite(const TSharedRef<FJsonObject>& In,
											  const TSharedRef<FJsonObject>& Out)
	{
		const FString Path = JStrAny(In, { TEXT("skeleton"), TEXT("path"), TEXT("assetPath") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("skeleton is required (aliases: path, assetPath) - a Skeleton asset, or a ")
				TEXT("SkeletalMesh whose Skeleton will be used. NOTHING was changed."));
			return nullptr;
		}
		UObject* Asset = LoadAssetLenient(Path);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: %s. NOTHING was changed."), *Path));
			return nullptr;
		}
		USkeleton* Skeleton = Cast<USkeleton>(Asset);
		if (!Skeleton)
		{
			// NOT const: GetSkeleton() is const-qualified on a const mesh and would hand back a
			// const USkeleton*, which cannot be written through.
			if (USkeletalMesh* Mesh = Cast<USkeletalMesh>(Asset))
			{
				Skeleton = Mesh->GetSkeleton();
			}
		}
		if (!Skeleton)
		{
			Fail(Out, FString::Printf(
				TEXT("%s is a %s - virtual bones live on a Skeleton (or a SkeletalMesh's assigned ")
				TEXT("one). NOTHING was changed."), *Path, *Asset->GetClass()->GetName()));
			return nullptr;
		}
		// COOKED IS REFUSED. The API is not editor-gated and would run, but a virtual bone is baked
		// into animation data at cook time and a cooked project's sequences cannot be rebuilt - so
		// the bone would exist on the skeleton and evaluate to nothing in every sequence using it.
		const UPackage* Pkg = Skeleton->GetPackage();
		if (Pkg && Pkg->HasAnyPackageFlags(PKG_Cooked))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a COOKED skeleton. Virtual bones are baked into animation data at cook ")
				TEXT("time and the sequences on this skeleton cannot be rebuilt, so the bone would ")
				TEXT("exist here and evaluate to nothing in every animation that uses it. NOTHING ")
				TEXT("was changed."), *Skeleton->GetPathName()));
			return nullptr;
		}
		return Skeleton;
	}

	/** The list_virtual_bones row shape, reused so the read and write halves cannot drift apart. */
	static TSharedRef<FJsonObject> VirtualBoneJson(const FVirtualBone& VB)
	{
		TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
		J->SetStringField(TEXT("name"), VB.VirtualBoneName.ToString());
		J->SetStringField(TEXT("source"), VB.SourceBoneName.ToString());
		J->SetStringField(TEXT("target"), VB.TargetBoneName.ToString());
		return J;
	}

	static const FVirtualBone* FindVirtualBone(const USkeleton* Skeleton, const FName Name)
	{
		for (const FVirtualBone& VB : Skeleton->GetVirtualBones())
		{
			if (VB.VirtualBoneName == Name) { return &VB; }
		}
		return nullptr;
	}

	/** Refuses a bone name the reference skeleton does not hold, with near matches. */
	static bool RequireRealBone(const USkeleton* Skeleton, const FString& Bone, const TCHAR* Which,
								const TSharedRef<FJsonObject>& Out)
	{
		const FReferenceSkeleton& Ref = Skeleton->GetReferenceSkeleton();
		if (Ref.FindBoneIndex(FName(*Bone)) != INDEX_NONE) { return true; }
		TArray<FString> Near;
		for (int32 i = 0; i < Ref.GetNum() && Near.Num() < 8; ++i)
		{
			const FString N = Ref.GetBoneName(i).ToString();
			if (N.Contains(Bone) || Bone.Contains(N)) { Near.Add(N); }
		}
		Fail(Out, FString::Printf(
			TEXT("no bone '%s' in this skeleton's reference skeleton (%d bones), so '%s' would be a ")
			TEXT("PHANTOM virtual bone: AddNewVirtualBone does not check bone existence, ")
			TEXT("RebuildRefSkeleton silently skips entries whose bones do not resolve, and the ")
			TEXT("result is a bone that list_virtual_bones reports and that drives no animation at ")
			TEXT("all. %s list_bones lists them. NOTHING was changed."),
			*Bone, Ref.GetNum(), Which,
			Near.Num() ? *FString::Printf(TEXT("Did you mean: %s?"), *FString::Join(Near, TEXT(", ")))
					   : TEXT("")));
		return false;
	}

	// --- add_virtual_bone ---------------------------------------------------
	void H_add_virtual_bone(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("skeleton"), TEXT("path"), TEXT("assetPath"), TEXT("source"), TEXT("sourceBone"),
			  TEXT("target"), TEXT("targetBone"), TEXT("name") },
			TEXT("skeleton (aliases: path, assetPath); source (alias sourceBone); target (alias ")
			TEXT("targetBone); name - optional, the engine names it \"VB <source>_<target>\" otherwise"),
			{ { TEXT("parent"), TEXT("spell it `source` - a virtual bone is defined by the bone it is "
									 "measured FROM and the bone it is measured TO") } }))
		{
			return;
		}
		USkeleton* Skeleton = ResolveSkeletonForWrite(In, Out);
		if (!Skeleton) { return; }

		const FString Source = JStrAny(In, { TEXT("source"), TEXT("sourceBone") });
		const FString Target = JStrAny(In, { TEXT("target"), TEXT("targetBone") });
		if (Source.IsEmpty() || Target.IsEmpty())
		{
			Fail(Out, TEXT("source and target are both required - a virtual bone measures one bone ")
				TEXT("relative to another. NOTHING was changed."));
			return;
		}
		if (Source == Target)
		{
			Fail(Out, TEXT("source and target are the same bone, so the virtual bone would always be ")
				TEXT("the identity transform. NOTHING was changed."));
			return;
		}
		// THE PHANTOM GUARD. Both, before anything is touched.
		if (!RequireRealBone(Skeleton, Source, TEXT("source"), Out)) { return; }
		if (!RequireRealBone(Skeleton, Target, TEXT("target"), Out)) { return; }

		// A duplicate PAIR is the one thing the engine does reject - it returns false and changes
		// nothing, which would otherwise look like an unexplained failure.
		for (const FVirtualBone& VB : Skeleton->GetVirtualBones())
		{
			if (VB.SourceBoneName == FName(*Source) && VB.TargetBoneName == FName(*Target))
			{
				Out->SetStringField(TEXT("skeleton"), Skeleton->GetPathName());
				Out->SetObjectField(TEXT("virtualBone"), VirtualBoneJson(VB));
				Out->SetBoolField(TEXT("created"), false);
				Out->SetStringField(TEXT("note"),
					TEXT("a virtual bone for that exact source/target pair already exists - nothing "
						 "was added, and nothing needed to be. created:false here means the end "
						 "state you asked for is already in place."));
				return;
			}
		}

		const FString WantName = JStr(In, TEXT("name"));
		const int32 Before = Skeleton->GetVirtualBones().Num();
		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_AddVirtualBone", "Add Virtual Bone"));
		Skeleton->Modify();

		FName MadeName = NAME_None;
#if MIF_ENGINE_AT_LEAST(5, 6)
		// 5.6+ can name it directly.
		if (!WantName.IsEmpty())
		{
			Skeleton->AddNewNamedVirtualBone(FName(*Source), FName(*Target), FName(*WantName));
			MadeName = FName(*WantName);
		}
		else
		{
			Skeleton->AddNewVirtualBone(FName(*Source), FName(*Target), MadeName);
		}
#else
		// 5.3 has NO named overload, so it is add-then-rename. The out-param reports the generated
		// name; it does not accept one.
		Skeleton->AddNewVirtualBone(FName(*Source), FName(*Target), MadeName);
		if (!WantName.IsEmpty() && MadeName != NAME_None)
		{
			Skeleton->RenameVirtualBone(MadeName, FName(*WantName));
			MadeName = FName(*WantName);
		}
#endif

		// READ BACK. The engine's bool says only that a duplicate pair was not found.
		const FVirtualBone* Made = FindVirtualBone(Skeleton, MadeName);
		if (!Made || Skeleton->GetVirtualBones().Num() <= Before)
		{
			Fail(Out, TEXT("the virtual bone was requested and the skeleton does not list it on ")
				TEXT("read-back. NOTHING usable was produced."));
			return;
		}
		Skeleton->MarkPackageDirty();

		Out->SetStringField(TEXT("skeleton"), Skeleton->GetPathName());
		// ECHOED FROM THE SKELETON, never from the request: the engine names it unless asked
		// otherwise, so reporting what was asked for would frequently be wrong.
		Out->SetObjectField(TEXT("virtualBone"), VirtualBoneJson(*Made));
		Out->SetBoolField(TEXT("created"), true);
		Out->SetNumberField(TEXT("countBefore"), Before);
		Out->SetNumberField(TEXT("count"), Skeleton->GetVirtualBones().Num());
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the skeleton is dirty and NOTHING has been saved."));
	}

	// --- remove_virtual_bone ------------------------------------------------
	void H_remove_virtual_bone(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("skeleton"), TEXT("path"), TEXT("assetPath"), TEXT("name"), TEXT("names"),
			  TEXT("confirm") },
			TEXT("skeleton (aliases: path, assetPath); name or names[]; confirm:true - removing a ")
			TEXT("virtual bone REPARENTS any virtual bone that used it as a source"),
			{ { TEXT("all"), TEXT("not supported - name them. Removing every virtual bone would "
								  "silently rewire the whole set through the reparenting rule") } }))
		{
			return;
		}
		USkeleton* Skeleton = ResolveSkeletonForWrite(In, Out);
		if (!Skeleton) { return; }

		TArray<FName> Names;
		const FString One = JStr(In, TEXT("name"));
		if (!One.IsEmpty()) { Names.Add(FName(*One)); }
		const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
		if (In->TryGetArrayField(TEXT("names"), Arr) && Arr)
		{
			for (const TSharedPtr<FJsonValue>& V : *Arr)
			{
				FString N;
				if (V.IsValid() && V->TryGetString(N) && !N.IsEmpty()) { Names.Add(FName(*N)); }
			}
		}
		if (Names.Num() == 0)
		{
			Fail(Out, TEXT("name or names[] is required. list_virtual_bones reports what exists. ")
				TEXT("NOTHING was changed."));
			return;
		}
		for (const FName& N : Names)
		{
			if (!FindVirtualBone(Skeleton, N))
			{
				TArray<FString> Have;
				for (const FVirtualBone& VB : Skeleton->GetVirtualBones())
				{
					Have.Add(VB.VirtualBoneName.ToString());
				}
				Fail(Out, FString::Printf(
					TEXT("no virtual bone named '%s'. This skeleton has: %s. NOTHING was changed."),
					*N.ToString(),
					Have.Num() ? *FString::Join(Have, TEXT(", ")) : TEXT("(none)")));
				return;
			}
		}

		// WORK OUT WHAT ELSE WILL CHANGE, before asking. RemoveVirtualBones rewires every virtual
		// bone whose SOURCE is one of these to point at that bone's own source - so removing one
		// silently edits others, and a caller cannot be expected to know that.
		TArray<TSharedPtr<FJsonValue>> WillReparent;
		for (const FVirtualBone& VB : Skeleton->GetVirtualBones())
		{
			if (Names.Contains(VB.SourceBoneName) && !Names.Contains(VB.VirtualBoneName))
			{
				const FVirtualBone* Removed = FindVirtualBone(Skeleton, VB.SourceBoneName);
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("name"), VB.VirtualBoneName.ToString());
				J->SetStringField(TEXT("sourceWas"), VB.SourceBoneName.ToString());
				J->SetStringField(TEXT("sourceBecomes"),
					Removed ? Removed->SourceBoneName.ToString() : FString());
				WillReparent.Add(MakeShared<FJsonValueObject>(J));
			}
		}

		if (!JBool(In, TEXT("confirm"), false))
		{
			Out->SetArrayField(TEXT("wouldReparent"), WillReparent);
			Fail(Out, FString::Printf(
				TEXT("removing %d virtual bone(s) also REPARENTS %d other(s) - RemoveVirtualBones ")
				TEXT("rewires any virtual bone whose source was one of these to point at that bone's ")
				TEXT("own source. See wouldReparent[]. Pass confirm:true. NOTHING was changed."),
				Names.Num(), WillReparent.Num()));
			return;
		}

		const int32 Before = Skeleton->GetVirtualBones().Num();
		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_RemoveVirtualBone",
										"Remove Virtual Bone"));
		Skeleton->Modify();
		Skeleton->RemoveVirtualBones(Names);
		const int32 After = Skeleton->GetVirtualBones().Num();
		if (After >= Before)
		{
			Fail(Out, FString::Printf(
				TEXT("RemoveVirtualBones ran and the skeleton still holds %d. NOTHING was removed."),
				After));
			return;
		}
		Skeleton->MarkPackageDirty();

		TArray<TSharedPtr<FJsonValue>> Removed;
		for (const FName& N : Names) { Removed.Add(MakeShared<FJsonValueString>(N.ToString())); }
		Out->SetStringField(TEXT("skeleton"), Skeleton->GetPathName());
		Out->SetArrayField(TEXT("removed"), Removed);
		Out->SetNumberField(TEXT("removedCount"), Before - After);
		Out->SetArrayField(TEXT("reparented"), WillReparent);
		Out->SetNumberField(TEXT("countBefore"), Before);
		Out->SetNumberField(TEXT("count"), After);
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the skeleton is dirty and NOTHING has been saved."));
	}

	// --- rename_virtual_bone ------------------------------------------------
	void H_rename_virtual_bone(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("skeleton"), TEXT("path"), TEXT("assetPath"), TEXT("name"), TEXT("newName") },
			TEXT("skeleton (aliases: path, assetPath); name - the existing virtual bone; newName"),
			{}))
		{
			return;
		}
		USkeleton* Skeleton = ResolveSkeletonForWrite(In, Out);
		if (!Skeleton) { return; }

		const FString Name = JStr(In, TEXT("name"));
		const FString NewName = JStr(In, TEXT("newName"));
		if (Name.IsEmpty() || NewName.IsEmpty())
		{
			Fail(Out, TEXT("name and newName are both required. NOTHING was changed."));
			return;
		}
		if (Name == NewName)
		{
			Fail(Out, TEXT("name and newName are the same. NOTHING was changed."));
			return;
		}
		// RenameVirtualBone IS A VOID SILENT NO-OP when nothing matches, so the original is
		// verified here or a typo would return success having done nothing.
		if (!FindVirtualBone(Skeleton, FName(*Name)))
		{
			TArray<FString> Have;
			for (const FVirtualBone& VB : Skeleton->GetVirtualBones())
			{
				Have.Add(VB.VirtualBoneName.ToString());
			}
			Fail(Out, FString::Printf(
				TEXT("no virtual bone named '%s' - RenameVirtualBone is void and does nothing quietly ")
				TEXT("when the name matches nothing, so this is checked here. This skeleton has: %s. ")
				TEXT("NOTHING was changed."), *Name,
				Have.Num() ? *FString::Join(Have, TEXT(", ")) : TEXT("(none)")));
			return;
		}
		if (FindVirtualBone(Skeleton, FName(*NewName)))
		{
			Fail(Out, FString::Printf(
				TEXT("a virtual bone named '%s' already exists. NOTHING was changed."), *NewName));
			return;
		}
		// The engine checks neither this nor the "VB " prefix, and a virtual bone sharing a REAL
		// bone's name makes every by-name lookup ambiguous.
		if (Skeleton->GetReferenceSkeleton().FindBoneIndex(FName(*NewName)) != INDEX_NONE)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is the name of a REAL bone in this skeleton. RenameVirtualBone does not ")
				TEXT("check that, and the collision would make every lookup by that name ambiguous. ")
				TEXT("NOTHING was changed."), *NewName));
			return;
		}

		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_RenameVirtualBone",
										"Rename Virtual Bone"));
		Skeleton->Modify();
		Skeleton->RenameVirtualBone(FName(*Name), FName(*NewName));

		const FVirtualBone* Now = FindVirtualBone(Skeleton, FName(*NewName));
		if (!Now)
		{
			Fail(Out, TEXT("RenameVirtualBone ran and the new name is not present on read-back. It ")
				TEXT("returns void, so this read-back is the only signal there is. NOTHING usable ")
				TEXT("was produced."));
			return;
		}
		Skeleton->MarkPackageDirty();

		Out->SetStringField(TEXT("skeleton"), Skeleton->GetPathName());
		Out->SetStringField(TEXT("name"), Name);
		Out->SetStringField(TEXT("newName"), NewName);
		Out->SetBoolField(TEXT("renamed"), true);
		Out->SetObjectField(TEXT("virtualBone"), VirtualBoneJson(*Now));
		// The rename also rewires any virtual bone that used the old name as its SOURCE
		// (Skeleton.cpp:1886-1895), which is the counterpart of removal's reparenting.
		Out->SetStringField(TEXT("note"),
			TEXT("any virtual bone that used the old name as its SOURCE was rewired to the new name "
				 "as well - RenameVirtualBone updates both fields."));
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the skeleton is dirty and NOTHING has been saved."));
	}
}
