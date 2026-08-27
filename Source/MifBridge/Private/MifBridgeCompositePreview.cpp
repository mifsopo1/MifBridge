// MifBridge — DECLARATIVE COMPOSITE WIDGET PREVIEW: assemble a root widget plus N children into
// named containers, transiently, and render the RESULT - without touching any source WBP asset.
//
// WHY THIS EXISTS. Phase B of the UMG enhancement proposal (infectedcoolpat/QOLCrafting_P,
// tools/FEATURE_PARITY_SPEC.md). Phase A (preview_widget, this file's sibling
// MifBridgeWidgetPreview.cpp) renders ONE widget class alone - useless for QOLCrafting_P's actual
// architecture, where the real screen is a vanilla parent widget with WBP_RecyclerStorage injected
// into a named container (`containerHolder`) at runtime. This endpoint reproduces that COMPOSITION
// step declaratively: create the root, create each child, insert each child into a NAMED
// panel/slot on the root by variable name, then render and describe the assembled result.
//
// STILL NOT PROOF the real interaction path produced this composition - that is Phase C's job
// (unstarted, and a much larger one: driving actual gameplay input). This endpoint proves the
// COMPOSITION MECHANISM works and lets a caller iterate on layout without packaging or PIE; it does
// not prove a machine's FocusSetup/interaction code would actually assemble it this way.
//
// REUSES the render pipeline from MifBridgeWidgetPreview.cpp (RTF_RGBA8 render target - built
// EXPLICITLY, not via FWidgetRenderer::CreateTargetFor, which was proven live to write OpenEXR data
// into a file named .png on this machine; see that file's header for the full story) and the
// geometry-tree shape from MifBridgeLiveWidgets.cpp's describe_live_widget, both re-implemented
// locally per this codebase's convention of small per-file helpers over cross-file promotion.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Blueprint/UserWidget.h"
#include "Blueprint/WidgetTree.h"
#include "Components/PanelWidget.h"
#include "Components/PanelSlot.h"
#include "Components/CanvasPanelSlot.h"
#include "Slate/WidgetRenderer.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/UserInterfaceSettings.h"
#include "Kismet/KismetRenderingLibrary.h"
#include "HAL/PlatformFileManager.h"
#include "GenericPlatform/GenericPlatformFile.h"
#include "Misc/Paths.h"
#include "UObject/Package.h"
#include "UObject/UObjectIterator.h"

namespace MifBridge
{
	namespace
	{
		int32 CountDirtyPackagesCP()
		{
			int32 Count = 0;
			for (TObjectIterator<UPackage> It; It; ++It)
			{
				if (It->IsDirty()) { ++Count; }
			}
			return Count;
		}

		TSharedRef<FJsonObject> JsonVec2CP(const FVector2D& V)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetNumberField(TEXT("x"), V.X);
			J->SetNumberField(TEXT("y"), V.Y);
			return J;
		}

		const TCHAR* VisibilityNameCP(ESlateVisibility V)
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

