# MifBridge endpoint audit — shared brief for all axis agents

You are one of several agents auditing what an UE 5.3.2 editor-automation HTTP bridge (MifBridge)
could expose but currently does not. Your job: enumerate the REAL engine API surface for your
assigned axis, derive concrete endpoint proposals from what is genuinely callable, verify every
citation, and write a catalogue section an implementer can execute without opening engine source.

## Environment (verified)

| Thing | Path |
|---|---|
| Engine source (5.3.2 fork "CookedEditorModKit") | `D:/UE532/Engine/Source` |
| Engine plugins | `D:/UE532/Engine/Plugins` |
| Project | `D:/DDS2SDK/Game/DrugDealerSimulator2.uproject` |
| Plugin under audit | `D:/DDS2SDK/Game/Plugins/MifBridge` |
| Plugin Build.cs (current module deps) | `Source/MifBridge/MifBridge.Build.cs` |

Current MifBridge module deps (anything NOT here is a NEW dependency you must flag):
Core, CoreUObject, Engine, UnrealEd, BlueprintGraph, GraphEditor, UMGEditor, UMG, Kismet,
KismetCompiler, HTTPServer, Json, JsonUtilities, Landscape, Foliage, VirtualTexturingEditor,
RenderCore, Renderer, AssetRegistry, AssetTools, NavigationSystem, AIModule, EditorSubsystem,
ToolMenus, Slate, SlateCore, Projects, Sockets.

Project-enabled plugins (from .uproject): ModelingToolsEditorMode, RamaSaveSystem,
OnlineSubsystemSteam, Water, Landmass, Oceanology_Plugin, Riverology_Plugin, BlueprintFileUtils,
ThumbnailGenerator, Streamline, DLSS(+NIS/Reflex/DeepDVC), GamepadVirtualCursor, GameFeatures,
FGearPlugin, AssetSearch, BugSplat, ElectronicNodes, ChristmasDlc, DDS2Casino, AutoSizeComments.
Engine-default plugins (EnabledByDefault in their .uplugin) are ALSO active unless the uproject
disables them — check `D:/UE532/Engine/Plugins/**/<Name>.uplugin` for `"EnabledByDefault": true`
when your endpoint depends on a plugin module (e.g. GeometryScripting, EnhancedInput, Niagara,
PCG, GameplayAbilities). Record what you found: enabled-by-default, project-enabled, or WOULD
REQUIRE ENABLING (that is a cost — flag it).

