# R3 — What is left in MifBridge

_Reconciliation of the 250-entry audit catalogue against what is actually in the running DLL and in
source, 2026-07-28. **Read-only pass** — nothing in this document was built, and no source file was
edited to produce it._

---

## 0. What was measured, and how

| Measurement | Value | How it was obtained |
|---|---|---|
| Live endpoints | **203** (191 built-in + 12 external) | `POST /api/self_audit` — `endpointCount: 203`, `externalEndpointCount: 12`, `externalProviders: [{provider: "MifKismetReconstructor", endpointCount: 12}]` |
| Live DLL build stamp | `Jul 28 2026 22:21:25` | `self_audit.buildDate` / `.buildTime` |
| Engine | `5.3.2-0+++UE5+Release-5.3-CookedEditorModKit` | `self_audit.engineVersion` |
| Live buckets | readOnly **66** · selfManaged **31** · transacted **106** · compileHeavy **33** | `self_audit.transactionBuckets` |
| Registry health | `healthy: true`, `policyContradictions: []` | `self_audit` |
| Source declarations | **191** unique `MIF_DECL(...)` in `MifBridgeHandlers.h` | parsed directly; the 192nd match is the macro definition itself at `MifBridgeHandlers.h:307` — `#define MIF_DECL(Name) void H_##Name(...)` |
| Source bindings | **191** unique `MIF_BIND(...)` in `MifBridgeCommon.cpp` | parsed directly; `MIF_DECL` set ≡ `MIF_BIND` set, **no registry drift** |
| External registrations | **12** `Reg(TEXT("kr_..."))` in `MifKrBridgeEndpoints.cpp` | parsed directly; identical to the live external list |
| Catalogue rows | **250** across 18 axes (246 unique names; 4 names appear on two axes each) | parsed from `01_CATALOGUE.md` |

**Source is ahead of the live DLL.** `MifBridgeHandlers.h`, `MifBridgeCommon.cpp`, `MifBridgeNodes.cpp`,
`MifBridgeNodes5/6.cpp`, `MifBridgeAuthoring.cpp`, `MifBridgeInherited.cpp`, `MifBridgePIE.cpp`,
`MifBridgeSpatial.cpp`, `MifBridgeStreaming.cpp`, `MifBridgeWorld.cpp` and `MifBridgeIntrospect.cpp`
all have mtimes **after** 22:21:25. The endpoint *set* has not moved (191 = 191), but handler
*bodies* have. Everything below that cites a source line is a snapshot at the time of this pass;
everything that cites `self_audit` is the 22:21 build.

### Where the 203 live endpoints came from

```
160  00_BASELINE.md inventory (the audit's declared starting set)
+  5  landed outside the implementation log  ......  get_referencers, get_dependencies,
                                                     audit_unused, delete_datatable_rows,
                                                     add_enhanced_input_action
+  5  Batch C (undo/redo + dirty packages)
+ 10  Batch D (material graph authoring)
+  8  Batch I (level streaming)
+  3  Batch J (inherited component overrides)
= 191 built-in
+ 12  MifKismetReconstructor (Batch R phases 1-2, Wave 3 step 2)
= 203
```

The five "landed outside the log" endpoints are real and live; `00_BASELINE.md` never listed
`get_referencers` / `get_dependencies` / `audit_unused` (its `MifBridgeAssetOps.cpp` table has three
rows: `delete_asset`, `rename_asset`, `duplicate_asset`), and the source comment above them says
*"Added 2026-07-28"* (`MifBridgeAssetOps.cpp:239`). This matches finding 3 in
`07_SELF_AUDIT_FINDINGS.md`.

### Correction to a number that is circulating

`07_SELF_AUDIT_FINDINGS.md` §doc-truth-6 states **"41 catalogue/ranked entries are live today"** and
lists 29 built-ins. That count treats a *behaviour-change* entry as delivered whenever the endpoint
**name** is live. Eight of the 29 are behaviour-change entries, and **none of the eight has actually
had its specced behaviour change land** (six not at all, two partially — proven line-by-line in §1
and §3 below).

**The corrected figure is 34 of 250 catalogue rows delivered** (32 unique endpoint names), plus 2
partially. The eight behaviour-change entries remain open work, and six of them are Tier 0.

---

## 1. Every catalogue entry, with status

**Status vocabulary**

- **SHIPPED** — a live endpoint delivers the entry.
- **SHIPPED (PARTIAL)** — the endpoint exists and part of the specced change landed; the named
  residue has not.
- **SUPERSEDED** — something else covers it; the covering thing is named, with any residue.
- **STILL OPEN** — not delivered. For the eight behaviour-change entries this means *the endpoint
  name exists but the specced behaviour does not*.
- **WITHDRAWN** — will not be built, with the reason.

**Totals over the 250 rows**

| SHIPPED | SHIPPED (PARTIAL) | SUPERSEDED | WITHDRAWN | STILL OPEN |
|---|---|---|---|---|
| 34 | 2 | 9 | 2 | **203** |

### A — Editor core

_20 entries — SHIPPED 5 · STILL OPEN 15_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `list_transactions` | 0 | 5/2/5 | — | **SHIPPED** | live as `list_transactions` |
| `undo_transactions` | 0 | 5/2/3 | — | **SHIPPED** | live as `undo_transactions` |
| `redo_transactions` | 0 | 4/1/3 | — | **SHIPPED** | live as `redo_transactions` |
| `get_cvar` | 1 | 4/1/5 | — | **STILL OPEN** |  |
| `get_viewport_state` | 1 | 3/2/5 | — | **STILL OPEN** |  |
| `list_cvars` | 1 | 3/2/5 | — | **STILL OPEN** |  |
| `list_developer_settings` | 1 | 4/1/5 | DeveloperSettings | **STILL OPEN** |  |
| `list_dirty_packages` | 1 | 4/1/5 | — | **SHIPPED** | live as `list_dirty_packages` |
| `save_dirty_packages` | 1 | 4/3/3 | — | **SHIPPED** | live as `save_dirty_packages` |
| `set_view_mode` | 1 | 4/2/4 | — | **STILL OPEN** |  |
| `list_actor_folders` | 1 | 3/1/5 | — | **STILL OPEN** |  |
| `run_editor_utility` | 1 | 4/2/3 | Blutility | **STILL OPEN** |  |
| `set_actor_folder` | 1 | 3/2/4 | — | **STILL OPEN** |  |
| `group_actors` | 2 | 3/2/4 | — | **STILL OPEN** |  |
| `get_editor_modes` | 2 | 2/1/5 | — | **STILL OPEN** |  |
| `list_layers` | 2 | 2/1/5 | — | **STILL OPEN** |  |
| `modify_actor_layers` | 2 | 2/2/4 | — | **STILL OPEN** |  |
| `pilot_actor` | 2 | 2/2/4 | LevelEditor | **STILL OPEN** |  |
| `select_components` | 2 | 2/2/4 | — | **STILL OPEN** |  |
| `close_asset_editors` | 2 | 2/1/4 | — | **STILL OPEN** |  |

### B — Assets and the registry

_18 entries — SHIPPED 1 · STILL OPEN 14 · SUPERSEDED 2 · WITHDRAWN 1_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `get_asset_dependencies` | 1 | 5/2/5 | — | **SUPERSEDED** | by `get_dependencies` (MifBridgeAssetOps.cpp:292). RESIDUE: no hard/soft/game/build edge classification. |
| `get_asset_referencers` | 1 | 5/2/5 | — | **SUPERSEDED** | by `get_referencers` (MifBridgeAssetOps.cpp:261). RESIDUE: no edge classification, no container attribution. |
| `get_class_hierarchy` | 1 | 5/2/5 | — | **STILL OPEN** |  |
| `create_asset` | 1 | 5/3/3 | — | **STILL OPEN** |  |
| `find_assets_by_tag` | 1 | 4/2/5 | — | **STILL OPEN** |  |
| `get_message_log` | 1 | 4/2/5 | MessageLog | **WITHDRAWN** | DUPLICATE of `message_log_read` (I) — same engine API (MessageLogModule.h:49,57 + IMessageLogListing.h:47), same module, same bucket. Dedup the audit missed. I owns (fuller spec). |
| `import_asset` | 1 | 5/3/3 | AudioEditor | **STILL OPEN** |  |
| `export_asset` | 1 | 4/2/4 | — | **STILL OPEN** |  |
| `get_asset_compilation_status` | 1 | 4/1/5 | — | **STILL OPEN** | ships under the merged name `asset_compile_status` (E owns the name; B owns the spec). |
| `get_asset_tags` | 1 | 4/1/5 | — | **STILL OPEN** |  |
| `list_dirty_packages` | 1 | 4/1/5 | — | **SHIPPED** | live as `list_dirty_packages` |
| `map_check` | 1 | 4/2/4 | — | **STILL OPEN** |  |
| `validate_assets` | 1 | 4/2/4 | DataValidation | **STILL OPEN** |  |
| `list_content_paths` | 1 | 3/1/5 | — | **STILL OPEN** |  |
| `resolve_redirector` | 1 | 3/1/5 | — | **STILL OPEN** |  |
| `consolidate_assets` | 2 | 3/3/2 | — | **STILL OPEN** |  |
| `fixup_redirectors` | 2 | 3/2/3 | — | **STILL OPEN** |  |
| `get_package_disk_data` | 2 | 2/1/5 | — | **STILL OPEN** |  |

### C — Blueprints and graphs

