# MifBridge MCP server

> **This directory was `tools/ue5-mcp-bridge/` before 0.3.0.** It was renamed because the server is
> no longer UE5-only. A forwarding shim is left at the old path so existing `.mcp.json` files keep
> working — see [Upgrading from 0.2.0](../../README.md#upgrading-from-020--the-server-moved).

Thin [FastMCP](https://github.com/modelcontextprotocol/python-sdk) wrapper over the in-editor
**MifBridge** C++ plugin. It lets Claude build, wire, compile, and *read compiler errors back from*
Blueprint graphs in the custom UE 5.3.2 source build — closing the feedback loop that the
clipboard-paste workflow never had.

It is also the single front end for MifBridge's **second backend** — the `MifBlender` addon on
`127.0.0.1:8792` ([`../blender-addon/`](../blender-addon/)). Two transports, two choke-point
functions (`_post` for Unreal, `_blender` for Blender), no shared dispatch. The Blender half ships
as the `bl_*` tools plus the composing `mif_mesh_roundtrip`; `python ../parity_check.py` ties the
`_blender("...")` call sites to the addon's op table and to each op's accepted-param set, which is
the `bl_*` equivalent of the `MIF_DECL == MIF_BIND` compile-time tie.

Full design + endpoint reference: [`docs/05_DESIGN_SPEC.md`](../../docs/05_DESIGN_SPEC.md);
architecture and the add-an-endpoint checklist: [`docs/00_ARCHITECTURE.md`](../../docs/00_ARCHITECTURE.md).

```
                              ┌── HTTP POST 127.0.0.1:8791 ──► MifBridge (editor) ──► UnrealEd graph API
Claude ── MCP (stdio) ──► server.py
                              └── framed JSON 127.0.0.1:8792 ──► MifBlender (addon) ──► bpy / bmesh
                                  (bl_* tools; see ../blender-addon/)
```

## Prerequisites

1. **The editor is open** on your `.uproject` with the `MifBridge` plugin built and loaded.
2. **The bridge is listening** — it auto-starts on editor load; toggle it from **Tools ▸ Mif Bridge: Start/Stop**
   (the menu label shows the port when running). It binds `127.0.0.1:8791` and only accepts loopback callers.
3. **Python 3.10+** with the deps below.

```bash
pip install -r requirements.txt
```

## Configuration (environment)

| Variable | Default | Notes |
|---|---|---|
| `MIF_BRIDGE_URL` | `http://127.0.0.1:8791/api` | Bridge base URL. |
| `MIF_BRIDGE_TOKEN` | `dev` | Shared secret; **must match** the editor's `MIF_BRIDGE_TOKEN` env var (sent as the `X-Mif-Token` header). |
| `MIF_BRIDGE_TIMEOUT` | `30` | Per-request timeout (seconds). Raise it if a compile is slow. |
| `MIF_BRIDGE_DEBUG` | *(unset)* | `1`/`true` to log request/response to **stderr** (same as `--debug`). |
| `MIF_BLENDER_HOST` / `MIF_BLENDER_PORT` | `127.0.0.1` / `8792` | The `MifBlender` addon's socket. |
| `MIF_BLENDER_TOKEN` | falls back to `MIF_BRIDGE_TOKEN` | Must match the addon preference. |
| `MIF_BLENDER_CONNECT_TIMEOUT` | `3` | Reaching the socket at all. Sits just above the ~2 s Windows takes to report a refused loopback connect, so "Blender is not running" reports as *refused* rather than as a misleading connect timeout. |
| `MIF_BLENDER_PROBE_TIMEOUT` | `5` | `bl_status` only, and it bounds **two** waits: the read, and the wait for the transport lock. |
| `MIF_BLENDER_TIMEOUT` | `180` | Read timeout for every other Blender op. |

**The timeout ladder has an owner.** The addon's own main-thread job timeout is 150 s and must stay
**below** `MIF_BLENDER_TIMEOUT`, so Blender gives up first and the socket carries a real error. If
the MCP abandons first it drops the socket while Blender goes on mutating the scene for the
remainder of its own timeout, on behalf of a caller already told the op failed. Raise one, raise the
other.

**The Blender transport is one serialised socket.** A long op blocks the rest. `bl_status` (and
`mif_mesh_roundtrip`'s step-0 probe) therefore bound their wait for the lock as well as for the
read, and answer with *which* op holds the line and for how long — the tool you reach for when
Blender is wedged must not be the tool wedged Blender makes unavailable.

The editor reads `MIF_BRIDGE_TOKEN` (and optional `MIF_BRIDGE_PORT`) from its own process
environment at startup, so set the same token on both sides.

## Run

```bash
python server.py            # normal (stdio transport)
python server.py --debug    # + request/response tracing on stderr
```

`stdout` is reserved for the MCP JSON-RPC stream — all logging goes to `stderr`.

## Wire into Claude Code

Copy [`mcp.json.sample`](./mcp.json.sample) into your project-scoped `.mcp.json` (or your user
`~/.claude` config) and set the token to match the editor. **Do not commit real secrets.**

## Tools (one per bridge endpoint)

> **This list is a historical subset, kept for orientation — it is not the tool inventory.** It was
> written when there were 79 tools; there are now **224**. The server itself is the only accurate
> list. Get it with `grep -c '@mcp.tool()' server.py`, or check it against the plugin with the
> parity command in the [root README](../../README.md#endpoint--tool-parity-with-a-second-backend-in-the-picture).

- **Session/assets:** `open_blueprint`, `list_blueprints`, `save_blueprint`, `backup_blueprint`
- **Introspection:** `list_graphs`, `list_nodes`, `get_node`, `list_variables`, `list_functions`, `find_nodes`, `resolve_struct`
- **Variables:** `add_variable`, `rename_variable`, `remove_variable`, `set_variable_default`
- **Nodes:** `add_function_call`, `add_variable_get`, `add_variable_set`, `add_branch`, `add_macro_instance`,
  `add_get_array_item`, `add_override_event`, `add_parent_call`, `add_cast`, `add_custom_event`, `add_make_struct`,
  `add_break_struct`, `add_self`, `add_literal`, `create_function`, `move_node`, `remove_node`, `refresh_node`
- **Pins/wiring:** `connect_pins`, `disconnect_pin`, `reconnect_pin`, `set_pin_default`, `splice_into_exec`
- **Recipes:** `recipe_add_debug_print`, `recipe_reset_and_loop`, `recipe_override_and_call_parent`,
  `recipe_splice_before_parent`, `recipe_argmax_over_components`
- **Pipeline:** `read_modloader_log` (tail UE4SS.log), `trigger_cook` (plan only — executes nothing)
- **Timeline/switch/cast:** `add_timeline`, `add_class_cast`, `add_switch_enum`, `add_switch_int`, `add_switch_string`,
  `add_enum_literal`, `set_pin_type`
- **Event dispatchers:** `add_event_dispatcher`, `add_call_dispatcher`, `add_bind_dispatcher`, `list_dispatchers`
- **Components (SCS):** `add_component`, `list_components`, `remove_component`, `set_component_transform`
- **Interfaces:** `add_interface`, `remove_interface`, `list_interfaces`
- **DataTables:** `list_datatables`, `read_datatable`, `get_datatable_row`, `write_datatable_rows` (confirm=true)
- **Functions/interfaces:** `implement_interface_function`, `remove_function`
- **Common nodes:** `add_sequence`, `add_spawn_actor`, `add_get_subsystem`, `add_make_array`, `add_format_text`,
  `add_get_data_table_row`, `add_comment`
- **Compile:** `compile_blueprint`, `validate_blueprint`
- **Batch:** `batch` — many ops in one transaction + one compile

> `add_variable`/`create_function`/`set_pin_type` accept ref-type prefixes: `class:X`, `object:X`, `softobject:X`,
> `softclass:X`, `interface:X`, `enum:X` (plus bare scalar/struct names). Event params (`add_custom_event inputs`,
> `add_event_dispatcher inputs`) are `[{name, type, container?}]`.

> `recipe_add_debug_print` targets a self-local `PrintToModLoader(Message)` (created on the fly if missing),
> **not** `PrintString` — which is `DevelopmentOnly` and stripped from the shipped game.

Every tool returns the bridge's JSON verbatim. Mutations return the resulting node/pin/variable
state (never a bare `ok:true`); `compile`/`validate` return `{ok, numErrors, numWarnings, messages:[…]}`
with each message mapped to its `nodeGuid`/`pinName`.

### Identifiers

- `blueprintId` — the asset object path (e.g. `/Game/Foo/BP_Bar.BP_Bar`), returned by `open_blueprint`.
- `graphId` — `<blueprintPath>::<graphName>`, returned by `list_graphs` / `open_blueprint`.
- `nodeGuid` — the engine-assigned node GUID, returned by every node-creating tool and by `list_nodes`.

### Destructive ops

`remove_node`, `remove_variable`, and `rename_variable` require `confirm=true`. Every mutation is
wrapped in a transaction, so **Ctrl-Z in the editor** undoes any bridge action.

## Quick smoke test

```bash
# with the editor open + bridge running:
curl -s -X POST http://127.0.0.1:8791/api/list_blueprints \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d '{"filter":"BP_"}'
```
