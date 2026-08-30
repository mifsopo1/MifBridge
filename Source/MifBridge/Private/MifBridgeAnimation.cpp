// MifBridge — animation ASSET introspection (read-only).
//
// The graph endpoints cover animation BLUEPRINTS (an AnimBlueprint is a UBlueprint, and since
// GatherGraphs recurses into UEdGraphNode::GetSubGraphs it now reaches state machines, individual
// states, and transition rule graphs). This file covers the animation DATA assets those graphs play:
// sequences, montages, blend spaces, composites — none of which are Blueprints at all, so nothing in
// the graph API could ever see them.
//
// Everything here reads UAnimSequence/UAnimMontage/UBlendSpace, which live in the Engine module —
// no extra build dependency. Read-only: registered in IsReadOnlyEndpoint, no transaction.
#include "MifBridgeHandlers.h"
#include "Engine/SkeletalMeshSocket.h"
#include "ScopedTransaction.h"

// Sockets, behavior trees and blackboards - all READ-ONLY. See H_list_sockets below for why these
// live here rather than in a new file: this is already the animation-and-skeleton module.
#include "Engine/SkeletalMesh.h"
#include "Engine/SkeletalMeshSocket.h"
#include "Animation/Skeleton.h"
#include "ScopedTransaction.h"        // USkeleton::Sockets - where DDS2 actually keeps them
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshSocket.h"
#include "BehaviorTree/BehaviorTree.h"
#include "BehaviorTree/BTCompositeNode.h"
#include "BehaviorTree/BTTaskNode.h"
#include "BehaviorTree/BlackboardData.h"
// The ten concrete key types. KeyType is an instanced UObject, not an enum, so each one is a class.
#include "BehaviorTree/Blackboard/BlackboardKeyType_Bool.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Int.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Float.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_String.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Name.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Vector.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Rotator.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Object.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Class.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Enum.h"
#include "UObject/Package.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType.h"
#include "Animation/AnimBlueprint.h"
#include "AnimGraphNode_Base.h"
#include "AnimationGraphSchema.h"   // UAnimationGraphSchema - add_anim_node checks the GRAPH, not just the blueprint (PM-013)
#include "MifBridgeLog.h"