_18 entries — STILL OPEN 17 · SUPERSEDED 1_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `list_components` | 0 | 4/5/5 | — | **STILL OPEN** | **Behaviour change NOT landed.** `MifBridgeComponents.cpp:117-122` still walks `Blueprint->SimpleConstructionScript` / `SCS->GetAllNodes()` only — no inherited, no native, no `source`/`origin` field. (It did gain `templatePath` at `:142`, which is not this entry.) |
| `connect_pins` | 0 | 4/5/4 | — | **STILL OPEN** | **Behaviour change NOT landed.** `MifBridgeCommon.cpp:2052` is still `const UEdGraphSchema_K2* Schema = K2();` — the hardcoded K2 CDO drives `BreakPinLinks` (:2063), `CanCreateConnection` (:2067) and `TryCreateConnection` (:2073). See §4.3. |
| `add_component` | 0 | 5/4/3 | — | **STILL OPEN** | **Behaviour change NOT landed.** `MifBridgeComponents.cpp:82` is still `Parent = SCS->FindSCSNode(FName(*ParentName));` — own SCS only; an inherited or native parent name fails with "parent component not found" (:55). |
| `add_create_delegate` | 0 | 4/4/4 | — | **STILL OPEN** |  |
| `create_macro` | 0 | 4/4/4 | — | **STILL OPEN** |  |
| `list_variables` | 0 | 4/4/4 | — | **STILL OPEN** | **Behaviour change NOT landed.** `MifBridgeIntrospect.cpp:246` iterates `Blueprint->NewVariables` only and hardcodes `scope` to `"member"` at `:251`; `rename_variable` (:898), `remove_variable` (:1026) and `set_variable_default` (:1108) contain no local-scope path. Only `add_variable` (:796,:802) understands `scope=local`. |
| `set_variable_type` | 0 | 5/4/3 | — | **STILL OPEN** |  |
| `add_node_by_class` | 0 | 5/2/3 | — | **STILL OPEN** |  |
| `disassemble_function` | 1 | 5/4/5 | ScriptDisassembler | **SUPERSEDED** | by `kr_disassemble_function` (live, MifKrBridgeEndpoints.cpp:1114). RESIDUE: BPGC-only + ExcludeSuper; drops the `ScriptDisassembler` module dep entirely. |
| `describe_anim_class` | 1 | 4/4/5 | — | **STILL OPEN** |  |
| `list_node_classes` | 1 | 3/5/5 | — | **STILL OPEN** |  |
| `add_anim_state` | 1 | 5/3/3 | — | **STILL OPEN** |  |
| `add_anim_state_machine` | 1 | 5/3/3 | — | **STILL OPEN** |  |
| `add_anim_transition` | 1 | 5/3/3 | — | **STILL OPEN** |  |
| `add_async_action` | 1 | 4/3/3 | — | **STILL OPEN** |  |
| `remove_graph` | 1 | 3/4/3 | — | **STILL OPEN** |  |
| `reparent_component` | 1 | 4/3/2 | — | **STILL OPEN** |  |
| `rename_function` | 2 | 2/5/5 | — | **STILL OPEN** | **Behaviour change NOT landed.** `MifBridgeNodes2.cpp:519-520` emits `oldName`/`name` and no `graphType`. (The `graphId` path at `:459-464` does already reach macro graphs, so half the entry is documentation.) |

### D — Materials and rendering

_25 entries — SHIPPED 10 · STILL OPEN 14 · SUPERSEDED 1_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `recompile_material` | 0 | 5/3/3 | MaterialEditor | **SHIPPED** | live as `recompile_material` |
| `add_material_expression` | 0 | 5/3/2 | MaterialEditor | **SHIPPED** | live as `add_material_expression` |
| `connect_material_expressions` | 0 | 5/2/2 | MaterialEditor | **SHIPPED** | live as `connect_material_expressions` |
| `connect_material_property` | 0 | 5/2/2 | MaterialEditor | **SHIPPED** | live as `connect_material_property` |
| `create_material` | 0 | 5/2/2 | — | **SHIPPED** | live as `create_material` |
| `validate_level_materials` | 0 | 5/3/1 | — | **STILL OPEN** |  |
| `list_material_expressions` | 0 | 5/2/1 | MaterialEditor | **SHIPPED** | live as `list_material_expressions` |
| `set_actor_render_overrides` | 0 | 4/2/2 | — | **STILL OPEN** |  |
| `get_actor_render_info` | 0 | 4/1/1 | — | **STILL OPEN** |  |
| `shader_compile_status` | 0 | 4/1/1 | — | **SHIPPED** | live as `shader_compile_status` |
| `create_material_function` | 1 | 4/2/2 | — | **SHIPPED** | live as `create_material_function` |
| `create_material_parameter_collection` | 1 | 4/2/2 | — | **STILL OPEN** |  |
| `get_material_stats` | 1 | 4/1/3 | MaterialEditor | **STILL OPEN** |  |
| `create_rvt_asset` | 1 | 3/2/2 | — | **STILL OPEN** |  |
| `read_render_target` | 1 | 4/2/1 | — | **STILL OPEN** |  |
| `set_mpc_parameters` | 1 | 3/2/2 | — | **STILL OPEN** |  |
| `delete_material_expression` | 1 | 3/1/2 | MaterialEditor | **SHIPPED** | live as `delete_material_expression` |
| `set_material_instance_parent` | 1 | 3/1/2 | MaterialEditor | **STILL OPEN** |  |
| `build_lighting` | 2 | 3/2/3 | — | **STILL OPEN** |  |
| `build_reflection_captures` | 2 | 2/2/3 | — | **SUPERSEDED** | by the F-axis entry of the same name (dedup table; F owns). F row is STILL OPEN. |
| `create_render_target` | 2 | 3/2/2 | — | **STILL OPEN** |  |
| `refresh_texture` | 2 | 3/2/2 | — | **STILL OPEN** |  |
| `lighting_build_status` | 2 | 3/1/1 | — | **STILL OPEN** |  |
| `layout_material_expressions` | 2 | 2/1/1 | MaterialEditor | **SHIPPED** | live as `layout_material_expressions` |
| `set_material_instance_layers` | 3 | 2/4/3 | — | **STILL OPEN** |  |

### E — Geometry and meshes

_23 entries — STILL OPEN 23_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `mesh_op` | 1 | 5/4/2 | GeometryScriptingCore, plugin:GeometryScripting | **STILL OPEN** |  |
| `commit_dynamic_mesh` | 1 | 5/3/2 | GeometryScriptingCore, plugin:GeometryScripting | **STILL OPEN** |  |
| `copy_from_static_mesh` | 1 | 5/3/2 | GeometryScriptingCore, plugin:GeometryScripting | **STILL OPEN** |  |
| `create_static_mesh_asset` | 1 | 5/2/2 | GeometryScriptingEditor, plugin:GeometryScripting | **STILL OPEN** |  |
| `set_static_mesh_collision_from_mesh` | 1 | 4/3/2 | GeometryScriptingCore, plugin:GeometryScripting | **STILL OPEN** |  |
| `set_static_mesh_lods` | 1 | 4/3/2 | StaticMeshEditor | **STILL OPEN** |  |
| `build_static_mesh` | 1 | 4/2/2 | — | **STILL OPEN** |  |
| `create_dynamic_mesh` | 1 | 5/2/1 | GeometryFramework | **STILL OPEN** |  |
| `create_physics_asset` | 1 | 4/2/2 | SkeletalMeshEditor | **STILL OPEN** |  |
| `mesh_asset_info` | 1 | 5/2/1 | StaticMeshEditor | **STILL OPEN** |  |
| `mesh_query` | 1 | 5/2/1 | GeometryScriptingCore, plugin:GeometryScripting | **STILL OPEN** |  |
| `set_convex_collision` | 1 | 4/2/2 | StaticMeshEditor | **STILL OPEN** |  |
| `skeletal_mesh_info` | 1 | 4/2/1 | SkeletalMeshEditor | **STILL OPEN** |  |
| `add_simple_collision` | 1 | 4/1/1 | StaticMeshEditor | **STILL OPEN** |  |
| `list_dynamic_meshes` | 1 | 3/1/1 | GeometryFramework | **STILL OPEN** |  |
| `release_dynamic_mesh` | 1 | 3/1/1 | GeometryFramework | **STILL OPEN** |  |
| `merge_static_mesh_actors` | 2 | 4/4/3 | MeshMergeUtilities | **STILL OPEN** |  |
| `generate_uv_channel` | 2 | 3/2/2 | StaticMeshEditor | **STILL OPEN** |  |
| `regenerate_skeletal_lods` | 2 | 3/2/2 | SkeletalMeshEditor | **STILL OPEN** |  |
| `set_lod_build_settings` | 2 | 3/2/2 | StaticMeshEditor | **STILL OPEN** |  |
| `set_nanite_settings` | 2 | 3/2/2 | StaticMeshEditor | **STILL OPEN** |  |
| `skeletal_mesh_sockets` | 2 | 3/2/1 | SkeletalMeshEditor | **STILL OPEN** |  |
| `static_mesh_sockets` | 2 | 3/2/1 | — | **STILL OPEN** |  |

### F — World and level

_25 entries — SHIPPED 6 · STILL OPEN 18 · WITHDRAWN 1_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `apply_spline_to_landscape` | 0 | 5/4/4 | — | **STILL OPEN** |  |
| `list_sublevels` | 1 | 4/5/5 | — | **SHIPPED** | live as `list_sublevels` |
| `add_sublevel` | 1 | 5/4/4 | — | **SHIPPED** | live as `add_sublevel` |
| `export_heightmap` | 1 | 4/4/5 | ImageWrapper | **STILL OPEN** |  |
| `list_foliage` | 1 | 4/4/5 | — | **STILL OPEN** |  |
| `set_current_sublevel` | 1 | 4/5/4 | — | **SHIPPED** | live as `set_current_sublevel` |
| `apply_landscape_splines` | 1 | 4/4/4 | — | **STILL OPEN** |  |
| `export_weightmap` | 1 | 3/4/5 | ImageWrapper | **STILL OPEN** |  |
| `list_landscape_splines` | 1 | 3/4/5 | — | **STILL OPEN** |  |
| `set_sublevel_visibility` | 1 | 3/5/4 | — | **SHIPPED** | live as `set_sublevel_visibility` |
| `paint_foliage` | 1 | 4/3/4 | — | **STILL OPEN** |  |
| `create_landscape_spline` | 1 | 5/2/3 | — | **STILL OPEN** |  |
| `create_sublevel` | 1 | 4/3/3 | — | **STILL OPEN** |  |
| `import_heightmap` | 1 | 4/3/3 | ImageWrapper | **STILL OPEN** |  |
| `remove_foliage_instances` | 1 | 3/3/4 | — | **STILL OPEN** |  |
| `remove_sublevel` | 1 | 3/4/3 | — | **SHIPPED** | live as `remove_sublevel` |
| `set_water_body_profile` | 1 | 4/3/3 | Water | **STILL OPEN** |  |
| `list_data_layers` | 2 | 3/4/5 | — | **STILL OPEN** |  |
| `build_reflection_captures` | 2 | 3/4/3 | — | **STILL OPEN** |  |
| `create_foliage_type` | 2 | 3/3/4 | — | **STILL OPEN** |  |
| `create_grass_type` | 2 | 2/4/4 | — | **STILL OPEN** |  |
| `set_sublevel_streaming` | 2 | 3/4/3 | — | **SHIPPED** | live as `set_sublevel_streaming` |
| `move_actors_to_sublevel` | 2 | 3/3/3 | — | **STILL OPEN** |  |
| `resimulate_procedural_foliage` | 2 | 3/2/3 | — | **STILL OPEN** |  |
| `create_level_instance` | 2 | 3/2/2 | — | **WITHDRAWN** | DEMOTED in phase-2 — `CreateLevelInstanceFrom` hard-codes a modal SaveAs (F_world_level.md:897). Not viable headless. |

### G1 — AI, navigation, NPC routing

