# MifBridge capability audit — 00 BASELINE (existing endpoint inventory)

- **Date:** 2026-07-26
- **Engine:** `5.3.2-0+++UE5+Release-5.3-CookedEditorModKit` (source fork at `D:\UE532`)
- **Live DLL:** built Jul 26 2026 01:25:02
- **Endpoint counts:** source **160** / live editor **156** / MCP server.py **159**

This file is the "already covered" set for the capability audit: every endpoint that EXISTS in
`Source/MifBridge/Private/*.cpp` at HEAD, grouped by defining file, with its transaction bucket and
a one-line description taken from the handler source (not guessed from the name).

> **Note on counts vs. the audit brief.** The brief circulated "source 159 / MCP 158 / 3 endpoints
> pending rebuild". Re-verified against HEAD today: `spawn_actor_in_pie` (MifBridgePIE.cpp) is also
> declared, bound, and wrapped in server.py but absent from the running DLL, which makes the true
> figures **source 160, MCP 159, 4 pending rebuild**. Everything else in the brief holds.

---

## Registry health (verified 2026-07-26)

| Check | Result |
|---|---|
| `MIF_DECL` set (MifBridgeHandlers.h) vs `MIF_BIND` set (MifBridgeCommon.cpp) | **Identical — 160 = 160, no drift** |
| Live editor (`self_audit`) | Serves **156** endpoints |
| Source-but-not-live (pending rebuild/restart) | `set_viewport_camera`, `get_viewport_camera`, `focus_viewport` (bucket per source: **read-only**), `spawn_actor_in_pie` (bucket per source: **transacted**, default) |
| server.py (`tools/ue5-mcp-bridge/server.py`) | **159** tools — everything except **`diagnose_landscape_draws`**, which has no MCP tool (**drift to fix**) |
| `self_audit` invariants | `policyContradictions: 0`, `healthy: true` |
| Live bucket sizes | read-only 48 / self-managed 15 / transacted 93 / compile-heavy 17 |

## Transaction policy (three buckets)

`RunEndpoint` (MifBridgeCommon.cpp) classifies every endpoint into exactly one of three buckets
(see `docs/00_ARCHITECTURE.md`). **Read-only** endpoints run with no transaction, because an undo
step for a pure read pollutes the undo stack (this bucket also holds compile/validate/save, which
touch the object but must not become undo steps). **Self-managed** endpoints get no outer
transaction either — each opens its own tight transaction(s) around just the graph mutations —
because they run a full `FKismetEditorUtilities::CompileBlueprint` (or an equivalent
non-undoable hazard: world swap, landscape `Import()`, whole-package asset ops), and a full compile
inside a transaction means a later Ctrl-Z restores pointers to a freed class/CDO and crashes the
editor. **Everything else** (transacted) runs inside one `FScopedTransaction` wrapping the whole
handler, so a single Ctrl-Z undoes the entire bridge action; a skeleton-only regen
(`MarkBlueprintAsStructurallyModified`) is not a full compile and is transaction-safe, which is why
the variable-flag and widget endpoints stay in this default bucket.

**Legend**
- Bucket values: `read-only` / `self-managed` / `transacted`, as reported by the live `self_audit`
  (pending endpoints: from the `IsReadOnlyEndpoint` / `IsSelfManagedEndpoint` lists in
  MifBridgeCommon.cpp).
- **†** = in `self_audit`'s **compile-heavy** set (= all 15 self-managed endpoints plus `compile`
  and `validate`): runs — or may run — a full compile or equally non-undoable operation; refused
  inside `batch`; never nest them.
- **\*** = read-shaped endpoint registered in the default transacted bucket (not in
  `IsReadOnlyEndpoint`), so each call pushes an undo entry. Not a policy contradiction, but a
  cleanup candidate.
- **(pending)** = in source at HEAD but not in the running DLL; needs rebuild + editor restart.

---

