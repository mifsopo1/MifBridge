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
#include "Rig/Solvers/IKRigSolver.h"     // UIKRigSolver - null entries are a crash vector, see IKNullEntryReason
#include "UObject/UObjectIterator.h"    // enumerating UIKRigSolver subclasses for list_ik_solver_types
#include "Rig/IKRigSkeleton.h"            // FIKRigSkeleton
#include "RigEditor/IKRigController.h"    // UIKRigController - all IK Rig authoring
#include "Retargeter/IKRetargeter.h"      // UIKRetargeter, URetargetChainSettings, ERetargetSourceOrTarget
#include "RetargetEditor/IKRetargeterController.h"   // UIKRetargeterController
#include "Rig/IKRigProcessor.h"          // the runtime IK Rig - the engine's own verdict on validity
#include "Retargeter/IKRetargetProcessor.h"  // the runtime retargeter
#include "IKRigLogger.h"                   // FIKRigLogger - why initialisation failed, in words
#include "UObject/StrongObjectPtr.h"       // processors are held only by our local pointer
#include "Engine/SkeletalMesh.h"
#include "UObject/Package.h"                // MarkPackageDirty
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

#if MIF_WITH_IKRIG
	namespace
	{
		/** The convention in this codebase is Package->MarkPackageDirty() at the call site (see
		 *  MifBridgeUserTypes.cpp:299). Wrapped here only because eight handlers need it and a null
		 *  package would otherwise be eight null checks. */
		void IKMarkDirty(UObject* Asset)
		{
			if (Asset)
			{
				if (UPackage* Pkg = Asset->GetOutermost()) { Pkg->MarkPackageDirty(); }
			}
		}

		/** Rig + its controller, or a populated failure. Prefixed because this module builds as a
		 *  unity blob and a colliding short helper name is the C2084 that PM-005 records. */
		UIKRigController* IKResolveRig(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
			UIKRigDefinition*& OutRig)
		{
			const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("rig") });
			if (Path.IsEmpty())
			{
				Fail(Out, TEXT("path is required - an IKRigDefinition asset. NOTHING was changed."));
				return nullptr;
			}
			OutRig = LoadObject<UIKRigDefinition>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!OutRig)
			{
				UObject* Any = LoadObject<UObject>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
				Fail(Out, Any
					? FString::Printf(TEXT("%s is a %s, not an IKRigDefinition. NOTHING was changed."),
						*Path, *Any->GetClass()->GetName())
					: FString::Printf(TEXT("no asset at %s. NOTHING was changed."), *Path));
				return nullptr;
			}
			UIKRigController* C = UIKRigController::GetController(OutRig);
			if (!C)
			{
				Fail(Out, TEXT("could not get a controller for this IK Rig. NOTHING was changed."));
				return nullptr;
			}
			return C;
		}

		/** Retargeter + its controller, or a populated failure. */
		UIKRetargeterController* IKResolveRetargeter(const TSharedRef<FJsonObject>& In,
			const TSharedRef<FJsonObject>& Out, UIKRetargeter*& OutAsset)
		{
			const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("retargeter") });
			if (Path.IsEmpty())
			{
				Fail(Out, TEXT("path is required - an IKRetargeter asset. NOTHING was changed."));
				return nullptr;
			}
			OutAsset = LoadObject<UIKRetargeter>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!OutAsset)
			{
				UObject* Any = LoadObject<UObject>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
				Fail(Out, Any
					? FString::Printf(TEXT("%s is a %s, not an IKRetargeter. NOTHING was changed."),
						*Path, *Any->GetClass()->GetName())
					: FString::Printf(TEXT("no asset at %s. NOTHING was changed."), *Path));
				return nullptr;
			}
			UIKRetargeterController* C = UIKRetargeterController::GetController(OutAsset);
			if (!C)
			{
				Fail(Out, TEXT("could not get a controller for this retargeter. NOTHING was changed."));
				return nullptr;
			}
			return C;
		}

		/** Does EndBone sit BELOW StartBone in the hierarchy? AddRetargetChain checks only that both
		 *  bones exist (IKRigController.cpp:183-193), so without this a pair of plausible names that
		 *  are not in a parent-child line is stored as a chain that spans nothing. */
		bool IKEndDescendsFromStart(const FIKRigSkeleton& Skel, FName Start, FName End, FString& OutWhy)
		{
			if (Start == End)
			{
				return true;   // a single-bone chain is legitimate
			}
			const int32 StartIdx = Skel.BoneNames.IndexOfByKey(Start);
			int32 Walk = Skel.BoneNames.IndexOfByKey(End);
			if (StartIdx == INDEX_NONE || Walk == INDEX_NONE)
			{
				OutWhy = TEXT("one of the bones is not in the skeleton");
				return false;
			}
			int32 Guard = 0;
			while (Skel.ParentIndices.IsValidIndex(Walk) && Guard++ <= Skel.BoneNames.Num())
			{
				Walk = Skel.ParentIndices[Walk];
				if (Walk == StartIdx) { return true; }
				if (Walk == INDEX_NONE) { break; }
			}
			OutWhy = FString::Printf(
				TEXT("'%s' is not a descendant of '%s'. A retarget chain is a path DOWN the hierarchy, "
					 "so these two bones do not span one - check the order, or that they are on the same "
					 "limb"), *End.ToString(), *Start.ToString());
			return false;
		}

		/** Chain names off a rig directly. UIKRetargeterController::GetChainNames looks public in the
		 *  header but sits below a `private:` (IKRetargeterController.h:167), so it is not callable
		 *  from here - and the rig's own GetRetargetChains() is public and says the same thing. */
		void IKChainNames(const UIKRigDefinition* Rig, TArray<FName>& Out)
		{
			Out.Reset();
			if (!Rig) { return; }
			for (const FBoneChain& Ch : Rig->GetRetargetChains()) { Out.Add(Ch.ChainName); }
		}

		/** Null entries in the rig's Solvers or Goals arrays, which the engine dereferences WITHOUT
		 *  checking. UIKRigProcessor::IsIKRigCompatibleWithSkeleton does Solver->GetRootBone() at
		 *  IKRigProcessor.cpp:193 and Goal->BoneName at :205 with no guard, and that function is the
		 *  first thing SetSkeletalMesh calls (IKRigController.cpp:571, 597-601) as well as being on
		 *  Initialize's path (:50). A null there is an access violation inside a handler, i.e. a dead
		 *  editor rather than an ok:false.
		 *
		 *  A rig referencing a solver class from a plugin that is not enabled on THIS machine loads
		 *  with exactly such a null, so this is a real state, not a contrived one. Returns an empty
		 *  string when the rig is safe to hand to the engine. */
		FString IKNullEntryReason(const UIKRigDefinition* Rig)
		{
			if (!Rig) { return TEXT("the rig is null"); }
			int32 NullSolvers = 0, NullGoals = 0;
			for (const UIKRigSolver* Solver : Rig->GetSolverArray()) { if (!Solver) { ++NullSolvers; } }
			for (const UIKRigEffectorGoal* Goal : Rig->GetGoalArray()) { if (!Goal) { ++NullGoals; } }
			if (NullSolvers == 0 && NullGoals == 0) { return FString(); }
			return FString::Printf(
				TEXT("this rig holds %d null solver(s) and %d null goal(s). The engine dereferences both "
					 "arrays without checking (IKRigProcessor.cpp:193 and :205), so handing it to the "
					 "engine would CRASH THE EDITOR rather than return an error. The usual cause is a "
					 "solver class from a plugin that is not enabled in this project - check the output "
					 "log for load warnings on this asset."),
				NullSolvers, NullGoals);
		}

		/** Resolves a solver class from a name, accepting either the bare class name or a full
		 *  /Script/ path. Returns null and fills OutError otherwise. */
		UClass* IKResolveSolverClass(const FString& Name, FString& OutError)
		{
			UClass* Found = nullptr;
			for (TObjectIterator<UClass> It; It; ++It)
			{
				UClass* C = *It;
				if (!C->IsChildOf(UIKRigSolver::StaticClass()) || C == UIKRigSolver::StaticClass()) { continue; }
				if (C->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated | CLASS_NewerVersionExists)) { continue; }
				if (C->GetName() == Name || C->GetPathName() == Name)
				{
					Found = C;
					break;
				}
			}
			if (!Found)
			{
				OutError = FString::Printf(
					TEXT("no IK Rig solver class called '%s'. list_ik_solver_types shows the ones this "
						 "engine has - note the names are not guessable (the full-body solver is "
						 "'IKRigFBIKSolver', not 'IKRig_FBIKSolver')."), *Name);
			}
			return Found;
		}

		/** Goals and solvers of a rig, for the read endpoints.
		 *
		 *  Solvers are reported by CLASS NAME, never through GetSolverUniqueName: that has
		 *  checkNoEntry() on a bad index (IKRigController.cpp:861) and calls GetNiceName(), whose base
		 *  is also checkNoEntry() (IKRigSolver.h:63). A custom solver class that does not override it
		 *  would kill the editor for the sake of a prettier label. */
		void IKWriteGoalsAndSolvers(const UIKRigDefinition* Rig, const UIKRigController* C,
			const TSharedRef<FJsonObject>& Out)
		{
			TArray<TSharedPtr<FJsonValue>> Solvers, Goals;
			const TArray<UIKRigSolver*>& SolverArray = Rig->GetSolverArray();
			for (int32 i = 0; i < SolverArray.Num(); ++i)
			{
				const UIKRigSolver* Solver = SolverArray[i];
				if (!Solver) { continue; }   // null entries are reported as a problem elsewhere
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetNumberField(TEXT("index"), i);
				J->SetStringField(TEXT("solverClass"), Solver->GetClass()->GetName());
				J->SetBoolField(TEXT("enabled"), Solver->IsEnabled());
				J->SetStringField(TEXT("rootBone"), Solver->GetRootBone().ToString());
				J->SetStringField(TEXT("endBone"), Solver->GetEndBone().ToString());
				Solvers.Add(MakeShared<FJsonValueObject>(J));
			}
			for (const UIKRigEffectorGoal* Goal : Rig->GetGoalArray())
			{
				if (!Goal) { continue; }
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("name"), Goal->GoalName.ToString());
				J->SetStringField(TEXT("bone"), Goal->BoneName.ToString());
				J->SetNumberField(TEXT("positionAlpha"), Goal->PositionAlpha);
				J->SetNumberField(TEXT("rotationAlpha"), Goal->RotationAlpha);
				// A goal wired to no solver does NOTHING, and the engine treats that as a warning at
				// most - it still initialises. Saying which solvers a goal reaches is the only way a
				// caller finds out.
				TArray<TSharedPtr<FJsonValue>> Connected;
				if (C)
				{
					for (int32 i = 0; i < SolverArray.Num(); ++i)
					{
						if (C->IsGoalConnectedToSolver(Goal->GoalName, i))
						{
							Connected.Add(MakeShared<FJsonValueNumber>(i));
						}
					}
				}
				J->SetArrayField(TEXT("connectedSolvers"), Connected);
				J->SetBoolField(TEXT("connected"), Connected.Num() > 0);
				Goals.Add(MakeShared<FJsonValueObject>(J));
			}
			Out->SetArrayField(TEXT("solvers"), Solvers);
			Out->SetArrayField(TEXT("goals"), Goals);
			Out->SetNumberField(TEXT("solverCount"), Solvers.Num());
			Out->SetNumberField(TEXT("goalCount"), Goals.Num());
		}

		/** Copies an FIKRigLogger's errors and warnings into the response. The engine reports WHY
		 *  initialisation failed only through this; the return value is a bare bool. Engine precedent
		 *  for reading it: AnimGraphNode_RetargetPoseFromMesh.cpp:95-112 republishes the same two
		 *  arrays into the compiler results log. */
		void IKCopyLog(const FIKRigLogger& Log, const TSharedRef<FJsonObject>& Out)
		{
			TArray<TSharedPtr<FJsonValue>> Errors, Warnings;
			for (const FText& T : Log.GetErrors())   { Errors.Add(MakeShared<FJsonValueString>(T.ToString())); }
			for (const FText& T : Log.GetWarnings()) { Warnings.Add(MakeShared<FJsonValueString>(T.ToString())); }
			Out->SetArrayField(TEXT("runtimeErrors"), Errors);
			Out->SetArrayField(TEXT("runtimeWarnings"), Warnings);
		}

		/** The live chain mapping, target-chain-keyed. ChainSettings, NOT the ChainMapping property -
		 *  FRetargetChainMap has been deprecated since 5.1 (IKRetargeter.h:18) and a write to it is
		 *  read by nothing. */
		void IKWriteMapping(const UIKRetargeter* Asset, const TSharedRef<FJsonObject>& Out)
		{
			TArray<TSharedPtr<FJsonValue>> Rows, Unmapped;
			for (const TObjectPtr<URetargetChainSettings>& CS : Asset->GetAllChainSettings())
			{
				if (!CS) { continue; }
				TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
				R->SetStringField(TEXT("targetChain"), CS->TargetChain.ToString());
				R->SetStringField(TEXT("sourceChain"), CS->SourceChain.ToString());
				const bool bMapped = !CS->SourceChain.IsNone();
				R->SetBoolField(TEXT("mapped"), bMapped);
				if (!bMapped)
				{
					Unmapped.Add(MakeShared<FJsonValueString>(CS->TargetChain.ToString()));
				}
				Rows.Add(MakeShared<FJsonValueObject>(R));
			}
			Out->SetArrayField(TEXT("mapping"), Rows);
			Out->SetNumberField(TEXT("chainCount"), Rows.Num());
			Out->SetNumberField(TEXT("unmappedCount"), Unmapped.Num());
			// An unmapped target chain is not an error but it IS a silent no-op at runtime: that part
			// of the body simply will not be retargeted, and nothing says so unless it is said here.
			if (Unmapped.Num() > 0)
			{
				Out->SetArrayField(TEXT("unmapped"), Unmapped);
				Out->SetStringField(TEXT("unmappedNote"),
					TEXT("these TARGET chains have no source chain mapped to them. That is not an error, "
						 "but at runtime those parts of the body are simply not retargeted - map them "
						 "with set_retarget_chain_mapping, or re-run auto_map_retarget_chains with "
						 "mode=fuzzy."));
			}
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

		// Counted as a structural problem so it is BOTH reported and, because the engine probe below
		// runs only when there are none, prevents that probe from crashing on the same dereference.
		{
			const FString NullReason = IKNullEntryReason(Rig);
			if (!NullReason.IsEmpty()) { AddProblem(NullReason); }
		}

		// --- what is this rig FOR? -----------------------------------------------------------
		// A rig has two independent halves and needs only the one it is used for. Retargeting wants a
		// root and chains; IK solving wants solvers and goals. Demanding both called a perfectly good
		// IK-only rig invalid - and, worse, that verdict gated the engine probe below, so the one
		// answer that would have settled it never ran.
		const int32 NumSolvers = Rig->GetSolverArray().Num();
		const bool bRetargeting = Chains.Num() > 0 || !Root.IsNone();
		const bool bSolving = NumSolvers > 0 || Rig->GetGoalArray().Num() > 0;
		Out->SetStringField(TEXT("purpose"),
			bRetargeting && bSolving ? TEXT("retargeting and IK")
			: bRetargeting           ? TEXT("retargeting")
			: bSolving               ? TEXT("IK")
									 : TEXT("nothing yet"));

		// --- the retarget root ---------------------------------------------------------------
		// Only asked for when the rig is set up to retarget at all.
		if (bRetargeting && Root.IsNone())
		{
			AddProblem(TEXT("this rig has retarget chains but no retarget root. Retargeting needs one - "
							"it is the bone the whole pose is anchored to, usually 'pelvis'."));
		}
		else if (!Root.IsNone() && Skel.BoneNames.Num() > 0 && !Bones.Contains(Root))
		{
			// The !IsNone guard is not redundant. Once the branch above became conditional on the rig
			// actually retargeting, an unset root fell through to here and was reported as "the
			// retarget root 'None' is not a bone" - which is true and useless, and it marked every
			// IK-only rig invalid.
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
		// Only a rig that does NEITHER is broken. A retargeting rig with no solvers is normal, and so
		// is an IK rig with no chains.
		if (!bRetargeting && !bSolving)
		{
			AddProblem(TEXT("this rig does nothing: it has no retarget chains or root (so it cannot "
							"retarget) and no solvers or goals (so it cannot solve IK). Add chains with "
							"add_ik_retarget_chain, or a solver with add_ik_solver."));
		}
		else if (bRetargeting && Chains.Num() == 0)
		{
			AddProblem(TEXT("this rig has a retarget root but no chains, so a retargeter would have "
							"nothing to map. Add them with add_ik_retarget_chain."));
		}

		Out->SetStringField(TEXT("path"), Rig->GetPathName());
		Out->SetStringField(TEXT("previewMesh"), Rig->PreviewSkeletalMesh.ToString());
		Out->SetNumberField(TEXT("boneCount"), Skel.BoneNames.Num());
		Out->SetNumberField(TEXT("refPoseCount"), Skel.RefPoseGlobal.Num());
		Out->SetStringField(TEXT("retargetRoot"), Root.ToString());
		Out->SetNumberField(TEXT("chainCount"), Chains.Num());
		Out->SetArrayField(TEXT("chains"), ChainJson);
		// Goals and solvers are the IK half of the rig; chains above are the retargeting half. A rig
		// can legitimately have either, both or neither, so no absence here is reported as a problem.
		IKWriteGoalsAndSolvers(Rig, UIKRigController::GetController(Rig), Out);
		{
			// A goal connected to no solver is inert, and the engine only warns about it - so it never
			// reaches runtimeErrors and would otherwise be invisible.
			TArray<FString> Orphans;
			for (const UIKRigEffectorGoal* Goal : Rig->GetGoalArray())
			{
				if (!Goal) { continue; }
				if (UIKRigController* GC = UIKRigController::GetController(Rig))
				{
					if (!GC->IsGoalConnectedToAnySolver(Goal->GoalName))
					{
						Orphans.Add(Goal->GoalName.ToString());
					}
				}
			}
			if (Orphans.Num() > 0)
			{
				Out->SetStringField(TEXT("goalNote"), FString::Printf(
					TEXT("goal(s) %s are connected to NO solver, so they do nothing. That is not an "
						 "error and the rig still initialises - the engine only warns - but it is "
						 "almost always an oversight. Connect them with "
						 "set_ik_goal_solver_connection."), *FString::Join(Orphans, TEXT(", "))));
			}
		}
		// The verdict is the point. An echo of the fields would have called the deliberately-broken
		// asset in the file header perfectly healthy.
		Out->SetBoolField(TEXT("valid"), Problems.Num() == 0);
		Out->SetArrayField(TEXT("problems"), Problems);

		// THE ENGINE'S OWN VERDICT, which is worth more than every check above put together:
		// UIKRigProcessor::IsInitialized() is true only if the very last line of Initialize was
		// reached, past every validation branch (IKRigProcessor.cpp:19-181).
		//
		// Gated on the structural checks passing, and that gate is not caution for its own sake:
		// IsIKRigCompatibleWithSkeleton asserts check(InputBoneIndex != INDEX_NONE && AssetBoneIndex
		// != INDEX_NONE) at IKRigProcessor.cpp:240, and AssetBoneIndex comes from the rig's own
		// FIKRigSkeleton - which set_property will write inconsistently with the rig's goals. A
		// check() in a handler terminates the editor. A rig already known to be broken teaches us
		// nothing here and risks everything.
		USkeletalMesh* PreviewMesh = Rig->PreviewSkeletalMesh.LoadSynchronous();
		if (Problems.Num() > 0)
		{
			Out->SetStringField(TEXT("runtimeNote"),
				TEXT("the engine's own initialisation check was NOT run, because the problems above mean "
					 "this rig is already known to be invalid - and handing a structurally inconsistent "
					 "rig to the engine can hit an assert that terminates the editor rather than "
					 "returning an error. Fix the problems and read this again."));
		}
		else if (!PreviewMesh)
		{
			Out->SetStringField(TEXT("runtimeNote"),
				TEXT("the engine's own initialisation check was NOT run: this rig has no preview mesh to "
					 "initialise against. Assign one with set_ik_rig_mesh."));
		}
		else
		{
			// Held in a TStrongObjectPtr because the processor is a UObject referenced by nothing else.
			TStrongObjectPtr<UIKRigProcessor> Proc(NewObject<UIKRigProcessor>(GetTransientPackage()));
			Proc->Initialize(Rig, PreviewMesh);
			const bool bInit = Proc->IsInitialized();
			Out->SetBoolField(TEXT("runtimeInitialized"), bInit);
			IKCopyLog(Proc->Log, Out);
			Out->SetStringField(TEXT("runtimeNote"), bInit
				? TEXT("the engine initialised this rig successfully against its preview mesh, so it "
					   "would run. Any runtimeWarnings above are real but not fatal - note that a goal "
					   "connected to NO solver is only a warning and still initialises.")
				: TEXT("the engine REFUSED to initialise this rig against its preview mesh, so it would "
					   "not run whatever the structural checks say. runtimeErrors above is the engine's "
					   "own explanation."));
			if (!bInit)
			{
				// The structural verdict must not disagree with the engine's.
				Out->SetBoolField(TEXT("valid"), false);
			}
		}
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
	// --- set_ik_rig_mesh -----------------------------------------------------
	//   in:  { path, mesh }
	//   out: { boneCount, refPoseCount, previewMesh }
	//
	// The one call that BUILDS the rig's skeleton rather than storing it. SetSkeletalMesh copies the
	// hierarchy and the reference pose out of the mesh (IKRigController.cpp:585-590); assigning
	// PreviewSkeletalMesh with set_property leaves the skeleton empty and every later call blind.
	void H_set_ik_rig_mesh(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("mesh"), TEXT("skeletalMesh") },
			TEXT("path (aliases: assetPath, rig) of an IKRigDefinition, mesh (alias: skeletalMesh)"),
			{ { TEXT("skeleton"), TEXT("an IK Rig is built from a SKELETAL MESH, not a Skeleton asset - pass the mesh") },
			  { TEXT("previewMesh"), TEXT("the parameter is 'mesh'; it becomes the preview mesh AND builds the rig's skeleton") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("set_ik_rig_mesh"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		const FString MeshPath = JStrAny(In, { TEXT("mesh"), TEXT("skeletalMesh") });
		if (MeshPath.IsEmpty())
		{
			Fail(Out, TEXT("mesh is required - a SkeletalMesh asset. NOTHING was changed."));
			return;
		}
		USkeletalMesh* Mesh = LoadObject<USkeletalMesh>(nullptr, *MeshPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Mesh)
		{
			UObject* Any = LoadObject<UObject>(nullptr, *MeshPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
			Fail(Out, Any
				? FString::Printf(
					TEXT("%s is a %s, not a SkeletalMesh. An IK Rig is built from a MESH, not from a "
						 "Skeleton asset. NOTHING was changed."), *MeshPath, *Any->GetClass()->GetName())
				: FString::Printf(TEXT("no asset at %s. NOTHING was changed."), *MeshPath));
			return;
		}

		// BEFORE the engine is touched. SetSkeletalMesh's very first act is a compatibility check that
		// dereferences every solver and goal without a null check, so this cannot be done afterwards
		// and cannot be reported as a failure - it would be a dead editor.
		const FString NullReason = IKNullEntryReason(Rig);
		if (!NullReason.IsEmpty())
		{
			Fail(Out, FString::Printf(TEXT("%s NOTHING was changed."), *NullReason));
			return;
		}

		if (!C->SetSkeletalMesh(Mesh, /*bTransact=*/true))
		{
			// The engine rejects a mesh missing bones the rig's existing goals or solvers need, and
			// writes the detail to the log rather than returning it - so say where to look.
			Fail(Out, FString::Printf(
				TEXT("the IK Rig refused this mesh: it is missing bones that this rig's existing goals "
					 "or solvers require. NOTHING was changed. The engine writes which bones to the "
					 "output log (LogIKRigEditor, Warning) and does not return them, so read the log - "
					 "or assign the mesh to a fresh rig, which has no requirements yet.")));
			return;
		}

		// Read back through the asset rather than trusting the return: an empty skeleton after a
		// successful call would mean the mesh had no bones, which is worth catching here.
		const FIKRigSkeleton& Skel = Rig->GetSkeleton();
		if (Skel.BoneNames.Num() == 0)
		{
			Fail(Out, TEXT("the mesh was accepted but the rig's skeleton is still empty, so the mesh has "
						   "no bones. Read it back with list_ik_rig before doing anything else."));
			return;
		}
		IKMarkDirty(Rig);
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetStringField(TEXT("mesh"), Mesh->GetPathName());
		Out->SetNumberField(TEXT("boneCount"), Skel.BoneNames.Num());
		Out->SetNumberField(TEXT("refPoseCount"), Skel.RefPoseGlobal.Num());
		Out->SetStringField(TEXT("note"),
			TEXT("this built the rig's skeleton from the mesh - bone names, parent indices and the "
				 "reference pose - as well as setting the preview mesh. Set the retarget root next "
				 "with set_ik_rig_retarget_root, then add chains."));
		UE_LOG(LogMifBridge, Log, TEXT("set_ik_rig_mesh: %s <- %s (%d bones)"),
			*Rig->GetName(), *Mesh->GetName(), Skel.BoneNames.Num());
#endif
	}

	// --- set_ik_rig_retarget_root --------------------------------------------
	//   in:  { path, bone }
	//   out: { retargetRoot }
	//
	// Guarded because the engine call SILENTLY CLEARS: given a bone that is not in the skeleton,
	// SetRetargetRoot sets the root to None and returns TRUE (IKRigController.cpp:391-403).
	void H_set_ik_rig_retarget_root(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("bone"), TEXT("boneName"), TEXT("root") },
			TEXT("path (aliases: assetPath, rig) of an IKRigDefinition, bone (aliases: boneName, root)"),
			{ { TEXT("chain"), TEXT("the retarget ROOT is a single bone, not a chain") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("set_ik_rig_retarget_root"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		const FString Bone = JStrAny(In, { TEXT("bone"), TEXT("boneName"), TEXT("root") });
		if (Bone.IsEmpty())
		{
			Fail(Out, TEXT("bone is required - usually 'pelvis'. NOTHING was changed."));
			return;
		}
		const FIKRigSkeleton& Skel = Rig->GetSkeleton();
		if (Skel.BoneNames.Num() == 0)
		{
			// Without this the call below would "succeed" and set the root to None, because every
			// bone name is absent from an empty skeleton.
			Fail(Out, TEXT("this rig has no skeleton yet, so no bone name can be valid. Call "
						   "set_ik_rig_mesh first. NOTHING was changed."));
			return;
		}
		const FName BoneName(*Bone);
		if (!Skel.BoneNames.Contains(BoneName))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a bone in this rig's skeleton (%d bones; list_bones on the rig's mesh "
					 "shows them). NOTHING was changed - note that the engine would have silently set "
					 "the root to None here rather than refusing."), *Bone, Skel.BoneNames.Num()));
			return;
		}

		C->SetRetargetRoot(BoneName);
		// Read back rather than trust: this is the call whose failure mode is a silent clear.
		const FName Now = Rig->GetRetargetRoot();
		if (Now != BoneName)
		{
			Fail(Out, FString::Printf(
				TEXT("asked for retarget root '%s' but the rig reports '%s' afterwards. Read it back "
					 "with list_ik_rig before relying on this rig."), *Bone, *Now.ToString()));
			return;
		}
		IKMarkDirty(Rig);
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetStringField(TEXT("retargetRoot"), Now.ToString());
		UE_LOG(LogMifBridge, Log, TEXT("set_ik_rig_retarget_root: %s <- %s"), *Rig->GetName(), *Bone);
#endif
	}

	// --- add_ik_retarget_chain -----------------------------------------------
	//   in:  { path, name, startBone, endBone, goal? }
	//   out: { name, requestedName, renamed, startBone, endBone }
	//
	// Two engine behaviours are surfaced rather than inherited: the silent unique-rename on a name
	// collision (IKRigController.cpp:204), and the ABSENCE of any hierarchy check - the engine
	// verifies both bones exist and stops (IKRigController.cpp:183-193), so a chain whose end bone is
	// not a descendant of its start bone is stored happily and spans nothing.
	void H_add_ik_retarget_chain(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("chainName"),
			  TEXT("startBone"), TEXT("endBone"), TEXT("goal"), TEXT("goalName") },
			TEXT("path (aliases: assetPath, rig), name (alias: chainName), startBone, endBone, "
				 "goal (alias: goalName, optional)"),
			{ { TEXT("bones"), TEXT("a chain is defined by its two ENDS: startBone and endBone. The bones between them are implied by the hierarchy") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("add_ik_retarget_chain"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		const FString Name = JStrAny(In, { TEXT("name"), TEXT("chainName") });
		const FString Start = JStr(In, TEXT("startBone"));
		const FString End = JStr(In, TEXT("endBone"));
		if (Name.IsEmpty() || Start.IsEmpty() || End.IsEmpty())
		{
			// A nameless chain is allowed by the engine and becomes "DefaultChainName"; refused here
			// because a chain is only useful if a mapping can name it.
			Fail(Out, TEXT("name, startBone and endBone are all required. NOTHING was created."));
			return;
		}
		const FIKRigSkeleton& Skel = Rig->GetSkeleton();
		if (Skel.BoneNames.Num() == 0)
		{
			Fail(Out, TEXT("this rig has no skeleton yet. Call set_ik_rig_mesh first. "
						   "NOTHING was created."));
			return;
		}
		const FName StartName(*Start), EndName(*End);
		for (const TPair<const TCHAR*, FName>& Pair :
			{ TPair<const TCHAR*, FName>(TEXT("startBone"), StartName),
			  TPair<const TCHAR*, FName>(TEXT("endBone"), EndName) })
		{
			if (!Skel.BoneNames.Contains(Pair.Value))
			{
				Fail(Out, FString::Printf(
					TEXT("%s '%s' is not a bone in this rig's skeleton (%d bones). NOTHING was created."),
					Pair.Key, *Pair.Value.ToString(), Skel.BoneNames.Num()));
				return;
			}
		}
		FString Why;
		if (!IKEndDescendsFromStart(Skel, StartName, EndName, Why))
		{
			// Stricter than the editor, deliberately: there is no correct use for an inverted chain,
			// and one costs nothing to create and never announces itself afterwards.
			Fail(Out, FString::Printf(TEXT("%s. NOTHING was created."), *Why));
			return;
		}

		const FName Goal(*JStrAny(In, { TEXT("goal"), TEXT("goalName") }));
		const FName Actual = C->AddRetargetChain(FName(*Name), StartName, EndName, Goal);
		if (Actual.IsNone())
		{
			Fail(Out, TEXT("the engine refused the chain and reported nothing beyond a log line. "
						   "NOTHING was created."));
			return;
		}
		IKMarkDirty(Rig);
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetStringField(TEXT("name"), Actual.ToString());
		Out->SetStringField(TEXT("requestedName"), Name);
		Out->SetStringField(TEXT("startBone"), Start);
		Out->SetStringField(TEXT("endBone"), End);
		// The rename is reported because it is otherwise invisible, and a mapping written against the
		// name you ASKED for would then silently target nothing.
		const bool bRenamed = Actual.ToString() != Name;
		Out->SetBoolField(TEXT("renamed"), bRenamed);
		if (bRenamed)
		{
			Out->SetStringField(TEXT("renameNote"), FString::Printf(
				TEXT("a chain called '%s' already existed, so the engine created '%s' instead. Use the "
					 "returned name in any mapping - '%s' would refer to the other chain."),
				*Name, *Actual.ToString(), *Name));
		}
		UE_LOG(LogMifBridge, Log, TEXT("add_ik_retarget_chain: %s.%s [%s -> %s]"),
			*Rig->GetName(), *Actual.ToString(), *Start, *End);
#endif
	}

	// --- remove_ik_retarget_chain --------------------------------------------
	//   in:  { path, name }
	//   out: { removed, remainingChains }
	void H_remove_ik_retarget_chain(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("chainName") },
			TEXT("path (aliases: assetPath, rig), name (alias: chainName)"), {}))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("remove_ik_retarget_chain"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		const FString Name = JStrAny(In, { TEXT("name"), TEXT("chainName") });
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required. NOTHING was removed."));
			return;
		}
		if (!C->RemoveRetargetChain(FName(*Name)))
		{
			// Named rather than generic: "no such chain" and "removal failed" are different problems.
			TArray<FString> Have;
			for (const FBoneChain& Ch : Rig->GetRetargetChains()) { Have.Add(Ch.ChainName.ToString()); }
			Fail(Out, FString::Printf(
				TEXT("this rig has no chain called '%s'. It has: %s. NOTHING was removed."),
				*Name, Have.Num() ? *FString::Join(Have, TEXT(", ")) : TEXT("(none)")));
			return;
		}
		IKMarkDirty(Rig);
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetBoolField(TEXT("removed"), true);
		Out->SetStringField(TEXT("name"), Name);
		Out->SetNumberField(TEXT("remainingChains"), Rig->GetRetargetChains().Num());
		Out->SetStringField(TEXT("note"),
			TEXT("any retargeter mapping that referred to this chain now points at nothing. Re-check "
				 "with list_retarget_chain_mapping."));
#endif
	}

	// --- set_retarget_rigs ---------------------------------------------------
	//   in:  { path, source?, target? }
	//   out: { sourceRig, targetRig, mapping[], unmapped[] }
	//
	// SetIKRig IS NOT AN ASSIGNMENT. It also copies the preview mesh off the rig, calls
	// CleanChainMapping and runs AutoMapChains(Fuzzy) (IKRetargeterController.cpp:52-82). Writing
	// SourceIKRigAsset with set_property does none of that and leaves an unmapped retargeter that
	// reads back as configured.
	void H_set_retarget_rigs(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("retargeter"),
			  TEXT("source"), TEXT("sourceRig"), TEXT("target"), TEXT("targetRig") },
			TEXT("path (aliases: assetPath, retargeter) of an IKRetargeter; source (alias: sourceRig) "
				 "and/or target (alias: targetRig), each an IKRigDefinition path"),
			{ { TEXT("mesh"), TEXT("the preview meshes come from the rigs themselves - set them on the rigs with set_ik_rig_mesh") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("set_retarget_rigs"));
#else
		UIKRetargeter* Asset = nullptr;
		UIKRetargeterController* C = IKResolveRetargeter(In, Out, Asset);
		if (!C) { return; }

		const FString SrcPath = JStrAny(In, { TEXT("source"), TEXT("sourceRig") });
		const FString TgtPath = JStrAny(In, { TEXT("target"), TEXT("targetRig") });
		if (SrcPath.IsEmpty() && TgtPath.IsEmpty())
		{
			Fail(Out, TEXT("at least one of source or target is required. NOTHING was changed."));
			return;
		}
		// BOTH are resolved before EITHER is applied, so a typo in the second does not leave the
		// retargeter half-wired by the first.
		UIKRigDefinition* Src = nullptr;
		UIKRigDefinition* Tgt = nullptr;
		for (int32 i = 0; i < 2; ++i)
		{
			const FString& P = (i == 0) ? SrcPath : TgtPath;
			if (P.IsEmpty()) { continue; }
			UIKRigDefinition* R = LoadObject<UIKRigDefinition>(nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!R)
			{
				UObject* Any = LoadObject<UObject>(nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
				Fail(Out, FString::Printf(
					TEXT("%s rig: %s. NOTHING was changed - neither rig was applied."),
					i == 0 ? TEXT("source") : TEXT("target"),
					Any ? *FString::Printf(TEXT("%s is a %s, not an IKRigDefinition"), *P, *Any->GetClass()->GetName())
						: *FString::Printf(TEXT("no asset at %s"), *P)));
				return;
			}
			(i == 0 ? Src : Tgt) = R;
		}

		if (Src) { C->SetIKRig(ERetargetSourceOrTarget::Source, Src); }
		if (Tgt) { C->SetIKRig(ERetargetSourceOrTarget::Target, Tgt); }

		IKMarkDirty(Asset);
		Out->SetStringField(TEXT("retargeter"), Asset->GetPathName());
		Out->SetStringField(TEXT("sourceRig"), Asset->GetSourceIKRig() ? Asset->GetSourceIKRig()->GetPathName() : FString());
		Out->SetStringField(TEXT("targetRig"), Asset->GetTargetIKRig() ? Asset->GetTargetIKRig()->GetPathName() : FString());

		IKWriteMapping(Asset, Out);
		// Stated because it is surprising: setting a rig here already auto-mapped the chains.
		Out->SetStringField(TEXT("note"),
			TEXT("setting a rig does more than store it: the preview mesh is copied off the rig, the "
				 "chain mapping is rebuilt against the TARGET rig's chains, and chains are auto-mapped "
				 "by fuzzy name match. The mapping above is the result - re-run "
				 "auto_map_retarget_chains only if you want a different mode or a forced remap."));
		UE_LOG(LogMifBridge, Log, TEXT("set_retarget_rigs: %s"), *Asset->GetName());
#endif
	}

	// --- auto_map_retarget_chains --------------------------------------------
	//   in:  { path, mode?, force? }
	//   out: { mode, mapping[], unmapped[] }
	void H_auto_map_retarget_chains(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("retargeter"), TEXT("mode"),
			  TEXT("remapExisting"), TEXT("force") },
			TEXT("path (aliases: assetPath, retargeter), mode (exact|fuzzy|clear, default fuzzy), "
				 "remapExisting (bool, default false - also remap chains that already have a source)"),
			{ { TEXT("sourceChain"), TEXT("this maps ALL chains automatically; set one by hand with set_retarget_chain_mapping") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("auto_map_retarget_chains"));
#else
		UIKRetargeter* Asset = nullptr;
		UIKRetargeterController* C = IKResolveRetargeter(In, Out, Asset);
		if (!C) { return; }

		// The engine's whole AutoMapChains body sits inside `if (IsValid(GetTargetIKRig()))`
		// (IKRetargeterController.cpp:230), so without a target rig it does nothing AT ALL and says
		// nothing. Refuse instead.
		if (!IsValid(Asset->GetTargetIKRig()))
		{
			Fail(Out, TEXT("this retargeter has no TARGET rig, and the mapping is built from the target "
						   "rig's chains - so there is nothing to map. Set it with set_retarget_rigs "
						   "first. NOTHING was changed. (The engine would have done nothing here and "
						   "reported success.)"));
			return;
		}
		if (!IsValid(Asset->GetSourceIKRig()))
		{
			Fail(Out, TEXT("this retargeter has no SOURCE rig, so every chain would map to nothing. Set "
						   "it with set_retarget_rigs first. NOTHING was changed."));
			return;
		}

		const FString Mode = JStr(In, TEXT("mode"), TEXT("fuzzy")).ToLower();
		EAutoMapChainType Type;
		if (Mode == TEXT("fuzzy"))      { Type = EAutoMapChainType::Fuzzy; }
		else if (Mode == TEXT("exact")) { Type = EAutoMapChainType::Exact; }
		else if (Mode == TEXT("clear")) { Type = EAutoMapChainType::Clear; }
		else
		{
			Fail(Out, FString::Printf(
				TEXT("mode '%s' is not recognised. Use fuzzy (closest name by edit distance), exact "
					 "(identical names only, anything else set to none) or clear (unmap everything). "
					 "NOTHING was changed."), *Mode));
			return;
		}
		// NAMED remapExisting, NOT force. This parameter is benign - it only decides whether chains
		// that already have a source are reconsidered - but "force" is the conventional name for
		// bypassing a destructive-operation guard, and tooling strips it on sight. The audit harness
		// used to test this bridge lists "force" alongside "confirm", "save" and "overwrite" in its
		// forbidden keys, so every force:true sent here arrived as false and the endpoint quietly did
		// half of what was asked. The old name still works for anything that gets it through.
		bool bRemap = JBool(In, TEXT("remapExisting"), JBool(In, TEXT("force"), false));

		// CLEAR WITHOUT REMAP IS A GUARANTEED NO-OP, so it is corrected rather than obeyed. The engine
		// skips any chain that already has a source (IKRetargeterController.cpp:336-340) - which is
		// exactly the set a caller asking to CLEAR wants cleared. Obeying literally would leave every
		// mapped chain mapped and report success.
		bool bClearImpliedRemap = false;
		if (Type == EAutoMapChainType::Clear && !bRemap)
		{
			bRemap = true;
			bClearImpliedRemap = true;
		}
		C->AutoMapChains(Type, bRemap);

		IKMarkDirty(Asset);
		Out->SetStringField(TEXT("retargeter"), Asset->GetPathName());
		Out->SetStringField(TEXT("mode"), Mode);
		Out->SetBoolField(TEXT("remapExisting"), bRemap);
		IKWriteMapping(Asset, Out);
		if (bClearImpliedRemap)
		{
			Out->SetStringField(TEXT("clearNote"),
				TEXT("mode=clear implies remapExisting, so it was applied. Without it the engine skips "
					 "every chain that already has a source - which is precisely the set you asked to "
					 "clear - and the call would have done nothing while reporting success."));
		}
		else if (!bRemap)
		{
			// Without this, "I re-ran it and nothing changed" is a mystery.
			Out->SetStringField(TEXT("forceNote"),
				TEXT("chains that were already mapped were left alone, because force was not set - the "
					 "engine treats an existing mapping as a deliberate choice. Pass force:true to "
					 "remap everything."));
		}
		UE_LOG(LogMifBridge, Log, TEXT("auto_map_retarget_chains: %s mode=%s"), *Asset->GetName(), *Mode);
#endif
	}

	// --- set_retarget_chain_mapping ------------------------------------------
	//   in:  { path, targetChain, sourceChain }
	//   out: { mapping[], unmapped[] }
	void H_set_retarget_chain_mapping(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("retargeter"),
			  TEXT("targetChain"), TEXT("sourceChain") },
			TEXT("path (aliases: assetPath, retargeter), targetChain (the chain ON THE TARGET rig), "
				 "sourceChain (the chain on the SOURCE rig to drive it, or empty to unmap)"),
			{ { TEXT("chain"), TEXT("a mapping has two ends: targetChain and sourceChain") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("set_retarget_chain_mapping"));
#else
		UIKRetargeter* Asset = nullptr;
		UIKRetargeterController* C = IKResolveRetargeter(In, Out, Asset);
		if (!C) { return; }

		const FString Target = JStr(In, TEXT("targetChain"));
		const FString Source = JStr(In, TEXT("sourceChain"));
		if (Target.IsEmpty())
		{
			Fail(Out, TEXT("targetChain is required - the mapping is keyed by the TARGET rig's chain. "
						   "NOTHING was changed."));
			return;
		}
		// Both ends are checked against the rigs BEFORE writing, because SetSourceChain reports a bool
		// and a caller cannot tell "no such target chain" from "no such source chain" from it.
		TArray<FName> TargetNames, SourceNames;
		IKChainNames(Asset->GetTargetIKRig(), TargetNames);
		IKChainNames(Asset->GetSourceIKRig(), SourceNames);
		const auto Join = [](const TArray<FName>& N)
		{
			TArray<FString> S;
			for (const FName& X : N) { S.Add(X.ToString()); }
			return S.Num() ? FString::Join(S, TEXT(", ")) : FString(TEXT("(none)"));
		};
		if (!TargetNames.Contains(FName(*Target)))
		{
			Fail(Out, FString::Printf(
				TEXT("the TARGET rig has no chain called '%s'. It has: %s. NOTHING was changed."),
				*Target, *Join(TargetNames)));
			return;
		}
		if (!Source.IsEmpty() && !SourceNames.Contains(FName(*Source)))
		{
			Fail(Out, FString::Printf(
				TEXT("the SOURCE rig has no chain called '%s'. It has: %s. NOTHING was changed."),
				*Source, *Join(SourceNames)));
			return;
		}

		if (!C->SetSourceChain(Source.IsEmpty() ? NAME_None : FName(*Source), FName(*Target)))
		{
			Fail(Out, FString::Printf(
				TEXT("the retargeter refused to map '%s' onto target chain '%s'. NOTHING was changed."),
				*Source, *Target));
			return;
		}
		IKMarkDirty(Asset);
		Out->SetStringField(TEXT("retargeter"), Asset->GetPathName());
		Out->SetStringField(TEXT("targetChain"), Target);
		Out->SetStringField(TEXT("sourceChain"), Source);
		IKWriteMapping(Asset, Out);
#endif
	}

	// --- list_retarget_chain_mapping -----------------------------------------
	//   in:  { path }
	//   out: { sourceRig, targetRig, mapping[], unmapped[], valid, problems[] }
	//
	// Reads ChainSettings, NOT the ChainMapping property - FRetargetChainMap has been deprecated since
	// 5.1 (IKRetargeter.h:18) and a set_property write to it succeeds and is read by nothing.
	void H_list_retarget_chain_mapping(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("retargeter") },
			TEXT("path (aliases: assetPath, retargeter) of an IKRetargeter asset"),
			{ { TEXT("rig"), TEXT("an IKRigDefinition is a different asset - read it with list_ik_rig") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("list_retarget_chain_mapping"));
#else
		UIKRetargeter* Asset = nullptr;
		UIKRetargeterController* C = IKResolveRetargeter(In, Out, Asset);
		if (!C) { return; }

		// The Writeable variants: GetSourceIKRig/GetTargetIKRig return const pointers
		// (IKRetargeter.h:213-215) and everything below only reads, but keeping one type avoids a
		// const_cast further down.
		const UIKRigDefinition* Src = Asset->GetSourceIKRig();
		const UIKRigDefinition* Tgt = Asset->GetTargetIKRig();
		TArray<TSharedPtr<FJsonValue>> Problems;
		if (!IsValid(Src))
		{
			Problems.Add(MakeShared<FJsonValueString>(
				TEXT("no SOURCE rig is set - there is nothing to copy animation from. "
					 "Set it with set_retarget_rigs.")));
		}
		if (!IsValid(Tgt))
		{
			Problems.Add(MakeShared<FJsonValueString>(
				TEXT("no TARGET rig is set - there is nothing to copy animation onto, and the chain "
					 "mapping is built from the target rig's chains so it will be empty. "
					 "Set it with set_retarget_rigs.")));
		}
		if (IsValid(Src) && IsValid(Tgt) && Src == Tgt)
		{
			// Legal, and almost always a mistake worth naming.
			Problems.Add(MakeShared<FJsonValueString>(
				TEXT("the source and target rigs are the SAME asset, so this retargeter maps a skeleton "
					 "onto itself. That is legal but rarely intended.")));
		}
		// Every rig involved must itself be sound, or the mapping is names pointing at nothing.
		for (int32 i = 0; i < 2; ++i)
		{
			const UIKRigDefinition* R = (i == 0) ? Src : Tgt;
			if (!IsValid(R)) { continue; }
			if (R->GetSkeleton().BoneNames.Num() == 0)
			{
				Problems.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("the %s rig '%s' has no skeleton - call set_ik_rig_mesh on it."),
					i == 0 ? TEXT("source") : TEXT("target"), *R->GetName())));
			}
			if (R->GetRetargetChains().Num() == 0)
			{
				Problems.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("the %s rig '%s' has no retarget chains, so nothing can be mapped %s it."),
					i == 0 ? TEXT("source") : TEXT("target"), *R->GetName(),
					i == 0 ? TEXT("from") : TEXT("onto"))));
			}
			if (R->GetRetargetRoot().IsNone())
			{
				Problems.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("the %s rig '%s' has no retarget root, so the overall body position will not "
						 "transfer."), i == 0 ? TEXT("source") : TEXT("target"), *R->GetName())));
			}
		}

		Out->SetStringField(TEXT("retargeter"), Asset->GetPathName());
		Out->SetStringField(TEXT("sourceRig"), IsValid(Src) ? Src->GetPathName() : FString());
		Out->SetStringField(TEXT("targetRig"), IsValid(Tgt) ? Tgt->GetPathName() : FString());
		IKWriteMapping(Asset, Out);
		// The engine's verdict - with the caveat that ON THIS SIDE the flag is not one.
		// UIKRetargetProcessor::bIsInitialized is set UNCONDITIONALLY at
		// IKRetargetProcessor.cpp:1566, after the root and chain initialisations have been allowed to
		// fail with warnings only, so a retargeter with zero mapped chains and no root reports TRUE.
		// The real answer needs all three: the flag, an empty error log, and the inner IK Rig
		// processor. (The header at IKRetargetProcessor.h:476 says to check bIsLoadedAndValid; no such
		// member exists - the comment is stale.)
		USkeletalMesh* SrcMesh = IsValid(Src) ? Src->PreviewSkeletalMesh.LoadSynchronous() : nullptr;
		USkeletalMesh* TgtMesh = IsValid(Tgt) ? Tgt->PreviewSkeletalMesh.LoadSynchronous() : nullptr;
		if (Problems.Num() > 0 || !SrcMesh || !TgtMesh)
		{
			Out->SetStringField(TEXT("runtimeNote"),
				Problems.Num() > 0
					? TEXT("the engine's own initialisation check was NOT run, because the problems above "
						   "already mean this retargeter cannot work - and initialising a structurally "
						   "broken rig can hit an assert that terminates the editor. Fix them and read "
						   "this again.")
					: TEXT("the engine's own initialisation check was NOT run: one of the rigs has no "
						   "preview mesh, and the retargeter runtime needs a source AND a target mesh. "
						   "Assign them with set_ik_rig_mesh."));
		}
		else
		{
			TStrongObjectPtr<UIKRetargetProcessor> Proc(
				NewObject<UIKRetargetProcessor>(GetTransientPackage()));
			Proc->Initialize(SrcMesh, TgtMesh, Asset, /*bSuppressWarnings=*/false);
			const bool bFlag = Proc->IsInitialized();
			const bool bNoErrors = Proc->Log.GetErrors().Num() == 0;
			const UIKRigProcessor* Inner = Proc->GetTargetIKRigProcessor();
			const bool bInner = Inner && Inner->IsInitialized();
			const bool bReallyOk = bFlag && bNoErrors && bInner;

			IKCopyLog(Proc->Log, Out);
			// All three reported separately, because the composite verdict is this endpoint's
			// judgement and a caller is entitled to see what it was built from.
			Out->SetBoolField(TEXT("runtimeInitialized"), bReallyOk);
			Out->SetBoolField(TEXT("runtimeFlagSet"), bFlag);
			Out->SetBoolField(TEXT("runtimeTargetRigInitialized"), bInner);
			Out->SetStringField(TEXT("runtimeNote"), bReallyOk
				? TEXT("the engine initialised this retargeter and reported no errors, so it would run.")
				: TEXT("this retargeter would NOT work. Note that the engine's own IsInitialized() flag "
					   "is set unconditionally and reports true even for a retargeter with no mapped "
					   "chains and no root, so runtimeInitialized here is the flag AND an empty error "
					   "log AND the target rig's own processor having initialised. runtimeErrors is the "
					   "engine's explanation."));
			if (!bReallyOk)
			{
				Out->SetBoolField(TEXT("valid"), false);
			}
		}
		if (!Out->HasField(TEXT("valid")))
		{
			Out->SetBoolField(TEXT("valid"), Problems.Num() == 0);
		}
		Out->SetArrayField(TEXT("problems"), Problems);
		Out->SetStringField(TEXT("sourceNote"),
			TEXT("this reads ChainSettings, which is the live mapping. The asset also carries a "
				 "ChainMapping property - that is FRetargetChainMap, DEPRECATED since 5.1, and writing "
				 "it with set_property succeeds while being read by nothing."));
