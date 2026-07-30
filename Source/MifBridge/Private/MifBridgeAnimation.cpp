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
}
