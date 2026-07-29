// MifBridge — ASSET ICON RENDERING (thumbnails).
//
// The gap this closes: the bridge could author assets and capture the VIEWPORT (capture_camera,
// MifBridgeSpatial.cpp) but could not produce an ICON — a small, framed, per-asset image — and,
// crucially, could not write one back as a UTexture2D ASSET. Handing back pixels is not the
// deliverable: an empty icon stub is a UTexture2D that other assets already reference BY PATH, so a
// PNG on disk leaves every one of those stubs still empty. write_thumbnail_texture is the point of
// this file; the other three exist to make it verifiable, previewable and preflightable.
//
//   render_thumbnail        render an asset's icon and write a PNG under <ProjectSaved>/MifBridge —
//                           the "look at what you are about to bake" half. Mutates no asset.
//   write_thumbnail_texture render it and WRITE/REFILL a UTexture2D asset. THE endpoint.
//   set_asset_thumbnail     render it and cache it as the asset's own Content Browser icon.
//   thumbnail_capabilities  read-only preflight: can this editor render at all, and does THIS asset
//                           have a real renderer or only a generic class icon?
//
// ENGINE PATH, NOT A PLUGIN. Everything below goes through ThumbnailTools::RenderThumbnail
// (ObjectTools.h:709, UNREALED_API) — the same call the Content Browser and package save use. The
// project also mounts ThumbnailGenerator (Plugins_RamaThumb), and MifBridge deliberately does NOT
// use it: MifBridge must load in projects that do not have that plugin, exactly like the
// MifKismetReconstructor coupling. thumbnail_capabilities REPORTS whether its module is loaded, via
// a runtime FModuleManager name lookup that needs no dependency, no header and no link symbol — and
// says in the same breath that the bridge does not drive it. Do not turn that probe into a call.
//
// SYNCHRONOUS, ONE TICK, NO JOB SLOT — and that is a verified claim, not an assumption.
// ThumbnailTools::RenderThumbnail ends with Canvas.Flush_GameThread() followed by
// RenderTargetResource->ReadPixelsPtr(...) (ObjectTools.cpp:5164-5186), and ReadPixelsPtr blocks on
// the render thread, so the pixel buffer is FILLED before the call returns. Nothing here spans a
// frame, so none of it is modelled as a job slot (contrast the kr_* one-slot pattern in
// MifKismetReconstructor, which exists for work that genuinely cannot finish in one tick).
//
// THE ONE STALL, STATED RATHER THAN HIDDEN: flushTextures:true selects
// EThumbnailTextureFlushMode::AlwaysFlush, which runs FlushAsyncLoading +
// FAssetCompilingManager::FinishAllCompilation + UTexture::ForceUpdateTextureStreaming +
// IStreamingManager::StreamAllResources (ObjectTools.cpp:5076-5089). Those are BLOCKING and can take
// tens of seconds on a cold editor. FHttpServerModule's ticker runs on the game thread, so for that
// whole time the bridge reads nothing off the socket and the caller's HTTP read timeout may fire on
// a request that is in fact succeeding. It therefore defaults to FALSE, and every response carries
// elapsedMs so the cost is a number the caller can see rather than a surprise. Blurry-looking icons
// are the symptom that warrants paying it once.
//
// NO MODAL, EVER. Two independent guards. (1) GIsRunningUnattendedScript is forced true around the
// whole render: the engine only sets it around the Draw call itself (ObjectTools.cpp:5098), and the
// flush path above sits OUTSIDE that guard. (2) ThumbnailTools::GenerateThumbnailForObjectToSaveToDisk
// is never called even though it looks like the convenient wrapper — its material branch runs
// FScopedSlowTask::MakeDialog (ObjectTools.cpp:5290-5292), i.e. it puts a window up, and an
// unattended agent cannot dismiss one. set_asset_thumbnail reimplements its two useful lines
// (RenderThumbnail + CacheThumbnail) instead.
//
// Transaction buckets (registered in MifBridgeCommon.cpp — see this file's report, not this file):
//   render_thumbnail, thumbnail_capabilities — READ-ONLY. render_thumbnail writes a PNG to
//     <ProjectSaved> and touches no UObject state that survives the call, which is precisely
//     capture_camera's bucket and precisely its reason (MifBridgeCommon.cpp:410-411).
//   write_thumbnail_texture, set_asset_thumbnail — SELF-MANAGED. New package / UObject creation with
//     explicit AssetCreated + MarkPackageDirty, plus a texture build and a package save: the
//     create_material precedent verbatim (MifBridgeCommon.cpp:514-526), and saving is not undoable
//     (the save_dirty_packages precedent, :510-512). Consequence, stated because it is a real
//     ergonomic cost: SelfManaged implies IsCompileHeavyEndpoint, so `batch` refuses both. Filling N
//     icon stubs is N HTTP calls. That is the honest shape — each call is a fully verified unit, and
//     a batch of texture builds inside one open transaction is the hazard the bucket exists to fence.
//
// UNITY-BUILD NOTE: every file-local helper here is prefixed Thumb*. A free function name duplicated
// across two .cpp in this module is C2084 even with internal linkage, and blob membership moves on
// its own as file sizes change (MifBridgeHandlers.h:204-210).
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Editor/UnrealEdEngine.h"                        // UUnrealEdEngine::GetThumbnailManager (UNREALED_API)
#include "EditorFramework/ThumbnailInfo.h"                // UThumbnailInfo (the property's declared type)
#include "Engine/Engine.h"                                // GEngine->DisplayGamma
#include "Engine/Texture.h"                               // FTextureSource
#include "Engine/Texture2D.h"
#include "Engine/TextureDefines.h"                        // TextureCompressionSettings / TextureGroup / TMGS_*
#include "Engine/TextureRenderTarget2D.h"
#include "HAL/FileManager.h"                              // IFileManager::FileExists — save verification
#include "HAL/PlatformFileManager.h"                      // CreateDirectoryTree for the PNG folder
#include "ImageUtils.h"                                   // FImageUtils::CreateTexture2D / PNGCompressImageArray (ENGINE_API)
#include "Materials/MaterialInterface.h"                  // the one asset family whose info class differs
#include "Misc/App.h"                                     // FApp::CanEverRender
#include "Misc/FileHelper.h"                              // FFileHelper::SaveArrayToFile(TArray64)
#include "Misc/ObjectThumbnail.h"                         // FObjectThumbnail (CORE_API)
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"                        // soft ThumbnailGenerator detection (name only)
#include "ObjectTools.h"                                  // namespace ThumbnailTools (UNREALED_API)
#include "RHIGlobals.h"                                   // GIsRHIInitialized — RenderThumbnail check()s it
#include "TextureCompiler.h"                              // FTextureCompilingManager::FinishCompilation
#include "ThumbnailRendering/SceneThumbnailInfo.h"        // OrbitPitch/OrbitYaw/OrbitZoom
#include "ThumbnailRendering/SceneThumbnailInfoWithPrimitive.h"
#include "ThumbnailRendering/ThumbnailManager.h"          // UThumbnailManager, FThumbnailRenderingInfo
#include "ThumbnailRendering/ThumbnailRenderer.h"         // FThumbnailRenderingInfo::Renderer is a TObjectPtr —
                                                          // reporting its class name needs the COMPLETE type,
                                                          // and ThumbnailManager.h only forward-declares it.
#include "UObject/Class.h"                                // UClass::FindPropertyByName, UEnum
#include "UObject/ReflectedTypeAccessors.h"               // StaticEnum<T>() — read-back names for the texture enums
#include "UObject/ObjectRedirector.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"                          // FSavePackageArgs
#include "UObject/StrongObjectPtr.h"
#include "UObject/UnrealType.h"                           // FObjectProperty / CastField — the ThumbnailInfo swap
#include "UObject/UObjectGlobals.h"
#include "UnrealEdGlobals.h"                              // GUnrealEd

namespace MifBridge
{
	namespace
	{
		// Engine ceiling, not ours: UEditorEngine::GetScratchRenderTarget check()s MinSize <= 2048
		// (EditorEngine.cpp:2756). This file supplies its OWN render target so that path is never
		// taken, but the ceiling still bounds what a thumbnail scene is expected to survive and an
		// unbounded width from a caller is a memory hazard either way.
		constexpr int32 ThumbMinSize = 8;
		constexpr int32 ThumbMaxSize = 2048;
		constexpr int32 ThumbDefaultSize = 256;   // == ThumbnailTools::DefaultThumbnailSize (ObjectTools.h:752)

