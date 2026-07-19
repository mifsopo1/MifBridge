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
| **server.py** (MCP server) | FastMCP wrapper, 1 tool per endpoint | `tools/ue5-mcp-bridge/` |

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
- **Confirm‑gated destruction.** Deleting nodes/variables/functions/components/interfaces or writing DataTable rows requires an explicit `confirm=true`.
- **Editor‑only.** The module never cooks into a shipped build.

---

## Capabilities (79 tools, 1:1 with HTTP endpoints)

- **Session / assets** — open, list, save, back up Blueprints.
- **Introspection** — list graphs/nodes/variables/functions, get a node's full pin detail, find nodes by class/title/function, resolve structs.
- **Variables** — add / rename / remove / set‑default (member or local; array & set containers; object/class/soft/interface/enum types). *Map containers aren't supported.*
- **Nodes** — function calls, variable get/set, branch, macro instances (e.g. ForEachLoop), get‑array‑item, override events, parent calls, casts, custom events, make/break struct, self, literals, sequence, spawn actor, get subsystem, make array, format text, get datatable row, comment, timeline, switch (enum/int/string), enum literal.
- **Pins / wiring** — connect, disconnect, reconnect, set pin default, set pin type, splice into an exec chain.
- **Functions / events / interfaces / components / dispatchers / datatables** — create/implement/remove functions, add event dispatchers + call/bind, add/remove/list interfaces, add/list/remove SCS components + transforms, read datatables & write rows.
- **Compile / diagnostics** — `compile` and `validate` return `{numErrors, numWarnings, messages:[{severity, text, nodeGuid, pinName}]}`.
- **Batch & recipes** — run many ops with one final compile; higher‑level recipes (debug‑print splice, reset‑and‑loop, override‑and‑call‑parent, argmax‑over‑components).
- **Pipeline hooks** — tail the UE4SS mod‑loader log; a plan‑only cook helper.

Full endpoint reference and design notes: **`docs/13_UE5_MCP_BRIDGE_PLUGIN.md`**.

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

## Gotchas worth knowing

- **Array‑library calls (`Array_Find`) won't stay typed — use a macro.** A raw `Array_Find` call node's wildcard pins can be forced to a type and compile clean, but the type **reverts to wildcard on save+reload**. For a durable key→value lookup over parallel arrays, use a **`ForEachLoop` macro + name‑compare + `GetArrayItem`** (macros/array nodes re‑resolve on reconstruct). `refresh_node` reproduces the reload, so use it to test durability before you cook.
- **Compile‑heavy ops run alone.** `create_function`, `recipe_add_debug_print`, and `batch` compile outside the blanket transaction (a full compile reinstances the class). Don't nest them.
- **Double‑loaded Blueprints** (some modded/cooked assets load as two copies with identical node GUIDs) need **`graphId`‑scoped** node resolution — pass `graphId` alongside `nodeGuid`.
- **`add_literal` is object‑only** — scalar literals go via `set_pin_default`.
- **Logging** — recipes use `PrintToModLoader` (hooked by UE4SS), because `PrintString` is stripped from shipping builds.

---

*MifBridge — by Mif. Editor tooling; not shipped in cooked builds.*
