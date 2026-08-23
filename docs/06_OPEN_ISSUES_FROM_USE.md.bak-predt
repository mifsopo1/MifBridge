# MifBridge — open issues found in use

**Provenance:** written by the plugin author 2026-07-29 during a long BotanistExpansion session, from
actual use rather than from reading the code. Every item was re-tested by the author against build
`Jul 29 02:56` (211 endpoints). Reproduced here verbatim below the status table, because a report
written from *use* catches a different class of defect than an audit written from *source* — every
issue in section 1 and 3 survived the 111K-line self-audit in `07_SELF_AUDIT_FINDINGS.md` unnoticed.

An earlier draft of the author's file listed three issues the 02:56 build had already fixed. That
waste is why the "FIXED — do not re-file" section below is kept rather than deleted.

---

## Status

Author's own priority ranking, "by what actually costs me time now". Status as of the
**2026-07-29 11:21 build (220 endpoints)**, each verified by a live call, not by reading the diff.

| rank | issue | status |
|---|---|---|
| 1 | Parameter drift (`path` dropped from `connect_pins` / `disconnect_pin`) | **FIXED + verified** — `path` accepted on `connect_pins`, `disconnect_pin`, `reconnect_pin`; `self_audit` now emits `surfaceSignature` / `paramSignature` |
| 2 | `list_components` returns empty for a cooked parent | **FIXED + verified** — `BP_PlantPot` returns 12 components (was 0), `targetKind:"cookedClass"`, each with a reason and `route` |
| 3 | `get_property` returns bools as strings | **FIXED + verified** — see the split below; `list_object_properties` now emits `typed` |
| 4 | No `describe_endpoint` | **FIXED + verified** — 10/10 previously-mis-reported endpoints now `params_declared` |
| — | No image/asset import endpoint | **FIXED + verified** — `import_texture` (file + base64), `import_asset`, `reimport_asset`, `set_texture_settings` |
| — | No icon / thumbnail render endpoint (§6) | **FIXED + verified** — `render_thumbnail` 128×128 PNG in 46 ms; plus `write_thumbnail_texture`, `set_asset_thumbnail`, `thumbnail_capabilities` |
| — | `set_viewport_camera` does not reach `capture_camera` (§7) | **FIXED + verified** — `useViewportCamera` opt-in; `cameraSource` echoes `default`/`explicit`/`viewport` |
| — | `CallArrayFunction` wildcards (§5) | **PARTIAL** — `set_pin_type`'s silent revert is fixed; the `connect_pins` half is unresolved, see §5 |

Note the ranking is by **cost now**, not by severity. §5 is the most severe item in the file — it
produces a build that is green in the editor and dead in the shipped game — but it is worked around
today with `ForEach`, so it costs less time than a parameter rename that fails seven calls in a row.

> **What only running it could find.** `import_texture` shipped unable to create ANY texture under
> `/Game/`. Its guard asked "does this package have no loose file?" and called the answer
> "container-only" — but a package that does not exist has no loose file either, and in a cooked-editor
> modkit the whole of `/Game/` is `.pak`-mounted, so every new destPath was refused. All 42 icon stubs
> would have stayed empty. Static review could not catch it: the helper is a verbatim copy of one in
> `MifBridgeCooked.cpp` that is correct THERE, because it is only ever asked about packages already
> known to exist. Fixed in `MifBridgeImport.cpp` `MifImportIsContainerOnlyPackage`.

---

## FIXED since the earlier draft — do not re-file

| was | status |
|---|---|
| `set_property` silently no-ops on `TArray` with a JSON list | **FIXED.** JSON arrays write correctly. (The apparent regression in the retest was author error — no compile after `add_variable`, so the CDO had no such property yet.) |
| No verb creates a property-get node targeting another object | **FIXED.** `add_variable_get` accepts `targetClass` (aliases `class`, `cls`, `className`, `ownerClass`, `objectClass`) and `var` aliases include `property` / `member`. This unblocked a real feature — see the worked example at the end. |
| `audit_unused` has no way to ignore a referencer | **FIXED.** `excludeReferencers` is accepted. |

---

## 1. Parameter names drift between builds — HIGHEST COST NOW

Two endpoints **dropped `path` between the 00:01 and 02:56 builds**:

- `disconnect_pin` — was `{path, graphId, node, pin}`, now rejects `path`
- `connect_pins` — same; discovered only when seven wiring calls failed in a row

Both now take `{graphId, srcNode, srcPin, dstNode, dstPin}` / `{graphId, node, pin}`.

This cost two full round-trips in one task. It is not that the new shape is wrong — it is that a
caller written against a build from two hours earlier breaks silently-ish, and the only signal is a
failed call whose error you have to read.

