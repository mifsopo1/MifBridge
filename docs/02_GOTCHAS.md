# MifBridge — parameter grammar and gotchas

Everything here is a trap someone actually hit. Read this before spending a probe.

---

## 1. Parameter names

### Node identity — all spellings accepted

Endpoints that address **one** node (`get_node`, `move_node`, `remove_node`, `refresh_node`,
`disconnect_pin`, `set_pin_default`, `set_pin_type`, `remove_pin`) accept **any** of:

```
nodeGuid | node | guid | nodeId
```

Endpoints with **two or more** node parameters keep their distinct names on purpose — aliasing
there would let one node satisfy two roles:

| Endpoint | Parameters |
|---|---|
| `connect_pins`, `reconnect_pin` | `srcNode`, `dstNode` |
| `splice_into_exec` | `afterNode`, `insertNode` |
| `recipe_splice_before_parent` | `parentNode`, `clusterEntry`, `clusterExit` |
| `recipe_argmax_over_components` | `loopBodyNode`, `scoreNode`, `indexNode` |

> **GUID format is not the issue.** `FGuid::Parse` accepts both the dashed 36-char form and the
> undashed 32-char form, on every endpoint. Every GUID the bridge emits is
> `FGuid::ToString()`'s default (`EGuidFormats::Digits`, undashed). If one endpoint appeared to
> want dashes and another not, the real mismatch was the **field name** — now fixed by aliasing.

### Class parameters — required means required

An empty class name resolves to *the blueprint's own class*. That is deliberate for
`add_function_call` (empty = "call on self"), and it used to be a silent trap everywhere else
(see [PM-001](01_POSTMORTEMS.md)). Endpoints where the class is mandatory now **reject** an
empty value and accept alternate spellings:

| Endpoint | Primary | Also accepted |
|---|---|---|
| `add_cast`, `add_class_cast` | `targetClass` | `class`, `castTo`, `to`, `targetType` |
| `add_spawn_actor` | `actorClass` | `class` |
| `add_create_widget` | `widgetClass` | `class` |
| `add_tree_widget` | `widgetClass` | `class` |
| `add_get_subsystem` | `subsystemClass` | `class` |
| `add_component` | `componentClass` | `class` |
| `add_interface`, `remove_interface` | `interface` | `interfaceClass`, `class` |
| `add_variable_get`, `add_variable_set` | `targetClass` | `class`, `ownerClass` |

`add_function_call`'s `class` is unchanged: empty still means self, which is what you want.

### Pin names — output aliases

Nodes disagree about what to call their single value output. When an exact name misses, `FindPin`
retries within one alias group and accepts the hit **only if exactly one pin matches** — so a node
that genuinely has the pin you asked for is never redirected:

| Group |
|---|
| `ReturnValue` · `Result` · `Output` · `OutputPin` |
| `Array` · `OutArray` |
| `Out Row` · `OutRow` · `Row` |

Known surprises: `add_format_text` → **`Result`** (not `ReturnValue`);
`add_get_data_table_row` → **`Out Row`** (with a space); `add_cast` → **`As<ClassName>`**.
When in doubt, `get_node` returns every pin with its exact name.

---

## 2. Type grammar (`type` field on `add_variable`, `create_function` pins, `set_pin_type`)

**Scalars** — `bool` · `byte` · `int` · `int64` · `float` · `double` · `real` · `string` · `name` · `text`

**`float` is now a true 32-bit float.** Previously `float`, `double` and `real` all produced a
64-bit `PC_Double` pin, so a real float pin was unreachable — which blocked UMG delegate bindings,
because `TAttribute<float>` properties (`PercentDelegate`, `OpacityDelegate`, …) require a
**float**-returning `UFUNCTION` and reject a double one.

| Spelling | Result |
|---|---|
| `float`, `float32`, `single` | `PC_Real` + `PC_Float` — 32-bit |
| `double`, `float64`, `real` | `PC_Real` + `PC_Double` — 64-bit |

Float and double pins still interconnect through the schema's autocast. **If you have existing
graphs that passed `"float"` expecting the old 64-bit behaviour, change them to `"double"`.**

**References need a prefix.** Struct, enum and class names may be given bare, but a *reference*
does not resolve without one:

```
object:<ClassOrPath>      class:<C>   (alias subclassof:<C>)
softobject:<C>            softclass:<C>
interface:<C>             enum:<E>
```

Paths work: `object:/Game/BP/BP_Foo.BP_Foo_C`. Containers go in the separate `container` field
(`array` | `set`). **`map` is not supported** — it needs a value type the grammar can't express.

---

## 3. Cooked Blueprints

Cooking strips the editor-only `UBlueprint` entirely; only the `UBlueprintGeneratedClass` ships.
`list_graphs` / `list_nodes` / `find_nodes` therefore **cannot** read a cooked Blueprint's graphs —
this is a property of the asset, not a bridge limitation.

The error now says so, and names the tool for the job instead of reporting "blueprint not found":

- **To read the logic** — decompile with the reconstructor via `run_console`:
  `mif.kr.Reconstruct <BP>` (also `mif.kr.DumpBP`, `mif.kr.DumpFull`, `mif.kr.Events`,
  `mif.kr.AnalyzeUbergraph`, `mif.kr.VerifyFidelity`)
- **To edit it** — mint an editable copy first:
  `create_editable_child {sourceAsset, variant:"full"}`, then point subsequent calls at the
  returned `blueprintId`

---

## 4. Variable flags

`set_variable_flags` (and the same keys on `add_variable`) is a **partial update** — only keys you
actually pass are touched.

```
replicated · repNotify · repNotifyFunction · replicationCondition
saveGame · transient · config
instanceEditable · blueprintReadOnly · exposeOnSpawn · advancedDisplay · interp · deprecated
category · tooltip
```