_17 entries — STILL OPEN 17_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `pie_move_status` | 0 | 5/2/5 | — | **STILL OPEN** |  |
| `find_path` | 0 | 5/1/5 | — | **STILL OPEN** |  |
| `pie_move_pawn` | 0 | 5/3/3 | — | **STILL OPEN** |  |
| `navmesh_tile_info` | 1 | 4/2/5 | — | **STILL OPEN** |  |
| `add_nav_modifier_volume` | 1 | 4/2/4 | — | **STILL OPEN** |  |
| `list_blackboard_keys` | 1 | 3/1/5 | — | **STILL OPEN** |  |
| `project_to_navmesh` | 1 | 3/1/5 | — | **STILL OPEN** |  |
| `random_reachable_point` | 1 | 3/1/5 | — | **STILL OPEN** |  |
| `list_nav_areas` | 1 | 2/1/5 | — | **STILL OPEN** |  |
| `pie_stop_move` | 1 | 3/1/4 | — | **STILL OPEN** |  |
| `add_nav_link` | 2 | 4/3/4 | — | **STILL OPEN** |  |
| `pie_get_perception` | 2 | 3/2/5 | — | **STILL OPEN** |  |
| `run_eqs_query` | 2 | 3/3/4 | — | **STILL OPEN** |  |
| `pie_possess` | 2 | 4/2/3 | — | **STILL OPEN** |  |
| `add_blackboard_key` | 2 | 2/3/3 | — | **STILL OPEN** |  |
| `nav_raycast` | 2 | 2/1/5 | — | **STILL OPEN** |  |
| `create_blackboard_asset` | 3 | 2/2/4 | — | **STILL OPEN** |  |

### G2 — Sequencer, UMG extras, Enhanced Input

_19 entries — STILL OPEN 19_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `list_widget_tree` | 0 | 5/4/5 | — | **STILL OPEN** |  |
| `reparent_widget` | 0 | 5/3/3 | — | **STILL OPEN** |  |
| `describe_sequence` | 1 | 5/3/5 | MovieScene | **STILL OPEN** |  |
| `list_input_mappings` | 1 | 3/5/5 | EnhancedInput | **STILL OPEN** |  |
| `input_map_key` | 1 | 4/4/3 | EnhancedInput | **STILL OPEN** |  |
| `create_input_action` | 1 | 3/4/3 | EnhancedInput | **STILL OPEN** |  |
| `create_input_mapping_context` | 1 | 3/4/3 | EnhancedInput | **STILL OPEN** |  |
| `create_widget_animation` | 1 | 4/3/3 | MovieScene | **STILL OPEN** |  |
| `sequence_add_section` | 1 | 4/3/3 | MovieScene | **STILL OPEN** |  |
| `sequence_add_track` | 1 | 4/3/3 | MovieScene, MovieSceneTracks | **STILL OPEN** |  |
| `sequence_bind_actor` | 1 | 4/3/3 | MovieScene, LevelSequence | **STILL OPEN** |  |
| `sequence_set_keys` | 1 | 5/2/3 | MovieScene | **STILL OPEN** |  |
| `widget_animation_bind` | 1 | 4/3/3 | MovieScene | **STILL OPEN** |  |
| `create_level_sequence` | 1 | 5/2/2 | LevelSequence, MovieScene | **STILL OPEN** |  |
| `rename_widget` | 1 | 4/2/2 | — | **STILL OPEN** |  |
| `open_sequence_editor` | 2 | 3/4/4 | LevelSequenceEditor | **STILL OPEN** |  |
| `sequence_editor_play` | 2 | 3/4/3 | LevelSequenceEditor | **STILL OPEN** |  |
| `render_movie_request` | 2 | 4/2/2 | MovieRenderPipelineCore, MovieRenderPipelineEditor | **STILL OPEN** |  |
| `render_movie_status` | 2 | 4/2/2 | MovieRenderPipelineCore, MovieRenderPipelineEditor | **STILL OPEN** |  |

### G3 — Niagara, audio, physics

_15 entries — STILL OPEN 14 · SUPERSEDED 1_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `create_niagara_system` | 1 | 4/3/2 | Niagara, NiagaraEditor | **STILL OPEN** |  |
| `set_niagara_user_parameter` | 1 | 5/2/2 | Niagara | **STILL OPEN** |  |
| `create_sound_cue` | 1 | 4/2/2 | — | **STILL OPEN** |  |
| `get_niagara_particle_counts` | 1 | 5/2/1 | Niagara | **STILL OPEN** |  |
| `niagara_compile_request` | 1 | 4/2/2 | Niagara | **STILL OPEN** |  |
| `spawn_niagara_component` | 1 | 4/2/2 | Niagara | **STILL OPEN** |  |
| `get_niagara_user_parameters` | 1 | 4/2/1 | Niagara | **STILL OPEN** |  |
| `play_sound_preview` | 1 | 4/1/1 | — | **STILL OPEN** |  |
| `set_niagara_component_active` | 1 | 3/1/1 | Niagara | **STILL OPEN** |  |
| `create_metasound_source` | 2 | 4/4/3 | MetasoundEngine, MetasoundFrontend | **STILL OPEN** |  |
| `add_niagara_emitter` | 2 | 3/3/3 | NiagaraEditor, Niagara | **STILL OPEN** |  |
| `create_physics_asset` | 2 | 4/3/2 | PhysicsUtilities | **SUPERSEDED** | by the E-axis entry of the same name (dedup table; E owns). E row is STILL OPEN. |
| `add_sound_cue_node` | 2 | 3/3/2 | — | **STILL OPEN** |  |
| `set_physics_constraint` | 2 | 3/2/2 | — | **STILL OPEN** |  |
| `create_geometry_collection` | 3 | 2/3/3 | GeometryCollectionEngine | **STILL OPEN** |  |

### H — Data, curves, localization, config, savegames

_25 entries — STILL OPEN 24 · SUPERSEDED 1_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `read_curve` | 0 | 4/1/5 | — | **STILL OPEN** |  |
| `set_curve_keys` | 0 | 5/2/1 | — | **STILL OPEN** |  |
| `get_config_value` | 1 | 4/1/5 | — | **STILL OPEN** |  |
| `create_datatable` | 1 | 5/2/2 | — | **STILL OPEN** |  |
| `export_datatable_csv` | 1 | 3/1/5 | — | **STILL OPEN** |  |
| `list_primary_assets` | 1 | 3/1/5 | — | **STILL OPEN** |  |
| `list_string_table_entries` | 1 | 3/1/5 | — | **STILL OPEN** |  |
| `create_curve` | 1 | 4/2/2 | — | **STILL OPEN** |  |
| `set_struct_member` | 1 | 4/2/2 | — | **STILL OPEN** |  |
| `create_string_table` | 1 | 4/1/2 | — | **STILL OPEN** |  |
| `import_datatable_csv` | 1 | 4/1/2 | — | **STILL OPEN** |  |
| `delete_datatable_row` | 1 | 4/1/1 | — | **SUPERSEDED** | by `delete_datatable_rows` (MifBridgeDataTables.cpp:642) — N rows, same `FDataTableEditorUtils::RemoveRow`. RESIDUE: no `savable:false` echo for pak-mounted tables. |
| `set_string_table_entry` | 1 | 4/1/1 | — | **STILL OPEN** |  |
| `duplicate_datatable_row` | 1 | 3/1/1 | — | **STILL OPEN** |  |
| `rename_datatable_row` | 1 | 3/1/1 | — | **STILL OPEN** |  |
| `list_savegames` | 2 | 3/1/5 | — | **STILL OPEN** |  |
| `read_savegame` | 2 | 3/2/4 | — | **STILL OPEN** |  |
| `get_settings_config_source` | 2 | 2/1/5 | DeveloperSettings | **STILL OPEN** |  |
| `list_cultures` | 2 | 2/1/5 | — | **STILL OPEN** |  |
| `set_config_value` | 2 | 3/2/3 | — | **STILL OPEN** |  |
| `create_curve_table` | 2 | 3/2/2 | — | **STILL OPEN** |  |
| `set_composite_datatable_parents` | 2 | 3/2/2 | — | **STILL OPEN** |  |
| `move_struct_member` | 2 | 2/1/2 | — | **STILL OPEN** |  |
| `move_datatable_row` | 2 | 2/1/1 | — | **STILL OPEN** |  |
| `read_rama_savefile` | 3 | 3/2/3 | — | **STILL OPEN** |  |

### I — Diagnostics and observation

_10 entries — STILL OPEN 10_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `get_perf_stats` | 1 | 4/4/5 | RHI | **STILL OPEN** |  |
| `get_properties_bulk` | 1 | 4/4/5 | — | **STILL OPEN** |  |
| `list_automation_tests` | 1 | 4/4/5 | — | **STILL OPEN** |  |
| `message_log_read` | 1 | 4/4/5 | MessageLog | **STILL OPEN** |  |
| `pie_resolve_path` | 1 | 4/4/5 | — | **STILL OPEN** |  |
| `log_tail` | 1 | 5/3/4 | — | **STILL OPEN** |  |
| `world_state_hash` | 2 | 3/4/5 | — | **STILL OPEN** |  |
| `trace_start` | 2 | 3/4/4 | — | **STILL OPEN** |  |
| `screenshot_request` | 2 | 3/3/4 | — | **STILL OPEN** |  |
| `run_automation_test` | 2 | 4/2/3 | — | **STILL OPEN** |  |

### J — DDS2 project-specific

_8 entries — STILL OPEN 8_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `list_native_classes` | 1 | 4/5/5 | — | **STILL OPEN** |  |
| `list_ue4ss_mods` | 1 | 4/5/5 | — | **STILL OPEN** |  |
| `read_modloader_log` | 1 | 3/5/5 | — | **STILL OPEN** | **Behaviour change NOT landed.** `MifBridgePipeline.cpp:82` still calls `PushLine(Tail, Kept[Index])`, which appends a bare `FJsonValueString` (`:19-22`). Output is raw lines, not structured events. |
| `verify_pak_contents` | 1 | 4/4/5 | — | **STILL OPEN** |  |
| `refresh_asset_registry` | 2 | 3/4/3 | — | **STILL OPEN** |  |
| `mod_package_request` | 2 | 5/2/2 | — | **STILL OPEN** |  |
| `mount_pak` | 2 | 4/3/2 | PakFile | **STILL OPEN** |  |
| `unmount_pak` | 3 | 2/3/1 | PakFile | **STILL OPEN** |  |

### K1 — Reconstructor toolkit

_10 entries — SHIPPED 7 · SUPERSEDED 3_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `kr_disassemble_function` | 0 | 5/4/5 | <provider:MifKismetReconstructor> | **SHIPPED** | live as `kr_disassemble_function` |
| `kr_dump_blueprint` | 0 | 5/4/5 | <provider:MifKismetReconstructor> | **SHIPPED** | live as `kr_dump_blueprint` |
| `kr_list_cooked_blueprints` | 1 | 3/5/5 | — | **SHIPPED** | live as `kr_list_cooked_blueprints` |
| `kr_list_events` | 1 | 4/3/4 | <provider:MifKismetReconstructor> | **SHIPPED** | live as `kr_list_events` |
| `kr_analyze_ubergraph` | 1 | 3/3/4 | <provider:MifKismetReconstructor> | **SHIPPED** | live as `kr_analyze_ubergraph` |
| `kr_reconstruct_function` | 1 | 4/3/3 | <provider:MifKismetReconstructor> | **SUPERSEDED** | by `kr_reconstruct_request {mode:"function"}` (live) — as the dedup table ratified. |
| `kr_batch_reconstruct_status` | 2 | 3/5/5 | — | **SUPERSEDED** | by `kr_reconstruct_status` (live) — the shared one-slot job model. |
| `kr_pin_type_from_property` | 2 | 2/5/5 | <provider:MifKismetReconstructor> | **SHIPPED** | live as `kr_pin_type_from_property` |
| `kr_verify_fidelity` | 2 | 4/2/3 | — | **SHIPPED** | live as `kr_verify_fidelity` |
| `kr_batch_reconstruct_request` | 2 | 3/2/3 | — | **SUPERSEDED** | by `kr_batch_reconstruct` (live, MifKrBridgeEndpoints.cpp) — one endpoint, not a request/status pair. |

