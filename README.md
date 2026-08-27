<div align="center">

# 🌉 MifBridge

### **Let an AI edit your Unreal Blueprints — and read the compiler errors back.**

<!-- MIFBRIDGE-VERSION-LINE -->
`v0.6.0` &nbsp;·&nbsp; 🎮 **UE 5.3 + 5.7** &nbsp;·&nbsp; 🎨 **Blender 3.6–5.0** &nbsp;·&nbsp; 🔌 **320 endpoints** &nbsp;·&nbsp; 🧰 **353 MCP tools** &nbsp;·&nbsp; 🧪 **75 test suites**

</div>

---

MifBridge is an in‑editor Unreal Engine plugin plus a Model Context Protocol (MCP) server that lets an AI assistant **build, wire, and compile Blueprint graphs programmatically, then read the actual compiler output**. It replaces the blind "AI writes T3D → you paste → you screenshot the errors → AI guesses → repeat" loop with a direct, closed feedback loop.

Every change goes through Unreal's own graph API (`Schema->TryCreateConnection`, `ReconstructNode`, `FKismetEditorUtilities::CompileBlueprint`), so it fires the pin/notification callbacks that clipboard paste skips — the ones that resolve wildcard pins, relink variables, and expand macros. Every edit is wrapped in a transaction, so **Ctrl‑Z in the editor undoes anything the AI did.**

### ✨ Why this one is different

| | |
|---|---|
| 🔁 **It closes the loop** | The compiler's own message list comes back mapped to node GUID + pin name. The AI reads the exact error, not a screenshot. |
| 🧵 **Real engine callbacks** | Wildcard pins resolve, macros expand, variables relink — because it drives `UnrealEd`, not a clipboard. |
| ↩️ **Undo‑safe by construction** | One transaction per mutation. Ctrl‑Z is the escape hatch, always. |
| 🧊 **Cooked projects are first‑class** | Cooked Blueprints have no graphs; MifBridge *says so* and names the route out instead of returning an empty answer. |
| 🎚️ **A safety gate you can see** | `read` / `scratch` / `full` write modes, switchable from the in‑editor panel. |
| 🧱 **Two DCCs, one agent** | Unreal over HTTP **and** Blender over a local socket — the mesh round trip neither tool can do alone. |

### 🧭 Honest status

| Half | State |
|---|---|
| 🎮 **UE plugin + MCP server** | **Mature.** 320 endpoints, 75 suites. Last full pass: 148 runs across 74 suites green, 0 editor deaths. |
| 🎨 **Blender addon** | **Working, version‑tested.** 20 ops and a real mesh round trip, green on Blender 3.6.23, 4.2.17 LTS, 4.4.0 and 5.0.1 — 89 assertions per version. The five `gen_*` ops that need an external service are declared in the suite output rather than skipped silently. |

---

## ⚙️ How it works

```
                     Claude Code (MCP client)
                              │  MCP tool call (stdio JSON-RPC)
                              ▼
              ┌───────────────────────────────────────┐
              │  server.py   —  FastMCP("mif-bridge") │
              │  one tool per backend operation       │
              └───────┬───────────────────────┬───────┘
                      │                       │
   HTTP POST          │                       │   framed JSON over TCP
   127.0.0.1:8791/api │                       │   127.0.0.1:8792
   header X-Mif-Token │                       │   header token, framed JSON
                      ▼                       ▼
        ┌──────────────────────────┐   ┌──────────────────────┐
        │ MifBridge                │   │ MifBlender           │
        │ UE editor plugin (C++)   │   │ Blender addon (py)   │
        │ token → loopback →       │   │ token → main-thread  │
        │ game-thread → transaction│   │ job queue            │
        └────────────┬─────────────┘   └──────────┬───────────┘
                     ▼                            ▼
        UnrealEd graph / asset API           bpy + bmesh
        → the live Blueprint in your         → the live scene in your
          open editor                          open Blender
```

Two transports, two choke-point functions in `server.py`, **no shared dispatch** — a change to one
backend cannot break the other. Unprefixed tools go to Unreal (it is the default backend and
renaming 320 of them would break every existing workflow); `bl_*` tools go to Blender; `mif_*` tools
are the only ones allowed to contain logic, because they orchestrate both.