## Endpoints by handler file

### MifBridgeAnimation.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `describe_animation` | read-only | Dump one animation asset's contents across every UAnimationAsset type: notifies (incl. notify-state windows and branching points), curves, sync markers, montage sections/slots, blend-space axes and samples. |
| `list_animations` | read-only | List animation assets via the asset registry only (never loads them), filterable by path substring and skeleton. |

### MifBridgeAssetOps.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `delete_asset` | self-managed† | Headless Content-Browser delete of a whole `/Game/` package via `ObjectTools::DeleteAssets` (confirm-gated, no modal dialogs). |
| `rename_asset` | self-managed† | Rename/move a `/Game/` asset via `IAssetTools::RenameAssets`; newPath's last segment is both destination folder and new name (confirm-gated). |
| `duplicate_asset` | self-managed† | Clone a `/Game/` asset to a new path via `IAssetTools::DuplicateAsset`; purely additive so not confirm-gated — fails instead of overwriting. |

### MifBridgeAuthoring.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `spawn_many` | transacted | Spawn N actors in one call from an `items[]` array; per-item mesh/material/transform overrides fall back to top-level defaults. |
| `duplicate_actors` | transacted | Duplicate a whole set of actors N times with per-copy location/rotation offsets (modular-building stamping). |
| `create_material_instance` | transacted | Mint a new `MaterialInstanceConstant` asset from a parent material with optional scalar/vector/texture parameter overrides. |
| `set_material_parameter` | transacted | Edit scalars/vectors on an EXISTING MaterialInstanceConstant; reports parameter names the parent doesn't expose instead of silently accepting them. |
| `add_foliage_instances` | transacted | Create one instanced-static-mesh actor holding N instance transforms — foliage as one draw setup and one outliner row instead of N actors. |

### MifBridgeCommon.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `self_audit` | read-only | The running DLL reports its own registry: endpoint list, transaction buckets, policy contradictions, build date/time, engine version. |

### MifBridgeComponents.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `add_component` | transacted | Add an ActorComponent class to a Blueprint's SimpleConstructionScript tree, optionally attached under a named parent node (strict class resolution — no empty-class fallback to self). |
| `list_components` | read-only | List the Blueprint's SCS component nodes. |
| `remove_component` | transacted | Remove an SCS component node (confirm-gated). |
| `set_component_transform` | transacted | Set an SCS component template's relative location/rotation/scale. |

### MifBridgeCooked.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `list_mounted_containers` | read-only | Report mounted IoStore containers (files, sizes, config/install dirs) plus container-vs-loose asset counts — answers "did the base-game mount actually work". |
| `find_assets` | read-only | Query the asset registry directly (class / pathPrefix / nameContains / origin filters); sees cooked container content that was never loaded, incl. BlueprintGeneratedClass assets with no UBlueprint wrapper. |
| `describe_package` | read-only | Say what a package IS this session: cooked or not, container or loose, loaded or not, flags, registry assets and exports — the "why does this base-game asset behave oddly" endpoint. |
| `diagnose_landscape` | read-only | Per-component landscape render-state audit of the editor world: scene proxy created, registered/visible, heightmap mip residency, resolved material and landscape-VF shader coverage. |
| `diagnose_landscape_draws` | read-only | Inspect the renderer's cached draw commands (`FPrimitiveSceneInfo::StaticMeshCommandInfos`) per landscape component to catch base-pass draws silently dropped in `CacheMeshDrawCommands`. |

### MifBridgeDataTables.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `list_datatables` | read-only | List DataTable assets from the asset registry, filterable. |
| `read_datatable` | read-only | Read a DataTable's row struct and rows serialized to JSON. |
| `get_datatable_row` | read-only | Read one named row serialized to JSON. |
| `write_datatable_rows` | transacted | Upsert rows from a JSON `rows[]` array, or full-table overwrite with `replace=true` via `CreateTableFromJSONString`; confirm-gated, broadcasts the editor change event and dirties the package. |

