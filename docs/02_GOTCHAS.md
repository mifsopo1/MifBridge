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
> out one widget with four properties is four compiles — and that branch **cannot be batched**: it
> refuses itself inside `batch`, because reinstancing captured by batch's open transaction leaves a
> dead CDO for the next Ctrl-Z. The `objectPath` branch (CDO edits, component templates, node
> properties, placed actors) compiles nothing and **is** batchable; batch it freely. Until Batch K
> the ban covered the whole endpoint, so this paragraph told you to do something the code refused.

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

## 5e. FText in DataTables — `NSLOCTEXT` in reads is not corruption

A read that comes back with

```json
"Description": "NSLOCTEXT(\"DT_Currency [4819EC...]\", \"DOLAR_CurrencyDescription\", \"Plain description here\")"
```

looks like the bridge wrapped your string. It did not. **That is the engine's lossless FText export
and the data is intact** — the display string is the *third* argument.

### Why reads look like that

`UDataTable::GetTableAsJSON` defaults to `EDataTableExportFlags::None`
(`Engine/Classes/Engine/DataTable.h:328`). With `None`, every `FText` is written by
`ExportText_Direct` in its complex lossless form. The readable alternative is gated on
`EDataTableExportFlags::UseSimpleText` — *"Export text properties as their display string, rather
than their complex lossless form"* (`DataTableUtils.h:21`), applied at `DataTableUtils.cpp:213`.
`UseSimpleText` **drops the namespace and key**, so it is lossy and cannot be the default.

### `textFormat` — pick your poison explicitly

`read_datatable` and `get_datatable_row` take `textFormat`, aliases `textMode` and `simpleText: true`:

| Value | FText comes back as | Round-trip |
|---|---|---|
| `export` (**default**) | `NSLOCTEXT("ns","key","source")` | Safe — `write_datatable_rows` merge mode accepts it verbatim |
| `simple` | the display string only | **Lossy** — namespace/key are gone; writing it back mints new ids |

An unrecognised *value* is an error naming the accepted set, never a silent fall back to the
default. The effective value is echoed as `textFormat`, and an `export` response that actually
contains `NSLOCTEXT(` carries a `textNote` saying it is not damage. Clean tables stay quiet.

### The asymmetry that caused the bug report: merge vs replace

`write_datatable_rows` has two modes that disagree about `FText`, **one flag apart**:

| Mode | Parser | Plain `"some text"` in an `FText` column becomes |
|---|---|---|
| merge — `replace:false` (**default**) | `FJsonObjectConverter::JsonObjectToUStruct` | an unlocalized `FText`; NSLOCTEXT input round-trips exactly |
| replace — `replace:true` | `CreateTableFromJSONString` → `DataTableUtils::AssignStringToProperty` (`DataTableJSON.cpp:753/772`) | a **localized** `FText` with a generated namespace `"<TableName> [<guid>]"` and key `"<RowName>_<ColumnName>"` |

So a plain string written by **replace** reads back as `NSLOCTEXT(...)` — which is what the report
described. It wraps **once** and is then stable (verified live: cycles 2 and 3 byte-identical),
because the stored `FText` really is localized now. A successful replace on a row struct that holds
any `FTextProperty` now returns `textLocalizationNote` stating this.

> **Prefer merge (`replace:false`) unless you intend a full-table overwrite.** Merge is the
> round-trip-safe mode; replace empties the table and re-imports, and re-localizes your `FText`
> columns on the way in.

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
- **Compile-heavy ops run alone.** They compile outside the blanket transaction, because a full
  compile reinstances the class and a later Ctrl-Z over that would restore a dead CDO and crash.
  Don't nest them. The list that used to sit here named 8 of ~25 and had drifted — **do not read it
  from a document at all.** `self_audit` reports the live set as `transactionBuckets.compileHeavy`,
  computed from the same predicate `batch` refuses with (`IsCompileHeavyEndpoint`, which derives from
  the self-managed bucket rather than a second literal list). As of Batch K that is every
  self-managed endpoint plus `compile`/`validate`, **minus** `set_property`, which fences only its
  widget branch (see §5d).
- **Double-loaded Blueprints** (some modded/cooked assets load as two copies with identical node
  GUIDs) need `graphId`-scoped node resolution — pass `graphId` alongside the node GUID.
- **`NSLOCTEXT(...)` in a DataTable read is the engine's lossless FText export**, not a wrapped or
  corrupted value — see §5e for `textFormat` and the merge-vs-replace asymmetry.
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

