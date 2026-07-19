// MifBridge — UWidgetBlueprint asset endpoints: "Is Variable" flag, property bindings,
// and widget-tree add/remove. All are transaction-safe (Modify + mutate + MarkStructural);
// none full-compile inline — RunEndpoint's FScopedTransaction wraps them and a full compile
// inside a transaction reinstances the class (crash). Mirrors the engine designer handlers,
// which likewise end at MarkBlueprintAsStructurallyModified.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "WidgetBlueprint.h"                      // UWidgetBlueprint, FDelegateEditorBinding
#include "Blueprint/WidgetTree.h"                 // UWidgetTree::ConstructWidget/FindWidget/RemoveWidget/RootWidget
#include "Blueprint/WidgetBlueprintGeneratedClass.h" // EBindingKind
#include "Components/Widget.h"                     // UWidget::bIsVariable
#include "Components/PanelWidget.h"                // UPanelWidget::AddChild
#include "Components/CanvasPanel.h"                // UCanvasPanel::AddChildToCanvas
#include "Components/CanvasPanelSlot.h"            // UCanvasPanelSlot layout setters
#include "Engine/Blueprint.h"                      // UBlueprint::GetGuidFromClassByFieldName
#include "Engine/BlueprintGeneratedClass.h"        // UBlueprintGeneratedClass (SkeletonGeneratedClass cast)
#include "UObject/UObjectGlobals.h"                // MakeUniqueObjectName

