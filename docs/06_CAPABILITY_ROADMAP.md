# MifBridge — capability roadmap

From a fan-out audit (2026-07-25): 8 domains audited against the real endpoint list and the UE 5.3
engine source, each finding adversarially re-verified to strip out "gaps" that were already covered.

**Result:** 6 blocking, 17 high-value, 19 nice-to-have, 10 impossible-or-elsewhere, in 14 work
packages. Two domains came back largely refuted — **debugging/diagnostics is in good shape**
(compile messages are already node- and pin-mapped, graph read-back is complete, `batch` already
functions as compile-many, undo/redo is drivable via `run_console`), leaving PIE control as its only
real gap.

---

## Already fixed from this audit

| Was | Now |
|---|---|
| `add_function_call` always spawned a plain `UK2Node_CallFunction`, so **every array op was unauthorable** | Mirrors the engine's node-class selection chain → `UK2Node_CallArrayFunction` / `CallDataTableFunction` / `CommutativeAssociativeBinaryOperator` / `CallMaterialParameterCollectionFunction`, plus `UK2Node_Message` for interface calls. Response reports `nodeClass`. |
| `save_package`/`save_blueprint` wrote `.uasset` for Worlds, shadowing the real `.umap` | Picks the extension via `UPackage::ContainsMap()` |
| `batch`'s compile-op disallow-list had drifted from `IsSelfManagedEndpoint` | Derived from it via `IsCompileHeavyEndpoint()` |
| No RPC/access flags on functions or events | `set_function_flags` |
| **Signatures frozen at creation** — adding one parameter meant destroying and rebuilding the function/event | `add_pin` (mirrors the editor's entry/Return direction inversion and sibling-Return sync) |
| **TMap unexpressible anywhere** — every typing path rejected `container=map` | `valueType` on `MakePinType`, threaded through `add_variable`, `add_pin`, `create_function`, `add_event_dispatcher`, `set_pin_type`; `SerializePinType` reports it back |

> **The `Array_Find` gotcha was never an `Array_Find` quirk.** Wildcard pins on a plain
> `CallFunction` compile 0/0 and then revert to wildcard on save+reload because nothing re-resolves
> them on reconstruct. `UK2Node_CallArrayFunction` owns that propagation logic. The
> "use a ForEachLoop macro instead" workaround in the README is no longer required — though it
> remains valid.

---

## Blocking

Of the original six blocking items, **five are now fixed** (see the table above). One remains:

| # | Gap | Fix |
|---|---|---|
| 1 | **No user-defined struct or enum authoring.** Zero references to `FStructureEditorUtils` / `FEnumEditorUtils`; `create_blueprint`'s allowlist has no entry for either. `resolve_struct` / `add_make_struct` / `add_switch_enum` can only consume types a human made by hand. Not reachable via `set_property` (members live in a container the dot-walker refuses mid-path, and a raw write would skip `CompileStructure`), and there is no Python — `PythonScriptPlugin` is absent from the project. | `FStructureEditorUtils` / `FEnumEditorUtils`; both in already-linked modules |

---

## High value

**Reflection addressing** — three findings that are *already possible but undiscoverable*:
- `SerializeNode` never emits `GetPathName()`, so the node-property route documented in
  `02_GOTCHAS.md` doesn't actually resolve. Blocks every details-panel-only node property
  (anim transition `CrossfadeDuration`/`BlendMode`, cast purity, switch defaults).
- **Component defaults already work** via `set_property` on
  `/Game/X/BP_Foo.BP_Foo_C:<Name>_GEN_VARIABLE` — verified end-to-end — but `_GEN_VARIABLE` appears
  nowhere in the plugin, README or docs. Flagged independently by three domains.
- **CDO editing already works** via `Default__<Class>`, likewise undocumented.

**Structural editing**
- `add_component` cannot attach to a **native or inherited** parent — the only lookup is
  `SCS->FindSCSNode`, so attaching to a Character's `Mesh` hard-fails.
- Local variables are write-once and invisible (`list_variables` iterates `NewVariables` only).
- No variable retype — repair means remove + add, dropping every get/set node, flags and category.
- No rename for a function, event, graph or dispatcher. **`rename_variable` on a dispatcher is an
  active footgun**: it renames the backing delegate variable and the next skeleton regen breaks it.

**Coverage**
- No generic add-node-by-class, so `UK2Node_Select`, `GenericCreateObject`, the async-action family,
  `MultiGate`, `SwitchName` and the whole AnimGraph set are unreachable.
- No `UK2Node_CreateDelegate` → a dispatcher can only be bound to a freshly-authored custom event,
  never from inside a function or macro graph.
- No element-level property addressing (no index/key grammar), so `UCurveVector` Y/Z, BlendSpace
  axes 1–2, and material-instance parameters need whole-array rewrites.
- No asset import and no non-Blueprint asset creation (no DataTable with a chosen RowStruct, no
  Curve, DataAsset, MaterialInstance, InputAction).
- No PIE control — "compiles clean" is the strongest claim the bridge can make unaided.
- No level-actor handles. `set_property` **already moves a placed actor correctly**; only discovery
  is missing (`ULevel::Actors` has no `UPROPERTY`).
- UMG: no one-call tree enumeration and no reparent/reorder. `add_tree_widget` always constructs a
  *new* object, so remove+add loses identity, bindings and every property edit.
- `set_property` full-compiles a Widget Blueprint on **every single write** — laying out one widget
  is four full compiles.
- `connect_pins` hardcodes the K2 schema CDO, so `UAnimationGraphSchema` overrides never run.

---

## Impossible, or belongs elsewhere

- **Collapse/expand to composite, function or macro** — `FBlueprintEditor::CollapseNodes*` are all
  `protected`.
- **Copy/paste/duplicate of nodes** — a *deliberate design choice*, not an oversight. The whole
  premise is that programmatic edits avoid the clipboard path's wildcard/relink failures.
- **Interactive debugger stepping** — `EnterDebuggingMode` spins a Tick-only loop that never pumps
  the HTTP server.
- **Dependency queries over cooked base-game content** — the dependency graph is stripped at cook.
- **Editing or saving base-game cooked maps.**
- **`diff_blueprints`** — redundant; `SerializePin` already emits `linkedTo`, so two `list_nodes`
  dumps are a complete client-side diff.
- **Control Rig graph authoring** — every edit must go through `URigVMController`; different object
  model. *Guard against it rather than support it* — `URigVMBlueprint` derives from `UBlueprint` and
  stores pages in `UbergraphPages`, so mutating endpoints will happily accept one today.
- **Sequencer / IK Rig / Material expression graphs** — each an entirely new object model.
- **Cooked AnimBP reconstruction** — belongs to MifKismetReconstructor; see
  [`03_RECONSTRUCTOR_PROMPTS.md`](03_RECONSTRUCTOR_PROMPTS.md).

---

## New module dependencies, if these are built

| Module | Unlocks |
|---|---|
| `MovieScene` + `MovieSceneTracks` | UMG widget animations |
| `AnimGraph` | AnimGraph node authoring (`UAnimGraphNode_*`, state nodes) |
| `InputBlueprintNodes` | Enhanced Input action nodes only — legacy input nodes need nothing |
| `MaterialEditor`, `GameplayTags(+Editor)`, `MessageLog`, `DataValidation` | Low priority |

**Explicitly NOT needed** (contrary to two domain claims): `SubobjectDataInterface` is already
transitive through `UnrealEd`, and PIE can be driven with `GEditor->RequestPlaySession` rather than
taking a `LevelEditor` dependency.

---

## Suggested order (remaining)

1. **Docs truth pass** — an unusually large share of findings were "already possible, nowhere
   documented" (`_GEN_VARIABLE`, `Default__<Class>`, widget `Slot.*`, node object paths). Zero code,
   immediate value, and it retires several apparent "gaps" outright.
2. **Struct / enum authoring** (medium) — the last blocking item. `FStructureEditorUtils` +
   `FEnumEditorUtils`, plus `create_asset` to mint them.
3. **Reflection addressing** (medium) — `nodeGuid`/`componentName` as `set_property` targets and
   `objectPath` in `SerializeNode`. The highest-ratio package left: it turns three
   already-possible-but-undiscoverable capabilities into first-class ones and fixes the
   documented-but-broken node-property route.
4. **Signature and naming, remainder** (small) — `rename_function` / `rename_event` /
   `rename_event_dispatcher` (and make `rename_variable` refuse a dispatcher's backing delegate
   variable, which is currently an active footgun), `create_macro`.
5. **Variables remainder** (small) — `set_variable_type`, local-variable lifecycle
   (`list_variables scope=local`, rename/remove/default for locals).
6. **Level and actor editing** (medium) — `UEditorActorSubsystem`, no new dependency. The value is
   the returned `actorPath`; `set_property` already edits a placed actor correctly once you have one.