The game is Drug Dealer Simulator 2. Much of /Game/** is cooked content mounted from .pak
containers: assets exist in the asset registry but Blueprint graphs are stripped (only
UBlueprintGeneratedClass ships), materials carry only cooked shader permutations, and the
dependency graph is stripped. An endpoint that only works on loose (uncooked) assets MUST say so.

## Architecture invariants (the contract — violating these is an automatic reject)

1. **Three-way registry**: every endpoint = `MIF_DECL(name)` in MifBridgeHandlers.h +
   `MIF_BIND(name)` in MifBridgeCommon.cpp + `@mcp.tool()` in server.py. You just name endpoints;
   the sync is mechanical. Names: lowercase snake_case, verb_noun, matching existing style.
2. **Transaction buckets** — every proposal states ONE bucket + justification:
   - `read-only`: pure query. No transaction (else every read pushes an empty undo entry).
   - `self-managed`: the handler opens its own tight transaction or none. REQUIRED for anything
     that runs a full FKismetEditorUtilities::CompileBlueprint, swaps/tears down the UWorld,
     creates+registers new UObjects/textures/components at scale, or deletes whole packages.
     (Full compile inside an outer transaction ⇒ reinstancing + Ctrl-Z ⇒ dead CDO ⇒ crash.)
   - `transacted` (default): wrapped in one blanket FScopedTransaction by RunEndpoint.
3. **Handlers run ON the game thread, mid-frame.** Blocking ⇒ deadlock. Anything that completes
   over multiple frames (PIE start, navmesh build, shader/asset compile, cook, lighting build,
   LOD generation, distance-field build) must be REQUEST + separate POLL endpoint. Never wait.
   Anything tearing down / swapping the UWorld must defer a tick via
   `GEditor->GetTimerManager()->SetTimerForNextTick` (inline ⇒ TickTaskManager assert ⇒ crash).
4. **Silent parameter ignore is the #1 bug class.** Every endpoint you specify lists its accepted
   parameter names + alternate spellings + types + defaults, and states that unrecognised
   parameters return an error naming the parameter (never silence). Mandatory params use strict
   resolution (empty ⇒ error naming the param), matching ResolveClassStrict precedent.
5. Style: many small composable endpoints, not god-endpoints. Structured, numerically checkable
   returns (agent cannot see the screen). Mutations propose their verification query alongside.
   NEVER propose an endpoint that merely wraps `run_console` — console commands are already
   reachable via run_console / run_console_captured.

## Verification rules (non-negotiable — an entry missing any field is incomplete)

For EVERY proposed endpoint record all ten:
1. **Engine API**: exact entry point(s), signature copied VERBATIM from the header, with
   `file:line` relative to D:/UE532/Engine/Source (e.g.
   `Editor/UnrealEd/Public/Editor.h:412`). Grep/read the actual file — no memory citations.
2. **Export check**: the class/function's export macro (ENGINE_API, UNREALED_API, EDITOR_API,
   LANDSCAPE_API, GEOMETRYSCRIPTINGCORE_API, …) verbatim. A symbol without an export macro
   cannot link from MifBridge — if the natural API is unexported, find the exported alternative
   or move the entry to UNVERIFIED/GAPS with that finding (precedent:
   ULandscapeComponent::UpdateCollisionData unexported ⇒ ALandscapeProxy::RecreateCollisionComponents).
   UFUNCTION(BlueprintCallable) static library functions in an exported class are fine.
   Note: MODULENAME_API on the CLASS exports all its methods; a method-level macro matters when
   the class itself is not exported (MinimalAPI classes export nothing but what's marked).
   MinimalAPI + BlueprintCallable is CALLABLE via reflection (FindFunction/ProcessEvent) — if you
   rely on that route, say so explicitly.
3. **Module dependency** to add to MifBridge.Build.cs (or "none — already linked"). State whether
   the module is editor-only, runtime, or lives in a plugin, and that plugin's enabled state
   (see above). Editor-only modules are fine (MifBridge is editor-only) but must never leak into
   a runtime dependency.
4. **WITH_EDITOR / WITH_EDITORONLY_DATA guards** the call sites need, if any.
5. **Transaction bucket** + one-line justification.
6. **Async**: `no` or the request/poll pair (`x_request` + `x_status`) with what the status
   payload reports.
7. **Parameter spec**: table of name | aliases | type | default | required. Unrecognised
   parameter ⇒ error. Say what each enum-ish string accepts.
8. **Failure modes**: concrete ways it fails + the error message text you'd want (name the
   parameter and the fix in the message).
9. **Cooked-content behaviour**: works / degraded / refuses on .pak-mounted assets, and why.
10. **Verification method**: how an implementer proves it works with NUMBERS (house rule:
    "numbers for correctness, pixels for taste"). Pair every mutation with the read endpoint
    that confirms it.

Plus a score line: `Unblocks`/`Effort`/`Risk`, each 1–5 (5 = unlocks a category / trivial /
safe-read), and a tier suggestion (0 = closes a known gap, 1 = high leverage low risk,
2 = valuable needs design, 3 = exotic).

**Anti-invention rule**: if you cannot open the file and paste the signature, the entry goes in
an `## UNVERIFIED` section at the bottom of your axis file, never the main catalogue. A
confidently wrong signature costs more than a missing entry. Cite what you READ, not what you
remember about Unreal.

## Already covered — diff every idea against this list (160 endpoints in source today)

add_bind_dispatcher add_branch add_break_struct add_call_dispatcher add_cast add_class_cast
add_comment add_component add_create_widget add_custom_event add_enum_literal add_enum_value
add_event_dispatcher add_foliage_instances add_format_text add_function_call add_get_array_item
add_get_data_table_row add_get_subsystem add_interface add_literal add_macro_instance
add_make_array add_make_map add_make_struct add_nav_volume add_override_event add_parent_call
add_pin add_self add_sequence add_spawn_actor add_struct_member add_switch_enum add_switch_int
add_switch_string add_timeline add_tree_widget add_variable add_variable_get add_variable_set
add_widget_binding backup_blueprint batch bind_landscape_rvt build_navmesh capture_camera
check_overlaps compile connect_pins create_blueprint create_editable_child create_enum
create_function create_landscape create_material_instance create_struct delete_asset
delete_level_actor describe_animation describe_class describe_package diagnose_landscape
diagnose_landscape_draws disconnect_pin duplicate_actors duplicate_asset find_assets find_nodes
focus_viewport get_actor_bounds get_datatable_row get_node get_property get_spline_points
get_viewport_camera implement_interface_function landscape_info list_animations list_blueprints
list_components list_datatables list_dispatchers list_enum_values list_functions list_graphs
list_interfaces list_level_actors list_mounted_containers list_nodes list_object_properties
list_pie_actors list_struct_members list_variables load_level move_actor_to move_node nav_status
new_level open_blueprint paint_landscape pie_status read_datatable read_modloader_log
recipe_add_debug_print recipe_argmax_over_components recipe_override_and_call_parent
recipe_reset_and_loop recipe_splice_before_parent reconnect_pin refresh_node remove_component
remove_enum_value remove_function remove_interface remove_node remove_pin remove_struct_member
remove_tree_widget remove_variable remove_widget_binding rename_asset rename_event
rename_event_dispatcher rename_function rename_variable resolve_struct run_console
run_console_captured save_blueprint save_level_as save_package scene_report sculpt_landscape
select_level_actors self_audit set_actor_label set_actor_transform set_component_transform
set_function_flags set_material_parameter set_pin_default set_pin_type set_property
set_spline_points set_variable_default set_variable_flags set_viewport_camera
set_widget_is_variable snap_actors_to_ground spawn_actor_in_level spawn_actor_in_pie spawn_many
splice_into_exec start_pie stop_pie trace_ground trigger_cook validate write_datatable_rows

Notes on the covered set, so you don't re-propose what exists in another shape:
- `set_property`/`get_property`/`list_object_properties` walk a dot-path from ANY objectPath —
  CDOs (`Default__<Class>`), SCS component templates (`<Name>_GEN_VARIABLE`), widget templates,
  graph-node objects, placed actors. A huge share of "missing" capabilities are really just
  set_property with a documented objectPath. Only propose a dedicated endpoint over this route
  when it adds real value (validation, batching, index/key addressing, post-edit side effects).
- `set_viewport_camera` / `get_viewport_camera` / `focus_viewport` / `spawn_actor_in_pie` exist
  in source (pending editor rebuild — live DLL serves 156 of the 160). Editor-camera control is
  CLOSED and PIE-world actor spawning is COVERED — do not re-propose either.
- Struct/enum authoring exists (create_struct/add_struct_member/…, create_enum/…).
- `run_console` + `run_console_captured` cover every console command / CVar / exec.
- `batch` composes endpoints; `validate` compiles+reports; PIE start/stop/status/actors exist
  (quality issues with pie_status are fair game to fix via a better designed endpoint).
- Known documented IMPOSSIBLES (do not re-propose; they're in docs/06_CAPABILITY_ROADMAP.md):
  collapse-to-function/macro (FBlueprintEditor::CollapseNodes* protected), node copy/paste
  (deliberate non-goal), interactive debugger stepping (blocks the HTTP pump), dependency
  queries over cooked base-game content, editing/saving cooked base-game maps, Control Rig
  graph authoring (URigVMController object model; needs a guard, not support), Sequencer/IK
  Rig/Material-expression as *K2* graphs (different object models — but dedicated
  MaterialEditingLibrary / Sequencer APIs are IN SCOPE for their axes).

## Known Tier-0 gaps (from the mission — if your axis touches one, nail it)

editor camera control (done in source — verify only), walking-NPC routing (what actually moves a
pawn along a route in-editor/PIE), material graph authoring (UMaterialEditingLibrary +
UMaterialExpression), level-material assignment validation, PIE status reliability, per-actor
cull/LOD overrides.

## Output format

Write your axis file to `D:/DDS2SDK/Game/Plugins/MifBridge/docs/audit/work/<AXIS>.md`
(you'll be told the exact filename). Structure:

```
# Axis <X> — <name>
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._

## Surface inventory
<what you actually enumerated: headers read, classes/subsystems/factories counted — with paths.
This proves coverage and lets Phase-2 spot holes.>

## Proposed endpoints
### <endpoint_name>
**Purpose**: <one sentence — what an agent can do that it could not before>
**Engine API**:
```cpp
<verbatim signature(s)>
```
<file:line for each>
**Export**: <macro, verbatim> | **Module**: <dep + status> | **Guards**: <WITH_EDITOR etc>
**Bucket**: <read-only|self-managed|transacted> — <why>
**Async**: <no | request+poll design>
**Params**: | name | aliases | type | default | required |
<unrecognised → error>
**Failure modes**: <bullet list, with error message text>
**Cooked**: <behaviour + why>
**Verify**: <numeric proof method>
**Score**: U<u> E<e> R<r> → tier <t> <optional: prevents documented failure X>

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)
<attractive APIs that are NOT viable: unexported symbols, protected methods, editor-module
circularity, cooked-content dead ends, 5.3.2-specific absences (APIs added in 5.4+). Cite.>

## UNVERIFIED
<ideas you could not confirm — one line each with what's missing>

## Coverage log
<what you covered, what remains — so an interrupted run can resume>
```

Your final agent reply: a compact JSON-ish summary only (counts per tier, the endpoint names,
top-5 highlights, negative-result count). The FILE is the deliverable, not your reply.

## Search tactics that work here

- `grep -rn "class UNREALED_API" D:/UE532/Engine/Source/Editor/UnrealEd/Public/...` style sweeps
  to find exported classes; then read the header regions you cite.
- UFUNCTION BlueprintCallable editor libraries live mostly in
  `Editor/Blutility`, `Plugins/Editor/EditorScriptingUtilities`, `Editor/*Subsystem*`, and
  per-domain `*EditingLibrary` / `*EditorLibrary` / `*Statics` classes. These are DESIGNED for
  scripting — prefer them: stable, exported, parameter-validated.
- Check `Engine/Plugins/Editor/EditorScriptingUtilities` early — many classic gaps
  (StaticMeshEditorSubsystem etc.) live there; note the plugin's enabled state.
- For subsystems: `grep -rln "public UEditorSubsystem" D:/UE532/Engine/Source D:/UE532/Engine/Plugins`.
- Windows paths with forward slashes work in Grep/Glob/Read tools.
- Engine source is huge; grep narrow (per-directory), read only the line ranges you need.
