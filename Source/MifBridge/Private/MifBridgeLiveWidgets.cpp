// MifBridge — LIVE widget instances: what a running game actually put on screen, not what a Widget
// Blueprint asset says it might.
//
// WHY THIS EXISTS, and why it is a DIFFERENT question from everything MifBridgeWidgets.cpp answers.
// That file walks a WBP asset's DESIGN-TIME WidgetTree - what a human would see in the Designer. It
// cannot see a runtime CreateWidget() call, a child injected into a named container at BeginPlay, or
// which of several possible parent screens actually assembled around a given machine. An external
// enhancement report (infectedcoolpat/QOLCrafting_P, 2026-08-27 - logged in
// tools/FEATURE_PARITY_SPEC.md) named this gap precisely: their Metal Recycler screen is composed at
// runtime from a vanilla parent plus two mod widgets injected into named containers, and no Designer
// preview of any one WBP shows the assembled result.
//
// PHASE 1-2 of that report's own staged proposal: enumerate live UUserWidget instances, then read
// back their calculated screen geometry. Read-only, no rendering, no PIE automation - the report's
// own recommended entry point, because it needs no new rendering path and works with a HUMAN already
// driving PIE manually. Phases A onward (isolated rendering, declarative composition, an interaction
// scenario runner that presses F for you) are separate, larger, unstarted items.
//
// TWO ENDPOINTS, matching the report's own split rather than one heavy combined call:
//   list_live_widgets     - lightweight enumeration, so a caller picks ONE widget to go deep on.
//   describe_live_widget  - the full geometry TREE for one widget, recursing through its UMG panel
//                           children AND through any nested UUserWidget's own internal WidgetTree -
//                           the two levels UMG actually renders as one continuous visual hierarchy.
//
// GEOMETRY IS "LAST PAINTED", not live-recomputed. UWidget::GetCachedGeometry() returns whatever the
// last Slate tick actually laid out - correct for "what is on screen right now", wrong to call before
// a single frame has ticked (a widget added this same frame reports a zero/default geometry until
// the next paint). Callers driving their own creation should tick at least once before inspecting.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Blueprint/UserWidget.h"                 // UUserWidget, WidgetTree
#include "Blueprint/WidgetTree.h"                 // UWidgetTree::RootWidget
#include "Blueprint/WidgetBlueprintLibrary.h"     // GetAllWidgetsOfClass
#include "Components/Widget.h"                    // UWidget, GetCachedGeometry
#include "Components/PanelWidget.h"               // UPanelWidget children
#include "Components/PanelSlot.h"
#include "Components/CanvasPanelSlot.h"           // ZOrder, the one common slot property worth surfacing
#include "Layout/Geometry.h"                      // FGeometry
#include "Engine/World.h"
#include "UObject/UObjectGlobals.h"                // FindObject

namespace MifBridge
{
	namespace
	{
		TSharedRef<FJsonObject> JsonVec2(const FVector2D& V)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetNumberField(TEXT("x"), V.X);
			J->SetNumberField(TEXT("y"), V.Y);
			return J;
		}

		// MifBridgePIE.cpp has its own NetModeName; file-local there, so a small local copy rather than
		// promoting it for one caller - same call this codebase already makes elsewhere for helpers
		// used by exactly one other file (see LoadMetasoundLoose's comment in MifBridgeMetasound.cpp).
		const TCHAR* LocalNetModeName(ENetMode Mode)
		{
			switch (Mode)
			{
			case NM_Standalone:      return TEXT("Standalone");
			case NM_DedicatedServer: return TEXT("DedicatedServer");
			case NM_ListenServer:    return TEXT("ListenServer");
			case NM_Client:          return TEXT("Client");
			default:                 return TEXT("Unknown");
			}
		}

		const TCHAR* VisibilityName(ESlateVisibility V)
		{
			switch (V)
			{
			case ESlateVisibility::Visible:            return TEXT("Visible");
			case ESlateVisibility::Collapsed:           return TEXT("Collapsed");
			case ESlateVisibility::Hidden:              return TEXT("Hidden");
			case ESlateVisibility::HitTestInvisible:    return TEXT("HitTestInvisible");
			case ESlateVisibility::SelfHitTestInvisible:return TEXT("SelfHitTestInvisible");
			default:                                    return TEXT("Unknown");
			}
		}