### MifBridgeDelegates.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `add_event_dispatcher` | self-managed† | Create an event dispatcher (delegate signature graph + multicast-delegate member variable) with optional typed params; compiles OUTSIDE the transaction so the delegate property materialises. |
| `add_call_dispatcher` | transacted | Add a "Call <Dispatcher>" node (`UK2Node_CallDelegate`); optional `targetClass` binds to a dispatcher declared on an external class. |
| `add_bind_dispatcher` | transacted | Add a "Bind Event to <Dispatcher>" node (`UK2Node_AddDelegate`); same optional external `targetClass`. |
| `list_dispatchers` | read-only | List the Blueprint's event dispatchers (delegate signature graphs). |

### MifBridgeFunctions.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `implement_interface_function` | transacted | Add the implementation graph for a return-valued interface function (mirrors `SMyBlueprint::ImplementFunction`); event-style (no-return) interface functions go via `add_override_event` instead. |
| `remove_function` | transacted | Delete a function graph (confirm-gated). |

### MifBridgeInterfaces.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `add_interface` | transacted | Add a Blueprint interface to the implemented list (strict interface-class resolution — empty name refused). |
| `remove_interface` | transacted | Remove an implemented interface (confirm-gated). |
| `list_interfaces` | read-only | List the Blueprint's implemented interfaces. |

### MifBridgeIntrospect.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `open_blueprint` | read-only | Resolve/load a Blueprint and return its identity (generated class, parent class) plus every graph with node counts. |
| `list_blueprints` | read-only | List UBlueprint assets from the asset registry (substring filter, 5000-entry safety cap). |
| `save_blueprint` | read-only | Save the Blueprint's package to disk (map-aware `.umap`/`.uasset` extension via `ContainsMap()`). |
| `save_package` | read-only | Save ANY asset's package to disk by `/Game/` path (DataTables, materials, …); a pak-mounted asset saves as a loose Content override the cook then bakes into a `_P`. |
| `backup_blueprint` | read-only | Copy the Blueprint's on-disk package file to a `.bak` beside it (fails cleanly if never saved). |
| `list_graphs` | read-only | List all graphs incl. NESTED ones (anim state machines, states, transition rules, collapsed graphs), addressed by dotted `graphId`. |
| `list_nodes` | read-only | List a graph's nodes with full pin detail (optional `hideKnots`). |
| `get_node` | read-only | Fetch one node's full serialization (pins, links, defaults) by GUID. |
| `list_variables` | read-only | List member variables with types and their current Details-panel flags. |
| `list_functions` | read-only | List the Blueprint's function graphs. |
| `describe_class` | transacted\* | Reflect over ANY resolvable class (native or BP-generated): BlueprintCallable functions with param names/types/direction, BlueprintVisible properties, multicast delegates; optional name filter. |
| `find_nodes` | read-only | Find nodes in a graph by node-class substring, title substring, or called-function name. |
| `set_variable_flags` | transacted | Partial-update the member-variable Details flag set — Replicated/RepNotify (auto-creates the `OnRep_` graph)/condition, SaveGame, transient, config, instance-editable, read-only, expose-on-spawn, advanced, interp, deprecated, category, tooltip. Only keys present are touched. |
| `add_variable` | transacted | Add a member (or local) variable using the full type grammar (array/set containers; object/class/soft/interface/enum types); accepts the same flag keys inline. |
| `rename_variable` | transacted | Rename a member variable via `FBlueprintEditorUtils::RenameMemberVariable` (graph references update). |
| `remove_variable` | transacted | Remove a member variable via `FBlueprintEditorUtils::RemoveMemberVariable` (confirm-gated). |
| `set_variable_default` | transacted | Set a member variable's `DefaultValue` string on its `NewVariables` entry. |
| `compile` | read-only† | Full Kismet compile; returns `{numErrors, numWarnings, messages[{severity, text, nodeGuid, pinName}]}`. Does NOT save to disk. |
| `run_console` | read-only | Execute an editor console command via `GEngine->Exec`; returns only whether a handler claimed it (output lands in the editor log). |
| `validate` | read-only† | Same full compile + structured diagnostics as `compile`, flagged `dryRun` — compiles and reports without saving anything to disk (use `save_blueprint` to persist). |