The UE plugin answers each request on the game thread, applies it through the real editor API, compiles, and returns the **structured compiler message list mapped to node GUID + pin name** — so the AI reads the exact error instead of a screenshot.

---

## 📦 Requirements

| For | You need |
|---|---|
| 🎮 **UE plugin** | **Unreal Engine 5.3 or 5.7** — every change is compiled against *both*. Editor‑only C++, so it must be built against the engine you actually run; a marketplace prebuilt will not ABI‑match a source build. Win64. |
| 🐍 **MCP server** | **Python 3.10+**, with `mcp>=1.2.0` and `requests>=2.31.0`. Any OS — it only speaks loopback. |
| 🎨 **Blender addon** | **Blender 4.4+** — the `bl_info` floor. Verified green on **3.6, 4.2, 4.4 and 5.0**; see `tools/blender-addon/README.md` for the matrix. Shipped as a zip; optional. |
| 🤖 **Client** | Claude Code, or anything that speaks MCP over stdio. |

> 💡 **You do not need all three.** The MCP server + UE plugin is a complete, useful install on its
> own — that is what every release before 0.3.0 was, and an install with Blender never started
> behaves exactly that way.

---

## 🧰 What's in the box

| Part | What it is | Where it lives in this repo | Where it gets installed |
|---|---|---|---|
| **MifBridge** (UE plugin) | The in‑editor HTTP bridge (C++) | `Source/`, `MifBridge.uplugin` — **the repo root is the plugin** | `<YourProject>/Plugins/MifBridge/` |
| **server.py** (MCP server) | FastMCP wrapper, 1 tool per operation, 2 backends | `tools/mcp-server/` | stays where it is; referenced by path from `.mcp.json` |
| **MifBlender** (Blender addon) | Loopback socket server inside Blender | `tools/blender-addon/` | Blender's addons dir (as a `.zip`) |

### 🗂️ Why the repo root is the Unreal plugin, and not a tidy three-way split

Unreal finds a plugin by locating a `.uplugin` at the **root of the plugin folder**. Moving the
plugin into `unreal-plugin/` to sit symmetrically beside `blender-addon/` would mean nobody can
`git clone` this into `Plugins/` any more — every user would have to clone somewhere else and copy a
subdirectory in, and every existing install would break. Symmetry is not worth that.

So the layout is asymmetric on purpose: **root = the UE plugin, `tools/` = everything that is not
Unreal.** Unreal never looks at `tools/`, `docs/` or `.github/` — there is no field in a `.uplugin`
descriptor that lists or excludes directories, and the module is `"Type": "Editor"`, so none of it
can reach a cooked build either.

All three halves stay in **one** repo for the reason the split was abandoned in the first place:
every UE endpoint needs a `MIF_DECL` + `MIF_BIND` in the C++ **and** a matching `@mcp.tool` in
`server.py`, and when those lived in separate repos they drifted silently. One repo, one commit, no
drift.

### 🔗 Endpoint ↔ tool parity, with a second backend in the picture

The 1:1 rule needs a scope clause, or the `bl_*` tools read as a pile of violations.
**Restated:** the set of UE endpoints and the set of endpoint strings passed to `_post()` must be
identical. Tools that call `_blender()` are outside *that* set — they own no C++ endpoint — but they
have their own, equally binding parity set: the `_blender("...")` literals must equal the `MifBlender`
addon's `OPS` keys, and every key a call site sends must be in that op's `reject_unknown` set.
`mif_*` tools compose and own nothing on either backend.

**One command checks all of it, both backends:**

```bash
python tools/parity_check.py          # exit 0 clean, 1 on any drift
```

It parses both sides with `ast` (no editor, no Blender, no `fastmcp` needed), fails closed on
anything it cannot resolve statically, and prints its exemption list on every run. It is not
optional garnish: before it existed, three `bl_*` tools called addon ops that did not exist and the
one shared op was called with two params it refuses — the whole Blender round trip was dead on
arrival and every layer above reported `ok: true`.

The underlying shell recipe, for the UE half only:

```bash
sed -n 's/.*MIF_BIND(\([a-z_0-9]*\)).*/\1/p' Source/MifBridge/Private/MifBridgeCommon.cpp | sort -u > /tmp/plugin.txt
sed -n 's/.*_post("\([a-z_0-9]*\)".*/\1/p'   tools/mcp-server/server.py                   | sort -u > /tmp/mcp.txt
comm -23 /tmp/plugin.txt /tmp/mcp.txt   # endpoints with no tool
comm -13 /tmp/plugin.txt /tmp/mcp.txt   # tools with no endpoint -> the 12 kr_* externals
```

Measured on the current tree: **320 built-in endpoints + 12 external = 332**, against **353 tools**
(320 that reach Unreal, 12 `kr_*`, 20 `bl_*`, 1 `mif_*`). Both columns of the diff are now EMPTY —
`parity_check` reports no drift and no exempted gaps:

- ✅ **0 endpoints with no tool.** There used to be five — `set_variable_type`,
  `retarget_variable_node`, `add_component_bound_event`, `set_cast_purity`, `reparent_blueprint` —
  reachable over HTTP and invisible to an MCP client. All five now have tools.
- ✅ **12 tools with no `MIF_BIND`** — the `kr_*` set, registered at runtime by the separate
  `MifKismetReconstructor` plugin. By design, and the only exemption.

Anything else in either column is real drift. `self_audit` reports the live count from the running
DLL and is the authority over any number written here.

**Four more audits run beside it**, each of which found real defects the day it was written:

| Command | Catches |
|---|---|
| `python tools/spec_check.py` | a spec item ticked while its own body still says "not built" |
| `python tools/audit_message_endpoints.py` | an error message telling you to call an endpoint that does not exist |
| `python tools/audit_dead_params.py` | a parameter the endpoint *accepts* and nothing ever reads |
| `python tools/audit_vacuous_checks.py` | a test assertion that passes no matter what the code does |

---

## 🚀 Install

Three installables, three sections. Do them in this order; each one is useful without the next.

### 1️⃣ The Unreal plugin → `<YourProject>/Plugins/`

1. Put this repo at `<YourProject>/Plugins/MifBridge/` — clone it there, or copy the folder in. You
   need **`Source/`** and the **`.uplugin`**; do **not** copy a prebuilt `Binaries/`/`Intermediate/`
   from a different engine (they are gitignored for exactly this reason).
2. The `.uplugin` is `"EnabledByDefault": true`, so no project‑file regeneration is needed.
3. Build your project's **Editor** target, with the editor **closed**:
   ```
   <Engine>\Build\BatchFiles\Build.bat <YourProject>Editor Win64 Development -Project="<...>.uproject" -WaitMutex
   ```
   This produces `Plugins/MifBridge/Binaries/Win64/UnrealEditor-MifBridge.dll`.
4. **Open the editor.** MifBridge auto‑starts and binds `127.0.0.1:8791`. Toggle it any time from
   **Tools ▸ Mif Bridge: Start / Stop** (the menu label shows the live port).
5. The editor reads two env vars from its own process environment at startup:
   **`MIF_BRIDGE_TOKEN`** (default `dev`) and **`MIF_BRIDGE_PORT`** (default `8791`).
6. Smoke test, with the editor open:
   ```bash
   curl -s -X POST http://127.0.0.1:8791/api/list_blueprints \
     -H "X-Mif-Token: dev" -H "Content-Type: application/json" -d '{"filter":"BP_"}'
   ```

### 2️⃣ The Blender addon → Blender's add-ons directory

**Optional.** Skip it and everything else still works; the `bl_*` and `mif_*` tools simply report
that the backend is unreachable. Full detail in [`tools/blender-addon/README.md`](tools/blender-addon/README.md).

1. Build the addon zip: `python tools/blender-addon/build_zip.py` → `tools/blender-addon/dist/MifBlender.zip`.
2. In Blender: **Edit ▸ Preferences ▸ Add-ons ▸ Install…**, pick the zip, then tick **MifBlender** to
   enable it. (Or symlink `tools/blender-addon/MifBlender/` into
   `%APPDATA%/Blender Foundation/Blender/4.4/scripts/addons/` if you want to edit it in place.)
   **Blender 4.4 is the declared floor**, and it is conservative rather than untested: the addon is
   verified green on 3.6.23, 4.2.17 LTS, 4.4.0 and 5.0.1 — 41/41 mesh and 12/12 op assertions on
   each. Reproduce with `python tools/run_blender_suites.py`. **Upgrading to 5.0 is safe.**