		// Picks the target world exactly the way list_pie_actors does (MifBridgePIE.cpp), so the two
		// endpoints agree about which world "server"/"client"/"any" means when more than one PIE
		// world is running. Falls back to the EDITOR world when nothing is playing - widgets can be
		// on screen there too (a human-opened preview), and this endpoint should not refuse just
		// because nothing is mid-PIE.
		UWorld* ResolveWidgetWorld(const FString& WantRole, TSharedPtr<FJsonObject> OutInfo, FString& OutError)
		{
			TArray<UWorld*> PIEWorlds;
			CollectPIEWorlds(PIEWorlds);
			if (PIEWorlds.Num() == 0)
			{
				UWorld* Editor = EditorWorld();
				if (!Editor)
				{
					OutError = TEXT("no PIE world and no editor world - nothing to enumerate widgets in.");
					return nullptr;
				}
				if (OutInfo) { OutInfo->SetStringField(TEXT("worldSource"), TEXT("editor")); }
				return Editor;
			}
			for (UWorld* W : PIEWorlds)
			{
				const bool bIsServer = (W->GetNetMode() != NM_Client);
				if (WantRole == TEXT("any")
					|| (WantRole == TEXT("server") && bIsServer)
					|| (WantRole == TEXT("client") && !bIsServer))
				{
					if (OutInfo)
					{
						OutInfo->SetStringField(TEXT("worldSource"), TEXT("pie"));
						OutInfo->SetStringField(TEXT("netMode"), LocalNetModeName(W->GetNetMode()));
					}
					return W;
				}
			}
			OutError = FString::Printf(
				TEXT("no PIE world matching netMode '%s' - PIE is running but not with that role."), *WantRole);
			return nullptr;
		}