**Ask:** keep `path` as an accepted-and-ignored alias where it used to work, or bump a version field
in `self_audit` that callers can check. The current `buildDate`/`buildTime` help, but nothing tells a
caller *which* signatures changed.

Related, lower priority — names still differ per verb:

| verb | wants |
|---|---|
| `add_function_call` | `class` (never `cls`) |
| `add_variable_get` / `add_variable_set` | `var` (many aliases) |
| `set_variable_default` | `name` |
| `get_inherited_component` | `component` |
| `connect_pins` / `disconnect_pin` | no `path` at all |

> **Root cause note (added during triage).** This is the *same* defect class as the silent-ignore
> family, inverted. Silent-ignore accepts a key it does not use; this rejects a key it used to use.
> Both leave the caller unable to tell a successful call from a failed one without reading prose. The
> instance fix is the `path` alias; the class fix is the `self_audit` signature field, because next
> time it will be a different parameter.

---

## 2. `list_components` returns nothing for a cooked parent blueprint

`list_components` on `/Game/Blueprints/LabEquipment/Growing/BP_PlantPot` (cooked-only parent) returns
an empty list.

This now matters *more* than it used to, because the ICH endpoints exist:
`get_inherited_component` needs a component **name**, and nothing enumerates the available names.
Guessing (`Pot`, `POT_PREVIEW`, `SM_PlantPot`) returns `canOverride: false`, which is
indistinguishable from "that name does not exist". A real job is blocked on this — positioning a
child-actor component against an inherited pot preview.

**Ask:** include inherited components in `list_components` (flagged `inherited: true` with their
owning class), or add `list_inherited_components {path}`.

> **Triage note.** `list_components` *was* extended in Batch N to enumerate own SCS +
> `parentBlueprintSCS` + native, and that shipped. It does not cover this case: a cooked-only parent
> ships as `UBlueprintGeneratedClass` with graphs stripped, so there is no `USimpleConstructionScript`
> to walk. The fix needs a different enumeration source for cooked ancestors. Separately,
> `canOverride:false` must carry a reason and the available names, so it can never again be
> indistinguishable from name-not-found.

---

## 3. `get_property` returns bools as strings

`get_property` on `bReplicates` returns the **string** `'True'`, not a boolean. Confirmed still
present on 02:56.

This silently broke a 63-blueprint audit earlier in this project (`if v is True` never matched). It is
a small fix with a large blast radius, because the failure is silent and reads as "nothing is
configured".

Arrays also come back as a single export-text string rather than a list, so `get_property` →
`set_property` is not a clean round trip even though the write side now accepts JSON.

> **Triage note.** The fix must be additive — keep the existing string field exactly as it is and add
> a typed field beside it, or gate the new shape behind an opt-in that defaults to today's behaviour.
> Silently changing an existing field's type would be the same breakage this file complains about in
> §1.

---

## 4. No single-endpoint schema query

`describe_endpoint`, `help`, `endpoint_info`, `describe_command` all 404. `self_audit` lists names and
transaction buckets but not parameters.

The discovery loop is therefore "call it wrong on purpose, read the error". That works — the errors
are good now — but it costs a round-trip per unknown endpoint, and with 211 endpoints that adds up.

**Ask:** `describe_endpoint {name}` returning the accepted parameter set and aliases.

> **Triage note.** The accepted-key lists already exist in source as the inline initialiser-list
> argument to `RejectUnknownParams`. But only **84 of 199** handlers call it, so any harvest must
> report "parameters not declared for this endpoint" honestly rather than returning an empty list that
> reads as "takes no parameters".

---

## 5. `CallArrayFunction` wildcards still cannot be durably typed

`Array_Find` / `Add` / `Clear` / `Contains` connect and compile, but the pin stays wildcard, the node
is reconstructed on save/cook, and the containing function then fails to compile during the cook and
is **stubbed** — so the editor says fine and the shipped game silently does nothing.

Long-standing rather than new, but it remains the single biggest constraint on what can be authored
through the bridge; every array operation has to be hand-built as a `ForEach`.

> **Triage note.** Most severe item in this file: green in editor, dead in the build, with no signal
> at either end. Needs a reproduction before any fix is attempted.

---

## 6. No icon / thumbnail render endpoint

Rendering an item icon from a mesh has no path through the bridge. `capture_camera` exists and may
be adaptable — untested.

Concretely: this mod has **42 icon textures that are empty 4.7 KB stubs** (no `.uexp`, no `.ubulk`)
with no source PNG, including every `plant_*`, every `soil_bag_*`, and ten `eq_*` equipment icons.
They render black in the shop. Generating those from the meshes that already exist in the pack is
otherwise a manual editor job.

