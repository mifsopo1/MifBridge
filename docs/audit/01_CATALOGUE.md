# MifBridge endpoint audit — catalogue (master index)

_Audit date: 2026-07-26. Engine: D:/UE532 (5.3.2 CookedEditorModKit fork). Baseline: 160 existing endpoints ([00_BASELINE.md](00_BASELINE.md))._

## Verdict on the working hypothesis

**Proven.** The sweep of the real engine surface produced **250 verified entries** (242 new endpoints + 8 behaviour changes to existing ones), of which **241 survive verification and dedup** — roughly 1.4× the existing surface, every one with a cited, export-checked engine entry point. Whole categories at zero coverage (materials, Sequencer, Niagara, audio, physics, automation, undo introspection) came back implementable. The catalogue was verified adversarially: 189 entries CONFIRMED as originally cited, 60 CORRECTED during verification (mostly hidden modal/blocking hazards), 1 DEMOTED.

## Data ownership — where the full entries live

This file is the **index**. The full ten-field entries (verbatim signatures with file:line, export macros, module deps,
WITH_EDITOR guards, bucket justification, async design, parameter tables with aliases, failure modes with error text,
cooked-content behaviour, numeric verification method, and the Phase-2 verdict line) live in the twelve axis files under
[work/](work/) — **those are the single source of truth**; this index is generated from them and must not be edited by hand.
Negative results and UNVERIFIED items live in the same axis files; the cross-cutting risk digest is
[03_GAPS_AND_RISKS.md](03_GAPS_AND_RISKS.md); pending decisions are [04_OPEN_QUESTIONS.md](04_OPEN_QUESTIONS.md).

Scoring: U = unblocks, E = effort (inverted), R = risk (inverted), each 1–5; rank = U+E+R.
Tiers: 0 closes a known gap · 1 high leverage/low risk · 2 valuable, needs design · 3 exotic.

## Cross-axis dedup resolutions (ratified in 04_OPEN_QUESTIONS.md §1)

| Kept | Dropped duplicate | Resolution |
|---|---|---|
| `list_dirty_packages` (B) | `list_dirty_packages` (A) | B owns (citation corrected there); A entry cross-references. |
| `build_reflection_captures` (F) | `build_reflection_captures` (D) | F owns; D's hazard analysis (FinishAllCompilation wait + SM5 check()) merged into the spec. |
| `create_physics_asset` (E) | `create_physics_asset` (G3) | E owns; G3's hazards (FScopedSlowTask MakeDialog, render-data check()) merged into the spec. |
| `asset_compile_status` (E) | `get_asset_compilation_status` (B) | Single endpoint named asset_compile_status; B's per-manager breakdown (GetAssetTypeName) folded in. |
| `kr_verify_fidelity` (K2) | `kr_verify_fidelity` (K1) | K2 owns (async request+poll form; verified cost chain). K1 sync variant merged in. |
| `kr_reconstruct_request` (K2) | `kr_reconstruct_function` (K1) | Function-scope reconstruct becomes kr_reconstruct_request {mode:"function"}. |
| `kr_reconstruct_request` (K2) | `kr_batch_reconstruct_request` (K1) | K2 one-slot job model owns; census batching is its mode:"census". |
| `kr_reconstruct_status` (K2) | `kr_batch_reconstruct_status` (K1) | Status poll merged with the shared job model. |
| `create_asset` (B) | — | Boundary policy: dedicated creators (D/G2/G3/H) own their asset types; `create_asset` covers the residue (DataAsset, PhysicalMaterial, AnimBlueprint, …) and hard-refuses owned types. |

## Totals

| Tier 0 | Tier 1 | Tier 2 | Tier 3 | Active total | Demoted | Merged dupes |
|---|---|---|---|---|---|---|
| 36 | 132 | 68 | 5 | 241 | 1 | 8 |

## Delivery status — hand-maintained, and the generator must preserve this section

_Added 2026-07-29. This index is generated from [work/](work/) and the rows below the Totals carry no
delivery marker; a reader who assumed "the endpoint name answers, therefore the entry is delivered"
got the wrong answer for eight rows. Authority for everything here is
[work/R3_REMAINING_WORK.md](work/R3_REMAINING_WORK.md), which reconciled all 250 rows line by line
against source and `self_audit` on 2026-07-28._

| SHIPPED | SHIPPED (PARTIAL) | SUPERSEDED | WITHDRAWN | STILL OPEN | of rows |
|---|---|---|---|---|---|
| 34 | 2 | 9 | 2 | **203** | 250 |

**34 rows delivered (32 unique endpoint names), plus 2 partially.** A previously circulating figure of
**41** (in `07_SELF_AUDIT_FINDINGS.md` §6) was wrong: it treated a *behaviour-change* entry as
delivered whenever the endpoint **NAME** was live. For these eight entries the name was live before
this catalogue was written — the entry asks for a change to what the endpoint DOES — so a live name
delivers nothing.

**The 8 behaviour-change entries, individually.** Status is R3's (2026-07-28), re-verified against
source on 2026-07-29. **Source line numbers are 2026-07-29 positions and drift as handlers are
edited** — R3 cited `connect_pins` at `MifBridgeCommon.cpp:2052` and the same statement is now at
`:3762`. Verify the statement, not the line.

| Entry | Axis | Status | Evidence |
|---|---|---|---|
| `connect_pins` | C | **STILL OPEN** — specced change never landed | `MifBridgeCommon.cpp:3762` is still `const UEdGraphSchema_K2* Schema = K2();` inside `ConnectPinsChecked` (:3738). The hardcoded K2 CDO drives `BreakPinLinks` (:3773-3774), `CanCreateConnection` (:3777) and `TryCreateConnection` (:3783), so **a graph whose own schema is not a `UEdGraphSchema_K2` is asked the wrong object whether a connection is legal** — the AnimGraph state-machine family (`UAnimationStateMachineSchema : public UEdGraphSchema`) still cannot be wired by this endpoint. Reported as shipped; it is not. |
| `add_component` | C | **STILL OPEN** | `MifBridgeComponents.cpp:76` is still `SCS->FindSCSNode(FName(*ParentName))` — own SCS only; an inherited or native parent name fails at `:79`. |
| `list_variables` | C | **STILL OPEN** | `MifBridgeIntrospect.cpp:247` iterates `Blueprint->NewVariables` only and hardcodes `scope` to `"member"` at `:252`. Only `add_variable` understands `scope=local`. |
| `rename_function` | C | **STILL OPEN** | No `graphType` is emitted anywhere in `MifBridgeNodes2.cpp`. (The `graphId` path does already reach macro graphs, so half the entry is documentation.) |
| `read_modloader_log` | J | **STILL OPEN** | `MifBridgePipeline.cpp:82` still calls `PushLine`, which appends a bare `FJsonValueString` (:19-22). Raw lines, not structured events. File unchanged since 2026-07-11. |
| `pie_status` | Q | **SHIPPED (PARTIAL)** | The `state` word + `HasBegunPlay` readiness landed (`MifBridgePIE.cpp:93-116`). The specced lifecycle words did **not**: `MifBridgePIE.cpp:114` emits only `running` / `starting` / `stopped`; `travelling`, `stopping` and `simulating` are not state words (`simulating` is a separate bool at `:112`). |
| `snap_actors_to_ground` | Q | **SHIPPED (PARTIAL)** | Multi-trace + landscape/groundActor filter landed. The specced **penetrating** trace did not: `MifBridgeWorld.cpp:486` is `LineTraceMultiByChannel(..., ECC_WorldStatic, ...)`, which stops at the first blocking hit, so landscape under blocking geometry is still unreachable. |
| `list_components` | C | **DELIVERED IN SOURCE ONLY — after the R3 pass** | R3 recorded STILL OPEN at 2026-07-28 23:11; Batch N implemented it at 2026-07-29 01:05 (`MifBridgeComponents.cpp` — three origins `ownSCS`/`parentBlueprintSCS`/`native`, per-row `origin` at `:279`, `nativeCount` at `:377`). **Batch N was source-only — nothing was built**, so this is not in a running DLL and `self_audit` will not show it until the next build. The entry's `source` field shipped as `origin` per R3 §3/S4. |