### K2 — Reconstructor pipeline

_5 entries — SHIPPED 5_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `kr_reconstruct_status` | 0 | 4/4/5 | provider:MifKismetReconstructor | **SHIPPED** | live as `kr_reconstruct_status` |
| `kr_reconstruct_request` | 0 | 5/2/3 | provider:MifKismetReconstructor | **SHIPPED** | live as `kr_reconstruct_request` |
| `kr_verify_fidelity` | 1 | 5/2/3 | provider:MifKismetReconstructor | **SHIPPED** | live as `kr_verify_fidelity` |
| `kr_classify_drift` | 2 | 3/2/3 | provider:MifKismetReconstructor | **SHIPPED** | live as `kr_classify_drift` |
| `kr_drift_census` | 2 | 3/2/3 | provider:MifKismetReconstructor | **SHIPPED** | live as `kr_drift_census` |

### P1 — Graph auto-layout & BP-text plugins

_5 entries — STILL OPEN 5_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `format_graph` | 0 | 5/3/4 | — | **STILL OPEN** |  |
| `export_graph_text` | 1 | 3/5/5 | — | **STILL OPEN** |  |
| `fit_comment_to_nodes` | 1 | 3/4/4 | AutoSizeComments | **STILL OPEN** |  |
| `format_graph_ba_status` | 2 | 4/4/5 | BlueprintAssist, plugin:BlueprintAssist | **STILL OPEN** |  |
| `format_graph_ba_request` | 2 | 4/3/2 | BlueprintAssist, plugin:BlueprintAssist | **STILL OPEN** |  |

### P2 — Oceanology / Riverology / FGear

_1 entries — STILL OPEN 1_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `call_object_function` | 1 | 5/3/3 | — | **STILL OPEN** |  |

### P3 — GameFeatures, thumbnails, misc plugins

_4 entries — STILL OPEN 4_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `get_game_feature_state` | 1 | 4/4/5 | GameFeatures | **STILL OPEN** |  |
| `render_asset_thumbnail` | 2 | 3/3/3 | — | **STILL OPEN** |  |
| `change_game_feature_state_request` | 2 | 3/3/2 | GameFeatures | **STILL OPEN** |  |
| `change_game_feature_state_status` | 2 | 3/3/2 | GameFeatures | **STILL OPEN** |  |

### Q — Known-defect root causes (repairs)

_2 entries — SHIPPED (PARTIAL) 2_

| Endpoint | T | U/E/R | New deps (as specced) | Status | Evidence / what covers it |
|---|---|---|---|---|---|
| `pie_status` | 0 | 5/4/5 | — | **SHIPPED (PARTIAL)** | `state` word + HasBegunPlay readiness shipped (MifBridgePIE.cpp:91-134); the spec's `travelling`/`stopping`/`simulating` state words are NOT emitted (only booleans). |
| `snap_actors_to_ground` | 0 | 4/4/4 | — | **SHIPPED (PARTIAL)** | Multi-trace + landscape/groundActor filter shipped (MifBridgeWorld.cpp:420-441); the spec's PENETRATING trace is NOT — `LineTraceMultiByChannel` stops at the first blocking hit. |

---

## 2. The 203 STILL OPEN entries, re-ranked and re-batched

### 2.1 Why `02_RANKED.md`'s batching is now wrong

`02_RANKED.md` grouped 241 entries into Batches 0–8 + R so that each build cycle added the fewest
new modules. Four of those module costs have since been **paid**, and one has been **eliminated**.
Verified verbatim against `Source/MifBridge/MifBridge.Build.cs`:

```csharp
"MaterialEditor",      // :22  UMaterialEditingLibrary (class-level MATERIALEDITOR_API)
"RHI",                 // :26  GMaxRHIShaderPlatform (RHI_API)
"InputBlueprintNodes", // :33  UK2Node_EnhancedInputAction (add_enhanced_input_action)
"EnhancedInput",       // :34  UInputAction / UInputMappingContext runtime types
```