#endif
	}
	// --- list_ik_solver_types ------------------------------------------------
	//   in:  {}
	//   out: { types:[{ solverClass, path }] }
	//
	// Without this a caller cannot know what to pass to add_ik_solver, and the names are NOT
	// guessable - the full-body solver class is UIKRigFBIKSolver while its siblings are
	// UIKRig_LimbSolver, UIKRig_PoleSolver, UIKRig_BodyMover, UIKRig_SetTransform.
	//
	// Reports CLASS NAMES only. The friendly label comes from GetNiceName(), whose base implementation
	// is checkNoEntry() (IKRigSolver.h:63) - a custom solver class that does not override it would
	// terminate the editor for the sake of a prettier string.
	void H_list_ik_solver_types(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {},
			TEXT("no parameters - this lists the solver classes this engine build has"),
			{ { TEXT("path"), TEXT("this lists solver CLASSES available in the engine, not the solvers on a particular rig - list_ik_rig reports those") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("list_ik_solver_types"));
#else
		TArray<TSharedPtr<FJsonValue>> Types;
		for (TObjectIterator<UClass> It; It; ++It)
		{
			UClass* C = *It;
			if (!C->IsChildOf(UIKRigSolver::StaticClass()) || C == UIKRigSolver::StaticClass()) { continue; }
			if (C->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated | CLASS_NewerVersionExists)) { continue; }
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("solverClass"), C->GetName());
			J->SetStringField(TEXT("path"), C->GetPathName());
			Types.Add(MakeShared<FJsonValueObject>(J));
		}
		Out->SetArrayField(TEXT("types"), Types);
		Out->SetNumberField(TEXT("count"), Types.Num());
		Out->SetStringField(TEXT("note"),
			TEXT("pass solverClass to add_ik_solver. These are class names rather than the friendly "
				 "labels the IK Rig editor shows: that label comes from GetNiceName(), whose base "
				 "implementation asserts, so it is deliberately not called here."));