		// --- asset resolution ------------------------------------------------
		// Accepts /Game/Path/SM_Foo and /Game/Path/SM_Foo.SM_Foo (the ResolveBlueprint spelling rule)
		// and follows redirectors. Deliberately NOT shared with MifBridgeMaterials.cpp's
		// ResolveMaterialOrFunction: that one narrows to two classes, this one must accept any asset
		// a thumbnail renderer is registered for.
		UObject* ThumbResolveAsset(const FString& InPath, FString& OutError)
		{
			FString P = InPath;
			P.TrimStartAndEndInline();
			if (P.IsEmpty())
			{
				OutError = TEXT("asset is required (an asset path, e.g. /Game/Meshes/SM_Sword or /Game/Meshes/SM_Sword.SM_Sword)");
				return nullptr;
			}

			UObject* Obj = StaticLoadObject(UObject::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Obj && !P.Contains(TEXT(".")))
			{
				const FString Full = P + TEXT(".") + FPackageName::GetShortName(P);
				Obj = StaticLoadObject(UObject::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
			if (UObjectRedirector* Redirector = Cast<UObjectRedirector>(Obj))
			{
				Obj = Redirector->DestinationObject;
			}
			if (!Obj)
			{
				OutError = FString::Printf(
					TEXT("asset not found: %s (bare package paths like /Game/A/SM_Foo are accepted; a Blueprint takes its ")
					TEXT("ASSET path /Game/A/BP_Foo, not the generated class /Game/A/BP_Foo.BP_Foo_C)"), *P);
				return nullptr;
			}
			return Obj;
		}

		// --- renderer discovery ----------------------------------------------
		// The name of the UThumbnailRenderer the engine would use, or empty when there is none.
		// Empty is the single most useful diagnostic this file produces: RenderThumbnail returns void
		// and draws NOTHING for an unregistered class (ObjectTools.cpp:5094), so "no renderer" and
		// "rendered a black square" are indistinguishable downstream unless it is reported.
		FString ThumbRendererClassName(UObject* Asset)
		{
			if (!GUnrealEd || !Asset) { return FString(); }
			UThumbnailManager* Manager = GUnrealEd->GetThumbnailManager();
			if (!Manager) { return FString(); }
			FThumbnailRenderingInfo* Info = Manager->GetRenderingInfo(Asset);
			if (!Info || !Info->Renderer) { return FString(); }
			return Info->Renderer->GetClass()->GetName();
		}

		// --- render preconditions ---------------------------------------------
		// RenderThumbnail's FIRST line is `if (!FApp::CanEverRender()) return;` and its second is
		// `check(GIsRHIInitialized)` (ObjectTools.cpp:5033-5041). The first is a SILENT no-op — the
		// caller would receive an all-zero buffer with no explanation — and the second is a crash.
		// Both are therefore tested before the call, never after.
		bool ThumbCanRender(FString& OutWhyNot)
		{
			if (!FApp::CanEverRender())
			{
				OutWhyNot = TEXT("this editor process cannot render (FApp::CanEverRender() is false — running with -nullrhi, ")
					TEXT("as a commandlet without -AllowCommandletRendering, or as a dedicated server). Thumbnail rendering ")
					TEXT("needs a real RHI; there is no software fallback.");
				return false;
			}
			if (!GIsRHIInitialized)
			{
				OutWhyNot = TEXT("the RHI is not initialised yet (GIsRHIInitialized is false) — the editor is still starting up. ")
					TEXT("Retry once the editor is idle.");
				return false;
			}
			if (!GUnrealEd || !GUnrealEd->GetThumbnailManager())
			{
				OutWhyNot = TEXT("the editor's thumbnail manager is unavailable (GUnrealEd/UThumbnailManager is null), so no ")
					TEXT("renderer can be resolved for any asset.");
				return false;
			}
			return true;
		}

		// --- orbit camera: engine-native, and RESTORED --------------------------
		// The thumbnail renderers take their camera from a USceneThumbnailInfo hanging off the asset
		// (ThumbnailHelpers.cpp:575-591 for static meshes, :487-508 skeletal, :400-418 materials) —
		// that object is what the editor writes when a human drags to rotate a Content Browser
		// thumbnail. Setting it is therefore the ONLY supported way to aim the icon; there is no
		// camera argument on RenderThumbnail.
		//
		// It is also why this is an RAII scope rather than three assignments. Two separate mutations
		// have to be undone:
		//   1. ours — pitch/yaw/zoom the caller asked for;
		//   2. THE ENGINE'S — every one of those renderers clamps the info's OrbitZoom in place when
		//      TargetDistance + OrbitZoom < 0 (ThumbnailHelpers.cpp:578-582). That happens whether or
		//      not the caller passed anything, so a render with NO orbit arguments could still leave
		//      the asset's saved camera altered in memory. This scope is therefore installed
		//      unconditionally whenever the asset owns a ThumbnailInfo, which is what lets
		//      render_thumbnail honestly claim the read-only bucket.
		// Nothing here calls Modify() or MarkPackageDirty(): the values are put back before the
		// handler returns, inside the same synchronous tick, so there is nothing for the undo stack
		// to record and the package never becomes dirty.
		struct FThumbOrbitScope
		{
			UObject*             Asset       = nullptr;
			FObjectProperty*     Prop        = nullptr;
			USceneThumbnailInfo* Info        = nullptr;
			bool                 bCreatedTemp = false;
			float                SavedPitch  = 0.0f;
			float                SavedYaw    = 0.0f;
			float                SavedZoom   = 0.0f;

			~FThumbOrbitScope()
			{
				if (Info)
				{
					Info->OrbitPitch = SavedPitch;
					Info->OrbitYaw   = SavedYaw;
					Info->OrbitZoom  = SavedZoom;
				}
				// A temp we minted has to be unhooked too, or the asset silently gains a
				// ThumbnailInfo it never had — which the next package save would persist.
				if (bCreatedTemp && Prop && Asset)
				{
					Prop->SetObjectPropertyValue_InContainer(Asset, nullptr);
				}
			}
		};

		/** The reflection property every thumbnail-capable asset class declares (UStaticMesh:1137,
		 *  USkeletalMesh:1468 — private there, which reflection does not care about —
		 *  UBlueprint:784, UMaterialInterface:341). Found by name rather than by cast so a class this
		 *  file has never heard of works for free. */
		FObjectProperty* ThumbFindInfoProperty(UObject* Asset)
		{
			return CastField<FObjectProperty>(Asset->GetClass()->FindPropertyByName(TEXT("ThumbnailInfo")));
		}

		/** Install the scope. bWant* say which axes the caller actually supplied; the others keep
		 *  whatever the asset already had, so overriding yaw alone does not silently reset pitch.
		 *  Returns false ONLY when the caller asked for an axis the asset cannot express. */
		bool ThumbBeginOrbit(UObject* Asset, bool bWantPitch, float Pitch, bool bWantYaw, float Yaw,
			bool bWantZoom, float Zoom, FThumbOrbitScope& Scope, FString& OutError)
		{
			const bool bAnyRequested = bWantPitch || bWantYaw || bWantZoom;

			FObjectProperty* Prop = ThumbFindInfoProperty(Asset);
			if (!Prop)
			{
				if (!bAnyRequested) { return true; }   // nothing to protect, nothing to apply
				OutError = FString::Printf(
					TEXT("orbitPitch/orbitYaw/orbitZoom are not supported for a %s: its class declares no ThumbnailInfo ")
					TEXT("property, so its thumbnail renderer has no camera to aim (textures, fonts, curves and sounds are ")
					TEXT("drawn flat). Re-send without the orbit fields, or use capture_camera for an arbitrary viewpoint."),
					*Asset->GetClass()->GetName());
				return false;
			}

			UObject* Existing = Prop->GetObjectPropertyValue_InContainer(Asset);
			USceneThumbnailInfo* Info = Cast<USceneThumbnailInfo>(Existing);

			if (!Info)
			{
				if (!bAnyRequested) { return true; }   // engine will read the CDO and write nothing back
				if (Existing)
				{
					// Some other UThumbnailInfo subclass (UWorldThumbnailInfo has no orbit at all).
					OutError = FString::Printf(
						TEXT("orbit control is not supported for this asset: its ThumbnailInfo is a %s, which is not a ")
						TEXT("USceneThumbnailInfo and carries no OrbitPitch/OrbitYaw/OrbitZoom. Re-send without the orbit fields."),
						*Existing->GetClass()->GetName());
					return false;
				}

				// A material's renderer casts to USceneThumbnailInfoWithPrimitive specifically
				// (ThumbnailHelpers.cpp:402) and would ignore a plain USceneThumbnailInfo, silently
				// rendering the default camera while reporting the requested one.
				UClass* InfoClass = Asset->IsA<UMaterialInterface>()
					? USceneThumbnailInfoWithPrimitive::StaticClass()
					: USceneThumbnailInfo::StaticClass();

				// Non-templated NewObject on purpose. Both classes are UCLASS(MinimalAPI)
				// (SceneThumbnailInfo.h:15, SceneThumbnailInfoWithPrimitive.h:18), so their
				// constructors are NOT exported and NewObject<USceneThumbnailInfo>() is an LNK2019
				// from another module; passing the UClass* routes through the class's stored
				// ClassConstructor instead. Same trap create_material documents for its factory.
				// Outered to the TRANSIENT package, never to the asset: an object parented to the
				// asset would be a candidate for serialisation if anything saved the package while
				// the swap was installed, and RF_Transient is a weaker guarantee than not being
				// inside the package at all.
				UObject* Created = NewObject<UObject>(GetTransientPackage(), InfoClass, NAME_None, RF_Transient);
				Info = Cast<USceneThumbnailInfo>(Created);
				if (!Info)
				{
					OutError = TEXT("failed to create a temporary USceneThumbnailInfo for orbit control");
					return false;
				}
				Prop->SetObjectPropertyValue_InContainer(Asset, Info);
				Scope.bCreatedTemp = true;
			}

			Scope.Asset      = Asset;
			Scope.Prop       = Prop;
			Scope.Info       = Info;
			Scope.SavedPitch = Info->OrbitPitch;
			Scope.SavedYaw   = Info->OrbitYaw;
			Scope.SavedZoom  = Info->OrbitZoom;

			if (bWantPitch) { Info->OrbitPitch = Pitch; }
			if (bWantYaw)   { Info->OrbitYaw   = Yaw; }
			if (bWantZoom)  { Info->OrbitZoom  = Zoom; }
			return true;
		}

		// --- the render itself --------------------------------------------------
		// One implementation; all three rendering endpoints call it, so they can never disagree about
		// size clamping, the unattended guard, or what counts as a successful render.
		bool ThumbRender(UObject* Asset, int32 W, int32 H, bool bFlushTextures,
			FObjectThumbnail& OutThumb, double& OutElapsedMs, FString& OutError)
		{
			const double Started = FPlatformTime::Seconds();

			// This file's OWN render target, never GEditor->GetScratchRenderTarget. Two reasons:
			// the scratch targets are editor-global mutable state a read-only endpoint has no
			// business allocating or resizing, and the scratch path carries the
			// check(MinSize <= 2048) crash. TStrongObjectPtr because the AlwaysFlush path below can
			// pump async loading, and an unrooted transient UObject is not guaranteed to survive that.
			TStrongObjectPtr<UTextureRenderTarget2D> RT(NewObject<UTextureRenderTarget2D>(GetTransientPackage()));
			RT->RenderTargetFormat = RTF_RGBA8;
			RT->ClearColor         = FLinearColor::Black;
			RT->bAutoGenerateMips  = false;
			// Match GetScratchRenderTarget's own setup (EditorEngine.cpp:2765-2768) so an icon baked
			// here is the same brightness as the one the Content Browser shows for the same asset.
			RT->TargetGamma = GEngine ? GEngine->DisplayGamma : 2.2f;
			RT->InitAutoFormat(W, H);
			RT->UpdateResourceImmediate(true);

			FTextureRenderTargetResource* Resource = RT->GameThread_GetRenderTargetResource();
			if (!Resource)
			{
				OutError = TEXT("failed to create a render target resource for the thumbnail");
				return false;
			}

			{
				// See the file header: the engine's own guard covers only Renderer->Draw, and the
				// flush path sits outside it. A modal here would freeze the editor AND this bridge.
				TGuardValue<bool> Unattended(GIsRunningUnattendedScript, true);

				ThumbnailTools::RenderThumbnail(
					Asset,
					static_cast<uint32>(W),
					static_cast<uint32>(H),
					bFlushTextures ? ThumbnailTools::EThumbnailTextureFlushMode::AlwaysFlush
					               : ThumbnailTools::EThumbnailTextureFlushMode::NeverFlush,
					Resource,
					&OutThumb);
			}

			OutElapsedMs = (FPlatformTime::Seconds() - Started) * 1000.0;

			// VERIFY, NEVER ASSUME — this is the whole reason the endpoints below can say ok:true.
			// RenderThumbnail returns void. It calls OutThumbnail->SetImageSize(W,H) BEFORE deciding
			// whether it can draw anything (ObjectTools.cpp:5044-5047), so an asset with no
			// registered renderer comes back reporting the requested dimensions with an EMPTY pixel
			// array. Trusting GetImageWidth() alone would bake a 256x256 icon out of nothing.
			// The renderer may also legitimately SHRINK the result (a texture's own aspect ratio,
			// :5124-5142), so the authoritative size is what it reports, not what we asked for.
			const int32 GotW = OutThumb.GetImageWidth();
			const int32 GotH = OutThumb.GetImageHeight();
			const int64 Expected = static_cast<int64>(GotW) * static_cast<int64>(GotH) * 4;
			if (GotW <= 0 || GotH <= 0 || Expected <= 0 || OutThumb.AccessImageData().Num() != Expected)
			{
				const FString Renderer = ThumbRendererClassName(Asset);
				OutError = FString::Printf(
					TEXT("no pixels were rendered for '%s' (a %s): %s. Nothing was written. ")
					TEXT("Check thumbnail_capabilities with this asset path to see whether a renderer exists for its class."),
					*Asset->GetPathName(),
					*Asset->GetClass()->GetName(),
					Renderer.IsEmpty()
						? TEXT("the engine has NO thumbnail renderer registered for this class, so the Content Browser shows it ")
						       TEXT("a generic class icon and there is nothing to capture")
						: TEXT("the renderer produced an empty image"));
				return false;
			}
			return true;
		}

		// --- pixels -------------------------------------------------------------
		// FObjectThumbnail's image data is written by ReadPixelsPtr((FColor*)...) (ObjectTools.cpp:5185),
		// i.e. it IS an FColor array (B,G,R,A byte order) — reinterpreting is exact, not a guess.
		// Copied into a real TArray<FColor> because FImageUtils::CreateTexture2D takes one by
		// reference; at 256x256 that is 256 KB, which is not worth a lifetime puzzle to avoid.
		void ThumbCopyPixels(const FObjectThumbnail& Thumb, TArray<FColor>& OutColors)
		{
			const TArray<uint8>& Bytes = Thumb.AccessImageData();
			const int32 Count = Thumb.GetImageWidth() * Thumb.GetImageHeight();
			OutColors.SetNumUninitialized(Count);
			FMemory::Memcpy(OutColors.GetData(), Bytes.GetData(), static_cast<SIZE_T>(Count) * sizeof(FColor));
		}

		/** Alpha as it came OUT of the renderer, measured before any normalisation. Reported on every
		 *  response so "why is my icon opaque black instead of cut out?" is answerable from the
		 *  payload instead of by opening the PNG. */
		struct FThumbAlphaStats
		{
			uint8 Min = 255;
			uint8 Max = 0;
			int64 FullyTransparent = 0;
			int64 FullyOpaque = 0;
		};

		FThumbAlphaStats ThumbMeasureAlpha(const TArray<FColor>& Colors)
		{
			FThumbAlphaStats S;
			for (const FColor& C : Colors)
			{
				S.Min = FMath::Min(S.Min, C.A);
				S.Max = FMath::Max(S.Max, C.A);
				if (C.A == 0)   { ++S.FullyTransparent; }
				if (C.A == 255) { ++S.FullyOpaque; }
			}
			if (Colors.Num() == 0) { S.Min = 0; }
			return S;
		}

		void ThumbWriteAlphaFields(const TSharedRef<FJsonObject>& Out, const FThumbAlphaStats& S, const TCHAR* Mode)
		{
			TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
			A->SetNumberField(TEXT("min"), S.Min);
			A->SetNumberField(TEXT("max"), S.Max);
			A->SetNumberField(TEXT("fullyTransparentPixels"), static_cast<double>(S.FullyTransparent));
			A->SetNumberField(TEXT("fullyOpaquePixels"), static_cast<double>(S.FullyOpaque));
			A->SetStringField(TEXT("mode"), Mode);
			A->SetStringField(TEXT("note"),
				TEXT("measured as rendered, before alpha mode was applied. The engine thumbnail renderers clear to opaque ")
				TEXT("black (FCanvas::Clear(FLinearColor::Black)) and draw a lit preview scene, so a cut-out icon is NOT ")
				TEXT("generally available from this path — min==max==255 means the background is solid, not transparent."));
			Out->SetObjectField(TEXT("alpha"), A);
		}

		/** ONE alpha rule, applied to the pixel buffer itself so the PNG, the created texture and the
		 *  refilled texture are byte-identical. Doing it in the buffer (rather than via
		 *  FCreateTexture2DParameters::bUseAlpha, which only reaches the create path) is what keeps
		 *  create and overwrite from drifting apart. Returns false for an unknown mode. */
		bool ThumbApplyAlphaMode(const FString& Mode, TArray<FColor>& Colors, FString& OutError)
		{
			if (Mode.Equals(TEXT("opaque"), ESearchCase::IgnoreCase))
			{
				for (FColor& C : Colors) { C.A = 255; }
				return true;
			}
			if (Mode.Equals(TEXT("asRendered"), ESearchCase::IgnoreCase) || Mode.Equals(TEXT("keep"), ESearchCase::IgnoreCase))
			{
				return true;
			}
			OutError = FString::Printf(
				TEXT("unknown alpha '%s' — accepted: opaque (force A=255; the safe default for icons), ")
				TEXT("asRendered (alias: keep — leave whatever the renderer produced, which is usually also opaque)"), *Mode);
			return false;
		}

		// --- texture settings ---------------------------------------------------
		struct FThumbCompressionName { const TCHAR* Name; TextureCompressionSettings Value; };
		const FThumbCompressionName ThumbCompressions[] =
		{
			// TC_EditorIcon is "UserInterface2D (RGBA)" (TextureDefines.h:353) — uncompressed RGBA.
			// It is the default because a DXT-compressed 64px icon shows block artefacts on exactly
			// the flat colours and hard edges an icon is made of.
			{ TEXT("EditorIcon"),            TC_EditorIcon },
			{ TEXT("UserInterface2D"),       TC_EditorIcon },
			{ TEXT("Default"),               TC_Default },
			{ TEXT("VectorDisplacementmap"), TC_VectorDisplacementmap },
			{ TEXT("Grayscale"),             TC_Grayscale },
		};

		bool ThumbParseCompression(const FString& In, TextureCompressionSettings& Out, FString& OutError)
		{
			FString S = In;
			S.RemoveFromStart(TEXT("TC_"), ESearchCase::IgnoreCase);
			for (const FThumbCompressionName& E : ThumbCompressions)
			{
				if (S.Equals(E.Name, ESearchCase::IgnoreCase)) { Out = E.Value; return true; }
			}
			OutError = FString::Printf(
				TEXT("unknown compression '%s' — accepted: EditorIcon (alias UserInterface2D, uncompressed RGBA, the default ")
				TEXT("and the right answer for UI icons), Default, VectorDisplacementmap, Grayscale"), *In);
			return false;
		}

		/** Read-BACK name for any of the three texture enums. Reflection rather than the parse tables
		 *  above on purpose: those tables list only the values this endpoint ACCEPTS, so a refilled
		 *  stub that already carried, say, TC_Normalmap would have been reported as "Unknown" — an
		 *  endpoint that cannot name what it left in place has no business claiming it verified it. */
		template <typename TEnum>
		FString ThumbEnumName(TEnum Value)
		{
			if (const UEnum* Enum = StaticEnum<TEnum>())
			{
				const FString Name = Enum->GetNameStringByValue(static_cast<int64>(Value));
				if (!Name.IsEmpty()) { return Name; }
			}
			return FString::Printf(TEXT("%d"), static_cast<int32>(Value));
		}

		struct FThumbGroupName { const TCHAR* Name; TextureGroup Value; };
		const FThumbGroupName ThumbGroups[] =
		{
			{ TEXT("UI"),        TEXTUREGROUP_UI },
			{ TEXT("World"),     TEXTUREGROUP_World },
			{ TEXT("Character"), TEXTUREGROUP_Character },
			// TEXTUREGROUP_MAX is FCreateTexture2DParameters' own sentinel for "leave LODGroup alone"
			// (ImageUtils.cpp:665-668); exposing it as "none" keeps that meaning reachable.
			{ TEXT("none"),      TEXTUREGROUP_MAX },
		};

		bool ThumbParseGroup(const FString& In, TextureGroup& Out, FString& OutError)
		{
			FString S = In;
			S.RemoveFromStart(TEXT("TEXTUREGROUP_"), ESearchCase::IgnoreCase);
			for (const FThumbGroupName& E : ThumbGroups)
			{
				if (S.Equals(E.Name, ESearchCase::IgnoreCase)) { Out = E.Value; return true; }
			}
			OutError = FString::Printf(TEXT("unknown lodGroup '%s' — accepted: UI (default), World, Character, none"), *In);
			return false;
		}

		// --- shared parameter reads ---------------------------------------------
		/** width/height, clamped and echoed. A caller that asks for 4096 gets 2048 AND is told so in
		 *  the response's requestedWidth/requestedHeight, because silently honouring a different size
		 *  than the one asked for is the silent-ignore class this codebase exists to kill. */
		void ThumbReadSize(const TSharedRef<FJsonObject>& In, int32& OutW, int32& OutH, int32& OutReqW, int32& OutReqH)
		{
			OutReqW = JInt(In, TEXT("width"), ThumbDefaultSize);
			OutReqH = JInt(In, TEXT("height"), OutReqW);   // height defaults to width: icons are square
			OutW = FMath::Clamp(OutReqW, ThumbMinSize, ThumbMaxSize);
			OutH = FMath::Clamp(OutReqH, ThumbMinSize, ThumbMaxSize);
		}

		void ThumbWriteSizeFields(const TSharedRef<FJsonObject>& Out, int32 W, int32 H, int32 ReqW, int32 ReqH)
		{
			Out->SetNumberField(TEXT("width"), W);
			Out->SetNumberField(TEXT("height"), H);
			if (ReqW != W || ReqH != H)
			{
				Out->SetNumberField(TEXT("requestedWidth"), ReqW);
				Out->SetNumberField(TEXT("requestedHeight"), ReqH);
				Out->SetStringField(TEXT("sizeNote"), FString::Printf(
					TEXT("requested %dx%d was clamped to %dx%d (engine thumbnail ceiling is %d, floor %d)"),
					ReqW, ReqH, W, H, ThumbMaxSize, ThumbMinSize));
			}
		}

		/** Render + orbit + verification, shared by all three rendering endpoints. Fails Out itself
		 *  and returns false so a handler is one `if` away from correct. */
		bool ThumbRenderForHandler(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
			UObject* Asset, int32 W, int32 H, FObjectThumbnail& OutThumb, double& OutElapsedMs)
		{
			FString Why;
			if (!ThumbCanRender(Why)) { Fail(Out, Why); return false; }

			const bool bWantPitch = JHasAny(In, { TEXT("orbitPitch") });
			const bool bWantYaw   = JHasAny(In, { TEXT("orbitYaw") });
			const bool bWantZoom  = JHasAny(In, { TEXT("orbitZoom") });

			FThumbOrbitScope Orbit;
			FString OrbitError;
			if (!ThumbBeginOrbit(Asset,
				bWantPitch, static_cast<float>(JNum(In, TEXT("orbitPitch"))),
				bWantYaw,   static_cast<float>(JNum(In, TEXT("orbitYaw"))),
				bWantZoom,  static_cast<float>(JNum(In, TEXT("orbitZoom"))),
				Orbit, OrbitError))
			{
				Fail(Out, OrbitError);
				return false;
			}

			const bool bFlush = JBool(In, TEXT("flushTextures"), false);
			FString RenderError;
			if (!ThumbRender(Asset, W, H, bFlush, OutThumb, OutElapsedMs, RenderError))
			{
				Fail(Out, RenderError);
				return false;
			}

			Out->SetBoolField(TEXT("flushedTextures"), bFlush);
			Out->SetNumberField(TEXT("elapsedMs"), FMath::RoundToDouble(OutElapsedMs));
			if (Orbit.Info)
			{
				TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
				O->SetNumberField(TEXT("pitch"), Orbit.Info->OrbitPitch);
				O->SetNumberField(TEXT("yaw"), Orbit.Info->OrbitYaw);
				O->SetNumberField(TEXT("zoom"), Orbit.Info->OrbitZoom);
				O->SetBoolField(TEXT("temporary"), Orbit.bCreatedTemp);
				O->SetStringField(TEXT("note"),
					TEXT("applied for this render only — the asset's saved ThumbnailInfo is restored before this response is ")
					TEXT("sent, so the asset is not dirtied and the Content Browser icon is unchanged."));
				Out->SetObjectField(TEXT("orbit"), O);
			}
			else if (bWantPitch || bWantYaw || bWantZoom)
			{
				// Unreachable by construction (ThumbBeginOrbit fails instead), kept as a tripwire.
				Out->SetStringField(TEXT("orbitNote"), TEXT("orbit fields were supplied but no ThumbnailInfo was in play"));
			}
			return true;
		}

		/** <ProjectSaved>/MifBridge/Thumbnails/<name>.png, created on demand. Same root as
		 *  capture_camera's output so a caller has one place to look for bridge-produced images. */
		FString ThumbPngPathFor(const FString& InName)
		{
			const FString Dir = FPaths::ProjectSavedDir() / TEXT("MifBridge") / TEXT("Thumbnails");
			FPlatformFileManager::Get().GetPlatformFile().CreateDirectoryTree(*Dir);
			return FPaths::ConvertRelativePathToFull(Dir / (FPaths::MakeValidFileName(InName) + TEXT(".png")));
		}

		/** PNG via FImageUtils::PNGCompressImageArray + FFileHelper (both ENGINE_API/CORE_API). NOT
		 *  FImageUtils::SaveImageByExtension, which would need an FImageView and therefore the
		 *  ImageCore module on MifBridge.Build.cs — a new module dependency bought for nothing. */
		bool ThumbWritePng(const TArray<FColor>& Colors, int32 W, int32 H, const FString& FullPath,
			int64& OutBytes, FString& OutError)
		{
			TArray64<uint8> Png;
			FImageUtils::PNGCompressImageArray(W, H, TArrayView64<const FColor>(Colors.GetData(), Colors.Num()), Png);
			if (Png.Num() == 0)
			{
				OutError = FString::Printf(TEXT("PNG compression produced no data for %dx%d"), W, H);
				return false;
			}
			if (!FFileHelper::SaveArrayToFile(Png, *FullPath))
			{
				OutError = FString::Printf(TEXT("failed to write %s (check disk space and that the path is writable)"), *FullPath);
				return false;
			}
			OutBytes = Png.Num();
			return true;
		}

		/** Save one package and VERIFY the file landed. Local rather than reusing H_save_package
		 *  because that one resolves by path and fails Out itself; this one is a step inside a larger
		 *  handler that has already produced an asset. Textures are never maps, so the ContainsMap
		 *  branch those handlers carry is not applicable here. */
		bool ThumbSavePackage(UPackage* Package, FString& OutFileName, FString& OutError)
		{
			OutFileName = FPackageName::LongPackageNameToFilename(Package->GetName(), FPackageName::GetAssetPackageExtension());

			FSavePackageArgs SaveArgs;
			SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
			SaveArgs.SaveFlags = SAVE_NoError;

			if (!UPackage::SavePackage(Package, nullptr, *OutFileName, SaveArgs))
			{
				OutError = FString::Printf(
					TEXT("the texture was built but SavePackage failed for %s — the asset exists in memory only. ")
					TEXT("Call save_package with its path once the cause is fixed (read-only file, source control checkout)."),
					*Package->GetName());
				return false;
			}
			if (!IFileManager::Get().FileExists(*OutFileName))
			{
				OutError = FString::Printf(
					TEXT("SavePackage reported success but %s does not exist on disk"), *OutFileName);
				return false;
			}
			return true;
		}
	}

	// --- thumbnail_capabilities ------------------------------------------------------
	//   in:  { asset? }
	//   out: { canRender, whyNot?, rhiInitialized, thumbnailManager, defaultSize, maxSize,
	//          asset?, class?, renderer?, hasRenderer?, hasCustomThumbnail?, supportsOrbit?,
	//          thumbnailGeneratorPluginLoaded, notes[] }
	// Bucket: READ-ONLY — enumerates engine state and loads nothing but the asset the caller named.
	//
	// This exists because every failure mode in this file is a PRECONDITION, not a bug: an editor
	// with no RHI, or an asset class with no registered renderer, cannot produce an icon no matter
	// how the call is spelled. Asking here first turns "write_thumbnail_texture failed" into "this
	// asset was never going to have a thumbnail" before anything is written.
	void H_thumbnail_capabilities(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("asset"), TEXT("assetPath"), TEXT("path") },
			TEXT("asset (aliases: assetPath, path) — optional; omit for editor-wide capability only")))
		{
			return;
		}

