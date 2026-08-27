# MifBlender — the Blender backend of MifBridge

MifBridge is one MCP server with two backends:

```
                  ┌──────────────────────────────────────────────┐
  agent ──MCP──▶  │  tools/mcp-server/server.py                  │
   (stdio)        │                                              │
                  │   _post(...)     ──HTTP─────▶ 127.0.0.1:8791  UE 5.3 C++ plugin
                  │   _blender(...)  ──TCP──────▶ 127.0.0.1:8792  ◀── THIS ADDON
                  └──────────────────────────────────────────────┘
```

Same response contract on both sides: `{"ok": true, ...}` / `{"ok": false, "error": "..."}`.

**Port `8792`.** Loopback only — there is deliberately no bind-address setting.
(The UE plugin owns 8791. The unrelated third-party `blender-mcp` addon owns 9876,
so both can run at once.)

> **A second Blender cannot silently steal the port.** On Windows the socket is bound with
> `SO_EXCLUSIVEADDRUSE`, not `SO_REUSEADDR`. The two are not equivalents: VERIFIED on this
> box (Python 3.11 / win32), two sockets with `SO_REUSEADDR` **both** bound `127.0.0.1:8792`
> and **both** `listen()`ed without error — so two Blender windows with the addon
> auto-starting would both log "listening", both show green in the N-panel, and the MCP
> would reach a nondeterministic one. *"My edits went to the other Blender"* is a whole day.
> With `SO_EXCLUSIVEADDRUSE` the second bind raises `WSAEADDRINUSE (10048)` and the addon
> refuses to start with a message naming the likely cause. Also VERIFIED: this does **not**
> break stop-then-start — rebinding immediately after a server-side connection close (the
> `TIME_WAIT` worry) succeeded. POSIX keeps `SO_REUSEADDR`, where it already refuses a second
> bind of a listening address.
>
> The owning process's **pid** is on the N-panel status line, in the preferences box, in the
> console banner, and in `ping`/`bl_status`. If those numbers disagree, this Blender is not
> the one your edits are landing in.

## 🧪 Blender version support — measured, not assumed

`bl_info` declares a floor of **4.4**. That floor is now *conservative* rather than unverified:

| version | imports | registers | 20 ops | FBX kwargs | mesh suite | ops suite |
|---|---|---|---|---|---|---|
| 3.6.23 | ✅ | ✅ | ✅ | all present | **77/77** | **12/12** |
| 4.2.17 LTS | ✅ | ✅ | ✅ | all present | **77/77** | **12/12** |
| 4.4.0 | ✅ | ✅ | ✅ | all present | **77/77** | **12/12** |
| **5.0.1** | ✅ | ✅ | ✅ | all present | **77/77** | **12/12** |

Reproduce the whole thing in one command each:

```bash
python ../blender_probe.py          # imports, registers, FBX kwargs, bmesh ops, legacy format
python ../run_blender_suites.py     # every suite against every installed Blender
```

**The worry this section used to carry was real and did not bite.** The FBX exporter's properties
genuinely do move between releases — `use_ascii` vanished in 4.4 — but that is one this addon never
passes. All 17 kwargs in `FBX_EXPORT_ARGS`, all 3 in `FBX_IMPORT_ARGS`, the four enum values
(`FBX_SCALE_NONE` / `FACE` / `SRGB` / `AUTO`) and all six `bmesh.ops` are still real on 5.0.1.

**Legacy add-ons are still fine on 5.0.** Blender 5.0.1 ships 25 `bl_info` add-ons of its own and
still has `addon_utils.enable`, so no `blender_manifest.toml` is required. Determined by running it,
not by reading release notes.

**Why the floor stays at 4.4 anyway** — a decision, not an oversight. The suites cover the op surface
and a Blender-side export→import round trip; they do **not** cover the full Unreal → Blender → Unreal
loop, whose last leg needs an Unreal write. The kwargs are proven present on 3.6; the round trip
*through* them is not proven there. Lower it when something exercises it.

---

## Install

1. Build the zip: `python build_zip.py` → `dist/MifBlender.zip`. (It refuses to build a
   package with no `__init__.py`, and verifies the archive has exactly one top-level item —
   `MifBlender/` — because a loose-at-the-root or double-nested zip installs to the wrong
   place and the addon silently never appears in the Add-ons list. Zip it by hand if you
   prefer; keep that layout.) `dist/` is gitignored.
