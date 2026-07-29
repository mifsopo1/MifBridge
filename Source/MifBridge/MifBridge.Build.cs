// MifBridge — editor-only localhost HTTP bridge for programmatic Blueprint graph edits.
// Editor module only; never a runtime dependency of any cooked mod.

using UnrealBuildTool;

public class MifBridge : ModuleRules
{
	public MifBridge(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine"
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"UnrealEd",          // FKismetEditorUtilities, FBlueprintEditorUtils, editor subsystems
			"MaterialEditor",    // UMaterialEditingLibrary (class-level MATERIALEDITOR_API) — material
			                     // graph authoring, Batch D. First new module dep since the audit began
			                     // (docs/audit/work/D_materials_rendering.md: editor-only, engine-core,
			                     // no plugin gating; must never leak into a runtime module).
			"RHI",               // GMaxRHIShaderPlatform (RHI_API, RHIShaderPlatform.h:86). Not used
			                     // directly: it is the DEFAULT ARGUMENT of FMaterialUpdateContext's
			                     // constructor (MaterialShared.h:2817), and default arguments are
			                     // evaluated in the CALLER's translation unit — so recompile_material
			                     // pulls the symbol in merely by writing `FMaterialUpdateContext Ctx;`.
			                     // Anticipated by docs/audit/work/I_diagnostics.md (get_perf_stats).
			"BlueprintGraph",
			"InputBlueprintNodes", // UK2Node_EnhancedInputAction (add_enhanced_input_action)
			"EnhancedInput",       // UInputAction / UInputMappingContext runtime types
    // UK2Node_* classes
			"GraphEditor",       // graph helpers
			"UMGEditor",         // UK2Node_CreateWidget (private header, see PrivateIncludePaths)
			"UMG",               // UUserWidget runtime class + Blueprint/UserWidget.h
			"Kismet",            // blueprint editor helpers (kept for safety)
			"KismetCompiler",    // compile results struct
			"HTTPServer",        // FHttpServerModule / IHttpRouter
			"Json",
			"JsonUtilities",
			"Landscape",         // ALandscape create/sculpt/paint + ALandscapeProxy diagnostics
			"Foliage",           // LandscapeEdit.h includes InstancedFoliageActor.h from this module
			"VirtualTexturingEditor", // RuntimeVirtualTexture::SetBounds for bind_landscape_rvt
			"RenderCore",        // FShaderMapContent::GetNumShaders for the landscape shader-map diagnostic
			"Renderer",          // FPrimitiveSceneInfo::StaticMeshCommandInfos for diagnose_landscape_draws
			"AssetRegistry",     // find/open blueprints by path
			"AssetTools",        // headless rename/duplicate (IAssetTools::RenameAssets/DuplicateAsset)
			"NavigationSystem",  // ANavMeshBoundsVolume + UNavigationSystemV1::Build (navigation endpoints)
			"AIModule",          // UAIBlueprintHelperLibrary::SimpleMoveToLocation (patrol / move_actor_to)
			"EditorSubsystem",
			"ToolMenus",         // Start/Stop menu toggle
			"Slate",
			"SlateCore",
			"Projects",
			"Sockets",           // FInternetAddr for loopback peer enforcement
			"InputCore",         // FKey / EKeys — send_editor_key and list_editor_commands report and
			                     // synthesise key chords (Batch O). Runtime module, no plugin gating.
			                     // Slate pulls InputCore in transitively for its own use, which is why
			                     // the HEADERS resolved and only the LINK failed (LNK2019 on
			                     // FKey::IsValid/IsModifierKey/ToString and EKeys::GetAllKeys) — a
			                     // reminder that a compiling include is not a linked module.
			"ImageWrapper"       // IImageWrapperModule / IImageWrapper — import_texture decodes
			                     // PNG/JPEG/BMP/TGA into FTextureSource for BOTH its file-path and its
			                     // base64 ingest modes. NOT reachable transitively: Engine lists
			                     // ImageWrapper under PrivateIncludePathModuleNames, which puts the
			                     // headers on the include path for Engine's OWN translation units and
			                     // exports nothing to ours — the same "a compiling include is not a
			                     // linked module" trap as InputCore above, one line up.
			                     //
			                     // ImageCore needs no entry of its own — ImageWrapper already brings it
			                     // in publicly (ImageWrapper.Build.cs:34) and IImageWrapper.h:9 includes
			                     // ImageCore.h, so it is on the include path and linked transitively.
			                     // (An earlier revision of this comment claimed ImageCore was being
			                     // deliberately avoided by not calling FImageUtils::SaveImageByExtension.
			                     // The avoidance is real — MifBridgeThumbnail.cpp uses
			                     // PNGCompressImageArray + FFileHelper::SaveArrayToFile instead — but the
			                     // stated reason was wrong, because ImageCore arrives regardless.)
		});

		// UK2Node_CreateWidget.h is a UMGEditor PRIVATE header (Nodes/); the module
		// dependency isn't enough — the private folder must be on the include path.
		PrivateIncludePaths.Add(System.IO.Path.Combine(GetModuleDirectory("UMGEditor"), "Private"));
	}
}