| Original batch | Original premise | Reality now |
|---|---|---|
| Batch 2 — materials (9) | `MaterialEditor` is a new module | **Paid.** `Build.cs:22`. 7 of the 9 shipped; the remaining **2** (`get_material_stats`, `set_material_instance_parent`) are now zero-cost. |
| Batch 3 — Enhanced Input (4) | `EnhancedInput` is a new module | **Paid.** `Build.cs:34`, plus `InputBlueprintNodes` at `:33`, plus the plugin is already listed in `MifBridge.uplugin`. All **4** are now zero-cost. Batch 3 should not exist. |
| Batch 8 — `get_perf_stats` | `RHI` is a new module | **Paid.** `Build.cs:26` (pulled in as `FMaterialUpdateContext`'s default argument during Batch D). Now zero-cost. |
| Batch 8 — `disassemble_function` | `ScriptDisassembler` is a new module | **Eliminated.** Superseded by the live `kr_disassemble_function`, which uses the reconstructor's own `FKismetBytecodeDisassemblerJson` (`MifKrBridgeEndpoints.cpp:1176-1177`). The module is never needed. |
| Batch R — reconstructor (10) | 10 endpoints via the registration interface | **Done, and then some** — 12 are live, including `kr_batch_reconstruct`, which `02_RANKED.md` had struck as a "merged duplicate". |
| Batch 0 — repairs (8) | 8 behaviour changes | **Still 8 open** (2 partial). `02_RANKED.md`'s claim that this is the cheapest batch is still correct and it is still undone — see §4. |

Net effect on the grouping: **141 of the 203 open entries now need no Build.cs change at all**
(the original figure was 145 out of 241, before 34 shipped and before four module costs were paid).

### 2.2 Re-ranking method

The original `U/E/R` scores were assigned before the material, streaming, reconstructor and
inherited-component work landed. Rather than silently rewrite 203 scores, this section:

1. **keeps the original rank as the base ordering inside each cycle** (so the original reasoning
   stays auditable), and
2. **lists explicit re-rank moves** below, each with the shipped thing that caused the move.

#### Re-rank UP — value increased because of what shipped

| Entry | Was | Now | Because |
|---|---|---|---|
| `list_components` (C, T0) | rank 14 | **top of the whole list** | Batch J shipped `get_inherited_component`, which resolves ONE component **by name** across the parent chain (`MifBridgeInherited.cpp:646`). Nothing enumerates the names. An agent that does not already know the name cannot use the endpoint the session just built. See §4.1. |
| `add_component` (C, T0) | rank 12 | **+2** | Same reason, and the parent-chain resolver it needs now already exists in `MifBridgeInherited.cpp` — effort drops. |
| `reparent_component` (C, T1) | rank 9 | **+2** | Same resolver; the component axis is now the live area of the codebase. |
| `format_graph` (P1, T0) | rank 12 | **+2** | The bridge now has ~60 node-spawning endpoints and a shipped *material*-graph layout (`layout_material_expressions`), which makes the K2-graph layout hole conspicuous by contrast. Every graph an agent authors is still a heap at the origin. |
| `add_node_by_class` (C, T0) | rank 10 | **+3** | One endpoint retires the "add a dedicated endpoint per node class" treadmill that produced `MifBridgeNodes.cpp` … `MifBridgeNodes7.cpp` (7 files, the newest created 2026-07-28). |
| `get_material_stats`, `set_material_instance_parent` (D) | rank 8, 6 | **+2 each** | `MaterialEditor` is paid for and the material family is half-built; these are the cheapest remaining completions of a shipped family. |
| `validate_level_materials`, `set_actor_render_overrides`, `get_actor_render_info` (D, T0) | 9, 8, 6 | **+1 each** | Same — they read/verify the material work that shipped. |
| `create_sublevel`, `move_actors_to_sublevel` (F) | 10, 9 | **+1 each** | Batch I shipped six sublevel verbs; these two complete the family and reuse its resolver. |
| `list_input_mappings`, `input_map_key`, `create_input_action`, `create_input_mapping_context` (G2) | 13, 11, 10, 10 | **+1 each** | Module cost is now zero and `add_enhanced_input_action` proved the node path works. |
| `asset_compile_status` (specced as `get_asset_compilation_status`, B) | rank 10 | **+1** | Six open E-axis entries name it as their async pair; it is a prerequisite, not a nicety. |

#### Re-rank DOWN — value decreased

| Entry | Was | Now | Because |
|---|---|---|---|
| `rename_datatable_row`, `duplicate_datatable_row`, `move_datatable_row` (H) | 5, 5, 4 | **−2 each** | Composable today from shipped endpoints. `MifBridgeDataTables.cpp:635-637` says so in its own words: *"Needed to rename a row: write the row under its new name, then drop the old one."* |
| `build_lighting`, `build_reflection_captures` (D/F) | 8, 10 | **−2 each** | The project's only large world (`IslaSombra`) is a **cooked** World Partition map; both endpoints are `degraded`/blocked there, and no loose map needs them yet. |
| `open_sequence_editor`, `sequence_editor_play`, `render_movie_request`, `render_movie_status` (G2) | 11, 10, 8, 8 | **−1 each** | Highest module cost of any remaining cycle against 4 LevelSequences in the project. |
| `disassemble_function` (C) | rank 14 | **removed** | Superseded — see §1. |

### 2.3 Corrected build cycles

Each cycle adds the modules named in its heading and nothing else. Cycle 1 adds nothing.

#### Cycle 1 — ZERO new modules (141)

| # | Endpoint | Axis | T | U/E/R | rank |
|---|---|---|---|---|---|
| 1 | `list_components` | C | 0 | 4/5/5 | 14 |
| 2 | `list_widget_tree` | G2 | 0 | 5/4/5 | 14 |
| 3 | `list_native_classes` | J | 1 | 4/5/5 | 14 |
| 4 | `list_ue4ss_mods` | J | 1 | 4/5/5 | 14 |
| 5 | `apply_spline_to_landscape` | F | 0 | 5/4/4 | 13 |
| 6 | `connect_pins` | C | 0 | 4/5/4 | 13 |
| 7 | `describe_anim_class` | C | 1 | 4/4/5 | 13 |
| 8 | `export_graph_text` | P1 | 1 | 3/5/5 | 13 |
| 9 | `get_perf_stats` | I | 1 | 4/4/5 | 13 |
| 10 | `get_properties_bulk` | I | 1 | 4/4/5 | 13 |
| 11 | `list_automation_tests` | I | 1 | 4/4/5 | 13 |
| 12 | `list_foliage` | F | 1 | 4/4/5 | 13 |
| 13 | `list_input_mappings` | G2 | 1 | 3/5/5 | 13 |
| 14 | `list_node_classes` | C | 1 | 3/5/5 | 13 |
| 15 | `pie_resolve_path` | I | 1 | 4/4/5 | 13 |
| 16 | `read_modloader_log` | J | 1 | 3/5/5 | 13 |
| 17 | `verify_pak_contents` | J | 1 | 4/4/5 | 13 |
| 18 | `add_component` | C | 0 | 5/4/3 | 12 |
| 19 | `add_create_delegate` | C | 0 | 4/4/4 | 12 |
| 20 | `create_macro` | C | 0 | 4/4/4 | 12 |
| 21 | `format_graph` | P1 | 0 | 5/3/4 | 12 |
| 22 | `list_variables` | C | 0 | 4/4/4 | 12 |
| 23 | `pie_move_status` | G1 | 0 | 5/2/5 | 12 |
| 24 | `set_variable_type` | C | 0 | 5/4/3 | 12 |
| 25 | `apply_landscape_splines` | F | 1 | 4/4/4 | 12 |
| 26 | `get_class_hierarchy` | B | 1 | 5/2/5 | 12 |
| 27 | `list_landscape_splines` | F | 1 | 3/4/5 | 12 |
| 28 | `log_tail` | I | 1 | 5/3/4 | 12 |
| 29 | `list_data_layers` | F | 2 | 3/4/5 | 12 |
| 30 | `rename_function` | C | 2 | 2/5/5 | 12 |
| 31 | `world_state_hash` | I | 2 | 3/4/5 | 12 |
| 32 | `find_path` | G1 | 0 | 5/1/5 | 11 |
| 33 | `pie_move_pawn` | G1 | 0 | 5/3/3 | 11 |
| 34 | `reparent_widget` | G2 | 0 | 5/3/3 | 11 |
| 35 | `add_anim_state` | C | 1 | 5/3/3 | 11 |
| 36 | `add_anim_state_machine` | C | 1 | 5/3/3 | 11 |
| 37 | `add_anim_transition` | C | 1 | 5/3/3 | 11 |
| 38 | `call_object_function` | P2 | 1 | 5/3/3 | 11 |
| 39 | `create_asset` | B | 1 | 5/3/3 | 11 |
| 40 | `find_assets_by_tag` | B | 1 | 4/2/5 | 11 |
| 41 | `input_map_key` | G2 | 1 | 4/4/3 | 11 |
| 42 | `navmesh_tile_info` | G1 | 1 | 4/2/5 | 11 |
| 43 | `paint_foliage` | F | 1 | 4/3/4 | 11 |
| 44 | `add_nav_link` | G1 | 2 | 4/3/4 | 11 |
| 45 | `trace_start` | I | 2 | 3/4/4 | 11 |
| 46 | `add_node_by_class` | C | 0 | 5/2/3 | 10 |
| 47 | `read_curve` | H | 0 | 4/1/5 | 10 |
| 48 | `add_async_action` | C | 1 | 4/3/3 | 10 |
| 49 | `add_nav_modifier_volume` | G1 | 1 | 4/2/4 | 10 |
| 50 | `create_input_action` | G2 | 1 | 3/4/3 | 10 |
| 51 | `create_input_mapping_context` | G2 | 1 | 3/4/3 | 10 |
| 52 | `create_landscape_spline` | F | 1 | 5/2/3 | 10 |
| 53 | `create_sublevel` | F | 1 | 4/3/3 | 10 |
| 54 | `export_asset` | B | 1 | 4/2/4 | 10 |
| 55 | `get_asset_compilation_status` | B | 1 | 4/1/5 | 10 |
| 56 | `get_asset_tags` | B | 1 | 4/1/5 | 10 |
| 57 | `get_config_value` | H | 1 | 4/1/5 | 10 |
| 58 | `get_cvar` | A | 1 | 4/1/5 | 10 |
| 59 | `get_viewport_state` | A | 1 | 3/2/5 | 10 |
| 60 | `list_cvars` | A | 1 | 3/2/5 | 10 |
| 61 | `map_check` | B | 1 | 4/2/4 | 10 |
| 62 | `remove_foliage_instances` | F | 1 | 3/3/4 | 10 |
| 63 | `remove_graph` | C | 1 | 3/4/3 | 10 |
| 64 | `set_view_mode` | A | 1 | 4/2/4 | 10 |
| 65 | `build_reflection_captures` | F | 2 | 3/4/3 | 10 |
| 66 | `create_foliage_type` | F | 2 | 3/3/4 | 10 |
| 67 | `create_grass_type` | F | 2 | 2/4/4 | 10 |
| 68 | `pie_get_perception` | G1 | 2 | 3/2/5 | 10 |
| 69 | `refresh_asset_registry` | J | 2 | 3/4/3 | 10 |
| 70 | `run_eqs_query` | G1 | 2 | 3/3/4 | 10 |
| 71 | `screenshot_request` | I | 2 | 3/3/4 | 10 |
| 72 | `validate_level_materials` | D | 0 | 5/3/1 | 9 |
| 73 | `create_datatable` | H | 1 | 5/2/2 | 9 |
| 74 | `export_datatable_csv` | H | 1 | 3/1/5 | 9 |
| 75 | `list_actor_folders` | A | 1 | 3/1/5 | 9 |
| 76 | `list_blackboard_keys` | G1 | 1 | 3/1/5 | 9 |
| 77 | `list_content_paths` | B | 1 | 3/1/5 | 9 |
| 78 | `list_primary_assets` | H | 1 | 3/1/5 | 9 |
| 79 | `list_string_table_entries` | H | 1 | 3/1/5 | 9 |
| 80 | `project_to_navmesh` | G1 | 1 | 3/1/5 | 9 |
| 81 | `random_reachable_point` | G1 | 1 | 3/1/5 | 9 |
| 82 | `reparent_component` | C | 1 | 4/3/2 | 9 |
| 83 | `resolve_redirector` | B | 1 | 3/1/5 | 9 |
| 84 | `set_actor_folder` | A | 1 | 3/2/4 | 9 |
| 85 | `group_actors` | A | 2 | 3/2/4 | 9 |
| 86 | `list_savegames` | H | 2 | 3/1/5 | 9 |
| 87 | `mod_package_request` | J | 2 | 5/2/2 | 9 |
| 88 | `move_actors_to_sublevel` | F | 2 | 3/3/3 | 9 |
| 89 | `pie_possess` | G1 | 2 | 4/2/3 | 9 |
| 90 | `read_savegame` | H | 2 | 3/2/4 | 9 |
| 91 | `render_asset_thumbnail` | P3 | 2 | 3/3/3 | 9 |
| 92 | `run_automation_test` | I | 2 | 4/2/3 | 9 |
| 93 | `set_material_instance_layers` | D | 3 | 2/4/3 | 9 |
| 94 | `set_actor_render_overrides` | D | 0 | 4/2/2 | 8 |
| 95 | `set_curve_keys` | H | 0 | 5/2/1 | 8 |
| 96 | `build_static_mesh` | E | 1 | 4/2/2 | 8 |
| 97 | `create_curve` | H | 1 | 4/2/2 | 8 |
| 98 | `create_material_parameter_collection` | D | 1 | 4/2/2 | 8 |
| 99 | `create_sound_cue` | G3 | 1 | 4/2/2 | 8 |
| 100 | `get_material_stats` | D | 1 | 4/1/3 | 8 |
| 101 | `list_nav_areas` | G1 | 1 | 2/1/5 | 8 |
| 102 | `pie_stop_move` | G1 | 1 | 3/1/4 | 8 |
| 103 | `rename_widget` | G2 | 1 | 4/2/2 | 8 |
| 104 | `set_struct_member` | H | 1 | 4/2/2 | 8 |
| 105 | `add_blackboard_key` | G1 | 2 | 2/3/3 | 8 |
| 106 | `add_sound_cue_node` | G3 | 2 | 3/3/2 | 8 |
| 107 | `build_lighting` | D | 2 | 3/2/3 | 8 |
| 108 | `consolidate_assets` | B | 2 | 3/3/2 | 8 |
| 109 | `fixup_redirectors` | B | 2 | 3/2/3 | 8 |
| 110 | `get_editor_modes` | A | 2 | 2/1/5 | 8 |
| 111 | `get_package_disk_data` | B | 2 | 2/1/5 | 8 |
| 112 | `list_cultures` | H | 2 | 2/1/5 | 8 |
| 113 | `list_layers` | A | 2 | 2/1/5 | 8 |
| 114 | `modify_actor_layers` | A | 2 | 2/2/4 | 8 |
| 115 | `nav_raycast` | G1 | 2 | 2/1/5 | 8 |
| 116 | `resimulate_procedural_foliage` | F | 2 | 3/2/3 | 8 |
| 117 | `select_components` | A | 2 | 2/2/4 | 8 |
| 118 | `set_config_value` | H | 2 | 3/2/3 | 8 |
| 119 | `create_blackboard_asset` | G1 | 3 | 2/2/4 | 8 |
| 120 | `read_rama_savefile` | H | 3 | 3/2/3 | 8 |
| 121 | `create_rvt_asset` | D | 1 | 3/2/2 | 7 |
| 122 | `create_string_table` | H | 1 | 4/1/2 | 7 |
| 123 | `import_datatable_csv` | H | 1 | 4/1/2 | 7 |
| 124 | `read_render_target` | D | 1 | 4/2/1 | 7 |
| 125 | `set_mpc_parameters` | D | 1 | 3/2/2 | 7 |
| 126 | `close_asset_editors` | A | 2 | 2/1/4 | 7 |
| 127 | `create_curve_table` | H | 2 | 3/2/2 | 7 |
| 128 | `create_render_target` | D | 2 | 3/2/2 | 7 |
| 129 | `refresh_texture` | D | 2 | 3/2/2 | 7 |
| 130 | `set_composite_datatable_parents` | H | 2 | 3/2/2 | 7 |
| 131 | `set_physics_constraint` | G3 | 2 | 3/2/2 | 7 |
| 132 | `get_actor_render_info` | D | 0 | 4/1/1 | 6 |
| 133 | `play_sound_preview` | G3 | 1 | 4/1/1 | 6 |
| 134 | `set_material_instance_parent` | D | 1 | 3/1/2 | 6 |
| 135 | `set_string_table_entry` | H | 1 | 4/1/1 | 6 |
| 136 | `static_mesh_sockets` | E | 2 | 3/2/1 | 6 |
| 137 | `duplicate_datatable_row` | H | 1 | 3/1/1 | 5 |
| 138 | `rename_datatable_row` | H | 1 | 3/1/1 | 5 |
| 139 | `lighting_build_status` | D | 2 | 3/1/1 | 5 |
| 140 | `move_struct_member` | H | 2 | 2/1/2 | 5 |
| 141 | `move_datatable_row` | H | 2 | 2/1/1 | 4 |

#### Cycle 2 — mesh editing: StaticMeshEditor, SkeletalMeshEditor, MeshMergeUtilities, GeometryCollectionEngine (13)

| Endpoint | Axis | T | U/E/R | rank | new module(s) |
|---|---|---|---|---|---|
| `merge_static_mesh_actors` | E | 2 | 4/4/3 | 11 | MeshMergeUtilities |
| `set_static_mesh_lods` | E | 1 | 4/3/2 | 9 | StaticMeshEditor |
| `create_geometry_collection` | G3 | 3 | 2/3/3 | 8 | GeometryCollectionEngine |
| `create_physics_asset` | E | 1 | 4/2/2 | 8 | SkeletalMeshEditor |
| `mesh_asset_info` | E | 1 | 5/2/1 | 8 | StaticMeshEditor |
| `set_convex_collision` | E | 1 | 4/2/2 | 8 | StaticMeshEditor |
| `generate_uv_channel` | E | 2 | 3/2/2 | 7 | StaticMeshEditor |
| `regenerate_skeletal_lods` | E | 2 | 3/2/2 | 7 | SkeletalMeshEditor |
| `set_lod_build_settings` | E | 2 | 3/2/2 | 7 | StaticMeshEditor |
| `set_nanite_settings` | E | 2 | 3/2/2 | 7 | StaticMeshEditor |
| `skeletal_mesh_info` | E | 1 | 4/2/1 | 7 | SkeletalMeshEditor |
| `add_simple_collision` | E | 1 | 4/1/1 | 6 | StaticMeshEditor |
| `skeletal_mesh_sockets` | E | 2 | 3/2/1 | 6 | SkeletalMeshEditor |

#### Cycle 3 — GeometryScript: plugin enable + GeometryScriptingCore/Editor + GeometryFramework (9)

| Endpoint | Axis | T | U/E/R | rank | new module(s) |
|---|---|---|---|---|---|
| `mesh_op` | E | 1 | 5/4/2 | 11 | GeometryScriptingCore, plugin:GeometryScripting |
| `commit_dynamic_mesh` | E | 1 | 5/3/2 | 10 | GeometryScriptingCore, plugin:GeometryScripting |
| `copy_from_static_mesh` | E | 1 | 5/3/2 | 10 | GeometryScriptingCore, plugin:GeometryScripting |
| `create_static_mesh_asset` | E | 1 | 5/2/2 | 9 | GeometryScriptingEditor, plugin:GeometryScripting |
| `set_static_mesh_collision_from_mesh` | E | 1 | 4/3/2 | 9 | GeometryScriptingCore, plugin:GeometryScripting |
| `create_dynamic_mesh` | E | 1 | 5/2/1 | 8 | GeometryFramework |
| `mesh_query` | E | 1 | 5/2/1 | 8 | GeometryScriptingCore, plugin:GeometryScripting |
| `list_dynamic_meshes` | E | 1 | 3/1/1 | 5 | GeometryFramework |
| `release_dynamic_mesh` | E | 1 | 3/1/1 | 5 | GeometryFramework |

#### Cycle 4 — FX & audio: Niagara, NiagaraEditor, Metasound*, AudioEditor (10)

| Endpoint | Axis | T | U/E/R | rank | new module(s) |
|---|---|---|---|---|---|
| `create_metasound_source` | G3 | 2 | 4/4/3 | 11 | MetasoundEngine, MetasoundFrontend |
| `import_asset` | B | 1 | 5/3/3 | 11 | AudioEditor |
| `add_niagara_emitter` | G3 | 2 | 3/3/3 | 9 | NiagaraEditor, Niagara |
| `create_niagara_system` | G3 | 1 | 4/3/2 | 9 | Niagara, NiagaraEditor |
| `set_niagara_user_parameter` | G3 | 1 | 5/2/2 | 9 | Niagara |
| `get_niagara_particle_counts` | G3 | 1 | 5/2/1 | 8 | Niagara |
| `niagara_compile_request` | G3 | 1 | 4/2/2 | 8 | Niagara |
| `spawn_niagara_component` | G3 | 1 | 4/2/2 | 8 | Niagara |
| `get_niagara_user_parameters` | G3 | 1 | 4/2/1 | 7 | Niagara |
| `set_niagara_component_active` | G3 | 1 | 3/1/1 | 5 | Niagara |

#### Cycle 5 — Sequencer & MRQ (12)

| Endpoint | Axis | T | U/E/R | rank | new module(s) |
|---|---|---|---|---|---|
| `describe_sequence` | G2 | 1 | 5/3/5 | 13 | MovieScene |
| `open_sequence_editor` | G2 | 2 | 3/4/4 | 11 | LevelSequenceEditor |
| `create_widget_animation` | G2 | 1 | 4/3/3 | 10 | MovieScene |
| `sequence_add_section` | G2 | 1 | 4/3/3 | 10 | MovieScene |
| `sequence_add_track` | G2 | 1 | 4/3/3 | 10 | MovieScene, MovieSceneTracks |
| `sequence_bind_actor` | G2 | 1 | 4/3/3 | 10 | MovieScene, LevelSequence |
| `sequence_editor_play` | G2 | 2 | 3/4/3 | 10 | LevelSequenceEditor |
| `sequence_set_keys` | G2 | 1 | 5/2/3 | 10 | MovieScene |
| `widget_animation_bind` | G2 | 1 | 4/3/3 | 10 | MovieScene |
| `create_level_sequence` | G2 | 1 | 5/2/2 | 9 | LevelSequence, MovieScene |
| `render_movie_request` | G2 | 2 | 4/2/2 | 8 | MovieRenderPipelineCore, MovieRenderPipelineEditor |
| `render_movie_status` | G2 | 2 | 4/2/2 | 8 | MovieRenderPipelineCore, MovieRenderPipelineEditor |

#### Cycle 6 — one-module singles (15)

| Endpoint | Axis | T | U/E/R | rank | new module(s) |
|---|---|---|---|---|---|
| `export_heightmap` | F | 1 | 4/4/5 | 13 | ImageWrapper |
| `get_game_feature_state` | P3 | 1 | 4/4/5 | 13 | GameFeatures |
| `message_log_read` | I | 1 | 4/4/5 | 13 | MessageLog |
| `export_weightmap` | F | 1 | 3/4/5 | 12 | ImageWrapper |
| `import_heightmap` | F | 1 | 4/3/3 | 10 | ImageWrapper |
| `list_developer_settings` | A | 1 | 4/1/5 | 10 | DeveloperSettings |
| `set_water_body_profile` | F | 1 | 4/3/3 | 10 | Water |
| `validate_assets` | B | 1 | 4/2/4 | 10 | DataValidation |
| `mount_pak` | J | 2 | 4/3/2 | 9 | PakFile |
| `run_editor_utility` | A | 1 | 4/2/3 | 9 | Blutility |
| `change_game_feature_state_request` | P3 | 2 | 3/3/2 | 8 | GameFeatures |
| `change_game_feature_state_status` | P3 | 2 | 3/3/2 | 8 | GameFeatures |
| `get_settings_config_source` | H | 2 | 2/1/5 | 8 | DeveloperSettings |
| `pilot_actor` | A | 2 | 2/2/4 | 8 | LevelEditor |
| `unmount_pak` | J | 3 | 2/3/1 | 6 | PakFile |

#### Cycle 7 — third-party plugin gated (3)

| Endpoint | Axis | T | U/E/R | rank | new module(s) |
|---|---|---|---|---|---|
| `format_graph_ba_status` | P1 | 2 | 4/4/5 | 13 | BlueprintAssist, plugin:BlueprintAssist |
| `fit_comment_to_nodes` | P1 | 1 | 3/4/4 | 11 | AutoSizeComments |
| `format_graph_ba_request` | P1 | 2 | 4/3/2 | 9 | BlueprintAssist, plugin:BlueprintAssist |

---

## 3. Specs that are now WRONG — do not implement them as written

Each item names the catalogue entries it invalidates and the verbatim source that moved under them.
An implementer who follows the stale text produces a duplicate, a divergence, or a build break.

### S1 — Every "Params" table is missing the mandatory unknown-parameter guard

`RejectUnknownParams` is now a shared, non-optional helper:

```cpp
// D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeHandlers.h:97
	bool RejectUnknownParams(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
```

Namespace-scope declaration in the module's own private header — no export macro applies (same
module, same link unit) and no access specifier applies (free function in `namespace MifBridge`).
Every endpoint written since Batch B calls it first (e.g. `MifBridgeMaterials.cpp:1487`,
`MifBridgeAssetOps.cpp:263`, `MifKrBridgeEndpoints.cpp:1116` via its `Kr`-prefixed twin).