#endif
	}

	// --- add_ik_solver -------------------------------------------------------
	//   in:  { path, solverClass }
	//   out: { index, solverClass, solverCount }
	void H_add_ik_solver(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("solverClass"), TEXT("solver") },
			TEXT("path (aliases: assetPath, rig), solverClass (alias: solver) - "
				 "list_ik_solver_types shows the available ones"),
			{ { TEXT("goal"), TEXT("a solver is added first, then a goal is connected to it with set_ik_goal_solver_connection") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("add_ik_solver"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		const FString ClassName = JStrAny(In, { TEXT("solverClass"), TEXT("solver") });
		if (ClassName.IsEmpty())
		{
			Fail(Out, TEXT("solverClass is required - list_ik_solver_types shows the available ones. "
						   "NOTHING was created."));
			return;
		}
		FString Why;
		UClass* SolverClass = IKResolveSolverClass(ClassName, Why);
		if (!SolverClass)
		{
			Fail(Out, FString::Printf(TEXT("%s NOTHING was created."), *Why));
			return;
		}

		const int32 Index = C->AddSolver(SolverClass);
		if (Index == INDEX_NONE)
		{
			Fail(Out, FString::Printf(
				TEXT("the rig refused solver class '%s' and reported nothing beyond a log line. "
					 "NOTHING was created."), *ClassName));
			return;
		}
		IKMarkDirty(Rig);
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetNumberField(TEXT("index"), Index);
		Out->SetStringField(TEXT("solverClass"), SolverClass->GetName());
		Out->SetNumberField(TEXT("solverCount"), Rig->GetSolverArray().Num());
		// The index is the handle for everything else, and it SHIFTS when an earlier solver is
		// removed - worth saying once rather than being discovered.
		Out->SetStringField(TEXT("note"),
			TEXT("solvers are addressed by INDEX, and indices shift when an earlier solver is removed - "
				 "re-read with list_ik_rig after any remove_ik_solver. Set the solver's bone span with "
				 "set_ik_solver, and connect goals to it with set_ik_goal_solver_connection."));
#endif
	}

	// --- remove_ik_solver ----------------------------------------------------
	//   in:  { path, index }
	//   out: { removed, solverCount }
	void H_remove_ik_solver(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("index"), TEXT("solverIndex") },
			TEXT("path (aliases: assetPath, rig), index (alias: solverIndex) from list_ik_rig"),
			{ { TEXT("solverClass"), TEXT("solvers are removed by INDEX - a rig may hold several of one class") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("remove_ik_solver"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		const int32 Count = Rig->GetSolverArray().Num();
		const int32 Index = int32(JNum(In, TEXT("index"), JNum(In, TEXT("solverIndex"), -1.0)));
		if (Index < 0 || Index >= Count)
		{
			// The index and the actual count, always - "invalid index" alone is a riddle.
			Fail(Out, FString::Printf(
				TEXT("solver index %d is out of range: this rig has %d solver(s), so valid indices are "
					 "0..%d. NOTHING was removed."), Index, Count, Count - 1));
			return;
		}
		if (!C->RemoveSolver(Index))
		{
			Fail(Out, FString::Printf(TEXT("the rig refused to remove solver %d. NOTHING was removed."),
				Index));
			return;
		}
		IKMarkDirty(Rig);
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetBoolField(TEXT("removed"), true);
		Out->SetNumberField(TEXT("index"), Index);
		Out->SetNumberField(TEXT("solverCount"), Rig->GetSolverArray().Num());
		Out->SetStringField(TEXT("note"),
			TEXT("every solver after this one has shifted DOWN by one index, and any goal connected "
				 "only to this solver is now inert. Re-read with list_ik_rig before using an index or "
				 "trusting a connection."));
#endif
	}

	// --- set_ik_solver -------------------------------------------------------
	//   in:  { path, index, rootBone?, endBone?, enabled? }
	//   out: { index, rootBone, endBone, enabled }
	//
	// Each pre-check exists because the engine's setters return a bare false for two different
	// reasons: SetRootBone gives the same answer for "no such solver" and "no such bone"
	// (IKRigController.cpp:775-784).
	void H_set_ik_solver(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("index"), TEXT("solverIndex"),
			  TEXT("rootBone"), TEXT("endBone"), TEXT("enabled") },
			TEXT("path (aliases: assetPath, rig), index (alias: solverIndex), and any of rootBone, "
				 "endBone, enabled"),
			{ { TEXT("goal"), TEXT("goals attach to a solver via set_ik_goal_solver_connection, not here") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("set_ik_solver"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		const int32 Count = Rig->GetSolverArray().Num();
		const int32 Index = int32(JNum(In, TEXT("index"), JNum(In, TEXT("solverIndex"), -1.0)));
		if (Index < 0 || Index >= Count)
		{
			Fail(Out, FString::Printf(
				TEXT("solver index %d is out of range: this rig has %d solver(s). NOTHING was changed."),
				Index, Count));
			return;
		}
		const bool bHasRoot = In->HasField(TEXT("rootBone"));
		const bool bHasEnd = In->HasField(TEXT("endBone"));
		const bool bHasEnabled = In->HasField(TEXT("enabled"));
		if (!bHasRoot && !bHasEnd && !bHasEnabled)
		{
			Fail(Out, TEXT("nothing to change - give at least one of rootBone, endBone or enabled. "
						   "NOTHING was changed."));
			return;
		}

		// Both bones are validated BEFORE either is written, so a bad endBone cannot leave a solver
		// with a new root and its old end.
		const FIKRigSkeleton& Skel = Rig->GetSkeleton();
		const FString RootName = JStr(In, TEXT("rootBone"));
		const FString EndName = JStr(In, TEXT("endBone"));
		for (int32 i = 0; i < 2; ++i)
		{
			const bool bHas = (i == 0) ? bHasRoot : bHasEnd;
			const FString& N = (i == 0) ? RootName : EndName;
			if (!bHas || N.IsEmpty()) { continue; }
			if (!Skel.BoneNames.Contains(FName(*N)))
			{
				Fail(Out, FString::Printf(
					TEXT("%s '%s' is not a bone in this rig's skeleton (%d bones; assign a mesh with "
						 "set_ik_rig_mesh if that is 0). NOTHING was changed."),
					i == 0 ? TEXT("rootBone") : TEXT("endBone"), *N, Skel.BoneNames.Num()));
				return;
			}
		}

		TArray<FString> Refused;
		if (bHasRoot && !C->SetRootBone(FName(*RootName), Index))
		{
			// Reached only when the solver type does not accept a root bone at all - the bone itself
			// was already proven to exist.
			Refused.Add(FString::Printf(
				TEXT("rootBone (this solver type may not use one)")));
		}
		if (bHasEnd && !C->SetEndBone(FName(*EndName), Index))
		{
			Refused.Add(FString::Printf(TEXT("endBone (this solver type may not use one)")));
		}
		if (bHasEnabled)
		{
			C->SetSolverEnabled(Index, JBool(In, TEXT("enabled"), true));
		}

		IKMarkDirty(Rig);
		const UIKRigSolver* Solver = Rig->GetSolverArray()[Index];
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetNumberField(TEXT("index"), Index);
		Out->SetStringField(TEXT("solverClass"), Solver ? Solver->GetClass()->GetName() : FString());
		// Read back off the solver rather than echoing the request: not every solver type honours
		// every field, and silence about that is how a rig ends up not doing what it was told.
		Out->SetStringField(TEXT("rootBone"), Solver ? Solver->GetRootBone().ToString() : FString());
		Out->SetStringField(TEXT("endBone"), Solver ? Solver->GetEndBone().ToString() : FString());
		Out->SetBoolField(TEXT("enabled"), Solver ? Solver->IsEnabled() : false);
		if (Refused.Num() > 0)
		{
			Out->SetStringField(TEXT("refusedNote"), FString::Printf(
				TEXT("this solver did not accept: %s. The bone names were valid, so the solver type "
					 "simply does not use those fields - the values read back above are what it "
					 "actually holds."), *FString::Join(Refused, TEXT(", "))));
		}
#endif
	}

	// --- add_ik_goal ---------------------------------------------------------
	//   in:  { path, name, bone }
	//   out: { name, requestedName, bone, sanitised }
	//
	// AddNewGoal neither sanitises nor uniquifies - unlike AddRetargetChain - and returns NAME_None
	// for BOTH "name already exists" (IKRigController.cpp:900-903) and "unknown bone" (:906-912).
	// Both are checked here so the failure says which.
	void H_add_ik_goal(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("goalName"),
			  TEXT("bone"), TEXT("boneName") },
			TEXT("path (aliases: assetPath, rig), name (alias: goalName), bone (alias: boneName)"),
			{ { TEXT("transform"), TEXT("a goal's transform is a preview pose, not authoring, and is deliberately not settable here - the engine call asserts on an unknown goal name") },
			  { TEXT("solver"), TEXT("connect the goal to a solver afterwards with set_ik_goal_solver_connection") } }))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("add_ik_goal"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		FString Name = JStrAny(In, { TEXT("name"), TEXT("goalName") });
		const FString Bone = JStrAny(In, { TEXT("bone"), TEXT("boneName") });
		if (Name.IsEmpty() || Bone.IsEmpty())
		{
			Fail(Out, TEXT("name and bone are both required. NOTHING was created."));
			return;
		}
		const FIKRigSkeleton& Skel = Rig->GetSkeleton();
		if (Skel.BoneNames.Num() == 0)
		{
			Fail(Out, TEXT("this rig has no skeleton yet, so no bone name can be valid. Call "
						   "set_ik_rig_mesh first. NOTHING was created."));
			return;
		}
		if (!Skel.BoneNames.Contains(FName(*Bone)))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a bone in this rig's skeleton (%d bones). NOTHING was created."),
				*Bone, Skel.BoneNames.Num()));
			return;
		}

		// The engine's own sanitiser, so the name this produces is the one the editor would.
		const FString Requested = Name;
		UIKRigController::SanitizeGoalName(Name);
		if (C->GetGoalIndex(FName(*Name)) != INDEX_NONE)
		{
			// Distinguished from the bone failure above, which AddNewGoal's bare NAME_None cannot do.
			Fail(Out, FString::Printf(
				TEXT("this rig already has a goal called '%s'. Goal names are not uniquified for you - "
					 "pick another, or remove the existing one. NOTHING was created."), *Name));
			return;
		}

		const FName Actual = C->AddNewGoal(FName(*Name), FName(*Bone));
		if (Actual.IsNone())
		{
			Fail(Out, TEXT("the rig refused the goal and reported nothing beyond a log line. Both the "
						   "name and the bone were checked first, so this is unexpected. NOTHING was "
						   "created."));
			return;
		}
		IKMarkDirty(Rig);
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetStringField(TEXT("name"), Actual.ToString());
		Out->SetStringField(TEXT("requestedName"), Requested);
		Out->SetStringField(TEXT("bone"), Bone);
		const bool bSanitised = Actual.ToString() != Requested;
		Out->SetBoolField(TEXT("sanitised"), bSanitised);
		if (bSanitised)
		{
			Out->SetStringField(TEXT("nameNote"), FString::Printf(
				TEXT("the name was sanitised from '%s' to '%s'. Use the returned name everywhere else - "
					 "the engine will not recognise the original."), *Requested, *Actual.ToString()));
		}
		Out->SetStringField(TEXT("note"),
			TEXT("a goal connected to no solver does NOTHING and the rig still initialises - the "
				 "engine only warns. Connect it with set_ik_goal_solver_connection."));
#endif
	}

	// --- remove_ik_goal ------------------------------------------------------
	//   in:  { path, name }
	//   out: { removed, goalCount }
	void H_remove_ik_goal(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("goalName") },
			TEXT("path (aliases: assetPath, rig), name (alias: goalName)"), {}))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("remove_ik_goal"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		const FString Name = JStrAny(In, { TEXT("name"), TEXT("goalName") });
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required. NOTHING was removed."));
			return;
		}
		if (!C->RemoveGoal(FName(*Name)))
		{
			TArray<FString> Have;
			for (const UIKRigEffectorGoal* G : Rig->GetGoalArray())
			{
				if (G) { Have.Add(G->GoalName.ToString()); }
			}
			Fail(Out, FString::Printf(
				TEXT("this rig has no goal called '%s'. It has: %s. NOTHING was removed."),
				*Name, Have.Num() ? *FString::Join(Have, TEXT(", ")) : TEXT("(none)")));
			return;
		}
		IKMarkDirty(Rig);
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetBoolField(TEXT("removed"), true);
		Out->SetStringField(TEXT("name"), Name);
		Out->SetNumberField(TEXT("goalCount"), Rig->GetGoalArray().Num());
		Out->SetStringField(TEXT("note"),
			TEXT("any retarget chain that named this goal now names nothing. Check with list_ik_rig."));