2. Blender → **Edit ▸ Preferences ▸ Add-ons ▸ Install…**, pick the zip, tick
   **MifBlender (MifBridge backend)**.
3. Set the **Token** in the addon preferences to match `MIF_BLENDER_TOKEN` on the MCP
   server (which itself falls back to `MIF_BRIDGE_TOKEN`; both default to `dev`).
4. The server auto-starts. Status, Start and Stop live in the 3D viewport **N-panel ▸
   MifBridge** tab, which also prints this Blender's **pid**.

> **Main-thread job timeout: 150 s, and it must stay BELOW the MCP's work timeout**
> (`MIF_BLENDER_TIMEOUT`, default 180 s). Whichever end gives up first owns the failure, and
> it should be this one, so the socket carries a real error. Inverted — which it was, at
> 600 s against 180 s — the MCP abandons the call, drops the socket, and Blender goes on
> mutating the scene for another seven minutes on behalf of a caller that has already been
> told the op failed. Raise one and you must raise the other, keeping this one lower.

During development you can skip the zip and symlink instead:

```powershell
New-Item -ItemType SymbolicLink `
  -Path   "$env:APPDATA\Blender Foundation\Blender\4.4\scripts\addons\MifBlender" `
  -Target "D:\DDS2SDK\Game\Plugins\MifBridge\tools\blender-addon\MifBlender"
```

### Headless

The addon does **not** auto-start under `blender -b`, and that is not an oversight: in
background mode there is no event loop, the server has to own the main thread, and
auto-starting from `register()` would block startup forever with no way to stop it.
Start it explicitly:

```bash
blender -b --python-expr "import MifBlender; MifBlender.serve_forever()"
```

---

## Protocol

One JSON object per frame, framed as `[4-byte big-endian uint32 length][UTF-8 JSON]`,
64 MiB cap. The connection is persistent: request → response, ping-pong, in order.

```jsonc
// request
{ "endpoint": "object_info", "token": "dev", "params": { "object": "SM_Road_Dirt_Wide" } }

// success — op fields are flat, not nested under a "result" key
{ "ok": true, "endpoint": "object_info", "object": { ... }, "elapsedMs": 3.1 }

// failure
{ "ok": false, "endpoint": "object_info", "error": "no object named '...'. Present: ..." }
```

`endpoint` also accepts the aliases `op` and `type`. An unknown *param* is a hard error,
not a silent ignore — a misspelled key tells you so instead of quietly doing nothing.

---

## Ops

| Op | Params (aliases in brackets) | Notes |
|---|---|---|
| `ping` | `echo` | Version, **pid**, background flag, op list, whether `run_python` is allowed. Call it first — it is the cheap liveness probe. The pid identifies *which* Blender owns the port; compare it against the N-panel. |
| `scene_info` | `detail` | Census plus `unitSettings`. **Warns when `scaleLength` is not 1.0** — see the unit note below; that one silently rescales every export. |
| `list_objects` | `type`, `pattern`, `detail` | Names, types, and for meshes vert/tri counts, dimensions, material slots. |
| `object_info` | `object` [`name`] | Local bounds in **both** BU and UU, transform, counts, material slots, UV layers, custom-normals flag. This is the pre-image a round-trip asserts against. |
| `import_mesh` | `file` [`filepath`,`path`], `clearScene`, `rename` | FBX only. Captures new objects by set difference (the import operator returns none). Warns on a non-identity transform. |
| `export_mesh` | `object`/`objects`, `file`, `overwrite`, `meshSmoothType`, `useTriangles`, `useMeshModifiers`, `useTspace` | FBX only. Re-stats the file after writing and fails if it is missing or 0 bytes. Restores your selection. |
| `select_edges` | the selector below, plus `maxReported` | Resolves a selector and reports it. **Writes nothing.** Runs the same `_select_edges` the two editing ops run, so what it reports is what they would act on. Returns the boundary / interior / wire breakdown. |
| `bevel_edges` | see below | Chamfer (`segments:1`) or round (`segments>1`), with a tiling guard. |
| `extrude_skirt` | see below | Duplicate a boundary loop and drop it in Z. The **safe** tiling edit: nothing moves in X or Y. |
| `delete_object` | `object`/`objects`, `purgeOrphans` | |
| `clear_scene` | `type`, `purgeOrphans` | |
| `run_python` | `code` or `file`, `returnLocals` | Escape hatch. Runs on the main thread like everything else. Returns `stdout`, `stderr`, and whatever the code assigns to `result`. Gated by a preference, and deliberately **not** exposed as an MCP tool (recorded in `tools/parity_check.py`). |

