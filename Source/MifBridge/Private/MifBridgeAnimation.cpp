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

// Sockets, behavior trees and blackboards - all READ-ONLY. See H_list_sockets below for why these
// live here rather than in a new file: this is already the animation-and-skeleton module.
#include "Engine/SkeletalMesh.h"
#include "Engine/SkeletalMeshSocket.h"
#include "Animation/Skeleton.h"        // USkeleton::Sockets - where DDS2 actually keeps them
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshSocket.h"
#include "BehaviorTree/BehaviorTree.h"
#include "BehaviorTree/BTCompositeNode.h"
#include "BehaviorTree/BTTaskNode.h"
#include "BehaviorTree/BlackboardData.h"
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
#include "Animation/AnimNotifies/AnimNotify.h"
#include "Animation/AnimNotifies/AnimNotifyState.h"
#include "Animation/Skeleton.h"
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
}