## 8. The bridge stops answering but the editor is alive — look for a modal window

### The threading model, stated correctly

**Handlers run SYNCHRONOUSLY, inline, inside `FHttpServerModule`'s own tick.** `FHttpServerModule`
derives from `FTSTickerObjectBase`, so the request handler is *already* on the game thread when it is
called — from `FTSTicker::GetCoreTicker().Tick()`, which `FEngineLoop::Tick()` runs **after
`GEngine->Tick()` has completed the entire world tick**, outside every tick group. That is the
safe point, and the handler simply runs there and replies. `MifBridgeServer.cpp:229-265`.

> **This document previously said handlers are dispatched via
> `AsyncTask(ENamedThreads::GameThread, …)`. That is backwards, and the source says so explicitly:
> "Do NOT reach for AsyncTask".** `AsyncTask` enqueues onto the game thread's *named-thread task
> queue*, which is pumped not only between frames but also from inside
> `FTickTaskSequencer::ReleaseTickGroup() -> WaitUntilTasksComplete()` — so an endpoint that
> recompiles a Blueprint reinstances actors **in the middle of a tick group**, leaving
> `FTickTaskManager` iterating `FTickFunction`s whose owning objects have just been trashed. The next
> one to execute lands on `check(!"Pure virtual not implemented")` (`EngineBaseTypes.h:409`) inside
> `FTickFunctionTask::DoTask()` — a hard crash with **no MifBridge frame in the stack at all**, so it
> reads as a spontaneous editor failure. It reproduced on every compile-heavy request and was misread
> for a long time as a project-side teardown bug. The fix was to stop deferring, not to defer better.

The only path that still hops is the off-game-thread one (reachable only if the HTTP server is ever
driven by another transport): it hands the work to `FTSTicker` — the *same* post-world-tick point —
and blocks until it has run, because `FHttpResultCallback` is valid only for the duration of the
handler call. Capturing it and invoking it a frame later was tried and turned a crash-on-compile into
a crash-on-every-request.

### Why the modal/blocking hazard is WORSE than an async model, not better

Any modal window — ours, the engine's, or a third-party plugin's — spins its own loop, the tick
stops, and the bridge stops reading the socket. Every call then times out with no response at all.

**Because handlers run inline in that same ticker, a blocking handler blocks the very ticker that
would have to advance whatever it is waiting on.** There is no separate worker to keep answering, and
nothing else pumps the wait: an endpoint that waits on a tick, a deferred callback, a streaming
flush, or its own next frame is waiting on a ticker it is itself occupying. An async model would have
degraded to "this one request is slow". This one degrades to "the whole bridge is gone until a human
intervenes" — which in an unattended run means forever.

The symptom is indistinguishable from "the bridge crashed", so check the process first:

```bash
powershell -NoProfile -Command "Get-Process UnrealEditor | Select-Object Id,MainWindowTitle"
```

A `MainWindowTitle` that is not the normal editor title is the answer. Real instance (2026-07-27):
BlueprintAssist's launch popup (`MainWindowTitle: "BA Welcome Screen"`) blocked a whole automated
build+prove cycle. Suppressed with `bShowWelcomeScreenOnLaunch=False` under
`[/Script/BlueprintAssist.BASettings_EditorFeatures]` in
`Saved/Config/WindowsEditor/EditorPerProjectUserSettings.ini`.

**This is why every endpoint spec in `docs/audit/` must state its modal/blocking hazards** — an
endpoint that opens a dialog does not merely fail, it takes the entire bridge down until a human
clicks something, which in an unattended run means forever. See `03_GAPS_AND_RISKS.md` §2 for the
inventory of engine calls that do this.

**A dialog is not the only way to stop the ticker.** Every handler runs *inline* in the ticker (see
the threading model above), so any unbounded wait is the same outage with no dialog to click — and
self-deadlocking, because the wait is holding the ticker that would satisfy it. Declared as of
Batch K:

| endpoint | what stops the ticker | disposition |
|---|---|---|
| `rename_variable` | `FSuppressableWarningDialog` when the variable has a RepNotify function (`BlueprintEditorUtils.cpp:4837`) | **CLOSED** — refused, pointing at `set_variable_flags {repNotify:false}`; the engine's modal path is now unreachable from HTTP |
| `audit_unused` | `ScanPathsSynchronous` on a mount root; an unconditional `WaitForCompletion()`; `GetReferencers` per asset regardless of `limit` | **CLOSED** — three hard refusals (≥2 path segments for `rescan`, "registry still scanning" instead of waiting, 20000-asset cap) |
| `set_sublevel_visibility`, `set_current_sublevel` | `SetLevelVisibility` → `FlushLevelStreaming()` → `FlushAsyncLoading()` (`World.cpp:4533`) | **DECLARED** — bounded in an editor world; moved to the self-managed bucket so the cascade is no longer captured by the blanket transaction |
| `add_sublevel`, `set_sublevel_streaming` | `AddLevelToWorld`'s unconditional `FScopedSlowTask::MakeDialog()` (`EditorLevelUtils.cpp:387`) — Slate ticks, `FTSTicker` does not | **DECLARED** — both call sites defer, so no response is left pending, but concurrent requests stall |
| `save_dirty_packages` | `SaveWorld`'s `FScopedSlowTask … MakeDialog(true)`, once per dirty map (`FileHelpers.cpp:767`) | **DECLARED** |
| `diagnose_landscape_draws` | `FlushRenderingCommands()` — a game/render-thread sync per call | **DECLARED** — bounded, tens-to-hundreds of ms on a heavy scene |

"Declared" means the endpoint's own comment block says so. "Closed" means the hazard cannot be
reached by any HTTP request.

---

## 9. Numbers are strict, and a write is checked before it happens

Two caller-visible rules changed in Batch L. Both came from live probes that returned `ok:true`
about something the caller had not asked for; the full evidence is in
[`audit/06_IMPLEMENTED.md`](audit/06_IMPLEMENTED.md) and [PM-006](01_POSTMORTEMS.md).

### A supplied value of the wrong JSON type is an ERROR, never a default

`set_actor_transform {location:{"x":"not-a-number","y":123,"z":456}}` used to answer `ok:true` and
move the actor to `{700,123,456}` — `y` and `z` applied, `x` quietly left alone, and the response
echoed that mixture as if it were the request. **Omitting a field still means "leave it alone".
Supplying one the bridge cannot read is now a hard error naming the field path (`location.x`), the
value and the expected type.**

- Vectors take `{x,y,z}` **or** `[x,y,z]` everywhere now (`add_component` and
  `set_component_transform` used to accept only the array form).
- Rotators additionally take `{pitch,yaw,roll}`. `x/y/z` still means pitch/yaw/roll.
- `scale` additionally takes a bare number, meaning uniform.
- An unrecognised key **inside** a vector object is refused: `{"x":1,"y":2,"zz":3}` is a typo, not a
  2D vector.
- A wholly-numeric **string** is still accepted (`"12.5"`). A partly-numeric one is not: `"12abc"`
  is refused because UE's parsers take the `12` and discard the rest without saying so.
- The rule is enforced centrally, so it applies to every numeric and boolean parameter on every
  endpoint — `limit`, `radius`, `fov`, `count`, `confirm` — not only to transforms. The response
  carries `ignoredParameters` when it trips.

> **`set_actor_transform {relative:true}` used to double the components you did NOT send.** It
> seeded its deltas from the actor's current transform and then added the current transform again,
> so `{relative:true, location:{"x":100}}` moved x by 100 and doubled y and z. Fixed; deltas seed
> from zero.

### `set_property` and friends check the value against the property TYPE before importing

`override_inherited_component {properties:{"SphereRadius":"not-a-float"}}` used to answer
`ok:true, applied:true, wanted:"0.000000"`. UE's float importer parsed the garbage as `0.0` and
reported success, and the post-write verification then compared `0` against `0` and passed —
**verifying that a write landed does not verify that the value was understood.**

Now refused up front, with the reason and the accepted form:

| kind | what is refused |
|---|---|
| numeric | anything that does not parse WHOLE — `"12abc"`, `"not-a-float"`, and **exponent form** (`"1e5"`), which UE's parser cannot read and would store as `1` |
| bool | any word that is not `True/False/Yes/No/On/Off/1/0` — an unrecognised word used to import as **False** |
| enum | any name that is not a real entry; the valid entries are LISTED in the error. A wrong name used to import as 0, the FIRST entry |
| object / class ref | a path that does not resolve (soft refs are exempt — they legitimately name unloaded assets) |

Structs and containers handed over as **export text** cannot be pre-checked; the response says so in
`typeValidated:false` + `typeValidationNote` instead of implying otherwise. Send those as typed JSON
and every leaf is checked individually.

### `set_property` on a PLACED actor's component reruns its construction scripts

