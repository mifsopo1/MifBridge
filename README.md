# MifBridge

**Let an AI edit your Unreal Blueprints — and read the compiler errors back.**

MifBridge is a small in‑editor Unreal Engine plugin plus a Model Context Protocol (MCP) server that lets an AI assistant (Claude Code) **build, wire, and compile Blueprint graphs programmatically, then read the actual compiler output**. It replaces the blind "AI writes T3D → you paste → you screenshot the errors → AI guesses → repeat" loop with a direct, closed feedback loop.

Because every change goes through Unreal's own graph API (`Schema->TryCreateConnection`, `ReconstructNode`, `FKismetEditorUtilities::CompileBlueprint`), it fires the pin/notification callbacks that clipboard paste skips — the callbacks that resolve wildcard pins, relink variables, and expand macros. Every edit is wrapped in a transaction, so **Ctrl‑Z in the editor undoes anything the AI did.**

---

## How it works

```
Claude Code (MCP client)
      │  MCP tool call (stdio JSON-RPC)
      ▼
server.py  (FastMCP wrapper — one tool per endpoint)
      │  HTTP POST  http://127.0.0.1:8791/api/<endpoint>   (header: X-Mif-Token)
      ▼
MifBridge  (UE editor plugin: token gate → loopback gate → game-thread hop → transaction)
      ▼
UnrealEd graph API  →  the live Blueprint in your open editor
```

The plugin answers each request on the game thread, applies it through the real editor API, compiles, and returns the **structured compiler message list mapped to node GUID + pin name** — so the AI reads the exact error instead of a screenshot.

---

## Requirements