		FString Why;
		const bool bCanRender = ThumbCanRender(Why);
		Out->SetBoolField(TEXT("canRender"), bCanRender);
		if (!bCanRender) { Out->SetStringField(TEXT("whyNot"), Why); }

		Out->SetBoolField(TEXT("rhiInitialized"), GIsRHIInitialized);
		Out->SetBoolField(TEXT("canEverRender"), FApp::CanEverRender());
		Out->SetBoolField(TEXT("thumbnailManager"), GUnrealEd != nullptr && GUnrealEd->GetThumbnailManager() != nullptr);
		Out->SetNumberField(TEXT("defaultSize"), ThumbDefaultSize);
		Out->SetNumberField(TEXT("minSize"), ThumbMinSize);
		Out->SetNumberField(TEXT("maxSize"), ThumbMaxSize);

		// SOFT, NAME-ONLY probe. IsModuleLoaded takes an FName and needs no header, no link symbol
		// and no entry in MifBridge.Build.cs — which is the entire point: MifBridge must load in
		// projects that do not have Plugins_RamaThumb. Reported so a caller knows the option exists,
		// explicitly NOT used, and must not become a call site (see the file header).
		const bool bThumbGen = FModuleManager::Get().IsModuleLoaded(TEXT("ThumbnailGenerator"));
		Out->SetBoolField(TEXT("thumbnailGeneratorPluginLoaded"), bThumbGen);

