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

Paths work: `object:/Game/BP/BP_Foo.BP_Foo_C`. Containers go in the separate `container` field:
`array` | `set` | `map`.

**Maps need two types.** For `container: "map"`, the `type` field is the **key** and a separate
`valueType` field is the **value**:

```json
{ "name": "ItemCounts", "type": "name", "container": "map", "valueType": "int" }
```

Underneath, the key occupies the usual `PinCategory`/`PinSubCategory` and the value goes into
`FEdGraphPinType::PinValueType`. `SerializePinType` reports it back as a nested `valueType` object,
so `TMap<Name,int>` and `TMap<Name,bool>` are distinguishable on read-back. This works everywhere
typing does — `add_variable`, `add_pin`, `create_function` params, `add_event_dispatcher` params,
`set_pin_type`. **Map values cannot themselves be containers** (Blueprint has no
`TMap<K, TArray<V>>`); wrap the value in a struct instead.

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

## 4b. Function / event flags

`set_function_flags` covers the Details-panel controls for a **function graph or a custom event** —
target it by `nodeGuid` (custom event), `graphId`, or `blueprintId` + `function`.

```
replicates: none | multicast | server | client      reliable
access: public | protected | private                pure · const · callInEditor
category · tooltip · keywords
```

Partial update, same contract as `set_variable_flags`. Things worth knowing:

- **A replication change triggers a full compile**, and other blueprints that *call* the function
  keep stale call-site bytecode until they are recompiled too. A skeleton regen is not enough —
  the class's replication data and `NetFields` list are only built by a full compile.
- **An overriding custom event is refused.** Its net flags come from the parent, which is why the
  editor greys the whole row out. Change them on the declaring class.
- `pure` and `const` are function-graph only; the panel hides them for events.
- **UE 5.3's function-graph Details panel has no Replicates row at all** (it's gated on
  `bIsCustomEvent`), so `set_function_flags` on a function graph exposes something the editor hides.
  The flags are real and the compiler honours them — you just can't verify them by clicking.
- Warnings, not errors, for: RPC on a non-replicated Actor; `reliable` without `replicates`; and
  `pure` + RPC (which the compiler does *not* reject — it just zeroes the return value whenever the
  call executes remotely).

> **Making an event an RPC RENAMES it.** Verified live: a custom event called `MulticastBoom` came
> back as **`Multicast_MulticastBoom`** after `replicates:"multicast"` — the engine prefixes the
> function name to match the RPC convention. `Run on Server` and `Run on owning Client` prefix
> similarly. The node GUID is stable, but anything holding the old *name* goes stale: a
> `find_nodes {byTitle}`, a `set_function_flags {function:"..."}` by name, a call site addressed by
> name. **Address the event by `nodeGuid` after changing its replication**, and re-read the returned
> `target` to learn the new name.

---

## 4c. Array nodes and node-class selection

`add_function_call` picks the same `UK2Node_CallFunction` **subclass** the engine would, and reports
it back as `nodeClass`:

| Function metadata | Node class |
|---|---|
| `MD_ArrayParam` (all of `UKismetArrayLibrary`) | `UK2Node_CallArrayFunction` |
| `MD_DataTablePin` | `UK2Node_CallDataTableFunction` |
| commutative + pure | `UK2Node_CommutativeAssociativeBinaryOperator` |
| `MD_MaterialParameterCollectionFunction` | `UK2Node_CallMaterialParameterCollectionFunction` |
| interface function on an external target (or `asMessage: true`) | `UK2Node_Message` |

> **This supersedes the old "`Array_Find` won't stay typed" gotcha.** That was never an `Array_Find`
> quirk — a plain `CallFunction` has none of the wildcard-propagation logic that ties `TargetArray`'s
> element type to its neighbours, so forced types compiled `0/0` and then reverted to wildcard on
> save+reload. `UK2Node_CallArrayFunction` owns that logic. Array Add/Remove/Contains/Length/Find/
> Insert/Append/Sort are now directly authorable. The `ForEachLoop` macro workaround still works but
> is no longer required. `refresh_node` remains the way to prove durability before you cook.