		// One widget's OWN geometry + identity, no children. Shared by both endpoints so a list entry
		// and a tree node never report the same widget two different ways.
		TSharedRef<FJsonObject> DescribeWidgetSelf(UWidget* W)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("path"), W->GetPathName());
			J->SetStringField(TEXT("name"), W->GetName());
			J->SetStringField(TEXT("class"), W->GetClass()->GetPathName());
			J->SetStringField(TEXT("visibility"), VisibilityName(W->GetVisibility()));
			J->SetBoolField(TEXT("isVisible"), W->IsVisible());

			const FGeometry& Geo = W->GetCachedGeometry();
			// FVector2D wrapping first: GetAbsolutePosition/Size return a deprecation-shim type
			// (UE::Slate::FDeprecateVector2DResult) that implicitly converts but does not carry every
			// FVector2D method - IsNearlyZero needs the real type.
			const FVector2D AbsPos(Geo.GetAbsolutePosition());
			const FVector2D AbsSize(Geo.GetAbsoluteSize());
			J->SetObjectField(TEXT("absolutePosition"), JsonVec2(AbsPos));
			J->SetObjectField(TEXT("absoluteSize"), JsonVec2(AbsSize));
			J->SetObjectField(TEXT("desiredSize"), JsonVec2(W->GetDesiredSize()));
			// Zero on BOTH counts (position and size) is the single most common "why is nothing here"
			// symptom - a widget that has never been painted (added this frame, or never actually
			// reached the viewport) reports exactly this and nothing else distinguishes it from a
			// deliberately zero-sized one.
			J->SetBoolField(TEXT("neverPainted"), AbsSize.IsNearlyZero() && AbsPos.IsNearlyZero());

			if (const UCanvasPanelSlot* CSlot = Cast<UCanvasPanelSlot>(W->Slot))
			{
				J->SetNumberField(TEXT("zOrder"), CSlot->GetZOrder());
			}
			if (W->Slot)
			{
				J->SetStringField(TEXT("slotClass"), W->Slot->GetClass()->GetName());
			}
			return J;
		}

		// Recurses through UPanelWidget children AND, when a node IS a UUserWidget, into its own
		// internal WidgetTree->RootWidget - the two levels UMG renders as one continuous hierarchy
		// (see the file header). MaxDepth guards a pathological UI from producing an unbounded response.
		TSharedRef<FJsonObject> DescribeWidgetTree(UWidget* W, int32 RemainingDepth)
		{
			TSharedRef<FJsonObject> J = DescribeWidgetSelf(W);
			if (RemainingDepth <= 0)
			{
				J->SetBoolField(TEXT("depthLimited"), true);
				return J;
			}

			TArray<TSharedPtr<FJsonValue>> Children;
			if (UPanelWidget* Panel = Cast<UPanelWidget>(W))
			{
				const int32 Count = Panel->GetChildrenCount();
				for (int32 i = 0; i < Count; ++i)
				{
					if (UWidget* Child = Panel->GetChildAt(i))
					{
						Children.Add(MakeShared<FJsonValueObject>(DescribeWidgetTree(Child, RemainingDepth - 1)));
					}
				}
			}
			if (UUserWidget* AsUserWidget = Cast<UUserWidget>(W))
			{
				if (AsUserWidget->WidgetTree && AsUserWidget->WidgetTree->RootWidget)
				{
					TSharedRef<FJsonObject> Inner = MakeShared<FJsonObject>();
					Inner->SetStringField(TEXT("note"),
						TEXT("this UUserWidget's own internal content - the WBP's design-time root, now live"));
					TSharedRef<FJsonObject> InnerTree =
						DescribeWidgetTree(AsUserWidget->WidgetTree->RootWidget, RemainingDepth - 1);
					J->SetObjectField(TEXT("userWidgetContent"), InnerTree);
				}
			}
			if (Children.Num() > 0)
			{
				J->SetArrayField(TEXT("children"), Children);
			}
			return J;
		}
	}

	// --- list_live_widgets ---------------------------------------------------------------------------
	//   in:  { netMode? (server|client|any, default server), topLevelOnly? (default true),
	//          classFilter? (substring on class name) }
	//   out: { worldSource, netMode?, count, widgets:[ {path, name, class, visibility, isVisible,
	//          absolutePosition, absoluteSize, desiredSize, neverPainted, zOrder?, slotClass?} ] }
	// Lightweight by design - pick a path from here and pass it to describe_live_widget for the tree.
	void H_list_live_widgets(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("netMode"), TEXT("topLevelOnly"), TEXT("classFilter") },
			TEXT("netMode? (server|client|any, default server - only meaningful with >1 PIE world), ")
			TEXT("topLevelOnly? (default true - widgets added directly to a viewport/player screen, ")
			TEXT("not every nested child), classFilter? (substring match on class name)")))
		{
			return;
		}

		const FString WantRole = JStr(In, TEXT("netMode"), TEXT("server")).ToLower();
		const bool bTopLevelOnly = JBool(In, TEXT("topLevelOnly"), true);
		const FString ClassFilter = JStr(In, TEXT("classFilter"));

		FString WorldError;
		UWorld* World = ResolveWidgetWorld(WantRole, Out, WorldError);
		if (!World)
		{
			Fail(Out, WorldError);
			return;
		}

		TArray<UUserWidget*> Found;
		UWidgetBlueprintLibrary::GetAllWidgetsOfClass(World, Found, UUserWidget::StaticClass(), bTopLevelOnly);

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UUserWidget* W : Found)
		{
			if (!W || !IsValid(W)) { continue; }
			if (!ClassFilter.IsEmpty() && !W->GetClass()->GetName().Contains(ClassFilter)) { continue; }
			Arr.Add(MakeShared<FJsonValueObject>(DescribeWidgetSelf(W)));
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("widgets"), Arr);
		Out->SetStringField(TEXT("note"),
			TEXT("geometry is LAST-PAINTED (GetCachedGeometry) - a widget added this same frame and ")
			TEXT("never ticked reports neverPainted:true. Pass a widget's path to describe_live_widget ")
			TEXT("for its full geometry tree, including nested UUserWidget content."));
	}

	// --- describe_live_widget -------------------------------------------------------------------------
	//   in:  { path (a live UWidget/UUserWidget instance's full object path, from list_live_widgets),
	//          maxDepth? (default 12) }
	//   out: { tree: {...recursive...} }
	void H_describe_live_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("maxDepth") },
			TEXT("path (a live widget instance's path, from list_live_widgets), maxDepth? (default 12)")))
		{
			return;
		}
		const FString Path = JStr(In, TEXT("path"));
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - list_live_widgets reports one for every instance found."));
			return;
		}
		// FindObject, not StaticLoadObject: this is a LIVE instance already in memory (PIE or editor
		// world), not an asset on disk. Loading it would either fail or - worse - resolve to something
		// unrelated if a same-named asset happens to exist.
		UWidget* Target = FindObject<UWidget>(nullptr, *Path);
		if (!Target || !IsValid(Target))
		{
			Fail(Out, FString::Printf(
				TEXT("no live widget instance at '%s' - it may have been destroyed, or this is an ")
				TEXT("asset path rather than a live instance path. Re-run list_live_widgets; instance ")
				TEXT("paths change across PIE sessions."), *Path));
			return;
		}
		const int32 MaxDepth = FMath::Clamp(JInt(In, TEXT("maxDepth"), 12), 1, 64);
		Out->SetObjectField(TEXT("tree"), DescribeWidgetTree(Target, MaxDepth));
	}
}
