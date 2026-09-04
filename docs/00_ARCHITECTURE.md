# MifBridge — architecture map

Editor-only UE 5.3 plugin exposing a loopback HTTP API over Unreal's Blueprint graph API, plus the
MCP server that fronts it.

---

## Repo layout — three installables, one repo

Since 0.3.0 this repo ships three things, and only one of them is Unreal:

| Path | What | Installed to |
|---|---|---|
| `MifBridge.uplugin`, `Source/` — **the repo root is the plugin** | UE editor plugin (C++) | `<Project>/Plugins/MifBridge/` |
| `tools/mcp-server/` | The MCP server. Fronts **both** backends. | nowhere; referenced by path from `.mcp.json` |
| `tools/blender-addon/` | `MifBlender` — the Blender backend, one `ops_*` module per family. **The op count is deliberately not written here**: this line said "12 ops", then "68 ops", and was stale within two days each time. `python tools/parity_check.py` prints it. | Blender's addons dir, **via `tools/sync_blender_addon.py`** |
| `tools/layout_graph.py` | Arranges a blueprint graph and adds comment boxes, entirely from the client — `list_nodes` for the topology, `move_node` and `add_comment` to apply. No C++, no plugin. `--self-test` proves the algorithm offline. | nowhere; run it from `tools/` |

### The Blender arm's capability families

THE COUNT IS NOT WRITTEN HERE, deliberately, and the reason is this line's own history: it read
"12 ops" long enough to be misleading, was corrected to "68 ops (2026-09-01)", and was stale again
within two days when the arm roughly doubled. A number in prose beside a tool that prints the same
number is a second source of truth, and it is always the prose that rots.

    python tools/parity_check.py          # ops, MCP call sites, endpoints, and any drift between them
    python tools/blender_version_matrix.py  # what actually runs, on every installed Blender

The families below are what the arm can DO. Each row names the trap the family exists to guard,
because that is the part that does not go stale.

| family | the trap it exists to guard |
|---|---|
| **lights** | type-specific settings are REFUSED on the wrong type; Blender would just not have the attribute and the write would vanish. Shadow settings move more than anything else here: `cycles.cast_shadow` is 3.6 only, contact shadows were dropped by EEVEE Next at 4.4, and the jitter group arrived at 4.2 |
| **cameras** | a camera faces its local **−Z**; `lookAt` derives the euler, because hand-aiming gets this wrong first. A PANO camera could be created and not configured until `set_camera_panorama` — declared-and-unreachable is worse than absent |
| **animation** | a transform is on the object and a light's energy on its **data**; and an f-curve edit that keeps the right NUMBER of keys while losing the motion is the normal failure, so bakes are judged by the evaluated matrix across the range |
| **rigging** | a vertex group that exists with every weight at **zero** deforms nothing and reads back identically to a working one, so weights are counted non-zero rather than by membership. Bones exist only in EDIT mode, so building them means changing mode and changing back — restoring it is a postcondition, not tidy-up, because being left in edit mode strands every call that follows; and a bone whose head equals its tail is DELETED by Blender on the way out, silently |
| **geometry nodes, shaders, compositor** | an unlinked Group Output is **not an error** and does **not** pass the geometry through — the modifier evaluates to EMPTY and the object disappears from the viewport and from every export. Measured as 0 vertices on 3.6.23, 4.2.17, 4.4.0 and 5.0.1; this row said "passes through unchanged" until 2026-09-04, which sends you to the opposite end of the pipeline. A Group node pointed at no tree is the same trap one level up, and a RECURSIVE nest is rejected SILENTLY with node_tree left empty. The compositor is `scene.node_tree` up to 4.4 and `scene.compositing_node_group` at 5.0, where `CompositorNodeComposite` does not exist at all |
| **collections & view layers** | a collection not linked into the scene, or an object in no collection, is invisible everywhere while every field on it reads perfectly. "Hidden" is four different properties in two datablocks, two of them per view layer |
| **materials & UV** | `uv_layers.new()` does not make the new layer active, so a lightmap pass silently repacks the base colour UVs; and a bake written to an sRGB image is a normal map with a gamma curve on it. The same gamma trap governs an IMAGE brought in from disk — colour space is chosen per input, because Blender decodes from the IMAGE and not from where it is plugged in — and a texture node with no image renders MAGENTA, which the read side hid until 2026-09-04. `blend_method` decides whether a transparent material is transparent at all: alpha 0.2 on an OPAQUE material renders solid |
| **queries** | `obj.ray_cast` is LOCAL on both sides, so world coordinates give a plausible WRONG hit; and an AABB overlap test says two objects touch when they do not |
| **physics** | a sim is stepped **forward**; jumping to a late frame shows the rest pose until the cache is baked, and a cache baked before the range was extended stays valid and short |
| **rendering & colour** | `render()` returns FINISHED whether or not a file appeared, so `wroteFile` is stat'd off disk. An animation render cannot be in-process under the 150s job ceiling, so it spawns a child and is polled |
| **file & interchange** | nothing the addon authored survived the process until `save_file`; and a save DESTROYS unused datablocks, which is data loss caused by the successful operation |
| **world** | strength 1.0 with mid-grey is overcast daylight and washes out a dark interior; a dim room wants 0.02–0.1. A world with `use_nodes` off ignores its whole tree |
| **viewport** | SOLID shading ignores lamps, so a correctly lit scene looks grey — only **RENDERED** shows what will render |