#endif
	}

	// --- set_ik_goal_bone ----------------------------------------------------
	//   in:  { path, name, bone }
	//   out: { name, bone, previousBone }
	void H_set_ik_goal_bone(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("goalName"),
			  TEXT("bone"), TEXT("boneName") },
			TEXT("path (aliases: assetPath, rig), name (alias: goalName), bone (alias: boneName)"), {}))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("set_ik_goal_bone"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		const FString Name = JStrAny(In, { TEXT("name"), TEXT("goalName") });
		const FString Bone = JStrAny(In, { TEXT("bone"), TEXT("boneName") });
		if (Name.IsEmpty() || Bone.IsEmpty())
		{
			Fail(Out, TEXT("name and bone are both required. NOTHING was changed."));
			return;
		}
		// SetGoalBone returns false for "no such goal" and "no such bone" alike, so both are checked
		// here and the error says which.
		if (C->GetGoalIndex(FName(*Name)) == INDEX_NONE)
		{
			Fail(Out, FString::Printf(TEXT("this rig has no goal called '%s'. NOTHING was changed."),
				*Name));
			return;
		}
		const FIKRigSkeleton& Skel = Rig->GetSkeleton();
		if (!Skel.BoneNames.Contains(FName(*Bone)))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a bone in this rig's skeleton (%d bones). NOTHING was changed."),
				*Bone, Skel.BoneNames.Num()));
			return;
		}
		const FName Previous = C->GetBoneForGoal(FName(*Name));
		if (!C->SetGoalBone(FName(*Name), FName(*Bone)))
		{
			Fail(Out, FString::Printf(
				TEXT("the rig refused to move goal '%s' to bone '%s', though both exist. NOTHING was "
					 "changed."), *Name, *Bone));
			return;
		}
		IKMarkDirty(Rig);
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetStringField(TEXT("name"), Name);
		Out->SetStringField(TEXT("bone"), C->GetBoneForGoal(FName(*Name)).ToString());
		Out->SetStringField(TEXT("previousBone"), Previous.ToString());