		TArray<TSharedPtr<FJsonValue>> Notes;
		Notes.Add(MakeShared<FJsonValueString>(
			TEXT("rendering is synchronous and completes inside one bridge tick; there is no job slot to poll")));
		Notes.Add(MakeShared<FJsonValueString>(
			TEXT("flushTextures:true blocks the whole editor (and this bridge) on asset compilation and streaming — ")
			TEXT("use it once for a final bake, not in a loop")));
		if (bThumbGen)
		{
			Notes.Add(MakeShared<FJsonValueString>(
				TEXT("the ThumbnailGenerator plugin is loaded in this project but MifBridge does NOT use it: MifBridge must ")
				TEXT("load in projects without it, so these endpoints use the engine's ThumbnailTools path only")));
		}

		const FString AssetPath = JStrAny(In, { TEXT("asset"), TEXT("assetPath"), TEXT("path") });
		if (!AssetPath.IsEmpty())
		{
			FString ResolveError;
			UObject* Asset = ThumbResolveAsset(AssetPath, ResolveError);
			if (!Asset) { Fail(Out, ResolveError); return; }

			const FString Renderer = ThumbRendererClassName(Asset);
			Out->SetStringField(TEXT("asset"), Asset->GetPathName());
			Out->SetStringField(TEXT("class"), Asset->GetClass()->GetName());
			Out->SetStringField(TEXT("renderer"), Renderer);
			Out->SetBoolField(TEXT("hasRenderer"), !Renderer.IsEmpty());
			Out->SetBoolField(TEXT("assetPackageCooked"), IsCookedOrContainerPackage(Asset->GetPackage()));

			// Orbit support is a property question, answered by reflection rather than a class list.
			FObjectProperty* InfoProp = ThumbFindInfoProperty(Asset);
			bool bOrbit = false;
			if (InfoProp)
			{
				UObject* Existing = InfoProp->GetObjectPropertyValue_InContainer(Asset);
				// No info yet is still orbit-capable: one is minted for the render and removed after.
				bOrbit = (Existing == nullptr) || Existing->IsA<USceneThumbnailInfo>();
			}
			Out->SetBoolField(TEXT("supportsOrbit"), bOrbit);
			Out->SetBoolField(TEXT("hasCustomThumbnail"),
				ThumbnailTools::AssetHasCustomThumbnail(Asset->GetFullName()));

			if (Renderer.IsEmpty())
			{
				Notes.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("no thumbnail renderer is registered for %s — the Content Browser shows a generic class icon for it, ")
					TEXT("and render_thumbnail / write_thumbnail_texture will refuse rather than bake a black square"),
					*Asset->GetClass()->GetName())));
			}
			if (!bOrbit)
			{
				Notes.Add(MakeShared<FJsonValueString>(
					TEXT("this asset has no USceneThumbnailInfo, so orbitPitch/orbitYaw/orbitZoom are not accepted for it")));
			}
		}

		Out->SetArrayField(TEXT("notes"), Notes);
	}

	// --- render_thumbnail ------------------------------------------------------------
	//   in:  { asset, width? = 256, height? = width, orbitPitch?, orbitYaw?, orbitZoom?,
	//          flushTextures? = false, alpha? = "opaque", name? }
	//   out: { asset, class, renderer, width, height, pngPath, pngBytes, pngExists,
	//          alpha:{...}, elapsedMs, flushedTextures, orbit? }
	// Bucket: READ-ONLY — writes a PNG under <ProjectSaved>/MifBridge/Thumbnails and mutates no
	// asset. The asset's ThumbnailInfo is saved and restored around the render (see FThumbOrbitScope),
	// so this is read-only in fact and not merely by declaration. capture_camera is the precedent:
	// it too renders and writes a file from the read-only bucket (MifBridgeCommon.cpp:410-411).
	//
	// Use this BEFORE write_thumbnail_texture. An unattended agent cannot judge an icon it never
	// looked at, and a bad camera angle baked into 42 assets is 42 assets to redo.
	void H_render_thumbnail(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("width"), TEXT("height"),
			  TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"),
			  TEXT("flushTextures"), TEXT("alpha"), TEXT("name") },
			TEXT("asset (aliases: assetPath, path), width, height, orbitPitch, orbitYaw, orbitZoom, ")
			TEXT("flushTextures, alpha, name")))
		{
			return;
		}

		FString ResolveError;
		UObject* Asset = ThumbResolveAsset(JStrAny(In, { TEXT("asset"), TEXT("assetPath"), TEXT("path") }), ResolveError);
		if (!Asset) { Fail(Out, ResolveError); return; }

		const FString AlphaMode = JStr(In, TEXT("alpha"), TEXT("opaque"));
		// Parsed BEFORE anything is rendered so a bad enum costs nothing (the create_material rule).
		{
			TArray<FColor> Probe;
			FString AlphaError;
			if (!ThumbApplyAlphaMode(AlphaMode, Probe, AlphaError)) { Fail(Out, AlphaError); return; }
		}

		int32 W, H, ReqW, ReqH;
		ThumbReadSize(In, W, H, ReqW, ReqH);

		FObjectThumbnail Thumb;
		double ElapsedMs = 0.0;
		if (!ThumbRenderForHandler(In, Out, Asset, W, H, Thumb, ElapsedMs)) { return; }

		// Authoritative dimensions come from the thumbnail, not from the request — the renderer is
		// allowed to shrink to the asset's own aspect ratio (ObjectTools.cpp:5124-5142).
		const int32 GotW = Thumb.GetImageWidth();
		const int32 GotH = Thumb.GetImageHeight();

		TArray<FColor> Colors;
		ThumbCopyPixels(Thumb, Colors);
		const FThumbAlphaStats Alpha = ThumbMeasureAlpha(Colors);
		FString AlphaError;
		ThumbApplyAlphaMode(AlphaMode, Colors, AlphaError);   // already validated above

		const FString Name = JStr(In, TEXT("name"), Asset->GetName());
		const FString PngPath = ThumbPngPathFor(Name);
		int64 PngBytes = 0;
		FString PngError;
		if (!ThumbWritePng(Colors, GotW, GotH, PngPath, PngBytes, PngError)) { Fail(Out, PngError); return; }

		Out->SetStringField(TEXT("asset"), Asset->GetPathName());
		Out->SetStringField(TEXT("class"), Asset->GetClass()->GetName());
		Out->SetStringField(TEXT("renderer"), ThumbRendererClassName(Asset));
		ThumbWriteSizeFields(Out, GotW, GotH, ReqW, ReqH);
		ThumbWriteAlphaFields(Out, Alpha, *AlphaMode);
		Out->SetStringField(TEXT("pngPath"), PngPath);
		Out->SetNumberField(TEXT("pngBytes"), static_cast<double>(PngBytes));
		// Verified, not assumed — the same discipline capture_camera applies to its own output.
		Out->SetBoolField(TEXT("pngExists"), IFileManager::Get().FileExists(*PngPath));
		Out->SetStringField(TEXT("hint"),
			TEXT("this wrote an image FILE only. To fill an icon asset that other assets reference by path, ")
			TEXT("call write_thumbnail_texture with the same asset/width/orbit arguments."));
		UE_LOG(LogMifBridge, Log, TEXT("render_thumbnail: %s -> %s (%dx%d, %.0f ms)"),
			*Asset->GetPathName(), *PngPath, GotW, GotH, ElapsedMs);
	}

	// --- write_thumbnail_texture -----------------------------------------------------
	//   in:  { asset, texturePath, width? = 256, height? = width, orbitPitch?, orbitYaw?, orbitZoom?,
	//          flushTextures? = false, alpha? = "opaque", srgb? = true, compression? = "EditorIcon",
	//          lodGroup? = "UI", generateMips? = false, overwrite? = false, save? = true }
	//   out: { texturePath, objectPath, packageName, created|refilled, width, height, sourceFormat,
	//          sourceSizeX, sourceSizeY, srgb, compression, lodGroup, mipGenSettings, alpha:{...},
	//          savedTo?, fileExists, elapsedMs, ... }
	// Bucket: SELF-MANAGED — package/UObject creation, a texture build and a package save.
	//
	// THIS IS THE ENDPOINT THE REST OF THE FILE EXISTS FOR. Rendering pixels does not fill an empty
	// icon stub; only a UTexture2D asset at the path other assets already reference does.
	//
	// TWO MODES, and the second is the one that matters for existing stubs:
	//   create   — texturePath does not exist: a new package + UTexture2D.
	//   refill   — texturePath exists and overwrite:true: the EXISTING UTexture2D's source is
	//              replaced IN PLACE. The object path, the GUID and therefore every widget, data
	//              table and material that already points at it keep working. Deleting and recreating
	//              would break all of them silently, which is why overwrite is a refill and never a
	//              delete-and-create.
	//
	// The SOURCE asset may be cooked — a cooked mesh renders perfectly well, and in this project most
	// of them are. Only the DESTINATION texture is required to be editable, because FTextureSource is
	// editor-only data that a cooked package does not carry.
	void H_write_thumbnail_texture(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("texturePath"), TEXT("outputPath"),
			  TEXT("width"), TEXT("height"), TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"),
			  TEXT("flushTextures"), TEXT("alpha"), TEXT("srgb"), TEXT("compression"), TEXT("lodGroup"),
			  TEXT("generateMips"), TEXT("overwrite"), TEXT("save") },
			TEXT("asset (aliases: assetPath, path), texturePath (alias: outputPath), width, height, ")
			TEXT("orbitPitch, orbitYaw, orbitZoom, flushTextures, alpha, srgb, compression, lodGroup, ")
			TEXT("generateMips, overwrite, save"),
			{ { TEXT("name"), TEXT("render_thumbnail names a PNG file; this endpoint names an ASSET — use texturePath") } }))
		{
			return;
		}

		// ---- resolve and validate EVERYTHING before a single pixel is rendered ----
		FString ResolveError;
		UObject* Asset = ThumbResolveAsset(JStrAny(In, { TEXT("asset"), TEXT("assetPath"), TEXT("path") }), ResolveError);
		if (!Asset) { Fail(Out, ResolveError); return; }

		FString TexturePath = JStrAny(In, { TEXT("texturePath"), TEXT("outputPath") });
		TexturePath.TrimStartAndEndInline();
		if (TexturePath.IsEmpty())
		{
			Fail(Out, TEXT("texturePath is required (the /Game/... UTexture2D asset to write or refill)"));
			return;
		}
		if (!TexturePath.StartsWith(TEXT("/Game/")))
		{
			Fail(Out, TEXT("texturePath must start with /Game/"));
			return;
		}
		// A caller who pasted an object path (/Game/UI/T_Icon.T_Icon) means the same asset; normalise
		// to the package path rather than minting a package literally named "T_Icon.T_Icon".
		{
			const FString ShortName = FPackageName::GetShortName(TexturePath);
			FString Left, Right;
			if (ShortName.Split(TEXT("."), &Left, &Right) && Left == Right)
			{
				TexturePath = FPackageName::GetLongPackagePath(TexturePath) / Left;
			}
		}
		const FString TextureName = FPackageName::GetLongPackageAssetName(TexturePath);
		if (TextureName.IsEmpty() || !FPackageName::IsValidLongPackageName(TexturePath))
		{
			Fail(Out, FString::Printf(TEXT("invalid texturePath: %s"), *TexturePath));
			return;
		}
		const FString ObjectPath = TexturePath + TEXT(".") + TextureName;

		const FString AlphaMode = JStr(In, TEXT("alpha"), TEXT("opaque"));
		{
			TArray<FColor> Probe;
			FString AlphaError;
			if (!ThumbApplyAlphaMode(AlphaMode, Probe, AlphaError)) { Fail(Out, AlphaError); return; }
		}

		TextureCompressionSettings Compression = TC_EditorIcon;
		FString SettingError;
		if (!ThumbParseCompression(JStr(In, TEXT("compression"), TEXT("EditorIcon")), Compression, SettingError))
		{
			Fail(Out, SettingError);
			return;
		}
		TextureGroup LodGroup = TEXTUREGROUP_UI;
		if (!ThumbParseGroup(JStr(In, TEXT("lodGroup"), TEXT("UI")), LodGroup, SettingError))
		{
			Fail(Out, SettingError);
			return;
		}
		const bool bSRGB = JBool(In, TEXT("srgb"), true);
		const bool bMips = JBool(In, TEXT("generateMips"), false);
		const bool bOverwrite = JBool(In, TEXT("overwrite"), false);
		const bool bSave = JBool(In, TEXT("save"), true);
		const TextureMipGenSettings MipGen = bMips ? TMGS_FromTextureGroup : TMGS_NoMipmaps;

		// WHICH settings did the caller actually ASK for? On a refill this is the difference between
		// filling a stub and quietly re-configuring it. An existing icon stub may have been given a
		// deliberate compression, LOD group or sRGB flag by a human; applying this endpoint's
		// defaults over the top would be a silent, unrequested change to somebody else's decision —
		// and the response would report the new values as if they had always been that way. So on the
		// refill path a setting is written ONLY when it was supplied, and the response echoes what the
		// texture ends up holding rather than what was parsed. The create path has nothing to
		// preserve, so it applies the icon-shaped defaults in full.
		const bool bHasSRGB       = JHasAny(In, { TEXT("srgb") });
		const bool bHasCompression = JHasAny(In, { TEXT("compression") });
		const bool bHasLodGroup   = JHasAny(In, { TEXT("lodGroup") });
		const bool bHasMips       = JHasAny(In, { TEXT("generateMips") });

		// ---- destination: existing (refill) or new (create)? ----
		// StaticFindObject first (already loaded), then a package-existence test, then a load. An
		// asset present on disk but not in memory must NOT be treated as absent — that is how an
		// overwrite:false guard gets bypassed and a referenced stub is replaced by a new package.
		UObject* ExistingObj = StaticFindObject(UObject::StaticClass(), nullptr, *ObjectPath);
		if (!ExistingObj && FPackageName::DoesPackageExist(TexturePath))
		{
			ExistingObj = StaticLoadObject(UObject::StaticClass(), nullptr, *ObjectPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
		}

		UTexture2D* ExistingTexture = nullptr;
		if (ExistingObj)
		{
			if (!bOverwrite)
			{
				Fail(Out, FString::Printf(
					TEXT("an asset already exists at %s (a %s). Pass overwrite:true to REFILL it in place — the object path ")
					TEXT("and every existing reference to it are preserved — or choose a new texturePath."),
					*TexturePath, *ExistingObj->GetClass()->GetName()));
				return;
			}
			ExistingTexture = Cast<UTexture2D>(ExistingObj);
			if (!ExistingTexture)
			{
				Fail(Out, FString::Printf(
					TEXT("%s exists but is a %s, not a UTexture2D — refusing to replace an asset of a different type. ")
					TEXT("Delete it with delete_asset first if that is really the intent."),
					*TexturePath, *ExistingObj->GetClass()->GetName()));
				return;
			}
			if (IsCookedOrContainerPackage(ExistingTexture->GetPackage()))
			{
				Fail(Out, FString::Printf(
					TEXT("%s lives in a COOKED package. FTextureSource is editor-only data and is stripped at cook, so there ")
					TEXT("is nothing to refill — writing source into it would produce an asset whose pixels and source ")
					TEXT("disagree. Write a NEW texture at an editable /Game/ path and repoint the reference instead."),
					*TexturePath));
				return;
			}
		}

		int32 W, H, ReqW, ReqH;
		ThumbReadSize(In, W, H, ReqW, ReqH);

		// ---- render ----
		FObjectThumbnail Thumb;
		double ElapsedMs = 0.0;
		if (!ThumbRenderForHandler(In, Out, Asset, W, H, Thumb, ElapsedMs)) { return; }

		const int32 GotW = Thumb.GetImageWidth();
		const int32 GotH = Thumb.GetImageHeight();

		TArray<FColor> Colors;
		ThumbCopyPixels(Thumb, Colors);
		const FThumbAlphaStats Alpha = ThumbMeasureAlpha(Colors);
		FString AlphaError;
		ThumbApplyAlphaMode(AlphaMode, Colors, AlphaError);   // validated above; one rule, one buffer

		// ---- write ----
		UTexture2D* Texture = nullptr;
		if (ExistingTexture)
		{
			// REFILL. PreEditChange/PostEditChange around the source swap is what makes the editor
			// rebuild platform data and refresh any open texture editor; Source.Init replaces the
			// mip chain wholesale, so a 64px stub refilled at 256px simply becomes 256px.
			// FColor is B,G,R,A in memory, which is exactly TSF_BGRA8 — the memcpy is a format
			// identity, not a conversion.
			ExistingTexture->PreEditChange(nullptr);
			ExistingTexture->Source.Init(GotW, GotH, /*NumSlices*/ 1, /*NumMips*/ 1, TSF_BGRA8,
				reinterpret_cast<const uint8*>(Colors.GetData()));
			// Supplied-only writes — see the bHas* block above. The pixels are always replaced (that
			// is the request); the CONFIGURATION is left exactly as the stub's author set it unless
			// this call named the field.
			if (bHasSRGB)        { ExistingTexture->SRGB = bSRGB; }
			if (bHasCompression) { ExistingTexture->CompressionSettings = Compression; }
			if (bHasMips)        { ExistingTexture->MipGenSettings = MipGen; }
			if (bHasLodGroup && LodGroup != TEXTUREGROUP_MAX) { ExistingTexture->LODGroup = LodGroup; }
			ExistingTexture->PostEditChange();
			Texture = ExistingTexture;
		}
		else
		{
			UPackage* Package = CreatePackage(*TexturePath);
			if (!Package) { Fail(Out, FString::Printf(TEXT("failed to create package %s"), *TexturePath)); return; }

			FCreateTexture2DParameters Params;
			// bUseAlpha:true because this file already applied its ONE alpha rule to Colors above.
			// Leaving it false would make FImageUtils force A=255 a second time (ImageUtils.cpp:631)
			// and set CompressionNoAlpha, quietly overriding an asRendered request.
			Params.bUseAlpha = true;
			Params.bSRGB = bSRGB;
			Params.CompressionSettings = Compression;
			Params.MipGenSettings = MipGen;
			Params.TextureGroup = LodGroup;
			Params.bDeferCompression = false;
			Params.bVirtualTexture = false;

			Texture = FImageUtils::CreateTexture2D(GotW, GotH, Colors, Package, TextureName,
				RF_Public | RF_Standalone, Params);
			if (!Texture)
			{
				Fail(Out, FString::Printf(TEXT("failed to create UTexture2D at %s"), *TexturePath));
				return;
			}
			FAssetRegistryModule::AssetCreated(Texture);
		}

		UPackage* Package = Texture->GetOutermost();
		Package->MarkPackageDirty();

		// The 5.3 texture build is ASYNC (FTextureCompilingManager), so GetSizeX()/GetPlatformData()
		// are meaningless the instant PostEditChange returns. Blocking here is what lets every number
		// below be a measurement rather than a hope — and SavePackage would block on it anyway.
		{
			UTexture* ToFinish[] = { Texture };
			FTextureCompilingManager::Get().FinishCompilation(ToFinish);
		}

		// ---- VERIFY AFTER WRITE ----
		// FTextureSource is the source of truth for an editor texture (platform data is derived), so
		// it is what gets checked. ok:true past this point means the bytes are actually in the asset.
		if (!Texture->Source.IsValid())
		{
			Fail(Out, FString::Printf(
				TEXT("wrote %s but its texture source is invalid afterwards — the asset is NOT usable"), *TexturePath));
			return;
		}
		const int64 SrcX = Texture->Source.GetSizeX();
		const int64 SrcY = Texture->Source.GetSizeY();
		if (SrcX != GotW || SrcY != GotH)
		{
			Fail(Out, FString::Printf(
				TEXT("wrote %s but its source is %lldx%lld instead of the rendered %dx%d"),
				*TexturePath, SrcX, SrcY, GotW, GotH));
			return;
		}
		if (!StaticFindObject(UTexture2D::StaticClass(), nullptr, *ObjectPath))
		{
			Fail(Out, FString::Printf(
				TEXT("wrote a texture but nothing resolves at %s — the asset would not be findable by path"), *ObjectPath));
			return;
		}

		FString SavedTo;
		bool bFileExists = false;
		if (bSave)
		{
			FString SaveError;
			if (!ThumbSavePackage(Package, SavedTo, SaveError)) { Fail(Out, SaveError); return; }
			bFileExists = true;   // ThumbSavePackage already verified the file, and fails otherwise
		}

		EmitAssetIdentity(Out, ObjectPath, Package->GetName());
		Out->SetStringField(TEXT("texturePath"), TexturePath);
		Out->SetStringField(TEXT("asset"), Asset->GetPathName());
		Out->SetStringField(TEXT("renderer"), ThumbRendererClassName(Asset));
		Out->SetBoolField(TEXT("created"), ExistingTexture == nullptr);
		Out->SetBoolField(TEXT("refilled"), ExistingTexture != nullptr);
		ThumbWriteSizeFields(Out, GotW, GotH, ReqW, ReqH);
		ThumbWriteAlphaFields(Out, Alpha, *AlphaMode);
		Out->SetStringField(TEXT("sourceFormat"), TEXT("TSF_BGRA8"));
		Out->SetNumberField(TEXT("sourceSizeX"), static_cast<double>(SrcX));
		Out->SetNumberField(TEXT("sourceSizeY"), static_cast<double>(SrcY));
		// READ BACK from the asset, never echoed from the parsed request: on a refill the settings
		// this call did not name were deliberately left alone, so the request is not the answer to
		// "what is this texture now?".
		Out->SetBoolField(TEXT("srgb"), Texture->SRGB != 0);
		Out->SetStringField(TEXT("compression"), ThumbEnumName(Texture->CompressionSettings.GetValue()));
		Out->SetStringField(TEXT("lodGroup"), ThumbEnumName(Texture->LODGroup.GetValue()));
		Out->SetStringField(TEXT("mipGenSettings"), ThumbEnumName(Texture->MipGenSettings.GetValue()));
		Out->SetBoolField(TEXT("saved"), bSave);
		if (bSave)
		{
			Out->SetStringField(TEXT("savedTo"), SavedTo);
			Out->SetBoolField(TEXT("fileExists"), bFileExists);
		}
		else
		{
			Out->SetStringField(TEXT("saveNote"),
				TEXT("save:false — the texture exists in memory and the package is dirty. Nothing is on disk until ")
				TEXT("save_package (or save_dirty_packages) runs."));
		}
		UE_LOG(LogMifBridge, Log, TEXT("write_thumbnail_texture: %s -> %s (%dx%d, %s, %.0f ms)"),
			*Asset->GetPathName(), *TexturePath, GotW, GotH,
			ExistingTexture ? TEXT("refilled") : TEXT("created"), ElapsedMs);
	}

	// --- set_asset_thumbnail ---------------------------------------------------------
	//   in:  { asset, width? = 256, height? = width, orbitPitch?, orbitYaw?, orbitZoom?,
	//          flushTextures? = false, save? = false }
	//   out: { asset, width, height, cached, packageDirty, savedTo?, fileExists?, elapsedMs, orbit? }
	// Bucket: SELF-MANAGED — dirties (and optionally saves) the asset's own package.
	//
	// The Content Browser half: this is the programmatic form of right-click > "Capture Thumbnail",
	// which is a DIFFERENT thing from write_thumbnail_texture. A package thumbnail is metadata the
	// editor shows; it is not an asset, cannot be referenced, and is stripped at cook. Use it to make
	// a folder of generated assets legible to a human; use write_thumbnail_texture to fill an icon a
	// game actually displays.
	//
	// Deliberately NOT ThumbnailTools::GenerateThumbnailForObjectToSaveToDisk, which does exactly
	// these two steps and ALSO opens a slow-task DIALOG on its material branch
	// (ObjectTools.cpp:5290-5292). See the file header: a modal freezes the editor and this bridge
	// together, and an unattended agent cannot dismiss it.
	void H_set_asset_thumbnail(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("width"), TEXT("height"),
			  TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"), TEXT("flushTextures"), TEXT("save") },
			TEXT("asset (aliases: assetPath, path), width, height, orbitPitch, orbitYaw, orbitZoom, ")
			TEXT("flushTextures, save"),
			{ { TEXT("texturePath"), TEXT("this endpoint sets the asset's own Content Browser icon and writes no texture asset — use write_thumbnail_texture for that") } }))
		{
			return;
		}

		FString ResolveError;
		UObject* Asset = ThumbResolveAsset(JStrAny(In, { TEXT("asset"), TEXT("assetPath"), TEXT("path") }), ResolveError);
		if (!Asset) { Fail(Out, ResolveError); return; }

		UPackage* Package = Asset->GetOutermost();
		if (IsCookedOrContainerPackage(Package))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' lives in a COOKED package: thumbnails are stripped at cook, so a cached thumbnail there would ")
				TEXT("never survive and saving over it is not a safe way to find out. Nothing was changed."),
				*Asset->GetPathName()));
			return;
		}

		int32 W, H, ReqW, ReqH;
		ThumbReadSize(In, W, H, ReqW, ReqH);

		FObjectThumbnail Thumb;
		double ElapsedMs = 0.0;
		if (!ThumbRenderForHandler(In, Out, Asset, W, H, Thumb, ElapsedMs)) { return; }

		// Without this the editor treats the entry as a legacy shared-type thumbnail and may discard
		// it on the next recycle pass (ObjectThumbnail.h:115-119).
		Thumb.SetCreatedAfterCustomThumbsEnabled();

		const FString FullName = Asset->GetFullName();
		if (ThumbnailTools::CacheThumbnail(FullName, &Thumb, Package) == nullptr)
		{
			Fail(Out, FString::Printf(
				TEXT("rendered %dx%d but CacheThumbnail refused to store it for '%s' — nothing was changed"),
				Thumb.GetImageWidth(), Thumb.GetImageHeight(), *FullName));
			return;
		}
		Package->MarkPackageDirty();

		// VERIFY AFTER WRITE — read the map back rather than trusting the return. GetThumbnailForObject
		// reads the in-memory package map only (ObjectTools.cpp:5446-5450); it does not re-render, so
		// this cannot accidentally confirm itself.
		const FObjectThumbnail* Stored = ThumbnailTools::GetThumbnailForObject(Asset);
		if (!Stored || Stored->GetImageWidth() != Thumb.GetImageWidth() || Stored->GetImageHeight() != Thumb.GetImageHeight())
		{
			Fail(Out, FString::Printf(
				TEXT("cached a thumbnail for '%s' but reading it back gave %s — the Content Browser icon was NOT set"),
				*Asset->GetPathName(),
				Stored ? TEXT("different dimensions") : TEXT("nothing")));
			return;
		}

		Out->SetStringField(TEXT("asset"), Asset->GetPathName());
		Out->SetStringField(TEXT("class"), Asset->GetClass()->GetName());
		Out->SetStringField(TEXT("renderer"), ThumbRendererClassName(Asset));
		ThumbWriteSizeFields(Out, Thumb.GetImageWidth(), Thumb.GetImageHeight(), ReqW, ReqH);
		Out->SetBoolField(TEXT("cached"), true);
		Out->SetBoolField(TEXT("packageDirty"), Package->IsDirty());

		if (JBool(In, TEXT("save"), false))
		{
			FString SavedTo, SaveError;
			if (!ThumbSavePackage(Package, SavedTo, SaveError)) { Fail(Out, SaveError); return; }
			Out->SetStringField(TEXT("savedTo"), SavedTo);
			Out->SetBoolField(TEXT("fileExists"), true);
			Out->SetBoolField(TEXT("saved"), true);
		}
		else
		{
			Out->SetBoolField(TEXT("saved"), false);
			Out->SetStringField(TEXT("saveNote"),
				TEXT("save:false — the thumbnail is cached in memory and the package is dirty. It is written to the .uasset ")
				TEXT("on the next save (save_package / save_dirty_packages), and lost if the editor closes without one."));
		}
		UE_LOG(LogMifBridge, Log, TEXT("set_asset_thumbnail: %s (%dx%d, %.0f ms)"),
			*Asset->GetPathName(), Thumb.GetImageWidth(), Thumb.GetImageHeight(), ElapsedMs);
	}
}
