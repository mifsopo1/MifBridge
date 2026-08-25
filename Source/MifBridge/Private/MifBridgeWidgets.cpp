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
#include "WidgetBlueprintEditorUtils.h"            // Export/ImportWidgetsFromText - the headless copy/paste path
#include "Components/PanelSlot.h"                  // UPanelSlot returned by AddChild/InsertChildAt
#include "UObject/UObjectIterator.h"               // TObjectIterator - find tree-owned widgets the walk misses
#include "Animation/WidgetAnimation.h"              // UWidgetAnimation, FWidgetAnimationBinding
#include "MovieScene.h"                             // UMovieScene: display rate, tick resolution, playback range

namespace MifBridge
{
	namespace
	{
		// Seconds -> FFrameNumber in TICK space. The single most load-bearing conversion in this
		// group: a MovieScene stores times as ticks (typically 60000/1), NOT as display frames and
		// NOT as seconds. At 20fps display and 60000 tick resolution, 0.95s is 57000 ticks and frame
		// 19. Feeding it 0.95, or 19, puts the key somewhere else entirely and reports success.
		FFrameNumber SecondsToTicks(const UMovieScene* MovieScene, double Seconds)
		{
			return (Seconds * MovieScene->GetTickResolution()).FrameNumber;
		}

		double TicksToSeconds(const UMovieScene* MovieScene, FFrameNumber Ticks)
		{
			return MovieScene->GetTickResolution().AsSeconds(FFrameTime(Ticks));
		}

		UWidgetAnimation* FindAnimation(UWidgetBlueprint* WBP, const FString& Name)
		{
			for (UWidgetAnimation* Anim : WBP->Animations)
			{
				if (Anim && (Anim->GetFName() == FName(*Name) || Anim->GetDisplayLabel() == Name))
				{
					return Anim;
				}
			}
			return nullptr;
		}