#endif
	}

	// --- set_ik_goal_solver_connection ---------------------------------------
	//   in:  { path, name, solverIndex, connected? }
	//   out: { name, solverIndex, connected }
	//
	// The step that makes a goal do anything. A goal wired to no solver is inert and the engine only
	// warns about it, so nothing else will tell you it was missed.
	void H_set_ik_goal_solver_connection(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("goalName"),
			  TEXT("solverIndex"), TEXT("index"), TEXT("connected") },
			TEXT("path (aliases: assetPath, rig), name (alias: goalName), solverIndex (alias: index), "
				 "connected (bool, default true - false disconnects)"), {}))
		{
			return;
		}
#if !MIF_WITH_IKRIG
		IKRigUnavailable(Out, TEXT("set_ik_goal_solver_connection"));
#else
		UIKRigDefinition* Rig = nullptr;
		UIKRigController* C = IKResolveRig(In, Out, Rig);
		if (!C) { return; }

		const FString Name = JStrAny(In, { TEXT("name"), TEXT("goalName") });
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required - the goal to connect. NOTHING was changed."));
			return;
		}
		if (C->GetGoalIndex(FName(*Name)) == INDEX_NONE)
		{
			Fail(Out, FString::Printf(TEXT("this rig has no goal called '%s'. NOTHING was changed."),
				*Name));
			return;
		}
		const int32 Count = Rig->GetSolverArray().Num();
		const int32 Index = int32(JNum(In, TEXT("solverIndex"), JNum(In, TEXT("index"), -1.0)));
		if (Index < 0 || Index >= Count)
		{
			Fail(Out, FString::Printf(
				TEXT("solver index %d is out of range: this rig has %d solver(s). Add one with "
					 "add_ik_solver. NOTHING was changed."), Index, Count));
			return;
		}

		const bool bConnect = JBool(In, TEXT("connected"), true);
		const bool bOk = bConnect ? C->ConnectGoalToSolver(FName(*Name), Index)
								  : C->DisconnectGoalFromSolver(FName(*Name), Index);
		if (!bOk)
		{
			Fail(Out, FString::Printf(
				TEXT("the rig refused to %s goal '%s' %s solver %d. The goal and the solver both exist, "
					 "so this solver type probably does not accept goals. NOTHING was changed."),
				bConnect ? TEXT("connect") : TEXT("disconnect"), *Name,
				bConnect ? TEXT("to") : TEXT("from"), Index));
			return;
		}
		// Read the connection back rather than trusting the bool.
		const bool bNow = C->IsGoalConnectedToSolver(FName(*Name), Index);
		if (bNow != bConnect)
		{
			Fail(Out, FString::Printf(
				TEXT("asked to set goal '%s' %s solver %d, and the rig reports the opposite afterwards. "
					 "Read it back with list_ik_rig before relying on this rig."),
				*Name, bConnect ? TEXT("connected to") : TEXT("disconnected from"), Index));
			return;
		}
		IKMarkDirty(Rig);
		Out->SetStringField(TEXT("rig"), Rig->GetPathName());
		Out->SetStringField(TEXT("name"), Name);
		Out->SetNumberField(TEXT("solverIndex"), Index);
		Out->SetBoolField(TEXT("connected"), bNow);
		Out->SetBoolField(TEXT("connectedToAnySolver"), C->IsGoalConnectedToAnySolver(FName(*Name)));
#endif
	}
}