That is engine behaviour, not a bridge choice: the `PreEditChange`/`PostEditChange` pair triggers
`RerunConstructionScripts`, which destroys the component, renames it `TRASH_<Class>_N` and builds a
replacement. The bridge now **re-resolves the component after the rerun** and verifies against the
new object, reporting `reconstructed`, `retargetedTo` and `verifiedOn`; if it cannot re-resolve, the
call FAILS as unverified rather than reporting `verified:true` about a destroyed object.

Two consequences worth knowing:

- An instance edit of a property `FComponentInstanceDataCache` does not carry — transient, no
  `EditAnywhere`/`Interp`, a multicast delegate, or written by the construction script itself — will
  now honestly fail. It cannot survive the rerun. **Edit the component template
  (`…_GEN_VARIABLE`) instead**, which is also what makes the change apply to every instance.
- The notification is `PostEditChangeChainProperty` now, not `PostEditChangeProperty`. It is a
  strict superset (it calls the plain one at the end) and it is the only one that reaches archetype
  instances, so a CDO edit propagates to already-placed actors instead of waiting for a reload.

## 10. What destroys work, and what `ok:false` does not promise

The single most important thing on this page:

> **`ok:false` does NOT mean "nothing changed."**

`RunEndpoint` wraps transacted endpoints in an `FScopedTransaction` and calls `Transaction.Cancel()`
when a handler reports failure (`MifBridgeCommon.cpp:771`, `:802-805`). `Cancel` **discards the undo
entry** — it never calls `FTransaction::Apply`, so it reverts nothing. Handlers that must be atomic
were reordered to validate *before* they create, and those report
`outcome: "preflight-rejected-nothing-created"`. Everything else may have written something.

**Always re-read the target after a failed call.** Do not assume failure means no-op.

`batch` is the sharpest case: it opens its own transaction (`MifBridgeNodes.cpp:1870`) and returns
`ok:false` on the first failing op with **every prior successful op already committed**
(`:1976`). Its own error text says so. A batch is not a unit of work.

**`apply_graph_patch` is the one exception, and only because it does not rely on the transaction
system at all.** It keeps an explicit inverse for every operation it applies and replays those
inverses in reverse order when one fails, so its rollback is real. That is also why its op set is
small — `connect_pins`, `disconnect_pin`, `set_pin_default` are exactly invertible; node creation is
not offered, because undoing it means deleting nodes, which is destructive enough to need its own
confirm. Create nodes with `add_*` first, then wire them in one patch.

Two things it does that a loop of single calls cannot:

* **Preflight.** Every guid and pin is resolved and every connection is put to
  `CanCreateConnection` *before* anything is mutated. A bad op is refused with the graph untouched,
  which is the same diagnosis you would have got 20 operations in — minus the cleanup.
* **Reports displaced links.** Exec outputs and data inputs are single-link, so connecting to an
  already-wired pin silently breaks what was there. `apply_graph_patch` says
  `replaced N existing link(s)` in that op's result, and its rollback restores those links too —
  snapshotting only the new link would leave the old wire destroyed while reporting a clean rollback.

`allowPartial:true` opts back into batch-like behaviour and says so in a `warning` field.

#### What a "connect" actually does (read this before touching `apply_graph_patch`)

`UEdGraphSchema::TryCreateConnection` (`Runtime/Engine/Private/EdGraph/EdGraphSchema.cpp`) is not one
atomic link. Four separate behaviours matter, and each one broke a naive inverse during development:

1. **`BREAK_OTHERS_A/_B/_AB` silently destroy existing wires.** Exec outputs and data inputs are
   single-link. Undoing only the new link leaves the displaced wire gone while reporting a clean
   rollback. Both endpoints' full `LinkedTo` sets must be snapshotted.
2. **Connecting WIPES the destination input pin's default.** It then calls `PinConnectionListChanged`
   on both owning nodes, and `UK2Node`'s base override calls `ResetPinToAutogeneratedDefaultValue` on
   a newly-connected input (`K2Node.cpp`). Restoring links without defaults silently loses it.
3. **Pins can be freed.** That same override can `RemovePin()` an orphaned pin, and
   `UEdGraphSchema_K2::TryCreateConnection` guards its own `PinA` with `IsPendingKill()` — the engine
   itself treats the pin as possibly dead after the call. **Never hold a `UEdGraphPin*` across a graph
   mutation.** `apply_graph_patch` stores node-guid + pin-name + direction and re-resolves at the
   moment of use; so should anything else that batches edits.
