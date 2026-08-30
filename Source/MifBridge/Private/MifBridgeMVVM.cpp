// MVVM — Widget Blueprint View Bindings: wire which viewmodel property drives which widget property.
//
// The OTHER HALF of the MVVM work from 2026-08-27, left explicitly unexplored at the time
// ("NOT YET DONE: the other half of MVVM, wiring a Widget Blueprint's View Bindings... unexplored").
// That earlier work (set_variable_flags' fieldNotify) made a Blueprint variable MVVM-BINDABLE; this
// file is what actually CONNECTS one to a widget.
//
// TWO NEW MODULE DEPENDENCIES, not the one already linked. The base `ModelViewViewModel` module
// (already linked since 2026-08-26) only carries the RUNTIME surface - UMVVMViewModelBase, the
// FieldNotify machinery - which is why the earlier work needed no new dependency at all. Everything a
// caller actually authors a binding WITH lives elsewhere: UMVVMEditorSubsystem in
// ModelViewViewModelEditor, and UMVVMBlueprintView / FMVVMBlueprintPropertyPath /
// FMVVMBlueprintViewBinding in ModelViewViewModelBlueprint. Both added to Build.cs under the existing
// MIF_WITH_MVVM guard - verified present under Engine/Plugins/Runtime in both 5.3.2 and 5.7 first.
//
// WHY THE EDITOR SUBSYSTEM, NOT THE RAW VIEW DATA DIRECTLY. UMVVMBlueprintView's own fields
// (AvailableViewModels, Bindings) are readable directly, and this file does read them for
// describe_mvvm_view - but WRITES go through UMVVMEditorSubsystem's setters (SetSourcePathForBinding
// etc.) rather than assigning FMVVMBlueprintViewBinding::SourcePath directly, even though that field is
// public. The setters are what the MVVM Editor's own UI calls, and are the only place bookkeeping this
// file has no visibility into (compile-dirty flags, message clearing) is guaranteed to happen -
// matching the general project discipline of using the engine's own authoring entry point over poking
// a data structure that happens to be public.
//
// FMVVMBlueprintPropertyPath, THE PART WORTH READING CAREFULLY. A path is rooted at either a
// viewmodel (SetViewModelId, an FGuid - found via UMVVMBlueprintView::FindViewModel(Name), not
// constructed) or a named widget (SetWidgetName, resolved through the Widget Blueprint's own
// WidgetTree - a UBaseWidgetBlueprint field, not part of this plugin at all). The actual property is
// set via SetPropertyPath(Blueprint, FMVVMConstFieldVariant(RealFProperty)) - the FProperty is
// resolved through ordinary UStruct::FindPropertyByName on the viewmodel/widget CLASS, the same
// reflection pattern GAS's add_gameplay_effect_modifier already uses for FGameplayAttribute.

#include "MifBridgeHandlers.h"
#include "MifBridgeVersion.h"
#include "MifBridgeLog.h"

#if MIF_WITH_MVVM
// MVVMEditorSubsystem.h and MVVMPropertyPath.h both end with a `#if
// UE_ENABLE_INCLUDE_ORDER_DEPRECATED_IN_5_2` backward-compat block that reaches for headers under
// their OWN module's Private/ folder (e.g. ModelViewViewModelEditor/Private/Types/MVVMBindingSource.h) -
// invisible outside that module's own compilation, so it fatal-errors the moment an EXTERNAL module
// (this one) includes either header with that macro true. MifBridge does not opt into the newer
// IWYU-style include order, so UBT defines it true here by default - live-confirmed (C1083, cannot
// open MVVMBindingSource.h). Locally forcing it false for the duration of these includes only skips
// that dead compat block; nothing this file uses comes from it. UBT injects the macro as a plain
// preprocessor define (TargetRules.cs), so a local #undef/#define is a legitimate, standard override,
// not a hack around anything load-bearing.
#undef UE_ENABLE_INCLUDE_ORDER_DEPRECATED_IN_5_2
#define UE_ENABLE_INCLUDE_ORDER_DEPRECATED_IN_5_2 0
#include "MVVMEditorSubsystem.h"
#include "MVVMBlueprintView.h"
#include "MVVMBlueprintViewBinding.h"
#include "MVVMBlueprintViewModelContext.h"
#include "MVVMPropertyPath.h"
#include "Types/MVVMBindingMode.h"
#include "WidgetBlueprint.h"
#include "Blueprint/WidgetTree.h"
#include "Components/Widget.h"
#include "Editor.h"
#include "Editor/EditorEngine.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_MVVM
	static void MifNoMVVM(const TSharedRef<FJsonObject>& Out)
	{
		Fail(Out, TEXT("this engine build has no ModelViewViewModel plugin, so there is no MVVM view ")
					  TEXT("to bind. The endpoint exists on every build deliberately - a missing endpoint ")
					  TEXT("would tell you nothing, while this tells you the plugin is what is missing."));
	}
	void H_add_mvvm_viewmodel(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoMVVM(Out);
	}
	void H_add_mvvm_binding(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoMVVM(Out);
	}
	void H_describe_mvvm_view(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoMVVM(Out);
	}
	void H_remove_mvvm_viewmodel(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoMVVM(Out);
	}
	void H_remove_mvvm_binding(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoMVVM(Out);
	}