The viewport family was not on the original gap list. That list was written by asking what the
ENGINE can do; viewport control is about what the person watching can SEE, and a bridge that can
light a scene and cannot show it has not finished the job.

**How the arm is verified**, and the distinction matters more than the numbers:

  * `test_blender_refusals.py` runs with **no Blender at all**, against a stub. It proves refusal
    contracts and the few families whose logic is pure data. It cannot prove an op DOES anything —
    and worse, it agrees with whatever the author believed, because the same person wrote the stub.
  * `blender_version_matrix.py` runs **almost every op on every installed Blender**, headless, in a
    throwaway `--factory-startup` process. This is what catches version drift. On 2026-09-03 it
    found three ops that had never worked on ANY build, and the compositor family dead on 5.0 with
    every static gate green. "Almost" is load-bearing: a handful reach a network service, spawn a
    second Blender or need a fixture no op here can build. It PRINTS that list on every run, and
    the number that matters is REACH - an op refused at the door is green and proves nothing.
    It compares two different things across builds: whether each op still SUCCEEDS, and whether
    it still returns the SAME ANSWER. The second was missing until 2026-09-04 and is the one that
    catches an op quietly changing its results - and it found a geometry query reading a fixture
    ten earlier ops had reshaped on its first real run.

Both are in `make_release --gates`.

### Client-side capability

`layout_graph.py` is the first thing here that adds a real capability **without touching the
plugin**. It exists because the endpoints underneath were already sufficient: `list_nodes` returns
the whole graph topology in one call, and `move_node` and `add_comment` write positions and boxes.
Worth checking for that before reaching for C++ — the engine ships no blueprint graph layout, so
this looked like an engine job and was not one.

    python tools/layout_graph.py <graphId>                     # plan only, nothing moves
    python tools/layout_graph.py <graphId> --comment --apply   # arrange and box it
    python tools/layout_graph.py --self-test                   # no editor needed

NOT AN MCP TOOL YET, which is the gap that matters more than this table entry: an agent driving
MifBridge through MCP cannot call it, and agents authoring graphs are exactly who needs it.

The root is the UE plugin because Unreal locates a plugin by finding a `.uplugin` at the root of the
plugin folder — a tidy `unreal-plugin/` subdirectory would mean nobody can clone this straight into
`Plugins/`. Unreal never reads `tools/`, `docs/` or `.github/`: `FPluginDescriptor`
(`Runtime/Projects/Public/PluginDescriptor.h`) has no field that enumerates or excludes directories,
and the single module is `"Type": "Editor"`, so none of it can reach a cooked build either.

