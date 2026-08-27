// MifBridge — ISOLATED WIDGET PREVIEW: render one Widget Blueprint class to a PNG without touching a
// PIE session or the open level.
//
// WHY THIS EXISTS. Phase A of the UMG enhancement proposal (infectedcoolpat/QOLCrafting_P,
// tools/FEATURE_PARITY_SPEC.md). Phase 1-2 (list_live_widgets/describe_live_widget, this same file's
// sibling MifBridgeLiveWidgets.cpp) answers "what is a RUNNING game showing" - this answers the
// smaller, cheaper question "what does THIS ONE Widget Blueprint look like", without needing PIE at
// all. Precedent for the creation half: UMGEditor's own SWidgetPreview.cpp (the Designer's live
// preview pane) - CreateWidget + SetDesignerFlags(Previewing) + TakeWidget(), the exact three calls
// used here. Precedent for the render half: FunctionalUIScreenshotTest.cpp (the engine's own
// automated UI screenshot test) - FWidgetRenderer::DrawWidget into a UTextureRenderTarget2D, then
// BeginCleanup (never a bare `delete`; FWidgetRenderer is FDeferredCleanupInterface and deletes
// itself once the render thread is done with it).
//
// SCOPE, deliberately smaller than the proposal's own illustrative endpoint family
// (widget_preview_start/status/capture/inspect/stop). This bridge's existing capture_viewport and
// capture_camera are both ONE synchronous call, not a stateful session - a widget render is just as
// fast, so the same shape applies: one call in, one PNG out. No start/stop lifecycle to leak.
//
// EXPLICIT DPI ONLY for this first version. The proposal's dpiMode:project would apply
// UUserInterfaceSettings::GetDPIScaleBasedOnSize - reported here as a FACT (dpiScaleAtThisSize) so a
// caller can pass it back as dpiScale next time, but not applied automatically, because doing so
// silently would make two callers asking for the same width/height get different-looking renders
// depending on project settings neither one typed. Explicit beats implicit for a first version.
//
// NO ASSET IS TOUCHED. The widget is CreateWidget'd transient, never AddToViewport'd (which is what
// would register it with a real game layer), and dropped for garbage collection once this call
// returns. dirtyPackagesDelta is reported and should always be 0 - if it is not, that is a real
// finding about this endpoint, not an expected cost of using it.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Blueprint/UserWidget.h"
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
		// A cheap COUNT, not the full list list_dirty_packages builds - this only needs to answer
		// "did anything change", not name what.
		int32 CountDirtyPackages()
		{
			int32 Count = 0;
			for (TObjectIterator<UPackage> It; It; ++It)
			{
				if (It->IsDirty()) { ++Count; }
			}
			return Count;
		}
	}

	// --- preview_widget --------------------------------------------------------------------------
	//   in:  { widgetClass, width? (default 512), height? (default 512), dpiScale? (default 1.0),
	//          background? (transparent|black|white, default transparent), name? }
	//   out: { path, exists, wroteFile, width, height, dpiScaleApplied, dpiScaleAtThisSize (fact,
	//          not applied), widgetClass, dirtyPackagesDelta }
	void H_preview_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("widgetClass"), TEXT("width"), TEXT("height"), TEXT("dpiScale"),
			  TEXT("background"), TEXT("name") },
			TEXT("widgetClass (a UserWidget-derived class, e.g. /Game/UI/WBP_Foo.WBP_Foo_C), ")
			TEXT("width/height? (64-4096, default 512), dpiScale? (default 1.0 - see ")
			TEXT("dpiScaleAtThisSize in the response for the project's own curve at this size, not ")
			TEXT("applied automatically), background? (transparent|black|white, default transparent), name?"),
			{ { TEXT("dpiMode"), TEXT("not implemented - pass dpiScale explicitly; the response's dpiScaleAtThisSize reports what dpiMode:project would have used") } }))
		{
			return;
		}

		UClass* WidgetClass = ResolveClassStrictField(In, { TEXT("widgetClass") }, nullptr, Out);
		if (!WidgetClass) { return; }
		if (!WidgetClass->IsChildOf(UUserWidget::StaticClass()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a UserWidget-derived class."), *WidgetClass->GetPathName()));
			return;
		}
		if (WidgetClass->HasAnyClassFlags(CLASS_Abstract))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is ABSTRACT and cannot be instantiated."), *WidgetClass->GetPathName()));
			return;
		}

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

		const int32 DirtyBefore = CountDirtyPackages();

		UUserWidget* Widget = CreateWidget<UUserWidget>(World, WidgetClass);
		if (!Widget)
		{
			Fail(Out, TEXT("CreateWidget returned null."));
			return;
		}
		// Same flag SWidgetPreview.cpp sets for the Designer's own live preview pane - marks the
		// widget as a non-interactive preview instance rather than a real gameplay one.
		Widget->SetDesignerFlags(EWidgetDesignFlags::Previewing);
		const TSharedRef<SWidget> SlateWidget = Widget->TakeWidget();

		// NOT FWidgetRenderer::CreateTargetFor - it sizes the target using
		// FSlateApplication::GetRenderer()->GetSlateRecommendedColorFormat(), which on this machine is
		// an HDR/float format. ExportRenderTarget then writes actual OpenEXR data with a '.png'
		// filename rather than refusing or converting - caught live: `file` on the result reported
		// "OpenEXR image data", not PNG, even though exists/wroteFile both came back true. Built
		// explicitly instead, same RTF_RGBA8 construction capture_camera already uses and is proven to
		// export real PNGs with.
		UTextureRenderTarget2D* RT = NewObject<UTextureRenderTarget2D>(GetTransientPackage());
		if (!RT)
		{
			Widget->MarkAsGarbage();
			Fail(Out, TEXT("failed to create the render target."));
			return;
		}
		RT->RenderTargetFormat = RTF_RGBA8;
		RT->ClearColor = ClearColor;
		RT->bAutoGenerateMips = false;
		RT->InitAutoFormat(Width, Height);
		RT->UpdateResourceImmediate(true);

		// bUseGammaCorrection true, bInClearTarget true - same construction FunctionalUIScreenshotTest
		// uses. BeginCleanup, never `delete`: FWidgetRenderer is FDeferredCleanupInterface and frees
		// itself once the render thread has actually finished with it - deleting it directly here
		// would race the draw command this same call just enqueued.
		FWidgetRenderer* Renderer = new FWidgetRenderer(/*bUseGammaCorrection*/ true, /*bInClearTarget*/ true);
		Renderer->DrawWidget(RT, SlateWidget, static_cast<float>(DpiScale),
			FVector2D(Width, Height), /*DeltaTime*/ 0.f);
		BeginCleanup(Renderer);

		FString Name = JStr(In, TEXT("name"), TEXT("MifWidgetPreview"));
		Name = FPaths::MakeValidFileName(Name);
		const FString Dir = FPaths::ProjectSavedDir() / TEXT("MifBridge");
		IPlatformFile& PF = FPlatformFileManager::Get().GetPlatformFile();
		PF.CreateDirectoryTree(*Dir);
		const FString FullPath = FPaths::ConvertRelativePathToFull(Dir / (Name + TEXT(".png")));

		// Same stale-file trap capture_camera guards against: ExportRenderTarget returns void, so a
		// bare FileExists() afterward answers "yes" for a name reused from an earlier call that wrote
		// nothing new this time.
		const bool      bExistedBefore = PF.FileExists(*FullPath);
		const FDateTime BeforeStamp    = bExistedBefore ? PF.GetTimeStamp(*FullPath) : FDateTime::MinValue();
		const int64      BeforeSize    = bExistedBefore ? PF.FileSize(*FullPath) : -1;

		UKismetRenderingLibrary::ExportRenderTarget(World, RT, Dir, Name + TEXT(".png"));

		const bool bExists = PF.FileExists(*FullPath);
		const bool bFresh  = bExists && (!bExistedBefore
			|| PF.GetTimeStamp(*FullPath) != BeforeStamp
			|| PF.FileSize(*FullPath) != BeforeSize);

		// Nothing keeps this widget alive once the function returns - no AddToViewport, no owner
		// holding a strong reference beyond the local pointer. MarkAsGarbage makes that explicit
		// rather than relying on the next GC pass to notice.
		Widget->MarkAsGarbage();

		const int32 DirtyAfter = CountDirtyPackages();

		Out->SetStringField(TEXT("path"), FullPath);
		Out->SetBoolField(TEXT("exists"), bExists);
		Out->SetBoolField(TEXT("wroteFile"), bFresh);
		Out->SetNumberField(TEXT("width"), Width);
		Out->SetNumberField(TEXT("height"), Height);
		Out->SetNumberField(TEXT("dpiScaleApplied"), DpiScale);
		Out->SetNumberField(TEXT("dpiScaleAtThisSize"), DpiAtThisSize);
		Out->SetStringField(TEXT("widgetClass"), WidgetClass->GetPathName());
		Out->SetNumberField(TEXT("dirtyPackagesDelta"), DirtyAfter - DirtyBefore);
		Out->SetStringField(TEXT("fidelity"), TEXT("isolatedOffscreenPreview"));
		Out->SetStringField(TEXT("note"),
			TEXT("this is an ISOLATED preview - no game world, no PIE, no parent widget composition. ")
			TEXT("Runtime-created children, dynamic bindings driven by BeginPlay, and anything another ")
			TEXT("widget injects into this one at runtime will NOT appear here. For that, drive PIE and ")
			TEXT("use list_live_widgets/describe_live_widget instead."));

		if (!bExists)
		{
			Fail(Out, FString::Printf(
				TEXT("render target export wrote no file at %s."), *FullPath));
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
				TEXT("dirtyPackagesDelta is %d, not 0 - this preview may have dirtied something. ")
				TEXT("That should not happen; treat it as a bug report, not an expected cost."),
				DirtyAfter - DirtyBefore));
		}

		UE_LOG(LogMifBridge, Log, TEXT("preview_widget: %s (%dx%d, dpi %.2f) -> %s"),
			*WidgetClass->GetName(), Width, Height, DpiScale, *FullPath);
	}
}