#else

	namespace
	{
		// NOT ResolveBlueprintField directly - that helper only reads "blueprintId"/"path", so
		// "widgetBlueprintPath" (this file's own, clearer name for the same thing, listed as an
		// accepted key below) would be silently IGNORED rather than resolved: live-verified before this
		// fix - a call passing only widgetBlueprintPath failed with "missing blueprint path/blueprintId"
		// even though widgetBlueprintPath was right there in the payload. Resolving the path ourselves
		// with all three spellings, then calling the lower-level ResolveBlueprint(FString&, FString&)
		// directly, is the fix - same underlying resolution, just fed from the right key.
		UWidgetBlueprint* ResolveWidgetBlueprintField(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
		{
			const FString Path = JStrAny(In, { TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId") });
			if (Path.IsEmpty())
			{
				Fail(Out, TEXT("widgetBlueprintPath (aliases: path, blueprintId) is required"));
				return nullptr;
			}
			FString Error;
			UBlueprint* Blueprint = ResolveBlueprint(Path, Error);
			if (!Blueprint)
			{
				Fail(Out, Error);
				return nullptr;
			}
			UWidgetBlueprint* WBP = Cast<UWidgetBlueprint>(Blueprint);
			if (!WBP)
			{
				Fail(Out, FString::Printf(TEXT("not a Widget Blueprint: '%s'"), *Blueprint->GetPathName()));
				return nullptr;
			}
			return WBP;
		}

		UMVVMEditorSubsystem* GetMVVMSubsystem()
		{
			return GEditor ? GEditor->GetEditorSubsystem<UMVVMEditorSubsystem>() : nullptr;
		}

		bool ParseBindingMode(const FString& In, EMVVMBindingMode& OutMode, FString& OutError)
		{
			const FString Lower = In.ToLower();
			if (Lower.IsEmpty() || Lower == TEXT("onewaytodestination")) { OutMode = EMVVMBindingMode::OneWayToDestination; return true; }
			if (Lower == TEXT("onetimetodestination")) { OutMode = EMVVMBindingMode::OneTimeToDestination; return true; }
			if (Lower == TEXT("twoway")) { OutMode = EMVVMBindingMode::TwoWay; return true; }
			if (Lower == TEXT("onewaytosource")) { OutMode = EMVVMBindingMode::OneWayToSource; return true; }
			OutError = FString::Printf(
				TEXT("bindingMode '%s' is not one of oneWayToDestination (default), oneTimeToDestination, ")
				TEXT("twoWay, oneWayToSource."), *In);
			return false;
		}

		const TCHAR* BindingModeToString(EMVVMBindingMode Mode)
		{
			switch (Mode)
			{
			case EMVVMBindingMode::OneTimeToDestination: return TEXT("oneTimeToDestination");
			case EMVVMBindingMode::OneWayToDestination: return TEXT("oneWayToDestination");
			case EMVVMBindingMode::TwoWay: return TEXT("twoWay");
			case EMVVMBindingMode::OneWayToSource: return TEXT("oneWayToSource");
			default: return TEXT("oneTimeToSource");
			}
		}
	}

	// --- add_mvvm_viewmodel -----------------------------------------------------------------------
	//   in:  { widgetBlueprintPath (alias: path/blueprintId), viewModelClass }
	//   out: { widgetBlueprintPath, viewModelName, viewModelId, viewModelClass }
	void H_add_mvvm_viewmodel(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId"), TEXT("viewModelClass") },
			TEXT("widgetBlueprintPath (aliases: path, blueprintId) - a Widget Blueprint; viewModelClass ")
			TEXT("- the class to add as a viewmodel"),
			{}))
		{
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		UClass* ViewModelClass = ResolveClassStrictField(In, { TEXT("viewModelClass") }, nullptr, Out);
		if (!ViewModelClass) { return; }

		UMVVMEditorSubsystem* Subsystem = GetMVVMSubsystem();
		if (!Subsystem)
		{
			Fail(Out, TEXT("UMVVMEditorSubsystem is not available"));
			return;
		}

		Subsystem->RequestView(WBP);
		UMVVMBlueprintView* View = Subsystem->GetView(WBP);
		// AddViewModel's return type changed between engines - 5.3.2 returns the assigned FName, 5.7
		// returns the FGuid directly instead (confirmed by a real C2440 on the probe build, not
		// assumed). Both engines still expose FindViewModel by either key, so resolve Context from
		// whichever one AddViewModel actually handed back, then read name/id uniformly from Context.
		const FMVVMBlueprintViewModelContext* Context = nullptr;
		// BOUNDARY IS 5.6, NOT 5.7 - corrected 2026-08-30 by reading both installed engines rather
		// than assuming the change arrived with the other 5.7 MVVM work. UE_5.6's
		// MVVMEditorSubsystem.h already declares `FGuid AddViewModel(...)` (:45) and
		// `SetDestinationPathForBinding(..., bool bAllowEventConversion)` (:89), identical to 5.7's
		// (:47, :91). Guarded at >= 7 these took the #else branch on 5.6, which assigns the return
		// to an FName and calls a 3-arg overload that does not exist there - a compile error on that
		// engine, not a behaviour difference. Written with MIF_ENGINE_AT_LEAST rather than by hand:
		// the raw `MAJOR >= 5 && MINOR >= 7` form is also wrong on any future 6.0, where major
		// passes and minor does not.
#if MIF_ENGINE_AT_LEAST(5, 6)
		const FGuid NewViewModelId = Subsystem->AddViewModel(WBP, ViewModelClass);
		if (!NewViewModelId.IsValid())
		{
			Fail(Out, FString::Printf(
				TEXT("AddViewModel returned no id for class '%s' - the class may not be a valid ")
				TEXT("viewmodel type. NOTHING was added."), *ViewModelClass->GetName()));
			return;
		}
		Context = View ? View->FindViewModel(NewViewModelId) : nullptr;
#else
		const FName NewViewModelName = Subsystem->AddViewModel(WBP, ViewModelClass);
		if (NewViewModelName.IsNone())
		{
			Fail(Out, FString::Printf(
				TEXT("AddViewModel returned no name for class '%s' - the class may not be a valid ")
				TEXT("viewmodel type. NOTHING was added."), *ViewModelClass->GetName()));
			return;
		}
		Context = View ? View->FindViewModel(NewViewModelName) : nullptr;
#endif
		if (!Context)
		{
			Fail(Out, TEXT("AddViewModel reported success but the view does not contain it on read-back."));
			return;
		}

		Out->SetStringField(TEXT("widgetBlueprintPath"), WBP->GetPathName());
		Out->SetStringField(TEXT("viewModelName"), Context->GetViewModelName().ToString());
		Out->SetStringField(TEXT("viewModelId"), Context->GetViewModelId().ToString(EGuidFormats::DigitsWithHyphens));
		Out->SetStringField(TEXT("viewModelClass"), ViewModelClass->GetPathName());
		UE_LOG(LogMifBridge, Log, TEXT("add_mvvm_viewmodel: %s gets viewmodel '%s' (%s)"),
			*WBP->GetName(), *Context->GetViewModelName().ToString(), *ViewModelClass->GetName());
	}

	// --- add_mvvm_binding -------------------------------------------------------------------------
	//   in:  { widgetBlueprintPath, sourceViewModelName, sourcePropertyName, destinationWidgetName,
	//          destinationPropertyName, bindingMode? }
	//   out: { widgetBlueprintPath, bindingId, bindingMode }
	void H_add_mvvm_binding(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId"),
			  TEXT("sourceViewModelName"), TEXT("sourcePropertyName"),
			  TEXT("destinationWidgetName"), TEXT("destinationPropertyName"), TEXT("bindingMode") },
			TEXT("widgetBlueprintPath (aliases: path, blueprintId); sourceViewModelName + ")
			TEXT("sourcePropertyName - a property already added via add_mvvm_viewmodel; ")
			TEXT("destinationWidgetName + destinationPropertyName - a named widget in the tree and a ")
			TEXT("property on it; bindingMode (optional: oneWayToDestination default, ")
			TEXT("oneTimeToDestination, twoWay, oneWayToSource)"),
			{}))
		{
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString SourceViewModelName = JStr(In, TEXT("sourceViewModelName"));
		const FString SourcePropertyName = JStr(In, TEXT("sourcePropertyName"));
		const FString DestWidgetName = JStr(In, TEXT("destinationWidgetName"));
		const FString DestPropertyName = JStr(In, TEXT("destinationPropertyName"));
		if (SourceViewModelName.IsEmpty() || SourcePropertyName.IsEmpty()
			|| DestWidgetName.IsEmpty() || DestPropertyName.IsEmpty())
		{
			Fail(Out, TEXT("sourceViewModelName, sourcePropertyName, destinationWidgetName and ")
						  TEXT("destinationPropertyName are all required. NOTHING was added."));
			return;
		}

		EMVVMBindingMode Mode;
		FString ModeError;
		if (!ParseBindingMode(JStr(In, TEXT("bindingMode")), Mode, ModeError))
		{
			Fail(Out, ModeError + TEXT(" NOTHING was added."));
			return;
		}

		UMVVMEditorSubsystem* Subsystem = GetMVVMSubsystem();
		if (!Subsystem)
		{
			Fail(Out, TEXT("UMVVMEditorSubsystem is not available"));
			return;
		}

		UMVVMBlueprintView* View = Subsystem->RequestView(WBP);
		if (!View)
		{
			Fail(Out, TEXT("RequestView returned null. NOTHING was added."));
			return;
		}

		const FMVVMBlueprintViewModelContext* VMContext = View->FindViewModel(FName(*SourceViewModelName));
		if (!VMContext)
		{
			Fail(Out, FString::Printf(
				TEXT("no viewmodel named '%s' on this Widget Blueprint - call add_mvvm_viewmodel first. ")
				TEXT("NOTHING was added."), *SourceViewModelName));
			return;
		}
		UClass* ViewModelClass = VMContext->GetViewModelClass();
		FProperty* SourceProperty = ViewModelClass ? ViewModelClass->FindPropertyByName(FName(*SourcePropertyName)) : nullptr;
		if (!SourceProperty)
		{
			Fail(Out, FString::Printf(
				TEXT("viewmodel '%s' (class '%s') has no property named '%s'. NOTHING was added."),
				*SourceViewModelName, ViewModelClass ? *ViewModelClass->GetName() : TEXT("?"), *SourcePropertyName));
			return;
		}

		UWidget* TargetWidget = WBP->WidgetTree ? WBP->WidgetTree->FindWidget(FName(*DestWidgetName)) : nullptr;
		if (!TargetWidget)
		{
			Fail(Out, FString::Printf(
				TEXT("no widget named '%s' in this Widget Blueprint's tree. NOTHING was added."), *DestWidgetName));
			return;
		}
		FProperty* DestProperty = TargetWidget->GetClass()->FindPropertyByName(FName(*DestPropertyName));
		if (!DestProperty)
		{
			Fail(Out, FString::Printf(
				TEXT("widget '%s' (class '%s') has no property named '%s'. NOTHING was added."),
				*DestWidgetName, *TargetWidget->GetClass()->GetName(), *DestPropertyName));
			return;
		}

		FMVVMBlueprintPropertyPath SourcePath;
		SourcePath.SetViewModelId(VMContext->GetViewModelId());
		SourcePath.SetPropertyPath(WBP, UE::MVVM::FMVVMConstFieldVariant(SourceProperty));

		FMVVMBlueprintPropertyPath DestPath;
		DestPath.SetWidgetName(FName(*DestWidgetName));
		DestPath.SetPropertyPath(WBP, UE::MVVM::FMVVMConstFieldVariant(DestProperty));

		FMVVMBlueprintViewBinding& Binding = Subsystem->AddBinding(WBP);
		Subsystem->SetSourcePathForBinding(WBP, Binding, SourcePath);
		// 5.7 grew a mandatory 4th parameter (bAllowEventConversion, no default) - not present at all
		// on 5.3.2. False matches this endpoint's scope: a plain property-to-property binding, not the
		// newer event-conversion path 5.7's own MVVM Events/Conditions additions introduced.
#if MIF_ENGINE_AT_LEAST(5, 6)
		Subsystem->SetDestinationPathForBinding(WBP, Binding, DestPath, /*bAllowEventConversion*/ false);
#else
		Subsystem->SetDestinationPathForBinding(WBP, Binding, DestPath);
#endif
		Subsystem->SetBindingTypeForBinding(WBP, Binding, Mode);

		Out->SetStringField(TEXT("widgetBlueprintPath"), WBP->GetPathName());
		Out->SetStringField(TEXT("bindingId"), Binding.BindingId.ToString(EGuidFormats::DigitsWithHyphens));
		Out->SetStringField(TEXT("bindingMode"), BindingModeToString(Mode));
		Out->SetStringField(TEXT("sourceViewModelName"), SourceViewModelName);
		Out->SetStringField(TEXT("sourcePropertyName"), SourcePropertyName);
		Out->SetStringField(TEXT("destinationWidgetName"), DestWidgetName);
		Out->SetStringField(TEXT("destinationPropertyName"), DestPropertyName);
		UE_LOG(LogMifBridge, Log, TEXT("add_mvvm_binding: %s: %s.%s -> %s.%s (%s)"), *WBP->GetName(),
			*SourceViewModelName, *SourcePropertyName, *DestWidgetName, *DestPropertyName, BindingModeToString(Mode));
	}

	// --- describe_mvvm_view -----------------------------------------------------------------------
	//   in:  { widgetBlueprintPath }
	//   out: { widgetBlueprintPath, viewModels: [...], bindings: [...] }
	// READ-ONLY: uses GetView, never RequestView - a pure read must not create the MVVM extension on a
	// Widget Blueprint that never had one.
	void H_describe_mvvm_view(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId") },
			TEXT("widgetBlueprintPath (aliases: path, blueprintId) - a Widget Blueprint"),
			{}))
		{
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		UMVVMEditorSubsystem* Subsystem = GetMVVMSubsystem();
		if (!Subsystem)
		{
			Fail(Out, TEXT("UMVVMEditorSubsystem is not available"));
			return;
		}

		UMVVMBlueprintView* View = Subsystem->GetView(WBP);
		Out->SetStringField(TEXT("widgetBlueprintPath"), WBP->GetPathName());
		Out->SetBoolField(TEXT("hasView"), View != nullptr);
		if (!View)
		{
			Out->SetArrayField(TEXT("viewModels"), TArray<TSharedPtr<FJsonValue>>());
			Out->SetArrayField(TEXT("bindings"), TArray<TSharedPtr<FJsonValue>>());
			return;
		}

		TArray<TSharedPtr<FJsonValue>> ViewModelsJson;
		for (const FMVVMBlueprintViewModelContext& VM : View->GetViewModels())
		{
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("name"), VM.GetViewModelName().ToString());
			Row->SetStringField(TEXT("id"), VM.GetViewModelId().ToString(EGuidFormats::DigitsWithHyphens));
			Row->SetStringField(TEXT("class"), VM.GetViewModelClass() ? VM.GetViewModelClass()->GetPathName() : FString());
			ViewModelsJson.Add(MakeShared<FJsonValueObject>(Row));
		}
		Out->SetArrayField(TEXT("viewModels"), ViewModelsJson);

		TArray<TSharedPtr<FJsonValue>> BindingsJson;
		for (const FMVVMBlueprintViewBinding& Binding : View->GetBindings())
		{
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("bindingId"), Binding.BindingId.ToString(EGuidFormats::DigitsWithHyphens));
			Row->SetStringField(TEXT("bindingMode"), BindingModeToString(Binding.BindingType));
			Row->SetBoolField(TEXT("enabled"), Binding.bEnabled);
			Row->SetBoolField(TEXT("compile"), Binding.bCompile);
			Row->SetBoolField(TEXT("sourceIsFromViewModel"), Binding.SourcePath.IsFromViewModel());
			Row->SetBoolField(TEXT("destinationIsFromWidget"), Binding.DestinationPath.IsFromWidget());
			TArray<TSharedPtr<FJsonValue>> SourceFields, DestFields;
			for (const FName& N : Binding.SourcePath.GetFieldNames(WBP->GeneratedClass))
			{
				SourceFields.Add(MakeShared<FJsonValueString>(N.ToString()));
			}
			for (const FName& N : Binding.DestinationPath.GetFieldNames(WBP->GeneratedClass))
			{
				DestFields.Add(MakeShared<FJsonValueString>(N.ToString()));
			}
			Row->SetArrayField(TEXT("sourceFieldPath"), SourceFields);
			Row->SetArrayField(TEXT("destinationFieldPath"), DestFields);
			BindingsJson.Add(MakeShared<FJsonValueObject>(Row));
		}
		Out->SetArrayField(TEXT("bindings"), BindingsJson);
	}

	// --- remove_mvvm_viewmodel --------------------------------------------------------------------
	//   in:  { widgetBlueprintPath, viewModelName }
	//   out: { widgetBlueprintPath, viewModelName, removed: true }
	// Reopened 2026-08-28 - a real, bounded scope cut from the original MVVM batch the same night
	// ("the subsystem's own RemoveViewModel/RemoveBinding exist and would be simple to wire on top of
	// this same file"), not new territory. UMVVMEditorSubsystem::RemoveViewModel (engine source,
	// checked before writing this, not assumed) silently NO-OPS on an unknown name or on a viewmodel
	// whose own bCanRemove is false - it does not report failure at all. So this handler checks both
	// BEFORE calling it, and reads the view back afterward rather than trusting a void return.
	void H_remove_mvvm_viewmodel(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId"), TEXT("viewModelName") },
			TEXT("widgetBlueprintPath (aliases: path, blueprintId); viewModelName - from ")
			TEXT("add_mvvm_viewmodel or describe_mvvm_view"),
			{}))
		{
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString ViewModelName = JStr(In, TEXT("viewModelName"));
		if (ViewModelName.IsEmpty())
		{
			Fail(Out, TEXT("viewModelName is required. NOTHING was removed."));
			return;
		}

		UMVVMEditorSubsystem* Subsystem = GetMVVMSubsystem();
		if (!Subsystem)
		{
			Fail(Out, TEXT("UMVVMEditorSubsystem is not available"));
			return;
		}

		// GetView, not RequestView - a removal must not create the MVVM extension on a Blueprint that
		// never had one, same reasoning describe_mvvm_view already uses.
		UMVVMBlueprintView* View = Subsystem->GetView(WBP);
		if (!View)
		{
			Fail(Out, TEXT("this Widget Blueprint has no MVVM view at all - nothing to remove from."));
			return;
		}

		const FMVVMBlueprintViewModelContext* Context = View->FindViewModel(FName(*ViewModelName));
		if (!Context)
		{
			Fail(Out, FString::Printf(
				TEXT("no viewmodel named '%s' on this Widget Blueprint. NOTHING was removed."), *ViewModelName));
			return;
		}
		if (!Context->bCanRemove)
		{
			Fail(Out, FString::Printf(
				TEXT("viewmodel '%s' is marked non-removable (bCanRemove=false) by the engine itself. ")
				TEXT("NOTHING was removed."), *ViewModelName));
			return;
		}

		Subsystem->RemoveViewModel(WBP, FName(*ViewModelName));

		// void return, and the engine source confirms it silently no-ops rather than reporting
		// failure - read the view back rather than trust the call happened.
		if (View->FindViewModel(FName(*ViewModelName)) != nullptr)
		{
			Fail(Out, FString::Printf(
				TEXT("RemoveViewModel was called but '%s' is still present on read-back."), *ViewModelName));
			return;
		}

		Out->SetStringField(TEXT("widgetBlueprintPath"), WBP->GetPathName());
		Out->SetStringField(TEXT("viewModelName"), ViewModelName);
		Out->SetBoolField(TEXT("removed"), true);
		UE_LOG(LogMifBridge, Log, TEXT("remove_mvvm_viewmodel: %s removes viewmodel '%s'"),
			*WBP->GetName(), *ViewModelName);
	}

	// --- remove_mvvm_binding ----------------------------------------------------------------------
	//   in:  { widgetBlueprintPath, bindingId }
	//   out: { widgetBlueprintPath, bindingId, removed: true }
	// UMVVMBlueprintView::RemoveBinding (engine source, checked before writing this) matches by
	// POINTER IDENTITY against its own internal Bindings array, not by value or by BindingId - so the
	// FMVVMBlueprintViewBinding* passed to it must be a reference into that SAME array (the
	// non-const View->GetBindings() below), never a copy pulled out of it. Getting this wrong would
	// not crash - RemoveBindingAt bounds-checks a not-found index and silently no-ops - but it would
	// silently remove nothing while still returning ok:true, exactly the class of bug this whole
	// project's read-back discipline exists to catch.
	void H_remove_mvvm_binding(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId"), TEXT("bindingId") },
			TEXT("widgetBlueprintPath (aliases: path, blueprintId); bindingId - from add_mvvm_binding ")
			TEXT("or describe_mvvm_view"),
			{}))
		{
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString BindingIdStr = JStr(In, TEXT("bindingId"));
		FGuid WantId;
		if (BindingIdStr.IsEmpty() || !FGuid::Parse(BindingIdStr, WantId))
		{
			Fail(Out, FString::Printf(
				TEXT("bindingId '%s' is not a valid GUID. NOTHING was removed."), *BindingIdStr));
			return;
		}

		UMVVMEditorSubsystem* Subsystem = GetMVVMSubsystem();
		if (!Subsystem)
		{
			Fail(Out, TEXT("UMVVMEditorSubsystem is not available"));
			return;
		}

		UMVVMBlueprintView* View = Subsystem->GetView(WBP);
		if (!View)
		{
			Fail(Out, TEXT("this Widget Blueprint has no MVVM view at all - nothing to remove from."));
			return;
		}

		FMVVMBlueprintViewBinding* Found = nullptr;
		for (FMVVMBlueprintViewBinding& Binding : View->GetBindings())
		{
			if (Binding.BindingId == WantId) { Found = &Binding; break; }
		}
		if (!Found)
		{
			Fail(Out, FString::Printf(
				TEXT("no binding with id '%s' on this Widget Blueprint. NOTHING was removed."), *BindingIdStr));
			return;
		}

		Subsystem->RemoveBinding(WBP, *Found);

		bool bStillPresent = false;
		for (const FMVVMBlueprintViewBinding& Binding : View->GetBindings())
		{
			if (Binding.BindingId == WantId) { bStillPresent = true; break; }
		}
		if (bStillPresent)
		{
			Fail(Out, FString::Printf(
				TEXT("RemoveBinding was called but binding '%s' is still present on read-back."), *BindingIdStr));
			return;
		}

		Out->SetStringField(TEXT("widgetBlueprintPath"), WBP->GetPathName());
		Out->SetStringField(TEXT("bindingId"), BindingIdStr);
		Out->SetBoolField(TEXT("removed"), true);
		UE_LOG(LogMifBridge, Log, TEXT("remove_mvvm_binding: %s removes binding '%s'"),
			*WBP->GetName(), *BindingIdStr);
	}
#endif
}