4. **`MAKE_WITH_CONVERSION_NODE` creates a whole extra node**, and `MAKE_WITH_PROMOTION` rewrites pin
   types. Neither has an inverse a journal can replay, so both are refused.

#### Wildcards make preflight verdicts expire

`UEdGraphSchema_K2::ArePinTypesCompatible` returns true **unconditionally** when either side is
`PC_Wildcard`. So a connect into an unresolved `Array_Add` / `Select` / `MakeArray` pin passes
preflight no matter what type is on the other end — and the *first* such connect resolves the pin,
which can make a *later* op in the same patch illegal, or legal only via a conversion node.

Two consequences, both learned by an adversarial review of a version that had neither:

* **Validate at apply time, not only at preflight.** `apply_graph_patch` re-asks
  `CanCreateConnection` immediately before `TryCreateConnection` and refuses if the verdict is no
  longer invertible. Without that re-check the engine quietly inserts a conversion node that the
  rollback then orphans, under a `rollbackComplete:true`.
* **Restoring links is not restoring the node.** A wildcard resolved by the patch may not revert.
  The endpoint therefore snapshots each touched node's *pin shape* (every pin's name, direction and
  full type) and verifies it came back; a mismatch sets `rollbackComplete:false` and
  `rollbackReshapedNodes` rather than claiming success. Restoring an inverse must also **notify** the
  owning nodes, because the forward path does — an un-notified inverse leaves node-side state adrift
  from the wires.

#### Testing a rollback: make sure the failure is a *runtime* failure

A patch containing a nonexistent pin fails at **preflight**, so nothing is applied and nothing is
rolled back. A test built that way asserts "the graph came back" over a graph that was never touched
and passes no matter how broken the rollback is. Two of this endpoint's tests were written that way
and had to be redone. To force a genuine mid-apply failure, use the wildcard tripwire: connect an
`int` into `Array_Add.NewItem`, then a `string` into the same pin — legal at preflight, illegal by
the time it runs. Assert `preflightErrors == 0` and `rolledBack > 0` before trusting the result.

#### A `self` pin can legally take MORE THAN ONE source, and that decides replace vs append

Reported from production: a 12-connection rewire returned `12/12 OK`, but a read-back showed 4
destinations replaced cleanly and **8 left carrying both the old and the new source**. All 12
destinations were ordinary object-reference input pins named `self`.

Not corruption — deliberate engine behaviour.
`UEdGraphSchema_K2::DetermineConnectionResponseOfCompatibleTypedPins`
(`EdGraphSchema_K2.cpp:2106-2150`) skips the break-existing step when **all** of:

* the destination is already linked, and
* `IsSelfPin(*InputPin)`, and
* `OwningNode->AllowMultipleSelfs(false)` is true, and
* neither pin is a container, and
* no existing link is an array

…then it falls through to plain `CONNECT_RESPONSE_MAKE` — **append, not replace**. UE uses that to
expand one call over several targets.

For `UK2Node_CallFunction`, `AllowMultipleSelfs` is `CanFunctionSupportMultipleTargets`: **no return
value, impure, and not latent**. So the outcome is decided by the *callee's signature*:

| destination | signature | multi-target? | engine does |
|---|---|---|---|
| `Setup.self`, `SetVisibility.self` | impure, no return | yes | **appends** — 2 sources |
| `Get btnRemoveSubstance.self` | has a return value | no | **replaces** — 1 source |

Two visually identical `self` pins, opposite results. A caller cannot predict this per-operation, so
`apply_graph_patch` no longer leaves it to the schema: **`existingLinkPolicy` defaults to `replace`**
and explicitly clears the incumbent link first. `preserve` opts into multi-target; `reject` refuses
and names what is in the way. Every connect result now carries `sourcesBefore`, `sourcesAfter`,
`replacedExisting` and `appendedToExisting`, and a `replace` that failed to clear the pin is reported
**failed** rather than as a successful rewire.

> **Exec inputs are exempt, and must stay exempt.** Several exec outputs converging on one exec input
> is ordinary fan-in; the engine never auto-breaks it (`bBreakExistingDueToDataInput` is gated on
> `!IsExecPin(*InputPin)`). A blanket "one source per input" rule would silently tear down valid
> graphs. The policy only touches non-exec inputs.

The standalone endpoints already encoded this distinction: `connect_pins` is
`DoConnect(bBreakFirst=false)` and **`reconnect_pin` is `DoConnect(bBreakFirst=true)`**. If you want
replace semantics from a single call, `reconnect_pin` is the one to reach for.