> **The op table is checked, not asserted.** `python tools/parity_check.py` (repo root) diffs the
> `_blender("...")` literals in the MCP server against these `OPS` dicts, *and* diffs the keys each
> call site sends against each op's `reject_unknown` set. It is the bl_* half of the
> `MIF_DECL == MIF_BIND` discipline — there is no compiler here, so the check has to be a script.
> It exists because three of these ops were once called and did not exist, and a fourth was called
> with two params it refuses.

### The edge selector — shared by `select_edges`, `bevel_edges` and `extrude_skirt`

One grammar, one code path, **flat keys**. Every criterion supplied is **AND**ed, and it refuses to
run with none:

| | |
|---|---|
| `minAngleDeg` / `maxAngleDeg` | Angle between the two faces an edge joins. `0` = coplanar, `90` = a box corner. This is the "chamfer everything sharper than X" selector. |
| `axis` (`X`/`Y`/`Z`) + `side` (`min`/`max`/`both`) + `tolerance` | Edges lying in the object's min or max plane along an axis. This is the **min/max Z** selector. |
| `boundaryOnly` | Edges with exactly one linked face. |
| `edgeIndices` | Explicit list. |
| `allEdges` | Say it out loud if you really mean every edge. |

There is **no nested `selector` object** and no `preserveX`. The MCP server's `bl_*` tools take a
nested `selector` dict because it reads better at a call site, and flatten it (`boundary` →
`boundaryOnly`, `preserve_x` → `preserveAxes` + `assertAxes`) in `_bl_selector` before it goes over
the wire. Send the flat keys if you are speaking to the socket directly.

`select_edges` is the tool for iterating on a selector: it reports `count`, `boundaryEdges`,
`interiorEdges`, `wireEdges` and `edgeIndices[]` and changes nothing. A non-zero `interiorEdges` is
exactly why `extrude_skirt` would refuse.

### `bevel_edges`

Geometry: `offset` (Blender units) **or** `offsetUU` (Unreal units — exactly one),
`segments`, `profile`, `offsetType`, `clampOverlap`, `loopSlide`, `hardenNormals`,
`miterOuter`, `miterInner`, `spread`.

`dryRun: true` reports the matched edge count and indices and changes nothing.

**The tiling guard.** A bevel moves the endpoints of every edge it touches, so bevelling
the long sides of a tiling mesh also drags its end-cap verts inward and the tile stops
butting up against the next one.

- `preserveAxes: ["X"]` — after the bevel, snap any vert that ended up near the *original*
  min/max along X back onto it. Tolerance defaults to just over `offset`, because the very
  verts the bevel pulled inward are otherwise the ones it fails to catch.
  **Defaults to whatever `assertAxes` names.** Asserting an axis without preserving it is
  not a configuration, it is a guaranteed failure — the assert measures exactly the drift
  the preserve exists to remove. Pass `preserveAxes: []` explicitly to really mean none.
- `assertAxes: ["X"]` — after that, **throw the whole edit away and fail** if *either* the
  size along X moved by more than `assertTolerance` (default `1e-5` BU) *or* the seam
  **planarity** on X broke. Nothing is written to the mesh.

Both default to off. The response always carries `sizeBeforeBU` / `sizeAfterBU` /
`sizeDeltaBU` / `sizeDeltaUU`, **and `offSeamVerts` / `seamPlanarity` for all three axes
whatever is guarded** — a guard decides what *fails*, never what is *looked at*.

Two `bmesh.ops.bevel` defaults are overridden and must stay overridden: `affect` (defaults
to `'VERTICES'`) and `material` (defaults to `0`, which would drag every new face into the
first material slot instead of inheriting).

#### Seam planarity — the check the size assert cannot make