		// Everything a caller needs to VERIFY the animation, not merely that one exists. The time
		// fields are emitted in both ticks and seconds on purpose - a wrong conversion is invisible
		// in one unit and obvious in two.
		TSharedRef<FJsonObject> SerializeAnimation(UWidgetAnimation* Anim)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), Anim->GetFName().ToString());
			J->SetStringField(TEXT("displayLabel"), Anim->GetDisplayLabel());
			UMovieScene* MS = Anim->GetMovieScene();
			if (!MS)
			{
				// Reported rather than skipped: an animation without a MovieScene is exactly the
				// half-created state this endpoint family exists to make impossible.
				J->SetBoolField(TEXT("hasMovieScene"), false);
				return J;
			}
			J->SetBoolField(TEXT("hasMovieScene"), true);
			const FFrameRate Display = MS->GetDisplayRate();
			const FFrameRate Tick = MS->GetTickResolution();
			J->SetStringField(TEXT("displayRate"), FString::Printf(TEXT("%d/%d"), Display.Numerator, Display.Denominator));
			J->SetStringField(TEXT("tickResolution"), FString::Printf(TEXT("%d/%d"), Tick.Numerator, Tick.Denominator));
			const TRange<FFrameNumber> Range = MS->GetPlaybackRange();
			if (Range.HasLowerBound() && Range.HasUpperBound())
			{
				J->SetNumberField(TEXT("startTick"), Range.GetLowerBoundValue().Value);
				J->SetNumberField(TEXT("endTick"), Range.GetUpperBoundValue().Value);
				J->SetNumberField(TEXT("startTime"), TicksToSeconds(MS, Range.GetLowerBoundValue()));
				J->SetNumberField(TEXT("endTime"), TicksToSeconds(MS, Range.GetUpperBoundValue()));
			}
			J->SetNumberField(TEXT("trackCount"), MS->GetTracks().Num());
			J->SetNumberField(TEXT("possessableCount"), MS->GetPossessableCount());

			TArray<TSharedPtr<FJsonValue>> Bindings;
			for (const FWidgetAnimationBinding& B : Anim->GetBindings())
			{
				TSharedRef<FJsonObject> BJ = MakeShared<FJsonObject>();
				BJ->SetStringField(TEXT("widgetName"), B.WidgetName.ToString());
				BJ->SetStringField(TEXT("animationGuid"), B.AnimationGuid.ToString());
				BJ->SetBoolField(TEXT("isRootWidget"), B.bIsRootWidget);
				Bindings.Add(MakeShared<FJsonValueObject>(BJ));
			}
			J->SetArrayField(TEXT("bindings"), Bindings);
			return J;
		}
	}

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

	// --- list_widget_animations ---------------------------------------------
	//   in:  { blueprintId | path }
	//   out: { count, animations:[{name, displayRate, tickResolution, start/endTick, start/endTime, ...}] }
	void H_list_widget_animations(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path) of a Widget Blueprint"),
			{ { TEXT("animationName"), TEXT("this lists them all — there is no single-animation read; the listing carries the full detail for each") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UWidgetAnimation* Anim : WBP->Animations)
		{
			if (Anim)
			{
				Arr.Add(MakeShared<FJsonValueObject>(SerializeAnimation(Anim)));
			}
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("animations"), Arr);
	}

	// --- add_widget_animation -----------------------------------------------
	//   in:  { blueprintId | path, name, startTime?, endTime?, displayRate? }
	//   out: { animation:{...} }
	void H_add_widget_animation(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("name"),
			  TEXT("startTime"), TEXT("endTime"), TEXT("displayRate") },
			TEXT("blueprintId (alias: path), name, startTime (seconds, default 0), endTime (seconds, "
				 "default 1), displayRate (fps, default 20)"),
			{ { TEXT("fps"), TEXT("the parameter is displayRate, in frames per second") },
			  { TEXT("duration"), TEXT("give endTime instead — the range is start..end, not a length") },
			  { TEXT("tickResolution"), TEXT("not settable here; the engine's default is used and list_widget_animations reports it, because keys are authored in TICK space") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty() || !IsValidIdentifier(Name))
		{
			Fail(Out, FString::Printf(
				TEXT("name must be a valid identifier (got '%s'). NOTHING was created."), *Name));
			return;
		}
		if (FindAnimation(WBP, Name))
		{
			Fail(Out, FString::Printf(
				TEXT("this widget blueprint already has an animation named '%s'. NOTHING was created; "
					 "list_widget_animations shows what is there."), *Name));
			return;
		}

		const double StartTime = JNum(In, TEXT("startTime"), 0.0);
		const double EndTime = JNum(In, TEXT("endTime"), 1.0);
		const int32 Fps = JInt(In, TEXT("displayRate"), 20);
		if (EndTime <= StartTime)
		{
			Fail(Out, FString::Printf(
				TEXT("endTime (%f) must be greater than startTime (%f). NOTHING was created."),
				EndTime, StartTime));
			return;
		}
		if (Fps <= 0)
		{
			Fail(Out, TEXT("displayRate must be a positive number of frames per second. NOTHING was created."));
			return;
		}

		WBP->Modify();

		// Mirrors AnimationTabSummoner.cpp:589. Order matters: the MovieScene is outered to the
		// ANIMATION, and the animation is not part of the asset until Animations.Add below.
		UWidgetAnimation* Anim = NewObject<UWidgetAnimation>(WBP, FName(), RF_Transactional);
		Anim->SetDisplayLabel(Name);
		Anim->Rename(*Name);
		Anim->MovieScene = NewObject<UMovieScene>(Anim, FName(*Name), RF_Transactional);
		Anim->MovieScene->SetDisplayRate(FFrameRate(Fps, 1));
		Anim->MovieScene->SetPlaybackRange(TRange<FFrameNumber>(
			SecondsToTicks(Anim->MovieScene, StartTime),
			SecondsToTicks(Anim->MovieScene, EndTime) + 1));   // +1 as the editor does — end is exclusive
		Anim->MovieScene->GetEditorData().WorkStart = StartTime;
		Anim->MovieScene->GetEditorData().WorkEnd = EndTime;

		// THE LINE THAT MAKES IT REAL. Without this the animation exists, compiles, and is not in
		// the widget - the failure mode this whole endpoint is written to avoid.
		WBP->Animations.Add(Anim);

		// Verify rather than assume, and specifically verify MEMBERSHIP by re-finding it through the
		// blueprint rather than reusing the pointer we already hold.
		UWidgetAnimation* ReadBack = FindAnimation(WBP, Name);
		if (!ReadBack || !ReadBack->GetMovieScene())
		{
			Fail(Out, TEXT("the animation was created but did not attach to the blueprint. WHAT IS "
						   "LEFT BEHIND: an orphaned UWidgetAnimation object; re-read with "
						   "list_widget_animations before retrying."));
			return;
		}

		MarkStructural(WBP);
		Out->SetObjectField(TEXT("animation"), SerializeAnimation(ReadBack));
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
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("isVariable") },
			TEXT("blueprintId (alias: path), widgetName, isVariable (default true)"),
			{ { TEXT("name"), TEXT("the widget parameter is called widgetName — the widget's FName in the tree, not its display label") },
			  { TEXT("widget"), TEXT("spell it widgetName") },
			  { TEXT("variableName"), TEXT("not settable here — the generated member variable is ALWAYS named after the widget itself; rename the widget to rename the variable") } }))
		{
			return;
		}

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
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyName"), TEXT("functionName") },
			TEXT("blueprintId (alias: path), widgetName, propertyName, functionName - all four required"),
			{ { TEXT("property"), TEXT("spell it propertyName (the widget property to drive, e.g. \"Text\")") },
			  { TEXT("function"), TEXT("spell it functionName (a pure UFUNCTION on the user widget, e.g. \"GetText\")") },
			  { TEXT("widget"), TEXT("spell it widgetName") },
			  { TEXT("kind"), TEXT("not settable — this endpoint only writes function bindings (EBindingKind::Function)") },
			  { TEXT("sourcePath"), TEXT("not settable — SourcePath is deliberately left empty so the runtime binds via BindUFunction(functionName)") } }))
		{
			return;
		}

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
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyName") },
			TEXT("blueprintId (alias: path), widgetName, propertyName - both required"),
			{ { TEXT("functionName"), TEXT("not part of the identity — a binding is removed by widgetName + propertyName alone, whatever function it points at") },
			  { TEXT("property"), TEXT("spell it propertyName") },
			  { TEXT("widget"), TEXT("spell it widgetName") } }))
		{
			return;
		}

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
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetClass"), TEXT("class"),
			  TEXT("name"), TEXT("parentName"), TEXT("asRoot"),
			  TEXT("x"), TEXT("y"), TEXT("autoSize") },
			TEXT("blueprintId (alias: path), widgetClass (alias: class), name (optional, uniquified on collision), ")
			TEXT("parentName or asRoot, and canvas-slot placement x, y, autoSize (default true)"),
			{ { TEXT("widgetName"), TEXT("the NEW widget's name parameter is called name; widgetName is only a response field") },
			  { TEXT("className"), TEXT("the class parameter is called widgetClass (alias: class)") },
			  { TEXT("parent"), TEXT("spell it parentName — the FName of a UPanelWidget already in the tree") },
			  { TEXT("position"), TEXT("pass the canvas-slot position as separate numbers x and y") },
			  { TEXT("size"), TEXT("not implemented — the canvas slot is auto-sized; set the slot's Size with set_property after adding") },
			  { TEXT("slot"), TEXT("slot properties beyond x/y/autoSize are not settable here — use set_property on the created widget's Slot") } }))
		{
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		UWidgetTree* Tree = WBP->WidgetTree;

		// STRICT — an empty widgetClass used to resolve to the widget blueprint's OWN class, which
		// IS a UWidget subclass, so the guard passed and the tree got a self-referencing child.
		UClass* WidgetClass = ResolveClassStrictField(In, { TEXT("widgetClass"), TEXT("class") }, WBP, Out);
		if (!WidgetClass)
		{
			return;
		}
		if (!WidgetClass->IsChildOf(UWidget::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not a UWidget class: '%s'"), *WidgetClass->GetName()));
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

		// Placement keys are meaningful only on a canvas slot. Checked BEFORE the widget is constructed
		// so a request that cannot be honoured does not leave anything behind: adding to a VerticalBox
		// with x:100, y:50 used to return ok:true having ignored both.
		const bool bWantsPlacement = JHasAny(In, { TEXT("x"), TEXT("y"), TEXT("autoSize") });
		if (bWantsPlacement && bRootCase)
		{
			Fail(Out, TEXT("x/y/autoSize position a widget inside a CanvasPanel slot; the ROOT widget has no slot. ")
				TEXT("Add a CanvasPanel as the root first, then add this widget to it."));
			return;
		}
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
				// ConstructWidget already ran, so failing here left an orphan UWidget in the tree's
				// outer. RunEndpoint cancels the transaction on ok:false, but the object itself is not
				// transaction-managed, so mark it garbage explicitly rather than relying on that.
				NewWidget->MarkAsGarbage();
				Fail(Out, FString::Printf(TEXT("AddChild failed on parent '%s' (single-child panel already full?)"), *Parent->GetName()));
				return;
			}
			// Optional canvas placement (a fresh UCanvasPanelSlot already defaults to top-left anchors).
			UCanvasPanelSlot* CSlot = Cast<UCanvasPanelSlot>(Slot);
			// The slot type is only knowable AFTER AddChild (UPanelWidget::GetSlotClass is protected,
			// so it cannot be asked in advance from outside the module). That made this check
			// non-atomic: it returned ok:false having already parented the widget, and the caller
			// found "BadLine" sitting in the tree after a failed call. Unwind before failing, so a
			// rejected request genuinely leaves nothing behind.
			if (!CSlot && bWantsPlacement)
			{
				Parent->RemoveChild(NewWidget);
				Tree->RemoveWidget(NewWidget);
			}
			if (!CSlot && bWantsPlacement)
			{
				NewWidget->MarkAsGarbage();
				Fail(Out, FString::Printf(
					TEXT("x/y/autoSize apply to a CanvasPanel slot, but '%s' is a %s and gave this child a %s — they would ")
					TEXT("have been ignored. Remove them, or parent this widget to a CanvasPanel. Nothing was created."),
					*Parent->GetName(), *Parent->GetClass()->GetName(), *Slot->GetClass()->GetName()));
				return;
			}
			if (CSlot)
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
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName") },
			TEXT("blueprintId (alias: path), widgetName"),
			{ { TEXT("name"), TEXT("the widget parameter is called widgetName") },
			  { TEXT("widget"), TEXT("spell it widgetName") },
			  { TEXT("recursive"), TEXT("not a parameter — RemoveWidget always takes the widget's whole subtree with it") } }))
		{
			return;
		}

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

	// ---------------------------------------------------------------------------
	// Tree topology: list, duplicate, wrap, move.
	//
	// These exist because the tree was effectively write-only: add created, remove deleted, and nothing
	// could read the shape or rearrange it. Callers were reduced to get_property "Slot" one widget at a
	// time, and only on Is-Variable-flagged widgets, since the rest have no member to address.
	//
	// The engine designer actions (FWidgetBlueprintEditorUtils::DuplicateWidgets / WrapWidgets) take a
	// TSharedRef<FWidgetBlueprintEditor> — an OPEN asset editor — so they are unusable from a headless
	// bridge. ExportWidgetsToText / ImportWidgetsFromText take only the UWidgetBlueprint, so duplicate
	// rides the engine real copy/paste path (carrying the whole subtree and every property value);
	// wrap and move do the panel surgery directly.
	// ---------------------------------------------------------------------------

	namespace
	{
		// True if Candidate is Root or anywhere beneath it. Re-parenting a panel into its own
		// descendant builds a cycle, and the next tree walk never terminates.
		static bool IsSelfOrDescendant(UWidget* Root, UWidget* Candidate)
		{
			if (!Root || !Candidate) { return false; }
			if (Root == Candidate) { return true; }
			UPanelWidget* Panel = Cast<UPanelWidget>(Root);
			if (!Panel) { return false; }
			for (int32 i = 0; i < Panel->GetChildrenCount(); ++i)
			{
				if (IsSelfOrDescendant(Panel->GetChildAt(i), Candidate)) { return true; }
			}
			return false;
		}

		// Detach from whatever holds the widget, reporting where it came from. Handles the ROOT case,
		// which is not a panel child and would otherwise be silently left in place.
		static void DetachFromParent(UWidgetTree* Tree, UWidget* Widget, FString& OutFromParent, int32& OutFromIndex)
		{
			OutFromIndex = INDEX_NONE;
			OutFromParent = TEXT("");
			if (UPanelWidget* Old = Widget->GetParent())
			{
				OutFromParent = Old->GetName();
				OutFromIndex = Old->GetChildIndex(Widget);
				Old->SetFlags(RF_Transactional);
				Old->Modify();
				Old->RemoveChild(Widget);
			}
			else if (Tree->RootWidget == Widget)
			{
				OutFromParent = TEXT("<root>");
				Tree->RootWidget = nullptr;
			}
		}
	}

	// --- list_tree_widgets --------------------------------------------------
	//   in:  { blueprintId | path }
	//   out: { root, count, widgets:[{name,class,parent,index,slotClass,isVariable,isPanel,childCount}] }
	// Read-only. The call that makes the rest of the tree addressable: every other tree endpoint takes a
	// widgetName, and before this there was no way to discover them.
	void H_list_tree_widgets(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path)"),
			{ { TEXT("widgetName"), TEXT("this endpoint lists the WHOLE tree; there is no per-widget filter") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }
		UWidgetTree* Tree = WBP->WidgetTree;

		// Two sources, unioned, because neither alone tells the truth.
		//   * GetAllWidgets walks from RootWidget via ForWidgetAndChildren, which reads
		//     UPanelWidget::GetChildAt. A panel whose Slots carry a null Content therefore reports a
		//     non-zero GetChildrenCount while contributing NOTHING to the walk — the tree silently
		//     under-reports and a caller believes those widgets do not exist.
		//   * The tree also owns widgets that are not under the root at all (named-slot content,
		//     and anything orphaned by a failed edit).
		// So: take the walk, then sweep every UWidget outered to the tree and mark whatever the walk
		// missed as unreachable. A broken tree becomes a reported number instead of a silent absence.
		TArray<UWidget*> All;
		Tree->GetAllWidgets(All);
		TSet<UWidget*> Reachable(All);

		int32 NullSlots = 0;
		for (TObjectIterator<UWidget> It; It; ++It)
		{
			UWidget* W = *It;
			if (W && IsValid(W) && W->GetOuter() == Tree && !Reachable.Contains(W))
			{
				All.Add(W);
			}
		}

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UWidget* W : All)
		{
			if (!W) { continue; }
			int32 ChildIndex = INDEX_NONE;
			UPanelWidget* Parent = UWidgetTree::FindWidgetParent(W, ChildIndex);

			TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetStringField(TEXT("name"), W->GetName());
			O->SetStringField(TEXT("class"), W->GetClass()->GetPathName());
			O->SetStringField(TEXT("classShort"), W->GetClass()->GetName());
			O->SetStringField(TEXT("parent"), Parent ? Parent->GetName()
				: (Tree->RootWidget == W ? TEXT("<root>") : TEXT("")));
			O->SetNumberField(TEXT("index"), ChildIndex);
			// The slot CLASS is the useful half: it says which layout properties even exist. A
			// UCanvasPanelSlot takes x/y; a UVerticalBoxSlot does not, which is the whole reason
			// add_tree_widget rejects x/y on a box parent.
			O->SetStringField(TEXT("slotClass"), W->Slot ? W->Slot->GetClass()->GetName() : TEXT(""));
			O->SetBoolField(TEXT("isVariable"), W->bIsVariable != 0);
			O->SetBoolField(TEXT("reachable"), Reachable.Contains(W));
			UPanelWidget* AsPanel = Cast<UPanelWidget>(W);
			O->SetBoolField(TEXT("isPanel"), AsPanel != nullptr);
			if (AsPanel)
			{
				const int32 SlotCount = AsPanel->GetChildrenCount();
				// Count slots whose Content is null. childCount counts SLOTS; a null Content is a slot
				// that exists with nothing in it, which is exactly what makes the walk skip a child.
				int32 Live = 0;
				for (int32 i = 0; i < SlotCount; ++i)
				{
					if (AsPanel->GetChildAt(i)) { ++Live; } else { ++NullSlots; }
				}
				O->SetNumberField(TEXT("childCount"), Live);
				O->SetNumberField(TEXT("slotCount"), SlotCount);
				if (Live != SlotCount) { O->SetNumberField(TEXT("emptySlots"), SlotCount - Live); }
				O->SetBoolField(TEXT("canHaveMultipleChildren"), AsPanel->CanHaveMultipleChildren());
			}
			else
			{
				O->SetNumberField(TEXT("childCount"), 0);
			}
			Arr.Add(MakeShared<FJsonValueObject>(O));
		}

		Out->SetStringField(TEXT("root"), Tree->RootWidget ? Tree->RootWidget->GetName() : TEXT(""));
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetNumberField(TEXT("reachableCount"), Reachable.Num());
		Out->SetArrayField(TEXT("widgets"), Arr);
		if (Arr.Num() != Reachable.Num())
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("%d widget(s) are owned by the tree but NOT reachable from the root - they are listed with reachable:false. They will not render."),
				Arr.Num() - Reachable.Num()));
		}
		if (NullSlots > 0)
		{
			Out->SetNumberField(TEXT("emptySlotTotal"), NullSlots);
			Out->SetStringField(TEXT("slotWarning"), FString::Printf(
				TEXT("%d panel slot(s) exist with NO content. childCount reports live children; slotCount reports slots. A gap between them means an edit left slots behind."),
				NullSlots));
		}
	}

	// --- duplicate_tree_widget ----------------------------------------------
	//   in:  { blueprintId | path, widgetName, parentName?, index? }
	//   out: { created:[...], primary, parent, index }
	// Clones a widget AND its whole subtree through the engine copy/paste text path, so property values
	// and child structure come along. parentName defaults to the source own parent — "duplicate beside
	// the original", which is what the Designer Duplicate does.
	void H_duplicate_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("parentName"), TEXT("index") },
			TEXT("blueprintId (alias: path), widgetName, parentName (optional - defaults to the source own parent), index (optional insert position)"),
			{ { TEXT("newName"), TEXT("the clone name is assigned by the engine paste path to keep it unique; rename afterwards if you need a specific one") },
			  { TEXT("widget"), TEXT("spell it widgetName") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }
		UWidgetTree* Tree = WBP->WidgetTree;

		const FString WidgetName = JStr(In, TEXT("widgetName"));
		if (WidgetName.IsEmpty()) { Fail(Out, TEXT("widgetName is required")); return; }
		UWidget* Source = Tree->FindWidget(FName(*WidgetName));
		if (!Source) { Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s'"), *WidgetName)); return; }

		// Resolve the destination parent BEFORE cloning, so a bad parentName leaves nothing behind.
		const FString ParentName = JStr(In, TEXT("parentName"));
		UPanelWidget* Dest = nullptr;
		if (!ParentName.IsEmpty())
		{
			UWidget* DestWidget = Tree->FindWidget(FName(*ParentName));
			if (!DestWidget) { Fail(Out, FString::Printf(TEXT("parent widget not found: '%s'"), *ParentName)); return; }
			Dest = Cast<UPanelWidget>(DestWidget);
			if (!Dest) { Fail(Out, FString::Printf(TEXT("parent '%s' is not a panel (cannot hold children)"), *ParentName)); return; }
		}
		else
		{
			Dest = Source->GetParent();
			if (!Dest)
			{
				Fail(Out, TEXT("source is the ROOT widget and has no parent to duplicate into - pass parentName"));
				return;
			}
		}
		if (!Dest->CanAddMoreChildren())
		{
			Fail(Out, FString::Printf(TEXT("parent '%s' is a single-child panel and is already full"), *Dest->GetName()));
			return;
		}

		Tree->SetFlags(RF_Transactional);
		Tree->Modify();
		WBP->Modify();

		// Export the SOURCE PLUS EVERY DESCENDANT. Exporting {Source} alone produced a clone whose
		// slots existed but whose Content was null - a VerticalBox reporting slotCount 3 and
		// childCount 0. The slot records travel with the panel; the child widgets only travel if they
		// are in the exported set, so the paste rebuilt the slots pointing at nothing.
		TArray<UWidget*> ToExport;
		UWidgetTree::GetChildWidgets(Source, ToExport);   // includes Source itself
		if (!ToExport.Contains(Source)) { ToExport.Insert(Source, 0); }

		FString Exported;
		FWidgetBlueprintEditorUtils::ExportWidgetsToText(ToExport, Exported);
		TSet<UWidget*> Imported;
		TMap<FName, UWidgetSlotPair*> SlotData;
		FWidgetBlueprintEditorUtils::ImportWidgetsFromText(WBP, Exported, Imported, SlotData);
		if (Imported.Num() == 0)
		{
			Fail(Out, FString::Printf(TEXT("copy/paste of '%s' produced no widgets"), *WidgetName));
			return;
		}

		// Import brings the whole subtree in, but only the TOP of it needs parenting - the children
		// already point at their cloned parents. The top is the one whose parent is outside the set.
		UWidget* Primary = nullptr;
		TArray<TSharedPtr<FJsonValue>> Created;
		for (UWidget* W : Imported)
		{
			if (!W) { continue; }
			Created.Add(MakeShared<FJsonValueString>(W->GetName()));
			if (!Primary && !Imported.Contains(W->GetParent())) { Primary = W; }
		}
		if (!Primary) { Primary = *Imported.CreateIterator(); }

		Dest->SetFlags(RF_Transactional);
		Dest->Modify();
		const int32 WantIndex = JHasAny(In, { TEXT("index") }) ? JInt(In, TEXT("index"), 0) : INDEX_NONE;
		UPanelSlot* Slot = (WantIndex >= 0 && WantIndex <= Dest->GetChildrenCount())
			? Dest->InsertChildAt(WantIndex, Primary)
			: Dest->AddChild(Primary);
		if (!Slot)
		{
			Fail(Out, FString::Printf(TEXT("failed to parent the clone under '%s'"), *Dest->GetName()));
			return;
		}

		MarkStructural(WBP);
		Out->SetArrayField(TEXT("created"), Created);
		Out->SetStringField(TEXT("primary"), Primary->GetName());
		Out->SetStringField(TEXT("parent"), Dest->GetName());
		Out->SetNumberField(TEXT("index"), Dest->GetChildIndex(Primary));
		Out->SetStringField(TEXT("slotClass"), Slot->GetClass()->GetName());
		Out->SetNumberField(TEXT("sourceSubtreeSize"), ToExport.Num());
		Out->SetNumberField(TEXT("clonedCount"), Created.Num());
		if (Created.Num() != ToExport.Num())
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("cloned %d widget(s) from a %d-widget subtree - the clone is INCOMPLETE. Verify with list_tree_widgets (emptySlots)."),
				Created.Num(), ToExport.Num()));
		}
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// --- wrap_tree_widget ---------------------------------------------------
	//   in:  { blueprintId | path, widgetName, wrapperClass, wrapperName? }
	//   out: { wrapper, wrapped, parent, index, wasRoot }
	// The Designer "Wrap With": insert a new panel where the widget sits, then move the widget inside it.
	// Handles the ROOT case, which has no parent slot to inherit.
	void H_wrap_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("wrapperClass"), TEXT("wrapperName") },
			TEXT("blueprintId (alias: path), widgetName, wrapperClass (a UPanelWidget class), wrapperName (optional)"),
			{ { TEXT("class"), TEXT("spell it wrapperClass - the PANEL to wrap with, not the widget being wrapped") },
			  { TEXT("panelClass"), TEXT("spell it wrapperClass") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }
		UWidgetTree* Tree = WBP->WidgetTree;

		const FString WidgetName = JStr(In, TEXT("widgetName"));
		if (WidgetName.IsEmpty()) { Fail(Out, TEXT("widgetName is required")); return; }
		UWidget* Target = Tree->FindWidget(FName(*WidgetName));
		if (!Target) { Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s'"), *WidgetName)); return; }

		UClass* WrapperClass = ResolveClassStrictField(In, { TEXT("wrapperClass") }, nullptr, Out);
		if (!WrapperClass) { return; }
		if (!WrapperClass->IsChildOf(UPanelWidget::StaticClass()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a UPanelWidget - only a panel can wrap a widget (try CanvasPanel, VerticalBox, HorizontalBox, Overlay, SizeBox, Border)"),
				*WrapperClass->GetName()));
			return;
		}
		if (WrapperClass->HasAnyClassFlags(CLASS_Abstract))
		{
			Fail(Out, FString::Printf(TEXT("'%s' is abstract and cannot be constructed"), *WrapperClass->GetName()));
			return;
		}

		// Capture the original position BEFORE any mutation.
		UPanelWidget* OldParent = Target->GetParent();
		const bool bTargetIsRoot = (Tree->RootWidget == Target);
		const int32 OldIndex = OldParent ? OldParent->GetChildIndex(Target) : INDEX_NONE;
		if (!OldParent && !bTargetIsRoot)
		{
			Fail(Out, FString::Printf(TEXT("'%s' has no parent and is not the root - it is orphaned and cannot be wrapped"), *WidgetName));
			return;
		}

		Tree->SetFlags(RF_Transactional);
		Tree->Modify();
		WBP->Modify();

		const FString WrapperName = JStr(In, TEXT("wrapperName"));
		UPanelWidget* Wrapper = Cast<UPanelWidget>(WrapperName.IsEmpty()
			? Tree->ConstructWidget<UWidget>(WrapperClass)
			: Tree->ConstructWidget<UWidget>(WrapperClass, FName(*WrapperName)));
		if (!Wrapper) { Fail(Out, TEXT("failed to construct the wrapper panel")); return; }

		if (OldParent)
		{
			OldParent->SetFlags(RF_Transactional);
			OldParent->Modify();
			OldParent->RemoveChild(Target);
			// Put the wrapper back at the SAME index so sibling order survives.
			if (!OldParent->InsertChildAt(OldIndex, Wrapper))
			{
				OldParent->AddChild(Wrapper);
			}
		}
		else
		{
			Tree->RootWidget = Wrapper;
		}

		if (!Wrapper->AddChild(Target))
		{
			Fail(Out, FString::Printf(TEXT("wrapper '%s' refused the child (single-child panel already full?)"), *Wrapper->GetName()));
			return;
		}

		MarkStructural(WBP);
		Out->SetStringField(TEXT("wrapper"), Wrapper->GetName());
		Out->SetStringField(TEXT("wrapperClass"), Wrapper->GetClass()->GetName());
		Out->SetStringField(TEXT("wrapped"), Target->GetName());
		Out->SetStringField(TEXT("parent"), OldParent ? OldParent->GetName() : TEXT("<root>"));
		Out->SetNumberField(TEXT("index"), OldParent ? OldParent->GetChildIndex(Wrapper) : 0);
		Out->SetBoolField(TEXT("wasRoot"), bTargetIsRoot);
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// --- move_tree_widget ---------------------------------------------------
	//   in:  { blueprintId | path, widgetName, parentName | asRoot, index? }
	//   out: { widget, fromParent, fromIndex, toParent, index }
	// Reparent an EXISTING widget. add_tree_widget creates and remove_tree_widget deletes; there was no
	// way to move one, so rearranging meant delete + recreate, losing every property already set.
	void H_move_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("parentName"), TEXT("asRoot"), TEXT("index"),
			  TEXT("replaceRoot"), TEXT("confirm") },
			TEXT("blueprintId (alias: path), widgetName, parentName (the new parent panel) OR asRoot:true (+ replaceRoot:true if a root already exists), index (optional position within the new parent)"),
			{ { TEXT("newParent"), TEXT("spell it parentName") },
			  { TEXT("x"), TEXT("move changes PARENTAGE only; set slot layout afterwards with set_property on the widget Slot") },
			  { TEXT("y"), TEXT("move changes PARENTAGE only; set slot layout afterwards with set_property on the widget Slot") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }
		UWidgetTree* Tree = WBP->WidgetTree;

		const FString WidgetName = JStr(In, TEXT("widgetName"));
		if (WidgetName.IsEmpty()) { Fail(Out, TEXT("widgetName is required")); return; }
		UWidget* Target = Tree->FindWidget(FName(*WidgetName));
		if (!Target) { Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s'"), *WidgetName)); return; }

		const bool bAsRoot = JBool(In, TEXT("asRoot"), false);
		const FString ParentName = JStr(In, TEXT("parentName"));
		if (!bAsRoot && ParentName.IsEmpty())
		{
			Fail(Out, TEXT("pass parentName (the new parent panel) or asRoot:true"));
			return;
		}

		UPanelWidget* Dest = nullptr;
		if (!bAsRoot)
		{
			UWidget* DestWidget = Tree->FindWidget(FName(*ParentName));
			if (!DestWidget) { Fail(Out, FString::Printf(TEXT("parent widget not found: '%s'"), *ParentName)); return; }
			Dest = Cast<UPanelWidget>(DestWidget);
			if (!Dest) { Fail(Out, FString::Printf(TEXT("parent '%s' is not a panel (cannot hold children)"), *ParentName)); return; }

			// The check that matters: parenting a panel under itself or its own descendant builds a
			// cycle, and the next tree walk never returns.
			if (IsSelfOrDescendant(Target, Dest))
			{
				Fail(Out, FString::Printf(
					TEXT("cannot move '%s' into '%s' - that is itself or one of its own descendants, which would create a cycle"),
					*WidgetName, *ParentName));
				return;
			}
			if (Target->GetParent() != Dest && !Dest->CanAddMoreChildren())
			{
				Fail(Out, FString::Printf(TEXT("parent '%s' is a single-child panel and is already full"), *ParentName));
				return;
			}
		}
		else if (Tree->RootWidget == Target)
		{
			Fail(Out, TEXT("widget is already the root"));
			return;
		}
		else if (Tree->RootWidget != nullptr)
		{
			// Promoting to root DISPLACES whatever is there, and the displaced root plus its entire
			// subtree stop being part of the widget hierarchy - measured: a 12-widget tree became 5.
			// That is a delete wearing a move's clothing, so it needs the same explicit opt-in every
			// other destructive endpoint here demands. An earlier version only emitted a warning, and
			// the warning was wrong as well: it claimed the old root was "still in the tree object".
			if (!JBoolAny(In, { TEXT("replaceRoot"), TEXT("confirm") }, false))
			{
				TArray<UWidget*> Doomed;
				UWidgetTree::GetChildWidgets(Tree->RootWidget, Doomed);
				Fail(Out, FString::Printf(
					TEXT("asRoot would displace the current root '%s' and drop it and its %d-widget subtree out of the hierarchy. ")
					TEXT("Pass replaceRoot:true to accept that, or move '%s' under an existing panel instead."),
					*Tree->RootWidget->GetName(), Doomed.Num(), *WidgetName));
				return;
			}
		}

		Tree->SetFlags(RF_Transactional);
		Tree->Modify();
		WBP->Modify();
		Target->SetFlags(RF_Transactional);
		Target->Modify();

		// Size the displaced subtree BEFORE detaching. Measured after, the target has already left its
		// old parent, so promoting the only child of the root reported "0-widget subtree" on the accept
		// path while the refusal path (which runs first, undetached) correctly said 2.
		int32 DisplacedSize = 0;
		UWidget* PrevRootSnapshot = Tree->RootWidget;
		if (bAsRoot && PrevRootSnapshot && PrevRootSnapshot != Target)
		{
			TArray<UWidget*> Doomed;
			UWidgetTree::GetChildWidgets(PrevRootSnapshot, Doomed);
			DisplacedSize = Doomed.Num();
		}

		FString FromParent;
		int32 FromIndex = INDEX_NONE;
		DetachFromParent(Tree, Target, FromParent, FromIndex);

		int32 NewIndex = INDEX_NONE;
		if (bAsRoot)
		{
			// Whatever was root becomes orphaned unless the caller re-homes it. Say so rather than
			// silently dropping it out of the visible tree.
			UWidget* PrevRoot = Tree->RootWidget;
			const int32 Displaced = DisplacedSize;   // snapshot taken before detach
			Tree->RootWidget = Target;
			if (PrevRoot && PrevRoot != Target)
			{
				Out->SetStringField(TEXT("displacedRoot"), PrevRoot->GetName());
				Out->SetNumberField(TEXT("displacedSubtreeSize"), Displaced);
				Out->SetStringField(TEXT("warning"), FString::Printf(
					TEXT("'%s' and its %d-widget subtree are no longer in the hierarchy and will not render. This was accepted via replaceRoot."),
					*PrevRoot->GetName(), Displaced));
			}
			NewIndex = 0;
		}
		else
		{
			Dest->SetFlags(RF_Transactional);
			Dest->Modify();
			const int32 WantIndex = JHasAny(In, { TEXT("index") }) ? JInt(In, TEXT("index"), 0) : INDEX_NONE;
			UPanelSlot* Slot = (WantIndex >= 0 && WantIndex <= Dest->GetChildrenCount())
				? Dest->InsertChildAt(WantIndex, Target)
				: Dest->AddChild(Target);
			if (!Slot)
			{
				Fail(Out, FString::Printf(TEXT("failed to parent '%s' under '%s' - the widget is now DETACHED; re-add it"),
					*WidgetName, *Dest->GetName()));
				return;
			}
			NewIndex = Dest->GetChildIndex(Target);
			Out->SetStringField(TEXT("slotClass"), Slot->GetClass()->GetName());
		}

		MarkStructural(WBP);
		Out->SetStringField(TEXT("widget"), Target->GetName());
		Out->SetStringField(TEXT("fromParent"), FromParent);
		Out->SetNumberField(TEXT("fromIndex"), FromIndex);
		Out->SetStringField(TEXT("toParent"), bAsRoot ? TEXT("<root>") : Dest->GetName());
		Out->SetNumberField(TEXT("index"), NewIndex);
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}
}
