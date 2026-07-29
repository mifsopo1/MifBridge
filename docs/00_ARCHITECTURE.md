# MifBridge — architecture map

Editor-only UE 5.3 plugin exposing a loopback HTTP API over Unreal's Blueprint graph API.

---

## Request path

```
MCP client (Claude Code)
   │  stdio JSON-RPC
   ▼
server.py                      one @mcp.tool per endpoint
   │  HTTP POST 127.0.0.1:8791/api/<endpoint>   header X-Mif-Token
   ▼
FMifBridgeServer               token gate → loopback gate → run INLINE in the HTTP ticker
   │
   ▼
MifBridge::RunEndpoint         script guard → transaction policy → dispatch
   │
   ▼
H_<endpoint>(In, Out)          one free function per endpoint
   │
   ▼
UnrealEd graph API             the live Blueprint in the open editor
```

Every handler has the identical signature `(const TSharedRef<FJsonObject>& In, TSharedRef<FJsonObject>& Out)`.
Read-only handlers fill `Out`; mutating handlers call `Modify()` and end at
`MarkBlueprintAsStructurallyModified` **inside the single transaction `RunEndpoint` opened** — they
never open their own.

### Threading — there is no hop

**The handler runs synchronously, inline, in `FHttpServerModule`'s own tick.** `FHttpServerModule`
derives from `FTSTickerObjectBase`, so the request callback is *already* on the game thread, invoked
from `FTSTicker::GetCoreTicker().Tick()` — which `FEngineLoop::Tick()` runs **after `GEngine->Tick()`
has completed the whole world tick**, outside every tick group. Nothing is marshalled and nothing is
deferred (`MifBridgeServer.cpp:229-265`).

**Do NOT reach for `AsyncTask(ENamedThreads::GameThread, …)`** — the source comment says so in those
words. It enqueues onto the named-thread queue, which is also pumped from inside
`FTickTaskSequencer::ReleaseTickGroup() -> WaitUntilTasksComplete()`, so a compile-heavy endpoint
reinstances actors mid-tick-group and the next `FTickFunction` hits
`check(!"Pure virtual not implemented")` (`EngineBaseTypes.h:409`) with no MifBridge frame on the
stack. That was the model this document used to describe; it is exactly wrong. The consequence for
callers and endpoint authors — a blocking handler blocks the ticker that would have to advance
whatever it is waiting on — is `docs/02_GOTCHAS.md` §8.

The only surviving hop is the off-game-thread entry (unreachable over the current transport), which
adds a one-shot `FTSTicker` delegate to land on the *same* post-world-tick point and blocks until it
has run.

---

## Source layout

| File | Owns |
|---|---|
| `MifBridge.cpp` | Module startup/shutdown, `Tools ▸ Mif Bridge` menu, autostart CVar. Skips autostart under `IsRunningCommandlet()` so cooks don't fight for the port. |
| `MifBridgeServer.{h,cpp}` | `FHttpServerModule` routing, token + loopback enforcement, **inline** post-world-tick execution (no marshalling — see *Threading* above) |
| `MifBridgeHandlers.h` | **The contract.** Every endpoint declaration (`MIF_DECL`) + every shared helper |
| `MifBridgeCommon.cpp` | Registry (`MIF_BIND`), `RunEndpoint`, transaction policy, resolution (blueprint/graph/node/pin/class/struct), `MakePinType`, `PlaceAndInit`, JSON serializers |
| `MifBridgeIntrospect.cpp` | Session/assets, listing, variables + variable flags, `CompileBlueprintInto` |
| `MifBridgeNodes.cpp` … `Nodes6.cpp` | Node creation and pin wiring, split by phase |
| `MifBridgeNodes5.cpp` | Generic reflection property get/set (`set_property` dot-path walker) |
| `MifBridgeWidgets.cpp` | `UWidgetBlueprint` — Is-Variable, bindings, widget tree |
| `MifBridgeAnimation.cpp` | Animation **asset** introspection (read-only) |
| `MifBridgeCooked.cpp` | Mounted-container / cooked-package introspection |
| `MifBridgeDelegates / Components / Interfaces / DataTables / AssetOps` | Their namesakes |
| `MifBridgeRecipes.cpp` | Composite multi-node recipes |
| `MifBridgeNodes7.cpp` | Later node additions (the `Nodes*` split is chronological, not thematic) |
| `MifBridgeInherited.cpp` | Inherited-component overrides via `UInheritableComponentHandler` — the Details-panel write path |
| `MifBridgeMaterials.cpp` | Material + material-function graph authoring, expression wiring, recompile, shader-compile poll |
| `MifBridgeAuthoring.cpp` | Level-authoring throughput: `spawn_many`, `duplicate_actors`, material instances, foliage |
| `MifBridgeLevel.cpp` | Placed-actor editing in the open level (spawn / transform / label / delete / select) |
| `MifBridgeWorld.cpp` | World lifecycle (`new_level` / `load_level` / `save_level_as`), spline authoring, ground snapping |
| `MifBridgeStreaming.cpp` | Sublevel composition + PIE level instances. Every mutating verb pre-checks an engine modal/assert; the world-mutating ones defer a tick and report through an op log |
| `MifBridgePIE.cpp` | Play-In-Editor control and runtime observation |
| `MifBridgeLandscape.cpp` | Terrain: create / sculpt / paint / RVT binding / info |
| `MifBridgeSpatial.cpp` | Bounds, overlaps, ground traces, viewport capture, scene report |
| `MifBridgeViewport.cpp` | The camera the USER sees (as opposed to `capture_camera`) |
| `MifBridgeNavigation.cpp` | Nav bounds, async navmesh build, nav-driven movement |
| `MifBridgeUserTypes.cpp` | User-defined struct + enum authoring |
| `MifBridgeUndo.cpp` | Undo/redo introspection and rollback, dirty-package flows |
| `MifBridgeCooked.cpp` | Mounted-container / cooked-package introspection, landscape draw diagnostics |
| `MifBridgeAssetOps.cpp` | Asset lifecycle (delete/rename/duplicate), referencers, dependencies, `audit_unused` |
| `MifBridgeReconstruct.cpp` | `create_editable_child` — **requires the engine fork** (`CompiledBlueprintReconstructor.h`) |
| `MifBridgePipeline.cpp` | Mod-loader log tail, cook helper |

