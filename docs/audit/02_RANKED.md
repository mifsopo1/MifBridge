# MifBridge endpoint audit — ranked and batched

_Generated from the verified index (work/index/*.rows.json) on 2026-07-26. Rank = U+E+R (each 1–5)._
_Full specs: the axis files under [work/](work/); risks: [03_GAPS_AND_RISKS.md](03_GAPS_AND_RISKS.md)._

## Tier ranking

### Tier 0 — closes a known gap (36)

| # | Endpoint | Score | Axis | New deps | Purpose |
|---|---|---|---|---|---|
| 1 | `kr_disassemble_function` | 14 (5/4/5) | [K1](work/K1_reconstructor_toolkit.md) | <provider:MifKismetReconstructor> | Full paginated JSON statement stream for ONE function of a cooked BPGC — what an agent reads before… |
| 2 | `kr_dump_blueprint` | 14 (5/4/5) | [K1](work/K1_reconstructor_toolkit.md) | <provider:MifKismetReconstructor> | mif.kr.DumpBP as inline JSON: per-function bytecode inventory + opcode histogram for a cooked BPGC… |
| 3 | `list_components` *(behaviour change)* | 14 (4/5/5) | [C](work/C_blueprints_graphs.md) | — | Include inherited and native components (source field) so agents can discover legal parents |
| 4 | `list_widget_tree` | 14 (5/4/5) | [G2](work/G2_sequencer_umg_input.md) | — | One-call enumeration of a Widget Blueprint's whole widget hierarchy — closes roadmap gap 'UMG: no o… |
| 5 | `pie_status` *(behaviour change)* | 14 (5/4/5) | [Q](work/Q_gap_rootcauses.md) | — | Truthful PIE state across full lifecycle (queued/starting/running/simulating/travelling/stopping/st… |
| 6 | `apply_spline_to_landscape` | 13 (5/4/4) | [F](work/F_world_level.md) | — | Deform (and optionally paint) the landscape along any existing USplineComponent — road/river bed fr… |
| 7 | `connect_pins` *(behaviour change)* | 13 (4/5/4) | [C](work/C_blueprints_graphs.md) | — | ConnectPinsChecked must use the graph's own schema instead of the hardcoded K2 CDO |
| 8 | `kr_reconstruct_status` | 13 (4/4/5) | [K2](work/K2_reconstructor_pipeline.md) | provider:MifKismetReconstructor | Poll the single kr job slot: phase, per-function/event done-counts, node counts, compile numbers, a… |
| 9 | `add_component` *(behaviour change)* | 12 (5/4/3) | [C](work/C_blueprints_graphs.md) | — | parentName resolves inherited-SCS and native parents, not just own SCS |
| 10 | `add_create_delegate` | 12 (4/4/4) | [C](work/C_blueprints_graphs.md) | — | Spawn UK2Node_CreateDelegate wired to a target function so any existing function can bind to a disp… |
| 11 | `create_macro` | 12 (4/4/4) | [C](work/C_blueprints_graphs.md) | — | Create a user macro graph (entry/exit tunnels) so add_macro_instance can place user-authored macros |
| 12 | `format_graph` | 12 (5/3/4) | [P1](work/P1_graph_layout.md) | — | Deterministic headless Sugiyama-lite layered-DAG layout of a K2 graph — fixes '40 spawned nodes hea… |
| 13 | `list_transactions` | 12 (5/2/5) | [A](work/A_editor_core.md) | — | Introspect the undo buffer (indices, titles, contexts, object counts, sizes) so an agent can see wh… |
| 14 | `list_variables` *(behaviour change)* | 12 (4/4/4) | [C](work/C_blueprints_graphs.md) | — | Add scope=local (+function) to list_variables, rename_variable, remove_variable, set_variable_defau… |
| 15 | `pie_move_status` | 12 (5/2/5) | [G1](work/G1_ai_navigation.md) | — | The poll half of pie_move_pawn — numeric progress (status, leg index, remaining path length/cost) o… |
| 16 | `set_variable_type` | 12 (5/4/3) | [C](work/C_blueprints_graphs.md) | — | Retype a member or local variable in place instead of remove+add which drops get/set nodes, flags a… |
| 17 | `snap_actors_to_ground` *(behaviour change)* | 12 (4/4/4) | [Q](work/Q_gap_rootcauses.md) | — | Penetrating ECR_Overlap multi-trace so landscape is reachable through blocking geometry; per-actor… |
| 18 | `find_path` | 11 (5/1/5) | [G1](work/G1_ai_navigation.md) | — | Synchronous point-to-point pathfinding returning path points + length + partial flag — the numeric… |
| 19 | `pie_move_pawn` | 11 (5/3/3) | [G1](work/G1_ai_navigation.md) | — | Issue a tracked, tunable nav move (single dest or multi-point route) to an AI pawn in PIE — the wal… |
| 20 | `recompile_material` | 11 (5/3/3) | [D](work/D_materials_rendering.md) | MaterialEditor | Apply graph/parameter edits, dispatching on asset class (Material/Function/MIC) — without it none o… |
| 21 | `reparent_widget` | 11 (5/3/3) | [G2](work/G2_sequencer_umg_input.md) | — | Move an existing widget to a new parent panel and/or child index WITHOUT destroying it. |
| 22 | `add_material_expression` | 10 (5/3/2) | [D](work/D_materials_rendering.md) | MaterialEditor | Add a node to a material or material-function graph — the atom of the Tier-0 material-graph-authori… |
| 23 | `add_node_by_class` | 10 (5/2/3) | [C](work/C_blueprints_graphs.md) | — | Spawn ANY concrete UK2Node subclass by class path via PlaceAndInit with a reflection-applied init m… |
| 24 | `kr_reconstruct_request` | 10 (5/2/3) | [K2](work/K2_reconstructor_pipeline.md) | provider:MifKismetReconstructor | Start an async job decompiling a cooked BP's bytecode into editable K2 graphs (whole copy or single… |
| 25 | `read_curve` | 10 (4/1/5) | [H](work/H_data.md) | — | Read keys AND numerically evaluate a curve at N sample points — the verification half of set_curve_… |
| 26 | `undo_transactions` | 10 (5/2/3) | [A](work/A_editor_core.md) | — | Undo the last N transactions (or down to a queue index) — the rollback half of the gap. |
| 27 | `connect_material_expressions` | 9 (5/2/2) | [D](work/D_materials_rendering.md) | MaterialEditor | Wire expression output to expression input inside a material/function graph. |
| 28 | `connect_material_property` | 9 (5/2/2) | [D](work/D_materials_rendering.md) | MaterialEditor | Wire an expression output into a material OUTPUT pin (BaseColor, Roughness…) — without this the gra… |
| 29 | `create_material` | 9 (5/2/2) | [D](work/D_materials_rendering.md) | — | Mint a new UMaterial asset (master material) — the missing half of the Tier-0 material-authoring ga… |
| 30 | `validate_level_materials` | 9 (5/3/1) | [D](work/D_materials_rendering.md) | — | Read-only structured report over all mesh components: null slots, default-material fallbacks, incom… |
| 31 | `list_material_expressions` | 8 (5/2/1) | [D](work/D_materials_rendering.md) | MaterialEditor | Read-back for the whole authoring loop: enumerate graph nodes, positions, key parameters, connectio… |
| 32 | `redo_transactions` | 8 (4/1/3) | [A](work/A_editor_core.md) | — | Redo the last N undone transactions — lets an agent A/B a change numerically. |
| 33 | `set_actor_render_overrides` | 8 (4/2/2) | [D](work/D_materials_rendering.md) | — | Batch per-actor cull/LOD control — force LODs, set draw distances, exclude from HLOD — with render-… |
| 34 | `set_curve_keys` | 8 (5/2/1) | [H](work/H_data.md) | — | Structured key editing on any FRichCurve-bearing asset — closes the element-level-addressing gap fo… |
| 35 | `get_actor_render_info` | 6 (4/1/1) | [D](work/D_materials_rendering.md) | — | Read-back pair for set_actor_render_overrides: per-component LOD forcing, draw distances, HLOD flag… |
| 36 | `shader_compile_status` | 6 (4/1/1) | [D](work/D_materials_rendering.md) | — | THE poll endpoint for every material mutation on this axis and for editor-wide shader churn after l… |

### Tier 1 — high leverage, low risk (132)

| # | Endpoint | Score | Axis | New deps | Purpose |
|---|---|---|---|---|---|
| 1 | `disassemble_function` | 14 (5/4/5) | [C](work/C_blueprints_graphs.md) | ScriptDisassembler | Read-only Kismet bytecode disassembly of any UFunction, including cooked UBlueprintGeneratedClass f… |
| 2 | `list_native_classes` | 14 (4/5/5) | [J](work/J_dds2_project.md) | — | Enumerate the native class surface of a /Script module — today describe_class needs a name the agen… |
| 3 | `list_sublevels` | 14 (4/5/5) | [F](work/F_world_level.md) | — | Enumerate every streaming level of the open world with load/visibility state; also reports whether… |
| 4 | `list_ue4ss_mods` | 14 (4/5/5) | [J](work/J_dds2_project.md) | — | One call answering what mods are installed/enabled/deployed and whether a deploy actually landed, a… |
| 5 | `add_sublevel` | 13 (5/4/4) | [F](work/F_world_level.md) | — | Add an existing level package to the open world as a streaming sublevel with optional transform. |
| 6 | `describe_anim_class` | 13 (4/4/5) | [C](work/C_blueprints_graphs.md) | — | Structured read of a cooked or uncooked AnimBP generated class: baked state machines and anim-node… |
| 7 | `describe_sequence` | 13 (5/3/5) | [G2](work/G2_sequencer_umg_input.md) | MovieScene | One-call structured dump of any UMovieSceneSequence: ranges, rates, bindings with GUIDs, tracks, se… |
| 8 | `export_graph_text` | 13 (3/5/5) | [P1](work/P1_graph_layout.md) | — | Lossless T3D (clipboard-format) export of a graph or node subset — exact pin defaults, positions, c… |
| 9 | `export_heightmap` | 13 (4/4/5) | [F](work/F_world_level.md) | ImageWrapper | Dump landscape height data (whole extent or window) to a 16-bit file for numeric inspection/diff. |
| 10 | `get_game_feature_state` | 13 (4/4/5) | [P3](work/P3_sessions_misc_plugins.md) | GameFeatures | Enumerate every game-feature plugin with exact state-machine state (incl. error states) — machine-r… |
| 11 | `get_perf_stats` | 13 (4/4/5) | [I](work/I_diagnostics.md) | RHI | One read-only call returning numeric frame-health: FPS, per-thread ms, draw calls, object counts, G… |
| 12 | `get_properties_bulk` | 13 (4/4/5) | [I](work/I_diagnostics.md) | — | Read up to 200 object/property pairs in one call — the watch-list primitive for PIE observation loo… |
| 13 | `kr_list_cooked_blueprints` | 13 (3/5/5) | [K1](work/K1_reconstructor_toolkit.md) | — | Registry census of cooked Blueprints (PKG_Cooked-filtered, package-deduped, loaded flag) so an agen… |
| 14 | `list_automation_tests` | 13 (4/4/5) | [I](work/I_diagnostics.md) | — | Enumerate every registered automation test with flags and source locations — prerequisite for runni… |
| 15 | `list_foliage` | 13 (4/4/5) | [F](work/F_world_level.md) | — | Enumerate foliage types present in the world with instance counts — the reader that makes foliage m… |
| 16 | `list_input_mappings` | 13 (3/5/5) | [G2](work/G2_sequencer_umg_input.md) | EnhancedInput | Read back an InputMappingContext: every (action, key, modifiers, triggers) row — verification twin… |
| 17 | `list_node_classes` | 13 (3/5/5) | [C](work/C_blueprints_graphs.md) | — | Enumerate every loaded spawnable UK2Node subclass (name, module, abstract, denylist status, dedicat… |
| 18 | `message_log_read` | 13 (4/4/5) | [I](work/I_diagnostics.md) | MessageLog | Structured read of editor Message Log listings (MapCheck, PIE, BlueprintLog, ...) — where map_check… |
| 19 | `pie_resolve_path` | 13 (4/4/5) | [I](work/I_diagnostics.md) | — | Convert an editor object path to its live PIE counterpart (and back) deterministically, with option… |
| 20 | `read_modloader_log` *(behaviour change)* | 13 (3/5/5) | [J](work/J_dds2_project.md) | — | Structured events instead of raw lines from UE4SS.log — agents currently regex a text blob. |
| 21 | `set_current_sublevel` | 13 (4/5/4) | [F](work/F_world_level.md) | — | Route all subsequent spawn output into a chosen sublevel; without it everything lands in the persis… |
| 22 | `verify_pak_contents` | 13 (4/4/5) | [J](work/J_dds2_project.md) | — | Enumerate what is actually inside a .utoc/.ucas (or retoc trio) before/after deploying a mod, inste… |
| 23 | `apply_landscape_splines` | 12 (4/4/4) | [F](work/F_world_level.md) | — | Rasterise all landscape splines into heightmap/weightmaps (the Deform Landscape to Splines button). |
| 24 | `export_weightmap` | 12 (3/4/5) | [F](work/F_world_level.md) | ImageWrapper | Dump a paint layer's weights (0-255) to an 8-bit PNG — numerically verifies paint/spline corridors. |
| 25 | `get_asset_dependencies` | 12 (5/2/5) | [B](work/B_assets_registry.md) | — | On-disk dependency list of a package with hard/soft/game/build edge classification — graph data fin… |
| 26 | `get_asset_referencers` | 12 (5/2/5) | [B](work/B_assets_registry.md) | — | Reverse edge: what references this asset, incl. which loose project assets reference a base-game co… |
| 27 | `get_class_hierarchy` | 12 (5/2/5) | [B](work/B_assets_registry.md) | — | Registry-level class inheritance: all classes derived from X (incl. unloaded cooked BPGCs) and ance… |
| 28 | `list_landscape_splines` | 12 (3/4/5) | [F](work/F_world_level.md) | — | Read back every landscape-spline control point and segment — verification read for create_landscape… |
| 29 | `log_tail` | 12 (5/3/4) | [I](work/I_diagnostics.md) | — | Incremental structured tail of the editor process log via a GLog ring-buffer device with monotonic… |
| 30 | `set_sublevel_visibility` | 12 (3/5/4) | [F](work/F_world_level.md) | — | Show/hide a sublevel in the editor viewport and set runtime loaded/visible flags; lighting-scenario… |
| 31 | `add_anim_state` | 11 (5/3/3) | [C](work/C_blueprints_graphs.md) | — | Add a state to a state machine graph; returns the state's bound animation graph id for existing gra… |
| 32 | `add_anim_state_machine` | 11 (5/3/3) | [C](work/C_blueprints_graphs.md) | — | Create a state machine node inside an AnimBP's AnimGraph — the entry point of AnimBP authoring, cur… |
| 33 | `add_anim_transition` | 11 (5/3/3) | [C](work/C_blueprints_graphs.md) | — | Create a transition between two states, returning the transition node and its K2 rule graph id |
| 34 | `call_object_function` | 11 (5/3/3) | [P2](work/P2_world_vehicle_plugins.md) | — | Invoke a named UFUNCTION on any objectPath via reflection — the only lane that executes cooked-BP b… |
| 35 | `create_asset` | 11 (5/3/3) | [B](work/B_assets_registry.md) | — | Mint non-Blueprint assets (DataTable, curves, DataAssets, StringTable, MPC, PhysicalMaterial, AnimB… |
| 36 | `find_assets_by_tag` | 11 (4/2/5) | [B](work/B_assets_registry.md) | — | Query registry by asset-registry tag/value pairs (e.g. DataTables by RowStructure) — axis find_asse… |
| 37 | `fit_comment_to_nodes` | 11 (3/4/4) | [P1](work/P1_graph_layout.md) | AutoSizeComments | Compute union rect of a node set and set comment bounds + membership so agent-made comments behave… |
| 38 | `get_message_log` | 11 (4/2/5) | [B](work/B_assets_registry.md) | MessageLog | Read any FMessageLog channel (MapCheck, AssetCheck, LoadErrors, BlueprintLog, PIE, ...) as structur… |
| 39 | `import_asset` | 11 (5/3/3) | [B](work/B_assets_registry.md) | AudioEditor | Import a disk file (PNG/TGA to Texture2D, FBX to meshes, WAV to SoundWave) as a project asset. |
| 40 | `input_map_key` | 11 (4/4/3) | [G2](work/G2_sequencer_umg_input.md) | EnhancedInput | Append a key-to-action mapping to an InputMappingContext — the one structural edit set_property can… |
| 41 | `kr_list_events` | 11 (4/3/4) | [K1](work/K1_reconstructor_toolkit.md) | <provider:MifKismetReconstructor> | Event census of a cooked BP: every thunk with kind, recovered ubergraph entry offset, param count,… |
| 42 | `mesh_op` | 11 (5/4/2) | [E](work/E_geometry_meshes.md) | GeometryScriptingCore, plugin:GeometryScripting | Single dispatcher applying one allowlisted GeometryScript operation to a named mesh — ~80 engine fu… |
| 43 | `navmesh_tile_info` | 11 (4/2/5) | [G1](work/G1_ai_navigation.md) | — | Per-tile poly counts and bounds — turns nav_status's single tiles number into a spatial map exposin… |
| 44 | `paint_foliage` | 11 (4/3/4) | [F](work/F_world_level.md) | — | Add instances to the real foliage system (AInstancedFoliageActor + FFoliageInfo) for a UFoliageType. |
| 45 | `add_async_action` | 10 (4/3/3) | [C](work/C_blueprints_graphs.md) | — | Spawn UK2Node_AsyncAction family (latent proxy nodes for UBlueprintAsyncActionBase factories), curr… |
| 46 | `add_nav_modifier_volume` | 10 (4/2/4) | [G1](work/G1_ai_navigation.md) | — | Place a volume stamping a UNavArea (cost/Null/Obstacle) onto the navmesh region it overlaps — 'neve… |
| 47 | `commit_dynamic_mesh` | 10 (5/3/2) | [E](work/E_geometry_meshes.md) | GeometryScriptingCore, plugin:GeometryScripting | Write a pooled mesh's geometry into a UStaticMesh asset LOD — bridge from procedural compute to rea… |
| 48 | `copy_from_static_mesh` | 10 (5/3/2) | [E](work/E_geometry_meshes.md) | GeometryScriptingCore, plugin:GeometryScripting | Load an existing StaticMesh (or SkeletalMesh) LOD into a pooled dynamic mesh for measurement or der… |
| 49 | `create_input_action` | 10 (3/4/3) | [G2](work/G2_sequencer_umg_input.md) | EnhancedInput | Mint a UInputAction asset — closes the roadmap gap 'no non-Blueprint asset creation (no InputAction… |
| 50 | `create_input_mapping_context` | 10 (3/4/3) | [G2](work/G2_sequencer_umg_input.md) | EnhancedInput | Mint a UInputMappingContext asset — shared implementation with create_input_action, same minting pa… |
| 51 | `create_landscape_spline` | 10 (5/2/3) | [F](work/F_world_level.md) | — | Author a real landscape spline (control points + segments) from a world-space point list — the nati… |
| 52 | `create_sublevel` | 10 (4/3/3) | [F](work/F_world_level.md) | — | Create a brand-new empty streaming level in the open world and save it to a package path in one cal… |
| 53 | `create_widget_animation` | 10 (4/3/3) | [G2](work/G2_sequencer_umg_input.md) | MovieScene | Add a UWidgetAnimation to a Widget Blueprint — the container that makes UMG animation authoring pos… |
| 54 | `export_asset` | 10 (4/2/4) | [B](work/B_assets_registry.md) | — | Write an asset to a disk file (texture to PNG, mesh to OBJ/FBX, sound to WAV, object/level to T3D)… |
| 55 | `get_asset_tags` | 10 (4/1/5) | [B](work/B_assets_registry.md) | — | Dump the full asset-registry tag/value map for one asset without loading it. |
| 56 | `get_config_value` | 10 (4/1/5) | [H](work/H_data.md) | — | Structured read of any .ini value or whole section (merged runtime view or specific Default*.ini fi… |
| 57 | `get_cvar` | 10 (4/1/5) | [A](work/A_editor_core.md) | — | Structured READ of a console variable — value, type, flags, help — closing the SET-without-GET asym… |
| 58 | `get_viewport_state` | 10 (3/2/5) | [A](work/A_editor_core.md) | — | Read the active level viewport's render configuration — view mode, game view, realtime, size, show-… |
| 59 | `import_heightmap` | 10 (4/3/3) | [F](work/F_world_level.md) | ImageWrapper | Push a 16-bit heightmap file onto an existing landscape region — round-trip partner of export_heigh… |
| 60 | `kr_analyze_ubergraph` | 10 (3/3/4) | [K1](work/K1_reconstructor_toolkit.md) | <provider:MifKismetReconstructor> | Per-BP ubergraph slice stats as JSON: prologue shape, per-event reachability, shared/unreached coun… |
| 61 | `kr_verify_fidelity` | 10 (5/2/3) | [K2](work/K2_reconstructor_pipeline.md) | provider:MifKismetReconstructor | Reconstruct+compile a throwaway transient child of a cooked BP, diff recompiled vs cooked bytecode,… |
| 62 | `list_cvars` | 10 (3/2/5) | [A](work/A_editor_core.md) | — | Enumerate console variables/commands by prefix or substring with values and flags. |
| 63 | `list_developer_settings` | 10 (4/1/5) | [A](work/A_editor_core.md) | DeveloperSettings | Enumerate every UDeveloperSettings subclass (class path, config, category, section) as an index for… |
| 64 | `list_dirty_packages` | 10 (4/1/5) | [B](work/B_assets_registry.md) | — | Enumerate unsaved (dirty) content and world packages before save_package / trigger_cook / PIE. |
| 65 | `map_check` | 10 (4/2/4) | [B](work/B_assets_registry.md) | — | Run the editor's Map Check over the loaded level and return the message list structurally. |
| 66 | `remove_foliage_instances` | 10 (3/3/4) | [F](work/F_world_level.md) | — | Delete foliage instances by type and/or area — clearing a building footprint before placement. |
| 67 | `remove_graph` | 10 (3/4/3) | [C](work/C_blueprints_graphs.md) | — | Delete a macro or collapsed/extra graph; remove_function only searches FunctionGraphs so macros are… |
| 68 | `remove_sublevel` | 10 (3/4/3) | [F](work/F_world_level.md) | — | Detach a streaming sublevel from the open world (asset stays on disk) — inverse of add_sublevel. |
| 69 | `save_dirty_packages` | 10 (4/3/3) | [A](work/A_editor_core.md) | — | One-call checkout-free, prompt-free save of everything dirty. |
| 70 | `sequence_add_section` | 10 (4/3/3) | [G2](work/G2_sequencer_umg_input.md) | MovieScene | Give a track an actual section with a frame range — tracks evaluate nothing without one. |
| 71 | `sequence_add_track` | 10 (4/3/3) | [G2](work/G2_sequencer_umg_input.md) | MovieScene, MovieSceneTracks | Add a typed track (transform, float/bool property, visibility, UMG 2DTransform/Margin) to a binding… |
| 72 | `sequence_bind_actor` | 10 (4/3/3) | [G2](work/G2_sequencer_umg_input.md) | MovieScene, LevelSequence | Bind a placed level actor into a sequence as a possessable or spawnable, returning the binding GUID… |
| 73 | `sequence_set_keys` | 10 (5/2/3) | [G2](work/G2_sequencer_umg_input.md) | MovieScene | Batch-write keys into a section's channels — transform XYZ, float/double properties, bools — turnin… |
| 74 | `set_view_mode` | 10 (4/2/4) | [A](work/A_editor_core.md) | — | Switch viewport view mode, toggle game view/realtime and individual show flags — makes capture_came… |
| 75 | `set_water_body_profile` | 10 (4/3/3) | [F](work/F_world_level.md) | Water | Set per-point water parameters (depth, river width, flow velocity) living in UWaterSplineMetadata c… |
| 76 | `validate_assets` | 10 (4/2/4) | [B](work/B_assets_registry.md) | DataValidation | Run the engine data-validation pass over chosen assets, returning structured per-asset errors/warni… |
| 77 | `widget_animation_bind` | 10 (4/3/3) | [G2](work/G2_sequencer_umg_input.md) | MovieScene | Bind a named widget (or widget slot) into a widget animation and return the possessable GUID. |
| 78 | `create_datatable` | 9 (5/2/2) | [H](work/H_data.md) | — | Create a new UDataTable asset with a chosen RowStruct — rows can be written today but no table can… |
| 79 | `create_level_sequence` | 9 (5/2/2) | [G2](work/G2_sequencer_umg_input.md) | LevelSequence, MovieScene | Mint a new, saveable ULevelSequence asset at a given package path — currently no cinematic can be a… |
| 80 | `create_niagara_system` | 9 (4/3/2) | [G3](work/G3_niagara_audio_physics.md) | Niagara, NiagaraEditor | Create a new, immediately-usable NiagaraSystem asset, optionally seeded from an emitter asset, with… |
| 81 | `create_static_mesh_asset` | 9 (5/2/2) | [E](work/E_geometry_meshes.md) | GeometryScriptingEditor, plugin:GeometryScripting | Create a brand-new UStaticMesh asset from a pooled mesh — the mod-content authoring path, no cooked… |
| 82 | `export_datatable_csv` | 9 (3/1/5) | [H](work/H_data.md) | — | Round-trip tables through the CSV format designers/Excel use — read_datatable only emits JSON. |
| 83 | `list_actor_folders` | 9 (3/1/5) | [A](work/A_editor_core.md) | — | Enumerate the World Outliner folder tree with per-folder actor counts. |
| 84 | `list_blackboard_keys` | 9 (3/1/5) | [G1](work/G1_ai_navigation.md) | — | Enumerate a UBlackboardData asset's keys (own + inherited) with types — required reading before BT-… |
| 85 | `list_content_paths` | 9 (3/1/5) | [B](work/B_assets_registry.md) | — | Enumerate the content folder tree (registry-cached paths) — answers 'what folders exist under /Game… |
| 86 | `list_primary_assets` | 9 (3/1/5) | [H](work/H_data.md) | — | Read-only dump of UAssetManager primary asset types and ids — shows how DDS2 organizes scannable co… |
| 87 | `list_string_table_entries` | 9 (3/1/5) | [H](work/H_data.md) | — | Dump all key/source-string pairs (+namespace) of a string table — works on cooked base-game text to… |
| 88 | `project_to_navmesh` | 9 (3/1/5) | [G1](work/G1_ai_navigation.md) | — | Snap an arbitrary world point onto the navmesh (or report it cannot be) — pre-flight check turning… |
| 89 | `random_reachable_point` | 9 (3/1/5) | [G1](work/G1_ai_navigation.md) | — | Generate random reachable navmesh locations from an origin — the primitive for scattering ambient N… |
| 90 | `reparent_component` | 9 (4/3/2) | [C](work/C_blueprints_graphs.md) | — | Move an existing SCS component under a different parent (own-SCS, inherited-SCS, or native) without… |
| 91 | `resolve_redirector` | 9 (3/1/5) | [B](work/B_assets_registry.md) | — | Chase ObjectRedirectors to the live object path at registry level, no load — fixes stale paths afte… |
| 92 | `run_editor_utility` | 9 (4/2/3) | [A](work/A_editor_core.md) | Blutility | Execute an Editor Utility Blueprint's Run event by asset path — escape hatch for arbitrary editor l… |
| 93 | `set_actor_folder` | 9 (3/2/4) | [A](work/A_editor_core.md) | — | Move actors into an Outliner folder (create on demand); also create/delete/rename folders. |
| 94 | `set_niagara_user_parameter` | 9 (5/2/2) | [G3](work/G3_niagara_audio_physics.md) | Niagara | Set a User.* parameter on any UNiagaraComponent — user params live in a serialized parameter store,… |
| 95 | `set_static_mesh_collision_from_mesh` | 9 (4/3/2) | [E](work/E_geometry_meshes.md) | GeometryScriptingCore, plugin:GeometryScripting | Generate simple collision (boxes/spheres/capsules/convex/swept hulls) for a StaticMesh asset from a… |
| 96 | `set_static_mesh_lods` | 9 (4/3/2) | [E](work/E_geometry_meshes.md) | StaticMeshEditor | Generate reduction LOD chain for a StaticMesh with per-LOD triangle percentages and screen sizes. |
| 97 | `build_static_mesh` | 8 (4/2/2) | [E](work/E_geometry_meshes.md) | — | Explicit renderable-data rebuild after property-level edits — the missing apply step; plus a global… |
| 98 | `create_curve` | 8 (4/2/2) | [H](work/H_data.md) | — | Create UCurveFloat/UCurveVector/UCurveLinearColor assets — no endpoint can make a standalone curve… |
| 99 | `create_dynamic_mesh` | 8 (5/2/1) | [E](work/E_geometry_meshes.md) | GeometryFramework | Allocate a named transient UDynamicMesh working object — opens the entire GeometryScript pipeline t… |
| 100 | `create_material_function` | 8 (4/2/2) | [D](work/D_materials_rendering.md) | — | Mint a UMaterialFunction asset so reusable graph fragments can be authored and called from material… |
| 101 | `create_material_parameter_collection` | 8 (4/2/2) | [D](work/D_materials_rendering.md) | — | Mint a UMaterialParameterCollection asset — global scalar/vector parameters, the one-knob-drives-50… |
| 102 | `create_physics_asset` | 8 (4/2/2) | [E](work/E_geometry_meshes.md) | SkeletalMeshEditor | Generate a UPhysicsAsset for a SkeletalMesh as if created through FBX import — one call, no UI. |
| 103 | `create_sound_cue` | 8 (4/2/2) | [G3](work/G3_niagara_audio_physics.md) | — | Create a SoundCue asset with an optional ready-wired wave-player chain — the 80% case for mod audio… |
| 104 | `get_material_stats` | 8 (4/1/3) | [D](work/D_materials_rendering.md) | MaterialEditor | Numeric ground truth for a compiled material — 8 integers (instructions, samplers, fetches, interpo… |
| 105 | `get_niagara_particle_counts` | 8 (5/2/1) | [G3](work/G3_niagara_audio_physics.md) | Niagara | Per-emitter live particle counts plus execution state for any active Niagara component — the axis's… |
| 106 | `list_nav_areas` | 8 (2/1/5) | [G1](work/G1_ai_navigation.md) | — | Enumerate every loaded UNavArea class with cost numbers so area-class params elsewhere take a valid… |
| 107 | `mesh_asset_info` | 8 (5/2/1) | [E](work/E_geometry_meshes.md) | StaticMeshEditor | One read-only report of a StaticMesh asset's numeric state — LODs, verts, UV channels, collision co… |
| 108 | `mesh_query` | 8 (5/2/1) | [E](work/E_geometry_meshes.md) | GeometryScriptingCore, plugin:GeometryScripting | Numeric inspection of a pooled mesh — the verification story for every mesh_op (agent cannot see th… |
| 109 | `niagara_compile_request` | 8 (4/2/2) | [G3](work/G3_niagara_audio_physics.md) | Niagara | Request (re)compilation of a NiagaraSystem's scripts after authoring mutations and poll completion… |
| 110 | `pie_stop_move` | 8 (3/1/4) | [G1](work/G1_ai_navigation.md) | — | Abort/pause/resume a pawn's current move or route cleanly and clear the plugin-side route queue. |
| 111 | `rename_widget` | 8 (4/2/2) | [G2](work/G2_sequencer_umg_input.md) | — | Rename a widget preserving identity — variable references, delegate bindings, animation bindings. |
| 112 | `set_convex_collision` | 8 (4/2/2) | [E](work/E_geometry_meshes.md) | StaticMeshEditor | Auto-convex decomposition collision (V-HACD) for one or many StaticMeshes — the quality option beyo… |
| 113 | `set_struct_member` | 8 (4/2/2) | [H](work/H_data.md) | — | Retype/re-default/rename an EXISTING UserDefinedStruct member in place — remove+re-add breaks graph… |
| 114 | `spawn_niagara_component` | 8 (4/2/2) | [G3](work/G3_niagara_audio_physics.md) | Niagara | Spawn a one-shot/pooled Niagara FX component at a location in the editor or PIE world — runtime spa… |
| 115 | `create_rvt_asset` | 7 (3/2/2) | [D](work/D_materials_rendering.md) | — | Mint a URuntimeVirtualTexture asset with sizing/material-type set — the missing producer for the ex… |
| 116 | `create_string_table` | 7 (4/1/2) | [H](work/H_data.md) | — | Create a UStringTable asset — first step of a localization-ready text pipeline. |
| 117 | `get_niagara_user_parameters` | 7 (4/2/1) | [G3](work/G3_niagara_audio_physics.md) | Niagara | Enumerate a component's or system's user parameters with types and current values — the read/verifi… |
| 118 | `import_datatable_csv` | 7 (4/1/2) | [H](work/H_data.md) | — | Bulk-fill a table from CSV text (designer-facing interchange); complements JSON-only write paths. |
| 119 | `read_render_target` | 7 (4/2/1) | [D](work/D_materials_rendering.md) | — | Numeric pixel verification — read single pixels or areas from any render target, optionally export… |
| 120 | `set_mpc_parameters` | 7 (3/2/2) | [D](work/D_materials_rendering.md) | — | Add/update/remove named parameters on an existing MPC with PreEditChange/PostEditChange propagation… |
| 121 | `skeletal_mesh_info` | 7 (4/2/1) | [E](work/E_geometry_meshes.md) | SkeletalMeshEditor | Read-only numeric report on a SkeletalMesh asset — LODs, verts/sections, morph targets, sockets. Fi… |
| 122 | `add_simple_collision` | 6 (4/1/1) | [E](work/E_geometry_meshes.md) | StaticMeshEditor | Add primitive simple-collision shapes (box/sphere/capsule/kDOP) to a StaticMesh — replicates Collis… |
| 123 | `delete_datatable_row` | 6 (4/1/1) | [H](work/H_data.md) | — | Remove ONE row; today the only deletion path is whole-table replace (read-all/rewrite-all). |
| 124 | `delete_material_expression` | 6 (3/1/2) | [D](work/D_materials_rendering.md) | MaterialEditor | Remove one node (or all nodes) from a material/function graph — enables iterate-fix loops. |
| 125 | `play_sound_preview` | 6 (4/1/1) | [G3](work/G3_niagara_audio_physics.md) | — | Audition any USoundBase (cue, wave, MetaSound source) through the editor's preview audio component… |
| 126 | `set_material_instance_parent` | 6 (3/1/2) | [D](work/D_materials_rendering.md) | MaterialEditor | Re-parent an existing MaterialInstanceConstant (and optionally wipe its overrides) — parent is othe… |
| 127 | `set_string_table_entry` | 6 (4/1/1) | [H](work/H_data.md) | — | Add/update/remove source strings in a string table — makes agent-authored UI text localizable. |
| 128 | `duplicate_datatable_row` | 5 (3/1/1) | [H](work/H_data.md) | — | Clone a row under a new key — the natural make-a-variant-item/recipe primitive for a data-driven ga… |
| 129 | `list_dynamic_meshes` | 5 (3/1/1) | [E](work/E_geometry_meshes.md) | GeometryFramework | Enumerate live mesh handles with numeric state — the leak detector and session inspector. |
| 130 | `release_dynamic_mesh` | 5 (3/1/1) | [E](work/E_geometry_meshes.md) | GeometryFramework | Return a mesh (or all meshes) to the pool — explicit lifetime end, prevents GC-rooted leaks. |
| 131 | `rename_datatable_row` | 5 (3/1/1) | [H](work/H_data.md) | — | Rename a row key in place preserving data + order (currently impossible without full replace). |
| 132 | `set_niagara_component_active` | 5 (3/1/1) | [G3](work/G3_niagara_audio_physics.md) | Niagara | Activate / deactivate / reset a Niagara component so an agent can drive FX state during PIE verific… |

### Tier 2 — valuable, needs design (68)

| # | Endpoint | Score | Axis | New deps | Purpose |
|---|---|---|---|---|---|
| 1 | `format_graph_ba_status` | 13 (4/4/5) | [P1](work/P1_graph_layout.md) | BlueprintAssist, plugin:BlueprintAssist | Poll the Plan-A BA format job — phase, size progress, transaction state, moved-node diff vs request… |
| 2 | `kr_pin_type_from_property` | 12 (2/5/5) | [K1](work/K1_reconstructor_toolkit.md) | <provider:MifKismetReconstructor> | Property path or (class, property) to exact FEdGraphPinType JSON — pre-compute pin type strings add… |
| 3 | `list_data_layers` | 12 (3/4/5) | [F](work/F_world_level.md) | — | Read-only census of World Partition data layers (name, runtime state, visibility) in the open world. |
| 4 | `rename_function` *(behaviour change)* | 12 (2/5/5) | [C](work/C_blueprints_graphs.md) | — | Document that rename_function already renames macro graphs (any graphId via RenameGraph); add graph… |
| 5 | `world_state_hash` | 12 (3/4/5) | [I](work/I_diagnostics.md) | — | One deterministic CRC summarizing world state (actor set + quantized transforms + loaded levels) —… |
| 6 | `add_nav_link` | 11 (4/3/4) | [G1](work/G1_ai_navigation.md) | — | Spawn an ANavLinkProxy with validated simple point links — connects navmesh islands (stairs, ledges… |
| 7 | `create_metasound_source` | 11 (4/4/3) | [G3](work/G3_niagara_audio_physics.md) | MetasoundEngine, MetasoundFrontend | Create a playable MetaSoundSource (mono/stereo, one-shot or looping, optionally wave-backed) via th… |
| 8 | `merge_static_mesh_actors` | 11 (4/4/3) | [E](work/E_geometry_meshes.md) | MeshMergeUtilities | Merge multiple placed StaticMeshComponents into ONE new StaticMesh asset — the editor Merge Actors… |
| 9 | `open_sequence_editor` | 11 (3/4/4) | [G2](work/G2_sequencer_umg_input.md) | LevelSequenceEditor | Open (or close) a level sequence in the Sequencer editor so a human or capture_camera can see what… |
| 10 | `trace_start` | 11 (3/4/4) | [I](work/I_diagnostics.md) | — | Record an Unreal Insights .utrace of an agent-triggered workload (channel-selectable); trace_stop/t… |
| 11 | `build_reflection_captures` | 10 (3/4/3) | [F](work/F_world_level.md) | — | Recapture every reflection capture in the world after geometry/lighting changes; reports capture co… |
| 12 | `create_foliage_type` | 10 (3/3/4) | [F](work/F_world_level.md) | — | Author a UFoliageType_InstancedStaticMesh asset (density/scale/alignment rules around a mesh) as a… |
| 13 | `create_grass_type` | 10 (2/4/4) | [F](work/F_world_level.md) | — | Author a ULandscapeGrassType asset (mesh + density + placement rules) so scratch-landscape material… |
| 14 | `pie_get_perception` | 10 (3/2/5) | [G1](work/G1_ai_navigation.md) | — | Read-only dump of what an AI currently/ever perceives in PIE — turns 'the guard doesn't react' into… |
| 15 | `refresh_asset_registry` | 10 (3/4/3) | [J](work/J_dds2_project.md) | — | Make the asset registry learn about new loose files dropped into /Game/MODS out-of-editor, without… |
| 16 | `run_eqs_query` | 10 (3/3/4) | [G1](work/G1_ai_navigation.md) | — | Run an existing UEnvQuery asset and read back scored locations — numeric ground truth for 'where wo… |
| 17 | `screenshot_request` | 10 (3/3/4) | [I](work/I_diagnostics.md) | — | Capture what is actually on the editor screen — active viewport incl. PIE frame and Slate UI — as a… |
| 18 | `sequence_editor_play` | 10 (3/4/3) | [G2](work/G2_sequencer_umg_input.md) | LevelSequenceEditor | Drive the open Sequencer: play/pause/scrub/speed — step a cinematic to a frame and capture_camera i… |
| 19 | `set_sublevel_streaming` | 10 (3/4/3) | [F](work/F_world_level.md) | — | Change a sublevel's streaming class (always-loaded vs dynamic) and its level transform without re-a… |
| 20 | `add_niagara_emitter` | 9 (3/3/3) | [G3](work/G3_niagara_audio_physics.md) | NiagaraEditor, Niagara | Add an emitter (copied from an emitter asset) to an existing loose NiagaraSystem — the one structur… |
| 21 | `format_graph_ba_request` | 9 (4/3/2) | [P1](work/P1_graph_layout.md) | BlueprintAssist, plugin:BlueprintAssist | Pixel-quality format identical to pressing BlueprintAssist Format-All — real measured sizes; Plan A… |
| 22 | `group_actors` | 9 (3/2/4) | [A](work/A_editor_core.md) | — | Create/disband AGroupActor groups so multi-part agent-assembled props move as one unit (pair with u… |
| 23 | `list_savegames` | 9 (3/1/5) | [H](work/H_data.md) | — | Enumerate save-game files (name, size, timestamp) under the project's Saved/SaveGames dir. |
| 24 | `mod_package_request` | 9 (5/2/2) | [J](work/J_dds2_project.md) | — | Actually execute the retoc pack/deploy lane that trigger_cook only plans, from the editor session,… |
| 25 | `mount_pak` | 9 (4/3/2) | [J](work/J_dds2_project.md) | PakFile | Mount a mod's retoc trio (or any pak/IoStore container) into the running editor so its packages bec… |
| 26 | `move_actors_to_sublevel` | 9 (3/3/3) | [F](work/F_world_level.md) | — | Rehome existing placed actors into a sublevel with a numeric moved/failed report. |
| 27 | `pie_possess` | 9 (4/2/3) | [G1](work/G1_ai_navigation.md) | — | Fix the #1 reason moves no-op — a controllerless pawn — by spawning its default AI controller or ha… |
| 28 | `read_savegame` | 9 (3/2/4) | [H](work/H_data.md) | — | Load a standard UE .sav slot and dump the USaveGame object's properties via the reflection serializ… |
| 29 | `render_asset_thumbnail` | 9 (3/3/3) | [P3](work/P3_sessions_misc_plugins.md) | — | Render a per-asset Content Browser-style thumbnail PNG so an agent can visually confirm an asset wi… |
| 30 | `run_automation_test` | 9 (4/2/3) | [I](work/I_diagnostics.md) | — | Run one automation test in-process and get pass/fail, error entries, and duration as numbers via re… |
| 31 | `add_blackboard_key` | 8 (2/3/3) | [G1](work/G1_ai_navigation.md) | — | Add a typed key to a loose UBlackboardData asset with duplicate/type/parent-chain validation and co… |
| 32 | `add_sound_cue_node` | 8 (3/3/2) | [G3](work/G3_niagara_audio_physics.md) | — | Extend an existing loose SoundCue's node tree (attenuation/random/mixer/looping/wave player under a… |
| 33 | `build_lighting` | 8 (3/2/3) | [D](work/D_materials_rendering.md) | — | Kick a Lightmass static-lighting build for the loaded level from the bridge. |
| 34 | `change_game_feature_state_request` | 8 (3/3/2) | [P3](work/P3_sessions_misc_plugins.md) | GameFeatures | Drive a game-feature plugin to Installed/Registered/Loaded/Active; completion error code becomes ma… |
| 35 | `change_game_feature_state_status` | 8 (3/3/2) | [P3](work/P3_sessions_misc_plugins.md) | GameFeatures | Poll a pending game-feature state change: {pending, done, ok, errorCode, errorText, currentState, t… |
| 36 | `consolidate_assets` | 8 (3/3/2) | [B](work/B_assets_registry.md) | — | Merge duplicate assets: repoint every referencer of N sources at one target and optionally delete t… |
| 37 | `fixup_redirectors` | 8 (3/2/3) | [B](work/B_assets_registry.md) | — | Repoint all referencers of ObjectRedirectors at live assets and delete the redirectors. |
| 38 | `get_editor_modes` | 8 (2/1/5) | [A](work/A_editor_core.md) | — | Report which editor modes are active (Default, Landscape, Foliage, Modeling) before mutating. |
| 39 | `get_package_disk_data` | 8 (2/1/5) | [B](work/B_assets_registry.md) | — | Per-package physical data from the registry — disk size, file version, imported classes, extension… |
| 40 | `get_settings_config_source` | 8 (2/1/5) | [H](work/H_data.md) | DeveloperSettings | For any UDeveloperSettings class, report which ini file + section its values live in. |
| 41 | `kr_classify_drift` | 8 (3/2/3) | [K2](work/K2_reconstructor_pipeline.md) | provider:MifKismetReconstructor | Per-function drift verdicts for one BP: verdict class, deduped intentional reasons, and root-cause… |
| 42 | `kr_drift_census` | 8 (3/2/3) | [K2](work/K2_reconstructor_pipeline.md) | provider:MifKismetReconstructor | Fidelity verify across a path-filtered set of cooked BPs, sliced one BP per tick, with live totals… |
| 43 | `list_cultures` | 8 (2/1/5) | [H](work/H_data.md) | — | Enumerate cultures known to the engine's ICU data — read-only grounding for localization work. |
| 44 | `list_layers` | 8 (2/1/5) | [A](work/A_editor_core.md) | — | Enumerate editor layers with actor counts and visibility — the read half of layer management. |
| 45 | `modify_actor_layers` | 8 (2/2/4) | [A](work/A_editor_core.md) | — | Add/remove actors to/from named layers and toggle layer visibility for bulk show/hide. |
| 46 | `nav_raycast` | 8 (2/1/5) | [G1](work/G1_ai_navigation.md) | — | 2D navigable-space raycast — is there a straight walkable line from A to B; distinguishes detour-ne… |
| 47 | `pilot_actor` | 8 (2/2/4) | [A](work/A_editor_core.md) | LevelEditor | Pilot/eject the viewport camera onto an actor so capture_camera can shoot from any actor's exact PO… |
| 48 | `render_movie_request` | 8 (4/2/2) | [G2](work/G2_sequencer_umg_input.md) | MovieRenderPipelineCore, MovieRenderPipelineEditor | Queue a Movie Render Pipeline job for a sequence+map — turns authored cinematics into video/image o… |
| 49 | `render_movie_status` | 8 (4/2/2) | [G2](work/G2_sequencer_umg_input.md) | MovieRenderPipelineCore, MovieRenderPipelineEditor | Poll a queued Movie Render Pipeline job: {isRendering, finished, success, outputDirectory, filesWri… |
| 50 | `resimulate_procedural_foliage` | 8 (3/2/3) | [F](work/F_world_level.md) | — | Run a procedural foliage simulation and spawn its instances into the world — biome-scale vegetation… |
| 51 | `select_components` | 8 (2/2/4) | [A](work/A_editor_core.md) | — | Component-level selection — focuses the details panel and drives per-component gizmos for human han… |
| 52 | `set_config_value` | 8 (3/2/3) | [H](work/H_data.md) | — | Write a config value to a Default*.ini with explicit flush — make-this-setting-stick, currently han… |
| 53 | `close_asset_editors` | 7 (2/1/4) | [A](work/A_editor_core.md) | — | Close open asset-editor tabs for an asset (or all), and list currently edited assets. |
| 54 | `create_curve_table` | 7 (3/2/2) | [H](work/H_data.md) | — | Create a UCurveTable asset and optionally bulk-fill from CSV — curve tables are currently untouchab… |
| 55 | `create_render_target` | 7 (3/2/2) | [D](work/D_materials_rendering.md) | — | Mint a transient UTextureRenderTarget2D (optionally baked to a static UTexture2D asset) — canvas fo… |
| 56 | `generate_uv_channel` | 7 (3/2/2) | [E](work/E_geometry_meshes.md) | StaticMeshEditor | Asset-level UV projection (planar/cylindrical/box) plus UV channel add/insert/remove on a StaticMes… |
| 57 | `refresh_texture` | 7 (3/2/2) | [D](work/D_materials_rendering.md) | — | Rebuild textures via UpdateResource after settings edits, with an honest Source.IsValid report per… |
| 58 | `regenerate_skeletal_lods` | 7 (3/2/2) | [E](work/E_geometry_meshes.md) | SkeletalMeshEditor | (Re)generate a skeletal mesh LOD chain via the built-in reducer. |
| 59 | `set_composite_datatable_parents` | 7 (3/2/2) | [H](work/H_data.md) | — | Author UCompositeDataTable parent stacks (DLC/patch row overlays) — the only meaningful composite m… |
| 60 | `set_lod_build_settings` | 7 (3/2/2) | [E](work/E_geometry_meshes.md) | StaticMeshEditor | Set per-LOD build options — lightmap UV generation, normals/tangents recompute, remove degenerates… |
| 61 | `set_nanite_settings` | 7 (3/2/2) | [E](work/E_geometry_meshes.md) | StaticMeshEditor | Enable/disable/tune Nanite on a StaticMesh asset (with build trigger) — per-asset Nanite control no… |
| 62 | `set_physics_constraint` | 7 (3/2/2) | [G3](work/G3_niagara_audio_physics.md) | — | Wire a physics constraint component to its two bodies with correct init order — set_property on Com… |
| 63 | `skeletal_mesh_sockets` | 6 (3/2/1) | [E](work/E_geometry_meshes.md) | SkeletalMeshEditor | Create/rename/remove sockets on a SkeletalMesh asset (attach points for props/weapons — direct DDS2… |
| 64 | `static_mesh_sockets` | 6 (3/2/1) | [E](work/E_geometry_meshes.md) | — | List/create/remove sockets on a StaticMesh asset (attachment points for spawn logic). |
| 65 | `lighting_build_status` | 5 (3/1/1) | [D](work/D_materials_rendering.md) | — | Poll half for build_lighting (the async rule). |
| 66 | `move_struct_member` | 5 (2/1/2) | [H](work/H_data.md) | — | Reorder UserDefinedStruct members (pairs with set_struct_member to close in-place struct editing). |
| 67 | `layout_material_expressions` | 4 (2/1/1) | [D](work/D_materials_rendering.md) | MaterialEditor | Auto-arrange nodes in a grid after programmatic authoring so a human opening the asset sees a reada… |
| 68 | `move_datatable_row` | 4 (2/1/1) | [H](work/H_data.md) | — | Reorder rows (row order matters for iteration order and designer diffing; unreachable today). |

### Tier 3 — exotic / speculative (5)

| # | Endpoint | Score | Axis | New deps | Purpose |
|---|---|---|---|---|---|
| 1 | `set_material_instance_layers` | 9 (2/4/3) | [D](work/D_materials_rendering.md) | — | Author a material-layers stack (layer + blend functions) on a MaterialInstanceConstant programmatic… |
| 2 | `create_blackboard_asset` | 8 (2/2/4) | [G1](work/G1_ai_navigation.md) | — | Create a new empty UBlackboardData asset (optionally with parent) so add_blackboard_key has a loose… |
| 3 | `create_geometry_collection` | 8 (2/3/3) | [G3](work/G3_niagara_audio_physics.md) | GeometryCollectionEngine | Build a GeometryCollection from static meshes — Chaos destruction entry asset; game ships zero, no… |
| 4 | `read_rama_savefile` | 8 (3/2/3) | [H](work/H_data.md) | — | Read the static-data header of a DDS2 gameplay save (RamaSaveSystem format) — the format the game a… |
| 5 | `unmount_pak` | 6 (2/3/1) | [J](work/J_dds2_project.md) | PakFile | Undo mount_pak in the same session (iterate: repack then remount). |

## Implementation batches (grouped so each build cycle adds the fewest new modules)

### Batch 0 — repairs to existing endpoints — 8 endpoints

Zero new modules. Fix the defects the audit proved in the live bridge before adding surface: the six behaviour-change entries below, plus (from 03 §7) find_assets unknown-param strictness, server.py tool for diagnose_landscape_draws, bucket reclassification of describe_class/list_enum_values, and the editor rebuild that picks up the 4 in-source endpoints.

`list_components`°, `pie_status`°, `connect_pins`°, `read_modloader_log`, `add_component`°, `list_variables`°, `rename_function`, `snap_actors_to_ground`°

_° = Tier 0 (6 in this batch)._

### Batch 1 — zero-new-dependency endpoints — 145 endpoints

No Build.cs change at all; the largest single win. Undo/redo introspection, dirty-package flows, selection/folders/layers, dependency graphs, generic node spawning, SCS reparenting, local variables, landscape splines, foliage, sublevels, navigation queries, PIE movement, datatable/curve authoring, config, savegames, perf/log/screenshot diagnostics.

`list_widget_tree`°, `apply_spline_to_landscape`°, `add_create_delegate`°, `create_macro`°, `format_graph`°, `list_transactions`° ✅, `pie_move_status`°, `set_variable_type`°, `find_path`°, `pie_move_pawn`°, `reparent_widget`°, `add_node_by_class`°, `read_curve`°, `undo_transactions`° ✅, `create_material`°, `validate_level_materials`°, `redo_transactions`° ✅, `set_actor_render_overrides`°, `set_curve_keys`°, `get_actor_render_info`°, `shader_compile_status`°, `list_native_classes`, `list_sublevels`, `list_ue4ss_mods`, `add_sublevel`, `describe_anim_class`, `export_graph_text`, `get_properties_bulk`, `kr_list_cooked_blueprints`, `list_automation_tests`, `list_foliage`, `list_node_classes`, `pie_resolve_path`, `set_current_sublevel`, `verify_pak_contents`, `apply_landscape_splines`, `get_asset_dependencies`, `get_asset_referencers`, `get_class_hierarchy`, `list_landscape_splines`, `log_tail`, `set_sublevel_visibility`, `add_anim_state`, `add_anim_state_machine`, `add_anim_transition`, `call_object_function`, `create_asset`, `find_assets_by_tag`, `navmesh_tile_info`, `paint_foliage`, `add_async_action`, `add_nav_modifier_volume`, `create_landscape_spline`, `create_sublevel`, `export_asset`, `get_asset_tags`, `get_config_value`, `get_cvar`, `get_viewport_state`, `list_cvars`, `list_dirty_packages` ✅, `map_check`, `remove_foliage_instances`, `remove_graph`, `remove_sublevel`, `save_dirty_packages` ✅, `set_view_mode`, `create_datatable`, `export_datatable_csv`, `list_actor_folders`, `list_blackboard_keys`, `list_content_paths`, `list_primary_assets`, `list_string_table_entries`, `project_to_navmesh`, `random_reachable_point`, `reparent_component`, `resolve_redirector`, `set_actor_folder`, `build_static_mesh`, `create_curve`, `create_material_function`, `create_material_parameter_collection`, `create_sound_cue`, `list_nav_areas`, `pie_stop_move`, `rename_widget`, `set_struct_member`, `create_rvt_asset`, `create_string_table`, `import_datatable_csv`, `read_render_target`, `set_mpc_parameters`, `delete_datatable_row`, `play_sound_preview`, `set_string_table_entry`, `duplicate_datatable_row`, `rename_datatable_row`, `list_data_layers`, `world_state_hash`, `add_nav_link`, `trace_start`, `build_reflection_captures`, `create_foliage_type`, `create_grass_type`, `pie_get_perception`, `refresh_asset_registry`, `run_eqs_query`, `screenshot_request`, `set_sublevel_streaming`, `group_actors`, `list_savegames`, `mod_package_request`, `move_actors_to_sublevel`, `pie_possess`, `read_savegame`, `render_asset_thumbnail`, `run_automation_test`, `add_blackboard_key`, `add_sound_cue_node`, `build_lighting`, `consolidate_assets`, `fixup_redirectors`, `get_editor_modes`, `get_package_disk_data`, `list_cultures`, `list_layers`, `modify_actor_layers`, `nav_raycast`, `resimulate_procedural_foliage`, `select_components`, `set_config_value`, `close_asset_editors`, `create_curve_table`, `create_render_target`, `refresh_texture`, `set_composite_datatable_parents`, `set_physics_constraint`, `static_mesh_sockets`, `lighting_build_status`, `move_struct_member`, `move_datatable_row`, `set_material_instance_layers`, `create_blackboard_asset`, `read_rama_savefile`

_° = Tier 0 (21 in this batch)._

### Batch 2 — materials (MaterialEditor) — 9 endpoints

One editor-only module unlocks the whole material-graph authoring category (a named Tier-0 gap).

`recompile_material`°, `add_material_expression`°, `connect_material_expressions`°, `connect_material_property`°, `list_material_expressions`°, `get_material_stats`, `delete_material_expression`, `set_material_instance_parent`, `layout_material_expressions`

_° = Tier 0 (5 in this batch)._

### Batch 3 — Enhanced Input (EnhancedInput) — 4 endpoints

One runtime module (plugin already enabled by default); DDS2 census: 62 InputActions, 5 IMCs.

`list_input_mappings`, `input_map_key`, `create_input_action`, `create_input_mapping_context`

### Batch 4 — mesh editing (StaticMeshEditor, SkeletalMeshEditor, MeshMergeUtilities, PhysicsUtilities, GeometryCollectionEngine) — 13 endpoints

Engine-source editor modules (no plugin enables): LODs, collision, Nanite, sockets, UV channels, physics assets, merging.

`set_static_mesh_lods`, `create_physics_asset`, `mesh_asset_info`, `set_convex_collision`, `skeletal_mesh_info`, `add_simple_collision`, `merge_static_mesh_actors`, `generate_uv_channel`, `regenerate_skeletal_lods`, `set_lod_build_settings`, `set_nanite_settings`, `skeletal_mesh_sockets`, `create_geometry_collection`

### Batch 5 — GeometryScript (enable plugin + GeometryScriptingCore/Editor, GeometryFramework) — 9 endpoints

One plugin enable + three modules buys the ~478-function procedural-mesh surface via the dynamic-mesh session model.

`mesh_op`, `commit_dynamic_mesh`, `copy_from_static_mesh`, `create_static_mesh_asset`, `set_static_mesh_collision_from_mesh`, `create_dynamic_mesh`, `mesh_query`, `list_dynamic_meshes`, `release_dynamic_mesh`

### Batch 6 — FX & audio (Niagara, NiagaraEditor, MetasoundEngine, MetasoundFrontend, AudioEditor) — 10 endpoints

VFX + audio authoring; census-backed (38 Niagara systems, 354 SoundCues, 185 MetaSounds in DDS2).

`import_asset`, `create_niagara_system`, `set_niagara_user_parameter`, `get_niagara_particle_counts`, `niagara_compile_request`, `spawn_niagara_component`, `get_niagara_user_parameters`, `set_niagara_component_active`, `create_metasound_source`, `add_niagara_emitter`

### Batch 7 — Sequencer & MRQ (MovieScene, MovieSceneTracks, LevelSequence, LevelSequenceEditor, MovieRenderPipeline*) — 12 endpoints

Biggest module count for the smallest project surface (4 LevelSequences in DDS2) — deliberately last among Tier-1 carriers; the same endpoints double for UMG widget animations, which raises the value above the raw census.

`describe_sequence`, `create_widget_animation`, `sequence_add_section`, `sequence_add_track`, `sequence_bind_actor`, `sequence_set_keys`, `widget_animation_bind`, `create_level_sequence`, `open_sequence_editor`, `sequence_editor_play`, `render_movie_request`, `render_movie_status`

### Batch R — reconstructor endpoints via the registration interface (provider: MifKismetReconstructor) — 10 endpoints

Coupling model (b): endpoints live in the reconstructor plugin, registered through the new MifBridgeEndpointRegistry; MifBridge stays loadable without it. Two need zero export promotions (kr_dump_blueprint, kr_disassemble_function); the verify family needs one KISMET_API refactor in the engine fork (spec in work/K2_reconstructor_pipeline.md).

`kr_disassemble_function`°, `kr_dump_blueprint`°, `kr_reconstruct_status`°, `kr_reconstruct_request`°, `kr_list_events`, `kr_analyze_ubergraph`, `kr_verify_fidelity`, `kr_pin_type_from_property`, `kr_classify_drift`, `kr_drift_census`

_° = Tier 0 (4 in this batch)._

### Batch 8 — small single-module additions — 21 endpoints

Independent one-module endpoints; implement opportunistically: Blutility (run_editor_utility), DeveloperSettings, LevelEditor (pilot_actor), DataValidation, MessageLog, ScriptDisassembler (cooked bytecode!), RHI (perf stats), ImageWrapper (heightmap/render-target IO), PakFile (pak verification), Water, AudioEditor.

`disassemble_function`, `export_heightmap`, `get_game_feature_state`, `get_perf_stats`, `message_log_read`, `export_weightmap`, `fit_comment_to_nodes`, `get_message_log`, `import_heightmap`, `list_developer_settings`, `set_water_body_profile`, `validate_assets`, `run_editor_utility`, `format_graph_ba_status`, `format_graph_ba_request`, `mount_pak`, `change_game_feature_state_request`, `change_game_feature_state_status`, `get_settings_config_source`, `pilot_actor`, `unmount_pak`

## Removed from ranking

- DEMOTED: `create_level_instance` (F_world_level) — see its UNVERIFIED entry
- Merged duplicates: `list_dirty_packages` (A), `build_reflection_captures` (D), `create_physics_asset` (G3), `get_asset_compilation_status` (B), `kr_verify_fidelity` (K1), `kr_reconstruct_function` (K1), `kr_batch_reconstruct_request` (K1), `kr_batch_reconstruct_status` (K1)