`tools/ue5-mcp-bridge/` was renamed to `tools/mcp-server/` in 0.3.0 (the server is no longer
UE5-only). A forwarding shim remains at the old path for existing `.mcp.json` files. **Everything
under `docs/audit/` still says `ue5-mcp-bridge` on purpose** — those are dated records, not
instructions, and rewriting them would falsify the history they exist to preserve.

---

## Two backends, one tool namespace

```
agent ──MCP stdio──► server.py ──┬── _post()     ──HTTP  127.0.0.1:8791/api ──► MifBridge  (UE, C++)
                                 └── _blender()  ──TCP   127.0.0.1:8792     ──► MifBlender (Blender, py)
                                                          (planned — not in 0.3.0)
```

| Tool prefix | Backend | Rule |
|---|---|---|
| *(none)* | Unreal, via `_post` | The default backend. **Do not rename these to `ue_*`** — it would break every existing workflow and doc for cosmetic symmetry. |
| `kr_*` | Unreal, foreign provider (`MifKismetReconstructor`, registered at runtime) | Existing precedent that a prefix marks a provider. |
| `bl_*` | Blender, via `_blender` | One tool per Blender op, same one-statement passthrough discipline. |
| `mif_*` | Composes both | The **only** tools allowed to contain logic. |

Two choke-point functions, **no shared dispatch** — a change to one backend cannot break the other.
Both return the same `{ok, error}` envelope, so one error contract spans the pair, and neither one
connects at MCP startup (a lazy connect is what keeps a closed Blender from wedging client startup).

### The Blender transport is ONE serialised socket

`_BL_LOCK` guards a persistent connection, so a second op cannot read the first op's response off a
desynced stream. The consequence is that one long op blocks every other. Calls whose whole job is to
**diagnose** that — `bl_status`, and `mif_mesh_roundtrip`'s step-0 probe — pass `_lock_timeout` and
give up on the lock in 5 s, answering with the op that holds the line and for how long, which is
itself the diagnosis. Real work takes the lock unbounded and queues, which is correct for it.

Both `_timeout` (read) and `_lock_timeout` (lock) are transport-only and never reach the addon;
`tools/parity_check.py` knows that via `BLENDER_TRANSPORT_KWARGS` so they are not mistaken for
params an op must accept.

### Timeout ladder — the addon gives up first

| | default | owner |
|---|---|---|
| MCP connect | 3 s | `MIF_BLENDER_CONNECT_TIMEOUT` |
| MCP probe (read **and** lock, `bl_status` only) | 5 s | `MIF_BLENDER_PROBE_TIMEOUT` |
| **Addon main-thread job** | **150 s** | `MifBlender/server.py DEFAULT_JOB_TIMEOUT` |
| MCP work read | 180 s | `MIF_BLENDER_TIMEOUT` |

