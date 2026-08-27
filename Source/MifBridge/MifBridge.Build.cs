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
			"ImageWrapper",      // IImageWrapperModule / IImageWrapper — import_texture decodes
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
			"ApplicationCore"    // IPlatformInputDeviceMapper::Get().GetDefaultInputDevice() for
			                     // ui_scenario_activate's UGameViewportClient::InputKey call. Headers
			                     // resolved without this (transitively via Slate/Engine's own include
			                     // paths) and only LINK failed - the same InputCore trap above, one more
			                     // time: LNK2019 on IPlatformInputDeviceMapper::Get.
		});

		// ---- BREADTH BATCH, 2026-08-26: ENGINE modules -----------------------------------
		// Andre asked for every subsystem the competitor covers. Kept as a SEPARATE AddRange so the
		// list above stays as it was and this batch can be read, or reverted, on its own.
		//
		// These six ship with every build of the engine - verified by locating each one's .Build.cs
		// under Engine/Source in BOTH 5.3.2 and 5.7 - so an unconditional dependency is safe. The
		// fourteen PLUGIN modules are detected below instead, for exactly the reason the IK Rig block
		// records: an unconditional dependency on an absent plugin stops the WHOLE of MifBridge
		// loading, which is far worse than losing the endpoints it would have added.
		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"LevelSequence",     // ULevelSequence / ALevelSequenceActor - Sequencer reads and authoring
			"MovieSceneTools",   // sequencer editor helpers LevelSequence itself does not export
			"GameplayTags",      // FGameplayTag / UGameplayTagsManager - useful alone, required by GAS
			"MediaAssets",       // UMediaPlayer / UMediaSource
			"AudioExtensions",   // audio interfaces shared by MetaSounds and the base audio path
			"DataLayerEditor"    // UDataLayerEditorSubsystem - the WRITE half of Data Layers, which
			                     // list_data_layers could not reach. Reported as a needed dependency
			                     // earlier today, now authorised.
		});

		// ---- BREADTH BATCH: PLUGIN modules, detected not assumed --------------------------
		// Same contract as IK Rig below. For each: define MIF_WITH_<NAME> either way, and add the
		// dependency only when the plugin's descriptor is actually on disk. The handlers compile a real
		// implementation when the define is 1 and a named refusal when it is 0, so the endpoints stay
		// REGISTERED on every engine and the three-way MIF_DECL/MIF_BIND/@mcp.tool parity holds.
		//
		// Every descriptor path below was verified to exist in BOTH 5.3.2 and 5.7 before being written.
		// SEARCH FOR THE DESCRIPTOR, DO NOT HARDCODE ITS PATH. Plugins graduate out of Experimental
		// between engine versions: GeometryScripting, GameFeatures and ModularGameplay are all under
		// Plugins/Experimental in 5.3.2 and Plugins/Runtime in 5.7. A hardcoded path would have set
		// their define to 0 on 5.7 and silently dropped three whole families - a clean-looking build
		// with the endpoints quietly refusing, which is the exact failure this project keeps finding.
		// Searching by descriptor NAME survives the move in either direction.
		System.Func<string, string> FindPluginDescriptor = (PluginName) =>
		{
			string Root = System.IO.Path.Combine(EngineDirectory, "Plugins");
			if (!System.IO.Directory.Exists(Root)) { return null; }
			string[] Hits = System.IO.Directory.GetFiles(
				Root, PluginName + ".uplugin", System.IO.SearchOption.AllDirectories);
			return Hits.Length > 0 ? Hits[0] : null;
		};

		System.Action<string, string, string[]> AddPluginModules = (Define, PluginName, Modules) =>
		{
			string Found = FindPluginDescriptor(PluginName);
			bool bHas = !string.IsNullOrEmpty(Found);
			PublicDefinitions.Add(Define + "=" + (bHas ? "1" : "0"));
			if (bHas)
			{
				PrivateDependencyModuleNames.AddRange(Modules);
			}
			else
			{
				System.Console.WriteLine("MifBridge: plugin '" + PluginName +
					"' not found under Engine/Plugins - its endpoints will compile as " +
					"unavailable-on-this-engine refusals.");
			}
		};

		AddPluginModules("MIF_WITH_NIAGARA", "Niagara",
			new string[] { "Niagara", "NiagaraEditor" });
		// PCG MOVED between the two engines: Plugins/Experimental/PCG on 5.3, Plugins/PCG on 5.7 after
		// being promoted out of experimental. AddPluginModules searches AllDirectories, so neither path
		// is hardcoded - the same reason GameFeatures survived the identical move.
		// LIVE CODING is an ENGINE module, not a plugin, so AddPluginModules does not apply - it lives
		// at Source/Developer/Windows/LiveCoding and there is no .uplugin to find. That also means it is
		// WINDOWS ONLY.
		//
		// PrivateIncludePathModuleNames, NOT a link dependency: the code only ever reaches it through
		// FModuleManager::GetModulePtr, so it needs the header path and no symbols. A platform without
		// the module then gives a null pointer and a named refusal instead of a link error.
		bool bHasLiveCoding = System.IO.File.Exists(System.IO.Path.Combine(
			EngineDirectory, "Source", "Developer", "Windows", "LiveCoding", "Public",
			"ILiveCodingModule.h"));
		PublicDefinitions.Add("MIF_WITH_LIVECODING=" + (bHasLiveCoding ? "1" : "0"));
		if (bHasLiveCoding)
		{
			PrivateIncludePathModuleNames.Add("LiveCoding");
		}

		AddPluginModules("MIF_WITH_STATETREE", "StateTree",
			new string[] { "StateTreeModule" });

		AddPluginModules("MIF_WITH_PCG", "PCG",
			new string[] { "PCG" });
		AddPluginModules("MIF_WITH_GAS", "GameplayAbilities",
			new string[] { "GameplayAbilities" });
		AddPluginModules("MIF_WITH_GEOMETRYSCRIPT", "GeometryScripting",
			new string[] { "GeometryScriptingCore", "GeometryScriptingEditor" });
		AddPluginModules("MIF_WITH_GAMEFEATURES", "GameFeatures",
			new string[] { "GameFeatures" });
		AddPluginModules("MIF_WITH_MODULARGAMEPLAY", "ModularGameplay",
			new string[] { "ModularGameplay" });
		AddPluginModules("MIF_WITH_MVVM", "ModelViewViewModel",
			new string[] { "ModelViewViewModel" });
		AddPluginModules("MIF_WITH_WATER", "Water",
			new string[] { "Water" });
		AddPluginModules("MIF_WITH_VEHICLES", "ChaosVehiclesPlugin",
			new string[] { "ChaosVehicles" });
		AddPluginModules("MIF_WITH_MASSENTITY", "MassEntity",
			new string[] { "MassEntity" });
		AddPluginModules("MIF_WITH_LIVELINK", "LiveLink",
			new string[] { "LiveLink" });
		AddPluginModules("MIF_WITH_LEVELSNAPSHOTS", "LevelSnapshots",
			new string[] { "LevelSnapshots" });
		AddPluginModules("MIF_WITH_METASOUND", "Metasound",
			new string[] { "MetasoundEngine" });
		// ABSENT FROM 5.3.2 ENTIRELY - MetaHuman Creator's plugin is UE 5.6+ only, so on the DDS2
		// fork this is always 0 and the endpoints compile as refusals. Present on stock 5.7.4.
		// "EnabledByDefault": false in MetaHumanCharacter.uplugin - that governs whether the PLUGIN
		// MANAGER auto-loads it for a project that never references it, not whether a build-time
		// module dependency links and loads it. MifBridge takes a hard PrivateDependencyModuleNames
		// dependency on MetaHumanCharacterEditor, which makes the OS loader pull in its DLL as an
		// import of MifBridge.dll regardless of the host .uproject's enabled-plugins list - the same
		// mechanism every other AddPluginModules entry above already relies on.
		AddPluginModules("MIF_WITH_METAHUMAN", "MetaHumanCharacter",
			new string[] { "MetaHumanCharacterEditor", "MetaHumanCharacter" });

		// ---- Blueprint reconstructor: present ONLY in the DDS2 ENGINE FORK ------------------
		// CompiledBlueprintReconstructor.h lives in Engine/Source/Editor/Kismet/Public and does NOT
		// ship with any stock Unreal, of any version. It is a fork-local addition to D:/UE532, which
		// is why create_editable_child can headlessly mint an editable copy of a COOKED blueprint -
		// something stock UE has no API for at all.
		//
		// This is a DIFFERENT KIND of absence from the ones around it, and the distinction matters
		// when reading the refusal. Every other MIF_WITH_* here guards an optional PLUGIN: enable it,
		// or use an engine that ships it, and the endpoint comes back. This one cannot be enabled on
		// a stock engine at any version - there is no plugin to install and no newer release that
		// adds it. The refusal says so, rather than implying an upgrade would help.
		//
		// Found by a second session compiling the synced plugin against stock UE 5.7 in Curfew. From
		// a 5.3 machine this file looked like ordinary editor code, because on THIS engine the header
		// is exactly where an engine header should be. Nothing about the include hints that it is not
		// stock; only building somewhere else reveals it.
		string ReconstructorHeader = System.IO.Path.Combine(
			EngineDirectory, "Source", "Editor", "Kismet", "Public", "CompiledBlueprintReconstructor.h");
		bool bHasReconstructor = System.IO.File.Exists(ReconstructorHeader);
		PublicDefinitions.Add("MIF_WITH_RECONSTRUCTOR=" + (bHasReconstructor ? "1" : "0"));
		if (!bHasReconstructor)
		{
			System.Console.WriteLine(
				"MifBridge: no CompiledBlueprintReconstructor.h at " + ReconstructorHeader +
				" - this is a stock engine, so create_editable_child will compile as a refusal " +
				"naming the fork requirement.");
		}
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
