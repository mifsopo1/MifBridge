# Changelog

Every number here was **measured from the tagged tree**, not remembered — UE endpoints by counting
`MIF_DECL` in `MifBridgeHandlers.h` at that commit, Blender ops by parsing the `OPS` dicts. The one
number in this repo that was ever hand-typed read "20 ops" long after it was 68, which is why none
of these are.

**Every UE column was one too high until 2026-09-02, and the correction is left visible rather than
quietly applied.** The count included the macro DEFINITION — `#define MIF_DECL(Name) ...` matches
`MIF_DECL\((\w+)\)` just as a real declaration does — so a parameter called `Name` was counted as an
endpoint in every release since 0.3.0. Measured, and still wrong by one: being generated protects a
number from going stale, not from a bug in the generator. The same off-by-one turned up the same day
in `audit_param_guards`, which reported a phantom endpoint named `Name` for exactly this reason.

`make_release.py` was never affected and needs no change — its counter matches on `[a-z_0-9]+`, and
the macro's parameter is `Name` with a capital, so it could not match. That is why the README badge
read 440 for 0.8.1 while this table read 441. Said here so the paragraph above does not send anyone
hunting a generator bug that is not there: the badge is generated and was right, this table was
measured by hand and was not.

| version | date | UE endpoints | Blender ops |
|---|---|---|---|
| [Unreleased](#unreleased) | — | 453 | 155 |
| [0.9.0](#090) | 2026-09-04 | 453 | 151 |
| [0.8.1](#081) | 2026-09-01 | 440 | 68 |
| [0.8.0](#080) | 2026-09-01 | 440 | 68 |
| [0.7.0](#070) | 2026-08-30 | 421 | 42 |
| [0.6.0](#060) | 2026-08-27 | 320 | 20 |
| [0.5.0](#050) | 2026-08-27 | 320 | 18 |
| [0.4.1](#041) | 2026-08-21 | 227 | 17 |
| [0.4.0](#040) | 2026-08-21 | 227 | 17 |
| [0.3.0](#030) | 2026-08-10 | 218 | 12 |

---

## Unreleased

**Not tagged.** Four new Blender ops and the tooling around them, all landed after 0.9.0 shipped.

### The last entity a caller could name and not create

`render_animation` takes a scene BY NAME and resolves it through `bpy.data.scenes.get()`, and nothing in the addon could make a scene. So a caller could render a scene somebody else had made and never make one, and setting up a second render configuration - a different resolution, frame range or output path - meant editing the only scene there was.

* **`create_scene`** - `bpy.data.scenes.new()`, not `bpy.ops.scene.new()`: the operator needs a window and does nothing under `blender -b`, which is the path every suite and showcase stage takes. The new scene is EMPTY and is NOT activated, and the response says both rather than leaving a caller to find out. Blender appends `.001` on a name collision rather than failing, so the reported `name` is the one it actually got - a caller who then rendered by the string they sent would otherwise render the OLD scene. Verified against Blender 3.6.23, 4.2.17 LTS, 4.4.0 and 5.0.1: 13 postcondition checks green on each.

It was found by a tool rather than by hand, which is the first time for this class. `audit_blender_consumer_no_creator` asks which entity types a caller can NAME but not CREATE - the direction of `audit_family_asymmetry` that had failed to port twice - and it is scored against ground truth: run at the commit before `create_collection` existed it reports that gap and exits 1. With `create_scene` in, its baseline is empty and it is a gate at zero.

### The addon can SEE its own work now

Seven render-shaped ops existed and not one returned an image — renders went to disk and nothing
read them, so an agent could build, light and frame a scene and had never once seen the result. That
is not a knowledge problem, it is a perception problem, and it is the only change here that alters
what the tool *can* do rather than how fast it does it.

* **`render_still` gained `returnImage`** — the frame comes back as a base64 PNG, downscaled to
  `previewMaxPx` (default 1024). Opt-in, so no existing caller is handed an unrequested megabyte.
  The response carries `renderedWidth`/`renderedHeight` beside `imageWidth`/`imageHeight`, so a
  preview can never be mistaken for the artifact on disk.
* **`mesh_quality`** — the objectively checkable things that get an asset rejected from a store:
  ngons, non-manifold edges, loose and zero-area geometry, unapplied transforms, empty material
  slots, UV loops outside the tile, texel density spread. It reports every NUMBER and lists only
  defects with a defensible threshold. **Read `notMeasured` before trusting a clean report** — a
  mesh with no UV layer gets no UV verdict, which is an absence of data rather than a pass.
* **`recipe_game_ready`** — the boring pipeline, banked: apply transforms, ensure UVs, then measure
  the result. Order is the whole value, because unwrapping before scale is baked lays UVs out
  against the unscaled mesh. If it stops half way it names what it left behind, which is the
  contract the four UE-side recipes already keep.
* **`compare_to_reference`** — "match this blockout" as a number. Silhouette IoU and mean luminance
  difference, reported separately because one blended score would hide which of the two moved.
  **Render with a transparent film first**: with an opaque background every pixel counts as subject
  and IoU is 1.0 by definition — the op detects that and returns null rather than a meaningless
  number.

### Tooling

* **`tools/verify_install.py`** — one command for all three pieces. It tells the causes apart: a
  refused token means the plugin is *fine* and two configs disagree, a dead port means it is not
  started, and a squatter on the port means neither. The Blender addon is reported as an optional
  note, never a failure.
* **`tools/make_demo.py`** — generates demo images and the numbers behind them from one run, and
  checks its own output, because a demo generator that writes a blank frame and reports success is
  worse than none.
* **`make_release.py --fab`** — packages for a store, leaving out the 305 files of 510 that a buyer
  never runs. The default zip is unchanged.

### Fixed

* **`start_pie` and `trace` advertised parameters their own handlers refuse.** Both summaries are
  the text `describe_endpoint` publishes, so the contract a caller read before writing the call
  disagreed with the guard that answered it. `oneProcess`, `width` and `height` are multiplayer-only;
  `drawDuration` is only read when `draw` is true. Each condition now attaches to its own parameter.
* **A constraint whose target was deleted reported itself healthy.** Blender's `is_valid` is correct
  when a constraint is *created* without a target but is never recomputed when the target is deleted
  afterwards — the one case the field exists for. `isValid` is now derived, with `targetMissing`
  beside it. `PIVOT` is exempt, correctly.

## 0.9.0

**449 commits past `v0.8.1`.** The 5.7 probe no longer blocks
packaging: both engine records were re-taken on 2026-09-03 and cover the current Source commit —
5.3 built, 5.7 compile-probed and linked. `python tools/make_release.py --gates` answers that
without starting a release, which is new, and exists because both records had gone stale unnoticed
across nine Source commits while every one of them was being parse-checked against 5.7 with
`cl /Zs` — which is not a link.

Thirteen new endpoints — `set_node_state`; `group_actors` / `ungroup_actors`;
`register_landscape_layer`; four viewport bookmark ops; `set_material_layers`; and four blend-profile
ops — plus `rename_asset renames[]` for bulk renames in one `IAssetTools` pass, and a `delete_asset`
that names what is holding an asset instead of saying it cannot tell.

The through-line, because it decided how all of them are written: five separate engine APIs return
`void` or a single bool and then do **nothing, silently** — `JumpToBookmark` on an empty slot,
`SetBoneBlendScale` with its default `bCreate=false`, `UActorGroupingUtils::GroupActors` on four
distinct causes, `RenameAssets` uniquifying a clash rather than failing. Each new endpoint therefore
diagnoses the cause *before* calling the engine and verifies afterwards through the same predicate its
consumer uses. `register_landscape_layer`'s postcondition is literally "`paint_landscape` will accept
this now", proven by then calling it.

Also: the autonomous report loop went live and worked its first real report end to end, and now pings
the reporter on Discord so they know to pull.

### Blender: 68 ops to 151, and the addon stopped answering `ok:true` for work it had not done

The addon is a first-class half of this bridge rather than a UE accessory, and this is the release
where it was audited like one. Eighty-three new ops across modelling, materials, UV, rigging,
lighting, cameras, animation, geometry nodes, physics, particles, rendering, world and compositing.

The through-line is the same one the UE half had, found by pointing the same question at the other
backend: **an operation that cannot do what was asked should refuse, not succeed quietly.** Every
refusal in this addon ends on a promise about state - "NOTHING was changed" - and two new gated
audits (`audit_mutate_then_deny` for the addon, `audit_mutate_then_deny_ue` for the C++) now hold
both backends to that sentence.

#### Calls that used to succeed and now REFUSE - read this before upgrading

| what you send | what happens now | what used to happen |
|---|---|---|
| `NaN` or `Infinity` in any numeric parameter | refused, naming the parameter | accepted. `NaN` made the response invalid JSON, so the caller got a parse error rather than an answer |
| an integer outside 32-bit range | refused, naming the bound | a raw `ValueError` traceback, on four ops |
| a boolean typo - `"ture"`, `"flase"`, `""` | refused | read as **false**, and the op carried on as though you had asked for that |
| two spellings of one parameter with different values, e.g. `object` and `name` | refused, naming both spellings and both values | the second was silently discarded. Found by asking `add_modifier` for a modifier named `MifSub` and getting `Subsurf` - in that op `name` aliases the *object* |
| a mesh op while the object is in EDIT mode | refused | `ok:true` and nothing changed - `mesh.polygons` is stale in edit mode, the live data is in a BMesh |
| a write to a LINKED (library) object | refused | succeeded **in memory only**. RNA writes to linked data do not error and do not survive a reload |
| `add_modifier` with a datablock pointer Blender's poll rejects - a mesh where a curve is required | refused | `ok:true` with `settingsApplied` naming the setting, while the pointer stayed empty and the modifier sat inert |
| an output path that cannot be written | refused before any work | `render_still` wrote a file called `.exr` into the working directory; three other ops did the whole job and then failed to save |

Two changes that are not refusals and are worth knowing about:

* **`list_constraints.isValid` now reports a constraint with a deleted target as invalid.** It used
  to read `true`. Blender's own `is_valid` is correct when a constraint is *created* without a
  target - false for 19 of the 20 target-taking types - but it is never recomputed when the target
  is deleted afterwards, which is the one case the field exists for. A new `targetMissing` sits
  beside it, because "Blender says invalid" and "the target is gone" want different fixes. `PIVOT`
  is deliberately exempt: with no target it pivots around the object's own point, which is correct.
* **A create op that hits a name clash now reports the name it actually got.** Blender uniquifies
  silently, so seven create ops were answering a retry with the name you asked for while the object
  was called something else.

Responses also gained consequence fields where an op affects something the caller did not name -
`meshSharedWith` and `alsoChangedCount` when an edit lands on a linked duplicate that shares one
mesh datablock, `editModeStale` when a read is answering from stale data.

### Fixed: `override_inherited_component` could not be called through the MCP at all — v0.3.0 to v0.8.1

Same cause as the `sculpt_landscape` entry below, found the same way. `confirm` is **optional** on
this endpoint — minting an override is reversible with `revert_inherited_component` — but it is
*honoured* rather than ignored, so an explicit `confirm=false` is a deliberate no and is refused.
The wrapper declared `confirm: bool = False` and `_post` sends anything that is not `None`, so every
call carried `confirm: false` and every call was correctly refused. There was no argument list that
made it work short of passing `confirm=True` by hand.

`confirm` defaults to `None` now: omit it to proceed, pass `False` only when you mean it. Posting to
the bridge directly was never affected.

This is the only endpoint that honours `confirm` this way — checked, one handler
(`MifBridgeInherited.cpp:817`), so no other tool has it.

### Fixed: `sculpt_landscape` flatten and smooth never worked through the MCP — v0.3.0 to v0.8.1

If you drove terrain sculpting through the MCP server rather than posting to the bridge directly,
**`mode: "flatten"` and `mode: "smooth"` have been refused since v0.3.0** — eight tagged releases.
The default invocation, `sculpt_landscape(center, radius)` with nothing else supplied, was among
them.

The wrapper declared `mode="flatten"` and `amount=0.0`. `_post` sends anything that is not `None`,
so every call carried `amount: 0.0`, and the endpoint refuses `amount` unless the mode is
raise/lower. The **endpoint was right** — that refusal is deliberate and is the one the codebase
cites as the model for the whole silent-ignore class. The wrapper defeated it by carrying a default
the handler already had.

`amount` now defaults to `None`. Omitting it on raise/lower is still refused, by the handler, with
"needs a non-zero amount" — which is the correct answer to that call. Posting to the bridge directly
was never affected.

A check now covers the class: `tools/audit_mcp_default_sends.py` asks whether any MCP wrapper sends,
by default, a key its endpoint refuses for being present. Eighteen endpoints, plant-proven across
four shapes — a string default, a float, a bool, and one on the Blender transport — plus a negative
control that an `or None` wrapper is *not* flagged.

### Fixed: four more MCP tools were uncallable, and one of them lied about it

The two entries above were not the whole class. A review of the tree found four more of exactly the
same shape — the handler refuses a key for being *present*, and the wrapper sent a concrete default,
so `_post` never dropped it.

| tool | state before | detail |
|---|---|---|
| `map_legacy_input` | **uncallable in both modes** — v0.7.0 to v0.8.1, every release that has ever carried it | an action mapping refuses `scale`, an axis mapping refuses `shift`/`ctrl`/`alt`/`cmd`, and the wrapper sent all five — so each mode was refused for the *other* mode's keys, and no argument combination reached the mapping code |
| `set_struct_member` | **uncallable** | `bWantRename` is a presence check on `newName`, and the wrapper sent `newName=""`, so the rename branch ran on every call and the next line refused the empty identifier |
| `set_enum_value` | **bitflags mode unreachable** | `bHasEntry` is a presence check on `value` among others, and `value=""` made it true always; bitflags-plus-entry is refused. That mode has no other route — `UEnum::Names` is a protected non-`UPROPERTY`, which is why the endpoint exists |
| `set_collision` | **applied the change, then reported it had not** | both branches are presence-gated and both keys were sent, so a profile-only call reached `SetCollisionProfileName` and *applied* it, then failed on the empty `collisionEnabled` with `NOTHING was changed.` The component was left on a new collision profile while the response said it was untouched |

`set_collision` is the one to check if you script against it: a call that reported failure may have
succeeded in part. The others refused cleanly, so nothing was half-done.

Posting to the bridge directly was never affected in any of the four — the handlers were right
throughout and the wrappers defeated them.

### Seven calls that used to succeed now REFUSE — read this before upgrading

Every one of them was accepted, silently ignored, and answered `ok:true`. That is the shape the
`invoke_editor_tab` bug had, and `RejectUnknownParams` cannot catch it because the parameter *is*
declared — it is simply never read on the branch you reached. Found by reading
`audit_mode_params`' review list, which exits 0 either way and had therefore never been read.

| endpoint | what is refused now | what used to happen |
|---|---|---|
| `trace` | `radius` / `halfExtent` / `halfHeight` on a shape that does not read them; `drawDuration` without `draw` | **`shape` defaults to `line`**, so setting `radius` and forgetting `shape` fired a ray instead of a sweep and returned a line's much smaller hit set |
| `draw_debug` | `start`/`end`/`center`/`radius`/`extent`/`text` on a shape that does not read them | drew a default-sized shape and threw the argument away under `drawn:true` |
| `create_procedural_mesh` | any of 17 shape-specific parameters on the wrong shape | `{"shape":"box","radius":200}` returned a default 100³ box |
| `blueprint_watch` | `nodeGuid`/`nodeId`/`pin` on `op:list` or `op:clear` | **`clear` removed EVERY watch on the blueprint**, not the one you named, and `removed: 7` read like confirmation |
| `blueprint_breakpoint` | `nodeGuid`/`nodeId` on `op:list` or `op:clear` | same — every breakpoint gone. Both are editor-only state, so nothing undoes it |
| `start_pie` | `oneProcess`/`width`/`height` on a single-player session | window opened at the editor's own size, values not even echoed back |
| `list_sublevels` | `netMode` when `world` resolves to `"editor"` — including the default, which is `"editor"` | defaulted to the **editor** world and answered about that instead |

The two `clear` guards are the ones to check first if you drive this from a script: the fix is to
use `op:remove`, which the refusal now names.

Two more endpoints already warned rather than ignoring, and the warning did not say *what* it had
dropped: `add_variable` named three of sixteen flags and an ellipsis, `export_asset` named none of
its eight FBX-only options. Both now list exactly what was passed and carry it as an array —
`ignoredFlags` and `ignoredOptions` — so a caller need not parse prose to react. **They still
succeed** — the heading above counts the seven in the table, not these two; an earlier draft said
nine and counted them, which would have sent an upgrader looking for two call sites that never
changed behaviour.

Also fixed, and invisible from outside: `draw_debug`'s unknown-shape refusal sat inside the
`center` branch, so `{"shape":"blob"}` answered *"shape 'blob' needs center"* — sending you off to
supply a centre for a shape that does not exist.

---

## 0.8.1

**A one-line fix for a release that did not compile on 5.3.**

0.8.0 shipped with a blanket rename that rewrote a type alias into `using X = X;`. The 5.7 build never enters that arm, so the probe passed and the break reached a tag. 0.8.1 fixes it and `make_release.py` gained `gate_53`, which refuses to package without a recorded successful 5.3 build — the check that would have caught it.

No endpoints added; the count is 0.8.0's.

---

## 0.8.0

**The Blender arm becomes a full DCC, and the 5.7 gate earns its keep.**

The Blender half went **45 ops → 68**, closing every capability family that had no typed op at all.
Before this it could model, boolean, transform and shade; it could not light, aim a camera,
animate, simulate or render — so everything past modelling had to leave the typed path for
`run_python`.

Nine families landed: **lights**, **cameras**, **keyframes**, **geometry-node tree authoring**,
**particles**, **physics**, **rendering**, **world**, and **viewport control**.

That ninth one was not on the original gap list, and the reason is worth keeping: the list had been
written by asking what the *engine* can do. Viewport control is about what the person watching can
**see** — a bridge that can light a scene and cannot show it has not finished the job. It surfaced
only because a lighting stage was reported as "all grey, couldn't see any of it" while every light
was being created correctly into a SOLID viewport.

**The 5.7 release gate refused to package, and was right.** Four C++ files had changed since the
recorded engine probe; re-running it found the plugin no longer compiled on 5.7 at all. Three
breaks, every one of a shape a symbol-presence check cannot see because all the names still exist
in both trees:

- `EAutomationTestFlags` changed **three ways at once** — namespaced `enum Type` became an
  `enum class`, the accessor moved namespace *and* was renamed, and `GetTestFlags()`'s return type
  changed.
- Comparing the resulting enum class against a bare `0` is a hard error, where the old plain enum
  converted silently.
- 5.7 escalates the `FInputKeyEventArgs` deprecation from a warning to a **build failure**. The
  code already documented that the 6-arg form was deprecated and chose it deliberately; what
  changed was the consequence, not the API.

This is why the probe **compiles** rather than greps. Reading finds symbols that were *deleted* and
reliably misses ones that *changed shape*.

Also: the spec backlog reached zero (358 done, 45 declined); two new Blender suites
(`test_blender_anim`, `test_blender_scene`) were written *because* a 44-run sweep went green over
all the new code while calling none of it; and `audit_dead_params` plus its Blender twin stopped
letting one handler vouch for every other.

## 0.7.0

**+101 endpoints, and the first regression sweep that ever finished.**

The endpoint count went 320 → 421 and the Blender arm doubled, 20 → 42. More importantly it is the
first release where a full sweep ran to completion rather than dying partway — which is what made
every count after it trustworthy.

## 0.6.0

Blender 18 → 20 ops. UE endpoints unchanged from 0.5.0.

## 0.5.0

**227 → 320 endpoints.** The largest single jump in the project's history.

## 0.4.1

**Fixes an `add_pin` editor crash when a default is supplied.** A patch release for one crash, which
is the right size for a crash.

## 0.4.0

**Animation Blueprints, widget tree topology, and a crash that had been documented instead of
fixed.** The title is the lesson: a known crash written down in a doc is still a crash.

## 0.3.0

**The Blender round trip.** The first release where the two halves worked as one tool — a mesh could
leave Unreal, be edited in Blender, and come back.

---

Releases with attached zips: <https://github.com/mifsopo1/MifBridge/releases>