The addon number must stay **below** the MCP work number. Inverted — which it was, at 600 s against
180 s — the MCP abandons the call and drops the socket while Blender goes on mutating the scene for
another seven minutes on behalf of a caller already told the op failed. Raise one, raise the other.

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
| `MifBridgeCollision.cpp` | Collision profiles and per-component collision; simplified collision generation via UnrealEd's private `GeomFitUtils.h` |
| `MifBridgeConsole.cpp` | `exec_console`, `get_cvar`, `set_cvar` — console and CVar access, distinct from `run_console` which is deliberately unguarded |
| `MifBridgeDetails.cpp` | The Details panel's read/compare/reset verbs: `describe_property`, `diff_properties_vs_default`, `reset_property_to_default`, `edit_container` |
| `MifBridgeExport.cpp` | `export_asset` — the outbound half of the Blender round trip |
| `MifBridgeFunctions.cpp` | `implement_interface_function`, `remove_function` |
| `MifBridgeGraphPatch.cpp` | `apply_graph_patch` — many dependent graph edits in one call, with a real inverse journal. Atomicity CANNOT come from the transaction system here (PM-007), so this keeps its own |
| `MifBridgeImport.cpp` | `import_texture` (file and base64), `import_asset`, `reimport_asset`, `set_texture_settings` |
| `MifBridgeNodePins.cpp` | `add_node_pin` — adding a pin to an existing node, as opposed to wiring two that exist |
| `MifBridgeThumbnail.cpp` | Thumbnail render and write. Writes PNG via `PNGCompressImageArray` + `FFileHelper` rather than `SaveImageByExtension`, to stay off a module dependency |
| `MifBridgeUI.cpp` | Editor UI surface: commands, tabs, synthesised key chords, opening asset editors |
| `MifBridgeSkeleton.cpp` | `list_bones` — the bone hierarchy of a Skeleton or SkeletalMesh. Nothing else could name a bone: `ReferenceSkeleton` is a plain C++ member, so reflection cannot reach it |
| `MifBridgeIKRig.cpp` | IK Rig and IK Retargeter authoring — retarget root, chains, goals, solvers, chain mapping. Compiled conditionally behind `MIF_WITH_IKRIG`; the endpoints stay REGISTERED on an engine without the plugin and refuse with that reason |
| `MifBridgeNiagara.cpp` | `list_niagara_user_parameters` — reads a parameter store's values without a Niagara module dependency. The store holds THREE parallel arrays behind one offset list (§12 of the gotchas) |
| `MifBridgeDescribe.cpp` | `describe_endpoint` — the endpoint's own parameter contract, read from the same declaration the guard uses, so documentation cannot drift from enforcement |

### Adding an endpoint — files that MUST stay in sync

1. `MifBridgeHandlers.h` — `MIF_DECL(name)`
2. `MifBridgeCommon.cpp` — `MIF_BIND(name)` in `Handlers()`
3. `MifBridgeCommon.cpp` — add to `IsReadOnlyEndpoint` **or** `IsSelfManagedEndpoint` if it qualifies
4. `<some>.cpp` — define `H_name`
5. `MifBridgeDescribe.cpp` — the per-endpoint key list + notes + `Summary` row. The `Summary` must be
   **byte-identical** to the `AcceptedSummary` string the handler passes to `RejectUnknownParams`
6. `tools/mcp-server/server.py` — the MCP tool wrapper (**in this repo**, beside the plugin)
7. `README.md` + `docs/02_GOTCHAS.md`

> Steps 1–2 are checkable: the `MIF_DECL` and `MIF_BIND` name sets must be identical, and a missing
> `MIF_BIND` is a link error rather than a silent gap. **Step 6 is not link-checkable** — run the
> parity diff below instead.

**Scope clause for the 1:1 rule, now that there are two backends.** "Every endpoint needs a
`MIF_DECL` + `MIF_BIND` + `@mcp.tool`" was written when every tool was a UE endpoint. Restated: *the
UE endpoint set and the set of endpoint strings passed to `_post()` must be identical.* Tools that
call `_blender()` are outside **that** set by construction — they own no C++ endpoint — but they are
**not** outside the discipline. They have their own parity set, below. `mif_*` tools compose and own
nothing on either backend.

### The Blender half has the same rule and its own checker

`_blender("<op>")` is to `MifBlender.OPS` what `MIF_DECL` is to `MIF_BIND` — with one difference
that cost the flagship round trip: **there is no compiler.** A missing `MIF_BIND` is a link error; a
missing addon op is a runtime `"unknown endpoint"` discovered by a user. So the tie is a script:

```bash
python tools/parity_check.py            # exit 0 clean, 1 on any drift
python tools/parity_check.py --verbose  # print the resolved op tables too
```

It parses both sides with `ast` — no `bpy`, no `fastmcp`, no editor — and runs three checks:

| check | what it ties together |
|---|---|
| op parity | `_blender("...")` literals in `server.py` **==** the union of **every** `ops_*.OPS` dict, both directions. Named two modules until 2026-09-01, when there were fourteen — the checker always read them all; only this row was stale. |
| param parity | every kwarg each `_blender("op", …)` call site sends **∈** that op's `reject_unknown` accepted set |
| UE parity | `MIF_BIND(...)` **==** `_post("...")` literals, minus the recorded exemptions — the `comm` recipe below, mechanised |