The wider lesson, and the third time this exact shape has bitten: **"the requested link exists" is not
"the graph is how the caller asked for it."** Verify the postcondition, not the call.

#### A pin pointer is unsafe across far more than `ReconstructNode()`

The `add_pin` crash taught that `ReconstructNode()` destroys and reallocates every `UEdGraphPin` on a
node. Auditing the rest of the module turned up a wider rule, stated by the engine itself in
`UEdGraphSchema_K2::BreakPinLinks`:

> `// cache this here, as BreakPinLinks can trigger a node reconstruction invalidating the TargetPin reference`

`UEdGraphSchema::BreakPinLinks` calls `PinConnectionListChanged` on the owning node **unconditionally**
and on every node at the far end of the links it destroys, then `NodeConnectionListChanged` on all of
them when `bSendsNodeNotification` is true — which is the override `UK2Node_Select` uses to call
`ReconstructNode()`. `TryCreateConnection` ends the same way.

**So a `UEdGraphPin*` is unsafe across any of:** `ReconstructNode`, `BreakPinLinks`,
`BreakAllPinLinks`, `TryCreateConnection`, `PinConnectionListChanged`, `RemovePin`. Passing
`bSendsNodeNotification: false` narrows it but does not close it — `PinConnectionListChanged` still
runs, and it can `RemovePin` an orphan.

Four sites were holding pointers across exactly that, all in the shape *snapshot an array of pins,
then loop over it calling something destructive*:

| site | what it held | across |
|---|---|---|
| `remove_pin` (userDefined) | several pins on the **same** node | `BreakPinLinks(..., true)` |
| `remove_pin` (dup cleanup) | same array | `BreakPinLinks(..., false)` |
| `SpliceExecAfter` | the downstream targets | `BreakPinLinks` on their own link |
| `SpliceExecBefore` | the upstreams **and `TargetIn`** | the pin the engine comment names |

Use **`FMifPinRef`** (`MifBridgeHandlers.h`) — node guid + pin name + direction + graph. Node guid
survives a rebuild and pins come back under the same names, so `CapturePin` before the destructive
call and `ResolvePin` after, handling null: a pin that genuinely did not come back must be reported,
never dereferenced. `CapturePins` does a whole array (e.g. a `LinkedTo` snapshot).

`tools/scan_pinloops.py`-style greps will not catch every instance; the rule is the thing to hold.

#### `add_pin direction=output` on a function with no Return node

It always failed with *"cannot add that pin: Cannot add input pins to function entry node!"* — an
error naming the wrong direction, about a node the pin was never going to land on. The preflight
picked a `UK2Node_FunctionResult` to validate against, but when the function had none yet it fell
back to the **entry** node and asked whether it would accept an `EGPD_Input`, which
`UK2Node_FunctionEntry` refuses outright. The code that creates the Return node sat below and was
never reached.

A CDO is not a valid stand-in for the missing node: `UK2Node_FunctionTerminator::CanCreateUserDefinedPin`
consults `IsEditable()` and `CanModifyExecutionWires()`, which are instance state. Both terminators
share every check except one direction rule, so the fix asks the **entry** about the direction it
accepts (`EGPD_Output`) — identical type/exec/editable question — and the direction rule is satisfied
by construction, since the pin goes onto a Result node as an input.

#### Macro libraries are discovered from the ASSET REGISTRY, and `macroPath` defaults to one

A user needed "Switch Has Authority", tried `SwitchHasAuthority` and `Switch Has Authority` at
`add_macro_instance`, had both refused, and concluded from the refusals that the node must be a
dedicated `K2Node` class needing a new endpoint. They later placed it by hand, read it back, saw
`K2Node_MacroInstance`, and retracted the report themselves.

The node was reachable the whole time. `/Engine/Content/EditorBlueprintResources/` holds three of
them:

| library | contains |
|---|---|
| `StandardMacros` | ForEachLoop, DoOnce, Gate, FlipFlop, IsValid, ... (24 graphs) |
| `ActorMacros` | **Switch Has Authority**, Create and Assign RenderTarget |
| `ActorComponentMacros` | **Switch Has Authority** |

`macroPath` defaults to `StandardMacros`, so everything in the Actor libraries was invisible to a
caller who did not already know it was there. Their *second* guess was the right name in the wrong
library - and note the graph name genuinely **contains spaces**, so "guess the internal name by
removing them" is wrong here too (`Do N` and `Create and Assign MID` are the same shape).

Three things now close the loop:

* **`list_nodes` / `get_node` report a `macro` block** on every `K2Node_MacroInstance` - `graphName`,
  `library`, `libraryName`, `displayTitle`, and `addMacroInstanceArgs` holding the exact
  `macroGraph` + `macroPath` to pass back. Copy those; do not re-derive them from the title.
* **A miss lists `availableMacroGraphs`** for the requested library plus `didYouMean`, and **searches
  every other macro library the asset registry knows** - `foundInOtherLibrary` names each exact
  `macroPath` to retry with, and the error lists them all when a name exists in several (`Switch Has
  Authority` is in both `ActorMacros` and `ActorComponentMacros`, which are NOT interchangeable).

  > The first version of this hardcoded the three `EditorBlueprintResources` paths. An engine-wide
  > search then turned up a fourth library - `ArtTools/RenderToTexture/Macros/RenderToTextureMacros`
  > - that the list could never have reached. It rotted inside the session it was written in. The
  > registry also covers macro libraries the PROJECT defines, which is what a user is most likely to
  > be reaching for; the `BlueprintType` asset tag identifies them without loading anything.
* **Matching ignores case and spacing**, so `switchhasauthority` resolves; the response says
  `matchedBy: "normalized"` when it was not an exact hit.

The general lesson: a *failed guess is not evidence about a node's type*. When an endpoint refuses a
name, the refusal must say what it does know - the alternatives - or the caller's next inference is
made from nothing.

#### `add_bind_dispatcher` already binds EXTERNAL dispatchers, via `targetClass`

Reported as a missing feature: binding `DDS2_GameMode.PlayerLoggedChanged` from another Blueprint
failed with *"event dispatcher 'PlayerLoggedChanged' not found on SKEL_Modactor_C"*, and the caller
concluded the endpoint had no way to name an external owner.

The endpoint has accepted `targetClass` all along; the **MCP tool never passed it**, so an agent
driving through MCP could not express the call. The capability existed and was unreachable - see the
next section.

    add_bind_dispatcher(graph_id, "PlayerLoggedChanged", target_class="DDS2_GameMode")
    connect_pins(cast_node, "AsDDS2GameMode", bind_node, "self")   # the Target pin
    # then add_custom_event with the delegate signature -> its OutputDelegate into "Delegate"

`targetClass` names the CLASS that declares the dispatcher, never the object; the object goes into
the node's Target pin with `connect_pins`. `describe_class` reads the delegate's parameter list.

#### An endpoint can accept a parameter no tool can send

The two halves of the surface drift independently. `parity_check.py` compared endpoint **names** on
the UE side and checked parameters only for the Blender addon, so "endpoint grows a parameter, MCP
tool never exposes it" was invisible - which is how `targetClass` above stayed unreachable long
enough to be reported as a missing feature.

`tools/param_reach.py` now checks it, and runs as part of `parity_check.py`. It reads the accepted
keys out of each handler's `RejectUnknownParams` list and the sent keys out of every `_post()` call
site. Most of the difference is alias spellings, so it **ratchets**: the accepted backlog lives in
`tools/param_reach_baseline.txt` and only additions fail. Accept a new one deliberately with
`python tools/param_reach.py --update-baseline`.

Its first run found, besides `targetClass`: `self_audit.summaryOnly` (the compact mode built because
the full response was too large to read) and `trace_ground.location` (added after top-level `x`/`y`
silently ignored `location:{}` and ran a whole terrain investigation at the world origin) - both
added days earlier, both never wired into the tool layer. Also `add_cast.pure`, `get_node.graphId`
(the only way to disambiguate two loaded copies of a Blueprint sharing NodeGuids) and every
`start_pie` multiplayer option, which matters because **a standalone PIE always has authority** and
will make authority-gated replication code look like it works.

#### The silent-ignore backstop did not cover arrays

`MifBridgeHandlers.h` documents a backstop: `JNum`/`JInt`/`JBool` record a violation when a field is
**present but the wrong JSON type**, and `RunEndpoint` turns any recording into a failed response. It
describes itself as covering "EVERYTHING ELSE in one place". Arrays were never in it.

`TryGetArrayField` returns false for *absent* and for *present but not an array* alike, so a handler
takes its nothing-was-asked-for path either way:

    select_level_actors  {"actorPaths": "/Game/Foo"}   ->   {"ok":true,"selected":0,"selection":[]}

A call that did nothing at all, reported as success, never mentioning the parameter. Found by the
endpoint fuzzer. Note the first triage of that finding was **wrong**: the handler does already report
`notFound` for paths that fail to resolve — the array simply never reached it.