#include "Animation/AnimationAsset.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimSequenceBase.h"
#include "Animation/AnimMontage.h"
#include "Animation/AnimComposite.h"
#include "Animation/AnimCompositeBase.h"  // FAnimSegment (montage slot tracks)
#include "Animation/BlendSpace.h"
#include "Animation/AnimTypes.h"          // FAnimNotifyEvent, FAnimSyncMarker
#include "AnimationBlueprintLibrary.h"   // the PUBLIC notify-authoring API (Editor module)
#include "AnimStateNode.h"              // add_anim_state
#include "AnimStateNodeBase.h"
#include "AnimationStateMachineGraph.h" // the CLASS the node's outer is CastChecked to
#include "Kismet2/BlueprintEditorUtils.h"
#include "Animation/AnimNotifies/AnimNotify.h"
#include "Animation/AnimNotifies/AnimNotifyState.h"
#include "Animation/Skeleton.h"
#include "ScopedTransaction.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Misc/PackageName.h"
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
	namespace
	{
		TSharedRef<FJsonObject> SocketJson(const FName& Name, const FName& Bone,
			const FVector& Loc, const FRotator& Rot, const FVector& Scale)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), Name.ToString());
			if (!Bone.IsNone()) { J->SetStringField(TEXT("bone"), Bone.ToString()); }
			J->SetObjectField(TEXT("relativeLocation"), Vec3(Loc));
			J->SetObjectField(TEXT("relativeRotation"), Vec3(FVector(Rot.Pitch, Rot.Yaw, Rot.Roll)));
			J->SetObjectField(TEXT("relativeScale"), Vec3(Scale));
			return J;
		}

		// Walk a behavior tree depth-first. Bounded: a corrupt asset with a cycle would otherwise
		// hang the game thread, which on this bridge means the whole editor stops answering.
		void WalkBT(UBTCompositeNode* Node, int32 Depth, int32& Budget,
			TArray<TSharedPtr<FJsonValue>>& Out)
		{
			if (!Node || Budget <= 0) { return; }
			--Budget;
			for (const FBTCompositeChild& Child : Node->Children)
			{
				UBTNode* Actual = Child.ChildComposite
					? static_cast<UBTNode*>(Child.ChildComposite)
					: static_cast<UBTNode*>(Child.ChildTask);
				if (!Actual) { continue; }
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetNumberField(TEXT("depth"), Depth);
				J->SetStringField(TEXT("name"), Actual->GetNodeName());
				J->SetStringField(TEXT("class"), Actual->GetClass()->GetName());
				J->SetStringField(TEXT("kind"), Child.ChildComposite ? TEXT("composite") : TEXT("task"));
				J->SetNumberField(TEXT("decorators"), Child.Decorators.Num());
				Out.Add(MakeShared<FJsonValueObject>(J));
				if (Child.ChildComposite)
				{
					WalkBT(Child.ChildComposite, Depth + 1, Budget, Out);
				}
			}
		}
	}

	// --- list_sockets --------------------------------------------------------
	//   in:  { path }  (a SkeletalMesh or StaticMesh asset)
	//   out: { assetKind, count, sockets:[{name, bone, relativeLocation, ...}] }
	//
	// Attaching a mod's prop to a character socket is ordinary work and there was no way to even see
	// what sockets exist. Lives in the animation module because that is where skeleton-adjacent
	// reading already happens.
	void H_list_sockets(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("mesh") },
			TEXT("path (alias: assetPath, mesh) of a SkeletalMesh or StaticMesh asset"),
			{ { TEXT("blueprintId"), TEXT("sockets live on the MESH ASSET, not on a blueprint - take the mesh path from the component's StaticMesh/SkeletalMesh property, or from find_assets") },
			  { TEXT("componentName"), TEXT("same: resolve the component's mesh asset first, then pass that path here") } }))
		{
			return;
		}
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("mesh") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required (a SkeletalMesh or StaticMesh asset)"));
			return;
		}
		UObject* Asset = LoadAssetLenient(Path);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: %s"), *Path));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Arr;
		if (USkeletalMesh* SK = Cast<USkeletalMesh>(Asset))
		{
			// BOTH LISTS, OR THIS ENDPOINT IS USELESS HERE.
			//
			// Sockets live in two places: on the mesh, and on the USkeleton the mesh uses. The first
			// version of this returned only the mesh's and carried a note explaining that the skeleton
			// has its own. Then it was pointed at real content: all 12 sampled DDS2 skeletal meshes
			// have ZERO mesh sockets, because the game keeps them on one shared
			// DDS2_CharacterSkeleton - which is the normal pattern for a game with a common rig.
			//
			// So the honest version returned an empty array for every character in the game and
			// explained why. Explaining an empty answer is not the same as giving the right one.
			int32 MeshCount = 0;
			for (USkeletalMeshSocket* S : SK->GetMeshOnlySocketList())
			{
				if (S)
				{
					TSharedRef<FJsonObject> J = SocketJson(S->SocketName, S->BoneName,
						S->RelativeLocation, S->RelativeRotation, S->RelativeScale);
					J->SetStringField(TEXT("source"), TEXT("mesh"));
					// INDEX AND objectPath, WITHOUT WHICH THE WRITE HALF IS UNREACHABLE. Moving or
					// deleting a socket needs no new endpoint - set_property on
					// "Sockets[N].RelativeLocation" and edit_container {propertyPath:"Sockets",
					// operation:"remove", index:N} both work today. What was missing was N. Emitting
					// it here is the whole reason those two verbs did not need building.
					J->SetNumberField(TEXT("index"), MeshCount);
					J->SetStringField(TEXT("objectPath"), S->GetPathName());
					Arr.Add(MakeShared<FJsonValueObject>(J));
					++MeshCount;
				}
			}
			int32 SkeletonCount = 0;
			USkeleton* Skeleton = SK->GetSkeleton();
			if (Skeleton)
			{
				for (USkeletalMeshSocket* S : Skeleton->Sockets)
				{
					if (S)
					{
						TSharedRef<FJsonObject> J = SocketJson(S->SocketName, S->BoneName,
							S->RelativeLocation, S->RelativeRotation, S->RelativeScale);
						// Which list a socket came from decides where you would EDIT it, so it is
						// reported per socket rather than only in a summary.
						J->SetStringField(TEXT("source"), TEXT("skeleton"));
						// The index is into the SKELETON's Sockets array, and `owner` says which
						// object to address - a skeleton socket's index means nothing on the mesh.
						J->SetNumberField(TEXT("index"), SkeletonCount);
						J->SetStringField(TEXT("owner"), Skeleton->GetPathName());
						J->SetStringField(TEXT("objectPath"), S->GetPathName());
						Arr.Add(MakeShared<FJsonValueObject>(J));
						++SkeletonCount;
					}
				}
				Out->SetStringField(TEXT("skeleton"), Skeleton->GetPathName());
			}
			Out->SetStringField(TEXT("assetKind"), TEXT("SkeletalMesh"));
			Out->SetNumberField(TEXT("meshSocketCount"), MeshCount);
			Out->SetNumberField(TEXT("skeletonSocketCount"), SkeletonCount);
			if (!Skeleton)
			{
				Out->SetStringField(TEXT("note"),
					TEXT("this mesh has no USkeleton, so only its own sockets could be listed"));
			}
		}
		else if (UStaticMesh* SM = Cast<UStaticMesh>(Asset))
		{
			for (UStaticMeshSocket* S : SM->Sockets)
			{
				if (S)
				{
					Arr.Add(MakeShared<FJsonValueObject>(SocketJson(
						S->SocketName, NAME_None, S->RelativeLocation, S->RelativeRotation,
						S->RelativeScale)));
				}
			}
			Out->SetStringField(TEXT("assetKind"), TEXT("StaticMesh"));
		}
		else
		{
			// "Not a mesh" and "a mesh with no sockets" both produce an empty array otherwise.
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s, which has no sockets - pass a SkeletalMesh or StaticMesh."),
				*Path, *Asset->GetClass()->GetName()));
			return;
		}

		Out->SetStringField(TEXT("path"), Asset->GetPathName());
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("sockets"), Arr);
	}

	// --- describe_behavior_tree ----------------------------------------------
	//   in:  { path }
	//   out: { root, blackboard, nodeCount, nodes:[{depth, name, class, kind, decorators}] }
	void H_describe_behavior_tree(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath") },
			TEXT("path (alias: assetPath) of a BehaviorTree asset"),
			{ { TEXT("blueprintId"), TEXT("a BehaviorTree is its own asset, not a blueprint - find one with find_assets {class: BehaviorTree}") } }))
		{
			return;
		}
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath") });
		UObject* Asset = Path.IsEmpty() ? nullptr : LoadAssetLenient(Path);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("behavior tree not found: %s"), *Path));
			return;
		}
		UBehaviorTree* BT = Cast<UBehaviorTree>(Asset);
		if (!BT)
		{
			Fail(Out, FString::Printf(TEXT("'%s' is a %s, not a BehaviorTree."),
				*Path, *Asset->GetClass()->GetName()));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Nodes;
		int32 Budget = 2000;          // bounded walk - see WalkBT
		if (BT->RootNode)
		{
			TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
			R->SetNumberField(TEXT("depth"), 0);
			R->SetStringField(TEXT("name"), BT->RootNode->GetNodeName());
			R->SetStringField(TEXT("class"), BT->RootNode->GetClass()->GetName());
			R->SetStringField(TEXT("kind"), TEXT("root"));
			Nodes.Add(MakeShared<FJsonValueObject>(R));
			WalkBT(BT->RootNode, 1, Budget, Nodes);
		}
		Out->SetStringField(TEXT("path"), BT->GetPathName());
		Out->SetBoolField(TEXT("hasRoot"), BT->RootNode != nullptr);
		if (UBlackboardData* BB = BT->GetBlackboardAsset())
		{
			Out->SetStringField(TEXT("blackboard"), BB->GetPathName());
		}
		Out->SetNumberField(TEXT("nodeCount"), Nodes.Num());
		Out->SetArrayField(TEXT("nodes"), Nodes);
		if (Budget <= 0)
		{
			Out->SetBoolField(TEXT("truncated"), true);
			Out->SetStringField(TEXT("truncatedNote"),
				TEXT("the walk hit its 2000-node budget and stopped. Reported rather than silently "
					 "returning a partial tree as if it were the whole one."));
		}
	}

	// --- list_blackboard_keys ------------------------------------------------
	//   in:  { path }
	//   out: { count, keys:[{name, type, instanceSynced}], inheritedFrom? }
	void H_list_blackboard_keys(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath") },
			TEXT("path (alias: assetPath) of a BlackboardData asset"),
			{ { TEXT("behaviorTree"), TEXT("pass the BLACKBOARD's path; describe_behavior_tree reports which blackboard a tree uses") } }))
		{
			return;
		}
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath") });
		UObject* Asset = Path.IsEmpty() ? nullptr : LoadAssetLenient(Path);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("blackboard not found: %s"), *Path));
			return;
		}
		UBlackboardData* BB = Cast<UBlackboardData>(Asset);
		if (!BB)
		{
			Fail(Out, FString::Printf(TEXT("'%s' is a %s, not a BlackboardData."),
				*Path, *Asset->GetClass()->GetName()));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Arr;
		auto AddKeys = [&Arr](const TArray<FBlackboardEntry>& Keys, bool bInherited)
		{
			for (const FBlackboardEntry& E : Keys)
			{
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("name"), E.EntryName.ToString());
				J->SetStringField(TEXT("type"), E.KeyType ? E.KeyType->GetClass()->GetName() : TEXT("(none)"));
				J->SetBoolField(TEXT("instanceSynced"), E.bInstanceSynced != 0);
				// Inherited keys are usable but are NOT editable on this asset, and a caller who
				// cannot tell the two apart will try to change one and wonder why nothing happened.
				J->SetBoolField(TEXT("inherited"), bInherited);
				Arr.Add(MakeShared<FJsonValueObject>(J));
			}
		};
		AddKeys(BB->GetKeys(), false);
		AddKeys(BB->ParentKeys, true);

		Out->SetStringField(TEXT("path"), BB->GetPathName());
		if (BB->Parent) { Out->SetStringField(TEXT("parent"), BB->Parent->GetPathName()); }
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("keys"), Arr);
	}

	namespace
	{
		// Same path tolerance as ResolveBlueprint: accept /Game/A/Foo or /Game/A/Foo.Foo.
		UObject* LoadAssetLoose(const FString& Path)
		{
			FString P = Path;
			P.TrimStartAndEndInline();
			if (P.IsEmpty())
			{
				return nullptr;
			}
			UObject* Obj = StaticLoadObject(UObject::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Obj && !P.Contains(TEXT(".")))
			{
				const FString Full = P + TEXT(".") + FPackageName::GetShortName(P);
				Obj = StaticLoadObject(UObject::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
			return Obj;
		}

		// Notifies carry EITHER a one-shot UAnimNotify (Notify) OR a ranged UAnimNotifyState
		// (NotifyStateClass, with a Duration). Report which, so a caller can tell a footstep marker
		// from a windowed state like "invulnerable".
		TSharedRef<FJsonObject> SerializeNotify(const FAnimNotifyEvent& Event)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), Event.NotifyName.ToString());
			J->SetNumberField(TEXT("triggerTime"), Event.GetTriggerTime());
			J->SetNumberField(TEXT("duration"), Event.GetDuration());
			if (Event.GetDuration() > 0.f)
			{
				J->SetNumberField(TEXT("endTriggerTime"), Event.GetEndTriggerTime());
			}
			J->SetStringField(TEXT("kind"), Event.NotifyStateClass ? TEXT("state") : TEXT("notify"));
			if (Event.Notify)
			{
				J->SetStringField(TEXT("notifyClass"), Event.Notify->GetClass()->GetPathName());
			}
			if (Event.NotifyStateClass)
			{
				J->SetStringField(TEXT("notifyStateClass"), Event.NotifyStateClass->GetClass()->GetPathName());
			}
			// Default is 1.0; only report a genuinely probabilistic notify.
			if (Event.NotifyTriggerChance < 1.f)
			{
				J->SetNumberField(TEXT("triggerChance"), Event.NotifyTriggerChance);
			}
			if (Event.MontageTickType == EMontageNotifyTickType::BranchingPoint)
			{
				J->SetBoolField(TEXT("branchingPoint"), true);
			}
			return J;
		}
	}

	// --- describe_animation -------------------------------------------------
	//   in:  { assetPath: "/Game/.../AS_Run" }
	//   out: { assetPath, class, type, skeleton?, playLength, rateScale, notifyCount, notifies[],
	//          syncMarkers[], curves[], frameRate?, numSampledKeys?, sections[]?, slots[]?,
	//          blendAxes[]?, samples[]? }
	//
	// `numKeys?` was listed here and is emitted by no line of this plugin — a documented field a caller
	// could branch on and never receive (verified: the literal appears nowhere else in the plugin).
	// The key-count field this handler actually emits, for UAnimSequence only, is `numSampledKeys`
	// (GetNumberOfSampledKeys). `notifyCount` was emitted and undocumented; both are corrected here
	// rather than one of them being deleted, because the response shape is the contract.
	//
	// One endpoint across every UAnimationAsset type rather than four near-identical ones: the caller
	// usually has a path and wants to know what is IN it, without first knowing which class it is.
	void H_describe_animation(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("animation"), TEXT("asset") },
			TEXT("assetPath (aliases: path, animation, asset) - the animation asset to describe, e.g. /Game/Anims/AS_Run"),
			{ { TEXT("name"), TEXT("this endpoint needs an object PATH - assetPath (aliases: path, animation, asset). list_animations returns assetPath values you can paste straight in") },
			  { TEXT("skeleton"), TEXT("not an input here - the skeleton is REPORTED in the response; to filter a LIST by skeleton use list_animations") },
			  { TEXT("blueprintId"), TEXT("this reads animation DATA assets (sequence/montage/blend space/composite). For an Animation BLUEPRINT use list_graphs/list_nodes, which recurse into state machines and transition graphs") } }))
		{
			return;
		}

		const FString AssetPath = JStrAny(In, { TEXT("assetPath"), TEXT("path"), TEXT("animation"), TEXT("asset") });
		if (AssetPath.IsEmpty())
		{
			Fail(Out, TEXT("assetPath required (e.g. /Game/Anims/AS_Run)"));
			return;
		}

		UObject* Asset = LoadAssetLoose(AssetPath);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: %s (list_animations lists what is available)"), *AssetPath));
			return;
		}

		UAnimationAsset* AnimAsset = Cast<UAnimationAsset>(Asset);
		if (!AnimAsset)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s, not an animation asset. For an Animation BLUEPRINT use list_graphs/list_nodes "
				     "(nested state machines and transition graphs are included)."),
				*AssetPath, *Asset->GetClass()->GetName()));
			return;
		}

		Out->SetStringField(TEXT("assetPath"), AnimAsset->GetPathName());
		Out->SetStringField(TEXT("class"), AnimAsset->GetClass()->GetPathName());
		if (const USkeleton* Skeleton = AnimAsset->GetSkeleton())
		{
			Out->SetStringField(TEXT("skeleton"), Skeleton->GetPathName());
		}
		Out->SetNumberField(TEXT("playLength"), AnimAsset->GetPlayLength());

		// --- UAnimSequenceBase: sequences, montages and composites all share notifies + curves ---
		if (UAnimSequenceBase* SeqBase = Cast<UAnimSequenceBase>(AnimAsset))
		{
			Out->SetNumberField(TEXT("rateScale"), SeqBase->RateScale);

			TArray<TSharedPtr<FJsonValue>> Notifies;
			for (const FAnimNotifyEvent& Event : SeqBase->Notifies)
			{
				Notifies.Add(MakeShared<FJsonValueObject>(SerializeNotify(Event)));
			}
			Out->SetArrayField(TEXT("notifies"), Notifies);
			Out->SetNumberField(TEXT("notifyCount"), Notifies.Num());

			// Float/vector curves driving material params, IK weights, etc.
			TArray<TSharedPtr<FJsonValue>> Curves;
			for (const FFloatCurve& Curve : SeqBase->GetCurveData().FloatCurves)
			{
				Curves.Add(MakeShared<FJsonValueString>(Curve.GetName().ToString()));
			}
			Out->SetArrayField(TEXT("curves"), Curves);
		}

		// --- UAnimSequence: sampling detail + sync markers -----------------------------------
		if (UAnimSequence* Sequence = Cast<UAnimSequence>(AnimAsset))
		{
			Out->SetStringField(TEXT("type"), TEXT("sequence"));
			Out->SetNumberField(TEXT("numSampledKeys"), Sequence->GetNumberOfSampledKeys());
			const FFrameRate Rate = Sequence->GetSamplingFrameRate();
			Out->SetNumberField(TEXT("frameRate"), Rate.AsDecimal());
			Out->SetBoolField(TEXT("additive"), Sequence->IsValidAdditive());

			TArray<TSharedPtr<FJsonValue>> Markers;
			for (const FAnimSyncMarker& Marker : Sequence->AuthoredSyncMarkers)
			{
				TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
				M->SetStringField(TEXT("name"), Marker.MarkerName.ToString());
				M->SetNumberField(TEXT("time"), Marker.Time);
				Markers.Add(MakeShared<FJsonValueObject>(M));
			}
			Out->SetArrayField(TEXT("syncMarkers"), Markers);
		}
		// --- UAnimMontage: sections + slot tracks --------------------------------------------
		else if (UAnimMontage* Montage = Cast<UAnimMontage>(AnimAsset))
		{
			Out->SetStringField(TEXT("type"), TEXT("montage"));
			Out->SetNumberField(TEXT("blendInTime"), Montage->BlendIn.GetBlendTime());
			Out->SetNumberField(TEXT("blendOutTime"), Montage->BlendOut.GetBlendTime());

			TArray<TSharedPtr<FJsonValue>> Sections;
			for (const FCompositeSection& Section : Montage->CompositeSections)
			{
				TSharedRef<FJsonObject> S = MakeShared<FJsonObject>();
				S->SetStringField(TEXT("name"), Section.SectionName.ToString());
				S->SetNumberField(TEXT("startTime"), Section.GetTime());
				// The next-section link is what makes a montage loop or chain; without it the
				// section list reads as linear when it may not be.
				if (Section.NextSectionName != NAME_None)
				{
					S->SetStringField(TEXT("nextSection"), Section.NextSectionName.ToString());
				}
				Sections.Add(MakeShared<FJsonValueObject>(S));
			}
			Out->SetArrayField(TEXT("sections"), Sections);

			TArray<TSharedPtr<FJsonValue>> Slots;
			for (const FSlotAnimationTrack& Track : Montage->SlotAnimTracks)
			{
				TSharedRef<FJsonObject> T = MakeShared<FJsonObject>();
				T->SetStringField(TEXT("slotName"), Track.SlotName.ToString());
				TArray<TSharedPtr<FJsonValue>> Segments;
				for (const FAnimSegment& Segment : Track.AnimTrack.AnimSegments)
				{
					TSharedRef<FJsonObject> G = MakeShared<FJsonObject>();
					if (const UAnimSequenceBase* Anim = Segment.GetAnimReference())
					{
						G->SetStringField(TEXT("animation"), Anim->GetPathName());
					}
					G->SetNumberField(TEXT("startPos"), Segment.StartPos);
					G->SetNumberField(TEXT("playRate"), Segment.AnimPlayRate);
					Segments.Add(MakeShared<FJsonValueObject>(G));
				}
				T->SetArrayField(TEXT("segments"), Segments);
				Slots.Add(MakeShared<FJsonValueObject>(T));
			}
			Out->SetArrayField(TEXT("slots"), Slots);
		}
		// --- UBlendSpace: axes + sample grid --------------------------------------------------
		else if (UBlendSpace* BlendSpace = Cast<UBlendSpace>(AnimAsset))
		{
			Out->SetStringField(TEXT("type"), TEXT("blendSpace"));
			TArray<TSharedPtr<FJsonValue>> Axes;
			// Fixed-size BlendParameters[3]; an axis with Min==Max is unused.
			for (int32 Index = 0; Index < 3; ++Index)
			{
				const FBlendParameter& Param = BlendSpace->GetBlendParameter(Index);
				if (Param.Min == Param.Max)
				{
					continue;
				}
				TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
				A->SetNumberField(TEXT("index"), Index);
				A->SetStringField(TEXT("name"), Param.DisplayName);
				A->SetNumberField(TEXT("min"), Param.Min);
				A->SetNumberField(TEXT("max"), Param.Max);
				Axes.Add(MakeShared<FJsonValueObject>(A));
			}
			Out->SetArrayField(TEXT("blendAxes"), Axes);

			TArray<TSharedPtr<FJsonValue>> Samples;
			for (const FBlendSample& Sample : BlendSpace->GetBlendSamples())
			{
				TSharedRef<FJsonObject> S = MakeShared<FJsonObject>();
				if (Sample.Animation)
				{
					S->SetStringField(TEXT("animation"), Sample.Animation->GetPathName());
				}
				S->SetNumberField(TEXT("x"), Sample.SampleValue.X);
				S->SetNumberField(TEXT("y"), Sample.SampleValue.Y);
				Samples.Add(MakeShared<FJsonValueObject>(S));
			}
			Out->SetArrayField(TEXT("samples"), Samples);
		}
		else if (Cast<UAnimComposite>(AnimAsset))
		{
			Out->SetStringField(TEXT("type"), TEXT("composite"));
		}
		else
		{
			Out->SetStringField(TEXT("type"), TEXT("other"));
		}
	}

	// --- list_animations ----------------------------------------------------
	//   in:  { filter?: "substring", skeleton?: "/Game/.../SK_Skeleton", limit?: 200 }
	//   out: { count, truncated, animations:[{ assetPath, class, name }] }
	//
	// Asset-registry only — does NOT load the assets, so it stays cheap on a large project.
	void H_list_animations(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("filter"), TEXT("skeleton"), TEXT("limit") },
			TEXT("filter (substring matched against the full object path), skeleton (substring matched against the registry's Skeleton tag), limit (default 200, max 5000)"),
			{ { TEXT("nameContains"), TEXT("the substring filter here is 'filter', and it matches the FULL object path, not just the asset name") },
			  { TEXT("path"), TEXT("there is no path/root parameter - put the folder in 'filter', e.g. filter:'/Game/Anims/'") },
			  { TEXT("count"), TEXT("'count' is an OUTPUT field - the cap is 'limit' (default 200, max 5000); read 'truncated' to see whether you hit it") } }))
		{
			return;
		}

		const FString Filter = JStr(In, TEXT("filter"));
		const FString SkeletonFilter = JStr(In, TEXT("skeleton"));
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 200), 1, 5000);

		IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();

		FARFilter ArFilter;
		ArFilter.bRecursiveClasses = true;   // sequences, montages, composites, blend spaces, aim offsets
		ArFilter.ClassPaths.Add(UAnimationAsset::StaticClass()->GetClassPathName());

		TArray<FAssetData> Assets;
		Registry.GetAssets(ArFilter, Assets);

		TArray<TSharedPtr<FJsonValue>> Arr;
		bool bTruncated = false;
		for (const FAssetData& Data : Assets)
		{
			const FString ObjectPath = Data.GetObjectPathString();
			if (!Filter.IsEmpty() && !ObjectPath.Contains(Filter))
			{
				continue;
			}
			if (!SkeletonFilter.IsEmpty())
			{
				// The registry tags the skeleton, so this filters without loading the asset.
				const FString Tagged = Data.GetTagValueRef<FString>(TEXT("Skeleton"));
				if (!Tagged.Contains(SkeletonFilter))
				{
					continue;
				}
			}
			if (Arr.Num() >= Limit)
			{
				bTruncated = true;
				break;
			}
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("assetPath"), ObjectPath);
			J->SetStringField(TEXT("name"), Data.AssetName.ToString());
			J->SetStringField(TEXT("class"), Data.AssetClassPath.ToString());
			Arr.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetBoolField(TEXT("truncated"), bTruncated);   // never let a cap look like completeness
		Out->SetArrayField(TEXT("animations"), Arr);
	}

	// --- add_anim_node ------------------------------------------------------
	//   in:  { graphId, nodeClass (a UAnimGraphNode_* class), x?, y? }
	//   out: { node:{...}, nodeClass, graph }
	//
	// ONE endpoint for the whole UAnimGraphNode_* family rather than one per node type. That works
	// because UAnimGraphNode_Base derives from UK2Node (AnimGraphNode_Base.h:194), so an anim node
	// places through exactly the same PlaceAndInit path as every K2 node, and its pins are ordinary
	// UEdGraphPins - connect_pins, move_node, get_node and remove_node all already apply.
	//
	// The pose data lives in the node's `Node` member (an FAnimNode_* struct), NOT on pins: a
	// SequencePlayer's animation is `Node.Sequence`, a Slot's name is `Node.SlotName`. Set those with
	// set_property on the returned node, which is why this endpoint does not try to take them itself -
	// there are dozens of node types and each has a different struct.
	void H_add_anim_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("nodeClass"), TEXT("class"), TEXT("x"), TEXT("y") },
			TEXT("graphId (the AnimGraph or a state/transition graph inside it), nodeClass (alias: class) - any UAnimGraphNode_* class, x/y (optional layout)"),
			{ { TEXT("sequence"), TEXT("set the animation afterwards with set_property propertyPath=Node.Sequence on the returned node - the field differs per node type") },
			  { TEXT("slotName"), TEXT("set it afterwards with set_property propertyPath=Node.SlotName on the returned node") },
			  { TEXT("blueprintId"), TEXT("a node is added to a GRAPH; pass graphId (list_graphs shows them, e.g. \"AnimGraph\")") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph) { return; }

		UClass* NodeClass = ResolveClassStrictField(In, { TEXT("nodeClass"), TEXT("class") }, Blueprint, Out);
		if (!NodeClass) { return; }

		if (!NodeClass->IsChildOf(UAnimGraphNode_Base::StaticClass()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a UAnimGraphNode_* class. Anim graph nodes are things like AnimGraphNode_SequencePlayer, ")
				TEXT("AnimGraphNode_Slot, AnimGraphNode_StateMachine, AnimGraphNode_BlendSpacePlayer. For ordinary K2 nodes use add_function_call and friends."),
				*NodeClass->GetName()));
			return;
		}
		if (NodeClass->HasAnyClassFlags(CLASS_Abstract))
		{
			Fail(Out, FString::Printf(TEXT("'%s' is abstract and cannot be spawned"), *NodeClass->GetName()));
			return;
		}

		// THIS GUARD USED TO CHECK THE BLUEPRINT AND THE COMMENT PROMISED THE GRAPH, AND THAT GAP KILLED
		// THE EDITOR. An Animation Blueprint has BOTH an AnimGraph and an EventGraph, so
		//   add_anim_node { graphId: <the ABP's EventGraph>, nodeClass: AnimGraphNode_StateMachine }
		// passed a blueprint-level check and went straight into PlaceAndInit. PostPlacedNewNode on a
		// state machine builds a name validator that does CastChecked<UAnimationGraph>(GetGraph())
		// (AnimGraphNode_StateMachineBase.cpp:46), the cast fails on an EventGraph, and CastChecked
		// TERMINATES THE PROCESS rather than returning null:
		//   Fatal error: Cast of EdGraph ...:EventGraph to AnimationGraph failed
		// Not an error response - a dead editor, mid-request. See PM-013.
		//
		// So the check is on the GRAPH, which is what the node actually touches. The blueprint check
		// stays as the first arm because it produces the more useful message for the common mistake of
		// aiming at an ordinary Blueprint.
		if (!Blueprint->IsA<UAnimBlueprint>())
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not an Animation Blueprint, so it has no AnimGraph to hold anim nodes. ")
				TEXT("Create one with create_blueprint blueprintType=AnimBlueprint skeleton=<USkeleton path>."),
				*Blueprint->GetName()));
			return;
		}
		if (!Graph->GetSchema() || !Graph->GetSchema()->IsA<UAnimationGraphSchema>())
		{
			Fail(Out, FString::Printf(
				TEXT("graph '%s' is not an animation graph - it is a %s. An Animation Blueprint has an ")
				TEXT("EventGraph as well as an AnimGraph, and anim nodes belong ONLY in the AnimGraph or a ")
				TEXT("state/transition graph inside it. Placing one here would terminate the editor: the ")
				TEXT("node's PostPlacedNewNode CastChecks its graph to UAnimationGraph and a failed ")
				TEXT("CastChecked is fatal, not an error. Pass the AnimGraph's graphId - list_graphs shows ")
				TEXT("it. NOTHING was created."),
				*Graph->GetName(),
				Graph->GetSchema() ? *Graph->GetSchema()->GetClass()->GetName() : TEXT("graph with no schema")));
			return;
		}

		UAnimGraphNode_Base* Node = NewObject<UAnimGraphNode_Base>(Graph, NodeClass, NAME_None, RF_Transactional);
		if (!Node) { Fail(Out, FString::Printf(TEXT("failed to construct '%s'"), *NodeClass->GetName())); return; }

		PlaceAndInit(Graph, Node, JInt(In, TEXT("x"), 0), JInt(In, TEXT("y"), 0));
		MarkStructural(Blueprint);

		EmitNode(Out, Node);
		Out->SetStringField(TEXT("nodeClass"), NodeClass->GetPathName());
		Out->SetStringField(TEXT("graph"), Graph->GetName());
		Out->SetStringField(TEXT("note"),
			TEXT("pose data lives on the node's Node member, not on pins - e.g. set_property propertyPath=Node.Sequence (SequencePlayer) or Node.SlotName (Slot). Wire poses with connect_pins as normal."));
	}
	// --- PORTED FROM THE CURFEW (UE 5.7) DEPLOYMENT, 2026-08-26 ---------------
	// These two were written against UE 5.7 in D:/RoguelikeDealerGame, where MifBridge is VENDORED
	// rather than cloned, so they never reached this repo. Found by diffing the two endpoint sets:
	// 46 endpoints here had never been compiled against 5.7, and these 2 existed only there.
	// Work was being lost in both directions.
	//
	// Ported verbatim. Every engine call they make - UBlendSpace::AddSample/DeleteSample/ResampleData/
	// ValidateSampleData and USkeleton::GetBoneTranslationRetargetingMode/GetReferenceSkeleton - exists
	// unchanged in 5.3, and Curfew's include set is a subset of this file's, so nothing needed adapting.

	// --- set_blendspace_samples ---------------------------------------------
	//   in:  { assetPath, samples:[{ animation, x, y? }], clear? (default true) }
	//   out: { path, sampleCount, samples:[{ animation, x, y }] }
	//
	// WHY THIS EXISTS: UBlendSpace::AddSample is ENGINE_API C++ but is NOT exposed to Unreal's
	// Python bindings — UBlendSpace1D has no add_sample attribute at all. So a blend space could
	// be CREATED from a script and then never filled, which is a blend space that silently
	// outputs nothing. That is a real hole: locomotion is the first animation any project needs
	// and it cannot be automated without this.
	//
	// The AXIS is deliberately not taken here. BlendParameters is a UPROPERTY, so set_property
	// with propertyPath=BlendParameters[0].Max already reaches it, and duplicating that would be
	// a second way to do one thing.
	//
	// Samples must be UAnimSequence: AddSample takes that type specifically, and a montage or a
	// composite in a blend space is not a thing the engine supports.
	void H_set_blendspace_samples(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("blendSpace"), TEXT("samples"), TEXT("clear") },
			TEXT("assetPath (aliases: path, blendSpace), samples[] of { animation, x, y? }, clear (default true)"),
			{ { TEXT("axis"), TEXT("set the axis with set_property propertyPath=BlendParameters[0].Max (also .Min, .DisplayName, .GridNum)") },
			  { TEXT("animation"), TEXT("samples is an ARRAY of objects, each with its own animation and x") } }))
		{
			return;
		}

		FString Path;
		for (const TCHAR* Key : { TEXT("assetPath"), TEXT("path"), TEXT("blendSpace") })
		{
			if (In->TryGetStringField(Key, Path) && !Path.IsEmpty()) { break; }
		}
		if (Path.IsEmpty()) { Fail(Out, TEXT("assetPath is required")); return; }

		UObject* Asset = LoadAssetLoose(Path);
		UBlendSpace* BS = Cast<UBlendSpace>(Asset);
		if (!BS)
		{
			Fail(Out, FString::Printf(TEXT("'%s' is not a UBlendSpace (loaded: %s)"),
				*Path, Asset ? *Asset->GetClass()->GetName() : TEXT("nothing")));
			return;
		}

		const TArray<TSharedPtr<FJsonValue>>* Samples = nullptr;
		if (!In->TryGetArrayField(TEXT("samples"), Samples) || !Samples)
		{
			Fail(Out, TEXT("samples[] is required - each entry { animation, x, y? }"));
			return;
		}

		BS->Modify();

		// Clear by default. Re-running a setup script should converge on the same blend space
		// rather than stacking a second copy of every sample on top of the first.
		bool bClear = true;
		In->TryGetBoolField(TEXT("clear"), bClear);
		if (bClear)
		{
			for (int32 i = BS->GetBlendSamples().Num() - 1; i >= 0; --i)
			{
				BS->DeleteSample(i);
			}
		}

		TArray<TSharedPtr<FJsonValue>> Added;
		TArray<FString> Rejected;

		for (const TSharedPtr<FJsonValue>& V : *Samples)
		{
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (!V.IsValid() || !V->TryGetObject(Obj) || !Obj) { continue; }

			FString AnimPath;
			(*Obj)->TryGetStringField(TEXT("animation"), AnimPath);
			UAnimSequence* Seq = Cast<UAnimSequence>(LoadAssetLoose(AnimPath));
			if (!Seq)
			{
				Rejected.Add(FString::Printf(TEXT("%s: not a UAnimSequence"), *AnimPath));
				continue;
			}

			// A sample whose skeleton does not match is accepted by AddSample and then produces
			// a broken pose at runtime, which is a miserable thing to debug. Refuse it here.
			if (BS->GetSkeleton() && Seq->GetSkeleton() != BS->GetSkeleton())
			{
				Rejected.Add(FString::Printf(TEXT("%s: skeleton '%s' does not match the blend space's '%s'"),
					*Seq->GetName(),
					Seq->GetSkeleton() ? *Seq->GetSkeleton()->GetName() : TEXT("none"),
					*BS->GetSkeleton()->GetName()));
				continue;
			}

			double X = 0.0, Y = 0.0;
			(*Obj)->TryGetNumberField(TEXT("x"), X);
			(*Obj)->TryGetNumberField(TEXT("y"), Y);

			const int32 Index = BS->AddSample(Seq, FVector(X, Y, 0.0));
			if (Index == INDEX_NONE)
			{
				// Almost always an out-of-range value: AddSample refuses a sample outside the
				// axis, so say so rather than reporting a bare failure.
				Rejected.Add(FString::Printf(
					TEXT("%s: AddSample refused (%.2f, %.2f) - usually outside the axis range; widen it with set_property BlendParameters[0].Min/.Max first"),
					*Seq->GetName(), X, Y));
				continue;
			}

			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("animation"), Seq->GetPathName());
			J->SetNumberField(TEXT("x"), X);
			J->SetNumberField(TEXT("y"), Y);
			Added.Add(MakeShared<FJsonValueObject>(J));
		}

		// Rebuilds the triangulation / grid. Without it the samples exist but nothing interpolates
		// between them, which looks exactly like the samples not having been added.
		BS->ValidateSampleData();

		// ResampleData is THE call, and its absence is what produced a T-pose that survived
		// every other check.
		//
		// Its own comment: "Runs triangulation/segmentation to update our grid and
		// BlendSpaceData structures." A blend space evaluates by looking up GridSamples, NOT
		// SampleData - so samples without a rebuilt grid interpolate to nothing and the node
		// emits no pose at all. The mesh then falls back to its reference pose, which reads as
		// a T-pose and looks for all the world like a missing animation or a broken skeleton.
		// Everything else verified clean while this was wrong: three samples on disk, correct
		// skeletons everywhere, a correctly wired graph compiling with zero errors.
		BS->ResampleData();

		// PostEditChangeProperty, not just MarkPackageDirty.
		//
		// Learned the hard way: the first run of this endpoint reported three samples, the
		// in-memory asset genuinely had three, the save reported success - and the samples were
		// not in the file. The axis, which had been set through set_property (and therefore got
		// a PostEditChangeProperty), persisted from the same session. SampleData is a
		// UPROPERTY with editor-side derived data hanging off it; writing it without telling
		// the property system leaves the asset in a state the serialiser can drop.
		if (FProperty* SampleProp = UBlendSpace::StaticClass()->FindPropertyByName(TEXT("SampleData")))
		{
			FPropertyChangedEvent Changed(SampleProp, EPropertyChangeType::ValueSet);
			BS->PostEditChangeProperty(Changed);
		}

		BS->MarkPackageDirty();

		// RECONCILE WHAT WE CLAIM AGAINST WHAT THE ASSET ACTUALLY HOLDS.
		//
		// THE REAL HAZARD IS bIsValid, NOT DELETION. An earlier version of this comment claimed
		// ValidateSampleData silently DELETES samples this call added, via
		//     if (IsSameSamplePoint(...)) { SampleData.RemoveAt(Comparison); }
		// That cannot happen through this endpoint, and the reason is worth writing down so nobody
		// "fixes" it again: AddSample -> ValidateSampleValue already calls
		// IsTooCloseToExistingSamplePoint, which calls IsSameSamplePoint - the SAME predicate at the
		// SAME threshold. So a duplicate point is refused by AddSample and lands in rejected[]; it
		// never reaches the dedup pass. The deletion path is kept below as belt-and-braces for samples
		// that arrived some other way, not because this endpoint can trip it.
		//
		// What ValidateSampleData ACTUALLY does to samples we added is mark them INVALID without
		// removing them:
		//     Sample.bIsValid = bAnimationExists && bSampleInBounds && bSampleIsUnique;   // :36
		//     Sample.bIsValid = ValidateSampleValue(Sample.SampleValue, SampleIndex);     // :122
		// An invalid sample is STILL IN SampleData - it counts toward GetBlendSamples().Num(), it
		// survives any position-matching reconciliation - and contributes nothing to the blend. So
		// reporting it as added, which is what this endpoint did, is telling the caller the sample
		// works when the asset says it does not. bIsValid: 5.3 BlendSpace.h:182, 5.7 :194.
		const TArray<FBlendSample>& Surviving = BS->GetBlendSamples();
		TArray<TSharedPtr<FJsonValue>> Kept, Dropped;
		int32 InvalidCount = 0;
		for (const TSharedPtr<FJsonValue>& V : Added)
		{
			const TSharedPtr<FJsonObject>* Row = nullptr;
			if (!V.IsValid() || !V->TryGetObject(Row) || !Row) { continue; }
			FString AnimPath; double X = 0.0, Y = 0.0;
			(*Row)->TryGetStringField(TEXT("animation"), AnimPath);
			(*Row)->TryGetNumberField(TEXT("x"), X);
			(*Row)->TryGetNumberField(TEXT("y"), Y);

			bool bStillThere = false;
			bool bValid = false;
			for (const FBlendSample& Sample : Surviving)
			{
				// Match on animation AND position: the same clip may legitimately appear at several
				// points, so the clip alone does not identify a sample. KINDA_SMALL_NUMBER rather than
				// exact equality because the value made a round trip through double.
				if (Sample.Animation && Sample.Animation->GetPathName() == AnimPath
					&& FMath::IsNearlyEqual(Sample.SampleValue.X, X, KINDA_SMALL_NUMBER)
					&& FMath::IsNearlyEqual(Sample.SampleValue.Y, Y, KINDA_SMALL_NUMBER))
				{
					bStillThere = true;
					// THE field that matters. A present-but-invalid sample is on the asset and does
					// nothing, which is indistinguishable from a working one unless it is reported.
					bValid = Sample.bIsValid != 0;
					break;
				}
			}
			if (bStillThere)
			{
				(*Row)->SetBoolField(TEXT("valid"), bValid);
				if (!bValid) { ++InvalidCount; }
			}
			(bStillThere ? Kept : Dropped).Add(V);
		}

		Out->SetStringField(TEXT("path"), BS->GetPathName());
		Out->SetNumberField(TEXT("sampleCount"), Surviving.Num());
		// samples[] now lists only what is REALLY on the asset, so it can no longer disagree with
		// sampleCount about the same call.
		Out->SetArrayField(TEXT("samples"), Kept);
		Out->SetNumberField(TEXT("addedCount"), Kept.Num());
		// Reported ALWAYS, not only when nonzero, so a caller can assert on it rather than having to
		// notice a field's absence.
		Out->SetNumberField(TEXT("invalidCount"), InvalidCount);
		if (InvalidCount > 0)
		{
			Out->SetStringField(TEXT("invalidNote"), FString::Printf(
				TEXT("%d sample(s) are ON the asset but marked INVALID by ValidateSampleData, so they "
					 "contribute nothing to the blend. bIsValid is set from "
					 "bAnimationExists && bSampleInBounds && bSampleIsUnique - so the usual causes are a "
					 "missing animation or a position outside the axis range. They are counted in "
					 "sampleCount because the engine still stores them; check `valid` on each sample."),
				InvalidCount));
		}
		if (Dropped.Num() > 0)
		{
			Out->SetArrayField(TEXT("droppedByValidation"), Dropped);
			Out->SetStringField(TEXT("droppedNote"), FString::Printf(
				TEXT("%d sample(s) were accepted by AddSample and then REMOVED by ValidateSampleData, "
					 "which deletes any sample sharing a point with another. Move them to distinct "
					 "(x, y) positions. They are not on the asset and were not counted in samples[]."),
				Dropped.Num()));
		}
		if (Rejected.Num() > 0)
		{
			// ok:true with rejected entries would read as success. Report them loudly.
			TArray<TSharedPtr<FJsonValue>> R;
			for (const FString& S : Rejected) { R.Add(MakeShared<FJsonValueString>(S)); }
			Out->SetArrayField(TEXT("rejected"), R);
		}
		Out->SetStringField(TEXT("note"),
			TEXT("save_package to persist. Set the axis with set_property propertyPath=BlendParameters[0].Max"));
	}

	// --- set_bone_translation_retargeting -------------------------------------
	//   in:  { skeletonPath, boneName, mode, childrenToo? }
	//   out: { skeleton, bone, boneIndex, before, after }
	//
	// WHY THIS EXISTS: USkeleton::BoneTree is a read-only UPROPERTY from Python, so a bone's
	// translation retargeting mode simply cannot be set from a script - and that mode is the
	// only lever that stops a bone using the ANIMATION's translation.
	//
	// The concrete failure it fixes, measured 2026-08-24: retargeting the GASP locomotion put
	// the clips' travel on the PELVIS instead of the root (the retarget root is the pelvis and
	// the Root Motion op was disabled), while bForceRootLock pinned the root at the origin. So
	// the body walked 831 cm away from the capsule over a 4.000 s walk cycle and snapped back
	// at every loop. Setting the pelvis to SKELETON makes it take translation from the
	// reference pose, which removes the drift outright without touching a single clip.
	//
	// Modes: Animation, Skeleton, AnimationScaled, AnimationRelative, OrientAndScale.
	void H_set_bone_translation_retargeting(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("skeletonPath"), TEXT("path"), TEXT("boneName"), TEXT("bone"), TEXT("mode"), TEXT("childrenToo") },
			TEXT("skeletonPath (alias: path), boneName (alias: bone), mode {Animation|Skeleton|AnimationScaled|AnimationRelative|OrientAndScale}, childrenToo (default false)"),
			{}))
		{
			return;
		}

		FString Path;
		for (const TCHAR* Key : { TEXT("skeletonPath"), TEXT("path") })
		{
			if (In->TryGetStringField(Key, Path) && !Path.IsEmpty()) { break; }
		}
		if (Path.IsEmpty()) { Fail(Out, TEXT("skeletonPath is required")); return; }

		UObject* Asset = LoadAssetLoose(Path);
		USkeleton* Skel = Cast<USkeleton>(Asset);
		if (!Skel)
		{
			Fail(Out, FString::Printf(TEXT("'%s' is not a USkeleton (loaded: %s)"),
				*Path, Asset ? *Asset->GetClass()->GetName() : TEXT("nothing")));
			return;
		}

		FString BoneName;
		for (const TCHAR* Key : { TEXT("boneName"), TEXT("bone") })
		{
			if (In->TryGetStringField(Key, BoneName) && !BoneName.IsEmpty()) { break; }
		}
		if (BoneName.IsEmpty()) { Fail(Out, TEXT("boneName is required")); return; }

		const int32 BoneIndex = Skel->GetReferenceSkeleton().FindBoneIndex(FName(*BoneName));
		if (BoneIndex == INDEX_NONE)
		{
			Fail(Out, FString::Printf(TEXT("bone '%s' not found on %s"), *BoneName, *Skel->GetName()));
			return;
		}

		FString ModeStr = TEXT("Skeleton");
		In->TryGetStringField(TEXT("mode"), ModeStr);
		EBoneTranslationRetargetingMode::Type Mode = EBoneTranslationRetargetingMode::Skeleton;
		if      (ModeStr.Equals(TEXT("Animation"), ESearchCase::IgnoreCase))         { Mode = EBoneTranslationRetargetingMode::Animation; }
		else if (ModeStr.Equals(TEXT("Skeleton"), ESearchCase::IgnoreCase))          { Mode = EBoneTranslationRetargetingMode::Skeleton; }
		else if (ModeStr.Equals(TEXT("AnimationScaled"), ESearchCase::IgnoreCase))   { Mode = EBoneTranslationRetargetingMode::AnimationScaled; }
		else if (ModeStr.Equals(TEXT("AnimationRelative"), ESearchCase::IgnoreCase)) { Mode = EBoneTranslationRetargetingMode::AnimationRelative; }
		else if (ModeStr.Equals(TEXT("OrientAndScale"), ESearchCase::IgnoreCase))    { Mode = EBoneTranslationRetargetingMode::OrientAndScale; }
		else
		{
			Fail(Out, FString::Printf(TEXT("unknown mode '%s'"), *ModeStr));
			return;
		}

		auto ModeName = [](EBoneTranslationRetargetingMode::Type M) -> FString
		{
			switch (M)
			{
			case EBoneTranslationRetargetingMode::Animation:         return TEXT("Animation");
			case EBoneTranslationRetargetingMode::Skeleton:          return TEXT("Skeleton");
			case EBoneTranslationRetargetingMode::AnimationScaled:   return TEXT("AnimationScaled");
			case EBoneTranslationRetargetingMode::AnimationRelative: return TEXT("AnimationRelative");
			case EBoneTranslationRetargetingMode::OrientAndScale:    return TEXT("OrientAndScale");
			default:                                                 return TEXT("Unknown");
			}
		};

		const FString Before = ModeName(Skel->GetBoneTranslationRetargetingMode(BoneIndex));

		bool bChildrenToo = false;
		In->TryGetBoolField(TEXT("childrenToo"), bChildrenToo);

		Skel->Modify();
		Skel->SetBoneTranslationRetargetingMode(BoneIndex, Mode, bChildrenToo);
		Skel->MarkPackageDirty();

		// Read it BACK off the asset rather than reporting what we asked for - ok:true has
		// never been proof in this project.
		const FString After = ModeName(Skel->GetBoneTranslationRetargetingMode(BoneIndex));

		Out->SetStringField(TEXT("skeleton"), Skel->GetPathName());
		Out->SetStringField(TEXT("bone"), BoneName);
		Out->SetNumberField(TEXT("boneIndex"), BoneIndex);
		Out->SetStringField(TEXT("before"), Before);
		Out->SetStringField(TEXT("after"), After);
		Out->SetBoolField(TEXT("changed"), Before != After);
		Out->SetStringField(TEXT("note"), TEXT("save_package to persist."));
	}


	// --- add_blackboard_key ---------------------------------------------------------------------
	//   in:  { path, name, type, instanceSynced?, category?, confirm }
	//   out: { blackboard, name, type, keyCount }
	// Bucket: MUTATES the blackboard asset in memory. Nothing is saved.
	//
	// THE BOUNDED PIECE OF "BEHAVIOR TREE AUTHORING", chosen rather than stumbled into.
	//
	// The spec item was "behavior tree authoring - 2 reads, 0 writes". Authoring the TREE itself means
	// constructing UBTComposite / UBTDecorator / UBTService / UBTTask objects and wiring their parent
	// links by hand - a graph editor's job, and the same argument that declined MetaSound graph
	// authoring. Building half of it would produce trees that look right in the editor and assert at
	// runtime, which is worse than not having it.
	//
	// A BLACKBOARD KEY is the opposite: a flat entry in an array, with a name and a key-type object,
	// and it is the thing you actually cannot proceed without. Every decorator that tests a condition
	// tests a blackboard key; a tree cannot reference a key that does not exist, so adding one is the
	// FIRST step of authoring anything and the one most worth automating.
	//
	// Verified in BOTH trees: FBlackboardEntry is byte-identical (EntryName, EntryCategory, KeyType,
	// bInstanceSynced), UBlackboardData::Keys is a plain TArray of it, and GetKeyID is AIMODULE_API
	// in both.
	//
	// THE KEY TYPE IS AN OBJECT, NOT AN ENUM, which is the part that catches people. KeyType is a
	// UBlackboardKeyType* - an instanced UObject owned by the blackboard - so adding a key means
	// CONSTRUCTING one, not assigning an enum value. A null KeyType is accepted by the array and makes
	// the key useless: the editor shows it, and nothing can read or write it.
	void H_add_blackboard_key(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("blackboard"), TEXT("name"), TEXT("key"),
			  TEXT("type"), TEXT("keyType"), TEXT("instanceSynced"), TEXT("category"),
			  TEXT("confirm") },
			TEXT("path (a BlackboardData asset); name (alias: key); type (alias: keyType) - Bool, Int, "
				 "Float, String, Name, Vector, Rotator, Object, Class, Enum; instanceSynced (default "
				 "false); category; confirm:true"),
			{ { TEXT("behaviorTree"), TEXT("keys live on the BLACKBOARD asset, not on the tree - describe_behavior_tree reports which blackboard a tree uses") },
			  { TEXT("value"), TEXT("a blackboard key has no value at author time - values exist per running instance") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("add_blackboard_key needs confirm:true - it modifies a shared blackboard "
						   "asset that every tree using it will see. NOTHING was changed."));
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("blackboard") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a BlackboardData asset. describe_behavior_tree reports "
						   "the blackboard a tree uses. NOTHING was changed."));
			return;
		}
		UBlackboardData* BB = LoadObject<UBlackboardData>(nullptr, *Path, nullptr,
														  LOAD_NoWarn | LOAD_Quiet);
		if (!BB)
		{
			Fail(Out, FString::Printf(TEXT("no BlackboardData at '%s'. NOTHING was changed."), *Path));
			return;
		}

		const FString KeyName = JStrAny(In, { TEXT("name"), TEXT("key") });
		if (KeyName.IsEmpty())
		{
			Fail(Out, TEXT("name is required. NOTHING was changed."));
			return;
		}

		// ALREADY PRESENT? Checked against the PARENT CHAIN too, not just this asset's own array.
		// Blackboards inherit, and a key that shadows an inherited one of a different type is accepted
		// by the array and then resolves unpredictably depending on which the decorator looked up.
		if (BB->GetKeyID(FName(*KeyName)) != FBlackboard::InvalidKey)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' already exists on this blackboard or one it inherits from. Adding it again "
					 "would SHADOW the inherited key, and a decorator resolving the name could get "
					 "either. NOTHING was changed."), *KeyName));
			return;
		}

		// The type NAME maps to a UBlackboardKeyType SUBCLASS. Spelled out rather than resolved from a
		// string, so a typo is a refusal listing the real options instead of a null KeyType that the
		// editor happily shows and nothing can use.
		const FString TypeStr = JStrAny(In, { TEXT("type"), TEXT("keyType") });
		UClass* TypeClass = nullptr;
		if (TypeStr.Equals(TEXT("Bool"), ESearchCase::IgnoreCase))          { TypeClass = UBlackboardKeyType_Bool::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Int"), ESearchCase::IgnoreCase))      { TypeClass = UBlackboardKeyType_Int::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Float"), ESearchCase::IgnoreCase))    { TypeClass = UBlackboardKeyType_Float::StaticClass(); }
		else if (TypeStr.Equals(TEXT("String"), ESearchCase::IgnoreCase))   { TypeClass = UBlackboardKeyType_String::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Name"), ESearchCase::IgnoreCase))     { TypeClass = UBlackboardKeyType_Name::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Vector"), ESearchCase::IgnoreCase))   { TypeClass = UBlackboardKeyType_Vector::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Rotator"), ESearchCase::IgnoreCase))  { TypeClass = UBlackboardKeyType_Rotator::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Object"), ESearchCase::IgnoreCase))   { TypeClass = UBlackboardKeyType_Object::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Class"), ESearchCase::IgnoreCase))    { TypeClass = UBlackboardKeyType_Class::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Enum"), ESearchCase::IgnoreCase))     { TypeClass = UBlackboardKeyType_Enum::StaticClass(); }
		if (!TypeClass)
		{
			Fail(Out, FString::Printf(
				TEXT("unknown key type '%s'. Use one of: Bool, Int, Float, String, Name, Vector, "
					 "Rotator, Object, Class, Enum. A key with no type is accepted by the asset and "
					 "then cannot be read or written by anything, so this refuses rather than "
					 "creating one. NOTHING was changed."), *TypeStr));
			return;
		}

		BB->Modify();
		FBlackboardEntry Entry;
		Entry.EntryName = FName(*KeyName);
		Entry.EntryCategory = FName(*JStr(In, TEXT("category")));
		Entry.bInstanceSynced = JBool(In, TEXT("instanceSynced"), false) ? 1 : 0;
		// OUTERED TO THE BLACKBOARD. A key type outered anywhere else is not saved with the asset and
		// comes back null on the next load - a key that works until the editor restarts.
		Entry.KeyType = NewObject<UBlackboardKeyType>(BB, TypeClass);
		BB->Keys.Add(Entry);

		// The engine caches key IDs across the parent chain; without this the new key is in the array
		// and GetKeyID still cannot find it, which reads as the add having silently failed.
		BB->UpdateKeyIDs();
		if (UPackage* Pkg = BB->GetOutermost()) { Pkg->MarkPackageDirty(); }

		// READ BACK through GetKeyID rather than trusting the Add - the house rule, and here it also
		// proves UpdateKeyIDs did its job.
		const bool bResolves = BB->GetKeyID(FName(*KeyName)) != FBlackboard::InvalidKey;
		Out->SetStringField(TEXT("blackboard"), BB->GetPathName());
		Out->SetStringField(TEXT("name"), KeyName);
		Out->SetStringField(TEXT("type"), TypeClass->GetName());
		Out->SetNumberField(TEXT("keyCount"), BB->Keys.Num());
		Out->SetBoolField(TEXT("resolves"), bResolves);
		if (!bResolves)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' was appended to Keys but GetKeyID still cannot resolve it, so no decorator "
					 "would find it. Treat the key as unusable."), *KeyName));
			return;
		}
		Out->SetStringField(TEXT("note"),
			TEXT("nothing was saved. Every behavior tree using this blackboard now sees the key."));
		UE_LOG(LogMifBridge, Log, TEXT("add_blackboard_key: %s.%s (%s)"),
			*BB->GetName(), *KeyName, *TypeClass->GetName());
	}

	// =======================================================================
	// NOTIFY AUTHORING - add_anim_notify / remove_anim_notify /
	//                   add_anim_notify_track / remove_anim_notify_track
	// =======================================================================
	//
	// THE READ HALF HAS BEEN HERE ALL ALONG. describe_animation emits every notify in full through
	// SerializeNotify above, and nothing could create one - the textbook read-with-no-write. Notify
	// authoring is the single most common animation-asset edit: footstep sounds, hit windows, VFX
	// spawns, montage branching points are all notifies.
	//
	// THERE IS A WORKAROUND FOR ONE OF THE THREE CASES AND IT IS WORTH NAMING. UAnimSequenceBase::
	// Notifies is a plain UPROPERTY() TArray with no EditFixedSize, and edit_container only gates on
	// CPF_EditFixedSize - so edit_container{propertyPath:"Notifies", operation:"add"} really does
	// append a default FAnimNotifyEvent today, and NotifyName / TrackIndex / Duration and the
	// FAnimLinkableElement time fields are all writable through set_property. A name-only "skeleton
	// notify" is therefore hand-buildable in about seven calls. It will not appear in the notify
	// panel until a save-and-reload, because nothing calls RefreshCacheData. The CLASS-BACKED
	// AnimNotify and AnimNotifyState cases - the common ones - stay genuinely unreachable that way,
	// which is why these endpoints exist.
	//
	// COOKED ASSETS: THE TRACK ARRAY IS EDITOR-ONLY AND THE NOTIFIES ARE NOT. UAnimSequenceBase::
	// Notifies and UAnimSequence::AuthoredSyncMarkers are plain UPROPERTYs and survive the cook;
	// AnimNotifyTracks is WITH_EDITORONLY_DATA and does not. So a cooked-loaded sequence opens with
	// notifies whose TrackIndex points into an EMPTY track array, and the first call that triggers
	// RefreshCacheData runs its busted-index repair: it synthesises tracks and REWRITES TrackIndex on
	// every existing notify (AnimSequenceBase.cpp), and pops a Message Log tab for any notify failing
	// CanBePlaced. Not a crash, but a mutation nobody asked for, so it is detected up front and
	// reported as tracksSynthesized / trackIndexRewritten rather than happening silently.

	// --- the crash guard ----------------------------------------------------
	//
	// A HARD EDITOR CRASH, verified in the engine source, not inferred. UAnimSequence::
	// RefreshCacheData (AnimSequence.cpp:3421-3435) walks AuthoredSyncMarkers and, for a marker whose
	// TrackIndex is out of range, takes this else branch:
	//
	//     ensureMsgf(0, TEXT("AnimNotifyTrack: Wrong indices found"));
	//     AnimNotifyTracks[0].SyncMarkers.Add(&SyncMarker);
	//
	// AnimNotifyTracks[0] with NO bounds check. If that array is empty and AuthoredSyncMarkers is
	// not, it is TArray::operator[] on an empty array - a check() failure, which takes the editor out
	// with it. RemoveAnimationNotifyTrack removes the track and THEN calls RefreshCacheData, so
	// deleting the last remaining track on a sequence that still holds sync markers reaches it.
	//
	// Guarded before the engine is touched, which is the house rule for anything that can crash.
	bool MifNotifyTrackRemovalIsSafe(UAnimSequenceBase* Seq, int32 TracksNow, FString& OutWhy)
	{
		if (TracksNow > 1)
		{
			return true;
		}
		const UAnimSequence* AsSeq = Cast<UAnimSequence>(Seq);
		const int32 Markers = AsSeq ? AsSeq->AuthoredSyncMarkers.Num() : 0;
		if (Markers == 0)
		{
			return true;
		}
		OutWhy = FString::Printf(
			TEXT("removing this track would leave the sequence with ZERO notify tracks while it "
				 "still has %d authored sync marker(s), and that CRASHES the editor - "
				 "UAnimSequence::RefreshCacheData reaches `AnimNotifyTracks[0].SyncMarkers.Add(...)` "
				 "with no bounds check for a marker whose TrackIndex is out of range "
				 "(AnimSequence.cpp:3431), which is TArray::operator[] on an empty array. Refused "
				 "before the engine was touched. Remove the sync markers first, or keep at least one "
				 "track"), Markers);
		return false;
	}

	UAnimSequenceBase* MifResolveAnimSeq(const TSharedRef<FJsonObject>& In,
										 const TSharedRef<FJsonObject>& Out, const TCHAR* Endpoint)
	{
		const FString Path = JStrAny(In, { TEXT("assetPath"), TEXT("path"), TEXT("asset") });
		if (Path.IsEmpty())
		{
			Fail(Out, FString::Printf(
				TEXT("%s needs assetPath (aliases: path, asset) - an AnimSequence, AnimMontage or "
					 "AnimComposite. NOTHING was changed."), Endpoint));
			return nullptr;
		}
		UObject* Obj = LoadAssetLenient(Path);
		UAnimSequenceBase* Seq = Cast<UAnimSequenceBase>(Obj);
		if (!Seq)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not an AnimSequence / AnimMontage / AnimComposite%s. NOTHING was "
					 "changed."), *Path,
				Obj ? *FString::Printf(TEXT(" (it is a %s)"), *Obj->GetClass()->GetName()) : TEXT("")));
			return nullptr;
		}
		return Seq;
	}

	/** Report the cooked-asset track situation into Out, and say whether RefreshCacheData is about
	 *  to rewrite things the caller did not ask about. */
	void MifNoteTrackState(UAnimSequenceBase* Seq, const TSharedRef<FJsonObject>& Out)
	{
		const int32 Tracks = Seq->AnimNotifyTracks.Num();
		Out->SetNumberField(TEXT("notifyTracks"), Tracks);
		if (Tracks == 0 && Seq->Notifies.Num() > 0)
		{
			Out->SetBoolField(TEXT("tracksSynthesized"), true);
			Out->SetBoolField(TEXT("trackIndexRewritten"), true);
			Out->SetStringField(TEXT("cookedTrackNote"), FString::Printf(
				TEXT("this sequence has %d notif(y/ies) and ZERO notify tracks, which is what a "
					 "COOKED asset looks like: Notifies is a plain UPROPERTY and survives the cook, "
					 "AnimNotifyTracks is editor-only and does not. RefreshCacheData has therefore "
					 "synthesised tracks and REWRITTEN TrackIndex on every existing notify - a "
					 "change you did not ask for, reported rather than left to be discovered. It "
					 "may also have opened a Message Log tab for any notify that failed "
					 "CanBePlaced."), Seq->Notifies.Num()));
		}
	}

	// --- add_anim_notify_track ----------------------------------------------
	void H_add_anim_notify_track(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("track") },
			TEXT("assetPath (aliases: path, asset); track - the NAME of the track to create"),
			{ { TEXT("trackName"), TEXT("spell it track") },
			  { TEXT("index"), TEXT("tracks are addressed by NAME here, not index") } }))
		{
			return;
		}
		UAnimSequenceBase* Seq = MifResolveAnimSeq(In, Out, TEXT("add_anim_notify_track"));
		if (!Seq) { return; }

		const FString Track = JStr(In, TEXT("track"));
		if (Track.IsEmpty())
		{
			Fail(Out, TEXT("track is required - the name of the track to create. NOTHING was changed."));
			return;
		}
		const FName TrackName(*Track);
		if (UAnimationBlueprintLibrary::IsValidAnimNotifyTrackName(Seq, TrackName))
		{
			Out->SetStringField(TEXT("assetPath"), Seq->GetPathName());
			Out->SetStringField(TEXT("track"), Track);
			Out->SetBoolField(TEXT("created"), false);
			Out->SetNumberField(TEXT("notifyTracks"), Seq->AnimNotifyTracks.Num());
			Out->SetStringField(TEXT("note"),
				TEXT("a track with that name already exists - nothing was created, and nothing "
					 "needed to be. created:false is not a failure."));
			return;
		}

		const int32 Before = Seq->AnimNotifyTracks.Num();
		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_AddNotifyTrack", "Add Anim Notify Track"));
		Seq->Modify();
		UAnimationBlueprintLibrary::AddAnimationNotifyTrack(Seq, TrackName);

		// READ BACK - AddAnimationNotifyTrack is void.
		if (!UAnimationBlueprintLibrary::IsValidAnimNotifyTrackName(Seq, TrackName))
		{
			Fail(Out, FString::Printf(
				TEXT("AddAnimationNotifyTrack reported nothing and '%s' still does not exist on "
					 "read-back. NOTHING usable was produced."), *Track));
			return;
		}
		Out->SetStringField(TEXT("assetPath"), Seq->GetPathName());
		Out->SetStringField(TEXT("track"), Track);
		Out->SetBoolField(TEXT("created"), true);
		Out->SetNumberField(TEXT("tracksBefore"), Before);
		MifNoteTrackState(Seq, Out);
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the asset is now dirty and NOTHING has been saved."));
	}

	// --- remove_anim_notify_track -------------------------------------------
	void H_remove_anim_notify_track(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("track"), TEXT("confirm") },
			TEXT("assetPath (aliases: path, asset); track - the NAME of the track to remove; "
				 "confirm:true, because removing a track removes every notify on it"),
			{ { TEXT("trackName"), TEXT("spell it track") } }))
		{
			return;
		}
		UAnimSequenceBase* Seq = MifResolveAnimSeq(In, Out, TEXT("remove_anim_notify_track"));
		if (!Seq) { return; }

		const FString Track = JStr(In, TEXT("track"));
		const FName TrackName(*Track);
		if (Track.IsEmpty() || !UAnimationBlueprintLibrary::IsValidAnimNotifyTrackName(Seq, TrackName))
		{
			Fail(Out, FString::Printf(
				TEXT("no notify track named '%s' on this sequence (it has %d). NOTHING was changed."),
				*Track, Seq->AnimNotifyTracks.Num()));
			return;
		}

		// THE CRASH GUARD, before anything is touched.
		FString Why;
		if (!MifNotifyTrackRemovalIsSafe(Seq, Seq->AnimNotifyTracks.Num(), Why))
		{
			Fail(Out, Why + TEXT(". NOTHING was changed."));
			return;
		}

		if (!JBool(In, TEXT("confirm"), false))
		{
			int32 OnTrack = 0;
			for (const FAnimNotifyEvent& E : Seq->Notifies)
			{
				if (Seq->AnimNotifyTracks.IsValidIndex(E.TrackIndex)
					&& Seq->AnimNotifyTracks[E.TrackIndex].TrackName == TrackName)
				{
					++OnTrack;
				}
			}
			Fail(Out, FString::Printf(
				TEXT("removing track '%s' also removes the %d notif(y/ies) on it, and this endpoint "
					 "cannot put them back. Pass confirm:true. NOTHING was changed."), *Track, OnTrack));
			return;
		}

		const int32 NotifiesBefore = Seq->Notifies.Num();
		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_RemoveNotifyTrack", "Remove Anim Notify Track"));
		Seq->Modify();
		UAnimationBlueprintLibrary::RemoveAnimationNotifyTrack(Seq, TrackName);

		if (UAnimationBlueprintLibrary::IsValidAnimNotifyTrackName(Seq, TrackName))
		{
			Fail(Out, FString::Printf(
				TEXT("RemoveAnimationNotifyTrack ran and '%s' still exists on read-back."), *Track));
			return;
		}
		Out->SetStringField(TEXT("assetPath"), Seq->GetPathName());
		Out->SetStringField(TEXT("track"), Track);
		Out->SetBoolField(TEXT("removed"), true);
		Out->SetNumberField(TEXT("notifiesRemoved"), NotifiesBefore - Seq->Notifies.Num());
		Out->SetNumberField(TEXT("notifyTracks"), Seq->AnimNotifyTracks.Num());
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the asset is now dirty and NOTHING has been saved."));
	}

	// --- add_anim_notify ----------------------------------------------------
	void H_add_anim_notify(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("track"), TEXT("time"),
			  TEXT("notifyClass"), TEXT("notifyStateClass"), TEXT("duration"), TEXT("name") },
			TEXT("assetPath (aliases: path, asset); time (seconds into the sequence); track (name, "
				 "default the first existing track); ONE of notifyClass / notifyStateClass / name - "
				 "name alone makes a skeleton notify (the AnimNotify_<Name> event kind); duration "
				 "(states only, seconds)"),
			{ { TEXT("triggerTime"), TEXT("spell it time") },
			  { TEXT("class"), TEXT("spell it notifyClass, or notifyStateClass for a state") } }))
		{
			return;
		}
		UAnimSequenceBase* Seq = MifResolveAnimSeq(In, Out, TEXT("add_anim_notify"));
		if (!Seq) { return; }

		if (!In->HasField(TEXT("time")))
		{
			Fail(Out, TEXT("time is required (seconds into the sequence). NOTHING was changed."));
			return;
		}
		const float Time = static_cast<float>(JNum(In, TEXT("time"), 0.0));
		const float Length = Seq->GetPlayLength();
		if (Time < 0.f || Time > Length)
		{
			Fail(Out, FString::Printf(
				TEXT("time %.4f is outside this sequence, which is %.4f seconds long. A notify "
					 "placed outside the sequence never fires. NOTHING was changed."), Time, Length));
			return;
		}

		const FString NotifyClassPath = JStr(In, TEXT("notifyClass"));
		const FString StateClassPath = JStr(In, TEXT("notifyStateClass"));
		const FString Name = JStr(In, TEXT("name"));
		const int32 Given = (NotifyClassPath.IsEmpty() ? 0 : 1) + (StateClassPath.IsEmpty() ? 0 : 1)
			+ (Name.IsEmpty() ? 0 : 1);
		if (Given == 0)
		{
			Fail(Out, TEXT("name one of notifyClass, notifyStateClass or name. NOTHING was changed."));
			return;
		}
		if (!NotifyClassPath.IsEmpty() && !StateClassPath.IsEmpty())
		{
			Fail(Out, TEXT("notifyClass and notifyStateClass are alternatives - a notify is one or "
				TEXT("the other, never both. NOTHING was changed.")));
			return;
		}

		// The track. Default to the first existing one rather than inventing a name, and refuse
		// clearly when there are none, because AddAnimationNotifyEvent with an unknown track name
		// warns and returns without adding - a silent no-op.
		FString Track = JStr(In, TEXT("track"));
		if (Track.IsEmpty())
		{
			if (Seq->AnimNotifyTracks.Num() == 0)
			{
				Fail(Out, TEXT("this sequence has no notify tracks to place a notify on. Call "
					TEXT("add_anim_notify_track first. NOTHING was changed.")));
				return;
			}
			Track = Seq->AnimNotifyTracks[0].TrackName.ToString();
		}
		const FName TrackName(*Track);
		if (!UAnimationBlueprintLibrary::IsValidAnimNotifyTrackName(Seq, TrackName))
		{
			Fail(Out, FString::Printf(
				TEXT("no notify track named '%s'. AddAnimationNotifyEvent warns and adds NOTHING for "
					 "an unknown track, so this is refused rather than reported as success. Call "
					 "add_anim_notify_track first. NOTHING was changed."), *Track));
			return;
		}

		UClass* NotifyClass = nullptr;
		if (!NotifyClassPath.IsEmpty() || !StateClassPath.IsEmpty())
		{
			const FString Wanted = NotifyClassPath.IsEmpty() ? StateClassPath : NotifyClassPath;
			// ResolveClassSTRICT, per MifBridgeHandlers.h's own note: plain ResolveClass treats an
			// empty name as "self", which for a notify class would silently target the wrong thing.
			FString ClassError;
			NotifyClass = ResolveClassStrict(Wanted, nullptr,
				StateClassPath.IsEmpty() ? TEXT("notifyClass") : TEXT("notifyStateClass"), ClassError);
			if (!NotifyClass)
			{
				Fail(Out, FString::Printf(TEXT("%s NOTHING was changed."),
					ClassError.IsEmpty() ? *FString::Printf(TEXT("class not found: '%s'."), *Wanted)
					                     : *ClassError));
				return;
			}
			UClass* Base = StateClassPath.IsEmpty() ? UAnimNotify::StaticClass()
													: UAnimNotifyState::StaticClass();
			if (!NotifyClass->IsChildOf(Base))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is not a %s. A notify and a notify STATE are different base classes "
						 "and are not interchangeable. NOTHING was changed."),
					*NotifyClass->GetName(), *Base->GetName()));
				return;
			}
		}

		const int32 Before = Seq->Notifies.Num();
		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_AddNotify", "Add Anim Notify"));
		Seq->Modify();

		if (NotifyClass && StateClassPath.IsEmpty())
		{
			UAnimationBlueprintLibrary::AddAnimationNotifyEvent(Seq, TrackName, Time, NotifyClass);
		}
		else if (NotifyClass)
		{
			const float Duration = static_cast<float>(JNum(In, TEXT("duration"), 0.1));
			UAnimationBlueprintLibrary::AddAnimationNotifyStateEvent(Seq, TrackName, Time, Duration,
																	 NotifyClass);
		}
		else
		{
			UAnimationBlueprintLibrary::AddAnimationNotifyEvent(Seq, TrackName, Time, nullptr);
			// A skeleton notify carries only its NAME, and the library's class-less overload leaves
			// it empty - set it on the event that was just appended.
			if (Seq->Notifies.Num() > Before)
			{
				Seq->Notifies.Last().NotifyName = FName(*Name);
			}
		}

		// READ BACK. Every one of these library calls is void and warns-and-returns on failure, so
		// the count is the only evidence anything happened.
		const int32 Added = Seq->Notifies.Num() - Before;
		if (Added <= 0)
		{
			Fail(Out, TEXT("the notify was not added - the engine's own call warns and returns "
				TEXT("without adding rather than reporting an error. NOTHING was changed.")));
			return;
		}

		Out->SetStringField(TEXT("assetPath"), Seq->GetPathName());
		Out->SetStringField(TEXT("track"), Track);
		Out->SetNumberField(TEXT("added"), Added);
		Out->SetNumberField(TEXT("notifyIndex"), Seq->Notifies.Num() - 1);
		Out->SetNumberField(TEXT("notifyCount"), Seq->Notifies.Num());
		// Through SerializeNotify, so add and describe_animation speak one vocabulary.
		Out->SetObjectField(TEXT("notify"), SerializeNotify(Seq->Notifies.Last()));
		MifNoteTrackState(Seq, Out);
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the asset is now dirty and NOTHING has been saved."));
	}

	// --- remove_anim_notify -------------------------------------------------
	void H_remove_anim_notify(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("name"), TEXT("track"),
			  TEXT("confirm") },
			TEXT("assetPath (aliases: path, asset); name (remove every notify with this name) OR "
				 "track (remove every notify on this track); confirm:true"),
			{ { TEXT("notifyName"), TEXT("spell it name") } }))
		{
			return;
		}
		UAnimSequenceBase* Seq = MifResolveAnimSeq(In, Out, TEXT("remove_anim_notify"));
		if (!Seq) { return; }

		const FString Name = JStr(In, TEXT("name"));
		const FString Track = JStr(In, TEXT("track"));
		if (Name.IsEmpty() == Track.IsEmpty())
		{
			Fail(Out, TEXT("name exactly one of name or track - they are alternatives, and passing "
				TEXT("neither would mean removing everything. NOTHING was changed.")));
			return;
		}
		if (!Track.IsEmpty() && !UAnimationBlueprintLibrary::IsValidAnimNotifyTrackName(Seq, FName(*Track)))
		{
			Fail(Out, FString::Printf(
				TEXT("no notify track named '%s' on this sequence. NOTHING was changed."), *Track));
			return;
		}

		const int32 Before = Seq->Notifies.Num();
		if (!JBool(In, TEXT("confirm"), false))
		{
			int32 Would = 0;
			for (const FAnimNotifyEvent& E : Seq->Notifies)
			{
				const bool bMatch = Name.IsEmpty()
					? (Seq->AnimNotifyTracks.IsValidIndex(E.TrackIndex)
					   && Seq->AnimNotifyTracks[E.TrackIndex].TrackName.ToString() == Track)
					: (E.NotifyName.ToString() == Name);
				if (bMatch) { ++Would; }
			}
			Fail(Out, FString::Printf(
				TEXT("this would remove %d of %d notif(y/ies) and cannot be undone through this "
					 "endpoint. Pass confirm:true. NOTHING was changed."), Would, Before));
			return;
		}

		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_RemoveNotify", "Remove Anim Notify"));
		Seq->Modify();
		if (Name.IsEmpty())
		{
			UAnimationBlueprintLibrary::RemoveAnimationNotifyEventsByTrack(Seq, FName(*Track));
		}
		else
		{
			UAnimationBlueprintLibrary::RemoveAnimationNotifyEventsByName(Seq, FName(*Name));
		}

		const int32 Removed = Before - Seq->Notifies.Num();
		Out->SetStringField(TEXT("assetPath"), Seq->GetPathName());
		Out->SetNumberField(TEXT("removed"), Removed);
		Out->SetNumberField(TEXT("notifyCount"), Seq->Notifies.Num());
		if (Removed == 0)
		{
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("nothing matched %s '%s', so nothing was removed. removed:0 is the measured "
					 "difference in the notify count, not an assumption."),
				Name.IsEmpty() ? TEXT("track") : TEXT("name"),
				Name.IsEmpty() ? *Track : *Name));
		}
		MifNoteTrackState(Seq, Out);
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the asset is now dirty and NOTHING has been saved."));
	}

	// --- add_anim_state -----------------------------------------------------
	//   in:  { blueprintId, graphId, name, x?, y?, stateType? }
	//   out: { node, stateName, boundGraphId, ... }
	//
	// ONE MISSING CONSTRUCTOR CALL WAS BLOCKING ALL OF IT. list_graphs and list_nodes already READ
	// state machines, states and transition rule graphs - GatherGraphsRecursive walks SubGraphs - and
	// add_anim_node can already place the UAnimGraphNode_StateMachine container. What could not be
	// done was put a single STATE inside it, and with no state there is nothing for a transition to
	// join, so no locomotion Anim Blueprint could be authored end to end. Anim BPs are a top-tier
	// asset type and an agent hits this immediately.
	//
	// ONE ENDPOINT, NOT TWO. add_anim_transition was scoped out deliberately: connect_pins already
	// creates the transition node, because UAnimationStateMachineSchema's own connection response is
	// a MAKE_WITH_CONVERSION_NODE that spawns a UAnimStateTransitionNode when you join two states.
	// Adding a second name for the same operation is exactly what this codebase prefers not to do.
	// What is missing after a connect_pins is the new transition's ruleGraphId, and that belongs as
	// an optional block on connect_pins' own response - filed, not built here.
	//
	// THE GUARD IS ON THE GRAPH CLASS, NOT THE SCHEMA, and that distinction is the whole reason this
	// is a separate function rather than a relaxed branch of add_anim_node. FAnimStateNodeNameValidator
	// does:
	//
	//     UAnimationStateMachineGraph* StateMachine =
	//         CastChecked<UAnimationStateMachineGraph>(InStateNode->GetOuter());
	//
	// (AnimStateNodeBase.cpp:27) - a CastChecked on the node's OUTER, which is fatal, not an error.
	// So the target graph must BE a UAnimationStateMachineGraph and must be the node's Outer. A
	// schema-only test would let a fatal case through: a graph can carry the state-machine schema
	// without being that class. This is the same PM-013 shape add_anim_node already learned once -
	// the check has to be on the thing the engine actually casts.
	//
	// NAMING IS NOT COSMETIC. UAnimStateNode::GetStateName() returns BoundGraph->GetName()
	// (AnimStateNode.cpp:68), so a state's name IS its bound graph's name - there is no separate
	// field and no rename_graph endpoint. The name has to be right at creation, which is why it is
	// applied to the bound graph immediately after PostPlacedNewNode creates it.
	void H_add_anim_state(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("graphId"), TEXT("graph"), TEXT("name"),
			  TEXT("x"), TEXT("y") },
			TEXT("blueprintId (the Animation Blueprint); graphId - the STATE MACHINE's inner graph, ")
			TEXT("from list_graphs; name (the state's name, which is also its bound graph's name); ")
			TEXT("x, y (graph position)"),
			{ { TEXT("stateName"), TEXT("spell it name") },
			  { TEXT("nodeClass"), TEXT("not accepted - this endpoint makes a UAnimStateNode. Use ")
			                       TEXT("connect_pins between two states to make a transition; the ")
			                       TEXT("state machine schema creates the transition node itself") },
			  { TEXT("fromState"), TEXT("transitions are made by connect_pins between two states, ")
			                       TEXT("not here") } }))
		{
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint) { return; }
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph) { return; }

		if (!Blueprint->IsA<UAnimBlueprint>())
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not an Animation Blueprint, so it has no state machine to add a state ")
				TEXT("to. NOTHING was created."), *Blueprint->GetName()));
			return;
		}

		// THE FATAL-CAST GUARD. See the note above: this must test the graph CLASS.
		if (!Graph->IsA<UAnimationStateMachineGraph>())
		{
			Fail(Out, FString::Printf(
				TEXT("graph '%s' is a %s, not a state machine graph. A state's OUTER is CastChecked ")
				TEXT("to UAnimationStateMachineGraph (AnimStateNodeBase.cpp) and a failed CastChecked ")
				TEXT("TERMINATES the editor rather than returning an error, so this is refused before ")
				TEXT("anything is constructed. Add a state machine to the AnimGraph first ")
				TEXT("(add_anim_node nodeClass=AnimGraphNode_StateMachine), then pass ITS inner ")
				TEXT("graph - list_graphs shows it nested under the AnimGraph. NOTHING was created."),
				*Graph->GetName(), *Graph->GetClass()->GetName()));
			return;
		}

		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required. A state's name IS its bound graph's name ")
				TEXT("(GetStateName returns BoundGraph->GetName), there is no separate field for it, ")
				TEXT("and nothing here can rename it afterwards - so it has to be right now. ")
				TEXT("NOTHING was created."));
			return;
		}
		for (UEdGraphNode* Existing : Graph->Nodes)
		{
			if (const UAnimStateNodeBase* AsState = Cast<UAnimStateNodeBase>(Existing))
			{
				if (AsState->GetStateName() == Name)
				{
					Fail(Out, FString::Printf(
						TEXT("this state machine already has a state named '%s'. State names are ")
						TEXT("graph names and must be unique within the machine. NOTHING was ")
						TEXT("created."), *Name));
					return;
				}
			}
		}

		UAnimStateNode* Node = NewObject<UAnimStateNode>(Graph, UAnimStateNode::StaticClass(),
														NAME_None, RF_Transactional);
		if (!Node)
		{
			Fail(Out, TEXT("failed to construct the state node. NOTHING was created."));
			return;
		}

		PlaceAndInit(Graph, Node, JInt(In, TEXT("x"), 0), JInt(In, TEXT("y"), 0));

		// PostPlacedNewNode (inside PlaceAndInit) is what creates the BoundGraph, so the name can
		// only be applied after it - and it must be applied, because that graph's name is the state's
		// name and it defaults to something generic.
		if (!Node->BoundGraph)
		{
			Fail(Out, TEXT("the state node was placed but has no bound graph, which should not ")
				TEXT("happen - PostPlacedNewNode creates it. Reported rather than returning a state ")
				TEXT("that cannot hold animation."));
			return;
		}
		FBlueprintEditorUtils::RenameGraph(Node->BoundGraph, *Name);
		MarkStructural(Blueprint);

		// READ BACK through the engine's own accessor rather than echoing the request - RenameGraph
		// sanitises and de-duplicates, so the name that landed can legitimately differ from the one
		// asked for, and saying so is the difference between a report and a claim.
		const FString Actual = Node->GetStateName();
		EmitNode(Out, Node);
		Out->SetStringField(TEXT("stateName"), Actual);
		Out->SetStringField(TEXT("stateNameRequested"), Name);
		// GraphIdOf, NOT GetPathName. Live-caught 2026-08-30: a raw object path is not what the
		// graph endpoints accept - they want list_graphs' "<blueprintPath>::<graphName>" form,
		// and add_anim_node refused this field outright. A boundGraphId nothing can consume is
		// worse than none at all, because the whole point of returning it is that the caller can
		// immediately fill the state with a SequencePlayer.
		Out->SetStringField(TEXT("boundGraphId"), GraphIdOf(Blueprint, Node->BoundGraph));
		Out->SetNumberField(TEXT("statesInMachine"), [Graph]()
		{
			int32 N = 0;
			for (const UEdGraphNode* E : Graph->Nodes)
			{
				if (E && E->IsA<UAnimStateNodeBase>()) { ++N; }
			}
			return N;
		}());
		if (Actual != Name)
		{
			Out->SetStringField(TEXT("nameNote"), FString::Printf(
				TEXT("the state is called '%s', not '%s' - RenameGraph sanitises and de-duplicates "
					 "graph names, and a state's name is its graph's name."), *Actual, *Name));
		}
		Out->SetStringField(TEXT("nextStep"),
			TEXT("boundGraphId is this state's OWN animation graph - pass it to add_anim_node to put "
				 "a SequencePlayer or blend space in it. To make a transition, connect_pins between "
				 "two states: the state machine schema creates the transition node itself."));
	}

	// =======================================================================
	// add_socket - the ONE socket verb that needed building
	// =======================================================================
	//
	// SCOPE, CUT DOWN AFTER CHECKING. The survey asked for three endpoints: add, remove and
	// set_socket_transform. Two of them already exist by another name:
	//
	//   move    set_property {objectPath: <mesh or skeleton>, propertyPath: "Sockets[3].RelativeLocation"}
	//   delete  edit_container {propertyPath: "Sockets", operation: "remove", index: 3}
	//
	// The property walker crosses object boundaries, so both reach a socket today. What they needed
	// was the INDEX, which list_sockets did not emit - so this commit adds `index` and `objectPath`
	// there rather than adding two endpoints that would duplicate existing verbs. Only CREATION was
	// genuinely impossible: nothing in the plugin can NewObject a USkeletalMeshSocket outered to the
	// right owner.
	//
	// AddSocket CANNOT REPORT FAILURE. It is void, and it silently does nothing when the outer is
	// wrong, when the name is already taken, or when the bone is not in the reference skeleton
	// (SkeletalMesh.cpp:3699-3714) - it only UE_LOGs. So every one of those conditions is checked
	// here first, and the result is verified by finding the socket afterwards.
	//
	// THE OUTER IS NOT OPTIONAL. AddSocket's first act is `if (InSocket->GetOuter() == this)`, so a
	// socket constructed with any other outer is dropped on the floor with no error whatsoever.
	//
	// USkeleton HAS NO AddSocket AT ALL. The skeleton path is therefore hand-rolled - Modify, then
	// NewObject outered to the skeleton, then Sockets.Add - which is exactly what
	// USkeletalMesh::AddSocket does internally for its bAddToSkeleton branch.
	//
	// WHAT THIS DELIBERATELY DOES NOT DO: call RebuildSocketMap(). USkeletalMesh::SocketMap is a
	// PostLoad-built cache, and it is tempting to think an add leaves it stale. In an EDITOR build it
	// cannot: every read of that map (FindSocketAndIndex at SkeletalMesh.cpp:3799, and :3846) sits
	// inside `#if !WITH_EDITOR`, the editor paths linear-scan the Sockets array instead, and
	// RebuildSocketMap's entire body is `#if !WITH_EDITOR` too - so calling it here would compile to
	// nothing. Left out on purpose, and said out loud, because a call that looks like a safety
	// measure and does nothing is worse than no call: the next reader would believe it was handled.

	void H_add_socket(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("name"), TEXT("bone"),
			  TEXT("boneName"), TEXT("location"), TEXT("rotation"), TEXT("scale"),
			  TEXT("target") },
			TEXT("path (aliases: assetPath, mesh) - a SkeletalMesh or Skeleton; name; bone (alias ")
			TEXT("boneName); location/rotation/scale {x,y,z}; target (mesh|skeleton|both)"),
			{ { TEXT("index"), TEXT("that is an OUTPUT - list_sockets reports each socket's index, "
									"and set_property/edit_container use it to move or delete one") },
			  { TEXT("parent"), TEXT("spell it `bone` - a socket attaches to a BONE, not to another "
									 "socket") } }))
		{
			return;
		}