- **Member variables only.** Locals are stack values for one call — never replicated, never saved.
  Asking for these on a local is an explicit error, not a silent no-op.
- `repNotify` / `repNotifyFunction` implies `replicated`, and **creates the `OnRep_<Var>` function
  graph** if it does not exist (the compiler errors without it). The response reports
  `createdRepNotifyGraph`.
- `replicationCondition` takes an `ELifetimeCondition` — `COND_None`, `COND_OwnerOnly`,
  `COND_SkipOwner`, `COND_InitialOnly`, … The `COND_` prefix is optional.
- A replicated variable does nothing unless the **owning Actor** replicates. If `bReplicates` is
  false the response carries `replicationWarning`; the bridge does not flip it for you. Set it with
  `set_property {propertyPath:"bReplicates", value:"True"}`.
- `exposeOnSpawn` implies `instanceEditable`.
- `list_variables` reports the current `flags` for every variable, so changes are verifiable
  without opening the Details panel.

---

## 5. Removing pins

`remove_pin {node, pin, confirm:true, direction?}` handles exactly two cases:

| `kind` | What |
|---|---|
| `userDefined` | Function input/output, custom-event parameter, tunnel pin — the Details-panel **X** button. Removes the live pin *and* its `FUserPinInfo` record (dropping only one leaves the node "out-of-date" at compile), then syncs sibling Return nodes in the same graph. |
| `duplicate` | Two pins share a name+direction. Keeps a wired copy, drops the twin. This is the repair path for the old duplicate exec pin — see [PM-004](01_POSTMORTEMS.md). |

Anything else is **refused**: engine-allocated pins are recreated by `AllocateDefaultPins` on the
next reconstruct, so "removing" one would silently revert.

---

## 6. Animation

### Animation Blueprints — nested graphs are now reachable

`UAnimBlueprint` is a `UBlueprint`, so the normal graph endpoints have always *loaded* one. What
they could not do was look **inside** it: state machines, individual states, and transition rule
graphs are not in the blueprint's four top-level graph arrays — they hang off the **nodes**
(`UEdGraphNode::GetSubGraphs()`). `GatherGraphs` now recurses, so `list_graphs`, `list_nodes` and
`find_nodes` reach all of them. The same fix exposes **collapsed/composite node** bodies
(`UK2Node_Composite::BoundGraph`), which were equally invisible.

Nested graphs get a **dotted `graphId`**:

```
/Game/Anims/ABP_Scooter.ABP_Scooter::AnimGraph.Locomotion.Idle
```

A top-level graph still produces exactly its own name, so **every previously issued `graphId` keeps
working**. A bare leaf name is still accepted when unambiguous; when two state machines both hold a
state called `Idle`, `ResolveGraph` refuses to guess and lists the full paths.

AnimGraph nodes (`UAnimGraphNode_*`) serialize through the generic `UEdGraphNode` path — class,
title, pins, types and links all work with no extra module dependency. For node-specific settings
(blend times, thresholds) use `get_property` / `list_object_properties` against the node.

### Animation assets — `list_animations` / `describe_animation`

Sequences, montages, blend spaces and composites are **not** Blueprints, so no graph endpoint could
ever see them. Two read-only endpoints cover them:

- `list_animations {filter?, skeleton?, limit?}` — asset-registry only, does **not** load assets.
  Reports `truncated` so a cap never looks like completeness.
- `describe_animation {assetPath}` — one endpoint across all types. Common: `skeleton`,
  `playLength`, `rateScale`, `notifies[]` (trigger time, duration, notify vs notify-**state** class,
  branching points), `curves[]`. Then per type:
  - **sequence** — `numSampledKeys`, `frameRate`, `additive`, `syncMarkers[]`
  - **montage** — `blendInTime`/`blendOutTime`, `sections[]` (with `nextSection`, which is what makes
    a montage loop or chain), `slots[]` → `segments[]`
  - **blendSpace** — `blendAxes[]` (unused axes omitted), `samples[]` with X/Y coordinates

### Still not possible: cooked Animation Blueprints

A cooked ABP's AnimGraph is not bytecode — it is baked into `AnimNodeProperties` +
`BakedStateMachines`, which the reconstructor would have to interpret. See
[`03_RECONSTRUCTOR_PROMPTS.md`](03_RECONSTRUCTOR_PROMPTS.md).

---

## 7. Behaviours that are not bugs

- **`Array_Find` won't stay typed — use a macro.** A raw `Array_Find` call node's wildcard pins can
  be forced to a type and compile clean, but revert to wildcard on save+reload. For a durable
  key→value lookup over parallel arrays use `ForEachLoop` + name-compare + `GetArrayItem`.
  `refresh_node` reproduces the reload — use it to test durability before you cook.
- **Compile-heavy ops run alone.** `create_function`, `create_blueprint`, `recipe_add_debug_print`,
  `batch`, `set_property` (widget-BP branch), `create_editable_child`, `add_event_dispatcher` and
  the asset-lifecycle ops compile outside the blanket transaction, because a full compile
  reinstances the class and a later Ctrl-Z over that would restore a dead CDO and crash. Don't nest
  them.
- **Double-loaded Blueprints** (some modded/cooked assets load as two copies with identical node
  GUIDs) need `graphId`-scoped node resolution — pass `graphId` alongside the node GUID.
- **`add_literal` is object-only** — scalar literals go via `set_pin_default`.
- **Asset lifecycle is `/Game/`-only.** `delete_asset` / `rename_asset` require `confirm=true`;
  `duplicate_asset` does not, since it never destroys anything.
- **Logging** — recipes use `PrintToModLoader` (hooked by UE4SS), because `PrintString` is stripped
  from shipping builds.