### MifBridgeLandscape.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `create_landscape` | self-managed† | Spawn and `Import()` a new ALandscape — components, heightmap/weightmap textures — with optional procedural height modes (flat/rolling/island), material, and paint layers. |
| `sculpt_landscape` | transacted | Raise/lower/flatten/smooth the heightmap in a world-space radius with feathered falloff (defaults to half the radius, so no vertical-walled mesas). |
| `paint_landscape` | transacted | Paint a weightmap layer in a radius; `SetAlphaData` weight normalisation implicitly pushes other layers down (hence no erase mode). |
| `bind_landscape_rvt` | transacted | Bind runtime virtual textures into the landscape's RVT array AND create the bounding `ARuntimeVirtualTextureVolume`(s) — the two halves whose absence renders terrain black; mirrors the details-panel "Create Volumes" button's real code path. |
| `landscape_info` | read-only | Report every landscape's bounds, vertex counts, scale, material, layers, and components — the aiming data all sculpt/paint world-space arguments depend on. |

### MifBridgeLevel.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `list_level_actors` | read-only | List editor-world actors (class/name/folder/selected filters, limit) with label, class, folder, and transform. |
| `spawn_actor_in_level` | transacted | Spawn one actor of a class in the EDITOR world with optional transform, label, folder. |
| `set_actor_transform` | transacted | Set a placed actor's location/rotation/scale; omitted components keep their current value (doubles as "move only"). |
| `set_actor_label` | transacted | Set the World Outliner display label (NOT the object name — safe rename) and/or outliner folder. |
| `delete_level_actor` | transacted | Delete a placed actor (confirm-gated). |
| `select_level_actors` | transacted | Set (or clear) the editor selection so a human can take over mid-task with the editor's own gizmos/tools. |

### MifBridgeNavigation.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `add_nav_volume` | transacted | Place a nav-mesh bounds volume of a given world-unit size at a location. |
| `build_navmesh` | transacted | Kick off nav-mesh generation and return immediately — building is async over subsequent frames; poll `nav_status`. |
| `nav_status` | read-only | Report nav system state including the TILE COUNT — catches "successful" builds that produced zero tiles from a mis-sized volume. |
| `move_actor_to` | transacted | Issue a nav-driven AI move; requires PIE (AIController exists only at runtime) and a built nav mesh, with the two failure modes reported distinctly. |