### Adding an endpoint — files that MUST stay in sync

1. `MifBridgeHandlers.h` — `MIF_DECL(name)`
2. `MifBridgeCommon.cpp` — `MIF_BIND(name)` in `Handlers()`
3. `MifBridgeCommon.cpp` — add to `IsReadOnlyEndpoint` **or** `IsSelfManagedEndpoint` if it qualifies
4. `<some>.cpp` — define `H_name`
5. `server.py` (separate repo: `Eddie_v2/tools/ue5-mcp-bridge/`) — the MCP tool wrapper
6. `README.md` + `docs/02_GOTCHAS.md`

> Steps 1–2 are checkable: the `MIF_DECL` and `MIF_BIND` name sets must be identical, and a missing
> `MIF_BIND` is a link error rather than a silent gap. **Step 5 is not checkable and drifts** — see
> the sync warning below.

---

## Transaction policy — the part that crashes if you get it wrong

`RunEndpoint` classifies every endpoint into exactly one of three buckets:

| Bucket | Behaviour | Why |
|---|---|---|
| **Read-only** (`IsReadOnlyEndpoint`) | No transaction | An undo step for a read pollutes the stack |
| **Self-managed** (`IsSelfManagedEndpoint`) | No outer transaction; handler opens its own tight ones | The handler runs a **full compile** |
| Everything else | One `FScopedTransaction` wrapping the whole handler | Ctrl-Z undoes the entire bridge action |

**A full `FKismetEditorUtilities::CompileBlueprint` must never run inside a transaction.** Compiling
reinstances the generated class and trashes the old class/CDO; a later Ctrl-Z would restore pointers
to freed objects and crash the editor. Self-managed handlers therefore open a *tight* transaction
around only the graph mutations and compile after it closes.

A skeleton-only regen (`MarkBlueprintAsStructurallyModified`) is **not** a full compile and is
transaction-safe — which is why the variable-flag and widget endpoints can stay in the default bucket.

### What the transaction does NOT give you: rollback on failure

`RunEndpoint` calls `Transaction.Cancel()` when a handler returns `ok:false`. **That discards the
undo entry. It does not revert the handler's writes.** `UTransBuffer::Cancel` broadcasts
`TransactionCanceled`, ends the operation, nulls `GUndo` and pops the transaction off `UndoBuffer` —
it never calls `FTransaction::Apply()`, whose only callers are `UTransBuffer::Undo` and `::Redo`
(`Editor/UnrealEd/Private/EditorTransaction.cpp:1387-1437`, `:1624`, `:1688`). The engine's own doc
for the virtual says as much: *"Cancels the current transaction, no longer capture actions to be
placed in the undo buffer"* (`Editor/Transactor.h:514-519`).

Separately, plenty of the objects the bridge creates are not `RF_Transactional` at all
(`UInheritableComponentHandler` and its override templates, for two), so `Modify()` on them records
nothing even before the cancel question arises — `SaveToTransactionBuffer` requires the flag
(`UObjectGlobals.cpp:3131-3134`).

The `Cancel()` is still worth having: without it a failed call leaves an entry on the undo stack, and
the user's next Ctrl-Z undoes a bridge action that reported failure instead of their own last edit.

**A failed call leaves nothing behind only if the HANDLER is written that way** — validate every
input before the first mutation, or undo what it created on its own failure path (and only what *it*
created). This was proved the hard way; see `docs/01_POSTMORTEMS.md` **PM-007** and
`docs/audit/06_IMPLEMENTED.md` § *Batch M* for the per-handler audit.

Concretely, as of Batch M — **there is no blanket rollback, and no plan to add one**:

- **5 handlers were reordered to validate before creating**: `override_inherited_component`,
  `add_component`, `add_foliage_instances`, `add_timeline`, `create_material_instance`.
- **4 more name exactly what they leave behind** and which endpoint removes it, because a reorder was
  not safe: `add_pin`, `recipe_add_debug_print`, `create_struct`, `set_variable_flags`.
