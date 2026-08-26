// MifBridge — IK RIG and IK RETARGETER authoring.
//
// WHY THESE EXIST, since set_property can already write every field they touch. It can, and that is
// precisely the problem. Writing them by hand produces an asset that is syntactically valid and
// semantically broken, with ok:true. Proved before building this: set_property happily wrote an
// FIKRigSkeleton containing two bones with an EMPTY RefPoseGlobal, then BoneChains referring to
// "spine_01" and "spine_04" which do not exist in that two-bone skeleton, then a ChainMapping naming
// chains that exist on neither rig. Nothing objected to any of it.
//
// FRetargetDefinition::RootBone and ::BoneChains are in fact PRIVATE, with
// `friend class UIKRigController` (IKRigDefinition.h:169-180). Reflection bypasses C++ access control,
// which is the only reason the hand-written path worked at all — the engine's own design says these
// go through the controller.
//
// So the value here is not reach, it is CORRECTNESS AND VERIFICATION: derived state built by the
// engine rather than typed by a caller, and a read that says whether the result would actually work.
//
// PORTABILITY. IK Rig is a UE5 plugin and does not exist in UE4, which this bridge is also run
// against. Build.cs detects it and defines MIF_WITH_IKRIG; the .uplugin reference is marked
// "Optional" so a missing plugin is a logged skip rather than a refusal to load MifBridge at all
// (PluginManager.cpp:2164). The endpoints stay REGISTERED either way — a missing endpoint tells a
// caller nothing, while a refusal naming the reason tells them everything, and it keeps the three-way
// MIF_DECL/MIF_BIND/@mcp.tool parity true on every engine.
//
// Two modules, not interchangeable: UIKRigDefinition/UIKRetargeter/FBoneChain are /Script/IKRig
// (Runtime); UIKRigController/UIKRetargeterController, where all authoring lives, are
// /Script/IKRigEditor (Editor). Both controllers are CLASS-level IKRIGEDITOR_API, so unlike
// AInstancedFoliageActor there is no per-member export trap here — checked before writing this.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#if MIF_WITH_IKRIG
#include "Rig/IKRigDefinition.h"          // UIKRigDefinition, FBoneChain, FRetargetDefinition
#include "Rig/IKRigSkeleton.h"            // FIKRigSkeleton
#include "RigEditor/IKRigController.h"    // UIKRigController - all IK Rig authoring
#include "Engine/SkeletalMesh.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_IKRIG
	namespace
	{
		/** One message for every IK endpoint on an engine without the plugin. Says which engine it is
		 *  and what to do, rather than leaving a caller to guess why a registered endpoint refuses. */
		void IKRigUnavailable(const TSharedRef<FJsonObject>& Out, const TCHAR* What)
		{
			Fail(Out, FString::Printf(
				TEXT("%s is unavailable: this MifBridge was built against an engine with no IK Rig "
					 "plugin. IK Rig is UE5-only, so on UE4 there is nothing to author. The endpoint is "
					 "still registered so that this answer is possible at all - rebuild against an "
					 "engine that has Engine/Plugins/Animation/IKRig to enable it."), What));
		}
	}