**Invalidates:** all 203 open entries. Their `Params:` tables list accepted names but no
accepted-key set and no `KeyNote` map for advertised-but-unimplemented keys. Treat every params
table as *input to* a `RejectUnknownParams` call, never as the whole contract.

### S2 — Every asset-row emitter must call `EmitAssetIdentity`, not invent `path`/`package` keys

```cpp
// MifBridgeHandlers.h:106
	void EmitAssetIdentity(const TSharedRef<FJsonObject>& Row, const FString& ObjectPath, const FString& PackageName);
```

Batch H proved the same key meant different things on different endpoints, and fixed it by emitting
`objectPath` + `packageName` on every asset row from one helper. That helper has since been
**promoted out of the two-file duplicate into the shared header** (callers at
`MifBridgeAssetOps.cpp:622`, `MifBridgeCooked.cpp:323`, `:398`, `:437`; definition at
`MifBridgeCommon.cpp:845`).

**Invalidates the response shapes of:** `find_assets_by_tag`, `get_asset_tags`, `list_content_paths`,
`get_package_disk_data`, `resolve_redirector`, `consolidate_assets`, `fixup_redirectors`,
`create_asset`, `import_asset`, `export_asset`, `validate_assets`, `list_primary_assets`,
`list_savegames`, `render_asset_thumbnail`, `mesh_asset_info`, `skeletal_mesh_info`,
`list_dynamic_meshes`, `verify_pak_contents`, `list_ue4ss_mods` — anything whose spec says it
returns a `path` or `package` field.

### S3 — "Add a local static helper" is now a build hazard, and several helpers have already moved

```cpp
// MifBridgeHandlers.h:115-121 (verbatim)
	// --- Shared helpers that used to exist as per-file copies ----------------
	// A unity build merges every unnamed namespace in a translation unit into ONE namespace
	// ([namespace.unnamed]/1), and `static` at namespace scope collapses the same way, so two files
	// that land in the same Module.MifBridge.N.cpp blob and define the same helper are a hard C2084 —
	// which is exactly how EmitAssetIdentity and CollectPIEWorlds broke the build. Blob membership is
	// a function of file SIZES and moves on its own, so "they're in different blobs today" is not a
	// defence. Everything below is declared here and defined ONCE in MifBridgeCommon.cpp.
```