- **Unreal Engine 5.3** — built from source (editor target). MifBridge is an **editor‑only** C++ plugin; it must be compiled against the same engine you run. It is not a launcher/marketplace‑engine drop‑in (marketplace prebuilts won't ABI‑match a source build). Win64 only.
- **Python 3.10+** for the MCP server, with `mcp>=1.2.0` and `requests>=2.31.0`.
- **An MCP client** — Claude Code (or anything that speaks MCP over stdio).

---

## What's in the box

| Part | What it is | Where it lives |
|---|---|---|
| **MifBridge** (UE plugin) | The in‑editor HTTP bridge (C++) | `<YourProject>/Plugins/MifBridge/` |
| **server.py** (MCP server) | FastMCP wrapper, 1 tool per endpoint | `<YourProject>/Plugins/MifBridge/tools/ue5-mcp-bridge/` |

Both halves live in **this** repo on purpose. Every endpoint needs a `MIF_DECL` + `MIF_BIND` in the
C++ **and** a matching `@mcp.tool` in `server.py`; when they lived in separate repos they drifted
silently. One repo, one commit, no drift — parity is currently exact at **203 tools against 203
endpoints** (191 built-in + 12 registered by `MifKismetReconstructor`). Verify with:

```bash
sed -n 's/.*MIF_BIND(\([a-z_0-9]*\)).*/\1/p' Source/MifBridge/Private/MifBridgeCommon.cpp | sort -u > /tmp/a
sed -n 's/.*_post("\([a-z_0-9]*\)".*/\1/p' tools/ue5-mcp-bridge/server.py | sort -u > /tmp/b
diff /tmp/a /tmp/b && echo "1:1 parity"
```

---

## Install

### 1. The plugin

1. Copy the `MifBridge/` folder into your project's `Plugins/` directory. Copy **`Source/`** and the **`.uplugin`** — not any prebuilt `Binaries/`/`Intermediate/` from a different engine.
2. The `.uplugin` is `"EnabledByDefault": true`, so no project‑file regeneration is needed.
3. Build your project's **Editor** target (with the editor **closed**):
   ```
   <Engine>\Build\BatchFiles\Build.bat <YourProject>Editor Win64 Development -Project="<...>.uproject" -WaitMutex
   ```
   This produces `Plugins/MifBridge/Binaries/Win64/UnrealEditor-MifBridge.dll`.

### 2. The MCP server

```bash
cd tools/ue5-mcp-bridge
pip install -r requirements.txt
```

### 3. Connect it to Claude Code

Copy `mcp.json.sample` into your project‑scoped `.mcp.json` (or your user `~/.claude` config) and set a token that matches the editor:

```json
{
  "mcpServers": {
    "mif-ue5": {
      "command": "python",
      "args": ["<path>/tools/ue5-mcp-bridge/server.py"],
      "env": {
        "MIF_BRIDGE_URL": "http://127.0.0.1:8791/api",
        "MIF_BRIDGE_TOKEN": "change-me-to-match-the-editor"
      }
    }
  }
}
```

---

## Run

1. **Open the editor** on your project. MifBridge **auto‑starts** and binds `127.0.0.1:8791`. Toggle it any time from **Tools ▸ Mif Bridge: Start / Stop** (the menu shows the live port).
2. The editor reads two env vars at startup: **`MIF_BRIDGE_TOKEN`** (default `dev`) and **`MIF_BRIDGE_PORT`** (default `8791`). Set the same token the MCP server uses.
3. Start the MCP server (Claude Code launches it for you from `.mcp.json`, or run it directly):
   ```bash
   python server.py           # stdio transport
   python server.py --debug   # + request/response tracing on stderr
   ```
4. Smoke test with curl:
   ```bash
   curl -s -X POST http://127.0.0.1:8791/api/list_blueprints \
     -H "X-Mif-Token: dev" -H "Content-Type: application/json" -d '{"filter":"BP_"}'
   ```

Server env vars: `MIF_BRIDGE_URL` (default `http://127.0.0.1:8791/api`), `MIF_BRIDGE_TOKEN` (default `dev`), `MIF_BRIDGE_TIMEOUT` (default `30`s), `MIF_BRIDGE_DEBUG`.

---

## Security

MifBridge lets a local process **modify your project**, so it is locked down to a single dev machine:

- **Loopback only.** Non‑loopback callers are rejected in‑handler (127.*/::1). The port must never be exposed off‑box.
- **Shared‑secret token.** Every request must carry `X-Mif-Token` equal to the editor's token. **Change `MIF_BRIDGE_TOKEN` from the `dev` default** on both the editor and the server before using it for anything real, and don't commit the secret.
- **Undo‑safe.** Every mutation is a transaction — Ctrl‑Z reverts it.
- **Confirm‑gated destruction.** Deleting nodes/variables/functions/components/interfaces, deleting or renaming a whole asset, or writing DataTable rows all require an explicit `confirm=true`.
- **Editor‑only.** The module never cooks into a shipped build.

---

## Capabilities (203 HTTP endpoints: 191 built-in + 12 external)

- **Session / assets** — open, list, save, back up Blueprints; create new Blueprints (incl. function libraries, interfaces, macro libraries, widget blueprints); delete, rename, or duplicate any `/Game/` asset.
- **Introspection** — list graphs/nodes/variables/functions, get a node's full pin detail, find nodes by class/title/function, resolve structs, describe a class's callable functions/properties/dispatchers, list enum values. Graph enumeration recurses into **nested** graphs — anim state machines, their states, transition rules, and collapsed/composite node bodies — which are addressed by a dotted `graphId` (`…::AnimGraph.Locomotion.Idle`).
- **Animation assets** — `list_animations` (asset‑registry only, never loads) and `describe_animation`: notifies (including notify‑*state* windows and branching points), curves, sync markers, montage sections/slots, blend‑space axes and samples.
- **Generic reflection** — read or write any `UObject`'s properties by dot-path (`get_property`/`set_property`) or dump every top-level property on an object (`list_object_properties`) — the same mechanism the Details panel uses, so it covers non-Blueprint assets too (DataAssets, `InputMappingContext`, `InputAction`, …).
- **Variables** — add / rename / remove / set‑default (member or local; array & set containers; object/class/soft/interface/enum types). `set_variable_flags` covers the whole Details‑panel flag set on member variables — **Replicated / RepNotify (auto‑creating the `OnRep_` graph) / replication condition**, **SaveGame**, transient, config, instance‑editable, blueprint‑read‑only, expose‑on‑spawn, advanced‑display, interp, deprecated, category, tooltip — and the same keys work inline on `add_variable`. `list_variables` reports the current flags back. *Map containers aren't supported.*
- **Nodes** — function calls, variable get/set, branch, macro instances (e.g. ForEachLoop), get‑array‑item, override events, parent calls, casts, custom events, make/break struct, self, literals, sequence, spawn actor, get subsystem, make array, make map, format text, get datatable row, comment, timeline, switch (enum/int/string), enum literal, create widget.
- **Pins / wiring** — connect, disconnect, reconnect, set pin default, set pin type, splice into an exec chain, `remove_pin` (user‑defined parameter pins, and duplicate‑pin repair).
- **Functions / events / interfaces / components / dispatchers / datatables** — create/implement/remove functions, add event dispatchers + call/bind, add/remove/list interfaces, add/list/remove SCS components + transforms, read datatables & write rows.
- **Widget Blueprints** — toggle Is‑Variable, add/remove widget‑tree data bindings (`add_widget_binding`/`remove_widget_binding`), add/remove tree widgets (`add_tree_widget`/`remove_tree_widget`).
- **Cooked‑BP reconstruction** — mint a persistent editable child/sibling of a cooked Blueprint (`create_editable_child`), optionally reconstructing its whole Blueprint‑parent chain into editable siblings too (`variant: "full"`) instead of leaving the parent layer as cooked stubs.
- **Compile / diagnostics** — `compile` and `validate` return `{numErrors, numWarnings, messages:[{severity, text, nodeGuid, pinName}]}`.
- **Batch & recipes** — run many ops with one final compile; higher‑level recipes (debug‑print splice, reset‑and‑loop, override‑and‑call‑parent, argmax‑over‑components).
- **Pipeline hooks** — tail the UE4SS mod‑loader log; a plan‑only cook helper.

---

## License

**MIT** — see [`LICENSE`](LICENSE). MifBridge is entirely original code and does
not include or link any GPL-licensed source, so you're free to use, modify, and
redistribute it under the permissive MIT terms.

It does link Unreal Engine at build time (the engine is covered by Epic's Unreal
Engine EULA, not this license), and its `create_editable_child` endpoint calls an
engine-side function from a cooked-editor engine fork — so that one endpoint needs
the fork to build. At runtime only, it cooperates with the separate
**MifKismetReconstructor** plugin (GPL-3.0) through an engine-provided delegate;
that plugin is distributed separately and is not part of this MIT work.

---

## Docs

- [`docs/02_GOTCHAS.md`](docs/02_GOTCHAS.md) — **parameter grammar and traps.** Accepted spellings
  for node/class/pin parameters, the type grammar (including the `object:`/`class:`/`enum:` prefixes),
  variable‑flag semantics, and cooked‑Blueprint behaviour. Read this before spending a probe.
- [`docs/01_POSTMORTEMS.md`](docs/01_POSTMORTEMS.md) — symptom → root cause → fix → prevention for
  every bug that cost real time.

### The short version

- **`float` is a true 32‑bit float.** It used to be an alias for `double`; if you have graphs that
  passed `"float"` expecting 64‑bit, change them to `"double"`. This is what unblocks UMG
  `TAttribute<float>` delegate bindings, which reject a double‑returning function.
- **Cooked Blueprints have no graphs to read.** Cooking strips the `UBlueprint`; only the generated
  class ships. `list_graphs`/`find_nodes` say so explicitly now and name the route out —
  `mif.kr.Reconstruct` via `run_console` to read, `create_editable_child` to edit.
- **Node parameters accept `nodeGuid` / `node` / `guid` / `nodeId` interchangeably**, and both
  dashed and undashed GUIDs work everywhere. Endpoints taking *two* nodes keep distinct names.
- **A required class parameter can no longer be omitted.** An empty class used to resolve to the
  blueprint's own class — a silent self‑cast/self‑spawn that compiled clean.
- **Array‑library calls are first‑class.** `add_function_call` picks the same `UK2Node_CallFunction` **subclass** the engine would — `UK2Node_CallArrayFunction` for anything tagged `MD_ArrayParam`, plus the data‑table, commutative‑operator and interface‑message variants — and reports the choice back as `nodeClass`. This supersedes the old "`Array_Find` won't stay typed, use a `ForEachLoop` macro" rule: the wildcard reversion was caused by spawning a plain `CallFunction`, which has none of the array node's wildcard‑propagation logic. `refresh_node` still reproduces a reload reconstruct — use it to prove durability before you cook.
- **Compile‑heavy ops run alone.** `create_function`, `create_blueprint`, `recipe_add_debug_print`, `batch`, `set_property` (widget‑BP branch), `add_event_dispatcher`, and `create_editable_child` compile outside the blanket transaction (a full compile reinstances the class). Don't nest them.
- **Asset lifecycle is `/Game/`‑only and self‑managed.** `delete_asset`/`rename_asset`/`duplicate_asset` act on whole packages via `IAssetTools`/`ObjectTools`, not the Blueprint graph API — they refuse anything outside `/Game/` and run headless (no confirmation dialogs to click). `delete_asset` and `rename_asset` require `confirm=true`; `duplicate_asset` doesn't, since it never destroys or overwrites existing data.
- **Double‑loaded Blueprints** (some modded/cooked assets load as two copies with identical node GUIDs) need **`graphId`‑scoped** node resolution — pass `graphId` alongside `nodeGuid`.
- **`add_literal` is object‑only** — scalar literals go via `set_pin_default`.
- **Logging** — recipes use `PrintToModLoader` (hooked by UE4SS), because `PrintString` is stripped from shipping builds.

---

*MifBridge — by Mif. Editor tooling; not shipped in cooked builds.*