> **MEASURED TRAP.** `assertAxes` alone is provably blind to the failure it exists to catch.
> On Blender 4.4.0 headless, a 1000 × 300 × 50 uu tile, bevelling the Y edge loops at
> `offsetUU: 15` with the guards off:
>
> | measure | before | after |
> |---|---|---|
> | `sizeDeltaUU` | — | `[0.0, 0.0, 0.0]` ← says CLEAN |
> | verts off the X seam plane | 0 | **24 of 32**, up to 15 uu inside |
>
> A bounding box is pinned by its *extremes*, so the surviving corner verts hold the
> reported X length at exactly 1000 while most of the end cap has slid inward.
> `assertAxes: ["X"]` passes that, and the tile shears at every spline join.

So both editing ops measure planarity as well as extent, per axis:

| field | meaning |
|---|---|
| `bandVertsBefore` / `bandVertsAfter` | Verts lying strictly *inside* `(lo, lo+band)` or `(hi−band, hi)` — off the seam plane but close enough that the preserve snap should have caught them. **An increase is the shear**, and is the primary trigger. |
| `movedOffSeam` | Verts that were on the plane, **survived** the op (tracked by `BMVert` identity), and are now further than `band` from it. Catches a survivor that drifted clean past the band. |
| `seamVertsRemoved` | Tracked seam verts the op destroyed. Reported, never failed on — MEASURED, the bevel above removes all 8 originals and rebuilds 8 in the same places, which is correct. It is here so `movedOffSeam: 0` is never misread as "the originals are fine" when they are gone. |
| `onSeamBefore` / `onSeamAfter` | Population of the plane, for reading the other three. |

`seamBand` defaults to the preserve snap tolerance — the offset for a bevel, an epsilon for
a skirt — so *"in the band"* means exactly *"the snap should have got this"*. A band
narrower than the snap tolerance is the blind spot all over again. `seamTolerance`
(default `1e-4` BU) is the separate "is this vert **on** the plane" epsilon.

Warnings for **unguarded** axes are deliberately quiet: they are emitted only when *no*
axis is guarded at all, and never for the selector's own axis (`axis: 'Y'` means "bevel the
Y extremes", so leaving the Y plane is the request). Without that, the ordinary
`preserve_x` bevel emitted two true, irrelevant, identical warnings every single run.
Nothing is hidden either way — `seamPlanarity` carries the raw numbers on every response.

### `extrude_skirt`

Geometry: `depth` (BU) **or** `depthUU` (uu — exactly one), `direction` (`down` | `up`, default
`down`), `flipNormals`. Same selector, same `preserveAxes` / `assertAxes` / `seamTolerance` /
`seamBand` / `dryRun` vocabulary — including `preserveAxes` defaulting to `assertAxes` and the
same two-part extent-**and**-planarity assert — plus `allowNonBoundary`.

**Why it is the safe tiling edit.** A bevel moves the endpoints of every edge it touches, including
the end-cap seam verts. This op does not move any existing vertex at all:

```
new geometry = bmesh.ops.extrude_edge_only(edges)   # duplicates the loop IN PLACE
               then bmesh.ops.translate(new verts, vec=(0, 0, ±depth))
```

The X and Y components of that vector are literal zeros, so the seam stays planar **by
construction**, not by clamping afterwards.

VERIFIED on Blender 4.4.0 headless, a 10 × 3 BU grid tile, 8 boundary edges, `depthUU: 15`:
`dX 0.0`, `dY 0.0`, `dZ 0.15`, min/max X unmoved, `preserveSnappedVerts: 0`, and zero verts
near-but-off the X seam planes. On the same rig the new side faces on the `+Y` boundary came back
with normal `(0, +1, 0)` — outward — at the default `flipNormals: false`.

Guards and refusals:

- **Non-boundary edges are refused** (`allowNonBoundary` to override). Extruding an interior edge
  does not skirt the mesh, it duplicates the loop and *splits* it, and the seam is then invisible
  from outside. Run `select_edges` first and read `interiorEdges`.
- **`assertAxes` may not contain Z.** A skirt grows Z by up to `depth`; asserting it constant is a
  guaranteed failure, so it is refused up front rather than after the edit.
- **Seam planarity is measured, not assumed** — the same `offSeamVerts` / `seamPlanarity` block
  documented under `bevel_edges` above, reported for all three axes and failing on the guarded
  ones. A bounding-box extent cannot see this: one surviving corner vert pins the box while the
  rest of the seam slides inward.