- **Every other mutating handler is atomic on failure only if it happens to validate first.** Assume
  it does not. Atomicity is a property of the order a handler is written in — there is no central
  mechanism, and a comment claiming one is the specific mistake PM-007 records.

---

## Data ownership

| Data | Single source of truth |
|---|---|
| Endpoint set | `MifBridgeHandlers.h` `MIF_DECL` block |
| Endpoint→handler binding | `Handlers()` in `MifBridgeCommon.cpp` |
| Transaction class | `IsReadOnlyEndpoint` / `IsSelfManagedEndpoint` |
| Type-string grammar | `MakePinType` in `MifBridgeCommon.cpp` |
| Member variable flags | `Blueprint->NewVariables[i]` (`FBPVariableDescription`) — reached via `FBlueprintEditorUtils::GetBlueprintVariablePropertyFlags`, never cached |
| Graph identity | `GraphIdOf` = `<blueprintPath>::<dotted graph path>` |
| Node identity | `UEdGraphNode::NodeGuid` — **not globally unique**; pass `graphId` to disambiguate |

---

## `server.py` parity — the hazard is closed, keep it closed

**This section used to describe a repo split that no longer exists.** It named
`C:\Users\andre\Documents\GitHub\Eddie_v2\tools\ue5-mcp-bridge\server.py` — a path that is not on
disk — and claimed the wrapper exposed **82 of 102** endpoints with 20 having no MCP tool. All three
numbers and the path were wrong, and every endpoint in that "missing" list has had a tool for some
time. A stale hazard note is worse than none: it sends the next agent to fix drift that is not there
and to edit a file that does not exist.

**Actual, as of Batch K:**

| thing | count |
|---|---|
| `MIF_DECL` in `Source/MifBridge/Private/MifBridgeHandlers.h` | **191** |
| `MIF_BIND` in `Source/MifBridge/Private/MifBridgeCommon.cpp` | **191** (same name-set, diff empty both ways) |
| `H_*` handler definitions across `Private/*.cpp` | **191** |
| External (`kr_*`) endpoints registered by `MifKismetReconstructor` | **12** |
| **Endpoints total** | **203** |
| `@mcp.tool()` defs in `tools/ue5-mcp-bridge/server.py` | **203**, all above the `if __name__` guard |

The wrapper lives at `Game/Plugins/MifBridge/tools/ue5-mcp-bridge/server.py` — **in this repo**,
beside the plugin, which is what closed the drift. `self_audit` reports the live endpoint count from
the running DLL and is the authority over any number written down here.

Regenerate the parity diff (run from the plugin root):

```bash
sed -n 's/.*MIF_BIND(\([a-z_0-9]*\)).*/\1/p' Source/MifBridge/Private/MifBridgeCommon.cpp | sort -u > /tmp/plugin.txt
sed -n 's/.*_post("\([a-z_0-9]*\)".*/\1/p' tools/ue5-mcp-bridge/server.py | sort -u > /tmp/mcp.txt
comm -23 /tmp/plugin.txt /tmp/mcp.txt   # endpoints with no tool  -> must be empty
comm -13 /tmp/plugin.txt /tmp/mcp.txt   # tools with no endpoint  -> the 12 kr_* externals, and nothing else
```

The second diff is **not** empty by design: the 12 `kr_*` endpoints are registered at runtime by the
provider plugin (`Public/MifBridgeEndpointRegistry.h`) and never appear in `MIF_BIND`. Anything else
in that column is real drift.

---

## Build

Engine: **`D:\UE532`** (source fork — the launcher `UE_5.3` lacks
`CompiledBlueprintReconstructor.h`, which `MifBridgeReconstruct.cpp` and MifKismetReconstructor both
need). The project's `EngineAssociation` GUID maps to it via
`HKCU:\SOFTWARE\Epic Games\Unreal Engine\Builds`.

```bash
D:/UE532/Engine/Build/BatchFiles/Build.bat DrugDealerSimulator2Editor Win64 Development \
  -Project="D:/DDS2SDK/Game/DrugDealerSimulator2.uproject" -WaitMutex
```

**The editor must be closed — and "the editor" is three processes, not one.** Kill and verify
`UnrealEditor.exe`, **`UnrealEditor-Cmd.exe`** (a mod COOK runs under this name and holds the plugin
DLLs exactly like an interactive editor) and **`LiveCodingConsole.exe`** (holds a mutex UBT checks
independently, and survives the editor exiting). Otherwise UBT aborts with *"Unable to build while
Live Coding is active"*, and a partially-succeeding build reports `LNK1104: cannot open file` for
every DLL a surviving process holds open.

```bash
powershell -NoProfile -Command \
  "Get-Process UnrealEditor,UnrealEditor-Cmd,LiveCodingConsole -ErrorAction SilentlyContinue |
     Select-Object Id,ProcessName,MainWindowTitle"
```

**Never abort a build**, and never build merely to find out whether a change is live — both traps,
with the seconds-long binary check that replaces the second one, are `docs/01_POSTMORTEMS.md`
**PM-008**.