Use **`JArray`** (`MifBridgeHandlers.h`) for every request-array read. Absent is quiet; present-but-wrong
is recorded and fails the request, exactly like the scalar readers. All 19 sites go through it,
including `ParsePinSpecs` and `UiReadStringArray`, which every pin-spec and string-array parameter in
the module flows through.

> The general lesson is worth more than the fix: a backstop that says it covers "everything" is worth
> testing against a category nobody had in mind when it was written.

#### Never hardcode a macro library path — use `ResolveMacroGraph`

`recipe_reset_and_loop` loaded `StandardMacros` by literal path to find `ForEachLoop`. Harmless today,
and the same shape that already rotted once: macro libraries are discovered from the asset registry
because there are more of them than anyone remembers, including ones the *project* defines.

`ResolveMacroGraph(GraphName, PreferredLibraryPath, OutLibrary)` tries the preferred library, then
every macro library the registry knows, matching exactly first and then ignoring case and spacing.

**`add_macro_instance` deliberately does NOT use it**, even though that would remove a near-duplicate.
The two want opposite semantics: the recipe wants "find `ForEachLoop` wherever it lives" because there
is one right answer and it is internal; `add_macro_instance` must **refuse** when the macro is not in
the library the caller named and hand back the correct `macroPath`. Silently instantiating from a
different library is exactly the confusion the Switch Has Authority report was about, so sharing the
resolver there would trade a good error for a silent surprise.

#### Auditing for unverified writes: what the tool gets wrong

`tools/audit_postconditions.py` looks for the recurring defect — a handler that calls a UE API which
cannot fail loudly, then reports `ok` because nothing threw. Two things about reading its output:

* **It over-reports by construction.** Its first version flagged ~90 MEDIUM entries, almost all
  noise, because its mutation list contained `->Set` — which matches `Out->SetStringField`. Every
  endpoint builds its response that way, so every read-only lister looked like an unverified write.
  It now ignores response writes and skips the module's own `readOnly` bucket.
* **Creation is self-verifying; setters are not.** What survives is dominated by endpoints that make
  something and return it — `NewObject` failing would have thrown. The entries that can genuinely
  half-succeed are the **void setters**: `SetActorLabel`, `TrySetDefaultValue`, `SetMacroGraph`,
  `OnRenameNode`, `SetPurity`. That is where to look.

Fixed from its output: `rename_event` (OnRenameNode is void and declines a name that collides with
another event, so a refused rename read as a successful one — renaming is that endpoint's whole job,
so it now fails), `add_macro_instance` (a node whose `SetMacroGraph` did not take exists and does
nothing), and `duplicate_actors` (labels through `SetActorLabelChecked`).

### Endpoints that discard unsaved work without asking

| endpoint | what it does | undo? | confirm-gated? |
|---|---|---|---|
| `new_level` | discards unsaved edits in the current map | **no** | no |
| `load_level` | same | **no** | no |

`new_level` forces `bPromptUserToSave=false` (`MifBridgeWorld.cpp:135`, reasoning at `:120`). This is
**deliberate and correct**: handlers run synchronously inside the HTTP server's ticker, so a
"save your changes?" modal would freeze the editor *and* the bridge with it, and an unattended agent
could never dismiss it. The cost is that the safety net is gone — there is nothing to undo, because
the `UWorld` is torn down. Call `list_dirty_packages` first and decide deliberately.

### Other things with no safety net

- **`run_console`** executes arbitrary `UEngine::Exec` with a **deliberate no-deny-list policy**
  (`MifBridgeIntrospect.cpp:1371` — "a name-based list would be theatre"). It also sits in the
  **readOnly** bucket, so it does not even get the blanket transaction. Whatever the command does is
  outside every guarantee on this page.
- **`delete_asset`** is confirm-gated and `/Game/`-restricted (`MifBridgeAssetOps.cpp:78-80`) but
  takes **no backup** — there are zero `BackupPackage` calls in that file. `backup_blueprint` exists;
  call it yourself first.
- **There is no version control over the content tree.** The only git repo in play covers the
  plugin's own source. `Game/Content` is ~8.7 GB and unversioned, so a bad write there is recoverable
  only from whatever backup the endpoint happened to take.

### The rule this all reduces to

Read the error text. It is written to be read — several handlers name exactly what they left behind.
An agent that branches on `ok` alone and never reads `error`, `outcome` or `nothingModified` will
eventually corrupt something quietly, which is the failure mode this whole document exists to prevent.