#endif

	// --- list_ik_rig ---------------------------------------------------------
	//   in:  { path }
	//   out: { previewMesh, boneCount, retargetRoot, chains[], valid, problems[] }
	//
	// A read that CHECKS rather than echoes. Reporting the fields back verbatim would have called the
	// deliberately-broken asset described in the file header perfectly healthy.
	void H_list_ik_rig(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig") },
			TEXT("path (aliases: assetPath, rig) of an IKRigDefinition asset"),
			{ { TEXT("retargeter"), TEXT("an IKRetargeter is a different asset - read it with list_retarget_chain_mapping") },
			  { TEXT("mesh"), TEXT("the mesh is reported, not selected; set it with set_ik_rig_mesh") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("list_ik_rig"));
#else
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("rig") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - an IKRigDefinition asset "
						   "(find_assets with class=IKRigDefinition lists them)."));
			return;
		}
		UIKRigDefinition* Rig = LoadObject<UIKRigDefinition>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Rig)
		{
			UObject* Any = LoadObject<UObject>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
			Fail(Out, Any
				? FString::Printf(TEXT("%s is a %s, not an IKRigDefinition."), *Path, *Any->GetClass()->GetName())
				: FString::Printf(TEXT("no asset at %s"), *Path));
			return;
		}

		const FIKRigSkeleton& Skel = Rig->GetSkeleton();
		const TArray<FBoneChain>& Chains = Rig->GetRetargetChains();
		const FName Root = Rig->GetRetargetRoot();

		// Bone lookup once, so every check below is a set membership rather than a scan per chain.
		TSet<FName> Bones(Skel.BoneNames);
		TMap<FName, int32> BoneIndex;
		for (int32 i = 0; i < Skel.BoneNames.Num(); ++i) { BoneIndex.Add(Skel.BoneNames[i], i); }

		TArray<TSharedPtr<FJsonValue>> Problems;
		const auto AddProblem = [&Problems](const FString& Msg)
		{
			Problems.Add(MakeShared<FJsonValueString>(Msg));
		};

		// --- the skeleton itself -------------------------------------------------------------
		// Parallel arrays that have drifted are the signature of a hand-written FIKRigSkeleton; a
		// SetSkeletalMesh-built one cannot be ragged.
		if (Skel.BoneNames.Num() == 0)
		{
			AddProblem(TEXT("the rig has NO skeleton: no mesh has been assigned. "
							"Call set_ik_rig_mesh before anything else."));
		}
		else if (Skel.ParentIndices.Num() != Skel.BoneNames.Num())
		{
			AddProblem(FString::Printf(
				TEXT("skeleton is inconsistent: %d bone names but %d parent indices. These are parallel "
					 "arrays and a correctly built rig cannot have them differ - this rig was written "
					 "field-by-field rather than through set_ik_rig_mesh."),
				Skel.BoneNames.Num(), Skel.ParentIndices.Num()));
		}
		if (Skel.BoneNames.Num() > 0 && Skel.RefPoseGlobal.Num() != Skel.BoneNames.Num())
		{
			// The solver needs the reference pose. A hand-assigned skeleton has none, and everything
			// still reads back fine until it is actually used.
			AddProblem(FString::Printf(
				TEXT("the reference pose is missing or the wrong length (%d transforms for %d bones). "
					 "Nothing that solves or retargets through this rig will work. Re-assign the mesh "
					 "with set_ik_rig_mesh, which builds it."),
				Skel.RefPoseGlobal.Num(), Skel.BoneNames.Num()));
		}

		// --- the retarget root ---------------------------------------------------------------
		if (Root.IsNone())
		{
			AddProblem(TEXT("no retarget root is set. Retargeting needs one - it is the bone the whole "
							"pose is anchored to, usually 'pelvis'."));
		}
		else if (Skel.BoneNames.Num() > 0 && !Bones.Contains(Root))
		{
			AddProblem(FString::Printf(
				TEXT("the retarget root '%s' is not a bone in this rig's skeleton."), *Root.ToString()));
		}

		// --- the chains ----------------------------------------------------------------------
		TSet<FName> SeenChains;
		TArray<TSharedPtr<FJsonValue>> ChainJson;
		for (const FBoneChain& C : Chains)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), C.ChainName.ToString());
			J->SetStringField(TEXT("startBone"), C.StartBone.BoneName.ToString());
			J->SetStringField(TEXT("endBone"), C.EndBone.BoneName.ToString());
			J->SetStringField(TEXT("goal"), C.IKGoalName.ToString());

			TArray<FString> ChainProblems;
			if (C.ChainName.IsNone())
			{
				ChainProblems.Add(TEXT("the chain has no name"));
			}
			else if (SeenChains.Contains(C.ChainName))
			{
				// Two chains answering to one name makes any mapping ambiguous.
				ChainProblems.Add(FString::Printf(
					TEXT("duplicate chain name '%s' - a mapping naming it cannot say which is meant"),
					*C.ChainName.ToString()));
			}
			SeenChains.Add(C.ChainName);

			if (Skel.BoneNames.Num() > 0)
			{
				const bool bStart = Bones.Contains(C.StartBone.BoneName);
				const bool bEnd = Bones.Contains(C.EndBone.BoneName);
				if (!bStart)
				{
					ChainProblems.Add(FString::Printf(TEXT("start bone '%s' is not in this skeleton"),
						*C.StartBone.BoneName.ToString()));
				}
				if (!bEnd)
				{
					ChainProblems.Add(FString::Printf(TEXT("end bone '%s' is not in this skeleton"),
						*C.EndBone.BoneName.ToString()));
				}
				// THE check that matters, and the one nothing else performs: a chain is a path DOWN the
				// hierarchy. If the end bone is not a descendant of the start bone there is no chain
				// between them, however plausible the two names look side by side.
				if (bStart && bEnd && C.StartBone.BoneName != C.EndBone.BoneName)
				{
					int32 Walk = BoneIndex[C.EndBone.BoneName];
					const int32 Target = BoneIndex[C.StartBone.BoneName];
					bool bDescends = false;
					int32 Guard = 0;
					while (Skel.ParentIndices.IsValidIndex(Walk) && Guard++ <= Skel.BoneNames.Num())
					{
						Walk = Skel.ParentIndices[Walk];
						if (Walk == Target) { bDescends = true; break; }
						if (Walk == INDEX_NONE) { break; }
					}
					if (!bDescends)
					{
						ChainProblems.Add(FString::Printf(
							TEXT("'%s' is not a descendant of '%s', so these two bones do not form a "
								 "chain"), *C.EndBone.BoneName.ToString(), *C.StartBone.BoneName.ToString()));
					}
				}
			}
			J->SetBoolField(TEXT("valid"), ChainProblems.Num() == 0);
			if (ChainProblems.Num() > 0)
			{
				J->SetStringField(TEXT("problem"), FString::Join(ChainProblems, TEXT("; ")));
				AddProblem(FString::Printf(TEXT("chain '%s': %s"),
					*C.ChainName.ToString(), *FString::Join(ChainProblems, TEXT("; "))));
			}
			ChainJson.Add(MakeShared<FJsonValueObject>(J));
		}
		if (Chains.Num() == 0)
		{
			AddProblem(TEXT("the rig has no retarget chains, so there is nothing for a retargeter to "
							"map. Add them with add_ik_retarget_chain."));
		}

		Out->SetStringField(TEXT("path"), Rig->GetPathName());
		Out->SetStringField(TEXT("previewMesh"), Rig->PreviewSkeletalMesh.ToString());
		Out->SetNumberField(TEXT("boneCount"), Skel.BoneNames.Num());
		Out->SetNumberField(TEXT("refPoseCount"), Skel.RefPoseGlobal.Num());
		Out->SetStringField(TEXT("retargetRoot"), Root.ToString());
		Out->SetNumberField(TEXT("chainCount"), Chains.Num());
		Out->SetArrayField(TEXT("chains"), ChainJson);
		// The verdict is the point. An echo of the fields would have called the deliberately-broken
		// asset in the file header perfectly healthy.
		Out->SetBoolField(TEXT("valid"), Problems.Num() == 0);
		Out->SetArrayField(TEXT("problems"), Problems);
		if (Problems.Num() > 0)
		{
			Out->SetStringField(TEXT("validNote"),
				TEXT("this rig would not retarget correctly as it stands. Every problem above is one a "
					 "field-by-field write can produce while reporting success."));
		}
		UE_LOG(LogMifBridge, Log, TEXT("list_ik_rig: %s - %d bones, %d chains, %d problems"),
			*Rig->GetName(), Skel.BoneNames.Num(), Chains.Num(), Problems.Num());
#endif
	}
}