**Fail-closed.** A computed op name, a `**kwargs` splat, or an accepted-key set the checker cannot
read statically is reported as a *failure*, not skipped — a check that quietly could not run is the
defect it exists to catch. Exemptions (`run_python` on the Blender side, the five toolless UE
endpoints) are named, carry a reason, and are printed on every run, pass or fail.

This exists because it was needed. Before it, `server.py` called three ops the addon did not have
(`scene_info`, `select_edges`, `extrude_skirt` — and `mif_mesh_roundtrip` *defaulted* to the last
one), and the one op both sides did have was sent `selector` and `preserveX`, neither of which is in
its accepted set. Both classes are caught by the script in under a second. **Consequence for
step 6 of the checklist above: if the new endpoint is a Blender op, `tools/parity_check.py` must be
green before the commit.**

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

**Actual, measured 2026-08-09 (0.3.0 working tree):**

| thing | count |
|---|---|
| `MIF_DECL` in `Source/MifBridge/Private/MifBridgeHandlers.h` | **218** |
| `MIF_BIND` in `Source/MifBridge/Private/MifBridgeCommon.cpp` | **218** (same name-set, diff empty both ways) |
| External (`kr_*`) endpoints registered by `MifKismetReconstructor` | **12** |
| **Endpoints total** | **230** |
| `@mcp.tool()` defs in `tools/mcp-server/server.py` | **237**, all above the `if __name__` guard |
| — of which reach Unreal via `_post` | **225** (213 built-in + the 12 `kr_*`) |
| — of which are Blender (`bl_*`) or composing (`mif_*`) | **12** — outside the parity set |

> Counting `MIF_DECL` by raw `grep -o "MIF_DECL"` overcounts — it also matches the `#define` and the
> `#undef` that bracket the block. Match the parenthesised form (`grep -coE 'MIF_DECL\([a-z_0-9]+\)'`)
> or subtract 2. This table sat at 191/203 through the whole 160→211 expansion, so treat any number
> written down here as stale until `self_audit` agrees.

**225 ≠ 230, and the gap is real.** Five endpoints ship with a `MIF_DECL`, a `MIF_BIND` and a
handler but **no MCP tool** — reachable over HTTP, invisible to an MCP client:

```
add_component_bound_event   reparent_blueprint   retarget_variable_node
set_cast_purity             set_variable_type
```

These pre-date 0.3.0. They are recorded here so the next audit reads a known delta instead of
blaming whatever landed last.

The wrapper lives at `Game/Plugins/MifBridge/tools/mcp-server/server.py` — **in this repo**, beside
the plugin, which is what closed the drift. `self_audit` reports the live endpoint count from the
running DLL and is the authority over any number written down here.

Regenerate the parity diff (run from the plugin root). **`python tools/parity_check.py` does all of
this and the Blender half too** — the shell recipe is kept because it is the thing the script was
written from, and because it needs nothing but `sed` and `comm`:

```bash
sed -n 's/.*MIF_BIND(\([a-z_0-9]*\)).*/\1/p' Source/MifBridge/Private/MifBridgeCommon.cpp | sort -u > /tmp/plugin.txt
sed -n 's/.*_post("\([a-z_0-9]*\)".*/\1/p' tools/mcp-server/server.py                     | sort -u > /tmp/mcp.txt
comm -23 /tmp/plugin.txt /tmp/mcp.txt   # endpoints with no tool  -> the 5 above, and nothing else
comm -13 /tmp/plugin.txt /tmp/mcp.txt   # tools with no endpoint  -> the 12 kr_* externals, and nothing else
```

Neither column is empty, and both non-empties are accounted for above. **Grep `_post(`, not
`@mcp.tool()`** — that is what makes the check survive a second backend: `bl_*` and `mif_*` tools
carry the decorator but never call `_post`, so they correctly stay out of both columns. The 12 `kr_*`
endpoints are registered at runtime by the provider plugin
(`Public/MifBridgeEndpointRegistry.h`) and never appear in `MIF_BIND`. Anything else in either column
is real drift.

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
