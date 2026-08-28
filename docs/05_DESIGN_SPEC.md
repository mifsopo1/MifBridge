# 13 — UE5 MCP Bridge Plugin (design spec)

**Status:** **Phase 0 + Phase 1 + Phase 2 + Phase 3 built & compiled** (2026-07-11). In-editor C++ plugin lives at `D:/DDS2SDK/Game/Plugins/MifBridge/` and compiles clean against the 5.3.2 source engine (`UnrealEditor-MifBridge.dll`, editor module, PostEngineInit; built via `Build.bat DrugDealerSimulator2Editor Win64 Development`). Python MCP server ships at `tools/mcp-server/` — **79 tools ↔ 79 endpoints, 1:1 parity**. Implemented: all §9 Phase-0/1 endpoints; the §10 recipes; `read_modloader_log`/`trigger_cook` (plan-only); the Phase-3 node gaps (`add_custom_event` incl. params, `add_make_struct`, `add_break_struct`, `add_self`, `add_literal`, `create_function`, `resolve_struct`); **Phase-3 breadth** — timelines (`add_timeline`), switches/cast/enum (`add_class_cast`, `add_switch_enum/int/string`, `add_enum_literal`, `set_pin_type`), event dispatchers (`add_event_dispatcher`, `add_call_dispatcher`, `add_bind_dispatcher`, `list_dispatchers`), components/SCS (`add_component`, `list_components`, `remove_component`, `set_component_transform`), interfaces (`add_interface`, `remove_interface`, `list_interfaces`), DataTables (`list_datatables`, `read_datatable`, `get_datatable_row`, **`write_datatable_rows`**); and **Phase-3 completion** — `implement_interface_function`, `remove_function`, and common nodes (`add_sequence`, `add_spawn_actor`, `add_get_subsystem`, `add_make_array`, `add_format_text`, `add_get_data_table_row`, `add_comment`). All with game-thread dispatch, per-action transactions, structured compiler read-back, and **three rounds of adversarial multi-agent review (all findings fixed — see §20)**. **Live-verified 2026-07-12:** the two make-or-break §15 tests PASS against the live editor — **#2 wildcard regression** (ForEach `Array` wildcard resolves to `StaticMeshComponent[]` on connect → compile 0/0) and **#6 structured compile-error read-back** (errors mapped to node guid + pin). Driven via curl on `127.0.0.1:8791` (token `dev`); details in §15 "Live results." Remaining §15 items (#1/#3/#4/#5/#7/#8) are lower-risk, to exercise during real graph work. **Nothing left to build:** the §4 pitfall catalog, §9 endpoint surface, §10 recipes, and §16 phasing are all covered. See **§20 — Build & Run (as-built)**. Original design notes follow.

> One-line thesis: **the failures we keep hitting are almost all artifacts of the clipboard-paste path, not of Blueprint graph editing itself.** Programmatic graph edits via the engine API (`TryCreateConnection`, `ReconstructNode`, real transactions) fire the pin-notification callbacks that paste skips — which is exactly what resolves wildcards, re-links variables, and expands macros. A bridge doesn't just save typing; it removes a whole class of bugs.

---

## Table of contents
1. Why (the pain catalog)
2. What we are NOT trying to build
3. Architecture (3 tiers)
4. The complete pitfall catalog → and how the bridge kills each one
5. C++ plugin — module + build setup
6. C++ plugin — HTTP server + thread dispatch + transactions
7. C++ plugin — the graph-edit core (deep dive per node type)
8. C++ plugin — compile + **structured error read-back** (the whole point)
9. Endpoint API surface (full catalog)
10. Composite "recipe" endpoints (high-value macros)
11. MCP server layer (Python)
12. Claude Code wiring
13. Safety, validation, undo, dry-run, backups
14. Integration with the existing pipeline (cook / retoc / deploy / decompile / datatables)
15. Testing plan
16. Build phasing / roadmap
17. Appendix A — node-class cheat sheet
18. Appendix B — pin-type cheat sheet (the stuff that bit us)
19. Appendix C — known-good T3D fragments (fallback path)
20. Build & run (as-built)

---

## 1. Why (the pain catalog)

Every hour lost on the SteelRack graph traces to one of these. This plugin exists to delete them:

- **No feedback loop.** Current cycle is *author blind → user pastes → user screenshots → Claude guesses from a JPEG → repeat.* Claude never sees the compiler output directly. A bridge returns the compile log as text.
- **Clipboard paste doesn't resolve wildcards.** `ForEach Array` and `GetArrayItem Array` stayed `undetermined` even though connected+typed, because `ImportNodesFromText` doesn't fire `NotifyPinConnectionListChanged`. Cost ~1 hour and multiple cook cycles.
- **Macro instances are the worst offenders on paste** — the ForEachLoop needed a full palette replacement; refresh/reconnect didn't fix it.
- **Silent name mismatches.** A variable created as `"BestPotIndex "` (trailing space) vs nodes referencing `"BestPotIndex"` → "variable not found," invisible in the panel.
- **Hand-authored GUIDs.** Node/pin GUID typos, non-reciprocal `LinkedTo`, wrong pin categories — all only surface as a broken paste or a red node.
- **Cook cycle latency.** Editor must close, cook runs ~minutes, retoc, parity check, game must close, deploy. Every trivial fix is a full cook. A bridge lets us compile-in-editor and validate logic *before* committing to a cook.

The user explicitly asked: cover everything we hit **and** anything we might. §4 is the exhaustive list.

---

## 2. What we are NOT trying to build

- Not a replacement for the editor UI — the user still runs the editor; the plugin lives inside it.
- Not a runtime/shipping component — **editor-only module**, never cooked into a mod pak.
- Not a general "AI writes the whole game" tool — it's a precise, auditable graph-surgery API with undo.
- Not a way to skip understanding the game — decompile-first (retoc + KismetKompiler, see `docs/11`) still drives *what* to build; the bridge only changes *how* we apply it.

---

## 3. Architecture (3 tiers)

```
[ Claude (this agent) ]
        │  MCP tool calls (stdio JSON-RPC)
        ▼
[ MCP server: python, FastMCP ]        ← ships in repo: tools/mcp-server/server.py
        │  HTTP POST JSON on 127.0.0.1:8791 (localhost only)
        ▼
[ In-editor C++ plugin: "MifBridge" ]  ← D:/DDS2SDK/Game/Plugins/MifBridge/
        │  dispatched to Game Thread
        ▼
[ UnrealEd graph API: UBlueprint / UEdGraph / UK2Node / UEdGraphSchema_K2 /
  FKismetEditorUtilities / FBlueprintEditorUtils ]
```