`UK2Node_PromotableOperator` is deliberately **not** selected: the engine gates it on type-promotion
registry state, and one spawned outside that path comes up with unresolved wildcards.

---

## 5. Adding and removing pins

### `add_pin` — change a signature without rebuilding

`add_pin {name, type, direction?, container?, default?}` plus a target: `graphId`,
`blueprintId` + `function`, or `nodeGuid` (a custom event).

**The direction inversion is the thing to know.** You say `input`/`output` in *function* terms and
the endpoint maps it — but underneath, a function's inputs live on the **entry** node as
`EGPD_Output` (the entry emits arguments into the graph) and its outputs live on the **Return** node
as `EGPD_Input`. If you ever address those nodes directly, that is why the directions look backwards.

- **A custom event has no outputs.** Events are fire-and-forget; `direction:"output"` is refused.
- **Adding an output to a void function creates a Return node** and wires it from the entry's exec.
  Without that link the Return is unreachable, the out-param is never written, and the value feeding
  it is dead-code-eliminated — it compiles clean and does nothing.
- **Outputs are mirrored onto every sibling Return node**, all with the *same* name. A graph with
  several Return nodes shares one signature; letting each uniquify independently would give the same
  parameter different names on different returns, which does not compile.
- The name is uniquified if taken; the response reports the final name and a `warning` when it
  differs from what you asked for.
- `CanCreateUserDefinedPin` is consulted first, so an illegal type/direction fails before anything
  is mutated.

## 5b. Removing pins

`remove_pin {node, pin, confirm:true, direction?}` handles exactly two cases:

| `kind` | What |
|---|---|
| `userDefined` | Function input/output, custom-event parameter, tunnel pin — the Details-panel **X** button. Removes the live pin *and* its `FUserPinInfo` record (dropping only one leaves the node "out-of-date" at compile), then syncs sibling Return nodes in the same graph. |
| `duplicate` | Two pins share a name+direction. Keeps a wired copy, drops the twin. This is the repair path for the old duplicate exec pin — see [PM-004](01_POSTMORTEMS.md). |

Anything else is **refused**: engine-allocated pins are recreated by `AllocateDefaultPins` on the
next reconstruct, so "removing" one would silently revert.

---

## 5c. User-defined structs and enums

`create_struct` / `add_struct_member` / `remove_struct_member` / `list_struct_members`,
`create_enum` / `add_enum_value` / `remove_enum_value`.

**Blueprint types only.** Native C++ structs and enums cannot be edited — the endpoints refuse them
with that reason rather than a generic "not found".

Two engine quirks these endpoints hide, worth knowing if you inspect the assets directly:

- **A struct member's real `VarName` is not what you typed.** The engine appends a GUID suffix; what
  you see in the editor is the separate `FriendlyName`. Members are addressed internally by `FGuid`,
  which is why `list_struct_members` returns one — it is the only stable handle across the recompile
  that every edit triggers. `remove_struct_member` accepts either a name or a guid.
- **An enum entry's `FName` is engine-generated too.** The text you pass to `create_enum` /
  `add_enum_value` becomes the *display name* (`DisplayNameMap`), which is what Blueprint shows and
  what `list_enum_values` reports.

Both types must keep **at least one** member/entry or they will not compile, so removing the last
one is refused. A freshly created struct ships with a placeholder member; it is only dropped once
your own members exist.

> **Removing a non-final enum entry shifts every later index down.** Anything that stored the enum
> by index — switch-on-enum nodes, saved defaults — silently re-points to a different value. The
> response warns when this happens; refresh affected switch nodes afterwards.

---

## 5d. Reflection routes that already work

`set_property` / `get_property` / `list_object_properties` take **any** `objectPath` and walk a
dot-path from it. That covers far more than it looks like it does — a capability audit found that a
large share of apparent "gaps" were really just undiscoverable object paths. These all work today.

