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
			"Sockets"            // FInternetAddr for loopback peer enforcement
		});

		// UK2Node_CreateWidget.h is a UMGEditor PRIVATE header (Nodes/); the module
		// dependency isn't enough — the private folder must be on the include path.
		PrivateIncludePaths.Add(System.IO.Path.Combine(GetModuleDirectory("UMGEditor"), "Private"));
	}
}
