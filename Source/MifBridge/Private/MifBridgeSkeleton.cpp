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
}
