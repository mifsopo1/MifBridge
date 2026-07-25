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
FMifBridgeServer               token gate → loopback gate → game-thread hop
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

---

## Source layout

| File | Owns |
|---|---|
| `MifBridge.cpp` | Module startup/shutdown, `Tools ▸ Mif Bridge` menu, autostart CVar. Skips autostart under `IsRunningCommandlet()` so cooks don't fight for the port. |
| `MifBridgeServer.{h,cpp}` | `FHttpServerModule` routing, token + loopback enforcement, game-thread marshalling |
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

## Known sync hazard: `server.py`

The MCP wrapper lives in a **different repository**
(`C:\Users\andre\Documents\GitHub\Eddie_v2\tools\ue5-mcp-bridge\server.py`) and has no automated
check against the plugin. It currently exposes **82** of the plugin's **102** endpoints — 20
endpoints are reachable over raw HTTP but have no MCP tool:

```
add_create_widget      add_make_map           add_tree_widget        add_widget_binding
create_editable_child  delete_asset           describe_animation     describe_class
duplicate_asset        get_property           list_animations        list_enum_values
list_object_properties remove_pin             remove_tree_widget     remove_widget_binding
rename_asset           set_property           set_variable_flags     set_widget_is_variable
```

Regenerate the diff with:

```bash
sed -n 's/.*MIF_BIND(\([a-z_0-9]*\)).*/\1/p' Source/MifBridge/Private/MifBridgeCommon.cpp | sort -u > /tmp/plugin.txt
sed -n 's/.*_post("\([a-z_0-9]*\)".*/\1/p' <path>/server.py | sort -u > /tmp/mcp.txt
comm -23 /tmp/plugin.txt /tmp/mcp.txt
```

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

**The editor must be closed** — otherwise UBT aborts with *"Unable to build while Live Coding is
active"*, and a partially-succeeding build reports `LNK1104: cannot open file` for every DLL the
running editor holds open.