> **Triage note.** `ThumbnailGenerator` (Plugins_RamaThumb) is already mounted in this project at
> `/ThumbnailGenerator/` — check its public API before writing anything. Pairs with the image-import
> work (#16): the render must be able to *write a `UTexture2D` asset*, not merely return pixels, or it
> does not actually fill the 42 stubs.

---

## 7. `set_viewport_camera` returns `ok:true` but does not affect `capture_camera`

`set_viewport_camera` succeeds, and `capture_camera` then renders from somewhere else entirely.
`capture_camera` accepts `location` / `rotation` inline and **that does work** — it is the only
reliable path, and it should be documented as such.

**Root cause — the two endpoints share no state.**

- `set_viewport_camera` drives the real editor viewport: `FLevelEditorViewportClient::SetViewLocation`
  / `SetViewRotation` / `ViewFOV` / `Invalidate` (`MifBridgeViewport.cpp:84-141`). It is truthful.
- `capture_camera` spawns its **own** `ASceneCapture2D` (`MifBridgeSpatial.cpp`, the
  `World->SpawnActor<ASceneCapture2D>(Loc, Rot, …)` line) at a location and rotation derived *only*
  from its own `location`/`rotation` parameters, defaulting to `(0,0,500)` and `(-25,0,0)`. It never
  reads the viewport client. It is also truthful.

Neither endpoint is wrong in isolation. The caller's reasonable model — "point the camera, then
capture" — spans two endpoints that were never wired together, and nothing in either response says so.

**Fix:** add an opt-in `useViewportCamera: true` to `capture_camera` that seeds `Loc`/`Rot` from
`ActiveLevelViewport()`. Opt-in, not default, because the existing `(0,0,500)` / `(-25,0,0)` defaults
are load-bearing for existing callers. Tracked as task #21.

### The pattern behind this — third silent success in one session

Named here because the author spotted it as a class, not an instance. There are **two distinct
sub-classes**, and they need different fixes:

| sub-class | example | why it misleads | fix |
|---|---|---|---|
| **(a) accepted-then-ignored** — one endpoint takes an input it does not use | `set_property` on a `TArray`; `set_variable_default` wiping on a missing value; `MinLOD` without its `bOverrideMinLOD` gate | the endpoint is *wrong*: it reported success for work it did not do | reject the input, or do the work and say so (`overrideFlagWritten`) |
| **(b) composition gap** — two endpoints are each truthful, but do not share the state the caller assumes they share | `set_viewport_camera` → `capture_camera` | no single endpoint is wrong; the *model spanning them* is | cross-reference in the response, or wire them with an opt-in |

Sub-class (a) is what the audit hunted and largely closed. Sub-class (b) is invisible to any
single-endpoint audit — including the 111K-line self-audit — because every endpoint involved passes
its own test. It can only be found by *using* the bridge, which is why this file exists.

**Design rule going forward:** when endpoint A produces state that endpoint B could plausibly consume
and does not, B's response must name what it actually used. `capture_camera` should echo
`cameraSource: "explicit" | "viewport" | "default"` so the composition gap is visible in the response
rather than in the rendered image.

---

## Not MifBridge, but adjacent

**`retoc list` emits chunk hashes, not paths.** There is no path-level listing of a built container,
so "did my asset actually get packed?" can only be answered by `retoc info` package count plus the
`.ucas` byte size. A byte-identical `.ucas` across a rebuild is the only reliable tell that a staging
step silently dropped files — which is how a missing-`*.umap` bug was caught this session.

---

## What the `targetClass` fix unblocked, as a worked example

The rack's plant visuals call `SetChildActorClass`, which per engine source
(`ChildActorComponent.cpp:517-585`) destroys and recreates the child **unconditionally**. Without a
guard that churns six actors per refresh — the leading suspect for a game-side crash, and the reason
the feature had to be disabled.

The guard needs to read the component's current `ChildActorClass` (a `BlueprintReadOnly` UPROPERTY).
With `targetClass` that is now three calls:

    add_variable_get {graphId, var: "ChildActorClass", targetClass: "/Script/Engine.ChildActorComponent"}
    add_function_call {graphId, class: "/Script/Engine.KismetMathLibrary", function: "NotEqual_ClassClass"}
    add_branch {graphId}

wired component → `self`, new class → `A`, current class → `B`, result → `Condition`. Built, compiles
0/0, feature re-enabled. That is the shape of fix worth prioritising: one parameter that turns an
impossible job into a routine one.