- `preserveTolerance` defaults to `1e-6` BU here, not to just-over-`offset` as in `bevel_edges` —
  nothing moves laterally, so a wide snap band would drag genuine interior verts onto the seam and
  call that a fix. `preserveSnappedVerts` should always be `0` and `offSeamVerts` all zero;
  anything else means the selection was not a clean boundary loop.

**UVs.** The new faces get whatever bmesh copies from the source loop, which is not a skirt unwrap.
Expect stretched texturing until they are authored. Nothing here does that for you.

---

## FBX axis and scale — the part that must not be guessed

Unreal writes, and reads back, `Up = +Z`, `Front = -Y`, right-handed, unit centimetres.
Verified in engine source at `D:/UE532`:

- `Editor/UnrealEd/Private/Fbx/FbxMainExport.cpp:268-276` builds
  `FbxAxisSystem(eZAxis, -eParityOdd, eRightHanded)` and `SetSystemUnit(cm)`.
- `FbxMainImport.cpp:1500-1515` builds the **identical** system and only calls
  `ConvertScene()` `if (SourceSetup != UnrealImportAxis)`. Match it and nothing rotates.

Blender's operator defaults **do not** match — they are `axis_up='Y'`, `axis_forward='-Z'`
(Maya Y-up). Verified empirically on 4.4.0 by exporting the same object twice and parsing
`GlobalSettings` straight out of the binary FBX:

| export setting | UpAxis | UpSign | FrontAxis | FrontSign | CoordAxis | CoordSign |
|---|---|---|---|---|---|---|
| `axis_up='Z', axis_forward='Y'` ← what this addon pins | **2 (Z)** | **+1** | **1 (Y)** | **−1** | **0 (X)** | **+1** |
| operator defaults | 1 (Y) | +1 | 2 (Z) | +1 | 0 (X) | +1 |

Row 1 is bit-for-bit UE's system. `('Z','Y')` is also the row Blender's own table marks
`# Blender system!` (`io_scene_fbx/fbx_utils.py:126`), so it is the identity mapping in
both directions — not a correction, the absence of one.

**Scale.** With `apply_unit_scale=True` and `apply_scale_options='FBX_SCALE_NONE'`, Blender
bakes ×100 into the geometry and writes `UnitScaleFactor=1.0` — centimetre magnitudes in a
centimetre file (verified by reading the header while a 10.0 BU object measured 1000 in the
file). So **1 Blender unit = 100 Unreal units**; a 1000 uu road is 10.0 BU.

**Import takes no axis arguments on purpose.** `use_manual_orientation` defaults to `False`,
which makes the importer read `FrontAxis`/`UpAxis`/`CoordAxis` out of the file and
reverse-map them (`io_scene_fbx/import_fbx.py:3136-3145`). Passing axis args could only make
it wrong.

**Never call `bpy.ops.object.transform_apply`.** Anywhere. UE exports a static mesh in its
own local space; one "Apply All Transforms" bakes the round trip into the mesh and anything
that tiles shears. `import_mesh` warns when an imported object has a non-identity transform
precisely so nobody is tempted to "fix" it.

### Two measured traps in this area

**`scene.unit_settings.scale_length` silently rescales every export.** MEASURED on 4.4.0 with this
addon's own `FBX_EXPORT_ARGS`: the same 10 BU cube exported at `scale_length 1.0` reimports at
10.0 BU; exported at `scale_length 0.01` it reimports at 0.1 BU with object scale 0.01.
`UnitScaleFactor` in the file header is `1.0` **both times**, so the header does not give it away —
only the magnitudes do. `scene_info` reports `unitSettings.scaleLength` and warns when it is not 1.0,
and `mif_mesh_roundtrip`'s fidelity gate is what would otherwise catch it, late.

**`obj.bound_box` and `obj.dimensions` are caches, and they are stale right after a bmesh write.**
MEASURED on 4.4.0: after `bm.to_mesh(mesh)` + `mesh.update()`, the mesh verts span `z −0.15…0.0`
while `obj.bound_box` still reports `0.0…0.0` and `obj.dimensions` still reports `z = 0`.
`obj.update_tag()` does **not** refresh them; only `bpy.context.view_layer.update()` does. That
matters because the round trip's X-length assert reads `boundsLocalSizeUU` immediately after an
edit — a cached box would have it measuring the *pre-edit* mesh and passing a sheared tile. So
`local_bounds()` reads the vertex data directly (verified identical to a refreshed `bound_box` to
1e-6), and the editing ops call `view_layer.update()` anyway for every other consumer.