### 🔌 Port allocation — read this before pointing anything at a new port

| Port | Owner | Notes |
|---|---|---|
| `8791` | **MifBridge (UE)** — first editor | The default. `MIF_BRIDGE_PORT` overrides it. |
| `8792` | **MifBlender addon** — reserved | Do **not** reuse. Changing it means keeping the addon preference *and* `MIF_BLENDER_PORT` in sync — two moving parts. |
| `8793`–`8799` | leave clear | Headroom for the pair above. |
| `8801`+ | **additional UE editors** | Use these when running a second project's editor. |
| `9876` | third-party `blender-mcp` | Not ours. Avoid so it can stay installed alongside. |

**Why this table exists.** On 2026-08-26 a second editor was pointed at `8792` to dodge the first
one already holding `8791`. `8792` is MifBlender's. Blender was pushed onto `8793`, and the Blender
tools would have dialled `8792`, reached a UE HTTP bridge, and spoken a length-prefixed binary
protocol at it. The port is open and something answers, so the two checks anyone would run both
pass while nothing works. Nothing warned, because the allocation existed only as three scattered
defaults rather than as a map. Full write-up: `docs/06_OPEN_ISSUES_FROM_USE.md` issue 15.

MifBridge now logs a warning at startup if it is configured for `8792` or `9876`. It only warns —
a deliberate override is legitimate, and a bridge that refuses to boot is a worse problem than one
on an awkward port. The point is that the collision is no longer silent.

**Running two editors at once?** Give the second one `MIF_BRIDGE_PORT=8801`, not `8792`.
3. The addon binds **`127.0.0.1:8792`** — loopback only, no `0.0.0.0` option. Port 8792 is chosen to
   sit next to the UE bridge on 8791 and to *avoid* 9876, so the third-party `blender-mcp` addon can
   stay installed alongside it without a bind clash.
4. Check **N‑panel ▸ MifBridge ▸ Status** for the live port and the token state.
5. Set the same shared secret Unreal uses. The MCP server reads these:

   | Variable | Default | Notes |
   |---|---|---|
   | `MIF_BLENDER_HOST` | `127.0.0.1` | |
   | `MIF_BLENDER_PORT` | `8792` | Not 9876 — that is the third-party `blender-mcp` addon. |
   | `MIF_BLENDER_TOKEN` | *falls back to* `MIF_BRIDGE_TOKEN` | One secret for both backends unless you want two. |
   | `MIF_BLENDER_CONNECT_TIMEOUT` | `3` | A closed Blender fails in 3 s instead of hanging. |
   | `MIF_BLENDER_PROBE_TIMEOUT` | `5` | `bl_status` only — the cheap "is it alive" call. |
   | `MIF_BLENDER_TIMEOUT` | `180` | Per-op; geometry work is slow. |

   **Blender being closed is never an error you have to debug.** The server connects lazily on the
   first `bl_*` call, never at startup, and returns `{"ok": false, "error": …}` naming the fix.

### 3️⃣ The MCP server → deps + `mcp.json`

```bash
cd tools/mcp-server
pip install -r requirements.txt
```

Copy [`tools/mcp-server/mcp.json.sample`](tools/mcp-server/mcp.json.sample) into your project‑scoped
`.mcp.json` (or your user `~/.claude` config) and set a token that matches the editor:

```json
{
  "mcpServers": {
    "mif-bridge": {
      "command": "python",
      "args": ["<YourProject>/Plugins/MifBridge/tools/mcp-server/server.py"],
      "env": {
        "MIF_BRIDGE_URL": "http://127.0.0.1:8791/api",
        "MIF_BRIDGE_TOKEN": "change-me-to-match-the-editor"
      }
    }
  }
}
```

Or run it by hand:

```bash
python server.py           # stdio transport
python server.py --debug   # + request/response tracing on stderr
```

Server env vars for the Unreal backend: `MIF_BRIDGE_URL` (default `http://127.0.0.1:8791/api`),
`MIF_BRIDGE_TOKEN` (default `dev`), `MIF_BRIDGE_TIMEOUT` (default `30`s), `MIF_BRIDGE_DEBUG`.
Blender's are in §2 above.

