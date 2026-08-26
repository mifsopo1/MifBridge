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
			"AnimGraph",         // UAnimGraphNode_* (derives from UK2Node) for add_anim_node
			"InputBlueprintNodes", // UK2Node_EnhancedInputAction (add_enhanced_input_action)
			"EnhancedInput",       // UInputAction / UInputMappingContext runtime types
    // UK2Node_* classes
			"GraphEditor",       // graph helpers
			"UMGEditor",         // UK2Node_CreateWidget (private header, see PrivateIncludePaths)
			"UMG",               // UUserWidget runtime class + Blueprint/UserWidget.h
			"MovieSceneTracks",  // UMovieScenePropertyTrack::SetPropertyNameAndPath is
			                     // MOVIESCENETRACKS_API. The 2D transform track/section classes
			                     // themselves are UMG_API, so only the BASE class needs this.
			"MovieScene",        // UMovieScene for add_widget_animation / list_widget_animations.
			                     // NOT redundant with "UMG" even though UMG lists MovieScene in its
			                     // PublicDependencyModuleNames: that propagates INCLUDE PATHS, so the
			                     // MovieScene headers compile without this line and then fail at LINK
			                     // with unresolved UMovieScene::SetPlaybackRange / GetPossessableCount
			                     // / GetPrivateStaticClass. Compiling is not linking.
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

		// ---- IK Rig: present in UE5, ABSENT IN UE4 ------------------------------------------
		// This plugin is also run against UE4, where the IKRig plugin does not exist. An
		// unconditional dependency would stop the WHOLE of MifBridge loading there, which is a far
		// worse outcome than losing five endpoints, so the plugin is detected rather than assumed.
		//
		// MIF_WITH_IKRIG is defined either way. MifBridgeIKRig.cpp compiles a real implementation when
		// it is 1 and an explicit "this engine build has no IK Rig" refusal when it is 0 - the
		// endpoints stay REGISTERED in both cases, because a missing endpoint tells a caller nothing
		// while a refusal that names the reason tells them everything. It also keeps the three-way
		// MIF_DECL/MIF_BIND/@mcp.tool parity intact on every engine.
		//
		// Both modules are needed and they are not interchangeable: UIKRigDefinition and UIKRetargeter
		// are /Script/IKRig (Runtime), while UIKRigController and UIKRetargeterController - which is
		// where all the authoring lives - are /Script/IKRigEditor (Editor). Verified against the live
		// editor with describe_class rather than inferred from the folder layout.
		string IKRigDescriptor = System.IO.Path.Combine(
			EngineDirectory, "Plugins", "Animation", "IKRig", "IKRig.uplugin");
		bool bHasIKRig = System.IO.File.Exists(IKRigDescriptor);
		PublicDefinitions.Add("MIF_WITH_IKRIG=" + (bHasIKRig ? "1" : "0"));
		if (bHasIKRig)
		{
			PrivateDependencyModuleNames.AddRange(new string[]
			{
				"IKRig",         // UIKRigDefinition, UIKRetargeter, FBoneChain, FRetargetDefinition
				"IKRigEditor"    // UIKRigController / UIKRetargeterController. BOTH are class-level
				                 // IKRIGEDITOR_API, so unlike AInstancedFoliageActor every member is
				                 // linkable and there is no per-member export trap here.
			});
		}
		else
		{
			System.Console.WriteLine(
				"MifBridge: IKRig plugin not found at " + IKRigDescriptor +
				" - the IK Rig endpoints will compile as unavailable-on-this-engine refusals.");
		}

		// UK2Node_CreateWidget.h is a UMGEditor PRIVATE header (Nodes/); the module
		// dependency isn't enough — the private folder must be on the include path.
		PrivateIncludePaths.Add(System.IO.Path.Combine(GetModuleDirectory("UMGEditor"), "Private"));

		// GeomFitUtils.h is an UnrealEd PRIVATE header, but its collision generators
		// (GenerateBoxAsSimpleCollision, GenerateKDopAsSimpleCollision, RefreshCollisionChange,
		// ...) are all UNREALED_API, so they LINK fine — only the header is out of reach.
		// remove_collision / add_simplified_collision need it (MifBridgeCollision.cpp); it also
		// carries the KDopDir* direction tables, which are defined in the header itself.
		PrivateIncludePaths.Add(System.IO.Path.Combine(GetModuleDirectory("UnrealEd"), "Private"));
	}
}