#if !WITH_EDITOR
		Fail(Out, TEXT("add_socket needs an editor build - USkeletalMesh::AddSocket is WITH_EDITOR "
			TEXT("only.")));
#else
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("mesh") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a SkeletalMesh or Skeleton asset. NOTHING was changed."));
			return;
		}
		UObject* Asset = LoadAssetLenient(Path);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: %s. NOTHING was changed."), *Path));
			return;
		}
		USkeletalMesh* Mesh = Cast<USkeletalMesh>(Asset);
		USkeleton* Skeleton = Cast<USkeleton>(Asset);
		if (!Mesh && !Skeleton)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s - sockets live on a SkeletalMesh or a Skeleton. NOTHING was ")
				TEXT("changed."), *Path, *Asset->GetClass()->GetName()));
			return;
		}

		const FString Name = JStr(In, TEXT("name")).TrimStartAndEnd();
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required, and cannot be blank or whitespace - AddSocket trims ")
				TEXT("the name and silently drops a socket whose trimmed name is empty. NOTHING was ")
				TEXT("changed."));
			return;
		}
		const FString Bone = JStrAny(In, { TEXT("bone"), TEXT("boneName") });
		if (Bone.IsEmpty())
		{
			Fail(Out, TEXT("bone is required - the bone the socket attaches to. list_bones reports ")
				TEXT("them. NOTHING was changed."));
			return;
		}

		// Where does it go? Default to the skeleton when there is one, because that is where real
		// content keeps sockets: every sampled DDS2 skeletal mesh has ZERO mesh sockets and shares
		// one rig - see list_sockets' own comment.
		USkeleton* TargetSkeleton = Skeleton ? Skeleton : (Mesh ? Mesh->GetSkeleton() : nullptr);
		FString Target = JStr(In, TEXT("target")).ToLower();
		if (Target.IsEmpty()) { Target = TargetSkeleton ? TEXT("skeleton") : TEXT("mesh"); }
		if (Target != TEXT("mesh") && Target != TEXT("skeleton") && Target != TEXT("both"))
		{
			Fail(Out, FString::Printf(
				TEXT("target must be \"mesh\", \"skeleton\" or \"both\" - got '%s'. NOTHING was ")
				TEXT("changed."), *Target));
			return;
		}
		if (Skeleton && Target != TEXT("skeleton"))
		{
			Fail(Out, FString::Printf(
				TEXT("path names a Skeleton, so target '%s' has no meaning here - pass a SkeletalMesh ")
				TEXT("to reach the mesh-side list. NOTHING was changed."), *Target));
			return;
		}
		if (Target != TEXT("mesh") && !TargetSkeleton)
		{
			Fail(Out, TEXT("this mesh has no USkeleton, so a socket cannot be added to one. Pass ")
				TEXT("target:\"mesh\". NOTHING was changed."));
			return;
		}

		// THE BONE MUST EXIST, checked against the reference skeleton of whichever object will own
		// the socket. AddSocket makes the same check and then does NOTHING when it fails.
		const FReferenceSkeleton& RefSkel = (Target == TEXT("mesh") && Mesh)
			? Mesh->GetRefSkeleton() : TargetSkeleton->GetReferenceSkeleton();
		if (RefSkel.FindBoneIndex(FName(*Bone)) == INDEX_NONE)
		{
			TArray<FString> Near;
			for (int32 i = 0; i < RefSkel.GetNum() && Near.Num() < 8; ++i)
			{
				const FString BoneName = RefSkel.GetBoneName(i).ToString();
				if (BoneName.Contains(Bone) || Bone.Contains(BoneName)) { Near.Add(BoneName); }
			}
			Fail(Out, FString::Printf(
				TEXT("no bone '%s' in the reference skeleton (%d bones). AddSocket makes this same ")
				TEXT("check and then silently does nothing, so it is made here where it can be ")
				TEXT("reported. %s list_bones lists them all. NOTHING was changed."),
				*Bone, RefSkel.GetNum(),
				Near.Num() ? *FString::Printf(TEXT("Did you mean: %s?"),
											  *FString::Join(Near, TEXT(", ")))
						   : TEXT("")));
			return;
		}

		// Already taken? AddSocket refuses a duplicate name silently too.
		auto NameTaken = [&Name](const TArray<TObjectPtr<USkeletalMeshSocket>>& List)
		{
			for (const USkeletalMeshSocket* S : List)
			{
				if (S && S->SocketName == FName(*Name)) { return true; }
			}
			return false;
		};
		if (TargetSkeleton && Target != TEXT("mesh") && NameTaken(TargetSkeleton->Sockets))
		{
			Fail(Out, FString::Printf(
				TEXT("the skeleton '%s' already has a socket named '%s'. Socket names must be unique ")
				TEXT("or attach-by-name is ambiguous. NOTHING was changed."),
				*TargetSkeleton->GetName(), *Name));
			return;
		}
		if (Mesh && Target != TEXT("skeleton") && NameTaken(Mesh->GetMeshOnlySocketList()))
		{
			Fail(Out, FString::Printf(
				TEXT("the mesh already has a socket named '%s'. NOTHING was changed."), *Name));
			return;
		}

		// The house helpers, not a hand-rolled parse: ReadRotatorField accepts {pitch,yaw,roll} as
		// well as {x,y,z} and ReadScaleField accepts a bare number as a uniform scale, which is the
		// convention every other transform-taking endpoint here already documents. A malformed
		// component is REFUSED rather than silently defaulted - a socket quietly placed at the
		// origin is exactly the kind of wrong-but-plausible result that costs an hour to spot.
		FVector Loc(0.0), Scale(1.0);
		FRotator Rot(0.0);
		FString VecErr;
		if (ReadVectorField(In, TEXT("location"), Loc, VecErr) == EJsonRead::Invalid
			|| ReadRotatorField(In, TEXT("rotation"), Rot, VecErr) == EJsonRead::Invalid
			|| ReadScaleField(In, TEXT("scale"), Scale, VecErr) == EJsonRead::Invalid)
		{
			Fail(Out, VecErr + TEXT(" NOTHING was changed."));
			return;
		}

		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_AddSocket", "Add Socket"));
		FString Source;
		if (Target == TEXT("mesh") || Target == TEXT("both"))
		{
			// THE OUTER MUST BE THE MESH. AddSocket's first act is GetOuter() == this, and a socket
			// with any other outer is dropped with no error at all.
			Mesh->Modify();
			USkeletalMeshSocket* S = NewObject<USkeletalMeshSocket>(Mesh);
			S->SocketName = FName(*Name);
			S->BoneName = FName(*Bone);
			S->RelativeLocation = Loc;
			S->RelativeRotation = Rot;
			S->RelativeScale = Scale;
			Mesh->AddSocket(S, /*bAddToSkeleton*/ Target == TEXT("both"));
			Source = Target;
		}
		else
		{
			// USkeleton has NO AddSocket, so this is what USkeletalMesh::AddSocket does internally
			// for its own skeleton branch.
			TargetSkeleton->Modify();
			USkeletalMeshSocket* S = NewObject<USkeletalMeshSocket>(TargetSkeleton);
			S->SocketName = FName(*Name);
			S->BoneName = FName(*Bone);
			S->RelativeLocation = Loc;
			S->RelativeRotation = Rot;
			S->RelativeScale = Scale;
			TargetSkeleton->Sockets.Add(S);
			Source = TEXT("skeleton");
		}

		// VERIFIED BY SEARCHING FOR IT, because AddSocket is void and its three refusal paths are
		// UE_LOG-only. Nothing above this line proves a socket exists.
		const USkeletalMeshSocket* Found = nullptr;
		int32 FoundIndex = INDEX_NONE;
		if (Target == TEXT("mesh") || Target == TEXT("both"))
		{
			const TArray<USkeletalMeshSocket*> MeshList = Mesh->GetMeshOnlySocketList();
			for (int32 i = 0; i < MeshList.Num(); ++i)
			{
				if (MeshList[i] && MeshList[i]->SocketName == FName(*Name))
				{
					Found = MeshList[i]; FoundIndex = i; break;
				}
			}
		}
		else
		{
			for (int32 i = 0; i < TargetSkeleton->Sockets.Num(); ++i)
			{
				if (TargetSkeleton->Sockets[i]
					&& TargetSkeleton->Sockets[i]->SocketName == FName(*Name))
				{
					Found = TargetSkeleton->Sockets[i]; FoundIndex = i; break;
				}
			}
		}
		if (!Found)
		{
			Fail(Out, FString::Printf(
				TEXT("the socket was created and '%s' does not list it on read-back. AddSocket is ")
				TEXT("void and only UE_LOGs its refusals, so this is the only way to know. NOTHING ")
				TEXT("usable was produced."), *Name));
			return;
		}
		(Target == TEXT("mesh") || Target == TEXT("both") ? Cast<UObject>(Mesh)
														  : Cast<UObject>(TargetSkeleton))
			->MarkPackageDirty();

		TSharedRef<FJsonObject> J = SocketJson(Found->SocketName, Found->BoneName,
			Found->RelativeLocation, Found->RelativeRotation, Found->RelativeScale);
		J->SetStringField(TEXT("source"), Source);
		J->SetNumberField(TEXT("index"), FoundIndex);
		J->SetStringField(TEXT("objectPath"), Found->GetPathName());
		Out->SetObjectField(TEXT("socket"), J);
		Out->SetStringField(TEXT("path"), Asset->GetPathName());
		Out->SetBoolField(TEXT("created"), true);
		Out->SetStringField(TEXT("owner"),
			(Target == TEXT("mesh") || Target == TEXT("both")) ? Mesh->GetPathName()
															   : TargetSkeleton->GetPathName());
		Out->SetStringField(TEXT("editNote"),
			TEXT("to MOVE this socket use set_property {objectPath: <owner>, propertyPath: "
				 "\"Sockets[<index>].RelativeLocation\"}, and to DELETE it use edit_container "
				 "{propertyPath: \"Sockets\", operation: \"remove\", index: <index>}. Both work "
				 "today, which is why there is no set_socket_transform or remove_socket endpoint."));
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the owning asset is dirty and NOTHING has been saved."));
#endif
	}
}