		// Same shape as MifBridgeLiveWidgets.cpp's DescribeWidgetTree, minus the FindObject-by-path
		// use case (these widgets are freshly minted for this one call, never referenced by path
		// afterward) - kept as its own copy rather than a shared header promotion, same reasoning as
		// ValidateNewMetaHumanPath in MifBridgeMetaHuman.cpp.
		TSharedRef<FJsonObject> DescribeComposedTree(UWidget* W, int32 RemainingDepth)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), W->GetName());
			J->SetStringField(TEXT("class"), W->GetClass()->GetPathName());
			J->SetStringField(TEXT("visibility"), VisibilityNameCP(W->GetVisibility()));
			J->SetBoolField(TEXT("isVisible"), W->IsVisible());

			const FGeometry& Geo = W->GetCachedGeometry();
			const FVector2D AbsPos(Geo.GetAbsolutePosition());
			const FVector2D AbsSize(Geo.GetAbsoluteSize());
			J->SetObjectField(TEXT("absolutePosition"), JsonVec2CP(AbsPos));
			J->SetObjectField(TEXT("absoluteSize"), JsonVec2CP(AbsSize));
			J->SetObjectField(TEXT("desiredSize"), JsonVec2CP(W->GetDesiredSize()));
			J->SetBoolField(TEXT("neverPainted"), AbsSize.IsNearlyZero() && AbsPos.IsNearlyZero());

			if (const UCanvasPanelSlot* CSlot = Cast<UCanvasPanelSlot>(W->Slot))
			{
				J->SetNumberField(TEXT("zOrder"), CSlot->GetZOrder());
			}
			if (W->Slot)
			{
				J->SetStringField(TEXT("slotClass"), W->Slot->GetClass()->GetName());
			}

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
						Children.Add(MakeShared<FJsonValueObject>(DescribeComposedTree(Child, RemainingDepth - 1)));
					}
				}
			}
			if (UUserWidget* AsUserWidget = Cast<UUserWidget>(W))
			{
				if (AsUserWidget->WidgetTree && AsUserWidget->WidgetTree->RootWidget)
				{
					J->SetObjectField(TEXT("userWidgetContent"),
						DescribeComposedTree(AsUserWidget->WidgetTree->RootWidget, RemainingDepth - 1));
				}
			}
			if (Children.Num() > 0)
			{
				J->SetArrayField(TEXT("children"), Children);
			}
			return J;
		}
	}

	// --- preview_composite_widget -----------------------------------------------------------------
	//   in:  { rootClass, children: [{class, insertInto, name?}], width?, height?, dpiScale?,
	//          background?, name? }
	//   out: { path, exists, wroteFile, width, height, dpiScaleApplied, dpiScaleAtThisSize,
	//          rootClass, inserted:[{class, insertInto, ok, error?}], tree, dirtyPackagesDelta }
	// insertInto addresses a container by its VARIABLE NAME on the ROOT widget only (GetWidgetFromName)
	// - not a nested child-of-an-inserted-child target. That is the deliberate v1 boundary: it covers
	// exactly the QOLCrafting_P shape (root's own named container gets a child) without chasing every
	// possible recipe depth on the first version.
	void H_preview_composite_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("rootClass"), TEXT("children"), TEXT("width"), TEXT("height"), TEXT("dpiScale"),
			  TEXT("background"), TEXT("name") },
			TEXT("rootClass (a UserWidget class), children[] (each: class, insertInto - a named ")
			TEXT("panel/slot variable on the ROOT, name? - a label for this response only), width/height? ")
			TEXT("(64-4096, default 512), dpiScale? (default 1.0), background? (transparent|black|white), name?"),
			{ { TEXT("recipe"), TEXT("the field is called children[], not recipe") } }))
		{
			return;
		}

		UClass* RootClass = ResolveClassStrictField(In, { TEXT("rootClass") }, nullptr, Out);
		if (!RootClass) { return; }
		if (!RootClass->IsChildOf(UUserWidget::StaticClass()) || RootClass->HasAnyClassFlags(CLASS_Abstract))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a concrete UserWidget-derived class."), *RootClass->GetPathName()));
			return;
		}

		const TArray<TSharedPtr<FJsonValue>>* ChildArray = nullptr;
		JArray(In, TEXT("children"), ChildArray);

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world available")); return; }

		const int32 Width  = FMath::Clamp(JInt(In, TEXT("width"), 512), 64, 4096);
		const int32 Height = FMath::Clamp(JInt(In, TEXT("height"), 512), 64, 4096);
		const double DpiScale = FMath::Clamp(JNum(In, TEXT("dpiScale"), 1.0), 0.1, 8.0);
		const float DpiAtThisSize = GetDefault<UUserInterfaceSettings>()
			? GetDefault<UUserInterfaceSettings>()->GetDPIScaleBasedOnSize(FIntPoint(Width, Height))
			: 1.0f;

		const FString BackgroundMode = JStr(In, TEXT("background"), TEXT("transparent")).ToLower();
		FLinearColor ClearColor;
		if (BackgroundMode == TEXT("transparent"))     { ClearColor = FLinearColor(0, 0, 0, 0); }
		else if (BackgroundMode == TEXT("black"))       { ClearColor = FLinearColor(0, 0, 0, 1); }
		else if (BackgroundMode == TEXT("white"))       { ClearColor = FLinearColor(1, 1, 1, 1); }
		else
		{
			Fail(Out, FString::Printf(
				TEXT("background '%s' is not one of transparent | black | white."), *BackgroundMode));
			return;
		}

		const int32 DirtyBefore = CountDirtyPackagesCP();

		UUserWidget* Root = CreateWidget<UUserWidget>(World, RootClass);
		if (!Root) { Fail(Out, TEXT("CreateWidget returned null for rootClass.")); return; }
		Root->SetDesignerFlags(EWidgetDesignFlags::Previewing);

		TArray<TSharedPtr<FJsonValue>> InsertResults;
		if (ChildArray)
		{
			for (const TSharedPtr<FJsonValue>& Entry : *ChildArray)
			{
				const TSharedPtr<FJsonObject>* ChildObjPtr = nullptr;
				TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
				if (!Entry.IsValid() || !Entry->TryGetObject(ChildObjPtr) || !ChildObjPtr)
				{
					Result->SetBoolField(TEXT("ok"), false);
					Result->SetStringField(TEXT("error"), TEXT("children[] entry is not an object"));
					InsertResults.Add(MakeShared<FJsonValueObject>(Result));
					continue;
				}
				const TSharedRef<FJsonObject> ChildObj = ChildObjPtr->ToSharedRef();
				const FString ChildClassName = JStr(ChildObj, TEXT("class"));
				const FString InsertInto = JStr(ChildObj, TEXT("insertInto"));
				const FString Label = JStr(ChildObj, TEXT("name"), ChildClassName);
				Result->SetStringField(TEXT("class"), ChildClassName);
				Result->SetStringField(TEXT("insertInto"), InsertInto);

				// ResolveClassStrict, not ResolveClassStrictField+Fail(Out,...): a bad class name here
				// must not discard every OTHER child's result, so the error is captured per-entry into
				// InsertResults instead of failing the whole call.
				FString ClassError;
				UClass* ChildClass = ResolveClassStrict(ChildClassName, nullptr, TEXT("children[].class"), ClassError);
				if (!ChildClass || !ChildClass->IsChildOf(UUserWidget::StaticClass()))
				{
					Result->SetBoolField(TEXT("ok"), false);
					Result->SetStringField(TEXT("error"),
						ChildClass ? TEXT("class is not a UserWidget") : ClassError);
					InsertResults.Add(MakeShared<FJsonValueObject>(Result));
					continue;
				}

				UWidget* Target = Root->GetWidgetFromName(FName(*InsertInto));
				UPanelWidget* TargetPanel = Cast<UPanelWidget>(Target);
				if (!TargetPanel)
				{
					Result->SetBoolField(TEXT("ok"), false);
					Result->SetStringField(TEXT("error"), Target
						? FString::Printf(TEXT("'%s' is a %s, not a panel/named-slot - cannot insert a child into it"),
							*InsertInto, *Target->GetClass()->GetName())
						: FString::Printf(TEXT("no widget named '%s' on the root - it must be a variable ")
							TEXT("(bIsVariable) on rootClass's design-time tree"), *InsertInto));
					InsertResults.Add(MakeShared<FJsonValueObject>(Result));
					continue;
				}

				UUserWidget* ChildWidget = CreateWidget<UUserWidget>(World, ChildClass);
				if (!ChildWidget)
				{
					Result->SetBoolField(TEXT("ok"), false);
					Result->SetStringField(TEXT("error"), TEXT("CreateWidget returned null for this child"));
					InsertResults.Add(MakeShared<FJsonValueObject>(Result));
					continue;
				}
				ChildWidget->SetDesignerFlags(EWidgetDesignFlags::Previewing);
				UPanelSlot* NewSlot = TargetPanel->AddChild(ChildWidget);
				Result->SetBoolField(TEXT("ok"), NewSlot != nullptr);
				if (!NewSlot)
				{
					Result->SetStringField(TEXT("error"),
						FString::Printf(TEXT("'%s' refused the child (AddChild returned null) - a NamedSlot ")
							TEXT("or single-child container already holding content is the usual cause"), *InsertInto));
					ChildWidget->MarkAsGarbage();
				}
				InsertResults.Add(MakeShared<FJsonValueObject>(Result));
			}
		}

		const TSharedRef<SWidget> SlateWidget = Root->TakeWidget();

		UTextureRenderTarget2D* RT = NewObject<UTextureRenderTarget2D>(GetTransientPackage());
		if (!RT)
		{
			Root->MarkAsGarbage();
			Fail(Out, TEXT("failed to create the render target."));
			return;
		}
		RT->RenderTargetFormat = RTF_RGBA8;
		RT->ClearColor = ClearColor;
		RT->bAutoGenerateMips = false;
		RT->InitAutoFormat(Width, Height);
		RT->UpdateResourceImmediate(true);

		FWidgetRenderer* Renderer = new FWidgetRenderer(/*bUseGammaCorrection*/ true, /*bInClearTarget*/ true);
		Renderer->DrawWidget(RT, SlateWidget, static_cast<float>(DpiScale),
			FVector2D(Width, Height), /*DeltaTime*/ 0.f);
		BeginCleanup(Renderer);

		// Geometry read AFTER DrawWidget, same as list_live_widgets/describe_live_widget rely on for a
		// PIE-ticked widget - DrawWidget performs the prepass/paint pass that populates
		// GetCachedGeometry(), so this is the first point the tree's positions/sizes are real.
		TSharedRef<FJsonObject> Tree = DescribeComposedTree(Root, 16);

		FString Name = JStr(In, TEXT("name"), TEXT("MifCompositePreview"));
		Name = FPaths::MakeValidFileName(Name);
		const FString Dir = FPaths::ProjectSavedDir() / TEXT("MifBridge");
		IPlatformFile& PF = FPlatformFileManager::Get().GetPlatformFile();
		PF.CreateDirectoryTree(*Dir);
		const FString FullPath = FPaths::ConvertRelativePathToFull(Dir / (Name + TEXT(".png")));

		const bool      bExistedBefore = PF.FileExists(*FullPath);
		const FDateTime BeforeStamp    = bExistedBefore ? PF.GetTimeStamp(*FullPath) : FDateTime::MinValue();
		const int64      BeforeSize    = bExistedBefore ? PF.FileSize(*FullPath) : -1;

		UKismetRenderingLibrary::ExportRenderTarget(World, RT, Dir, Name + TEXT(".png"));

		const bool bExists = PF.FileExists(*FullPath);
		const bool bFresh  = bExists && (!bExistedBefore
			|| PF.GetTimeStamp(*FullPath) != BeforeStamp
			|| PF.FileSize(*FullPath) != BeforeSize);

		// Root->MarkAsGarbage() is enough: every inserted child is now owned by the root's panel slot,
		// so nothing else needs an explicit MarkAsGarbage - only the ones that FAILED to insert (the
		// early-continue branches above) needed it done for them, and already got it.
		Root->MarkAsGarbage();

		const int32 DirtyAfter = CountDirtyPackagesCP();

		Out->SetStringField(TEXT("path"), FullPath);
		Out->SetBoolField(TEXT("exists"), bExists);
		Out->SetBoolField(TEXT("wroteFile"), bFresh);
		Out->SetNumberField(TEXT("width"), Width);
		Out->SetNumberField(TEXT("height"), Height);
		Out->SetNumberField(TEXT("dpiScaleApplied"), DpiScale);
		Out->SetNumberField(TEXT("dpiScaleAtThisSize"), DpiAtThisSize);
		Out->SetStringField(TEXT("rootClass"), RootClass->GetPathName());
		Out->SetArrayField(TEXT("inserted"), InsertResults);
		Out->SetObjectField(TEXT("tree"), Tree);
		Out->SetNumberField(TEXT("dirtyPackagesDelta"), DirtyAfter - DirtyBefore);
		Out->SetStringField(TEXT("fidelity"), TEXT("declarativeCompositePreview"));
		Out->SetStringField(TEXT("note"),
			TEXT("this composition was ASSEMBLED BY THIS CALL, not by the game's own interaction code - ")
			TEXT("it proves the recipe mechanism works, not that a machine's real FocusSetup/interaction ")
			TEXT("path produces the same result. For that, drive PIE and use list_live_widgets/")
			TEXT("describe_live_widget instead. insertInto only addresses a named container on the ROOT ")
			TEXT("(not on an already-inserted child) in this version."));

		if (!bExists)
		{
			Fail(Out, FString::Printf(TEXT("render target export wrote no file at %s."), *FullPath));
			return;
		}
		if (!bFresh)
		{
			AddWarning(Out, FString::Printf(
				TEXT("%s already existed and is unchanged - pass a distinct 'name' per render."), *FullPath));
		}
		if (DirtyAfter != DirtyBefore)
		{
			AddWarning(Out, FString::Printf(
				TEXT("dirtyPackagesDelta is %d, not 0 - treat as a bug report."), DirtyAfter - DirtyBefore));
		}

		UE_LOG(LogMifBridge, Log, TEXT("preview_composite_widget: %s + %d children -> %s"),
			*RootClass->GetName(), InsertResults.Num(), *FullPath);
	}
}