### Class defaults (the CDO)

Every class has a default object named `Default__<ClassName>`, outered to the package
(`DEFAULT_OBJECT_PREFIX` in `ObjectMacros.h`). Point `set_property` at it to edit **Class Defaults**:

```json
{ "objectPath": "/Game/BP/BP_Scooter.Default__BP_Scooter_C",
  "propertyPath": "bReplicates", "value": "True" }
```

This is the route for `bReplicates`, `NetUpdateFrequency`, `bAlwaysRelevant`, `InitialLifeSpan`,
default variable values — anything the Class Defaults panel shows.

### Component defaults

An SCS component's editable template is outered to the **generated class** and named
`<ComponentName>_GEN_VARIABLE` (`USimpleConstructionScript::ComponentTemplateNameSuffix`). The `:`
separates the object from its subobject:

```json
{ "objectPath": "/Game/BP/BP_Scooter.BP_Scooter_C:Mesh_GEN_VARIABLE",
  "propertyPath": "StaticMesh", "value": "/Game/Meshes/SM_Body.SM_Body" }
```

That sets every component default — `StaticMesh`, `Mobility`, `AnimClass`, `OverrideMaterials`,
collision profile, relative transform. `list_components` reports the name; append the suffix.

### Widget layout

`add_tree_widget` places a widget; **all** of its layout is `set_property` on the widget template,
via the `blueprintId` + `widgetName` form:

```json
{ "blueprintId": "/Game/UI/WBP_HUD.WBP_HUD", "widgetName": "HealthBar",
  "propertyPath": "Slot.Anchors.Minimum", "value": "(X=0.5,Y=0.0)" }
```

`Slot.*` covers anchors, alignment, offsets, size rules and z-order — the slot class varies with the
parent panel (`UCanvasPanelSlot`, `UHorizontalBoxSlot`, …), so `list_object_properties` on the
widget is the fastest way to see what its slot actually exposes. Styles, brushes, fonts, colours and
padding are all ordinary properties on the widget itself.

> **Caveat:** the widget branch of `set_property` runs a **full compile on every write**, so laying
> out one widget with four properties is four compiles. Batch what you can.

### Node properties

Details-panel-only node settings have no dedicated endpoint, but every node serialization now emits
`objectPath` — feed it straight to `set_property`:

```json
{ "objectPath": "<objectPath from list_nodes>",
  "propertyPath": "CrossfadeDuration", "value": "0.25" }
```

That covers anim transition `CrossfadeDuration` / `BlendMode` / `PriorityOrder`, cast purity, switch
defaults, comment node colours — anything the node exposes as a `UPROPERTY`.
`list_object_properties` against the same path enumerates what a given node class actually has.

> After changing a property that affects a node's pins, call `refresh_node` so the node reconstructs.

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

- **Array-library calls are first-class now** — see §4c. The old "`Array_Find` won't stay typed, use
  a `ForEachLoop` macro" rule no longer applies: the cause was the spawned node class, and it is
  fixed. `refresh_node` still reproduces a reload reconstruct, so it remains the way to prove
  durability before you cook.
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

## Never silence a mutating call

`delete_level_actor` requires `confirm: true`. A cleanup script that piped its output to `/dev/null`
never saw `{"ok":false,"error":"delete_level_actor requires confirm=true"}` — so three landscapes
accumulated on top of each other (plus a leftover UDS actor fighting the sky), and the resulting
z-fighting and black sky got blamed on the landscape material for several rounds.

Worse, `[ -n "$OLD" ] && api delete_level_actor ... >/dev/null && echo "removed"` prints "removed"
regardless: `curl` exits 0 on any HTTP response, including a JSON body reporting failure.

**Rule:** always parse `ok` from a mutating endpoint's response. Never `>/dev/null` a call that
changes state, and never treat a transport-level exit code as the operation's result.