> ### Upgrading from 0.2.0 — the server moved
>
> `tools/ue5-mcp-bridge/` is now **`tools/mcp-server/`**. It was renamed because the server is no
> longer UE5-only. **If your `.mcp.json` names the old path it keeps working**: a small shim is left
> at `tools/ue5-mcp-bridge/server.py` that forwards to the new location and prints a deprecation
> notice to *stderr* (never stdout — that carries the JSON-RPC stream). Update the `args` path when
> convenient; the shim will be removed in a later release.
>
> The sample also renames the server key `mif-ue5` → `mif-bridge`. That key is arbitrary and
> client-local — it only sets the prefix your client shows on tool names. Keep `mif-ue5` if you have
> workflows that reference it; nothing on the wire depends on it.

---

## 🔒 Security

MifBridge lets a local process **modify your project**, so it is locked down to a single dev machine:

- **Loopback only.** Non‑loopback callers are rejected in‑handler (127.*/::1). The port must never be exposed off‑box. The Blender backend is designed the same way — bind address is not configurable.
- **Shared‑secret token.** Every request must carry `X-Mif-Token` equal to the editor's token. **Change `MIF_BRIDGE_TOKEN` from the `dev` default** on both the editor and the server before using it for anything real, and don't commit the secret.
- **Undo‑safe.** Every mutation is a transaction — Ctrl‑Z reverts it. *(Blender has no equivalent guarantee; a failed geometry op deliberately leaves the broken object in the scene as the debugging artifact.)*
- **Confirm‑gated destruction.** Deleting nodes/variables/functions/components/interfaces, deleting or renaming a whole asset, or writing DataTable rows all require an explicit `confirm=true`.
- **Editor‑only.** The module never cooks into a shipped build.
- **No arbitrary code execution by default.** The Blender addon's design keeps a `run_script`-style op out of the default op table entirely; if it is ever added it sits behind a preference that is off by default.

---

## 🧠 Capabilities — 332 HTTP endpoints (320 built‑in + 12 external)

> The authoritative list is whatever `self_audit` reports from the running editor, never this
> section. `tools/endpoints_current.json` is a snapshot of it, and `tools/parity_check.py`
> enforces that every endpoint has both a `MIF_BIND` and an `@mcp.tool`.