### MifBridgeNodes.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `add_function_call` | transacted | Add a function-call node, choosing the same `UK2Node_CallFunction` SUBCLASS the engine would (array / data-table / commutative-operator / interface-message variants) and reporting the choice as `nodeClass`. |
| `add_variable_get` | transacted | Add a variable Get node; a get whose value pin never materialised is reported as a dead reference instead of returned healthy. |
| `add_variable_set` | transacted | Add a variable Set node (same dead-reference detection as get). |
| `add_branch` | transacted | Add a Branch node (`UK2Node_IfThenElse`). |
| `add_macro_instance` | transacted | Add a macro instance (e.g. ForEachLoop), spawned fresh with `AllocateDefaultPins` — never pasted — so wildcard pins actually resolve. |
| `add_get_array_item` | transacted | Add a Get (array element) node. |
| `add_override_event` | transacted | Add an override event node for a parent-class or interface event. |
| `add_parent_call` | transacted | Add a call-to-parent-function node. |
| `add_cast` | transacted | Add a dynamic Cast node; target class is STRICT (an empty class no longer silently self-casts). |
| `move_node` | transacted | Set a node's position in the graph. |
| `remove_node` | transacted | Delete a node (confirm-gated). |
| `add_pin` | transacted | Add a parameter pin to an EXISTING function or custom event, handling the entry/result direction inversion; mirrors the Details-panel add-input/output buttons. |
| `remove_pin` | transacted | Delete a user-defined pin (incl. its `FUserPinInfo` record) or an unwired DUPLICATE pin (confirm-gated); refuses engine-allocated pins that would silently regrow on reconstruct. |
| `refresh_node` | transacted | `ReconstructNode()` — reproduces a reload's reconstruct, to prove a node's durability before cooking. |
| `connect_pins` | transacted | Wire two pins via `Schema->TryCreateConnection` (fires the wildcard/relink callbacks clipboard paste skips). |
| `reconnect_pin` | transacted | Same connect, but break the pin's existing links first. |
| `disconnect_pin` | transacted | Break a pin's links. |
| `set_pin_default` | transacted | Set an input pin's default value via `TrySetDefaultValue` — the path for SCALAR literals (see `add_literal`). |
| `splice_into_exec` | transacted | Insert a node into an exec chain after a given node, rewiring all previous downstream targets onto the inserted node's exec output. |
| `batch` | self-managed† | Run many ops in one call with a single final compile (optional pre-mutation backup); compile-heavy ops are refused inside it. |

### MifBridgeNodes2.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `resolve_struct` | read-only | Resolve a struct name to its full path — the existence check behind the type grammar. |
| `add_self` | transacted | Add a Self reference node. |
| `add_custom_event` | transacted | Add a custom event node with optional user-defined parameter pins. |
| `add_make_struct` | transacted | Add a Make Struct node for a resolved struct type. |
| `add_break_struct` | transacted | Add a Break Struct node. |
| `add_literal` | transacted | Add an OBJECT-reference literal (`UK2Node_Literal`) — object-only by design; scalar literals go via `set_pin_default`. |
| `create_function` | self-managed† | Create a new function graph with typed inputs/outputs, then full-compile outside its own tight transaction. |
| `rename_function` | transacted | Rename a function graph via `FBlueprintEditorUtils::RenameGraph` (fixes entry/result refs and child-BP override graphs; call sites in OTHER Blueprints resolve by name and do not auto-fix). |
| `rename_event` | transacted | Rename a custom event through the node's own `OnRenameNode` so `CustomFunctionName` and the cached title stay in sync. |
| `rename_event_dispatcher` | transacted | Rename a dispatcher's signature graph AND its backing multicast-delegate member variable together — renaming only one of the two breaks it. |
| `set_function_flags` | self-managed† | Set replication mode (multicast/server/client + reliable), access specifier, pure/const/CallInEditor, category/tooltip/keywords on a function or custom event; partial update, needs the full compile for replication data. |
| `create_blueprint` | self-managed† | Mint a fresh Blueprint asset (incl. function libraries, interfaces, macro libraries, widget Blueprints) with a chosen parent class. |

### MifBridgeNodes3.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `add_timeline` | transacted | Add a Timeline node plus its timeline template (optional float tracks), if the Blueprint supports timelines. |
| `add_class_cast` | transacted | Add a Cast-to-CLASS node (`UK2Node_ClassDynamicCast`) — casts a class reference, not an instance; strict class, no silent self-cast. |
| `list_enum_values` | transacted\* | Return an enum's real enumerator names (and display names) so byte/enum pin defaults — which need the exact name text — are right on the first try. |
| `add_switch_enum` | transacted | Add a Switch on Enum node with one exec pin per enumerator (Hidden/Spacer entries skipped). |
| `add_switch_int` | transacted | Add a Switch on Int node. |
| `add_switch_string` | transacted | Add a Switch on String node. |
| `add_enum_literal` | transacted | Add an enum-literal node, optionally presetting its value pin. |
| `set_pin_type` | transacted | Change a pin's `FEdGraphPinType` using the shared type grammar (retype wildcard/user-defined pins). |