Six of the eight are Tier 0. R3's own wording was *"six not at all, two partially"* — Batch N has
since moved `list_components` out of the "not at all" column **in source**, leaving **5 not at all, 2
partial, 1 in source but unbuilt**. All seven of the STILL OPEN / PARTIAL entries are open work, and
the R3 totals above still stand as row counts: Batch N's `list_components` cannot move the SHIPPED
column until it is in a built DLL.

## A — Editor core

Full entries: [work/A_editor_core.md](work/A_editor_core.md) — plus 10 cited negative results and 6 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `list_transactions` | 0 | 5/2/5 | read-only | — | — | works | CONFIRMED | Introspect the undo buffer (indices, titles, contexts, object counts, sizes) so an agent can see what its mut… |
| `undo_transactions` | 0 | 5/2/3 | self-managed | — | — | works | CONFIRMED | Undo the last N transactions (or down to a queue index) — the rollback half of the gap. |
| `redo_transactions` | 0 | 4/1/3 | self-managed | — | — | works | CONFIRMED | Redo the last N undone transactions — lets an agent A/B a change numerically. |
| `get_cvar` | 1 | 4/1/5 | read-only | — | — | works | CONFIRMED | Structured READ of a console variable — value, type, flags, help — closing the SET-without-GET asymmetry. |
| `get_viewport_state` | 1 | 3/2/5 | read-only | — | — | works | CONFIRMED | Read the active level viewport's render configuration — view mode, game view, realtime, size, show-flag diffs. |
| `list_cvars` | 1 | 3/2/5 | read-only | — | — | works | CONFIRMED | Enumerate console variables/commands by prefix or substring with values and flags. |
| `list_developer_settings` | 1 | 4/1/5 | read-only | — | DeveloperSettings | works | CONFIRMED | Enumerate every UDeveloperSettings subclass (class path, config, category, section) as an index for the set_p… |
| `list_dirty_packages` *(merged — see dedup table)* | 1 | 4/1/5 | read-only | — | — | works | CONFIRMED | Enumerate every unsaved package (map vs content split) so an agent knows what a crash would lose. |
| `save_dirty_packages` | 1 | 4/3/3 | self-managed | — | — | degraded | CORRECTED | One-call checkout-free, prompt-free save of everything dirty. |
| `set_view_mode` | 1 | 4/2/4 | self-managed | — | — | works | CONFIRMED | Switch viewport view mode, toggle game view/realtime and individual show flags — makes capture_camera diagnos… |
| `list_actor_folders` | 1 | 3/1/5 | read-only | — | — | works | CONFIRMED | Enumerate the World Outliner folder tree with per-folder actor counts. |
| `run_editor_utility` | 1 | 4/2/3 | self-managed | — | Blutility | degraded | CONFIRMED | Execute an Editor Utility Blueprint's Run event by asset path — escape hatch for arbitrary editor logic. |
| `set_actor_folder` | 1 | 3/2/4 | transacted | — | — | works | CONFIRMED | Move actors into an Outliner folder (create on demand); also create/delete/rename folders. |
| `group_actors` | 2 | 3/2/4 | self-managed | — | — | works | CONFIRMED | Create/disband AGroupActor groups so multi-part agent-assembled props move as one unit (pair with ungroup_act… |
| `get_editor_modes` | 2 | 2/1/5 | read-only | — | — | works | CONFIRMED | Report which editor modes are active (Default, Landscape, Foliage, Modeling) before mutating. |
| `list_layers` | 2 | 2/1/5 | read-only | — | — | works | CONFIRMED | Enumerate editor layers with actor counts and visibility — the read half of layer management. |
| `modify_actor_layers` | 2 | 2/2/4 | transacted | — | — | works | CONFIRMED | Add/remove actors to/from named layers and toggle layer visibility for bulk show/hide. |
| `pilot_actor` | 2 | 2/2/4 | self-managed | — | LevelEditor | works | CORRECTED | Pilot/eject the viewport camera onto an actor so capture_camera can shoot from any actor's exact POV. |
| `select_components` | 2 | 2/2/4 | transacted | — | — | works | CONFIRMED | Component-level selection — focuses the details panel and drives per-component gizmos for human handoff. |
| `close_asset_editors` | 2 | 2/1/4 | self-managed | — | — | works | CONFIRMED | Close open asset-editor tabs for an asset (or all), and list currently edited assets. |

## B — Assets and the registry

Full entries: [work/B_assets_registry.md](work/B_assets_registry.md) — plus 9 cited negative results and 5 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `get_asset_dependencies` | 1 | 5/2/5 | read-only | — | — | degraded | CONFIRMED | On-disk dependency list of a package with hard/soft/game/build edge classification — graph data find_assets l… |
| `get_asset_referencers` | 1 | 5/2/5 | read-only | — | — | degraded | CONFIRMED | Reverse edge: what references this asset, incl. which loose project assets reference a base-game container as… |
| `get_class_hierarchy` | 1 | 5/2/5 | read-only | — | — | works | CONFIRMED | Registry-level class inheritance: all classes derived from X (incl. unloaded cooked BPGCs) and ancestor chain… |
| `create_asset` | 1 | 5/3/3 | self-managed | — | — | n/a | CORRECTED | Mint non-Blueprint assets (DataTable, curves, DataAssets, StringTable, MPC, PhysicalMaterial, AnimBlueprint)… |
| `find_assets_by_tag` | 1 | 4/2/5 | read-only | — | — | works | CONFIRMED | Query registry by asset-registry tag/value pairs (e.g. DataTables by RowStructure) — axis find_assets cannot… |
| `get_message_log` | 1 | 4/2/5 | read-only | — | MessageLog | works | CONFIRMED | Read any FMessageLog channel (MapCheck, AssetCheck, LoadErrors, BlueprintLog, PIE, ...) as structured JSON. |
| `import_asset` | 1 | 5/3/3 | self-managed | `import_asset_status` | AudioEditor | n/a | CONFIRMED | Import a disk file (PNG/TGA to Texture2D, FBX to meshes, WAV to SoundWave) as a project asset. |
| `export_asset` | 1 | 4/2/4 | read-only | — | — | works | CONFIRMED | Write an asset to a disk file (texture to PNG, mesh to OBJ/FBX, sound to WAV, object/level to T3D) for outsid… |
| `get_asset_compilation_status` *(merged — see dedup table)* | 1 | 4/1/5 | read-only | — | — | works | CONFIRMED | Poll outstanding async asset compilation (textures, meshes, sounds) — the missing poll half for import_asset… |
| `get_asset_tags` | 1 | 4/1/5 | read-only | — | — | works | CONFIRMED | Dump the full asset-registry tag/value map for one asset without loading it. |
| `list_dirty_packages` | 1 | 4/1/5 | read-only | — | — | works | CORRECTED | Enumerate unsaved (dirty) content and world packages before save_package / trigger_cook / PIE. |
| `map_check` | 1 | 4/2/4 | read-only | — | — | works | CORRECTED | Run the editor's Map Check over the loaded level and return the message list structurally. |
| `validate_assets` | 1 | 4/2/4 | read-only | — | DataValidation | degraded | CORRECTED | Run the engine data-validation pass over chosen assets, returning structured per-asset errors/warnings. |
| `list_content_paths` | 1 | 3/1/5 | read-only | — | — | works | CONFIRMED | Enumerate the content folder tree (registry-cached paths) — answers 'what folders exist under /Game'. |
| `resolve_redirector` | 1 | 3/1/5 | read-only | — | — | works | CONFIRMED | Chase ObjectRedirectors to the live object path at registry level, no load — fixes stale paths after rename_a… |
| `consolidate_assets` | 2 | 3/3/2 | self-managed | — | — | degraded | CORRECTED | Merge duplicate assets: repoint every referencer of N sources at one target and optionally delete the sources. |
| `fixup_redirectors` | 2 | 3/2/3 | self-managed | — | — | degraded | CORRECTED | Repoint all referencers of ObjectRedirectors at live assets and delete the redirectors. |
| `get_package_disk_data` | 2 | 2/1/5 | read-only | — | — | degraded | CONFIRMED | Per-package physical data from the registry — disk size, file version, imported classes, extension — no files… |

## C — Blueprints and graphs

Full entries: [work/C_blueprints_graphs.md](work/C_blueprints_graphs.md) — plus 7 cited negative results and 7 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `list_components` *(behaviour change)* | 0 | 4/5/5 | read-only | — | — | refuses | CONFIRMED | Include inherited and native components (source field) so agents can discover legal parents |
| `connect_pins` *(behaviour change)* | 0 | 4/5/4 | transacted | — | — | refuses | CORRECTED | ConnectPinsChecked must use the graph's own schema instead of the hardcoded K2 CDO |
| `add_component` *(behaviour change)* | 0 | 5/4/3 | transacted | — | — | refuses | CONFIRMED | parentName resolves inherited-SCS and native parents, not just own SCS |
| `add_create_delegate` | 0 | 4/4/4 | transacted | — | — | refuses | CONFIRMED | Spawn UK2Node_CreateDelegate wired to a target function so any existing function can bind to a dispatcher fro… |
| `create_macro` | 0 | 4/4/4 | transacted | — | — | refuses | CONFIRMED | Create a user macro graph (entry/exit tunnels) so add_macro_instance can place user-authored macros |
| `list_variables` *(behaviour change)* | 0 | 4/4/4 | transacted | — | — | refuses | CONFIRMED | Add scope=local (+function) to list_variables, rename_variable, remove_variable, set_variable_default — local… |
| `set_variable_type` | 0 | 5/4/3 | transacted | — | — | refuses | CONFIRMED | Retype a member or local variable in place instead of remove+add which drops get/set nodes, flags and category |
| `add_node_by_class` | 0 | 5/2/3 | transacted | — | — | refuses | CORRECTED | Spawn ANY concrete UK2Node subclass by class path via PlaceAndInit with a reflection-applied init map |
| `disassemble_function` | 1 | 5/4/5 | read-only | — | ScriptDisassembler | works | CONFIRMED | Read-only Kismet bytecode disassembly of any UFunction, including cooked UBlueprintGeneratedClass functions |
| `describe_anim_class` | 1 | 4/4/5 | read-only | — | — | works | CONFIRMED | Structured read of a cooked or uncooked AnimBP generated class: baked state machines and anim-node property c… |
| `list_node_classes` | 1 | 3/5/5 | read-only | — | — | works | CORRECTED | Enumerate every loaded spawnable UK2Node subclass (name, module, abstract, denylist status, dedicated endpoin… |
| `add_anim_state` | 1 | 5/3/3 | transacted | — | — | refuses | CONFIRMED | Add a state to a state machine graph; returns the state's bound animation graph id for existing graph endpoin… |
| `add_anim_state_machine` | 1 | 5/3/3 | transacted | — | — | refuses | CORRECTED | Create a state machine node inside an AnimBP's AnimGraph — the entry point of AnimBP authoring, currently imp… |
| `add_anim_transition` | 1 | 5/3/3 | transacted | — | — | refuses | CONFIRMED | Create a transition between two states, returning the transition node and its K2 rule graph id |
| `add_async_action` | 1 | 4/3/3 | transacted | — | — | refuses | CONFIRMED | Spawn UK2Node_AsyncAction family (latent proxy nodes for UBlueprintAsyncActionBase factories), currently unau… |
| `remove_graph` | 1 | 3/4/3 | transacted | — | — | refuses | CONFIRMED | Delete a macro or collapsed/extra graph; remove_function only searches FunctionGraphs so macros are permanent… |
| `reparent_component` | 1 | 4/3/2 | transacted | — | — | refuses | CONFIRMED | Move an existing SCS component under a different parent (own-SCS, inherited-SCS, or native) without destroy/r… |
| `rename_function` *(behaviour change)* | 2 | 2/5/5 | transacted | — | — | refuses | CONFIRMED | Document that rename_function already renames macro graphs (any graphId via RenameGraph); add graphType respo… |

## D — Materials and rendering

Full entries: [work/D_materials_rendering.md](work/D_materials_rendering.md) — plus 10 cited negative results and 6 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `recompile_material` | 0 | 5/3/3 | self-managed | `shader_compile_status` | MaterialEditor | refuses | CORRECTED | Apply graph/parameter edits, dispatching on asset class (Material/Function/MIC) — without it none of the edit… |
| `add_material_expression` | 0 | 5/3/2 | transacted | — | MaterialEditor | refuses | CONFIRMED | Add a node to a material or material-function graph — the atom of the Tier-0 material-graph-authoring gap. |
| `connect_material_expressions` | 0 | 5/2/2 | transacted | — | MaterialEditor | refuses | CONFIRMED | Wire expression output to expression input inside a material/function graph. |
| `connect_material_property` | 0 | 5/2/2 | transacted | — | MaterialEditor | refuses | CONFIRMED | Wire an expression output into a material OUTPUT pin (BaseColor, Roughness…) — without this the graph never a… |
| `create_material` | 0 | 5/2/2 | self-managed | — | — | works | CONFIRMED | Mint a new UMaterial asset (master material) — the missing half of the Tier-0 material-authoring gap. |
| `validate_level_materials` | 0 | 5/3/1 | read-only | — | — | works | CONFIRMED | Read-only structured report over all mesh components: null slots, default-material fallbacks, incomplete mate… |
| `list_material_expressions` | 0 | 5/2/1 | read-only | — | MaterialEditor | degraded | CONFIRMED | Read-back for the whole authoring loop: enumerate graph nodes, positions, key parameters, connections, proper… |
| `set_actor_render_overrides` | 0 | 4/2/2 | transacted | — | — | works | CORRECTED | Batch per-actor cull/LOD control — force LODs, set draw distances, exclude from HLOD — with render-state refr… |
| `get_actor_render_info` | 0 | 4/1/1 | read-only | — | — | works | CONFIRMED | Read-back pair for set_actor_render_overrides: per-component LOD forcing, draw distances, HLOD flag, LOD coun… |
| `shader_compile_status` | 0 | 4/1/1 | read-only | — | — | works | CONFIRMED | THE poll endpoint for every material mutation on this axis and for editor-wide shader churn after level loads. |
| `create_material_function` | 1 | 4/2/2 | self-managed | — | — | works | CONFIRMED | Mint a UMaterialFunction asset so reusable graph fragments can be authored and called from materials. |
| `create_material_parameter_collection` | 1 | 4/2/2 | self-managed | — | — | works | CONFIRMED | Mint a UMaterialParameterCollection asset — global scalar/vector parameters, the one-knob-drives-50-materials… |
| `get_material_stats` | 1 | 4/1/3 | read-only | — | MaterialEditor | degraded | CORRECTED | Numeric ground truth for a compiled material — 8 integers (instructions, samplers, fetches, interpolators) to… |
| `create_rvt_asset` | 1 | 3/2/2 | self-managed | — | — | works | CORRECTED | Mint a URuntimeVirtualTexture asset with sizing/material-type set — the missing producer for the existing bin… |
| `read_render_target` | 1 | 4/2/1 | read-only | — | — | works | CONFIRMED | Numeric pixel verification — read single pixels or areas from any render target, optionally export to disk fo… |
| `set_mpc_parameters` | 1 | 3/2/2 | transacted | `shader_compile_status` | — | works | CONFIRMED | Add/update/remove named parameters on an existing MPC with PreEditChange/PostEditChange propagation to refere… |
| `delete_material_expression` | 1 | 3/1/2 | transacted | — | MaterialEditor | refuses | CONFIRMED | Remove one node (or all nodes) from a material/function graph — enables iterate-fix loops. |
| `set_material_instance_parent` | 1 | 3/1/2 | transacted | — | MaterialEditor | works | CONFIRMED | Re-parent an existing MaterialInstanceConstant (and optionally wipe its overrides) — parent is otherwise only… |
| `build_lighting` | 2 | 3/2/3 | self-managed | `lighting_build_status` | — | degraded | CORRECTED | Kick a Lightmass static-lighting build for the loaded level from the bridge. |
| `build_reflection_captures` *(merged — see dedup table)* | 2 | 2/2/3 | self-managed | — | — | degraded | CORRECTED | Re-capture all reflection captures in the world (Build → Reflection Captures) so specular ambience matches af… |
| `create_render_target` | 2 | 3/2/2 | self-managed | — | — | n/a | CORRECTED | Mint a transient UTextureRenderTarget2D (optionally baked to a static UTexture2D asset) — canvas for capture/… |
| `refresh_texture` | 2 | 3/2/2 | self-managed | — | — | degraded | CONFIRMED | Rebuild textures via UpdateResource after settings edits, with an honest Source.IsValid report per texture. |
| `lighting_build_status` | 2 | 3/1/1 | read-only | — | — | works | CONFIRMED | Poll half for build_lighting (the async rule). |
| `layout_material_expressions` | 2 | 2/1/1 | transacted | — | MaterialEditor | refuses | CONFIRMED | Auto-arrange nodes in a grid after programmatic authoring so a human opening the asset sees a readable graph. |
| `set_material_instance_layers` | 3 | 2/4/3 | transacted | `shader_compile_status` | — | refuses | CONFIRMED | Author a material-layers stack (layer + blend functions) on a MaterialInstanceConstant programmatically — via… |

## E — Geometry and meshes

Full entries: [work/E_geometry_meshes.md](work/E_geometry_meshes.md) — plus 10 cited negative results and 6 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `mesh_op` | 1 | 5/4/2 | self-managed | — | GeometryScriptingCore, plugin:GeometryScripting | works | CONFIRMED | Single dispatcher applying one allowlisted GeometryScript operation to a named mesh — ~80 engine functions vi… |
| `commit_dynamic_mesh` | 1 | 5/3/2 | self-managed | `asset_compile_status` | GeometryScriptingCore, plugin:GeometryScripting | refuses | CONFIRMED | Write a pooled mesh's geometry into a UStaticMesh asset LOD — bridge from procedural compute to real, placeab… |
| `copy_from_static_mesh` | 1 | 5/3/2 | self-managed | — | GeometryScriptingCore, plugin:GeometryScripting | degraded | CORRECTED | Load an existing StaticMesh (or SkeletalMesh) LOD into a pooled dynamic mesh for measurement or derivation. |
| `create_static_mesh_asset` | 1 | 5/2/2 | self-managed | `asset_compile_status` | GeometryScriptingEditor, plugin:GeometryScripting | works | CONFIRMED | Create a brand-new UStaticMesh asset from a pooled mesh — the mod-content authoring path, no cooked-asset res… |
| `set_static_mesh_collision_from_mesh` | 1 | 4/3/2 | self-managed | — | GeometryScriptingCore, plugin:GeometryScripting | refuses | CONFIRMED | Generate simple collision (boxes/spheres/capsules/convex/swept hulls) for a StaticMesh asset from any pooled… |
| `set_static_mesh_lods` | 1 | 4/3/2 | self-managed | `asset_compile_status` | StaticMeshEditor | refuses | CONFIRMED | Generate reduction LOD chain for a StaticMesh with per-LOD triangle percentages and screen sizes. |
| `build_static_mesh` | 1 | 4/2/2 | self-managed | `asset_compile_status` | — | refuses | CONFIRMED | Explicit renderable-data rebuild after property-level edits — the missing apply step; plus a global async-com… |
| `create_dynamic_mesh` | 1 | 5/2/1 | self-managed | — | GeometryFramework | works | CORRECTED | Allocate a named transient UDynamicMesh working object — opens the entire GeometryScript pipeline to the agen… |
| `create_physics_asset` | 1 | 4/2/2 | self-managed | — | SkeletalMeshEditor | works | CONFIRMED | Generate a UPhysicsAsset for a SkeletalMesh as if created through FBX import — one call, no UI. |
| `mesh_asset_info` | 1 | 5/2/1 | read-only | — | StaticMeshEditor | degraded | CONFIRMED | One read-only report of a StaticMesh asset's numeric state — LODs, verts, UV channels, collision counts, Nani… |
| `mesh_query` | 1 | 5/2/1 | read-only | — | GeometryScriptingCore, plugin:GeometryScripting | works | CONFIRMED | Numeric inspection of a pooled mesh — the verification story for every mesh_op (agent cannot see the viewport… |
| `set_convex_collision` | 1 | 4/2/2 | self-managed | — | StaticMeshEditor | refuses | CORRECTED | Auto-convex decomposition collision (V-HACD) for one or many StaticMeshes — the quality option beyond primiti… |
| `skeletal_mesh_info` | 1 | 4/2/1 | read-only | — | SkeletalMeshEditor | degraded | CORRECTED | Read-only numeric report on a SkeletalMesh asset — LODs, verts/sections, morph targets, sockets. First skelet… |
| `add_simple_collision` | 1 | 4/1/1 | transacted | — | StaticMeshEditor | refuses | CONFIRMED | Add primitive simple-collision shapes (box/sphere/capsule/kDOP) to a StaticMesh — replicates Collision > Add… |
| `list_dynamic_meshes` | 1 | 3/1/1 | read-only | — | GeometryFramework | works | CONFIRMED | Enumerate live mesh handles with numeric state — the leak detector and session inspector. |
| `release_dynamic_mesh` | 1 | 3/1/1 | self-managed | — | GeometryFramework | works | CONFIRMED | Return a mesh (or all meshes) to the pool — explicit lifetime end, prevents GC-rooted leaks. |
| `merge_static_mesh_actors` | 2 | 4/4/3 | self-managed | — | MeshMergeUtilities | works | CONFIRMED | Merge multiple placed StaticMeshComponents into ONE new StaticMesh asset — the editor Merge Actors tool, head… |
| `generate_uv_channel` | 2 | 3/2/2 | transacted | — | StaticMeshEditor | refuses | CORRECTED | Asset-level UV projection (planar/cylindrical/box) plus UV channel add/insert/remove on a StaticMesh LOD. |
| `regenerate_skeletal_lods` | 2 | 3/2/2 | self-managed | `asset_compile_status` | SkeletalMeshEditor | refuses | CONFIRMED | (Re)generate a skeletal mesh LOD chain via the built-in reducer. |
| `set_lod_build_settings` | 2 | 3/2/2 | transacted | — | StaticMeshEditor | refuses | CONFIRMED | Set per-LOD build options — lightmap UV generation, normals/tangents recompute, remove degenerates — then reb… |
| `set_nanite_settings` | 2 | 3/2/2 | self-managed | `asset_compile_status` | StaticMeshEditor | refuses | CONFIRMED | Enable/disable/tune Nanite on a StaticMesh asset (with build trigger) — per-asset Nanite control not reachabl… |
| `skeletal_mesh_sockets` | 2 | 3/2/1 | transacted | — | SkeletalMeshEditor | works | CORRECTED | Create/rename/remove sockets on a SkeletalMesh asset (attach points for props/weapons — direct DDS2 modding u… |
| `static_mesh_sockets` | 2 | 3/2/1 | transacted | — | — | works | CONFIRMED | List/create/remove sockets on a StaticMesh asset (attachment points for spawn logic). |

## F — World and level

Full entries: [work/F_world_level.md](work/F_world_level.md) — plus 8 cited negative results and 5 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `apply_spline_to_landscape` | 0 | 5/4/4 | transacted | — | — | degraded | CORRECTED | Deform (and optionally paint) the landscape along any existing USplineComponent — road/river bed from an auth… |
| `list_sublevels` | 1 | 4/5/5 | read-only | — | — | works | CORRECTED | Enumerate every streaming level of the open world with load/visibility state; also reports whether the world… |
| `add_sublevel` | 1 | 5/4/4 | self-managed | — | — | refuses | CORRECTED | Add an existing level package to the open world as a streaming sublevel with optional transform. |
| `export_heightmap` | 1 | 4/4/5 | read-only | — | ImageWrapper | works | CONFIRMED | Dump landscape height data (whole extent or window) to a 16-bit file for numeric inspection/diff. |
| `list_foliage` | 1 | 4/4/5 | read-only | — | — | degraded | CORRECTED | Enumerate foliage types present in the world with instance counts — the reader that makes foliage mutations v… |
| `set_current_sublevel` | 1 | 4/5/4 | transacted | — | — | works | CORRECTED | Route all subsequent spawn output into a chosen sublevel; without it everything lands in the persistent level. |
| `apply_landscape_splines` | 1 | 4/4/4 | transacted | — | — | degraded | CONFIRMED | Rasterise all landscape splines into heightmap/weightmaps (the Deform Landscape to Splines button). |
| `export_weightmap` | 1 | 3/4/5 | read-only | — | ImageWrapper | works | CONFIRMED | Dump a paint layer's weights (0-255) to an 8-bit PNG — numerically verifies paint/spline corridors. |
| `list_landscape_splines` | 1 | 3/4/5 | read-only | — | — | works | CONFIRMED | Read back every landscape-spline control point and segment — verification read for create_landscape_spline. |
| `set_sublevel_visibility` | 1 | 3/5/4 | transacted | — | — | works | CONFIRMED | Show/hide a sublevel in the editor viewport and set runtime loaded/visible flags; lighting-scenario workflows. |
| `paint_foliage` | 1 | 4/3/4 | transacted | — | — | degraded | CONFIRMED | Add instances to the real foliage system (AInstancedFoliageActor + FFoliageInfo) for a UFoliageType. |
| `create_landscape_spline` | 1 | 5/2/3 | transacted | — | — | degraded | CONFIRMED | Author a real landscape spline (control points + segments) from a world-space point list — the native roads/r… |
| `create_sublevel` | 1 | 4/3/3 | self-managed | — | — | n/a | CONFIRMED | Create a brand-new empty streaming level in the open world and save it to a package path in one call, no Save… |
| `import_heightmap` | 1 | 4/3/3 | transacted | — | ImageWrapper | degraded | CONFIRMED | Push a 16-bit heightmap file onto an existing landscape region — round-trip partner of export_heightmap. |
| `remove_foliage_instances` | 1 | 3/3/4 | transacted | — | — | refuses | CORRECTED | Delete foliage instances by type and/or area — clearing a building footprint before placement. |
| `remove_sublevel` | 1 | 3/4/3 | self-managed | — | — | works | CORRECTED | Detach a streaming sublevel from the open world (asset stays on disk) — inverse of add_sublevel. |
| `set_water_body_profile` | 1 | 4/3/3 | transacted | — | Water | degraded | CONFIRMED | Set per-point water parameters (depth, river width, flow velocity) living in UWaterSplineMetadata curves. |
| `list_data_layers` | 2 | 3/4/5 | read-only | — | — | works | CONFIRMED | Read-only census of World Partition data layers (name, runtime state, visibility) in the open world. |
| `build_reflection_captures` | 2 | 3/4/3 | self-managed | — | — | works | CORRECTED | Recapture every reflection capture in the world after geometry/lighting changes; reports capture count. |
| `create_foliage_type` | 2 | 3/3/4 | self-managed | — | — | n/a | CONFIRMED | Author a UFoliageType_InstancedStaticMesh asset (density/scale/alignment rules around a mesh) as a reusable b… |
| `create_grass_type` | 2 | 2/4/4 | self-managed | — | — | n/a | CORRECTED | Author a ULandscapeGrassType asset (mesh + density + placement rules) so scratch-landscape materials can emit… |
| `set_sublevel_streaming` | 2 | 3/4/3 | self-managed | — | — | degraded | CORRECTED | Change a sublevel's streaming class (always-loaded vs dynamic) and its level transform without re-adding the… |
| `move_actors_to_sublevel` | 2 | 3/3/3 | self-managed | — | — | degraded | CONFIRMED | Rehome existing placed actors into a sublevel with a numeric moved/failed report. |
| `resimulate_procedural_foliage` | 2 | 3/2/3 | self-managed | — | — | degraded | CONFIRMED | Run a procedural foliage simulation and spawn its instances into the world — biome-scale vegetation from one… |
| `create_level_instance` *(DEMOTED)* | 2 | 3/2/2 | self-managed | — | — | degraded | DEMOTED | Pack a set of placed actors into a Level Instance (reusable sub-level actor, the modern prefab). |

## G1 — AI, navigation, NPC routing

Full entries: [work/G1_ai_navigation.md](work/G1_ai_navigation.md) — plus 7 cited negative results and 5 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `pie_move_status` | 0 | 5/2/5 | read-only | — | — | works | CONFIRMED | The poll half of pie_move_pawn — numeric progress (status, leg index, remaining path length/cost) of a pawn's… |
| `find_path` | 0 | 5/1/5 | read-only | — | — | works | CONFIRMED | Synchronous point-to-point pathfinding returning path points + length + partial flag — the numeric verificati… |
| `pie_move_pawn` | 0 | 5/3/3 | self-managed | `pie_move_status` | — | works | CONFIRMED | Issue a tracked, tunable nav move (single dest or multi-point route) to an AI pawn in PIE — the walking-NPC e… |
| `navmesh_tile_info` | 1 | 4/2/5 | read-only | — | — | works | CONFIRMED | Per-tile poly counts and bounds — turns nav_status's single tiles number into a spatial map exposing half-emp… |
| `add_nav_modifier_volume` | 1 | 4/2/4 | transacted | — | — | degraded | CONFIRMED | Place a volume stamping a UNavArea (cost/Null/Obstacle) onto the navmesh region it overlaps — 'never path thr… |
| `list_blackboard_keys` | 1 | 3/1/5 | read-only | — | — | works | CONFIRMED | Enumerate a UBlackboardData asset's keys (own + inherited) with types — required reading before BT-adjacent w… |
| `project_to_navmesh` | 1 | 3/1/5 | read-only | — | — | works | CONFIRMED | Snap an arbitrary world point onto the navmesh (or report it cannot be) — pre-flight check turning 'pawn refu… |
| `random_reachable_point` | 1 | 3/1/5 | read-only | — | — | works | CONFIRMED | Generate random reachable navmesh locations from an origin — the primitive for scattering ambient NPCs to wan… |
| `list_nav_areas` | 1 | 2/1/5 | read-only | — | — | works | CONFIRMED | Enumerate every loaded UNavArea class with cost numbers so area-class params elsewhere take a validated name,… |
| `pie_stop_move` | 1 | 3/1/4 | self-managed | — | — | works | CONFIRMED | Abort/pause/resume a pawn's current move or route cleanly and clear the plugin-side route queue. |
| `add_nav_link` | 2 | 4/3/4 | transacted | — | — | degraded | CONFIRMED | Spawn an ANavLinkProxy with validated simple point links — connects navmesh islands (stairs, ledges, doorways… |
| `pie_get_perception` | 2 | 3/2/5 | read-only | — | — | works | CONFIRMED | Read-only dump of what an AI currently/ever perceives in PIE — turns 'the guard doesn't react' into actor lis… |
| `run_eqs_query` | 2 | 3/3/4 | self-managed | `eqs_query_status` | — | works | CORRECTED | Run an existing UEnvQuery asset and read back scored locations — numeric ground truth for 'where would the AI… |
| `pie_possess` | 2 | 4/2/3 | self-managed | — | — | works | CONFIRMED | Fix the #1 reason moves no-op — a controllerless pawn — by spawning its default AI controller or handing it t… |
| `add_blackboard_key` | 2 | 2/3/3 | transacted | — | — | refuses | CONFIRMED | Add a typed key to a loose UBlackboardData asset with duplicate/type/parent-chain validation and correct Inst… |
| `nav_raycast` | 2 | 2/1/5 | read-only | — | — | works | CONFIRMED | 2D navigable-space raycast — is there a straight walkable line from A to B; distinguishes detour-needed from… |
| `create_blackboard_asset` | 3 | 2/2/4 | self-managed | — | — | works | CONFIRMED | Create a new empty UBlackboardData asset (optionally with parent) so add_blackboard_key has a loose target. |

## G2 — Sequencer, UMG extras, Enhanced Input

Full entries: [work/G2_sequencer_umg_input.md](work/G2_sequencer_umg_input.md) — plus 8 cited negative results and 3 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `list_widget_tree` | 0 | 5/4/5 | read-only | — | — | degraded | CONFIRMED | One-call enumeration of a Widget Blueprint's whole widget hierarchy — closes roadmap gap 'UMG: no one-call tr… |
| `reparent_widget` | 0 | 5/3/3 | transacted | — | — | refuses | CONFIRMED | Move an existing widget to a new parent panel and/or child index WITHOUT destroying it. |
| `describe_sequence` | 1 | 5/3/5 | read-only | — | MovieScene | works | CORRECTED | One-call structured dump of any UMovieSceneSequence: ranges, rates, bindings with GUIDs, tracks, sections, ch… |
| `list_input_mappings` | 1 | 3/5/5 | read-only | — | EnhancedInput | works | CONFIRMED | Read back an InputMappingContext: every (action, key, modifiers, triggers) row — verification twin of input_m… |
| `input_map_key` | 1 | 4/4/3 | transacted | — | EnhancedInput | refuses | CONFIRMED | Append a key-to-action mapping to an InputMappingContext — the one structural edit set_property cannot expres… |
| `create_input_action` | 1 | 3/4/3 | self-managed | — | EnhancedInput | n/a | CONFIRMED | Mint a UInputAction asset — closes the roadmap gap 'no non-Blueprint asset creation (no InputAction)'. |
| `create_input_mapping_context` | 1 | 3/4/3 | self-managed | — | EnhancedInput | n/a | CONFIRMED | Mint a UInputMappingContext asset — shared implementation with create_input_action, same minting pattern as c… |
| `create_widget_animation` | 1 | 4/3/3 | transacted | — | MovieScene | refuses | CORRECTED | Add a UWidgetAnimation to a Widget Blueprint — the container that makes UMG animation authoring possible at a… |
| `sequence_add_section` | 1 | 4/3/3 | transacted | — | MovieScene | refuses | CONFIRMED | Give a track an actual section with a frame range — tracks evaluate nothing without one. |
| `sequence_add_track` | 1 | 4/3/3 | transacted | — | MovieScene, MovieSceneTracks | refuses | CORRECTED | Add a typed track (transform, float/bool property, visibility, UMG 2DTransform/Margin) to a binding or sequen… |
| `sequence_bind_actor` | 1 | 4/3/3 | transacted | — | MovieScene, LevelSequence | refuses | CONFIRMED | Bind a placed level actor into a sequence as a possessable or spawnable, returning the binding GUID track end… |
| `sequence_set_keys` | 1 | 5/2/3 | transacted | — | MovieScene | refuses | CONFIRMED | Batch-write keys into a section's channels — transform XYZ, float/double properties, bools — turning the obje… |
| `widget_animation_bind` | 1 | 4/3/3 | transacted | — | MovieScene | refuses | CORRECTED | Bind a named widget (or widget slot) into a widget animation and return the possessable GUID. |
| `create_level_sequence` | 1 | 5/2/2 | self-managed | — | LevelSequence, MovieScene | n/a | CONFIRMED | Mint a new, saveable ULevelSequence asset at a given package path — currently no cinematic can be authored at… |
| `rename_widget` | 1 | 4/2/2 | transacted | — | — | refuses | CORRECTED | Rename a widget preserving identity — variable references, delegate bindings, animation bindings. |
| `open_sequence_editor` | 2 | 3/4/4 | self-managed | — | LevelSequenceEditor | works | CONFIRMED | Open (or close) a level sequence in the Sequencer editor so a human or capture_camera can see what the agent… |
| `sequence_editor_play` | 2 | 3/4/3 | self-managed | — | LevelSequenceEditor | works | CONFIRMED | Drive the open Sequencer: play/pause/scrub/speed — step a cinematic to a frame and capture_camera it. |
| `render_movie_request` | 2 | 4/2/2 | self-managed | `render_movie_status` | MovieRenderPipelineCore, MovieRenderPipelineEditor | works | CORRECTED | Queue a Movie Render Pipeline job for a sequence+map — turns authored cinematics into video/image output, hea… |
| `render_movie_status` | 2 | 4/2/2 | self-managed | — | MovieRenderPipelineCore, MovieRenderPipelineEditor | works | CORRECTED | Poll a queued Movie Render Pipeline job: {isRendering, finished, success, outputDirectory, filesWritten}. |

## G3 — Niagara, audio, physics

Full entries: [work/G3_niagara_audio_physics.md](work/G3_niagara_audio_physics.md) — plus 8 cited negative results and 5 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `create_niagara_system` | 1 | 4/3/2 | self-managed | `niagara_compile_status` | Niagara, NiagaraEditor | degraded | CORRECTED | Create a new, immediately-usable NiagaraSystem asset, optionally seeded from an emitter asset, without the te… |
| `set_niagara_user_parameter` | 1 | 5/2/2 | transacted | — | Niagara | works | CONFIRMED | Set a User.* parameter on any UNiagaraComponent — user params live in a serialized parameter store, unreachab… |
| `create_sound_cue` | 1 | 4/2/2 | self-managed | — | — | works | CONFIRMED | Create a SoundCue asset with an optional ready-wired wave-player chain — the 80% case for mod audio, matching… |
| `get_niagara_particle_counts` | 1 | 5/2/1 | read-only | — | Niagara | works | CORRECTED | Per-emitter live particle counts plus execution state for any active Niagara component — the axis's numeric v… |
| `niagara_compile_request` | 1 | 4/2/2 | self-managed | `niagara_compile_status` | Niagara | refuses | CONFIRMED | Request (re)compilation of a NiagaraSystem's scripts after authoring mutations and poll completion — compilat… |
| `spawn_niagara_component` | 1 | 4/2/2 | self-managed | — | Niagara | works | CONFIRMED | Spawn a one-shot/pooled Niagara FX component at a location in the editor or PIE world — runtime spawn path sp… |
| `get_niagara_user_parameters` | 1 | 4/2/1 | read-only | — | Niagara | works | CONFIRMED | Enumerate a component's or system's user parameters with types and current values — the read/verification pai… |
| `play_sound_preview` | 1 | 4/1/1 | self-managed | `sound_preview_status` | — | works | CONFIRMED | Audition any USoundBase (cue, wave, MetaSound source) through the editor's preview audio component — no viewp… |
| `set_niagara_component_active` | 1 | 3/1/1 | self-managed | — | Niagara | works | CONFIRMED | Activate / deactivate / reset a Niagara component so an agent can drive FX state during PIE verification with… |
| `create_metasound_source` | 2 | 4/4/3 | self-managed | — | MetasoundEngine, MetasoundFrontend | works | CONFIRMED | Create a playable MetaSoundSource (mono/stereo, one-shot or looping, optionally wave-backed) via the 5.3 docu… |
| `add_niagara_emitter` | 2 | 3/3/3 | self-managed | — | NiagaraEditor, Niagara | refuses | CORRECTED | Add an emitter (copied from an emitter asset) to an existing loose NiagaraSystem — the one structural authori… |
| `create_physics_asset` *(merged — see dedup table)* | 2 | 4/3/2 | self-managed | — | PhysicsUtilities | degraded | CORRECTED | Generate a PhysicsAsset (bodies + constraints) from a skeletal mesh — from-scratch ragdoll/collision setup; 1… |
| `add_sound_cue_node` | 2 | 3/3/2 | transacted | — | — | refuses | CONFIRMED | Extend an existing loose SoundCue's node tree (attenuation/random/mixer/looping/wave player under a named par… |
| `set_physics_constraint` | 2 | 3/2/2 | transacted | — | — | works | CONFIRMED | Wire a physics constraint component to its two bodies with correct init order — set_property on ComponentName… |
| `create_geometry_collection` | 3 | 2/3/3 | self-managed | — | GeometryCollectionEngine | refuses | CONFIRMED | Build a GeometryCollection from static meshes — Chaos destruction entry asset; game ships zero, no fracture,… |

## H — Data, curves, localization, config, savegames

Full entries: [work/H_data.md](work/H_data.md) — plus 10 cited negative results and 6 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `read_curve` | 0 | 4/1/5 | read-only | — | — | works | CONFIRMED | Read keys AND numerically evaluate a curve at N sample points — the verification half of set_curve_keys. |
| `set_curve_keys` | 0 | 5/2/1 | transacted | — | — | degraded | CORRECTED | Structured key editing on any FRichCurve-bearing asset — closes the element-level-addressing gap for curves. |
| `get_config_value` | 1 | 4/1/5 | read-only | — | — | n/a | CONFIRMED | Structured read of any .ini value or whole section (merged runtime view or specific Default*.ini file). |
| `create_datatable` | 1 | 5/2/2 | self-managed | — | — | works | CORRECTED | Create a new UDataTable asset with a chosen RowStruct — rows can be written today but no table can be created. |
| `export_datatable_csv` | 1 | 3/1/5 | read-only | — | — | works | CONFIRMED | Round-trip tables through the CSV format designers/Excel use — read_datatable only emits JSON. |
| `list_primary_assets` | 1 | 3/1/5 | read-only | — | — | works | CONFIRMED | Read-only dump of UAssetManager primary asset types and ids — shows how DDS2 organizes scannable content. |
| `list_string_table_entries` | 1 | 3/1/5 | read-only | — | — | works | CONFIRMED | Dump all key/source-string pairs (+namespace) of a string table — works on cooked base-game text too. |
| `create_curve` | 1 | 4/2/2 | self-managed | — | — | works | CONFIRMED | Create UCurveFloat/UCurveVector/UCurveLinearColor assets — no endpoint can make a standalone curve asset. |
| `set_struct_member` | 1 | 4/2/2 | self-managed | — | — | refuses | CONFIRMED | Retype/re-default/rename an EXISTING UserDefinedStruct member in place — remove+re-add breaks graph pins toda… |
| `create_string_table` | 1 | 4/1/2 | self-managed | — | — | works | CONFIRMED | Create a UStringTable asset — first step of a localization-ready text pipeline. |
| `import_datatable_csv` | 1 | 4/1/2 | transacted | — | — | degraded | CONFIRMED | Bulk-fill a table from CSV text (designer-facing interchange); complements JSON-only write paths. |
| `delete_datatable_row` | 1 | 4/1/1 | transacted | — | — | degraded | CONFIRMED | Remove ONE row; today the only deletion path is whole-table replace (read-all/rewrite-all). |
| `set_string_table_entry` | 1 | 4/1/1 | transacted | — | — | degraded | CONFIRMED | Add/update/remove source strings in a string table — makes agent-authored UI text localizable. |
| `duplicate_datatable_row` | 1 | 3/1/1 | transacted | — | — | degraded | CONFIRMED | Clone a row under a new key — the natural make-a-variant-item/recipe primitive for a data-driven game. |
| `rename_datatable_row` | 1 | 3/1/1 | transacted | — | — | degraded | CONFIRMED | Rename a row key in place preserving data + order (currently impossible without full replace). |
| `list_savegames` | 2 | 3/1/5 | read-only | — | — | n/a | CONFIRMED | Enumerate save-game files (name, size, timestamp) under the project's Saved/SaveGames dir. |
| `read_savegame` | 2 | 3/2/4 | read-only | — | — | n/a | CONFIRMED | Load a standard UE .sav slot and dump the USaveGame object's properties via the reflection serializer. |
| `get_settings_config_source` | 2 | 2/1/5 | read-only | — | DeveloperSettings | n/a | CONFIRMED | For any UDeveloperSettings class, report which ini file + section its values live in. |
| `list_cultures` | 2 | 2/1/5 | read-only | — | — | n/a | CONFIRMED | Enumerate cultures known to the engine's ICU data — read-only grounding for localization work. |
| `set_config_value` | 2 | 3/2/3 | self-managed | — | — | n/a | CONFIRMED | Write a config value to a Default*.ini with explicit flush — make-this-setting-stick, currently hand-edit onl… |
| `create_curve_table` | 2 | 3/2/2 | self-managed | — | — | works | CORRECTED | Create a UCurveTable asset and optionally bulk-fill from CSV — curve tables are currently untouchable. |
| `set_composite_datatable_parents` | 2 | 3/2/2 | transacted | — | — | degraded | CONFIRMED | Author UCompositeDataTable parent stacks (DLC/patch row overlays) — the only meaningful composite mutation. |
| `move_struct_member` | 2 | 2/1/2 | self-managed | — | — | refuses | CONFIRMED | Reorder UserDefinedStruct members (pairs with set_struct_member to close in-place struct editing). |
| `move_datatable_row` | 2 | 2/1/1 | transacted | — | — | degraded | CORRECTED | Reorder rows (row order matters for iteration order and designer diffing; unreachable today). |
| `read_rama_savefile` | 3 | 3/2/3 | read-only | — | — | n/a | CONFIRMED | Read the static-data header of a DDS2 gameplay save (RamaSaveSystem format) — the format the game actually us… |

## I — Diagnostics and observation

Full entries: [work/I_diagnostics.md](work/I_diagnostics.md) — plus 8 cited negative results and 4 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `get_perf_stats` | 1 | 4/4/5 | read-only | — | RHI | works | CONFIRMED | One read-only call returning numeric frame-health: FPS, per-thread ms, draw calls, object counts, GC recency,… |
| `get_properties_bulk` | 1 | 4/4/5 | read-only | — | — | works | CONFIRMED | Read up to 200 object/property pairs in one call — the watch-list primitive for PIE observation loops, withou… |
| `list_automation_tests` | 1 | 4/4/5 | read-only | — | — | works | CONFIRMED | Enumerate every registered automation test with flags and source locations — prerequisite for running any of… |
| `message_log_read` | 1 | 4/4/5 | read-only | — | MessageLog | works | CONFIRMED | Structured read of editor Message Log listings (MapCheck, PIE, BlueprintLog, ...) — where map_check and PIE w… |
| `pie_resolve_path` | 1 | 4/4/5 | read-only | — | — | works | CONFIRMED | Convert an editor object path to its live PIE counterpart (and back) deterministically, with optional existen… |
| `log_tail` | 1 | 5/3/4 | read-only | — | — | works | CONFIRMED | Incremental structured tail of the editor process log via a GLog ring-buffer device with monotonic sequence i… |
| `world_state_hash` | 2 | 3/4/5 | read-only | — | — | works | CONFIRMED | One deterministic CRC summarizing world state (actor set + quantized transforms + loaded levels) — the reprod… |
| `trace_start` | 2 | 3/4/4 | self-managed | — | — | works | CONFIRMED | Record an Unreal Insights .utrace of an agent-triggered workload (channel-selectable); trace_stop/trace_statu… |
| `screenshot_request` | 2 | 3/3/4 | self-managed | `screenshot_status` | — | works | CONFIRMED | Capture what is actually on the editor screen — active viewport incl. PIE frame and Slate UI — as a file; dis… |
| `run_automation_test` | 2 | 4/2/3 | self-managed | `automation_status` | — | works | CORRECTED | Run one automation test in-process and get pass/fail, error entries, and duration as numbers via request+poll… |

## J — DDS2 project-specific

Full entries: [work/J_dds2_project.md](work/J_dds2_project.md) — plus 10 cited negative results and 5 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `list_native_classes` | 1 | 4/5/5 | read-only | — | — | works | CONFIRMED | Enumerate the native class surface of a /Script module — today describe_class needs a name the agent cannot d… |
| `list_ue4ss_mods` | 1 | 4/5/5 | read-only | — | — | n/a | CONFIRMED | One call answering what mods are installed/enabled/deployed and whether a deploy actually landed, across four… |
| `read_modloader_log` *(behaviour change)* | 1 | 3/5/5 | read-only | — | — | n/a | CONFIRMED | Structured events instead of raw lines from UE4SS.log — agents currently regex a text blob. |
| `verify_pak_contents` | 1 | 4/4/5 | read-only | — | — | works | CONFIRMED | Enumerate what is actually inside a .utoc/.ucas (or retoc trio) before/after deploying a mod, instead of ship… |
| `refresh_asset_registry` | 2 | 3/4/3 | self-managed | — | — | degraded | CONFIRMED | Make the asset registry learn about new loose files dropped into /Game/MODS out-of-editor, without restarting. |
| `mod_package_request` | 2 | 5/2/2 | self-managed | `mod_package_status` | — | n/a | CONFIRMED | Actually execute the retoc pack/deploy lane that trigger_cook only plans, from the editor session, with a pol… |
| `mount_pak` | 2 | 4/3/2 | self-managed | — | PakFile | works | CONFIRMED | Mount a mod's retoc trio (or any pak/IoStore container) into the running editor so its packages become loadab… |
| `unmount_pak` | 3 | 2/3/1 | self-managed | — | PakFile | works | CONFIRMED | Undo mount_pak in the same session (iterate: repack then remount). |

## K1 — Reconstructor toolkit (read/analysis) as kr_* endpoints

Full entries: [work/K1_reconstructor_toolkit.md](work/K1_reconstructor_toolkit.md) — plus 11 cited negative results and 5 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `kr_disassemble_function` | 0 | 5/4/5 | read-only | — | <provider:MifKismetReconstructor> | primary | CONFIRMED | Full paginated JSON statement stream for ONE function of a cooked BPGC — what an agent reads before deciding… |
| `kr_dump_blueprint` | 0 | 5/4/5 | read-only | — | <provider:MifKismetReconstructor> | primary | CONFIRMED | mif.kr.DumpBP as inline JSON: per-function bytecode inventory + opcode histogram for a cooked BPGC — first HT… |
| `kr_list_cooked_blueprints` | 1 | 3/5/5 | read-only | — | — | exists | CONFIRMED | Registry census of cooked Blueprints (PKG_Cooked-filtered, package-deduped, loaded flag) so an agent can size… |
| `kr_list_events` | 1 | 4/3/4 | read-only | — | <provider:MifKismetReconstructor> | primary | CONFIRMED | Event census of a cooked BP: every thunk with kind, recovered ubergraph entry offset, param count, and frame-… |
| `kr_analyze_ubergraph` | 1 | 3/3/4 | read-only | — | <provider:MifKismetReconstructor> | primary | CONFIRMED | Per-BP ubergraph slice stats as JSON: prologue shape, per-event reachability, shared/unreached counts, SHARED… |
| `kr_reconstruct_function` *(merged — see dedup table)* | 1 | 4/3/3 | self-managed | — | <provider:MifKismetReconstructor> | primary | CONFIRMED | Decompile ONE cooked function into a real compilable Blueprint graph with structured results — mif.kr.Reconst… |
| `kr_batch_reconstruct_status` *(merged — see dedup table)* | 2 | 3/5/5 | read-only | `kr_batch_reconstruct_request` | — | n/a | CONFIRMED | Poll the batch job: progress, live tallies, aggregate fidelity so far, current BP (crash-culprit signal), CSV… |
| `kr_pin_type_from_property` | 2 | 2/5/5 | read-only | — | <provider:MifKismetReconstructor> | works | CONFIRMED | Property path or (class, property) to exact FEdGraphPinType JSON — pre-compute pin type strings add_variable/… |
| `kr_verify_fidelity` *(merged — see dedup table)* | 2 | 4/2/3 | self-managed | — | — | primary | CORRECTED | Release-gate metric over HTTP: throwaway child reconstruct + recompile + cooked-vs-recompiled diff as FBluepr… |
| `kr_batch_reconstruct_request` *(merged — see dedup table)* | 2 | 3/2/3 | self-managed | `kr_batch_reconstruct_status` | — | target | CONFIRMED | mif.kr.ReconstructAll corpus regression sweep as a background job: reconstruct every cooked BP throwaway, com… |

## K2 — Reconstructor pipeline, verify, coupling model

Full entries: [work/K2_reconstructor_pipeline.md](work/K2_reconstructor_pipeline.md) — plus 9 cited negative results and 5 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `kr_reconstruct_status` | 0 | 4/4/5 | read-only | — | provider:MifKismetReconstructor | n/a | CONFIRMED | Poll the single kr job slot: phase, per-function/event done-counts, node counts, compile numbers, and final r… |
| `kr_reconstruct_request` | 0 | 5/2/3 | self-managed | `kr_reconstruct_status` | provider:MifKismetReconstructor | the | CONFIRMED | Start an async job decompiling a cooked BP's bytecode into editable K2 graphs (whole copy or single function)… |
| `kr_verify_fidelity` | 1 | 5/2/3 | self-managed | `kr_reconstruct_status` | provider:MifKismetReconstructor | designed | CORRECTED | Reconstruct+compile a throwaway transient child of a cooked BP, diff recompiled vs cooked bytecode, return th… |
| `kr_classify_drift` | 2 | 3/2/3 | self-managed | `kr_reconstruct_status` | provider:MifKismetReconstructor | as | CONFIRMED | Per-function drift verdicts for one BP: verdict class, deduped intentional reasons, and root-cause first-uncl… |
| `kr_drift_census` | 2 | 3/2/3 | self-managed | `kr_reconstruct_status` | provider:MifKismetReconstructor | cooked-only | CONFIRMED | Fidelity verify across a path-filtered set of cooked BPs, sliced one BP per tick, with live totals over HTTP… |

## P1 — Graph auto-layout & BP-text plugins

Full entries: [work/P1_graph_layout.md](work/P1_graph_layout.md) — plus 8 cited negative results and 5 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `format_graph` | 0 | 5/3/4 | transacted | — | — | cooked | CORRECTED | Deterministic headless Sugiyama-lite layered-DAG layout of a K2 graph — fixes '40 spawned nodes heaped at ori… |
| `export_graph_text` | 1 | 3/5/5 | read-only | — | — | stripped | CORRECTED | Lossless T3D (clipboard-format) export of a graph or node subset — exact pin defaults, positions, classes; ex… |
| `fit_comment_to_nodes` | 1 | 3/4/4 | transacted | — | AutoSizeComments | loose/reconstructed | CORRECTED | Compute union rect of a node set and set comment bounds + membership so agent-made comments behave like human… |
| `format_graph_ba_status` | 2 | 4/4/5 | read-only | `format_graph_ba_request` | BlueprintAssist, plugin:BlueprintAssist | n/a | CONFIRMED | Poll the Plan-A BA format job — phase, size progress, transaction state, moved-node diff vs request-time snap… |
| `format_graph_ba_request` | 2 | 4/3/2 | self-managed | `format_graph_ba_status` | BlueprintAssist, plugin:BlueprintAssist | refuses | CONFIRMED | Pixel-quality format identical to pressing BlueprintAssist Format-All — real measured sizes; Plan A, requires… |

## P2 — Oceanology / Riverology / FGear (stub SDK finding)

Full entries: [work/P2_world_vehicle_plugins.md](work/P2_world_vehicle_plugins.md) — plus 9 cited negative results and 6 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `call_object_function` | 1 | 5/3/3 | transacted | — | — | WORKS | CONFIRMED | Invoke a named UFUNCTION on any objectPath via reflection — the only lane that executes cooked-BP bytecode (e… |

## P3 — GameFeatures state, thumbnails, misc plugins

Full entries: [work/P3_sessions_misc_plugins.md](work/P3_sessions_misc_plugins.md) — plus 14 cited negative results and 4 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `get_game_feature_state` | 1 | 4/4/5 | read-only | — | GameFeatures | unaffected | CONFIRMED | Enumerate every game-feature plugin with exact state-machine state (incl. error states) — machine-readable DL… |
| `render_asset_thumbnail` | 2 | 3/3/3 | read-only | — | — | degraded | CONFIRMED | Render a per-asset Content Browser-style thumbnail PNG so an agent can visually confirm an asset without load… |
| `change_game_feature_state_request` | 2 | 3/3/2 | self-managed | `change_game_feature_state_status` | GameFeatures | transitions | CORRECTED | Drive a game-feature plugin to Installed/Registered/Loaded/Active; completion error code becomes machine-read… |
| `change_game_feature_state_status` | 2 | 3/3/2 | read-only | — | GameFeatures | works | CORRECTED | Poll a pending game-feature state change: {pending, done, ok, errorCode, errorText, currentState, targetState… |

## Q — Known-defect root causes (repairs)

Full entries: [work/Q_gap_rootcauses.md](work/Q_gap_rootcauses.md) — plus 6 cited negative results and 5 UNVERIFIED items there.

| Endpoint | T | U/E/R | Bucket | Async pair | New deps | Cooked | Verdict | Purpose |
|---|---|---|---|---|---|---|---|---|
| `pie_status` *(behaviour change)* | 0 | 5/4/5 | read-only | — | — | works | CORRECTED | Truthful PIE state across full lifecycle (queued/starting/running/simulating/travelling/stopping/stopped) and… |
| `snap_actors_to_ground` *(behaviour change)* | 0 | 4/4/4 | transacted | — | — | works | CONFIRMED | Penetrating ECR_Overlap multi-trace so landscape is reachable through blocking geometry; per-actor diagnosis… |