Already promoted and therefore **must not be redefined locally**: `EditorWorld()` (`:127`),
`JsonTypeName(EJson)` (`:132`), `CollectPIEWorlds` (`:113`), `EmitAssetIdentity` (`:106`),
`RejectUnknownParams` (`:97`), `IsOk` (`:69`), `PlaceAndInit` (`:244`), `MarkStructural` (`:247`),
`EmitNode` (`:248`), `ResolveGraphField` (`:211`), `K2()` (`:179`).

**Invalidates:** every F-, G1-, I- and Q-axis spec whose implementation sketch opens with a local
world getter or a local JSON-type-name helper.

### S4 — `list_components`'s `source` field conflicts with the vocabulary Batch J already shipped

The C-axis spec says to add a **`source`** field. Batch J shipped a different noun with a fixed
value set:

```cpp
// MifBridgeInherited.cpp:612
		Out->SetStringField(TEXT("origin"), Res.Origin);
```
with the four values `parentBlueprintSCS` (`:621`), `native` (`:670`), `ownSCS` (`:683`) and
`notFound`, and the same file's registry comment (`:17`) names all four as "the four routes".

**Fix the spec to `origin` with those four values**, or the bridge ends up with two different words
for the same fact on two endpoints an agent calls back-to-back.

### S5 — `add_component`'s parent resolver already exists; the spec says to write a new one

Current behaviour is own-SCS only:

```cpp
// MifBridgeComponents.cpp:82
			Parent = SCS->FindSCSNode(FName(*ParentName));
```

The parent-chain resolver the spec describes building is already implemented in
`MifBridgeInherited.cpp` (it produces `Res.Origin` / `Res.ParentNode` / `Res.NativeTemplate` /
`Res.NativeMatchedBy`). Extend `add_component` onto that resolver; do not write a second one.

### S6 — `pie_status`'s spec invites a rewrite of code that half-landed

`WritePieStateInto` already implements the readiness half the Q spec asked for, with the reasoning
inline:

```cpp
// MifBridgePIE.cpp:93-100 (verbatim)
			// "running" MUST mean "the world exists and BeginPlay has happened", not merely "a session
			// was requested". UEditorEngine::IsPlayingSessionInEditor() only reports that
			// PlayInEditorSessionInfo is set, which happens BEFORE any world is created — so polling
			// on it returns running while GetPIEWorld() is still null and every actor query comes back
			// "object not found". UWorld::HasBegunPlay() is the real readiness signal.
```
and a `state` word at `:113` limited to `running` / `starting` / `stopped`.

**Remaining delta is only:** fold `simulating` (already a bool at `:110`), `stopping` (already
`stopPending` at `:109`) and `travelling` into the `state` word. Do not re-derive the readiness
logic.

### S7 — `snap_actors_to_ground`'s spec is still correct, but only its *unshipped* half

The ground-selection loop landed; the penetrating trace did not:

```cpp
// MifBridgeWorld.cpp:425
			World->LineTraceMultiByChannel(Hits, Start, End, ECC_WorldStatic, Params);
```

```cpp
// D:/UE532/Engine/Source/Runtime/Engine/Classes/Engine/World.h:1954-1956 (verbatim doc)
	 *  Trace a ray against the world using a specific channel and return overlapping hits and then first blocking hit
	 *  Results are sorted, so a blocking hit (if found) will be the last element of the array
	 *  Only the single closest blocking result will be generated, no tests will be done after that
// World.h:1965
	bool LineTraceMultiByChannel(TArray<struct FHitResult>& OutHits, ... const FCollisionResponseParams& ResponseParam = FCollisionResponseParams::DefaultResponseParam) const;
```
`class ENGINE_API UWorld final : public UObject, public FNetworkNotify` (`World.h:953`) — class-level
export. Governing access specifier for `:1965` is `public:` at `World.h:1868` (brace-depth verified,
class scope).

So a blocking prop above the landscape terminates the trace and the filter at `MifBridgeWorld.cpp:428-441`
never sees the ground — the exact defect Q described. **The fix is the `ResponseParam` argument
(all-channels `ECR_Overlap`), which is the one thing not yet written.** Do not redo the multi-hit
loop, the landscape filter, the `groundActor` selector or the `SkippedGround` diagnosis.

### S8 — `get_asset_dependencies` / `get_asset_referencers` will produce a second dependency endpoint

Shipped, flat, unclassified:

```cpp
// MifBridgeAssetOps.cpp:305
		Registry().GetDependencies(FName(*Pkg), Deps);
```

The classification the spec wants is an overload away:

```cpp
// D:/UE532/Engine/Source/Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:354
	virtual bool GetDependencies(const FAssetIdentifier& AssetIdentifier, TArray<FAssetDependency>& OutDependencies,
		UE::AssetRegistry::EDependencyCategory Category = ..., const UE::AssetRegistry::FDependencyQuery& Flags = ...) const = 0;
```
```cpp
// D:/UE532/Engine/Source/Runtime/CoreUObject/Public/Misc/AssetRegistryInterface.h:96-98 (verbatim)
		Hard = 0x1,			// The target asset must be loaded before the source asset can finish loading. ...
		Game = 0x2,			// The target asset is needed in the game as well as the editor. ...
		Build = 0x4,		// Fields on the target asset are used in the transformation of the source asset during cooking ...
```
`struct FAssetDependency` at `IAssetRegistry.h:103` — plain struct, all members public by struct
default, header-only, **no export macro needed**. `class IAssetRegistry` at `:150` carries **no
export macro either** — every method above is `= 0` pure virtual reached through the vtable, which
is why the plugin already calls `GetDependencies` successfully today with only
`"AssetRegistry"` in `Build.cs:49`. Governing access for `:354` and `:364` is `public:` at `:153`
(brace-depth verified).

**Rewrite both entries as "extend the shipped endpoint with `edgeCategories`/`edgeProperties`",
never as new endpoints.**

### S9 — Bucket-count citations and two bucket premises are stale

Live today (`self_audit`, 22:21 build): readOnly **66** / selfManaged **31** / transacted **106** /
compileHeavy **33**. `00_BASELINE.md` recorded 48/15/93/17. Batch B3 also moved `describe_class`
and `list_enum_values` into read-only. Any spec that justifies a bucket by quoting a list size is
citing a number that has moved by 40 %.

`07_SELF_AUDIT_FINDINGS.md` §registry-4 additionally records that `create_material_instance` **is**
in the transacted bucket while two source comments assert it is not — so D-axis specs that reason
"my sibling is self-managed, therefore I am" are reasoning from a false premise.

### S10 — K-axis specs that assume `kr_*` can be batched: true in source, false in the live DLL

The live 22:21 DLL answers `unknown op` for any `kr_*` inside `batch`. The fix is in source but
unbuilt:

```cpp
// MifBridgeNodes.cpp:1895
			else if (const FHandlerFn* ExtFn = FindExternalHandler(OpName))
```
declared at `MifBridgeHandlers.h:65` with the reason at `:58-64`. **Do not write a workaround.**

### S11 — `RunEndpoint`'s failure contract changed in source between the catalogue and now

Every spec's transacted-bucket justification ("one Ctrl-Z undoes the whole action") was false on the
failure path when the catalogue was written. It is now true **in source, unbuilt**:

```cpp
// MifBridgeCommon.cpp:689-692
		if (!IsOk(Out))
		{
			Transaction.Cancel();
		}
```

Consequence for open specs: the "validate everything before the first `Modify()`" restructuring that
several entries prescribe is no longer required for atomicity. It is still good practice, but it is
no longer a correctness gate — do not spend effort there twice.

### S12 — `get_asset_compilation_status` is the wrong name to build under

The dedup table ratified the merged endpoint as **`asset_compile_status`**, and that is the name six
E-axis entries put in their `Async pair` column (`commit_dynamic_mesh`, `create_static_mesh_asset`,
`set_static_mesh_lods`, `build_static_mesh`, `regenerate_skeletal_lods`, `set_nanite_settings`).
`01_CATALOGUE.md`'s B row still carries the losing name. Build `asset_compile_status`.

### S13 — the remaining sublevel entries inherit a hazard their specs predate

`create_sublevel` and `move_actors_to_sublevel` were specced before Batch I shipped six sublevel
verbs. `07_SELF_AUDIT_FINDINGS.md` §hazards-2 records that `set_sublevel_visibility` and
`set_current_sublevel` run a full synchronous level-streaming flush **inside the handler and inside
the blanket transaction**. The two open entries touch the same subsystem and their specs carry no
such warning.

### S14 — the F-axis level-instance material has a shipped PIE-domain neighbour

`create_level_instance` is withdrawn (§1), but `pie_load_level_instance` /
`pie_unload_level_instance` shipped in Batch I, which states plainly
(`06_IMPLEMENTED.md:2627`): *"is new: it is not in the F-axis entry list"*. Anyone reading F's level
instance section should be routed there rather than concluding the capability is absent.

### S15 — `set_material_parameter`'s contract changed under the open D-axis entries

Batch D.1 turned a partial success into an all-or-nothing failure and added expression aliases
(`07_SELF_AUDIT_FINDINGS.md` §regressions-2). `set_material_instance_parent`, `set_mpc_parameters`
and `set_material_instance_layers` were specced against the old partial-success semantics.

---

## 4. Highest value next — five things, with the evidence

Ordered by how much each one increases what an agent can actually do.

### 4.1 `list_components` — enumerate inherited and native components _(C, Tier 0, zero new modules)_

**The gap, in one sentence:** Batch J built the write path for inherited components and shipped no
way to find out what they are called.

`get_inherited_component` resolves **one component by name**:
```cpp
// MifBridgeInherited.cpp:646
	void H_get_inherited_component(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
```
and its own registry comment (`MifBridgeInherited.cpp:16-17`) calls it *"the read-only discovery
verb: call it FIRST to learn which of the four routes … applies."* But the only enumerator walks the
child's own SCS and nothing else:
```cpp
// MifBridgeComponents.cpp:117-122
		USimpleConstructionScript* SCS = Blueprint->SimpleConstructionScript;
		TArray<TSharedPtr<FJsonValue>> Arr;
		if (SCS)
		{
			const TArray<USCS_Node*>& Roots = SCS->GetRootNodes();
			for (USCS_Node* Node : SCS->GetAllNodes())
```
An agent editing a child Blueprint therefore sees an **empty or near-empty component list** and has
no name to feed the three endpoints the session just built. This is the single largest
capability-per-line item left.

**Everything needed is already reachable and already linked (`Build.cs` has `UnrealEd`, `Engine`):**