### MifBridgeNodes4.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `add_sequence` | transacted | Add a Sequence node (`UK2Node_ExecutionSequence`). |
| `add_spawn_actor` | transacted | Add a SpawnActorFromClass node; setting the Class pin synthesises the class's expose-on-spawn pins. |
| `add_create_widget` | transacted | Add a UMG Create Widget node (`UK2Node_CreateWidget`); the Class pin default synthesises the widget's exposed-on-spawn property pins. |
| `add_get_subsystem` | transacted | Add a Get Subsystem node (`UK2Node_GetSubsystem`). |
| `add_make_array` | transacted | Add a Make Array literal node (wildcard element type resolves on connect). |
| `add_make_map` | transacted | Add a Make Map literal node — N Key/Value pin pairs, output `Map`; key/value types resolve when wired. |
| `add_format_text` | transacted | Add a Format Text node (`UK2Node_FormatText`). |
| `add_get_data_table_row` | transacted | Add a Get Data Table Row node (`UK2Node_GetDataTableRow`). |
| `add_comment` | transacted | Add a comment box (`UEdGraphNode_Comment`). |

### MifBridgeNodes5.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `set_property` | self-managed† | Write ANY UObject property by dot-path (`objectPath` or `blueprintId`+`widgetName`) via `ImportText` + `PreEditChange`/`PostEditChangeProperty` — the Details-panel write path; the widget-BP branch recompiles, hence self-managed. |

### MifBridgeNodes6.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `get_property` | read-only | Read one property by dot-path; value is its `ExportText` form, so structs/arrays/enums/object refs come back as readable text. |
| `list_object_properties` | read-only | Dump every top-level reflected property of an object — survey an unfamiliar asset (DataAsset, InputAction, …) without knowing field names, then descend with `get_property`. |