- **Session / assets** — open, list, save, back up Blueprints; create new Blueprints (incl. function libraries, interfaces, macro libraries, widget blueprints); delete, rename, or duplicate any `/Game/` asset.
- **Introspection** — list graphs/nodes/variables/functions, get a node's full pin detail, find nodes by class/title/function, resolve structs, describe a class's callable functions/properties/dispatchers, list enum values. Graph enumeration recurses into **nested** graphs — anim state machines, their states, transition rules, and collapsed/composite node bodies — which are addressed by a dotted `graphId` (`…::AnimGraph.Locomotion.Idle`).
- **Animation assets** — `list_animations` (asset‑registry only, never loads) and `describe_animation`: notifies (including notify‑*state* windows and branching points), curves, sync markers, montage sections/slots, blend‑space axes and samples.
- **Generic reflection** — read or write any `UObject`'s properties by dot-path (`get_property`/`set_property`) or dump every top-level property on an object (`list_object_properties`) — the same mechanism the Details panel uses, so it covers non-Blueprint assets too (DataAssets, `InputMappingContext`, `InputAction`, …).
- **Variables** — add / rename / remove / set‑default (member or local; array & set containers; object/class/soft/interface/enum types). `set_variable_flags` covers the whole Details‑panel flag set on member variables — **Replicated / RepNotify (auto‑creating the `OnRep_` graph) / replication condition**, **SaveGame**, transient, config, instance‑editable, blueprint‑read‑only, expose‑on‑spawn, advanced‑display, interp, deprecated, category, tooltip — and the same keys work inline on `add_variable`. `list_variables` reports the current flags back. *Map containers aren't supported.*
- **Nodes** — function calls, variable get/set, branch, macro instances (e.g. ForEachLoop), get‑array‑item, override events, parent calls, casts, custom events, make/break struct, self, literals, sequence, spawn actor, get subsystem, make array, make map, format text, get datatable row, comment, timeline, switch (enum/int/string), enum literal, create widget.
- **Pins / wiring** — connect, disconnect, reconnect, set pin default, set pin type, splice into an exec chain, `remove_pin` (user‑defined parameter pins, and duplicate‑pin repair).
- **Functions / events / interfaces / components / dispatchers / datatables** — create/implement/remove functions, add/rename/remove event dispatchers + call/bind, add/remove/list interfaces, add/list/remove SCS components + transforms, read datatables & write rows.
- **Widget Blueprints** — toggle Is‑Variable, add/remove widget‑tree data bindings (`add_widget_binding`/`remove_widget_binding`), add/remove tree widgets (`add_tree_widget`/`remove_tree_widget`).
- **Cooked‑BP reconstruction** — mint a persistent editable child/sibling of a cooked Blueprint (`create_editable_child`), optionally reconstructing its whole Blueprint‑parent chain into editable siblings too (`variant: "full"`) instead of leaving the parent layer as cooked stubs.
- **Compile / diagnostics** — `compile` and `validate` return `{numErrors, numWarnings, messages:[{severity, text, nodeGuid, pinName}]}`.
- **Batch & recipes** — run many ops with one final compile; higher‑level recipes (debug‑print splice, reset‑and‑loop, override‑and‑call‑parent, argmax‑over‑components).
- **Pipeline hooks** — tail the UE4SS mod‑loader log; a plan‑only cook helper.
- **Material graphs** — create materials and material functions; add and wire expressions
  (`add_material_expression`, `connect_material_expressions`, `connect_material_property`), lay them
  out, recompile, and poll shader compilation. `list_material_parameters` works on a **cooked** game
  because `FMaterialCachedParameters` survives cook — the expression graph does not, and the graph
  endpoints refuse rather than crash on a cooked material.
- **Landscape** — create, sculpt, paint layers, bind a runtime virtual texture, and two diagnostics
  (`diagnose_landscape`, `diagnose_landscape_draws`) for when terrain renders wrongly.
- **Level authoring** — spawn one actor or many (`spawn_many`), duplicate, transform, label, folder,
  delete, select; `snap_actors_to_ground`; splines; `get_actor_bounds` and `check_overlaps` for
  whole-scene audits. `add_foliage_instances` places either a standalone instanced-mesh actor or, with
  `foliageType`, real foliage in the level's `InstancedFoliageActor` so it inherits that type's cull
  distance and density.
- **Viewport and capture** — drive the user's camera (`set_viewport_camera`, `focus_viewport`),
  `capture_camera` for a shot from an arbitrary point, and `capture_viewport` for what the editor is
  **actually** drawing. Those are different cameras and the responses say which.
- **Navigation and AI** — nav bounds volumes, async navmesh build, `nav_project_point`,
  `nav_find_path` (a partial path is never reported as reachable), patrol setup, and read-only
  Behavior Tree / Blackboard introspection.
- **Textures, thumbnails, import/export** — `import_texture` from a file or base64, `import_asset`,
  `reimport_asset`, `set_texture_settings`; render and write asset thumbnails; `export_asset` for the
  outbound half of the Blender round trip.
- **UMG animation** — create animations, bind a widget and add a property track, key it in **seconds**
  (converted to MovieScene tick space for you), rename a widget and carry the name through all five
  places that store it, and change an existing animation's range in place with
  `set_widget_animation_range`.
- **IK Rig and retargeting** — build an IK Rig from a mesh, set the retarget root, author chains,
  add goals and solvers, wire goals to solvers, and auto-map chains between a source and target rig by
  exact or fuzzy name. `list_ik_rig` validates rather than echoes, and asks the engine's own processor
  whether the rig would actually initialise. UE5-only, and compiled conditionally so the plugin still
  loads without the IKRig plugin.
- **Skeletons** — `list_bones` gives the hierarchy of a Skeleton or SkeletalMesh with parents and
  depth, and says WHICH reference skeleton it read, because a mesh and its skeleton can differ.
- **Niagara and audio** — `list_niagara_user_parameters` reads a system's User parameters with their
  VALUES, which no composition of the generic reflection endpoints can reach; `audition_sound` previews
  any `USoundBase`.