namespace MifBridge
{
	// Resolve "blueprintId"/"path" and require it to be a UWidgetBlueprint. On failure
	// writes the error into Out and returns null (same convention as ResolveBlueprintField).
	static UWidgetBlueprint* ResolveWidgetBlueprintField(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);   // already Fail()ed if null
		if (!Blueprint)
		{
			return nullptr;
		}
		UWidgetBlueprint* WBP = Cast<UWidgetBlueprint>(Blueprint);
		if (!WBP)
		{
			Fail(Out, FString::Printf(TEXT("not a Widget Blueprint: '%s'"), *Blueprint->GetPathName()));
			return nullptr;
		}
		if (!WBP->WidgetTree)
		{
			Fail(Out, TEXT("widget blueprint has no WidgetTree"));
			return nullptr;
		}
		return WBP;
	}

	// --- set_widget_is_variable --------------------------------------------
	// Flip UWidget::bIsVariable (public uint8:1 bitfield — there is NO setter; the designer
	// assigns it directly). MarkStructural runs a skeleton-only compile
	// (RegenerateSkeletonOnly) which is what actually synthesises the member FProperty named
	// after the widget's FName — a plain MarkBlueprintAsModified would NOT, and a
	// self-member Get built afterward would stay pinless. Skeleton regen is transaction-safe;
	// we do NOT full-compile here (see file header). Mirrors SWidgetDetailsView::HandleIsVariableChanged.
	void H_set_widget_is_variable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		const FString WidgetName = JStr(In, TEXT("widgetName"));
		UWidget* Widget = WBP->WidgetTree->FindWidget(FName(*WidgetName));   // operate on TEMPLATE widget
		if (!Widget)
		{
			Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s'"), *WidgetName));
			return;
		}
		const bool bIsVariable = JBool(In, TEXT("isVariable"), true);

		Widget->Modify();
		Widget->bIsVariable = bIsVariable;   // public bitfield; no SetIsVariable exists
		MarkStructural(WBP);                 // MarkBlueprintAsStructurallyModified -> skeleton regen -> FProperty exists

		Out->SetStringField(TEXT("widgetName"), Widget->GetFName().ToString());
		Out->SetBoolField(TEXT("isVariable"), bIsVariable);
		// The generated variable name is ALWAYS Widget->GetFName() (never the display label).
		Out->SetStringField(TEXT("variableName"), Widget->GetFName().ToString());
	}

	// --- add_widget_binding -------------------------------------------------
	// Push an editor-time FDelegateEditorBinding (widget.PropertyName -> pure UFUNCTION
	// FunctionName on the UserWidget). Identity is (ObjectName, PropertyName) only —
	// operator== ignores FunctionName — so Remove-then-AddUnique replaces any existing
	// bind on that property (exactly the designer's OnAddBinding sequence). SourcePath is
	// left EMPTY so the runtime binds via ScriptDelegate->BindUFunction(FunctionName)
	// (the fallback path). MemberGuid is resolved for rename-safety but is optional.
	void H_add_widget_binding(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		const FString WidgetName   = JStr(In, TEXT("widgetName"));
		const FString PropertyName = JStr(In, TEXT("propertyName"));
		const FString FunctionName = JStr(In, TEXT("functionName"));
		if (WidgetName.IsEmpty() || PropertyName.IsEmpty() || FunctionName.IsEmpty())
		{
			Fail(Out, TEXT("widgetName, propertyName and functionName are all required"));
			return;
		}
		// Target widget must exist in the tree or SanitizeBindings silently drops the bind on compile.
		if (!WBP->WidgetTree->FindWidget(FName(*WidgetName)))
		{
			Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s' (binding would be dropped on compile)"), *WidgetName));
			return;
		}

		WBP->Modify();

		FDelegateEditorBinding Binding;
		Binding.ObjectName   = WidgetName;                 // Object->GetName() — the member variable name
		Binding.PropertyName = FName(*PropertyName);       // e.g. "Text"
		Binding.FunctionName = FName(*FunctionName);       // e.g. "GetText"
		Binding.Kind         = EBindingKind::Function;
		// Leave SourceProperty = NAME_None and SourcePath empty -> runtime BindUFunction(FunctionName).

		// Optional rename-safety GUID: resolve the function graph on the skeleton class.
		// Invalid/zero GUID is fine — ToRuntimeBinding then uses the literal FunctionName.
		if (UBlueprintGeneratedClass* Skel = Cast<UBlueprintGeneratedClass>(WBP->SkeletonGeneratedClass))
		{
			UBlueprint::GetGuidFromClassByFieldName<UFunction>(Skel, Binding.FunctionName, Binding.MemberGuid);
		}

		WBP->Bindings.Remove(Binding);      // clears any prior bind on (WidgetName, PropertyName)
		WBP->Bindings.AddUnique(Binding);
		MarkStructural(WBP);

		Out->SetStringField(TEXT("widgetName"), WidgetName);
		Out->SetStringField(TEXT("propertyName"), PropertyName);
		Out->SetStringField(TEXT("functionName"), FunctionName);
		Out->SetNumberField(TEXT("bindingCount"), WBP->Bindings.Num());
		// The runtime FDelegateRuntimeBinding materialises only at the next FULL compile/cook.
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// --- remove_widget_binding ----------------------------------------------
	// Remove by identity (ObjectName + PropertyName only — a stub with just those two set
	// matches via operator==). Mirrors OnRemoveBinding.
	void H_remove_widget_binding(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		const FString WidgetName   = JStr(In, TEXT("widgetName"));
		const FString PropertyName = JStr(In, TEXT("propertyName"));
		if (WidgetName.IsEmpty() || PropertyName.IsEmpty())
		{
			Fail(Out, TEXT("widgetName and propertyName are required"));
			return;
		}

		WBP->Modify();

		FDelegateEditorBinding Key;
		Key.ObjectName   = WidgetName;
		Key.PropertyName = FName(*PropertyName);
		const int32 Removed = WBP->Bindings.Remove(Key);   // == ignores FunctionName/Kind/SourcePath
		MarkStructural(WBP);

		Out->SetNumberField(TEXT("removed"), Removed);
		Out->SetNumberField(TEXT("bindingCount"), WBP->Bindings.Num());
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// --- add_tree_widget ----------------------------------------------------
	// ConstructWidget into the tree, then either set it as RootWidget (asRoot / empty tree)
	// or AddChild it to an existing UPanelWidget parent. Mirrors the SHierarchyViewItem drop
	// path (SetFlags(RF_Transactional)+Modify on tree AND parent, AddChild, MarkStructural).
	// Runtime render requires a recompile (the compiler duplicates WidgetTree into the
	// generated class); the designer shows it live without one.
	void H_add_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		UWidgetTree* Tree = WBP->WidgetTree;

		const FString ClassName = JStr(In, TEXT("widgetClass"));
		UClass* WidgetClass = ResolveClass(ClassName, WBP);
		if (!WidgetClass || !WidgetClass->IsChildOf(UWidget::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not a UWidget class: '%s'"), *ClassName));
			return;
		}

		// Optional explicit name; uniquify against the tree if it collides (mirrors WidgetTemplateClass).
		FName WidgetName = NAME_None;
		const FString NameStr = JStr(In, TEXT("name"));
		if (!NameStr.IsEmpty())
		{
			WidgetName = FName(*NameStr);
			if (Tree->FindWidget(WidgetName))
			{
				WidgetName = MakeUniqueObjectName(Tree, WidgetClass, WidgetName);
			}
		}

		const bool bAsRoot = JBool(In, TEXT("asRoot"), false);
		const FString ParentName = JStr(In, TEXT("parentName"));

		// Decide placement BEFORE constructing, so we can fail cleanly.
		UPanelWidget* Parent = nullptr;
		const bool bRootCase = bAsRoot || (Tree->RootWidget == nullptr && ParentName.IsEmpty());
		if (bRootCase)
		{
			if (Tree->RootWidget != nullptr)
			{
				Fail(Out, TEXT("tree already has a root; pass parentName to add as a child, or remove the root first"));
				return;
			}
		}
		else
		{
			UWidget* ParentWidget = ParentName.IsEmpty() ? Tree->RootWidget : Tree->FindWidget(FName(*ParentName));
			if (!ParentWidget)
			{
				Fail(Out, FString::Printf(TEXT("parent widget not found: '%s'"), *ParentName));
				return;
			}
			Parent = Cast<UPanelWidget>(ParentWidget);
			if (!Parent)
			{
				Fail(Out, FString::Printf(TEXT("parent '%s' is not a panel (cannot hold children)"), *ParentWidget->GetName()));
				return;
			}
		}

		Tree->SetFlags(RF_Transactional);
		Tree->Modify();

		UWidget* NewWidget = Tree->ConstructWidget<UWidget>(WidgetClass, WidgetName);
		if (!NewWidget)
		{
			Fail(Out, TEXT("ConstructWidget returned null"));
			return;
		}

		if (bRootCase)
		{
			Tree->RootWidget = NewWidget;                 // no SetRootWidget(); assign the public field
		}
		else
		{
			Parent->SetFlags(RF_Transactional);
			Parent->Modify();
			UPanelSlot* Slot = Parent->AddChild(NewWidget); // null if panel is single-child and full
			if (!Slot)
			{
				Fail(Out, FString::Printf(TEXT("AddChild failed on parent '%s' (single-child panel already full?)"), *Parent->GetName()));
				return;
			}
			// Optional canvas placement (a fresh UCanvasPanelSlot already defaults to top-left anchors).
			if (UCanvasPanelSlot* CSlot = Cast<UCanvasPanelSlot>(Slot))
			{
				CSlot->SetPosition(FVector2D(JNum(In, TEXT("x")), JNum(In, TEXT("y"))));
				if (JBool(In, TEXT("autoSize"), true))
				{
					CSlot->SetAutoSize(true);
				}
			}
		}

		MarkStructural(WBP);

		Out->SetStringField(TEXT("widgetName"), NewWidget->GetFName().ToString());
		Out->SetStringField(TEXT("widgetClass"), WidgetClass->GetPathName());
		Out->SetBoolField(TEXT("asRoot"), bRootCase);
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// --- remove_tree_widget -------------------------------------------------
	// UWidgetTree::RemoveWidget handles all three cases (child / root / named-slot).
	void H_remove_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		const FString WidgetName = JStr(In, TEXT("widgetName"));
		UWidget* Widget = WBP->WidgetTree->FindWidget(FName(*WidgetName));
		if (!Widget)
		{
			Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s'"), *WidgetName));
			return;
		}

		WBP->WidgetTree->SetFlags(RF_Transactional);
		WBP->WidgetTree->Modify();
		const bool bRemoved = WBP->WidgetTree->RemoveWidget(Widget);
		MarkStructural(WBP);

		Out->SetBoolField(TEXT("removed"), bRemoved);
		Out->SetStringField(TEXT("widgetName"), WidgetName);
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}
}