Design rules:
- **Bind to `127.0.0.1` only.** No auth needed if loopback-only, but add a shared-secret header anyway (`X-Mif-Token`) so a stray browser tab can't poke it.
- **One request = one transaction = one compile-and-report** (configurable; batch mode below).
- **Everything returns JSON**, including the full compiler message list. The MCP layer forwards it verbatim to Claude.
- **Idempotent where possible.** Node/var creation returns a stable handle (the node's `NodeGuid`) so follow-up calls address it deterministically.

---

## 4. The complete pitfall catalog → and how the bridge kills each one

| # | Pitfall (hit ✔ / anticipated ○) | Root cause | Bridge fix |
|---|---|---|---|
| 1 | ✔ Wildcard pin stuck `undetermined` after paste | `ImportNodesFromText` skips pin-connection notifications | `connect_pins` calls `Schema->TryCreateConnection` → fires `NotifyPinConnectionListChanged` → wildcard resolves |
| 2 | ✔ ForEachLoop macro instance won't expand after paste | macro instance keeps cached wildcard/expansion state | `add_macro_instance` spawns fresh `UK2Node_MacroInstance` + `AllocateDefaultPins` + `ReconstructNode`; never pastes it |
| 3 | ✔ Variable `"BestPotIndex "` trailing space | freehand naming; panel hides trailing WS | `add_variable` trims + validates name (`^[A-Za-z_][A-Za-z0-9_]*$`), returns canonical name; `list_variables` shows raw bytes |
| 4 | ✔ Nodes reference a variable created *after* them → cached "not found" | order-of-operations | bridge creates the variable first (or `refresh_node` re-resolves by name+GUID) |
| 5 | ✔ Refresh ≠ reconnect | `ReconstructNode` re-reads cached wildcard | bridge distinguishes `refresh_node` (reconstruct) vs `reconnect_pin` (break+`TryCreateConnection`) |
| 6 | ✔ `real`/`double` vs `float` pin category (UE5) | UE5 unified float→double | `set_pin_type` / node factory uses the schema's canonical type; helpers named `pin_double`, `pin_float`, `pin_int` |
| 7 | ✔ pure vs impure (`bIsPureFunc`) affects exec pins | function metadata | node factory reads `UFunction` flags; never guesses |
| 8 | ✔ Struct pins (Guid) need exact `PinSubCategoryObject` | struct path | bridge resolves struct by `TBaseStructure<FGuid>::Get()`; caller passes `"Guid"`, bridge maps |
| 9 | ✔ Container type (Array) on pins | forgot `ContainerType=Array` | derived from the `UFunction`/`FProperty`, not hand-set |
| 10 | ✔ Object pin class ref format | `/Script/CoreUObject.Class'/Script/Engine.StaticMeshComponent'` | bridge builds from `UClass*` |
| 11 | ✔ self/target pin hidden vs visible | library-static vs member | schema decides; caller just says "target = <node/var/self>" |
| 12 | ✔ Override interface event + Parent call | `bOverrideFunction`, `UK2Node_CallParentFunction` | `add_override_event(interface, name)` + `add_parent_call` endpoints |
| 13 | ✔ Exec-chain splicing ("loop on the loop") | manual 3-wire moves | `splice_into_exec(afterNode, insertNode)` does break+relink atomically |
| 14 | ✔ Knots (reroute nodes) in graphs | pasted reroutes | bridge treats knots transparently: `connect_pins` auto-routes through/around; `list_nodes` can hide knots |
| 15 | ✔ member vs local variables | event graph can't hold locals | `add_variable(scope=member|local, function=<name>)` validates scope |
| 16 | ✔ blind screenshot loop | no read-back | every mutation returns node/pin state; `compile` returns messages |
| 17 | ✔ cook cycle latency for trivial checks | pak pipeline | `compile` validates logic in-editor pre-cook; cook stays a separate deliberate step |
| 18 | ✔ deploy path C:\SteamLibrary not D:\Steam | two Steam libs | pipeline scripts already pinned; bridge's optional `trigger_cook` hard-codes the verified paths |
| 19 | ✔ hand-authored GUID typos / non-reciprocal LinkedTo | manual T3D | bridge never hand-writes GUIDs; engine assigns them |
| 20 | ✔ `GetArrayItem` index pin literally named `"Dimension 1"` | node quirk | `get_array_item` endpoint hides the name; caller passes `array`, `index` |
| 21 | ✔ `AddDeployedEquipment` same-transform → empty GUID | game dedup (see `docs/11`, memory) | not a graph issue, but bridge's `call_function` can assert non-empty return + report |
| 22 | ✔ cook parity (chunk count) | zen/iostore | pipeline check retained; bridge surfaces the count |
| 23 | ○ Enum / byte pins | `UEnum` default | node factory sets `PinSubCategoryObject=UEnum*`, default via `GetNameByValue` |
| 24 | ○ Latent nodes (Delay, async) | latent metadata | supported via `UK2Node_CallFunction` with latent UFUNCTION; exec fan handled |
| 25 | ○ Timeline nodes | `UK2Node_Timeline` needs a `UTimelineTemplate` | dedicated `add_timeline` endpoint (phase 3) |
| 26 | ○ Dynamic cast nodes | `UK2Node_DynamicCast` | `add_cast(targetClass)` endpoint; wires exec success/fail |
| 27 | ○ Custom struct pins (Brando's structs) | struct discovery | `resolve_struct(name)` searches loaded `UScriptStruct`s |
| 28 | ○ Interface *function* impl (not just events) | `ImplementInterface` | `add_interface`, `implement_interface_function` |
| 29 | ○ Function creation w/ inputs/outputs + locals | `FBlueprintEditorUtils::AddFunctionGraph` | `create_function` endpoint |
| 30 | ○ Delegate / event-dispatcher pins | multicast delegates | `bind_event`, `add_custom_event` (phase 3) |
| 31 | ○ Component add/remove on the BP | SCS (SimpleConstructionScript) | `add_component`, `set_component_transform` (phase 3) — SCS is a different tree than the graph |
| 32 | ○ Soft object / class refs | `TSoftObjectPtr` | pin factory handles soft pins |
| 33 | ○ Renamed asset redirectors | `UObjectRedirector` | bridge resolves through redirectors on `open_blueprint` |
| 34 | ○ Plugin itself needs recompiling on engine change | Live Coding vs full build | documented build steps; `MifBridge` is editor-only, hot-reload friendly |
| 35 | ○ Crash on non-game-thread edit | UE threading | **all** edits dispatched to game thread (see §6) |
| 36 | ○ Undo/redo corruption | missing transactions | every mutation wrapped in `FScopedTransaction` + `Modify()` |
| 37 | ○ Dirty-but-not-saved assets lost on crash | no autosave | `save_blueprint` endpoint + optional auto-`.bak` before each mutation batch |

---

## 5. C++ plugin — module + build setup

Location: `D:/DDS2SDK/Game/Plugins/MifBridge/`

`MifBridge.uplugin`:
```json
{
  "FileVersion": 3,
  "FriendlyName": "Mif Bridge",
  "Version": 1, "VersionName": "0.1",
  "Category": "Editor",
  "Description": "Localhost HTTP bridge for programmatic Blueprint graph edits (Claude/MCP).",
  "Modules": [
    { "Name": "MifBridge", "Type": "Editor", "LoadingPhase": "PostEngineInit" }
  ]
}
```

`Source/MifBridge/MifBridge.Build.cs` — the dependency set is where people get stuck. Minimum:
```csharp
PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine" });
PrivateDependencyModuleNames.AddRange(new[] {
    "UnrealEd",          // FKismetEditorUtilities, editor subsystems
    "BlueprintGraph",    // UK2Node_* classes
    "GraphEditor",       // graph helpers
    "Kismet",            // FBlueprintEditorUtils lives in KismetCompiler/UnrealEd; keep for safety
    "KismetCompiler",    // compile results struct
    "HTTPServer",        // FHttpServerModule / IHttpRouter
    "Json", "JsonUtilities",
    "AssetRegistry",     // find/open blueprints by path
    "EditorSubsystem",
    "ToolMenus"          // optional: a menu button to start/stop the server
});
```
Notes:
- **Editor module only** (`"Type": "Editor"`). It must never be a runtime dep of any mod, or the pak breaks.
- Because the engine is source-built 5.3.2, the plugin compiles with the same `Build.bat`/Live Coding flow already used for ElectronicNodes (see memory: ElectronicNodes compiled via `Build.bat DrugDealerSimulator2Editor Win64 Development -Project=...`). Marketplace prebuilts won't ABI-match; this is source, so fine.

---

## 6. C++ plugin — HTTP server + thread dispatch + transactions

### 6.1 Server startup
In `FMifBridgeModule::StartupModule()`:
```cpp
FHttpServerModule& Http = FHttpServerModule::Get();
TSharedPtr<IHttpRouter> Router = Http.GetHttpRouter(8791);
RegisterRoutes(Router);          // §9
Http.StartAllListeners();
```
Stop everything in `ShutdownModule()`. Guard with a CVar/menu toggle so it isn't listening unless the user wants it.

### 6.2 The single most important rule: game-thread dispatch

> **SUPERSEDED — DO NOT COPY THE PATTERN BELOW.** The `AsyncTask(ENamedThreads::GameThread, …)` hop was built, shipped, and removed: it enqueues onto the game thread's *named-thread* queue, which is also pumped from inside `FTickTaskSequencer::ReleaseTickGroup() -> WaitUntilTasksComplete()`, so a compile-heavy endpoint reinstanced actors **mid-tick-group** and the next `FTickFunction` hit `check(!"Pure virtual not implemented")` (`EngineBaseTypes.h:409`) with no MifBridge frame on the stack. As built, `FHttpServerModule` is an `FTSTickerObjectBase`, so the handler is **already** on the game thread — post-world-tick, outside every tick group — and runs **inline** with no hop at all. The source comment says *"Do NOT reach for AsyncTask"* (`MifBridgeServer.cpp:229-265`). The conclusion of this section (all `UObject` work on the game thread; `FEditorScriptExecutionGuard` inside) is still right; only the *mechanism* below is wrong. Current model: `docs/00_ARCHITECTURE.md` § *Threading* and `docs/02_GOTCHAS.md` §8.

HTTP callbacks fire on an HTTP worker thread. **All** `UBlueprint`/`UEdGraph`/`UK2Node` access must run on the game thread or the editor crashes. Pattern:
```cpp
auto Handler = [](const FHttpServerRequest& Req, const FHttpResultCallback& OnDone)
{
    TSharedRef<FJsonObject> In = ParseBody(Req);
    // Hop to game thread, do the work, hop back to reply.
    AsyncTask(ENamedThreads::GameThread, [In, OnDone]()
    {
        TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
        DoEditWork(In, Out);                     // all UObject work here
        OnDone(MakeJsonResponse(Out));           // reply from game thread is fine
    });
    return true;                                  // we answer async
};
```
Use `FEditorScriptExecutionGuard` inside `DoEditWork` so editor-only script paths are allowed.

### 6.3 Transactions + dirtying (undo-safe)
Every mutation:
```cpp
FScopedTransaction Tx(NSLOCTEXT("MifBridge","Edit","Eddie graph edit"));
Blueprint->Modify();
Graph->Modify();
// ...mutate...
FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
```
- `MarkBlueprintAsStructurallyModified` when pins/nodes/vars change (forces a fuller recompile).
- `MarkBlueprintAsModified` for value-only tweaks.
- Wrapping in `FScopedTransaction` means the user can **Ctrl-Z** any bridge action — critical trust feature.

### 6.4 Batch mode
Accept an array of ops in one request (`/api/batch`) executed in one transaction + one compile at the end. Prevents N recompiles when building a 20-node cluster (the SteelRack case). Return per-op results + one final compile report.

---

## 7. C++ plugin — the graph-edit core (deep dive per node type)

A shared helper spawns any node safely:
```cpp
template<typename TNode>
TNode* SpawnNode(UEdGraph* Graph, int32 X, int32 Y) {
    TNode* N = NewObject<TNode>(Graph);
    Graph->AddNode(N, /*bFromUI*/false, /*bSelectNewNode*/false);
    N->CreateNewGuid();
    N->PostPlacedNewNode();
    N->NodePosX = X; N->NodePosY = Y;
    N->AllocateDefaultPins();     // MUST come after member setup for CallFunction; see below
    return N;
}
```
Order matters per node type — details:

### 7.1 Function call — `UK2Node_CallFunction`
```cpp
auto* Node = NewObject<UK2Node_CallFunction>(Graph);
UFunction* Fn = TargetClass->FindFunctionByName(FnName);   // resolve first!
Node->SetFromFunction(Fn);        // sets FunctionReference + purity from UFUNCTION flags
Graph->AddNode(Node,false,false);
Node->CreateNewGuid(); Node->PostPlacedNewNode(); Node->AllocateDefaultPins();
```
- `SetFromFunction` derives `bIsPureFunc`, self/target visibility, param pins, container types, and struct/enum subcategories **from the reflected `UFunction`** — this is why the bridge never gets pin categories wrong (pitfalls 6–11). Compare: hand-T3D forces us to know all of that.
- For a **library static** (KismetMathLibrary::Dot_VectorVector, etc.) `SetFromFunction` hides `self`. For a **member/target** call (`K2_GetComponentLocation` on a component) it exposes the target pin.

### 7.2 Variable get/set — `UK2Node_VariableGet` / `UK2Node_VariableSet`
```cpp
Node->VariableReference.SetSelfMember(VarName);   // member var on this BP
// or ...SetExternalMember(VarName, OwnerClass) for another class
Node->AllocateDefaultPins();
```
- Resolves the pin type from the `FProperty` — so a `TArray<FGuid>` var yields a `Guid`-array pin automatically (pitfall 8/9). No trailing-space traps because we pass the *canonical* name the bridge returned from `add_variable`.
- `SetSelfMember` uses name; the pin also carries the var GUID after `ReconstructNode`, so rename-safety works.

### 7.3 Branch — `UK2Node_IfThenElse`
Trivial: spawn, `AllocateDefaultPins`. Pins: `execute`, `Condition`(bool), `then`, `else`.

### 7.4 Macro instance (ForEachLoop, etc.) — `UK2Node_MacroInstance`  ← the one that bit us
```cpp
UEdGraph* MacroGraph = LoadMacroGraph(TEXT("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros"), TEXT("ForEachLoop"));
Node->SetMacroGraph(MacroGraph);
Graph->AddNode(Node,false,false);
Node->CreateNewGuid(); Node->PostPlacedNewNode(); Node->AllocateDefaultPins();
```
- Spawning fresh (not pasting) + `AllocateDefaultPins` + later `TryCreateConnection` on the `Array` input = wildcard resolves correctly. **This is the fix for the exact bug that ate an hour.**
- The macro-graph path is resolved at runtime from the loaded engine (no guessing `/Engine/EditorBlueprintResources/...`); if not found, endpoint returns an explicit error instead of a silently-broken node.

### 7.5 Get array item — `UK2Node_GetArrayItem`
Spawn + `AllocateDefaultPins`. Pins: `Array`(wildcard→resolves on connect), `Dimension 1`(int index), `Output`(by-ref element). Endpoint `get_array_item` names them `array`/`index`/`out` so callers never touch `"Dimension 1"`.

### 7.6 Override event — `UK2Node_Event`
```cpp
Node->EventReference.SetExternalMember(EventName, InterfaceOrParentClass);
Node->bOverrideFunction = true;
Node->AllocateDefaultPins();
```
Covers interface events like `MainInteraction` from `BP_Interface_Interaction`. Endpoint validates the event exists on the given interface/parent and isn't already present in the graph.

### 7.7 Parent call — `UK2Node_CallParentFunction`
```cpp
Node->SetFromFunction(ParentClass->FindFunctionByName(Name));
```
For `Parent: MainInteraction` etc. The bridge can auto-add this when creating an override if `callParent=true`.

### 7.8 Knots — `UK2Node_Knot`
The bridge generally *doesn't* create knots (they're cosmetic reroutes). But `connect_pins` must **tunnel through** existing knots: when asked to connect A→B and B is really fed via a knot chain, resolve to the terminal pins. `list_nodes` gets a `hideKnots` flag so Claude sees logical topology, not reroute noise.

### 7.9 Casts, enums, math, make/break struct
- `UK2Node_DynamicCast` — set `TargetType`, `AllocateDefaultPins`; exposes `Object`, `As<T>`, exec `then`/`Cast Failed`, `bSuccess`.
- `UK2Node_MakeStruct` / `UK2Node_BreakStruct` — set `StructType`.
- Enum literals via `UK2Node_CallFunction` on comparison ops or a literal node.

### 7.10 Pin connection — the crux
```cpp
const UEdGraphSchema_K2* K2 = GetDefault<UEdGraphSchema_K2>();
UEdGraphPin* Out = FindPinChecked(SourceNode, OutName, EGPD_Output);
UEdGraphPin* In  = FindPinChecked(DestNode,   InName,  EGPD_Input);
const FPinConnectionResponse R = K2->CanCreateConnection(Out, In);
if (R.Response == CONNECT_RESPONSE_DISALLOW) { return Error(R.Message); }  // report *why*
K2->TryCreateConnection(Out, In);   // fires NotifyPinConnectionListChanged → wildcard resolve
```
`CanCreateConnection` gives a human-readable reason on failure (type mismatch, directionality, container mismatch) — the bridge returns that string to Claude. That alone would have short-circuited most of today's guessing.

### 7.11 Pin default values
`K2->TrySetDefaultValue(*Pin, ValueString)` for literals (the `-2.0` init, index `0`, etc.), with type-correct formatting handled by the schema.

---

## 8. C++ plugin — compile + structured error read-back (the whole point)

```cpp
FCompilerResultsLog Results;
FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::None, &Results);
```
Then serialize **every** message:
```json
{ "ok": false,
  "numErrors": 2, "numWarnings": 1,
  "messages": [
    {"severity":"error","text":"The type of Array ... is undetermined ...","nodeGuid":"FF17...","pinName":"Array"},
    ...
  ] }
```
- `FCompilerResultsLog` carries node/pin back-references → the bridge maps each message to the offending `NodeGuid`/pin, so Claude can fix the *exact* node without a screenshot.
- This is the single feature that converts the workflow from "guess from a JPEG" to "read the error, patch the node, recompile."

---

## 9. Endpoint API surface (full catalog)

All POST, JSON in/out, localhost, `X-Mif-Token` header. Grouped:

**Session / assets**
- `open_blueprint {path}` → `{blueprintId, class, parentClass, graphs:[...]}`; resolves redirectors.
- `list_blueprints {filter}` (asset registry search)
- `save_blueprint {blueprintId}` / `save_all`
- `backup_blueprint {blueprintId}` → writes a `.bak` copy of the `.uasset`

**Introspection (read-back)**
- `list_graphs {blueprintId}`
- `list_nodes {graphId, hideKnots?}` → node guids, titles, positions, pins (name/dir/type/linkedTo)
- `get_node {nodeGuid}` → full pin detail
- `list_variables {blueprintId}` → name (raw bytes flagged), type, scope, default
- `list_functions {blueprintId}`
- `find_nodes {graphId, byClass|byTitle|byFunction}` (locate the Print/Parent/etc.)

**Variables / functions**
- `add_variable {blueprintId, name, type, container?, scope=member|local, function?, default?}` → canonical name (trimmed/validated)
- `rename_variable`, `remove_variable`, `set_variable_default`
- `create_function {blueprintId, name, inputs[], outputs[], pure?}`
- `create_event_graph_event {name}` (custom event)

**Nodes**
- `add_function_call {graphId, class, function, x, y}`
- `add_variable_get {graphId, var, x, y}` / `add_variable_set {...}`
- `add_branch {graphId, x, y}`
- `add_macro_instance {graphId, macroPath, macroGraph, x, y}` (ForEachLoop, etc.)
- `add_get_array_item {graphId, x, y}`
- `add_override_event {blueprintId, interfaceOrParent, event, callParent?, x, y}`
- `add_parent_call {graphId, parentClass, function, x, y}`
- `add_cast {graphId, targetClass, x, y}`
- `add_make_struct` / `add_break_struct {structName}`
- `add_self` / `add_literal {type, value}`
- `move_node`, `remove_node`, `refresh_node {nodeGuid}` (ReconstructNode)

**Pins / wiring**
- `connect_pins {srcNode, srcPin, dstNode, dstPin}` → returns `CanCreateConnection` reason on failure
- `disconnect_pin {node, pin}` (break all links — the "Alt+click" we needed)
- `reconnect_pin {...}` (disconnect + connect, the wildcard-reset combo)
- `set_pin_default {node, pin, value}`
- `splice_into_exec {afterNode, afterPin, insertNode, insertExecIn, insertExecOut}` (atomic mid-chain insert)

**Compile / diagnostics**
- `compile {blueprintId}` → structured messages (§8)
- `validate {blueprintId}` (compile without saving; dry-run)

**Batch**
- `batch {ops:[...], compileAtEnd?}` → per-op results + final compile report

**Pipeline hooks (optional, phase 2)**
- `trigger_cook {mod}` → runs the verified cook/retoc/deploy chain (paths pinned; see `docs/04`)
- `read_modloader_log {lines}` → tails `Saved/Logs/DrugDealerSimulator2.log` for our `PrintToModLoader` output — closes the *runtime* loop too

**DataTables (phase 3, overlaps the python commandlet path in `docs/12`)**
- `read_datatable {path, columns}` / `write_datatable_rows {path, rowsJson}`

---

## 10. Composite "recipe" endpoints (high-value macros)

These bundle the multi-step patterns we hand-did. Each is one transaction + one compile:

- `recipe_add_debug_print {graphId, afterNode, message}` — the DEBUG-gated `PrintToModLoader` we bake into everything (see memory: always add debug).
- `recipe_reset_and_loop {graphId, arrayVar, bodyBuilder}` — spawns the init-SETs + ForEach + wires the array, resolving the wildcard the correct way (the pattern that failed via paste).
- `recipe_override_and_call_parent {blueprintId, interface, event}` — override event + Parent call pre-wired (MainInteraction shape).
- `recipe_argmax_over_components {graphId, componentsVar, scoreBuilder, outIndexVar}` — the dot-product aimed-pot selector, generalized.
- `recipe_splice_before_parent {graphId, clusterEntry, clusterExit}` — insert a cluster between the event and its Parent call (exactly the SteelRack splice).

Recipes are where the bridge stops being "an API" and becomes "Claude builds working subgraphs in one shot."

---

## 11. MCP server layer (Python)

`tools/mcp-server/server.py` — thin translator, no game logic:
```python
import os, requests
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("MIF_BRIDGE_URL", "http://127.0.0.1:8791/api")
TOKEN = os.environ.get("MIF_BRIDGE_TOKEN", "dev")
mcp = FastMCP("mif-ue5-bridge")

def _post(ep, **payload):
    r = requests.post(f"{BASE}/{ep}", json=payload,
                      headers={"X-Mif-Token": TOKEN}, timeout=30)
    return r.json()

@mcp.tool()
def compile_blueprint(blueprint_id: str) -> dict:
    "Compile a Blueprint and return structured error/warning messages."
    return _post("compile", blueprintId=blueprint_id)

@mcp.tool()
def connect_pins(src_node: str, src_pin: str, dst_node: str, dst_pin: str) -> dict:
    "Wire two pins; returns the schema's reason string if disallowed."
    return _post("connect_pins", srcNode=src_node, srcPin=src_pin,
                 dstNode=dst_node, dstPin=dst_pin)
# ...one thin wrapper per endpoint in §9...

if __name__ == "__main__":
    mcp.run()
```
- Keep the MCP layer dumb: 1 tool ↔ 1 endpoint, plus a couple of convenience tools that call `batch`.
- Timeouts + clear error surfacing so a dead editor reports "bridge unreachable," not a hang.

---

## 12. Claude Code wiring

Project-scoped `.mcp.json` (or user `~/.claude` config):
```json
{
  "mcpServers": {
    "mif-ue5": {
      "command": "python",
      "args": ["<YourProject>/Plugins/MifBridge/tools/mcp-server/server.py"],
      "env": { "MIF_BRIDGE_URL": "http://127.0.0.1:8791/api", "MIF_BRIDGE_TOKEN": "<secret>" }
    }
  }
}
```
Editor must be open with MifBridge listening. A ToolMenus button ("Mif Bridge: Start/Stop") gives the user a visible on/off + port readout.

---

## 13. Safety, validation, undo, dry-run, backups

- **Undo:** every mutation in `FScopedTransaction` → Ctrl-Z works. Non-negotiable trust feature.
- **Dry-run:** `validate` compiles a duplicate (or compiles without save) so Claude can check before committing.
- **Backups:** `batch` can auto-`.bak` the `.uasset` first; `backup_blueprint` on demand.
- **Confirm-destructive:** `remove_node`/`remove_variable`/`rename_variable` require an explicit `confirm=true` (mirrors the "look before you overwrite" global rule).
- **Read-back everything:** no mutation returns bare `ok:true` — it returns the resulting node/pin/var state so Claude verifies, not assumes.
- **Localhost + token:** loopback bind + shared secret; never expose the port.
- **Editor-only:** module type Editor; CI/headless cook path stays separate (bridge not required to cook).

---

## 14. Integration with the existing pipeline

The bridge complements, doesn't replace, the current flow (`docs/04`, `docs/11`, `docs/12`):
- **Decompile-first stays.** retoc + patched KismetKompiler still tells us the game's real logic (`MainInteraction(Pawn, Component)`, channel-6 traces, etc.). The bridge applies edits; it doesn't decide them.
- **Cook stays a deliberate step.** The bridge's value is *iterating logic to a clean compile in-editor* so each cook is worth it. `trigger_cook` (phase 2) can fire the verified `UnrealEditor-Cmd -run=Cook … → retoc to-zen UE5_3 → parity check → deploy to C:\SteamLibrary\…\LogicMods\` chain, but only on request.
- **Runtime loop closes too:** `read_modloader_log` tails our `PrintToModLoader` DEBUG output, so after a cook Claude can read what actually happened in-game instead of asking for a screenshot of the log.
- **DataTables:** phase-3 endpoints can supersede the headless python commandlet for row edits.

---

## 15. Testing plan

1. **Smoke:** `open_blueprint` a throwaway BP, `add_function_call PrintString`, `connect_pins` from BeginPlay, `compile` → expect `ok:true`. Ctrl-Z → node gone (proves transactions).
2. **Wildcard regression (the SteelRack bug):** `add_macro_instance ForEachLoop`, `add_variable_get Pots(array)`, `connect_pins Pots→Array`, `compile` → expect **no** "undetermined" error. This test *is* the reason the plugin exists; it must pass.
3. **GetArrayItem regression:** array var + `get_array_item` + index → compile clean, correct element type.
4. **Override + parent:** `add_override_event` an interface event + `add_parent_call`, compile clean.
5. **Splice:** build A→C, `splice_into_exec` B → assert A→B→C.
6. **Error read-back:** deliberately mis-wire a type → assert `compile` returns the schema's reason string mapped to the right node.
7. **Batch atomicity:** 20-op cluster in one `batch` → one compile, one undo step.
8. **Thread-safety soak:** fire 100 rapid requests → no crash (all marshalled to game thread).

### Live results — 2026-07-12 (editor open, driven via curl on `127.0.0.1:8791`, token `dev`)
The two make-or-break tests **PASS** against the live `BE_LABEQ_SteelRack` (run inside a temp `MifBridgeTest` function, backed up first, removed after → rack compiles 0/0, unchanged):
- **#2 Wildcard regression — PASS.** `add_variable_get Plants` (StaticMeshComponent[]) + `add_macro_instance ForEachLoop` (Array pin spawned as `wildcard`) → `connect_pins Plants→Array` **resolved the wildcard to `StaticMeshComponent[]`** (and `Array Element` → `StaticMeshComponent`) → `compile` **0 errors, 0 warnings**. The exact case that stayed `undetermined` on clipboard paste; the bridge's `TryCreateConnection` fires `NotifyPinConnectionListChanged`, which paste skips.
- **#6 Compile-error read-back — PASS.** A 2nd ForEachLoop made reachable with its wildcard `Array` left unconnected → `compile` returned `{ok:false, numErrors:2, messages:[{severity:"error", text:"The type of Array is undetermined…", nodeGuid:"8FE0ABF…"}, {…pinName:"TargetArray", nodeGuid:"8FE0ABF…"}]}` — structured, mapped to the exact node + pin.
- **Plumbing confirmed:** `X-Mif-Token`, game-thread dispatch, per-action transactions (`create_function`/`remove_function`), `backup_blueprint`, `save_blueprint`, `list_blueprints/variables/graphs`, `get_node`, `add_variable` (array/set; **map is rejected** — `MifBridgeCommon.cpp:690`), `set_variable_default` (incl. **object arrays** — 19 `StaticMesh` refs baked in one call), `connect_pins`, `disconnect_pin`, `set_pin_type`, `add_branch`, `add_get_array_item`, `refresh_node` all round-trip clean. **Field casing = camelCase on the wire** (`blueprintId`, `graphId`, `srcNode`); the owning-class field is **`class`** (Python param `cls` maps to it — `server.py:191`), the parent/cast class fields are `parentClass`/`targetClass`. `compile_blueprint` → endpoint `compile`.

### First real feature built via the bridge — 2026-07-12 (per-plant grow meshes)
Built the entire BotanistExpansion Steel Rack per-plant grow-mesh swap through the bridge (no clipboard, no cook to iterate): added `GrowKeys: Name[]` + `GrowMeshRefs: StaticMesh[]` (defaults set in 2 calls with all 19 entries), then in `UpdateAllPots` a **`ForEachLoop` over GrowKeys + `EqualEqual_NameName(element, CraftID)` → `Branch` → `GetArrayItem(GrowMeshRefs, index)` → a 2nd `SetStaticMesh`** spliced after the existing weed-mesh set (match → plant model; no match → keeps `GrowthMeshes[stage]`). **Compiled 0/0, saved, 19 refs verified baked into the `.uasset`, persistence confirmed via `refresh_node`.** (First cut used `Array_Find` — see the CallFunction-wildcard finding below; reworked to the macro form so it survives reload.)

**★ New finding — array-library CallFunctions (`Array_Find`) can't be made to persist; use a macro instead.** The bridge's `add_function_call` creates `Array_Find` as a plain **`K2Node_CallFunction`** (the editor uses `K2Node_CallArrayFunction`). Its wildcard `TargetArray`/`ItemToFind` do **not** resolve on `connect_pins` OR `refresh_node` (they stay `wildcard` → "The type of Target Array is undetermined"), whereas macros (`ForEachLoop`) and `K2Node_GetArrayItem` DO resolve on connect. **`set_pin_type` forces them and compiles 0/0 — BUT the forced type is TRANSIENT: it reverts to `wildcard` on save+reload** (the node reconstructs from the UFunction, and plain CallFunction has no array-parm re-resolution). So `set_pin_type` on a CallFunction wildcard is NOT a durable fix — it looks fixed, saves "clean," then breaks on next load. **Durable fix: avoid array-library CallFunctions.** For a key→value lookup over parallel arrays, use a **`ForEachLoop` (macro) over the keys + `EqualEqual_NameName` compare + `GetArrayItem`** — all persist (macros/array-nodes re-resolve on reconstruct). **In-session persistence probe: `refresh_node` reproduces the reload reconstruct** — if a wildcard survives `refresh_node` + recompile, it survives reload (Array_Find fails this; ForEach passes). Recommended C++ fix: `add_function_call` should create `K2Node_CallArrayFunction` for functions with `ArrayParm` metadata. Added to the gotcha list.

### ★ Patch 2026-07-12 — `graphId`-scoped node resolution (fixes duplicate-BP "ambiguous node guid")
**Symptom:** editing the SteelRack BP via the bridge failed with *"ambiguous node guid X matches 2 loaded nodes (duplicate blueprints loaded?)"* on every node op. The ModKit editor loads a mod BP as **two live copies** carrying identical `NodeGuid`s (NOT the deployed pak, NOT crash autosaves — both removed, still ambiguous; a cooked-editor mod-asset double-load). `ResolveGraph`/`list_nodes` resolve the graph fine (one primary blueprint at the path), but `ResolveNode`'s global `TObjectIterator<UEdGraphNode>` scan finds both.
**Fix (`MifBridgeCommon.cpp::ResolveNodeField`):** if the request includes a `graphId`, resolve that graph (primary BP) and find the node within `Graph->Nodes` — scoped, unambiguous. Additive: no `graphId` → unchanged global scan. Rebuilt `Build.bat DrugDealerSimulator2Editor` (27s, exit 0).
**Usage:** pass `graphId` alongside `nodeGuid` on `get_node`/`set_pin_default`/`disconnect_pin`/`connect_pins` (direct-curl) whenever a BP double-loads. Proven: fixed the SteelRack `UpdateAllPots` `OutQuery[i]→OutQuery[0]` bug (disconnect index wire + `set_pin_default 0`, compile 0/0, persisted). **DONE 2026-08-28** — the Python wrapper threads `graph_id` through all six relevant tools now (`get_node`, `set_pin_default`, `disconnect_pin`, `connect_pins`, `reconnect_pin`, `splice_into_exec`); `set_pin_default` was the one still missing it, found via `parity_check.py`'s param-reach check, verified live end-to-end.

Remaining §15 (not yet run, lower risk): #1 smoke/undo, #3 GetArrayItem, #4 override+parent, #5 splice, #7 batch atomicity, #8 soak — exercise opportunistically during real graph work.

---

## 16. Build phasing / roadmap

- **Phase 0 (½ day):** plugin skeleton, HTTP server, game-thread dispatch, `open_blueprint`, `list_nodes`, `compile`. This alone gives read-back — huge.
- **Phase 1 (1 day):** the node/pin/variable CRUD in §7/§9 + transactions + `batch`. Enough to build the SteelRack graph end-to-end from Claude. Ship after test #2 passes.
- **Phase 2 (½ day):** recipes (§10), `trigger_cook`, `read_modloader_log`. Closes both compile and runtime loops.
- **Phase 3 (later):** timelines, delegates/custom events, components/SCS, datatables, casts/structs breadth, interface implementation.

**Recommendation:** build Phase 0+1 next time we start a fresh DDS2 graph. Break-even is the *second* graph; the SteelRack alone would have paid it back.

---

## 20 — Build & run (as-built)

Phase 0 + Phase 1 are built. This section is the operational record.

### Layout
```
D:/DDS2SDK/Game/Plugins/MifBridge/          ← in-editor C++ plugin (editor-only)
├── MifBridge.uplugin                        (Editor module, LoadingPhase PostEngineInit, Win64)
└── Source/MifBridge/
    ├── MifBridge.Build.cs                    (deps per §5 + Sockets for loopback check)
    ├── Public/MifBridge.h                    (FMifBridgeModule)
    └── Private/
        ├── MifBridge.cpp                     (module boot + Tools▸"Mif Bridge: Start/Stop" toggle)
        ├── MifBridgeLog.h                    (LogMifBridge category + mif.BridgeDebug CVar)
        ├── MifBridgeServer.h/.cpp            (HTTP :8791, token + loopback gate, game-thread dispatch)
        ├── MifBridgeHandlers.h               (endpoint registry + shared-helper decls)
        ├── MifBridgeCommon.cpp               (resolve BP/graph/node/pin, pin types, serializers, RunEndpoint)
        ├── MifBridgeIntrospect.cpp           (session/introspection/variables/compile read-back)
        └── MifBridgeNodes.cpp                (node create + pin wiring + batch)

<repo>/tools/mcp-server/                    ← Python MCP server (thin wrapper)
├── server.py           (FastMCP, one tool per endpoint, env config, --debug)
├── requirements.txt    (mcp, requests)
├── README.md
└── mcp.json.sample     (copy into .mcp.json; do NOT commit secrets)
```

### Compile
Same source-engine flow as ElectronicNodes. The editor **must be closed** (it locks the editor DLLs);
the running *game* does not block it. Run through PowerShell (Git Bash `cmd //c` opens an interactive shell):
```
& "D:\DDS2SDK\Engine\Windows\Engine\Build\BatchFiles\Build.bat" DrugDealerSimulator2Editor Win64 Development -Project="D:\DDS2SDK\Game\DrugDealerSimulator2.uproject" -WaitMutex
```
Produces `D:/DDS2SDK/Game/Plugins/MifBridge/Binaries/Win64/UnrealEditor-MifBridge.dll`. UBT auto-discovers
the plugin (`EnabledByDefault: true`); no GenerateProjectFiles needed. Toolchain: MSVC 14.36 (LTSC 17.6) — the
same one that builds the rest of this source tree.

### Run
1. Open the editor on `DrugDealerSimulator2.uproject`. The bridge auto-starts (CVar `mif.BridgeAutoStart`,
   default on) and binds `127.0.0.1:8791`. Toggle via **Tools ▸ Mif Bridge: Start/Stop** (label shows the port).
2. Set `MIF_BRIDGE_TOKEN` in the editor's process env and the same value in the MCP server env; requests carry
   it as `X-Mif-Token`. Default `dev` on both sides. Non-loopback callers are rejected regardless.
   Optional `MIF_BRIDGE_PORT` overrides the port on the editor side.
3. Wire `tools/mcp-server/mcp.json.sample` into `.mcp.json` (see the tool README).
4. Debug tracing: `mif.BridgeDebug 1` in the editor console; `--debug` (or `MIF_BRIDGE_DEBUG=1`) on the server.

### Endpoint identifiers
- `blueprintId` = asset object path (`/Game/Foo/BP_Bar.BP_Bar`), returned by `open_blueprint`.
- `graphId` = `<blueprintPath>::<graphName>`, returned by `list_graphs`/`open_blueprint`.
- `nodeGuid` = engine-assigned GUID; returned by every node-creating call and `list_nodes`, resolved globally
  via `TObjectIterator` so callers pass just the guid (no graph context needed).

### As-built deviations from the design (and why)
- **Loopback enforcement is done by peer-address check, not by bind address.** `FHttpServerModule` has no
  loopback-only bind option in 5.3 (it listens on all interfaces), so the server rejects any non-loopback
  `PeerAddress` and still requires the token. Net effect matches the design intent ("a stray browser tab can't
  poke it"); added `Sockets` to the module deps for `FInternetAddr`.
- **`validate` == `compile` without save.** Neither compile nor validate writes the asset (save is the separate
  `save_blueprint` step), so `validate` runs the same in-editor compile and tags `dryRun:true` rather than
  duplicating the asset. Read-only endpoints (incl. compile/validate) run outside a transaction to keep the
  undo stack clean; all mutations are wrapped in one `FScopedTransaction`.
- **`create_function` / `create_event_graph_event` (listed in §9) are deferred.** They are not in the Phase-0/1
  build; the shipped node set builds the SteelRack-class graphs end-to-end. Phase 3 territory.
- **`map` variable container is rejected** (needs a value type); `array`/`set`/none are supported.
- **Struct `FBox`** has no `TBaseStructure` specialization in this engine — it resolves via the reflection
  fallback (`FindFirstObject<UScriptStruct>`), same as any other named struct.

### Phase 2 / Phase 3 as-built notes
- **`recipe_add_debug_print` uses `PrintToModLoader`, not `PrintString`.** `KismetSystemLibrary::PrintString` is
  `DevelopmentOnly` → stripped from the shipped game (does nothing in-game). The repo convention (see
  `mods/dds2/BotanistExpansion/END_TO_END.md`) is a **self-local** `PrintToModLoader(Message:String)` UFunction
  that BPModLoaderMod hooks by name and writes to `UE4SS.log`. The recipe mints that function on the target
  Blueprint if missing (empty body, one `String` input), compiles so it materialises, then calls it on self and
  splices after the given node. This bakes in the `feedback_always_add_debug` convention and avoids the trap of
  copying Brando's node (which hard-imports his mod).
- **`read_modloader_log` tails `UE4SS.log`, not `Saved/Logs/DrugDealerSimulator2.log`.** The spec (§9) guessed
  the latter; research confirmed the real DDS2 runtime sink for both Lua `print()` and Blueprint
  `PrintToModLoader` is `…\Binaries\Win64\ue4ss\UE4SS.log` on `C:\SteamLibrary`. Default path points there;
  `path` overrides it. Read-only; guards against pathological log sizes.
- **`trigger_cook` is PLAN-ONLY — it executes nothing.** Two reasons from research: (1) neither `docs/04` nor
  `docs/11` pins a literal cook command line (cook is abstract "RunUAT … -cook via Brando's DDS2 SDK"; the
  preferred DDS2 lane *skips* cook — `retoc to-legacy` → byte-patch → `retoc to-zen`), and (2) the pipeline
  operates on the **live game paks out-of-editor**, so running it from inside the editor process would be wrong
  and unsafe. The endpoint returns the verified retoc command sequence with all paths pinned
  (`C:\SteamLibrary`, `retoc.exe`, `Content\Paks\Mods\` flat vs `LogicMods\<mod>\`) for the caller to run
  deliberately. This matches §13 "dry-run" and §14 "cook stays a deliberate step."
- **`create_function` inverts pin direction by design.** Function *inputs* are created on the entry node as
  `EGPD_Output`, *outputs* on the result node as `EGPD_Input` (a void signature has no result node, so one is
  spawned and its exec wired from the entry). It compiles at the end so the `UFunction` is callable immediately.
- **`add_literal` is object-only** (`UK2Node_Literal` backs object references). For scalar literals
  (int/float/bool/string/name), set the consuming pin's default via `set_pin_default`.
- **`recipe_argmax_over_components` resolves `Greater_DoubleDouble` or `Greater_FloatFloat`** by candidate list
  (UE5 unified float→double, but names vary), and is a general argmax *update* cluster — the caller supplies the
  score pin and index pin sources (the per-element scoring, e.g. a `Dot_VectorVector`, is domain-specific).

### Post-review hardening (adversarial static review, 2026-07-11)
Two confirmed defects from a multi-agent review were fixed before shipping the Phase-2/3 build:
- **Full compiles never run inside a transaction.** `create_function`, `recipe_add_debug_print`, and `batch`
  need a `CompileBlueprint` (which reinstances the class — trashes the old class/CDO). Doing that inside
  `RunEndpoint`'s `FScopedTransaction` would let a later Ctrl-Z restore dead pointers and crash. These three are
  now **self-managed**: `RunEndpoint` does not wrap them; each opens its own tight transaction(s) around only the
  graph mutations and compiles *after* they close. `batch` additionally rejects the compile-heavy ops
  (`create_function`, `recipe_add_debug_print`, nested `batch`) — call those standalone.
- **`ResolveNode` no longer trusts a global GUID match.** `NodeGuid` is not globally unique (content-browser
  duplication copies it; `CompileBlueprint` clones source nodes into the transient consolidated event graph
  keeping the GUID). Resolution now skips transient-package nodes, requires a real owning blueprint
  (`FindBlueprintForNode`), and returns an **"ambiguous node guid"** error instead of silently editing the wrong
  (or a dead) node when two live assets collide.

### Phase 3 breadth as-built notes
- **`add_timeline` is node-first.** `FBlueprintEditorUtils::AddNewTimeline` only creates the *template*; the
  `UK2Node_Timeline` node is what creates the template (via `PostPlacedNewNode`). So the endpoint spawns the node
  (name + flags set first), then finds the template via `FindTimelineTemplateByVariableName` to add float tracks,
  then `ReconstructNode` to grow the per-track pins.
- **`add_event_dispatcher` creates BOTH a `PC_MCDelegate` member variable AND the signature graph** (mirroring
  `FBlueprintEditor::OnAddNewDelegate`: `AddMemberVariable` → `CreateNewGraph` → `bEditable=false` →
  `CreateDefaultNodesForGraph` + `CreateFunctionGraphTerminators` + `AddExtraFunctionFlags` +
  `MarkFunctionEntryAsEditable`). Without the member var, the compiler's `ConformDelegateSignatureGraphs` strips
  the graph and no delegate property is ever synthesised. It is **self-managed** (compiles outside its transaction)
  so `add_call_dispatcher`/`add_bind_dispatcher` can resolve the `FMulticastDelegateProperty` afterward.
- **`add_switch_enum` populates `EnumEntries` directly** (mirroring the unexported `UK2Node_SwitchEnum::SetEnum`)
  since that symbol is not `BLUEPRINTGRAPH_API`. `SwitchInteger` uses the exported base `AddPinToSwitchNode`;
  `SwitchString` populates `PinNames` before `AllocateDefaultPins`.
- **SCS component transforms use `SetRelative*_Direct`** on the (unregistered) template; `add_component` fails
  cleanly if a named `parentName` doesn't resolve (no silent root-attach). `set_component_transform` takes
  `location`/`scale` as `[x,y,z]` and `rotation` as `[pitch,yaw,roll]`.
- **DataTables are read-only.** `read_datatable`/`get_datatable_row` use `UDataTable::GetTableAsJSON` (WITH_EDITOR)
  and reflect the row struct at runtime — no compile-time type needed. Writes stay on the docs/12 commandlet path.
- **`MakePinType` grew ref prefixes:** `class:X`, `object:X`, `softobject:X`, `softclass:X`, `interface:X`,
  `enum:X` (bare scalar/struct/class names still work). Used by `add_variable`, `create_function`, `set_pin_type`.

### Phase-3 completion notes (2026-07-11)
- **`implement_interface_function`** adds the impl graph for a *return-valued* interface function via
  `CreateNewGraph` + `AddFunctionGraph<UClass>(BP, graph, /*bIsUserCreated*/false, InterfaceClass)` into
  `FunctionGraphs` (mirrors `SMyBlueprint::ImplementFunction`) — **not** `AddInterfaceGraph`. Event-style interface
  functions (no return) go through `add_override_event` instead; the endpoint detects this via
  `FunctionCanBePlacedAsEvent` and redirects.
- **`write_datatable_rows`** (confirm-gated) has two modes: `replace=true` → `UDataTable::CreateTableFromJSONString`
  (full overwrite); otherwise per-row `FDataTableEditorUtils::AddRow` + `FJsonObjectConverter::JsonObjectToUStruct`
  (reflects the runtime-only-known row struct — `AddRow<T>` is uncallable without the compile-time type). It rolls
  back a just-added row if population fails, and broadcasts `RowList` change so open editors refresh.
- **Common nodes** whose class is a *pin default* (`add_spawn_actor`, `add_get_data_table_row`) set it via
  `Pin->DefaultObject` + `PinDefaultValueChanged` *after* `AllocateDefaultPins`; ones whose class shapes the pins
  (`add_get_subsystem` via `Initialize`, `add_make_array` via `NumInputs`) set it *before*. `add_comment` uses
  `UEdGraphNode_Comment` (not a `UK2Node`; no pins). The comment header is `EdGraphNode_Comment.h` (UnrealEd/Public,
  not under `EdGraph/`).

### Adversarial review (three rounds, 2026-07-11)
Every phase was gated behind a multi-agent review (dimensions → find → independent skeptic per finding), all
findings fixed: **Round 1 (Phase 2)** — compile-inside-transaction crash risk + global-`NodeGuid` collision.
**Round 2 (Phase-3 breadth)** — (1) critical: `add_event_dispatcher` missing the `PC_MCDelegate` member var
(dispatcher silently never created); (2) high: `add_timeline` made only the template, not the node; (3) medium:
`add_component` silently root-attached on an unresolved parent; (4) low: `remove_interface` reported success
unconditionally. **Round 3 (Phase-3 completion)** — two `write_datatable_rows` defects: a stray half-written row on
a field-type mismatch (now rolled back via `RemoveRow`), and a misleading `replaced:true` + missing change
broadcast when the replace was a no-op (now gated on `Problems.Num()==0`).

### Not yet verified (needs a human + editor open)
The §15 test plan has **not** been run — most importantly test #2 (the ForEachLoop wildcard regression that
motivated this plugin) and #6 (compiler-reason read-back mapped to the right node). Wildcard resolution via
`TryCreateConnection` is implemented per §7.10 but must be confirmed live before relying on it for a cook. The
Phase-3 breadth endpoints (timelines, dispatchers, SCS, switches, interfaces, datatables) likewise compile clean
but are unverified in-editor.

---

## 17. Appendix A — node-class cheat sheet

| Purpose | Class | Key setup call |
|---|---|---|
| Call function/library | `UK2Node_CallFunction` | `SetFromFunction(UFunction*)` |
| Get variable | `UK2Node_VariableGet` | `VariableReference.SetSelfMember(name)` |
| Set variable | `UK2Node_VariableSet` | same |
| Branch | `UK2Node_IfThenElse` | — |
| Macro (ForEach/Gate/etc.) | `UK2Node_MacroInstance` | `SetMacroGraph(UEdGraph*)` |
| Array element | `UK2Node_GetArrayItem` | — (pins: Array/Dimension 1/Output) |
| Event / override | `UK2Node_Event` | `EventReference.SetExternalMember`, `bOverrideFunction=true` |
| Parent call | `UK2Node_CallParentFunction` | `SetFromFunction` |
| Dynamic cast | `UK2Node_DynamicCast` | set `TargetType` |
| Make/Break struct | `UK2Node_MakeStruct`/`UK2Node_BreakStruct` | set `StructType` |
| Reroute | `UK2Node_Knot` | (bridge tunnels through, rarely creates) |
| Custom event | `UK2Node_CustomEvent` | set `CustomFunctionName` |
| Timeline | `UK2Node_Timeline` | needs `UTimelineTemplate` (phase 3) |

## 18. Appendix B — pin-type cheat sheet (the stuff that bit us)

| Concept | Correct (UE5.3) | Trap we hit |
|---|---|---|
| Float | `PinCategory="real", PinSubCategory="double"` | writing `"float"` (UE4 habit) → mismatch |
| Int | `PinCategory="int"` | — |
| Bool | `PinCategory="bool"` | — |
| Struct (Guid) | `PinCategory="struct", PinSubCategoryObject=<UScriptStruct Guid>` | wrong/missing struct path |
| Object (component) | `PinCategory="object", PinSubCategoryObject=<UClass StaticMeshComponent>` | class-ref string format |
| Array | add `ContainerType=Array` | forgetting it → wildcard won't resolve |
| Wildcard | resolves only via `TryCreateConnection` | paste leaves it `undetermined` |
| Self/target | hidden for library statics, shown for member calls | wiring `self` when it should be hidden |
| Variable name | must be exact, no trailing WS | `"BestPotIndex "` invisible mismatch |

> Bridge principle: **never hand-set any of the left column.** Derive every pin from the reflected `UFunction`/`FProperty`, and let `TryCreateConnection` resolve wildcards. That is the difference between this tool and the clipboard.

## 19. Appendix C — known-good T3D fragments (fallback path)

Keep the current copy-paste workflow documented as a fallback for when the editor isn't bridged. The verified fragments live in scratch (`aim_cluster.txt` etc.) and the `docs/11` blueprint section. Rule if hand-authoring T3D again: (a) validate link reciprocity with the python checker before shipping to the user, (b) **never** author macro instances by hand — have the user drop them from the palette, (c) create variables *before* referencing nodes and confirm exact names.

---

*Cross-refs: `docs/04` (build/deploy paths), `docs/11` (DDS2 mod architecture, decompile pipeline), `docs/12` (economy/datatables). Memory: `feedback_bp_node_wiring_precision`, `feedback_always_add_debug`, `dds2-modding-setup`.*