- **Collision** — collision profiles, per-component collision, and simplified collision generation.
- **Undo and dirty state** — inspect the transaction stack, undo/redo by count, list dirty packages.
  Note that a cancelled transaction does NOT roll back (see PM-007) — this is introspection, not a
  safety net.
- **Sublevels and PIE** — sublevel composition, streaming and visibility, PIE control and runtime
  observation.
- **Struct and enum authoring** — create user-defined structs and enums, add and edit members, and the
  make/break/switch nodes that consume them.
- **Water** — `list_water_bodies`, `describe_water_body`, `create_water_body`, `set_water_body_spline`,
  and `create_water_zone`. The zone matters more than it sounds: since UE 5.1 a body overlapping **no**
  `AWaterZone` does not render at all, so the write half could author water nobody could see until the
  zone endpoint existed. It reports `bodiesNowCovered` and **names** the bodies still invisible.
- **MetaSounds** — `describe_metasound` reports a MetaSound's *interface* (the inputs you set to drive
  it) plus node/edge counts. Read **reflectively**, so it includes no Metasound header, works on an
  engine without the plugin, and never touches the engine's `*Checked` document accessors — those
  hard‑assert rather than returning null.
- **PCG** — list and describe graphs, list components, generate and clean up.
- **StateTree, Gameplay Tags, Game Features, Live Coding** — read surfaces for each, plus
  `live_coding_status`, which answers the question that has cost this project real time: *is something
  holding the editor's DLLs so an external build will silently do nothing?*
- **Sequencer** — `list_level_sequences` / `describe_level_sequence` to read, and the write half:
  `list_sequence_bindings`, `add_sequence_possessable`, `add_sequence_track`.
- **World Partition data layers** — create, list, describe, add/remove actors, and toggle
  loaded/visible in the editor.

---

## 📄 License

**MIT** — see [`LICENSE`](LICENSE). MifBridge is entirely original code and does
not include or link any GPL-licensed source, so you're free to use, modify, and
redistribute it under the permissive MIT terms.

It does link Unreal Engine at build time (the engine is covered by Epic's Unreal
Engine EULA, not this license), and its `create_editable_child` endpoint calls an
engine-side function from a cooked-editor engine fork — so that one endpoint needs
the fork to build. At runtime only, it cooperates with the separate
**MifKismetReconstructor** plugin (GPL-3.0) through an engine-provided delegate;
that plugin is distributed separately and is not part of this MIT work.

### 🙏 Third-party credits

The Blender backend's socket framing (4‑byte big‑endian length prefix + UTF‑8 JSON) and its
main‑thread job‑marshalling pattern are **adapted from
[blender-mcp](https://github.com/MCPBlender/blender-mcp)** by **Siddharth Ahuja**, MIT licensed,
© 2025. MifBridge is MIT too, so the adaptation is clean; the credit stays in the addon source
headers as well as here.

Nothing else travels from that project — no telemetry, no secret store, no third‑party service
integrations or API keys, and none of its terms documents.

Full third-party notices, including the Unreal Engine EULA boundary and the GPL-3.0
`MifKismetReconstructor` separation, are in [`NOTICE.md`](NOTICE.md).

---

## 📚 Docs

- [`docs/02_GOTCHAS.md`](docs/02_GOTCHAS.md) — **parameter grammar and traps.** Accepted spellings
  for node/class/pin parameters, the type grammar (including the `object:`/`class:`/`enum:` prefixes),
  variable‑flag semantics, and cooked‑Blueprint behaviour. Read this before spending a probe.
- [`docs/01_POSTMORTEMS.md`](docs/01_POSTMORTEMS.md) — symptom → root cause → fix → prevention for
  every bug that cost real time.
- [`docs/00_ARCHITECTURE.md`](docs/00_ARCHITECTURE.md) — source layout, the transaction policy, and
  the add‑an‑endpoint checklist.

> **Note on older docs.** Anything under `docs/audit/` is a dated record of what was true when it was
> written, and is deliberately left unedited — so it still says `tools/ue5-mcp-bridge/` and quotes
> endpoint counts from 79 up to 211. Treat those paths and numbers as history, not instructions.

### ⚡ The short version

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