### Not verified — read this before trusting the loop

- **The full Unreal → Blender → Unreal loop has never been run.** MifBridge had no export
  endpoint when this addon was written. What is verified is each half in isolation plus the
  byte-level axis header. Run a **no-op** round trip first — export, reimport with no edits,
  diff bounds / vert count / **material slot order** — before putting real geometry through.
- **`mesh_smooth_type='FACE'`.** It writes smoothing groups *and* normals, strictly more
  information than the `'OFF'` default, but which of the two Unreal consumes depends on the
  static-mesh import options ("Normal Import Method") and that was not confirmed. If normals
  come back wrong, this is the first knob to turn.
- **Material slot order.** If FBX round-tripping reorders slots, the mesh is still valid but
  renders with swapped materials — and it will look like a texture bug, not a slot bug.
  `object_info` reports `materialSlots` in order so you can diff it.
- **Bevel/skirt UVs.** New faces get no meaningful UV assignment. Expect stretched or garbage
  texturing on bevelled geometry until UVs are authored. Nothing here addresses that.

### Format support

FBX only. `import_mesh` / `export_mesh` reject anything else with a message rather than
guessing: OBJ in particular is not round-trip-safe against Unreal (UE's OBJ exporter swaps
Y/Z, de-indexes to three verts per triangle and writes no normals). Use `run_python` if you
need another format and accept that the orientation is yours to get right.

---

## Threading — why this addon is shaped the way it is

`bpy.data` and the DNA-RNA layer are not thread-safe, and `bpy.ops` additionally reads the
global context, pushes undo steps and tags the depsgraph. A write from a non-main thread
while the main thread is mid depsgraph-eval or mid-draw is a data race on raw C pointers:
the failure is a hard process crash or silent heap corruption with **no Python traceback**.
You cannot `try`/`except` your way out of it. This is the number one way Blender addons kill
Blender.

So the split is absolute:

- **socket thread** — `accept()`, `recv()`, `json.loads()`, enqueue, `sendall()`. Nothing else.
- **main thread** — every single `bpy` call, drained from a queue by exactly one timer.

The marshalling primitive is `bpy.app.timers.register`. Note *where* it is called from: the
drain timer is registered **once**, from `register()`, on the main thread. The socket thread
only ever does `_JOBS.put(job)`.

That differs from the usual pattern, which calls `bpy.app.timers.register` from the socket
thread once per command. I could not verify from Blender source or docs that
`bpy.app.timers.register` is safe to call off the main thread, and the whole design would
rest on it. A queue plus one main-thread-registered timer needs no such assumption. Please
do not "simplify" it back.

The socket thread **waits** for the job to finish before replying. Fire-and-forget would tell
the caller a file is on disk before Blender has written it — which for the mesh round trip
means Unreal reimports a stale file.

`blender -b` has no event loop, so timers never fire. Three places branch on
`bpy.app.background` and all three are required or a headless run hangs forever: the accept
loop runs on the main thread, clients are handled serially, and jobs execute inline.

---

## Security

- Binds `127.0.0.1` only. No 0.0.0.0 option, by design.
- Every request must carry a `token` matching the addon preference (`hmac.compare_digest`).
  An empty token means no authentication and the addon says so, loudly, in the console, the
  preferences panel and the N-panel.
- `run_python` executes arbitrary code with your user's privileges. It is a preference
  (default on, since it is the documented escape hatch) — turn it off if you do not need it.

---

## Not implemented

- Anything that talks to a network service. This addon only ever touches the loopback socket
  and the local filesystem.

(`extrude_skirt` was listed here until it was written; it is a first-class op now, documented
above. `run_python` is no longer the workaround for it.)

---

## Credits and licence

MifBridge — MIT, © 2026 Mifsopo ("Mif").

The socket framing and the main-thread-marshalling pattern are adapted from
[blender-mcp](https://github.com/MCPBlender/blender-mcp), © 2025 Siddharth Ahuja, MIT
licence. MIT to MIT, with thanks. None of that project's telemetry, secret-store, terms
document or third-party service integrations were carried across.