### MifBridgePIE.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `start_pie` | read-only | REQUEST a PIE session and return immediately (PIE startup is deferred; waiting on the game thread would deadlock — poll `pie_status`). |
| `stop_pie` | read-only | Queue the stop via `RequestEndPlayMap()` (direct `EndPlayMap` from this stack frame would tear the world down under its own callstack). |
| `pie_status` | read-only | Report PIE session state — the poll target for start/stop. |
| `list_pie_actors` | read-only | List actors in the PIE world (distinct from the editor world); returned actorPaths are LIVE objects, so `get_property` reads running values. |
| `run_console_captured` | read-only | Execute a console command while tailing `GLog` and return the output lines — the only way to see `mif.kr.*` command output, which is UE_LOG'd rather than written to the Exec archive. |
| `spawn_actor_in_pie` | transacted (pending) | Spawn an actor into the RUNNING PIE world (editor-world spawn can't reach it, and placed actors don't survive DDS2's on-play map travel) with `netMode` world targeting — exercises the mod's real BeginPlay under PIE. |

### MifBridgePipeline.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `read_modloader_log` | read-only | Tail `UE4SS.log` (Lua `print()` + Blueprint `PrintToModLoader` output) — the runtime read-back after a cook. |
| `trigger_cook` | read-only | PLAN-ONLY: return the verified retoc cook/deploy command sequence with paths pinned; executes nothing (the pipeline runs out-of-editor). |

### MifBridgeRecipes.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `recipe_add_debug_print` | self-managed† | Splice in the DEBUG-gated log call targeting a self-local `PrintToModLoader(Message)` function — created on the fly if missing — instead of `PrintString`, which is stripped from shipping builds. |
| `recipe_reset_and_loop` | transacted | Build the SET index(=-1) → [SET score(=-2.0)] → ForEachLoop cluster over an array variable; the array wildcard resolves because it is wired with `TryCreateConnection`. |
| `recipe_override_and_call_parent` | transacted | Create an override event wired straight to its parent call. |
| `recipe_splice_before_parent` | transacted | Insert a node cluster (entry..exit) between whatever currently feeds a node's exec input and that node. |
| `recipe_argmax_over_components` | transacted | Build the loop-body argmax cluster — `if (score > bestScore) { bestScore = score; bestIndex = index }` — from caller-supplied score/index pin sources. |

### MifBridgeReconstruct.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `create_editable_child` | self-managed† | Mint a PERSISTENT editable child or sibling of a cooked Blueprint via the engine fork's `CreateEditableBlueprintCopy` (compiles + saves); `full` variants reconstruct the entire Blueprint-parent chain into editable siblings instead of leaving cooked stubs. Graphs are decompiled iff the MifKismetReconstructor delegate is bound. |

### MifBridgeSpatial.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `get_actor_bounds` | read-only | Report an actor's AABB: origin, extent, size, min/max. |
| `check_overlaps` | read-only | Pure-AABB overlap audit on cached bounds (no collision queries, so it works on collisionless imported meshes); with no `actorPath` it audits the WHOLE scene. |
| `trace_ground` | read-only | Line-trace down at (x,y) and report the hit honestly — a miss is a miss, never a silent "ground at z=0". |
| `capture_camera` | read-only | Render a PNG from an ARBITRARY viewpoint via a transient SceneCapture2D that is spawned and destroyed inside the call — dirties nothing, never moves the user's viewport, and the file exists before the response returns. |
| `scene_report` | read-only | One-call scene audit: actor count, bounds, overlaps, floating/sunken actors, scale outliers — run it after placing. |

### MifBridgeUserTypes.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `create_struct` | transacted | Create a UserDefinedStruct asset with optional typed members (hides the engine's AddVariable-then-RenameVariable two-step; every mutation recompiles the struct). |
| `list_struct_members` | read-only | List a user struct's members (GUID-addressed — the only stable handle across recompiles). |
| `add_struct_member` | transacted | Add a typed member to a user struct. |
| `remove_struct_member` | transacted | Remove a member by name or GUID (confirm-gated). |
| `create_enum` | transacted | Create a UserDefinedEnum asset with optional initial values. |
| `add_enum_value` | transacted | Append an enumerator and set its display name in one call (hides the AddNewEnumerator + SetEnumeratorDisplayName two-step). |
| `remove_enum_value` | transacted | Remove an enumerator (confirm-gated). |

### MifBridgeViewport.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `set_viewport_camera` | read-only (pending) | Set the editor viewport camera transform/FOV or drop into an ortho view (top/front/side); `lookAt` wins over `rotation`. |
| `focus_viewport` | read-only (pending) | Frame an actor, a folder's actors, or the WHOLE level in the viewport — the programmatic select-all-then-F. |
| `get_viewport_camera` | read-only (pending) | Read the active viewport camera's location, rotation, projection mode, and FOV. |

### MifBridgeWidgets.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `set_widget_is_variable` | transacted | Flip `UWidget::bIsVariable` and run the skeleton-only regen that actually synthesises the member property (transaction-safe — deliberately NOT a full compile). |
| `add_widget_binding` | transacted | Add/replace an editor-time `FDelegateEditorBinding` (widget.Property → pure UFUNCTION), keyed by (ObjectName, PropertyName) exactly like the designer's OnAddBinding. |
| `remove_widget_binding` | transacted | Remove a binding by that same (ObjectName, PropertyName) identity. |
| `add_tree_widget` | transacted | `ConstructWidget` into the widget tree as root or as child of a panel widget; the designer shows it live, runtime render needs a recompile. |
| `remove_tree_widget` | transacted | Remove a widget from the tree — child, root, and named-slot cases all handled by `UWidgetTree::RemoveWidget`. |

### MifBridgeWorld.cpp

| Endpoint | Bucket | Description |
|---|---|---|
| `new_level` | self-managed† | Create a new (optionally world-partitioned) level with the save prompt forced OFF — a modal here would block the game thread and with it this HTTP server. |
| `save_level_as` | self-managed† | Save the current level to a `/Game/` path. |
| `load_level` | self-managed† | Load a level, discarding unsaved changes without asking (same no-modal reason as `new_level`). |
| `set_spline_points` | transacted | Replace a spline component's points (world/local space, point type, closed loop, optional per-point ground snap + offset) — what NPC patrol routes are authored with. |
| `get_spline_points` | read-only | Read a spline's points, length, and closed-loop flag. |
| `snap_actors_to_ground` | transacted | Trace each targeted actor down with ITSELF excluded (so a building can't "snap" onto its own roof) and set Z, optionally aligning to the surface normal. |

---

## Domain coverage summary

Where existing coverage is dense vs thin, at a glance (160 endpoints total):

| Domain | Count | Endpoints (abridged) |
|---|---|---|
| Blueprint graph authoring — nodes, pins, wiring | **40** | all `add_*` node spawners (Nodes/2/3/4), move/remove_node, add/remove_pin, refresh_node, connect/reconnect/disconnect, set_pin_default, set_pin_type, splice_into_exec |
| Functions, events, dispatchers, interfaces | 14 | create/rename/remove_function, rename_event(_dispatcher), set_function_flags, implement_interface_function, add/list dispatchers + call/bind, add/remove/list interfaces |
| Level & world authoring | 15 | level-actor CRUD + selection, spawn_many, duplicate_actors, foliage, new/save/load level, splines, snap_actors_to_ground |
| Blueprint/graph introspection | 9 | list_graphs/nodes/variables/functions, get_node, find_nodes, describe_class, resolve_struct, list_enum_values |
| User types (structs & enums) | 7 | create/list/add/remove struct members; create_enum, add/remove enum values |
| Batch & recipes | 6 | batch + 5 composite recipes |
| Session & asset save/backup | 5 | open/list/save/backup blueprint, save_package |
| Compile / diagnostics / console | 5 | compile, validate, run_console(_captured), self_audit |
| Variables | 5 | add/rename/remove, set_default, set_flags |
| Widget Blueprints (UMG) | 5 | is-variable, bindings ×2, tree widgets ×2 |
| Cooked/mounted-content introspection | 5 | containers, find_assets, describe_package, diagnose_landscape(_draws) |
| Landscape | 5 | create, sculpt, paint, RVT binding, info |
| PIE / runtime | 5 | start/stop/status, list_pie_actors, spawn_actor_in_pie |
| Spatial queries & visual feedback | 5 | bounds, overlaps, trace_ground, capture_camera, scene_report |
| Components (SCS) | 4 | add/list/remove, set_transform |
| DataTables | 4 | list/read/get_row/write_rows |
| Navigation | 4 | nav volume, build, status, move_actor_to |
| Asset lifecycle (whole packages) | 3 | delete/rename/duplicate_asset |
| Generic reflection (any UObject) | 3 | get/set_property, list_object_properties |
| Materials | 2 | create_material_instance, set_material_parameter |
| Viewport camera | 3 | set/get camera, focus (all pending rebuild) |
| Animation assets | 2 | describe_animation, list_animations |
| Blueprint/asset creation & cooked reconstruction | 2 | create_blueprint, create_editable_child |
| Pipeline hooks | 2 | read_modloader_log, trigger_cook (plan-only) |

**Dense:** Blueprint graph authoring (40), functions/events/interfaces (14), level/world authoring (15).
**Thin:** materials (2 — MaterialInstanceConstant instantiate/edit only, no material-graph editing); animation is introspection-only (no authoring); pipeline is read/plan-only (no execution); Sequencer, physics, audio, localization, source control, and Niagara have no endpoints at all.