| API | Verbatim | Export | Access (brace-depth verified) |
|---|---|---|---|
| `USimpleConstructionScript::GetAllNodes` | `ENGINE_API const TArray<USCS_Node*>& GetAllNodes() const;` — `SimpleConstructionScript.h:77` (inside `#if WITH_EDITOR`) | method-level `ENGINE_API`; class is `UCLASS(MinimalAPI)` at `:16` | **public** — implicit after `GENERATED_UCLASS_BODY()` at `:19`, no class-scope specifier before `:77`. Empirically proven: `MifBridgeComponents.cpp:152` already calls it |
| `UBlueprintGeneratedClass::SimpleConstructionScript` | `TObjectPtr<class USimpleConstructionScript> SimpleConstructionScript;` — `BlueprintGeneratedClass.h:685` | data member — no symbol to export; class is `UCLASS(NeedsDeferredDependencyLoading, MinimalAPI)` at `:630` | **public @660** |
| `AActor::GetComponents` (native components off the CDO) | `void GetComponents(TArray<UActorComponent*, AllocatorType>& OutComponents, bool bIncludeFromChildActors = false) const` — `Actor.h:3774` (template at `:3773`) | header-defined template — no export needed; `class AActor : public UObject` at `:209` has no class-level macro | **public @3652** |

**Spec correction required first:** emit `origin`, not `source` — see §3/S4.

---

### 4.2 `add_node_by_class` — spawn any `UK2Node` subclass by class path _(C, Tier 0, zero new modules)_

**Evidence that this is the constraint:** the bridge has grown a dedicated endpoint per node class
across **seven** files — `MifBridgeNodes.cpp`, `Nodes2`, `Nodes3`, `Nodes4`, `Nodes5`, `Nodes6`,
and `MifBridgeNodes7.cpp`, the last created **2026-07-28** to add exactly one node class. That file
is 2,928 bytes and its whole payload is:

```cpp
// MifBridgeNodes7.cpp:62-64
		UK2Node_EnhancedInputAction* Node = NewObject<UK2Node_EnhancedInputAction>(Graph);
		Node->InputAction = Action;   // MUST precede AllocateDefaultPins — see the ordering trap above
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));
```

Every one of those files ends in the same three lines. A reflection-applied init map plus
`PlaceAndInit` retires the pattern and covers the node classes nobody has asked for yet.

**The spawn primitive already exists in the plugin:**
```cpp
// MifBridgeHandlers.h:244
	void PlaceAndInit(UEdGraph* Graph, UEdGraphNode* Node, int32 X, int32 Y);
```
Free function in `namespace MifBridge`, same module — no export macro and no access specifier apply.

`UEdGraph::Nodes` — `TArray<TObjectPtr<class UEdGraphNode>> Nodes;` (`EdGraph.h:79`), **public @71**,
class `UCLASS(MinimalAPI)` at `:66`; data member, no export needed.

**Carry the Nodes7 ordering trap into the spec:** properties that drive `AllocateDefaultPins` must
be applied *before* `PlaceAndInit`, or the node comes back pinless and the call looks like it
succeeded. The catalogue entry does not say this.

---

### 4.3 `connect_pins` — use the graph's own schema _(C, Tier 0, ~2 lines, zero new modules)_

**Current, verbatim:**
```cpp
// MifBridgeCommon.cpp:2052
		const UEdGraphSchema_K2* Schema = K2();
```
used for `BreakPinLinks` (`:2063-2064`), `CanCreateConnection` (`:2067`) and `TryCreateConnection`
(`:2073`).

**Why this blocks real work rather than being a purity issue:** three Tier-1 entries with U=5
(`add_anim_state_machine`, `add_anim_state`, `add_anim_transition`) create graphs whose schema is
**not** a `UEdGraphSchema_K2` at all:

```cpp
// D:/UE532/Engine/Source/Editor/AnimGraph/Public/AnimationStateMachineSchema.h:66-67
UCLASS(MinimalAPI)
class UAnimationStateMachineSchema : public UEdGraphSchema
```
(compare `AnimationTransitionSchema.h:15-16`, which *is* `: public UEdGraphSchema_K2`). Connecting
state nodes through the K2 CDO asks the wrong object whether the connection is legal, so
`connect_pins` cannot be the wiring verb for the anim family — and the anim family is the largest
coherent block of open Tier-1 work with no module cost.

**The correct call, verified:**
```cpp
// D:/UE532/Engine/Source/Runtime/Engine/Classes/EdGraph/EdGraph.h:115
	ENGINE_API const class UEdGraphSchema* GetSchema() const;
```
method-level `ENGINE_API`; class `UCLASS(MinimalAPI)` at `:66`; **public @111** (brace-depth
verified).

> **Access-specifier warning for the implementer.** `UEdGraphNode::GetSchema()` at
> `EdGraphNode.h:800` looks like the more convenient call and is also `ENGINE_API`. A naive
> backward scan for the nearest `public:`/`private:` above it lands on `private:` at `:500` and
> concludes it is inaccessible. That `private:` belongs to the **nested** `struct
> FNameParameterHelper` (`:492`–`:502`), not to `UEdGraphNode`. A brace-depth walk shows line 800 is
> governed by `public:` at `:470`. **Both calls are legal**; use `UEdGraph::GetSchema()` because the
> pin's owning graph is the authority, not the node.

---

### 4.4 `call_object_function` — invoke a `UFUNCTION` on any object path _(P2, Tier 1, U=5, zero new modules)_

**Why it is the highest-value single new endpoint:** it is the only proposed way to *execute*
cooked Blueprint logic. The bridge can now **read** cooked bytecode extremely well — `kr_dump_blueprint`,
`kr_disassemble_function`, `kr_list_events`, `kr_analyze_ubergraph` all shipped — and can
**reconstruct** it (`kr_reconstruct_request`), but it cannot run a single function. The two existing
execution-shaped endpoints are `run_console` and `run_console_captured` (both live), which execute
**console commands**, not arbitrary `UFUNCTION`s on arbitrary object paths; `02_GOTCHAS.md` §3 is
the section that routes agents down the console detour, and `07_SELF_AUDIT_FINDINGS.md`
§doc-truth-10 already flags that routing as superseded.

**The engine call, verified:**
```cpp
// D:/UE532/Engine/Source/Runtime/CoreUObject/Public/UObject/Object.h:1391
	COREUOBJECT_API virtual void ProcessEvent( UFunction* Function, void* Parms );
```
Method-level `COREUOBJECT_API`. `class UObject : public UObjectBaseUtility` (`Object.h:85`) carries
**no class-level export macro**, so the method-level macro is what makes this linkable — and
`CoreUObject` is a `PublicDependencyModuleName` (`MifBridge.Build.cs:14`). Governing access is
**public @1275** (brace-depth verified). No new module.

**Grep confirms there is no existing lane:** `ProcessEvent` appears nowhere in
`Source/MifBridge/Private/`.

---

### 4.5 `format_graph` — deterministic headless K2 graph layout _(P1, Tier 0, zero new modules)_

**Evidence:** every node-spawning endpoint in the bridge takes `x`/`y` and defaults them to zero
(`MifBridgeNodes7.cpp:64` is the newest instance: `JInt(In, TEXT("x")), JInt(In, TEXT("y"))`). An
agent that spawns 40 nodes gets 40 nodes at the origin — which is not merely ugly: it makes the
human handoff, the screenshot verification and the "did my edit land where I meant" check
impossible. The contrast is now sharp, because the *material* graph got its layout verb in Batch D:

```cpp
// MifBridgeMaterials.cpp:1517-1518
		if (Material) { UMaterialEditingLibrary::LayoutMaterialExpressions(Material); }
		else { UMaterialEditingLibrary::LayoutMaterialFunctionExpressions(Function); }
```
That is material-expression-specific (`UMaterialEditingLibrary`) and does **not** generalise to
`UEdGraph`, so K2 graphs still have nothing.

**Everything needed is a plain public data member:**

| API | Verbatim | Export | Access |
|---|---|---|---|
| `UEdGraphNode::NodePosX` / `NodePosY` | `UPROPERTY()` / `int32 NodePosX;` — `EdGraphNode.h:285-286`; `int32 NodePosY;` — `:290` | data members; class `UCLASS(MinimalAPI)` at `:272` — nothing to export | **public @277** (brace-depth verified) |
| `UEdGraph::Nodes` | `TArray<TObjectPtr<class UEdGraphNode>> Nodes;` — `EdGraph.h:79` | data member; `UCLASS(MinimalAPI)` at `:66` | **public @71** |

It also multiplies 4.2: a generic node spawner without a layout verb makes the origin-heap problem
worse, so these two should land in the same cycle.

---

## 5. UNVERIFIED

Claims I could not establish to the standard above. None of these are used in §1–§4.

1. **Whether the five "outside the log" endpoints predate the audit or were added during it.**
   `07_SELF_AUDIT_FINDINGS.md` §doc-truth-3 attributes them to commits `64d0a04`, `d432712`,
   `a245cce`. I could not confirm those commits: `D:/DDS2SDK` is not reachable as a git repo from
   this session's tooling, so the commit ids are taken on that document's word. What **is** verified
   here is that the five are live, are absent from `00_BASELINE.md`, and that
   `MifBridgeAssetOps.cpp:239` dates three of them "Added 2026-07-28".

2. **Whether `add_enhanced_input_action` should be folded into the G2 Enhanced Input entries.**
   It is live and has no catalogue row and no section in `06_IMPLEMENTED.md`. Its relationship to
   `input_map_key` / `create_input_action` / `create_input_mapping_context` (all still open) is a
   design question, not a fact I can measure.

3. **Exact per-entry effort re-scores.** §2.2 re-ranks by named cause, not by recomputing `E` for
   203 entries. Anyone who needs numeric `E` values must re-derive them from the axis files in
   `work/`, which remain the single source of truth for the ten-field entries.

4. **`travelling` as a `pie_status` state.** The Q spec lists it; I did not verify that UE 5.3
   exposes a seam-travel signal readable from `WritePieStateInto`'s vantage point. The other two
   missing states (`simulating`, `stopping`) are verified present as booleans at
   `MifBridgePIE.cpp:110` and `:109`.

5. **Whether any further dedup collisions remain.** I found one the audit missed
   (`get_message_log` ≡ `message_log_read`) by comparing engine citations across axis files. I
   compared the axis files pairwise only for entries sharing a module dependency or an obviously
   overlapping purpose; a full 203 × 203 comparison was not performed.

6. **Live behaviour of anything in source but unbuilt.** `Transaction.Cancel()`
   (`MifBridgeCommon.cpp:689`), `FindExternalHandler` in `batch` (`MifBridgeNodes.cpp:1895`), and
   the promotion of `EmitAssetIdentity`/`EditorWorld` into `MifBridgeHandlers.h` are all present in
   source and **absent from the 22:21:25 DLL**. I did not build, so their runtime behaviour is
   inferred from source alone.

---

## Appendix — snapshot integrity

`MIF_DECL` was parsed from `MifBridgeHandlers.h` at the start of this pass (**191** unique) and
re-parsed at the end (**191** unique) — the endpoint set did not move while another agent was
editing. Handler *bodies* did move; every source citation above is a snapshot, and any that is
load-bearing for a future change should be re-read before acting on it.
