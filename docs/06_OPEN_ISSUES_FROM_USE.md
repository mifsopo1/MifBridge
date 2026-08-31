# MifBridge — open issues found in use

## FILE BUGS HERE

**This file is where a session reports a MifBridge defect found while USING it.** Not a commit
message, not a postmortem — those come after, and neither is somewhere a person looks to ask "what is
broken right now".

Add a row to the status table below and a numbered section underneath with:

- what you called and what came back, verbatim
- the build (`self_audit` reports `surfaceSignature` and the endpoint count; it does NOT report a git
  hash, so say so rather than guessing which commit you were on)
- the asset and project, so it can be reproduced
- whether anything was SAVED, because that decides whether work is at risk
- a minimal reproduction if you have one

Then say so in the session so it is not only in a file. A crash report gets fixed the same day; a
report in a commit message gets found in a month.

Related files, so nothing is filed in the wrong one:

| file | what belongs in it |
|---|---|
| `06_OPEN_ISSUES_FROM_USE.md` (this one) | a defect found in use, open or recently closed |
| `01_POSTMORTEMS.md` | the root cause AFTER a bug is understood, and how to avoid the class of it |
| `02_GOTCHAS.md` | engine behaviour that is not a bug but will bite the next person |
| `tools/FEATURE_PARITY_SPEC.md` | a missing capability, rather than something broken |


**Provenance:** written by the plugin author 2026-07-29 during a long BotanistExpansion session, from
actual use rather than from reading the code. Every item was re-tested by the author against build
`Jul 29 02:56` (211 endpoints). Reproduced here verbatim below the status table, because a report
written from *use* catches a different class of defect than an audit written from *source* — every
issue in section 1 and 3 survived the 111K-line self-audit in `07_SELF_AUDIT_FINDINGS.md` unnoticed.

An earlier draft of the author's file listed three issues the 02:56 build had already fixed. That
waste is why the "FIXED — do not re-file" section below is kept rather than deleted.

---

## Status

> **This table is a SNAPSHOT, not the current state.** It was written against the
> **2026-07-29 11:21 build (220 endpoints)**; the surface is now 434 own endpoints. Every row in it
> was verified by a live call at the time, so nothing here is wrong about *then* - but a table headed
> "Status" reads as a claim about NOW, and after a month it is not one.
>
> **The numbered sections below are authoritative**, and several carry a later verification than this
> table does. Two were re-checked on 2026-08-31: issue 14 is fixed AND regression-tested
> (test_ported_anim T574), and issue 21's misleading `export_asset` comment is corrected in the
> source with the export-path contract now asserted by test_safety_gate T632. Read the section, not
> the row.

Author's own priority ranking, "by what actually costs me time now".

| rank | issue | status |
|---|---|---|
| 12 | Eight endpoints reported success while doing something else (source hunt) | **FIXED + verified** 2026-08-26, 108 runs / 54 suites / 0 failed — see section 12; `edit_container` swap no-op, `add_variable`/`set_variable_type` unvalidated `scope`, `draw_debug shape:"string"`, `snap_actors_to_ground` discarded move result, `reparent_blueprint` discarded compile verdict |
| 11 | `landscape_info` vs `diagnose_landscape` disagree on component counts (World Partition proxies) | **FIXED + verified** 2026-08-26 — `landscape_info` now counts streaming proxies matched on `LandscapeGuid` and reports `proxyCount` / `proxyComponents` / `totalComponents` / `componentScope`; both endpoints now agree on 256 for the same world |
| 1 | Parameter drift (`path` dropped from `connect_pins` / `disconnect_pin`) | **FIXED + verified** — `path` accepted on `connect_pins`, `disconnect_pin`, `reconnect_pin`; `self_audit` now emits `surfaceSignature` / `paramSignature` |
| 2 | `list_components` returns empty for a cooked parent | **FIXED + verified** — `BP_PlantPot` returns 12 components (was 0), `targetKind:"cookedClass"`, each with a reason and `route` |
| 3 | `get_property` returns bools as strings | **FIXED + verified** — see the split below; `list_object_properties` now emits `typed` |
| 4 | No `describe_endpoint` | **FIXED + verified** — 10/10 previously-mis-reported endpoints now `params_declared` |
| — | No image/asset import endpoint | **FIXED + verified** — `import_texture` (file + base64), `import_asset`, `reimport_asset`, `set_texture_settings` |
| — | No icon / thumbnail render endpoint (§6) | **FIXED + verified** — `render_thumbnail` 128×128 PNG in 46 ms; plus `write_thumbnail_texture`, `set_asset_thumbnail`, `thumbnail_capabilities` |
| — | `set_viewport_camera` does not reach `capture_camera` (§7) | **FIXED + verified** — `useViewportCamera` opt-in; `cameraSource` echoes `default`/`explicit`/`viewport` |
| — | **No way to CREATE a DataTable asset (§8)** | **VERIFIED 2026-08-26** — `create_datatable {path, rowStruct}` exercised against a running editor: creates the asset, reports `dataTablePath` / `rowStruct` / `rowCount`, and the result is visible to `list_datatables` and readable by `read_datatable`. Regression: `tools/test_datatables.py`, 23 checks |
| — | `CallArrayFunction` wildcards (§5) | **RECONSTRUCT HALF VERIFIED FIXED 2026-08-26** — a wildcard `TargetArray` resolves on connect and SURVIVES `refresh_node`, which §4c of the gotchas nominates as the durability proxy. Reproduction: `tools/test_array_wildcard_durability.py`, 11 checks. The COOK half is still unverified — a cook cannot be run from the bridge. See §5 |
| — | **`add_enum_value` added a junk entry under the wrong name and said ok (§10)** | **FIXED + verified 2026-08-26** — a duplicate display name passed the engine's own guard, the entry was appended, `SetEnumeratorDisplayName` silently declined it, and the response reported `NewEnumeratorN`. Now reads the applied name back and REMOVES the appended entry if it does not match. 32 checks |
| — | `list_enum_values` threw away the only meaningful name a user enum has (§10) | **FIXED + verified 2026-08-26** — it emitted only authored names, which on a user-defined enum are always `NewEnumeratorN`. Now also returns `entries[]` pairing each with its display name; `values[]` is unchanged for existing callers |
| — | **remove then recreate a WidgetAnimation of the same name CRASHED the editor (§9)** | **FIXED + verified 2026-08-26** — the removed UObject stayed alive holding its name; `remove_widget_animation` now frees it and reports `objectNameReusable`, `add_widget_animation` refuses a held name instead of asserting, and `set_widget_animation_range` removes the need for the destructive sequence. PM-010, 43 checks |
| — | UMG WidgetAnimation authoring was unavailable | **FIXED + verified 2026-08-25** — `add_widget_animation`, `add_widget_animation_track`, `set_widget_animation_keys`, `remove_widget_animation*`, `rename_tree_widget`. All three animatable properties reachable |
| — | `set_widget_animation_keys` could only ever animate translation | **FIXED + verified 2026-08-25** — the C++ supported RenderOpacity and ColorAndOpacity all along; the MCP tool never exposed `property` and the sibling docstring said they did not exist |
| — | Macro discovery used a hardcoded library list | **FIXED** — discovered from the asset registry instead (94ae2f5) |
| — | A pin pointer is unsafe across `BreakPinLinks` | **FIXED** — four sites audited and fixed (cdcd8d6) |

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

## 5. `CallArrayFunction` wildcards — the reconstruct half is fixed; the cook half is unproven

**Original report.** `Array_Find` / `Add` / `Clear` / `Contains` connect and compile, but the pin
stays wildcard, the node is reconstructed on save/cook, and the containing function then fails to
compile during the cook and is **stubbed** — so the editor says fine and the shipped game silently
does nothing. The triage note asked for a reproduction before any fix was attempted.

**2026-08-26 — the reproduction exists and the reconstruct half PASSES.**
`tools/test_array_wildcard_durability.py`, 11 checks, against the 285-endpoint build:

| step | observed |
|---|---|
| `Array_Length` spawned, unconnected | `TargetArray` is `wildcard[array]` |
| int-array variable connected to it | resolves to `int[array]` |
| `refresh_node` (the reconstruct) | **still `int[array]`**, and the link survives |
| `compile` | 0 errors |

This is what §4c of `02_GOTCHAS.md` already claimed — that the cause was the spawned node class and
that it is fixed — so the two documents were contradicting each other on this file's most severe item,
and the gotchas one was right. That is worth more than the fix: **two docs disagreeing about whether
the worst known defect is live is itself a defect**, and it stood for weeks because nobody made the
five-minute reproduction the triage note asked for.

**What is still NOT proven, and why the entry is downgraded rather than closed.** A cook cannot be run
from the bridge, so the failure this report actually describes — stubbed *during cook* — has not been
reproduced or refuted. `refresh_node` is the proxy §4c itself nominates for durability, and it is a
proxy, not the cook. If someone sees a stubbed array function in a shipped build again, this entry is
where to reopen it, and the reproduction above is the starting point rather than a blank page.

A note on the reproduction itself, because it nearly shipped green and meaningless: its first version
read `r["pins"]` and `pin["category"]`, and `get_node` nests pins under `node` and the type under
`type`. So it saw no pins, found no wildcards, and every assertion passed while measuring an empty
dict. It now asserts that a wildcard was actually OBSERVED before asking whether it survived —
a suite that proves nothing is worse than no suite, and this one was written to catch exactly that
class of thing in other people's code.

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

## 8. Cannot CREATE a DataTable asset — three routes, all closed

**Found in use 2026-08-21** (build `Aug 19 2026 17:00`, 239 endpoints) while building the EddieWiki
in-game wiki. Blocks the whole `URichTextBlock` workstream, which needs two DataTables that do not
exist yet:

    /Game/MODS/EddieWiki_P/GUI/DT_EddieWikiTextStyles   row struct RichTextStyleRow
    /Game/MODS/EddieWiki_P/GUI/DT_EddieWikiImages       row struct RichImageRow

Without them a `URichTextBlock` has no style set and no image decorator source, so `<bold>` and
`<img id="..."/>` markup renders literally and the result looks WORSE than plain TextBlocks. Every
other part of that work is already verified reachable: `SetText`, `SetDefaultColorAndOpacity`,
`SetAutoWrapText`, `SetTextStyleSet`, `SetDefaultFont` all resolve through `add_function_call`, and
`DecoratorClasses` is settable through `set_property` and reads back correctly.

**Route A — a dedicated endpoint.** Does not exist. The DataTable surface is rows-only:
`read_datatable`, `write_datatable_rows`, `get_datatable_row`, `delete_datatable_rows`,
`list_datatables`. Nothing creates the asset. Note the bridge DOES create other user types —
`create_struct`, `create_enum`, `create_blueprint`, `create_material`, `create_material_instance`,
`create_material_function`, `create_landscape` — so this is a gap in an otherwise-covered area, not
a deliberate policy.

**Route B — `duplicate_asset` an existing one.** Refused. The project's ONLY `RichTextStyleRow`
table is the game's own `/DDS2Casino/GUI/Tutorials/DT_CasinoTutorial_RichText` (rows
Default / Bold / Image), and it lives under a plugin mount, so:

    duplicate_asset {path:"/DDS2Casino/GUI/Tutorials/DT_CasinoTutorial_RichText", newPath:"/Game/..."}
    -> ok:false  "path required, must start with /Game/"

The `/Game/`-only guard is reasonable for a WRITE destination but it is applied to the **source**
too, which makes every engine-, plugin- and GameFeature-mounted asset uncopyable. `/DDS2Casino/`,
`/ChristmasDlc/` and `/Engine/` are all unreachable as duplication sources today.

**Route C — `import_asset` with an explicit factory.** Resolves the factory but cannot configure it.
`import_asset` accepts `factory` by class name and will find `CSVImportFactory`, but its own
KeyNotes already say per-factory option objects are not exposed:

    "options": "not implemented - per-factory option objects (UFbxImportUI etc.) are not exposed yet"

A DataTable import needs `UCSVImportFactory::AutomatedImportSettings.ImportRowStruct`, which is one
of those option objects. The failure is at least SAFE and not a hang, because `H_import_asset` sets
`Task->bAutomated = true` as a documented invariant, so UE takes the automated branch
(`CSVImportFactory.cpp:246-251`):

    else if (!bHaveInfo && IsAutomatedImport())
    {
        if (ImportSettings.ImportType == ECSV_DataTable && !ImportSettings.ImportRowStruct)
            UE_LOG(LogCSVImportFactory, Error,
                   TEXT("A Data table row type must be specified ... for automated import"));
        bDoImport = false;
    }

i.e. it logs and does nothing. No modal, no editor hang — worth stating explicitly, since a factory
dialog on a synchronous handler would deadlock the HTTP ticker.

### FIXED 2026-08-21 — implemented, built, NOT yet verified against a live editor

`create_datatable { path, rowStruct }` in `MifBridgeUserTypes.cpp`, beside `create_struct` and
reusing its `ValidateNewUserTypePath` plus the shared `ResolveStruct`. Registered in all four
places the 1:1 rule requires: `MIF_DECL` (MifBridgeHandlers.h), `MIF_BIND` (MifBridgeCommon.cpp),
the `describe_endpoint` param + KeyNote table (MifBridgeDescribe.cpp), and the `@mcp.tool` wrapper
(tools/mcp-server/server.py). Two implementation notes worth keeping:

* **`UDataTableFactory` was deliberately NOT used.** Its only behaviour over
  `NewObject<UDataTable>` is `ConfigureProperties()`, which opens a MODAL struct picker — and these
  handlers run synchronously inside the HTTP ticker, so a modal would deadlock the bridge. The
  handler does `NewObject<UDataTable>` + assign `RowStruct` + `FAssetRegistryModule::AssetCreated`.
* **A row struct that is not an `FTableRowBase` child is refused up front**, because such a table
  loads without complaint and only misbehaves when opened in the editor.

`duplicate_asset` separately relaxed: the SOURCE may now be any mounted path, the DESTINATION is
still `/Game/`-only. **`rename_asset` keeps its `/Game/`-only guard on purpose** — renaming a
shipped asset in place is not the same operation as copying one. (An early revision of this change
edited `rename_asset` by mistake, because the guard string is byte-identical in both functions.)

Still to do: exercise it against a running editor, confirm `self_audit` reports 240 endpoints and
`describe_endpoint {name:"create_datatable"}` reports `params_declared`.

### Suggested fix — small, and the pattern already exists

`UDataTableFactory` is trivially drivable and needs no options object
(`Editor/UnrealEd/Classes/Factories/DataTableFactory.h:13-22`):

    UCLASS() class UDataTableFactory : public UFactory
        UPROPERTY(BlueprintReadWrite) TObjectPtr<const UScriptStruct> Struct;
        virtual UObject* FactoryCreateNew(...) override;

So `create_datatable { path, rowStruct }` is a near-copy of `create_struct` in
`MifBridgeUserTypes.cpp`: resolve the `UScriptStruct` by name or path, `NewObject<UDataTableFactory>`,
assign `Struct`, hand it to `IAssetTools::CreateAsset`. Reject a `rowStruct` that is not a
`FTableRowBase` child, and return the created path so `write_datatable_rows` can fill it in the same
script.

**Second, separable fix:** allow `duplicate_asset` to READ from any mounted root while still
requiring `/Game/` (or another writable root) for `newPath`. That alone would have unblocked this
particular case, and it generalises — copying an engine or GameFeature asset as a starting point is
a normal modding move.

**Workaround until then:** create the two assets by hand in the editor (right-click ->
Miscellaneous -> Data Table -> pick the row struct). `write_datatable_rows` can populate them
afterwards, so only the creation step is manual.

---

## 9. Removing then recreating a WidgetAnimation of the same name crashed the editor

**Reported** 2026-08-25 from QOLCrafting_P / `WBP_QOL_DropZone`, animation `ArrowLoop`, crash GUID
`UECC-Windows-2A82EB2E400C3FC119CD1E859837B612_0000`. **Fixed and verified** 2026-08-26.

`remove_widget_animation` returned `{"ok": true, "removed": "ArrowLoop", "remaining": 0}` and the very
next `add_widget_animation` with the same name killed the editor:

```
Fatal error: Obj.cpp line 265
Renaming an object WidgetAnimation ...:WidgetAnimation_0
on top of an existing object WidgetAnimation ...:ArrowLoop is not allowed
```

One missing line. `WBP->Animations.Remove(Anim)` detaches the animation from the array and leaves the
UObject alive under the same outer, still owning the name. The engine's own delete path does the
missing step with the reason in a comment (`AnimationTabSummoner.cpp:823-829`): rename to the transient
package first. `add_widget_animation` had been written by mirroring the CREATE path in that same file,
thirty lines above, and the DELETE path was never read.

Also worth keeping: the remove handler DID verify itself, by re-finding the animation in
`WBP->Animations`, and that check passed the whole time the bug existed. **A read-back that queries a
different structure than the one the next operation will consult proves nothing.** Full write-up in
`01_POSTMORTEMS.md` PM-010.

Fixed three ways, one per failure the report identified: `remove_widget_animation` frees the name and
reports `objectNameReusable` separately from `removedFromAnimationsArray`; `add_widget_animation`
refuses before mutating when the name is held; and `set_widget_animation_range` was added so the
destructive sequence is not needed to change a playback range — which is all the reporter wanted.
Regression: `tools/test_widget_anim_recreate.py`, 43 checks, running the cycle three times because a
name-holding leak usually survives one round.

**The report was excellent and made the fix cheap.** It gave the exact sequence, the assertion text,
the stack, the source-level cause, the data-durability position, and three suggested fixes — all three
of which were implemented. That is the standard to file at.

## 10. User-defined enums: a write that lied and a read that lost the answer

Both found on 2026-08-26 by hunting the enum family, which `tools/coverage_gaps.py` had flagged as
covered by no suite. Both are fixed, and `tools/test_enums.py` (32 checks) is the regression.

**`add_enum_value` appended a junk entry and reported success.** Adding a value whose display name is
already taken:

```
add_enum_value {"enum": E, "value": "Common"}      ("Common" already exists)
-> {"ok": true, "index": 3, "displayName": "NewEnumerator3", "name": "NewEnumerator3"}
```

An entry WAS added, the requested name was NOT applied, and the call said it worked. The handler does
guard duplicates with `FEnumEditorUtils::IsProperNameForUserDefinedEnumerator`, and that guard let
"Common" through; `SetEnumeratorDisplayName` then declined the name and returned void, so nothing
noticed.

Fixed by reading the applied name back and, if it differs from the request, REMOVING the entry that
was just appended before failing. The rollback matters as much as the failure: without it a refused
name still leaves a nameless `NewEnumeratorN` behind, and an enum quietly growing junk entries costs
nothing today and corrupts meaning later. Reading the result back is also correct whatever the
engine's guard does next, which a guard-only fix would not have been.

**`list_enum_values` discarded the display names.** It emitted `GetNameStringByIndex()` only. On a
`UserDefinedEnum` the authored names are ALWAYS `NewEnumerator0`, `NewEnumerator1`, ... and the name a
person chose lives in the display name — so the endpoint returned a list of meaningless strings and
gave a caller no way to map a value back to what they named it. `add_enum_value` had always answered
with both, so the write set information the read threw away.

Fixed additively: `values[]` keeps its exact previous contents because callers read it, and the detail
arrives as a new `entries[]` of `{index, name, displayName, value}`.

A note on the fix's own first attempt, because it was wrong in an instructive way. It warned whenever
a display name differed from an authored one — which fires on almost every NATIVE enum too, since UE
prettifies `HitTestInvisible` into "Hit Test Invisible". A warning that fires on `ESlateVisibility` is
noise, and noise is how a real warning gets ignored. The condition is now the enum's CLASS
(`UUserDefinedEnum`), which is the thing that actually makes the authored name meaningless.

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

---

## 11. `landscape_info` and `diagnose_landscape` disagree about the same world, and neither says which question it answered

**Found:** 2026-08-26, overnight hunt, against build `Aug 26 03:34` (286 endpoints). Not from reading
the source — from calling both endpoints on the open world and noticing the numbers could not both be
right.

**What came back, verbatim (same world, seconds apart):**

    landscape_info      -> count: 11 landscapes
                           [0] label "Landscape"  verts 2017x2017  components: 0
                                                  worldMin.z 100   worldMax.z 100
                           [1..10] verts 505x505  components: 64 each        (~640 total)

    diagnose_landscape  -> world "Untitled_1"  proxyCount: 75  componentCount: 896

75 proxies against 11 landscapes, and 896 components against roughly 640. Both report `ok:true`.

**Why.** They iterate different actor classes, and `ALandscape` derives from `ALandscapeProxy`:

| endpoint | iterator | sees |
|---|---|---|
| `landscape_info` | `TActorIterator<ALandscape>` (`MifBridgeLandscape.cpp:820`) | only the PARENT landscape actors |
| `diagnose_landscape` | `TActorIterator<ALandscapeProxy>` (`MifBridgeCooked.cpp:575`, `:941`) | parents **and** `ALandscapeStreamingProxy` |

Under World Partition the terrain's components live on the streaming proxies, so a parent `ALandscape`
genuinely owns **zero** of them. `components: 0` is therefore TRUE and reads as a broken landscape —
and `componentsWithoutWeightmap: 0`, derived from it, carries no information at all in that state.

This is the same shape as the five "current world" helpers that had silently split into two policies
(`02_GOTCHAS.md`): two readers of one fact, each correct about a different question, with nothing in
either response naming the question.

**FIXED 2026-08-26**, right after filing — the fix was small enough to do properly after all. Item 1
below is what shipped; items 2 and 3 shipped with it. Verified live: both endpoints now report **256**
components for the same world.

**The fix worth making,** in order of value:

1. `landscape_info` should count the STREAMING PROXIES' components too, or report them separately as
   `streamingProxyCount` / `proxyComponents`. A caller asking "is this landscape healthy" must not be
   told 0 for a 2017x2017 terrain.
2. Whichever it does, it should SAY which it counted — one word (`"scope": "actorOnly"` versus
   `"includingProxies"`) removes the whole ambiguity.
3. Suppress or annotate `componentsWithoutWeightmap` when `components` is 0, since a ratio out of
   nothing is not a diagnosis.

**Also noticed while there, and NOT chased:** every landscape reports `worldMin.z == worldMax.z`
(100/100 on the first, 0/0 on the others), i.e. a zero-height extent. That may be correct for a flat
template landscape, and this world is `/Temp/Untitled_1` rather than a real DDS2 map — but it is worth
one measurement against `DDS2_Landscape_IslaSombra` before trusting the Z bounds for anything.

---

## 12. Five endpoints that reported success while doing something else

**Found:** 2026-08-26 by a ten-agent parallel read of all 49 handler files (~51k lines) against the
eight patterns that had already produced real bugs here, with every candidate handed to a separate
agent whose job was to refute it. **73 candidates, 26 refuted, 28 survived — and of the survivors I
checked by hand, 5 were real and 2 were false positives.** The refutation pass helps; it does not
replace reading the source. Both false positives were the same mistake: not reading the whole
response block before declaring a value hidden.

Not found in use, so strictly this is a source audit rather than a use report — filed here anyway
because they are defects a user would eventually hit, and this is where someone looks to ask what is
broken.

**1. `edit_container` — swapping an element with itself.** Both range checks accept
`index == swapWith`, `FScriptArrayHelper::SwapValues(3,3)` does nothing, and `changed` was hardcoded
true for `swap` because a structural op cannot be verified by counting. So the call reported
`changed: true` **and dirtied the package** (`Modify` + `PreEditChange` + `PostEditChange(ArrayMove)`)
having moved nothing. Now reports `changed: false` with a note and mutates nothing — matching
`set_variable_type`, which already answers a same-type request that way.

**2. `add_variable` and `set_variable_type` never validated `scope`.** Both did
`Scope.Equals("local")` and treated **everything else** as member, so `scope:"loca1"` silently created
a MEMBER variable — and `add_variable` then echoed the request back as `scope: "loca1"`. The
documented values are `member|local`. Both now validate, and the response reports the RESOLVED scope
rather than what was asked for.

**3. `draw_debug` with `shape:"string"` drew nothing and said `drawn: true`.** `DrawDebugString`
walks `GetPlayerControllerIterator` and only draws where a controller has BOTH `MyHUD` and `Player`
(`DrawDebugHelpers.cpp:613-630`). An editor world has no such controller, so the loop body never
runs; the function is void, so nothing could tell. Every **other** shape goes through the world's line
batcher and renders in the editor viewport, which is why only this one was wrong. Now refused with an
explanation that PIE is where it would work.

**4. `snap_actors_to_ground` counted actors it had not moved.** `SetActorLocation` returns whether it
moved and the result was discarded while `++Snapped` ran regardless; it returns false without moving
when the actor has no root component. Worse, the `moved[]` entry was built **before** the move, so a
refused move was listed with a `toZ` it never reached. Counts are this endpoint's entire product, and
it already separates `missed`, `skippedGround` and `missedUnderDeepStack`. Now `moveRefused` is
counted separately and `moved[]` records what happened rather than what was intended.

**5. `reparent_blueprint` discarded the compile verdict.** It runs a full
`FKismetEditorUtilities::CompileBlueprint` (void) and reported `changed: true` regardless — so a
reparent that BROKE the blueprint looked identical to one that worked. Reparenting is the operation
most likely to break one: an override whose function the new parent no longer declares, a variable or
component name that now clashes. `Blueprint->Status` had the answer all along. Now reports
`compileStatus` / `compiled`, and on error explains the likely cause and how to reverse it.

**Status: all eight BUILT, verified live, and each covered by a test.** 108 runs across 54 suites,
0 failed, 0 took the editor down. Two of the verifications proved the bug was reachable rather than
theoretical: asking `create_function` for an input named `then` really did come back as `then1`, and
`batch` really did report another endpoint's parameter count as its own.

`tools/test_edit_container.py` is new: that endpoint had no suite at all, which is how (1) survived.

**Three more from the same hunt, verified the same way:**

**6. `get_inherited_component`'s available-components list is capped at 80 and ORDER-BIASED.**
`EnumerateBlueprintComponents` fills in three sections — own SCS, then the parent's SCS, then the
native CDO — and checks its `HasRoom()` gate in every one. Section 1 spends the whole budget first, so
a blueprint with 80 or more of its OWN components produces a list that **structurally cannot contain
an inherited or native row** — while the note asserted "list_components on this blueprint returns the
same set". It does not: `list_components` passes Cap 0 and really is uncapped. That note appears on the
error path whose entire purpose is to stop a caller guessing what exists. Now reports
`availableComponentsTruncated`, and when capped says so and points at `list_components`.

**7. Batch ops' parameter guards were filed under the name `"batch"`.** `RejectUnknownParams` attributes
each accepted-key list to `GMifCurrentEndpoint`, and the only writer of that global is `RunEndpoint` —
which `batch` deliberately does not recurse through. So every op's key list was recorded against
`"batch"`, and `TMap::Add` REPLACES, so batch's own five-key entry was destroyed by whichever op ran
last; `self_audit` then reported batch's `observedParamCount` as some other endpoint's count. The op
lost out too: anything only ever exercised through batch never got an entry of its own, so
`describe_endpoint` kept answering `params_not_declared` for a guard that had demonstrably just run —
which is exactly what the runtime-observed branch exists to prevent. `batch` now sets the current
endpoint around each op and restores it afterwards.

**8. `create_function` discarded the pins it actually created.** It calls
`CreateUserDefinedPin(..., bUseUniqueName=true)`, which RENAMES on collision and returns the pin it
made — and the return value was thrown away. Ask for a parameter named `then` (the entry node's own
exec pin) or name two inputs the same, and you get a differently-named pin with no way to learn it,
then fail later trying to wire the name you asked for. The response reported `inputs: N` / `outputs: N`
— counts, which imply the names were honoured. Now reports `inputNames` / `outputNames` as actually
created, plus `pinsRenamed` when they differ. Same shape as `GenerateNewComponentName` in
`add_component`; that makes three instances of "an engine add takes a name and hands back a different
one" in unrelated subsystems, which is worth treating as a standing suspicion rather than three
coincidences.

---

## 13. Four more from the same hunt — A, B and C now FIXED (2026-08-28 update); O remains declined

**UPDATE 2026-08-28.** This section sat as "verified, not yet fixed" for two days after being queued
behind section 12. Re-checked against the current source rather than assumed still open, because a
doc that claims something is broken after it has been fixed is worse than no doc at all — a reader
acts on the claim, not the code. A, B and C were ALL fixed in commit `9525ce5` (2026-08-26,
"fix(silent-success): six endpoints that reported success while doing something else") — the same
commit that closed section 12's eight, just never reported back here. All three re-verified live
against the current build, not just read in the diff:

- **A**: `MifDeferToNextTick` (MifBridgeCommon.cpp:1421) is exactly the "one helper that re-arms the
  guard inside the lambda" this section asked for — it wraps every deferred call in its own
  `TGuardValue<bool> UnattendedGuard(GIsRunningUnattendedScript, true)` before running the real work.
  Confirmed at all five real call sites this section named (new_level, load_level, and the three
  MifBridgeStreaming.cpp verbs — line numbers have since shifted with file growth, but the same
  logical sites), not assumed from the helper's existence alone.
- **B**: `rename_event_dispatcher` now reads back both halves and fails loudly naming which one moved,
  instead of asserting `true` over two engine calls that answer nothing. Regression: `tools/
  test_components_dispatchers.py` T325, via `scratch_confirm.py`'s real success path.
- **C**: `create_enum`'s `values[]` loop now checks `SetEnumeratorDisplayName`'s return value and
  warns per entry instead of discarding it. This fix itself had ZERO test coverage until today, found
  while updating this file rather than trusted from the commit message alone — `tools/test_enums.py`
  T301 now drives `create_enum`'s OWN `values[]` path directly (a clean list, then a genuine
  duplicate), confirming the duplicate keeps its generated name and the response carries a warning
  naming it, both from the write's own response AND an independent read-back.

Original text below, preserved for the reasoning; do not re-file A/B/C.

**A. Every DEFERRED engine call escapes the modal backstop.** The most important one, because it is a
hole in the safety net rather than in one endpoint. `RunEndpoint` runs each handler under
`TGuardValue<bool>(GIsRunningUnattendedScript, true)` — and a TGuardValue **restores on scope exit**.
Six handlers schedule their real work with `SetTimerForNextTick` and answer immediately, so the
engine call runs on a LATER tick with the guard already destroyed: `MifBridgeWorld.cpp:141`
(new_level), `:219` (load_level), and `MifBridgeStreaming.cpp:655`, `:787`, `:1198`. §8 of
`02_GOTCHAS.md` already records that `AddLevelToWorld` opens `FScopedSlowTask::MakeDialog` and
`LevelAlreadyExistsInWorldWarning`, and `FEditorFileUtils::LoadMap` can raise save prompts. A modal on
the game thread stops the HTTP ticker — PM-011, the worst failure this server has. Nothing caught it
because all six endpoints are on the audit harness DENY list and no suite has ever driven them. Fix as
ONE helper that re-arms the guard inside the lambda, not five copies.

**B. `rename_event_dispatcher` asserts a rename it never checks.** It writes
`renamedSignatureGraph: true` and `renamedDelegateVariable: true` as literals.
`FBlueprintEditorUtils::RenameMemberVariable` is void and early-returns silently when the variable is
absent or the names match — which `rename_variable`'s own comment states, and which is why THAT
endpoint reads back. `RenameGraph` gives no answer either. So a half-rename is reportable as a full
one, and the comment directly above the call says "BOTH halves, or the dispatcher breaks". The
remover I added last night verifies both halves; the renamer does not.

**C. `create_enum` guards on the wrong predicate, so display names can silently keep
`NewEnumeratorN`.** It pre-checks with `FEnumEditorUtils::IsProperNameForUserDefinedEnumerator`, which
validates the AUTHORED name — but for a `UUserDefinedEnum` the authored names are always
`NewEnumeratorN`, so that check effectively always passes. The real gate inside
`SetEnumeratorDisplayName` is `IsEnumeratorDisplayNameValid`, which rejects a duplicate DISPLAY name;
its bool return is discarded. Same class as the `add_enum_value` bug fixed on 2026-08-25, which was
closed by reading the applied display name back.

**O. remove_pin cannot remove a same-direction duplicate - the exact case its duplicate branch is for.**

The branch at MifBridgeNodes.cpp:2051 exists to clean up two pins sharing a name, which is the residue
an add_pin crash leaves behind. It resolves pins through identities rather than raw pointers, correctly,
because BreakPinLinks can reconstruct a node underneath you. But ResolvePin (MifBridgeCommon.cpp:3952)
matches on (NodeGuid, PinName, Direction) and returns the FIRST pin satisfying it.

For two GENUINE duplicates - same node, same name, same direction - every captured ref is byte-identical
to KeepRef. So ResolvePin(Ref) and ResolvePin(KeepRef) both return the same first pin, the
`if (Pin == ResolvePin(KeepRef)) continue;` guard fires on every iteration, and Removed stays 0. The
second duplicate is unreachable through an identity ref by construction. Only a CROSS-DIRECTION pair -
an input and an output sharing a name, which is not really a duplicate - has a differing Dir and can
actually be deleted.

FIXED ALREADY: the response no longer lies about it. `removed` now reflects whether anything was
removed, and a duplicateNote says plainly that the pin is still on the node.

UPDATE, after trying to reach it rather than assuming. THE BROKEN PATH IS NOT REACHABLE THROUGH THIS
BRIDGE AT ALL. Three routes to a same-name same-direction pin were tried and every one is blocked:
  - add_pin on the Entry path passes bUseUniqueName TRUE, so a second pin of the same name is renamed.
  - the sibling Return-node route (FinalName is uniquified against Results[0] only, then applied to
    every sibling with bUseUniqueName FALSE) would produce one if a SIBLING already held the name -
    but reaching that asymmetry needs a second Return node, and there is no add_node or
    add_return_node endpoint to place one.
  - the cross-direction case (an input and an output sharing a name) is renamed too: asking for an
    input 'Same' then an output 'Same' yields 'Same' on the entry and 'Same1' on the result, and
    remove_pin then takes branch A (kind userDefined), not the duplicate branch.

So Matches.Num() > 1 fires only on state this bridge cannot create: the add_pin crash residue its own
comment names, or hand-editing in the editor. That is real - the comment was written from a real
incident - but it is much rarer than a reachable API path, and it lowers the severity considerably.

DECLINED on that basis. The reporting fix stands and is the part that matters: if the state ever does
arise, the caller is told the truth instead of being told the duplicate was cleaned up. Writing an
untestable addressing fix into pin manipulation - which has taken the editor down before - to serve a
path the bridge cannot produce is a bad trade. If someone later adds an add_node endpoint, the route
opens and this should be revisited.

NOT FIXED: the addressing itself, deliberately. Reaching the real case needs two pins with the same
name AND direction on one node, which this bridge cannot create on demand - so a fix cannot be tested
here. Pin manipulation across BreakPinLinks has taken the editor down before (see the pin-pointer audit
in the git history, four sites). Writing an untestable fix into that is how the crash happens. Whoever
takes it will need to address pins by INDEX into the live Node->Pins array rather than by identity,
re-validating after every BreakPinLinks, and will need a way to manufacture the duplicate first.

**P. An `in:` comment advertised a parameter the handler rejects (capture_viewport).**
The comment read `in: { path?, viewport? }`; the accept-list is `{path, name, file}`. A caller
following the documentation got `unrecognised parameter 'viewport'` and no capture. There is no
viewport selection in the handler - it captures whichever viewport the editor is drawing, which is
what `viewportType` in the out: block reports rather than something you pick. FIXED by correcting the
comment, the same way the duplicate_actors `rotationOffset?` line was.

Found by comparing every `in:` block against its handler's RejectUnknownParams accept-list. That
comparison is worth keeping in mind as a lens: the accept-list is the authoritative set of keys a
handler admits, so anything the docs advertise outside it fails hard at runtime.

THE SCAN IS NOT WORTH AUTOMATING AS-IS. It produced 8 candidates and only 1 was real. The rest were
nested parameters - spawn_many's `label` and create_landscape's `weight` live inside items[] and
layers[], and the accept-list deliberately guards TOP-LEVEL keys only - or prose caught by the
lookback, including a `targetClass` mentioned in a comment about a different endpoint entirely. A
checked-in version would need to understand nesting before it earned its place.

**Q. "Nothing is saved" has a hole: endpoints that write to disk as their PURPOSE.**

The audit harness guarantees that a run saves nothing. It enforces that by DENY-listing save_blueprint,
save_level, save_level_as, save_dirty_packages, save_all, save_asset and save_package, and by stripping
`save` from every payload. That covers everything NAMED like a save.

It does not cover endpoints whose function IS to write a file. import_texture and
write_thumbnail_texture create .uasset files on disk by definition - there is no in-memory-only mode to
ask for. So their suites left 94 real assets in the project content tree
(Content/_MifTex, 47 files; Content/_MifThumb, 47 files) during the overnight run of 2026-08-26,
between 03:58 and 04:30. Scratch names, scratch paths, but real files in a real content folder.

Nothing was corrupted and nothing of Andre's was touched - the paths are /Game/_MifTex and
/Game/_MifThumb, which nothing else uses. The problem is that the guarantee was believed to be
absolute and is not, and the belief is the dangerous part: it is why nobody was looking.

RESOLVED for the existing files, 2026-08-26. Andre authorised deletion and all 98 assets were removed
THROUGH THE EDITOR via delete_asset rather than by deleting .uasset files off disk - the editor was
running and holding references to them, and pulling files out from under it is how you get a confused
editor and a half-populated Asset Registry. Both directories are gone and Content is clean.

**OBSERVED 2026-08-26 (late), and it did NOT reproduce.** A full two-pass regression - 128 runs across
64 suites, so both of those suites ran twice - wrote ZERO files anywhere under Content. Both
/Game/_MifTex and /Game/_MifThumb exist as EMPTY directories and nothing was created in them.

This is recorded as an observation, NOT as a fix, because the mechanism is not understood. Two full
passes not reproducing it is evidence, but the original incident was also an overnight run and the
difference between then and now has not been identified - it may be a path that only triggers under
conditions this run did not hit. Do not close this item on the strength of one clean run; the useful
next step is to find out WHY files appeared before, not to assume they no longer will.

The hole as originally described - the next run recreates the files. Options:
  - have the harness sweep /Game/_MifTex and /Game/_MifThumb at the end of a run;
  - point those two endpoints at a path outside Content for test purposes, if they accept one;
  - accept it and document it, so the next person reading 'nothing is saved' knows the exception.

The wider lesson is about how the DENY list is CONSTRUCTED. It is a list of names. Anything that has
the effect without the name passes straight through - which is the same shape as every other defect
found in this project: the check tests a proxy for the real question rather than the question.

**R. list_blueprints truncated at 5000 without saying so.** FIXED.
The handler ends its loop with `if (Arr.Num() >= 5000) break; // safety cap` and then reported count
and the array with no indication it had stopped early. There is no limit PARAMETER to blame - the
refusal for one even says so - which makes the answer look complete. Someone searching for a blueprint
that sorts after the 5000th would be told it does not exist.

Not reachable on this project today: 1744 blueprints. Fixed anyway, because a latent silent truncation
starts lying on a day nobody is watching for it. `truncated` and a note are emitted only when the cap
is actually hit, matching the convention elsewhere of omitting a field rather than sending false.

VERIFIED ONE-SIDED, and worth saying: the negative case is confirmed live (under the cap, the key is
absent) and the code path is confirmed by its string in the DLL - but the POSITIVE branch cannot be
exercised without 5000 blueprints, so it is verified by reading, not by running.

The sweep that found it produced almost nothing else, which is the useful part of the report. Of 13
endpoints accepting a cap, three appeared to truncate silently and all three were false positives -
list_transactions carries queueLength (824 next to a returned 1), diagnose_landscape carries proxyCount
(67 next to 1), and diagnose_landscape_draws caps a sub-list that was empty. Each reports its true
total under a field name the scan was not looking for. A second hard-coded cap in add_macro_instance
turned out to be a line number my regex mistook for a bound.

**S. describe_endpoint hid capability from the callers most likely to need it.** FIXED.

Five rows in the describe table listed fewer keys than their handler actually accepts. Two of them hid
whole capabilities rather than aliases:
  set_material_parameter   omitted textures, switches, association, index
  add_foliage_instances    omitted foliageType/type - an entire second mode of the endpoint
  set_spline_points        omitted skipPostEditChange, which its OWN handler documents as REQUIRED on
                           blueprints that rebuild their own spline
  add_cast                 omitted pure
  reparent_blueprint       omitted parentClass/path (aliases; the primary spellings were listed)

This matters more than a stale comment. describe_endpoint is the MACHINE-READABLE contract - what an
agent consults before deciding whether an endpoint can do something. A key missing from a comment costs
a human one read of the source; a key missing from this table means the capability does not exist for
anyone who discovers by asking.

The bridge cannot catch this itself, and says so in its own coverage note: staleTableRows detects only
the opposite direction, and a guard with no row 'leaves no runtime trace and is NOT detectable from
inside the DLL'. True from inside; trivial from outside, where the source and the running build can be
compared. tools/audit_describe_drift.py now does exactly that, with a self-check that refuses to report
a clean result if either the source walk or the live query has stopped working.

Same shape as the param-reach backlog, one layer up: there the capability existed and no MCP tool could
SEND it; here it existed and no caller could FIND OUT it existed.

**N. A discarded-bool sweep: 299 candidates, and the scan cannot resolve overloads.**

RESOLVED 2026-08-26. All 28 candidates surviving the conventional-discard filter were triaged with the
overload actually selected read first, then every REAL verdict attacked by a second pass. The result:

  11  NOT_BOOL  - the selected overload returns void. The name-based index matched a DIFFERENT overload.
   6  HANDLED   - returns bool, but the code pre-checks the failing condition or reads the result back.
  10  BENIGN    - a false return has no consequence worth reporting.
   1  claimed REAL, and refuted on the adversarial pass.
   0  survived.

So FDataTableEditorUtils::RemoveRow (issue L, fixed) was the only genuine discarded bool in the
codebase. This is a clean negative and worth recording as one: the next person to notice a bare
engine call here does not need to re-run this.

The headline number is the lesson. 11 of 28 - nearly half - were the scan failing to resolve overloads,
exactly as the caveat above predicted. A name-based index over C++ is a candidate generator and nothing
more.
Indexing every bool-returning engine function from the 5.3 headers (7820 of them) and intersecting
that with every call MifBridge makes as a BARE STATEMENT gives 299 candidate sites where an engine
answer is thrown away. That is how issue L was found.

THE SCAN IS NAME-BASED AND THEREFORE CANNOT TELL OVERLOADS APART, which inflates the number badly.
Worked example, checked before it was believed: SetScalarParameterValueEditorOnly appears in the
index because the FName overload returns bool - but create_material_instance and set_material_parameter
call the const FMaterialParameterInfo& overload, which returns VOID. No bool is discarded there. Both
sites also pre-check with GetScalarParameterValue and record unknown names, so they were never the
bug they looked like. Do not treat a hit as a defect without reading the overload actually selected.

Most of the rest are conventional discards nobody checks: Modify(), MarkPackageDirty(), Destroy().
The subset worth triaging is the one where a false return means a mutation did not happen while the
endpoint reports it did - candidates seen so far include RemoveTrack and RemovePossessable
(MifBridgeWidgets.cpp:923/931), RemoveVariable (MifBridgeUserTypes.cpp:294), ChangeVariableDefaultValue
(:187), SetPropertyValue (MifBridgeDataTables.cpp:244), SetDisplayLabel (MifBridgeWidgets.cpp:1189),
SetActorRotation (MifBridgeWorld.cpp:657 - note SetActorLocation's discarded bool was already fixed in
snap_actors_to_ground, so this is the same shape) and SetRootComponent (MifBridgeAuthoring.cpp:1239).

A note on how this nearly went wrong. The first version of the sweep ran one grep of the whole engine
tree per call, and most greps hit the timeout - which an `except Exception` turned into 'no match'. It
printed a confident 'total: 0'. That is the same defect this project keeps hunting, written into the
tool doing the hunting. The working version indexes once and intersects, and asserts a known-positive
site is present before any result is believed.

**L. FDataTableEditorUtils::RemoveRow returns a bool that is discarded at both call sites.**
Found by scanning for engine calls used as bare statements and then checking their return types in the
engine headers - the same shape as the SetEnumeratorDisplayName bug fixed this morning.

MifBridgeDataTables.cpp:733 (delete_datatable_rows):
    FDataTableEditorUtils::RemoveRow(Table, Key);
    ++Deleted;
Deleted is incremented unconditionally and reported as `deleted: N`. A removal that returns false
still counts. The row is checked to EXIST first (FindRowUnchecked), so this is not easy to hit, but
the count is asserted rather than observed, which is the defect class this project keeps finding. The
response does also carry rowCount read back from the table, so a caller CAN cross-check - `deleted` is
simply the number that would be wrong.

MifBridgeDataTables.cpp:642 (write_datatable_rows) is the more interesting one. It is a cleanup path:
a row was added, populating it failed, and RemoveRow is called to avoid leaving a half-written default
row behind - the comment says exactly that. If the cleanup fails, the half-written row survives and
the warning the caller receives mentions only the conversion failure, never the row left in the table.

**M. A comment states an engine function returns void when it returns bool.**
MifBridgeUserTypes.cpp, just below :1068: "SetEnumeratorDisplayName returns void and declines a name it
does not like without saying so". The header says
  static UNREALED_API bool SetEnumeratorDisplayName(UUserDefinedEnum*, int32, FText);
The code around it is CORRECT - it reads the applied name back, which is stronger than checking the
bool - so this is a documentation defect rather than a behaviour one. It matters because these
comments are the documentation, and this one tells the next reader there is no return value to check.

**K. labelNote is written top-level from inside a loop, so all but the last one is lost.**
MifBridgeAuthoring.cpp:306 (spawn_many) and :455 (duplicate_actors) both do
  if (!LabelNote.IsEmpty()) { Out->SetStringField(TEXT("labelNote"), LabelNote); }
inside the per-item loop. SetStringField REPLACES, so spawning twenty actors where five labels were
refused or trimmed reports exactly one note - the last - and gives the caller no hint the other four
happened. They read it as a single oddity rather than a pattern.

What makes this worth fixing rather than shrugging at is why SetActorLabelChecked exists in the first
place. Its own comment says "void API, silent refusal": the engine's SetActorLabel returns nothing and
quietly declines names it does not like, and this helper was written to surface that. So the mechanism
built to stop silent label loss loses label notices silently. Same defect class, one layer up.

The fix is an array rather than a field - labelNotes[], or folding them into the errors[] array
spawn_many already emits per item, which has the advantage of carrying the item index. duplicate_actors
has no errors[] array, so it needs one or the array form.

FIXED - both, and this entry was stale. Checked 2026-08-29 rather than trusted: both
MifBridgeAuthoring.cpp:414 (spawn_many) and :575 (duplicate_actors) now do
`LabelNotes.Add(MakeShared<FJsonValueString>(LabelNote))` inside their per-item loop and emit
`labelNotes[]` only when non-empty - the array form, not folded into an errors[] array. spawn_many's
side is proven live by tools/test_spawn_many.py T545 (three refused labels in one call, three distinct
notes, each naming its own item index, the old single-valued field confirmed gone). duplicate_actors'
side is verified by READING the source, not by a live reproduction - matching this file's own §R
precedent ("verified by reading, not by running") for the same honest reason: the ONLY trigger anyone
has found for a genuine SetActorLabelChecked refusal is a WHITESPACE-ONLY wanted label (found empirically
while writing T545 - newlines, tabs, control characters and long names all get accepted unchanged), and
duplicate_actors' own `Wanted` is built as `SourceLabel + Suffix + N`. Getting that whole concatenation
to be pure whitespace needs the SOURCE actor's OWN label to already be whitespace-only - which the exact
same refusal mechanism prevents it from ever holding in the first place, the same "the broken state is
unreachable through this bridge" shape §O documents for the duplicate-pin case. Reading the source is
therefore the honest verification available here, not a shortcut taken instead of a harder one.

**J. The harness structurally cannot clean up level actors it spawns.**
delete_level_actor requires confirm=true, and scratch_confirm grants confirm only when every path in
the payload lies under /Game/_Mif. A placed actor's path is
/Temp/Untitled_1.Untitled_1:PersistentLevel.StaticMeshActor_UAID_..., which is not scratch by that
rule, so the guard refuses. It is refusing CORRECTLY - it cannot tell an actor in a throwaway
untitled level from one in a real map, and 'no evidence of danger' is not 'evidence of safety'.

The consequence is that every suite touching the level leaks actors for the lifetime of the editor
session. Harmless today because the harness runs in an untitled level that is never saved and is gone
on restart, but it means scene counts drift upward across a long run, and any future assertion of the
form 'the count returns to its baseline' cannot be written.

Two candidate fixes, neither obviously right:
  - teach scratch_confirm that /Temp/<Level> paths in an UNTITLED level are scratch. Narrow and
    truthful, but it widens the one guard that has never yet been wrong.
  - give the harness a sweep that removes actors whose label carries the suite prefix, running
    outside the confirm guard by addressing them some other way. More code, no widening.
Filed rather than chosen, because widening the confirm guard deserves a deliberate decision rather
than being done in passing while fixing a test.

**I. "NOTHING was created" was asserted at every other site too, and now every one has been checked.**
**AUDITED CLEAN, 2026-08-29 - no new defects.** Two foliage sites were corrected earlier because they
promised "NOTHING was created" AFTER real side effects had already happened -
GetInstancedFoliageActorForCurrentLevel had been called with bCreateIfNone=true (so an actor may have
been spawned into the level) and AddFoliageType had already registered a type on it. PM-007 means
there is no rollback that would make the old wording true.

By the time this audit ran the string had grown to 64 occurrences across 12 files - not 31 - since
MifBridgeGeometryScript, MifBridgeWater, MifBridgeStreaming, MifBridgeLevelSnapshots, MifBridgeMetaHuman
and MifBridgeAnimation did not exist yet when this item was filed. Every genuinely distinct call site
(the raw string count over-counts: several are multi-line `Fail(Out, ...)` invocations split across
several `TEXT()` fragments, or the phrase appearing inside a COMMENT describing the already-fixed
foliage bug rather than in a live message) was read in place and checked against the question this
item itself set: does anything before that specific Fail() call Modify(), spawn, register, or otherwise
touch state the failure does not undo?

None do. Three shapes account for every site:
- **Pure parameter/precondition validation** - the large majority. Nothing has been touched yet by the
  time the guard fires (e.g. add_ik_retarget_chain, add_ik_goal, create_asset, create_water_body,
  add_data_layer's early checks).
- **Scratch/transient working state that is thrown away, not the real asset.** Every
  MifBridgeGeometryScript site builds into a `UDynamicMesh` allocated with `GetTransientPackage()` -
  explicitly documented at the top of that file as "scratch working memory for the generator, never the
  asset itself" - and the real asset write happens only after every check has already passed.
- **The call itself is checked, and the checked failure path is the only one reachable.** Water's
  `create_water_zone`/`create_water_body` check `FindActorFactoryForActorClass` and `CreateActor`'s own
  return value before claiming nothing was made; add_data_layer's `CreateDataLayerInstance` failure
  branch is the one site that legitimately has partial state (`Asset` was already constructed) and it
  says so explicitly instead - "The asset was constructed but no instance exists" - never claiming
  NOTHING. add_ik_solver's engine call (`UIKRigController::AddSolver`, both the pre-5.6 class overload
  and the 5.6+ struct-string overload) was read directly in the 5.3 and 5.7 engine trees: both `Modify()`
  the rig only after every rejection check, matching the pattern add_ik_retarget_chain's own comment
  already worked out for the sibling `AddRetargetChain` call (and was already fixed for that call's
  reserved-name edge case).

Worth recording as a finding in its own right, not just a clean bill: the two foliage bugs this item
was filed to hunt for were real, but they were the ONLY two of what is now 64+ sites, both already
fixed before this audit ran. The failure mode is real and worth the systematic check when new families
land (the six files added since this was filed are proof the count keeps growing), but it is not a
common pattern here - MifBridge's habit of validating fully before touching state held up.

**H. `add_anim_node` guards the BLUEPRINT where its comment promises to guard the GRAPH.** The comment
reads "An anim node in a non-anim GRAPH compiles to nothing and is a confusing thing to debug, so
refuse it here rather than let it sit in an EventGraph looking placed" — and the check underneath is
`!Blueprint->IsA<UAnimBlueprint>()`, which is blueprint-level. An Animation Blueprint has BOTH an
AnimGraph and an EventGraph, so `add_anim_node` targeting the EventGraph of a perfectly valid
AnimBlueprint passes the guard and places a node into exactly the graph the comment names. It compiles
to nothing and the response reports it placed.
**FIXED, and this entry was stale.** `MifBridgeAnimation.cpp:696-708` now tests the GRAPH's schema
(`Graph->GetSchema()->IsA<UAnimationGraphSchema>()`), not the owning blueprint's class — shipped in
`3b5b42b` ("fix(anim): add_anim_node could TERMINATE the editor - the guard checked the blueprint,
not the graph"). Live testing found the real failure was worse than this entry's own guess: it is not
a silent no-op ("compiles to nothing"). `UAnimGraphNode_StateMachineBase::PostPlacedNewNode` does a
`CastChecked<UAnimationGraph>(GetGraph())` on the node it was just handed, a failed `CastChecked` is
**fatal** rather than returning null, and the process terminates mid-request with no error response at
all - see PM-013 (`docs/01_POSTMORTEMS.md`). The code comment at the fix site records this in full,
including the exact crash line. Covered by a dedicated live test, `tools/test_anim_nodes.py`, which
reproduces the state-machine-into-EventGraph case against a running editor and asserts the bridge
survives and refuses by name, plus the legitimate AnimGraph placement still succeeding.
This is the "a comment asserting what the code does needs a test, not prose" failure recorded in the
snap_actors_to_ground postmortem, arrived at from the other side: here the comment was right about the
intent and the code was narrower than the comment — until PM-013 forced the gap shut.

**F. `spawn_many` swallowed an unloadable mesh path twice.** The shared mesh is loaded with
`LOAD_NoWarn | LOAD_Quiet`, which kills the engine's own log line, and the assignment in the loop is
guarded by `if (Mesh && ...)`. So a misspelled path produced actors with NO mesh and a response
reporting `spawned: N`. For a modder placing props that is the entire job silently not done. Now
refused up front, naming the object-path format the loader wants.

**G. `spawn_many` accepts `mesh`/`material` for actor classes that cannot use them.** Both are applied
only inside `Cast<AStaticMeshActor>(Actor)`, so specifying a mesh while spawning any other class was
accepted and dropped without a word — the mode-dependent silent-ignore that `tools/audit_mode_params.py`
exists to find. Reported per item rather than failing the call, since the actor itself spawned
correctly and a shared default may simply not apply to that row.

**E. `batch` does not compile a blueprint an op touched only by `nodeGuid`.** `Touched` is filled from
`graphId` or `blueprintId`/`path` and nothing else (`MifBridgeNodes.cpp:2489-2510`), but several ops
address a blueprint purely by node — `rename_event` and `set_function_flags` both take `nodeGuid`
alone. Such an op mutates the blueprint, never lands in `Touched`, and `compileAtEnd` skips it: the
blueprint is left structurally modified and uncompiled while the response reports ok with
`compiles: []`. It is masked whenever the caller passes a top-level `blueprintId` to batch, which is
added to `Touched` at compile time, so it only bites a batch that relies on per-op addressing.
This is the SAME bug that was already fixed once for `path` — the comment right above the tracking
block records that history in its own words. A third addressing form was added later and the tracking
was not revisited.

**D. `add_foliage_instances` says "NOTHING was created" after creating something.**
`GetInstancedFoliageActorForCurrentLevel(World, bCreateIfNone=true)` SPAWNS an
`AInstancedFoliageActor` into the level, and `AddFoliageType` may register a type on it — then the
later failures announce that nothing was created. The FIRST such message (the IFA itself came back
null) is accurate; the ones after it are not. PM-007 means there is no rollback to make them true, so
the message has to change rather than the behaviour.

---

> **Numbering note.** This file has TWO sequences. Everything above is the main one, which reaches
> 23. Everything below restarts at 1 because it was merged in from a separate deployment. So "issue
> 1" is ambiguous and "issue 17" is not - when citing a low number from below, say "Curfew issue N".
> Left as two sequences rather than renumbered: the low numbers are cited from commit messages that
> cannot be edited, and silently moving them would break the trail those messages are for.

# MERGED FROM THE CURFEW DEPLOYMENT (UE 5.7), 2026-08-26

MifBridge is vendored into D:/RoguelikeDealerGame rather than cloned from this repo, so a second line
of development and issue-filing has been running there against UE 5.7 and never reached this file.
That copy is at 230 endpoints to this one's 274. Everything below was hit for real while building a
city in L_City_P and is reproduced verbatim - it is the general-UE5 use case this tool is FOR, and two
of the items are silent-success bugs of exactly the class hunted here all night.

Nothing below has been actioned in this repo yet; each has a spec item.
# Found in use — CURFEW city building, 2026-08-25/26

Filed from a long session laying a street grid, ground, alleys, buildings and props into
`L_City_P`. Every item below was hit for real and cost time; none came from reading source.

## 1. `save_package` on a World Partition map saves the map and NONE of its actors — silently

**This nearly cost a session's work and is the most valuable item here.**

`L_City_P` is a World Partition map with One-File-Per-Actor. Every actor lives in its own package
under `Content/__ExternalActors__`. A script placed 409 actors and called:

    save_package {path: "/Game/CF/Maps/L_City_P"}   -> ok:true

The map package was indeed written. All 409 actors stayed **dirty in memory**, and would have been
lost on the next level reload. On disk the actor count stayed at 223 and nothing had been written
for half an hour. The correct call is `save_dirty_packages {maps:true, content:true}`, which wrote
**418 packages** and took the count to 641.

`ok:true` was accurate — the requested package *was* saved — which is exactly what makes it
dangerous. **Suggested fix:** when `save_package` targets a map that `UWorld::IsPartitionedWorld()`,
return a `note` naming the count of still-dirty external actor packages, e.g.
`"note": "3 dirty external actor package(s) remain — use save_dirty_packages"`. Cheap, and it turns
a silent data-loss trap into a one-line warning.

## 2. No endpoint enumeration — `list_endpoints` 404s

`describe_endpoint` is excellent, but it needs a name you already have. There is no
`list_endpoints`, so discovering what exists means grepping
`Source/MifBridge/Private/MifBridgeDescribe.cpp` in the plugin. That is how `delete_level_actor`
was eventually found, after guessing `delete_actor`, `destroy_actor` and `remove_actor` — three
round trips that a listing would have saved.

**Suggested fix:** `list_endpoints {filter?}` returning names plus the one-line summary
`describe_endpoint` already holds.

## 3. `list_level_actors` defaults to 200 and truncates

Default `limit` is 200. The response is honest — `count:200, matched:239, truncated:true` — but a
caller that reads only `actors` gets a silently short list. This bit a cleanup routine that
reported "cleared 200/200" while 43 actors remained.

Not a defect so much as a sharp edge; the fields to check are there. **Suggested fix:** mention
`truncated` in the `describe_endpoint` summary so it is visible without reading a response first.

## 4. `get_property` cannot reach `UBodySetup::AggGeom`

**RESOLVED 2026-08-26 - not a defect.** Checked live: `BodySetup.AggGeom` works and returns 1689
characters with SphereElems, BoxElems, SphylElems and ConvexElems. The failing calls used `body_setup`,
the snake_case Python spelling rather than the UPROPERTY name, and the bridge already answered:
`property 'body_setup' not found on 'StaticMesh' (did you mean 'BodySetup'?) - list_object_properties
dumps what exists`. It named the answer. Left here rather than deleted because carrying snake_case over
from Python is an easy habit and this will be hit again.

    get_property {objectPath: "...Mesh_Props_Barrier_01", propertyPath: "body_setup"}   -> ok
    ... then AggGeom / aggregate_geom on the BodySetup                                   -> fails

    "BodySetup: Failed to find property 'aggregate_geom' for attribute 'aggregate_geom'"

Neither `AggGeom` nor the snake_case form resolves, so collision primitive counts are unreachable
through the bridge. Worked around by spawning the mesh and reading
`get_actor_bounds(bOnlyCollidingComponents=true)`, which answers "does it collide" but not "with
what shape". Relevant to any question about whether a prop blocks a pawn.

## 5. `spawn_actor_in_level` requires `actorClass` even when `staticMesh` is given

Not a bug, and the error is genuinely good:

    "'actorClass' is required and must name a class (an empty value would silently resolve to
     this blueprint's own class)"

Filed as a **positive example**. It states the requirement *and* the reason the permissive
behaviour would be worse. The Blueprint-class form needs the full generated path
(`/Game/.../BP_Foo.BP_Foo_C`) and that error says so too. More endpoints should read like these.

## 6. `save_dirty_packages` refusing during PIE — also correct

    "cannot save map packages during PIE — stop_pie first (or pass maps=false for a
     content-only save)"

Names the cause and both remedies. Worth keeping as the house style.

## 7. Note on `trace_ground` versus `list_level_actors` during PIE

These read **different worlds**. With PIE running, `trace_ground` hits the PIE world while
`list_level_actors` reports the editor world — which, in a World Partition map with no cells
resident, is empty. The combination reads as catastrophic ("ground exists but zero actors, and
spawns return null") when nothing is wrong at all. Both behaviours are defensible; the confusion
is real. **Suggested fix:** have both echo which world they operated on, the way `capture_camera`
echoes `cameraSource`.

## 8. `save_dirty_packages` cannot commit a DELETED package — and reports it as a failure

Destroying actors in an OFPA map leaves their external-actor packages needing **deletion**, not
saving. `save_dirty_packages` calls SavePackage on a package with no object left in it, which
fails, so:

    save_dirty_packages {maps:true, content:true}
      -> ok:true, saved:0, failed:915
         reason: "save failed (see editor log; still referenced by an in-flight operation?)"

The guessed reason is misleading — nothing was in flight. 915 `.uasset` files stayed on disk with
their actors already destroyed in memory, which World Partition would load back as ghost actors
next session.

`EditorAssetLibrary.delete_asset` does NOT reach them either: external actor packages are not in
the asset registry as ordinary assets, so `does_asset_exist` returns false and `delete_asset`
returns false for every one.

**What works** is the engine's own call, which handles deletions as part of the same pass:

    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)   -> True

That took the level from 1141 actor files to 226 — exactly the pre-existing content — in one call.

**Suggested fix:** have `save_dirty_packages` route through
`FEditorFileUtils::SaveDirtyPackages` / `UEditorLoadingAndSavingUtils::SaveDirtyPackages` rather
than iterating SavePackage itself, so deletions are handled; or at minimum detect a package whose
outer object is gone and report `"needs deletion, not save"` instead of a speculative in-flight
message.

**Sharp edge worth documenting either way:** `does_asset_exist` answers a question about the asset
registry, not about a file on disk. Treating "not in the registry" as "already deleted" made my
first cleanup pass report 915 successful deletions while changing nothing at all.

---

## 14. `set_blendspace_samples` reports invalid samples as added

**FILED WRONG, THEN CORRECTED, 2026-08-26.** The correction is the useful part, so it is kept rather
than tidied away.

### What I claimed first, and why it was wrong

`audit_postconditions.py` flagged the endpoint as "mutates, but nothing in the body reads the result
back". Reading `ValidateSampleData` - which the handler calls right after adding - showed this:

```cpp
if (IsSameSamplePoint(Sample.SampleValue, SampleData[ComparisonSampleIndex].SampleValue))
{ SampleData.RemoveAt(ComparisonSampleIndex); ... }
```

I concluded that samples added by this call were being silently deleted and still reported as added -
filed it, fixed it, wrote a commit message asserting it, and pushed. **That cannot happen through this
endpoint.** `AddSample` calls `ValidateSampleValue`, which calls `IsTooCloseToExistingSamplePoint`,
which calls `IsSameSamplePoint` - the SAME predicate at the SAME threshold. A duplicate point is
refused by `AddSample`, returns `INDEX_NONE`, and lands in `rejected[]`. It never reaches the dedup
pass.

Verified live afterwards: two samples at one point give `rejected: 2, droppedByValidation: 0`.

The mistake was reading ONE function and reasoning forward from it instead of reading the call chain
that leads into it. The engine already guarded the case I thought I had found.

### The real defect, which is next door to the wrong one

`ValidateSampleData` does not only remove. It also marks samples INVALID without removing them:

```cpp
Sample.bIsValid = bAnimationExists && bSampleInBounds && bSampleIsUnique;   // BlendSpace.cpp:36
Sample.bIsValid = ValidateSampleValue(Sample.SampleValue, SampleIndex);    // BlendSpace.cpp:122
```

An invalid sample is **still in `SampleData`**. It counts toward `GetBlendSamples().Num()`, it survives
any position-matching reconciliation, and it contributes NOTHING to the blend. The endpoint reported it
as added with no indication - telling the caller the sample works when the asset says it does not.
That is the same family as every other entry here, and it is what the audit was actually pointing at.

**Fixed** by reporting `valid` on each sample and an `invalidCount` emitted ALWAYS, not only when
nonzero, so a caller can assert on it rather than having to notice a field's absence. The note names
the three causes (`bAnimationExists && bSampleInBounds && bSampleIsUnique`) so a false one is
actionable. `bIsValid` verified in both trees: 5.3 BlendSpace.h:182, 5.7 :194.

The reconciliation against `GetBlendSamples()` from the first attempt is KEPT as belt-and-braces - it
costs nothing and covers samples that arrived by another route - but the code comment now states
plainly that this endpoint cannot trip the deletion path, so nobody re-derives the wrong conclusion.

### The lesson

Two. The obvious one: read the call chain, not the leaf function - the caller may already guard what
the callee handles. The sharper one: I filed, fixed, committed and pushed a finding before testing the
claim against the live editor. One five-second call would have shown `rejected: 2, dropped: 0`.
Evidence FOR a defect deserves the same standard as evidence against one, which is what PM-013 already
says about dismissals.

---

## 15. Curfew's bridge is sitting on MifBlender's reserved port

**FOUND 2026-08-26 while answering a question about something else.** Andre mentioned he had Blender
open and wondered whether it was causing a port problem. It was not causing the problem I was
chasing - but checking it turned up a real collision that would have broken the Blender phase before
it started.

### What is actually bound right now

| Process | Port | Should be |
|---|---|---|
| SDK editor, UE 5.3.2 (`DrugDealerSimulator2.uproject`) | 8791 | correct |
| **Curfew, UE 5.7 (`Curfew.uproject`)** | **8792** | **wrong - this is MifBlender's** |
| Blender | 8793 | it was pushed off 8792 |

8792 is not an arbitrary choice for MifBlender. It is reserved and documented in three places:
`README.md:178` ("the addon binds 127.0.0.1:8792 - loopback only, there is deliberately no
bind-address setting"), `README.md:187` (the `MIF_BLENDER_PORT` default, with a note that 9876 is the
third-party `blender-mcp` addon), and `tools/blender-addon/MifBlender/server.py:66`
("DEFAULT_PORT = 8792  # UE plugin is 8791; third-party blender-mcp is 9876. No clash.").

### How it happened

MifBridge does NOT auto-increment its port. `Source/MifBridge/Private/MifBridge.cpp:40-46` reads
`MIF_BRIDGE_PORT` and otherwise uses 8791 (`MifBridge.h:29`); a bind failure only logs a warning
(`MifBridge.cpp:127`). So Curfew did not drift onto 8792 - it was deliberately pointed there, almost
certainly to dodge the SDK editor already holding 8791. That solved a two-way collision by creating a
different one, because the port it moved to was already spoken for.

### Why it matters, and why it would have been confusing

When the Blender phase starts, `_blender()` in `tools/mcp-server/server.py:77-84` dials
127.0.0.1:8792 and speaks the MifBlender protocol - a 4-byte big-endian length prefix followed by
UTF-8 JSON. On this machine that reaches **Curfew's UE HTTP bridge**, which speaks HTTP.

The failure would not have been a clean "connection refused". It would have been a length-prefixed
binary frame arriving at an HTTP listener, and whatever came back would not be MifBlender's
`{ok:true,...}`. That is a genuinely hard thing to diagnose from the Blender side, because the port
IS open and something IS listening - the two checks anyone would run both pass.

### The fix, which is Andre's call

Move CURFEW, not MifBlender - but for a more precise reason than I first wrote here.

**Correction to my own first draft of this item.** I claimed MifBlender's port was hard to change
because "the addon has no bind-address option". That conflates two things. The bind ADDRESS is
hardcoded (`server.py:64`, `HOST = "127.0.0.1"`, with a deliberate comment: a `0.0.0.0` checkbox is
a foot-gun on a socket that can run arbitrary Python). The PORT is a normal user preference -
`__init__.py:65-69` declares an `IntProperty` and `:112` shows it in the addon preferences UI.

The recommendation is unchanged, on better grounds: MifBlender's port lives in **two places that
must agree** - the addon preference and `MIF_BLENDER_PORT` read by the MCP server
(`tools/mcp-server/server.py:77-84`) - and a mismatch between them fails the same silent way this
whole item is about. Curfew's port is **one** environment variable. Change the thing with one
moving part, not the thing with two. Set `MIF_BRIDGE_PORT=8801` for the Curfew editor and the
collision is gone.

What this really argues for is writing the allocation down as a MAP rather than as three scattered
defaults, so the next "just move it up one" does not land on something else:

| Port | Owner |
|---|---|
| 8791 | MifBridge, UE - first editor |
| 8792 | **MifBlender addon - reserved, do not reuse** |
| 8793+ | free |
| 9876 | third-party `blender-mcp` - not ours |

Recommended: MifBridge on additional editors should use 8801, 8802, ... leaving 879x alone entirely.

### The lesson

The same one as most entries here: a check that tests a proxy rather than the real question. "Is the
port free?" was answered by trying to bind it, which is not the same as "is this port mine to take?"
Nothing warned, because nothing knew the allocation existed.

---

## 16. Force Reload on a cooked Blueprint kills the editor

**FOUND 2026-08-26 by Andre, diagnosed from the crash log the same night. NOT a MifBridge defect** -
recorded here because it is a hazard anyone driving this editor will meet, and because ruling the
bridge out was the first question asked.

### What happened

Andre right-clicked an asset and chose Force Reload. The editor died. The crash log's tail is a
wall of:

```
LogUObjectGlobals: Warning: ReloadPackage failed to find a replacement object for
    'Default__BP_ConsoleCommandsComponent_C' in the new package
LogUObjectGlobals: Warning: ReloadPackage failed to find a replacement object for
    'Default__BP_InventoryComponent_C' in the new package
LogUObjectGlobals: Warning: ReloadPackage failed to find a replacement object for
    'Default__BP_QuestManager_C' in the new package
LogUObjectGlobals: Warning: ReloadPackage failed to find a replacement object for
    'Default__BP_CartelManagerComponent_C' in the new package
```

### Why

Same family as section 6c of `02_GOTCHAS.md`. A COOKED package has had its editor-only data
stripped. `ReloadPackage` tears down the existing objects and expects to find replacements in the
freshly-loaded package - and for a cooked Blueprint the class default objects it is looking for are
not there. It warns, keeps going, and dies.

The rule this generalises to, which is the useful part: **an editor operation that assumes it can
rebuild editor state from a package is unsafe on cooked content.** Duplicate, Reload and Reimport
all share that assumption. Two of the three have now taken this editor down.

### What ruled MifBridge out

The crash journal (added the same day, `15_SAFETY_GATE_AND_JOURNAL.md`) showed the bridge's last
call 37 seconds before the crash - `invoke_editor_tab` - and no session with a call that started
and never finished. Nothing died inside a handler.

**A limitation of the journal that this exposed, and which is worth knowing before trusting it:**
it reported six sessions as DIED, and most of those were the agent hard-killing the editor with
`Stop-Process` to get past Live Coding during builds. An external kill and a crash are identical
from inside the process - both are a session with no shutdown record. The journal distinguishes
*died* from *closed cleanly*; it does NOT distinguish *crashed* from *killed*. Reading it as a crash
count would overcount badly.

What it does answer reliably is the question it was built for: **which call was in flight when the
process stopped.** That stays sound, because it is an absence, not a count.

## 17. `Build.cs` links plugin modules that the target has not ENABLED — FIXED

Found 2026-08-26 by the session running MifBridge in Curfew (UE 5.7). **Real, reproduced, and NOT
fixed** — the obvious fix is provably wrong, which is most of what makes this worth writing down.

### The symptom

After vendoring MifBridge into a project that enables a different set of plugins, the editor refuses
to start:

```
Plugin 'MifBridge' failed to load because module 'MifBridge' could not be loaded.
LogWindows: Failed to load '...UnrealEditor-MifBridge.dll' (GetLastError=126)
```

`126` is `ERROR_MOD_NOT_FOUND` — a missing **dependency** of the DLL, not the DLL itself. The build
reports success. Nothing warns. It is the same failure family as the exit-code-0 build: every check
short of actually launching passes.

### The cause

`MifBridge.Build.cs`, `AddPluginModules`, gates on whether the plugin's descriptor exists **under
`Engine/Plugins`** — that is, whether it SHIPS WITH THE ENGINE — not on whether it is **enabled for
the target**:

```cs
string Found = FindPluginDescriptor(PluginName);   // does Engine/Plugins/**/<Name>.uplugin exist?
bool bHas = !string.IsNullOrEmpty(Found);
if (bHas) { PrivateDependencyModuleNames.AddRange(Modules); }
```

Niagara, Water, MassEntity, LiveLink, LevelSnapshots, Metasound, GeometryScripting, GameFeatures,
ModularGameplay and IKRig all ship with UE 5.7, so all ten get linked. Curfew enabled fourteen
plugins, none of them these, so at load the imports had nothing to resolve against.

DDS2 does not see it. Any third project vendoring this hits it on first launch.

### Why the obvious fix is WRONG, which is the part worth keeping

The natural repair is "also check whether the plugin is enabled": read the `.uproject`'s `Plugins`
array, fall back to the descriptor's own `EnabledByDefault`. That is a twenty-line change and it
looks unarguable.

Computed against DDS2 before writing any of it:

| Plugin | in .uproject | `EnabledByDefault` | that rule says |
|---|---|---|---|
| Niagara | – | true | enabled |
| Metasound | – | true | enabled |
| IKRig | – | true | enabled |
| GameFeatures | true | false | enabled |
| Water | true | false | enabled |
| GameplayAbilities | – | false | **disabled** |
| GeometryScripting | – | *(key absent)* | **disabled** |
| ModularGameplay | – | false | **disabled** |
| ModelViewViewModel | – | false | **disabled** |
| ChaosVehiclesPlugin | – | false | **disabled** |
| MassEntity | – | false | **disabled** |
| LiveLink | – | false | **disabled** |
| LevelSnapshots | – | false | **disabled** |

**Eight of thirteen come out "disabled" on the editor where all thirteen demonstrably work right
now.** So the rule is not merely imperfect, it is wrong, and shipping it would have turned eight
whole endpoint families into refusals on the primary target — silently, since a refusal is a
well-formed answer.

What it misses is **transitive enablement**: a plugin listed in the `.uproject` pulls in its own
dependencies, those pull in theirs, and the engine force-enables some regardless. Resolving that
properly is what UnrealBuildTool already does internally, and reading two JSON files does not
approximate it.

### What a real fix needs

Not a descriptor read. Either UBT's own resolved plugin set for the target, or a build-time check
that the specific MODULES are actually going to exist — the module list is what the linker cares
about, and it is one level below the plugin question that keeps being asked instead.

Until then the `MIF_WITH_*` guards work correctly for their designed case — a plugin **absent from
the engine** — and do not cover the case of a plugin **present but not enabled**.

### Workaround, and why it is only that

Enable the ten plugins in the consuming project. Curfew did, and reports it as no hardship. But it
forces every consumer to enable ten plugins to use a bridge whose endpoints they may never call,
which is backwards: the guards exist precisely so that absent capability degrades to a named refusal
rather than a requirement.

### The fix, which was neither of the things either of us proposed

Not a descriptor read, and not UBT's resolved plugin set either. `MifBridge.uplugin` declared **one**
of the twelve plugins whose modules `Build.cs` links - IKRig, as `Enabled: true, Optional: true`.
Declaring the other eleven the same way makes UBT enable them **transitively** when MifBridge is
enabled, so the imports resolve at load. `Optional: true` keeps a plugin genuinely absent from an
engine a logged skip rather than a refusal to load MifBridge at all (`PluginManager.cpp:2164`).

**IKRig was both the model and the tell**: it is the one plugin that did *not* appear in the reported
failure list, because it was the one declared properly. That evidence was in the first report and
neither of us read it that way.

This asks UBT the question instead of trying to answer it from JSON - which is exactly why the first
attempt failed. "Enabled" is something UBT **resolves**, not a property of a file.

Verified both ways: on 5.3 the rebuild ran 56 actions and linked `ModelViewViewModelEditor.dll` among
others. On stock 5.7, the Curfew session reverted all ten plugins it had added to `Curfew.uproject`,
rebuilt, and the editor launched with **no** `GetLastError=126` and all ten mounted transitively -
`Bound 291 routes`. The workaround is no longer needed by anyone.

### Credit

Diagnosed from the load failure by the Curfew session, including the `GetLastError=126` reading that
points at a dependency rather than the DLL. They flagged it rather than patching the vendored copy,
which is why the evidence above could be gathered against DDS2 before anything was changed.


## 18. `delete_material_expression(all=true)` cleared part of the graph and reported success — FIXED

Reported 2026-08-27 from Curfew, stock UE 5.7, on `/Game/CF/Materials/M_CF_Sand`. A clear returned
`ok: true` and left three expressions behind (`Constant3Vector_0`, `LinearInterpolate_0`,
`Constant_1`). Deleting the same three **by name** worked perfectly.

### The cause is in the ENGINE, and the reporter guessed it exactly

```cpp
// MaterialEditingLibrary.cpp - present in BOTH 5.3 and 5.7
void UMaterialEditingLibrary::DeleteAllMaterialExpressions(UMaterial* Material)
{
    for (UMaterialExpression* Expression : Material->GetExpressions())   // a VIEW over the LIVE array
        DeleteMaterialExpression(Material, Expression);                  // ...which removes from it
}
```

`GetExpressions()` returns a `TConstArrayView` over the array that `DeleteMaterialExpression` is
removing from. Each removal shifts the remainder down one, the iterator then advances past the
shifted element, and **every other expression is skipped**.

That is why *some* survived rather than none, and it is the detail that made this hard to notice: a
clean no-op gets spotted immediately, a half-done clear looks like it worked. The reporter's guess -
"iterating the expressions array while removing from it, which in UE's TArray skips every other
element" - was right down to the mechanism, from the behaviour alone and without the source.

### Two fixes, because there were two defects

**The engine bug is worked around.** The handler no longer calls
`DeleteAllMaterialExpressions`. It snapshots the expression list into its own `TArray` first and
deletes from the snapshot, so reallocation of the live array during the loop is irrelevant. The
per-expression engine call was never broken - only the loop over it - which is exactly what
"deleting by name works" was telling us.

**The silent success was a separate defect and is the more important one.** The handler already
computed `deleted` and `remaining` correctly and returned them. It also returned `ok: true`. Anything
checking the status rather than doing the arithmetic saw success, which is how an engine bug this
visible survived. `all=true` asks for an EMPTY graph, so anything left is now a failure that names
the survivors and points at the by-name path.

### Regression

`tools/test_material_graph.py` T356 seeds a graph, clears it, and asserts the **postcondition** -
either `ok:true` with zero expressions remaining, or an explicit failure naming what survived.
Deliberately not an assertion on `ok`, because `ok:true` is precisely what the broken version
returned.

### Status

Fixed, and now verified live on BOTH engines - T356 passes on 5.3 here, and the Curfew session ran the same shape on stock UE 5.7. Originally built on 5.3 (DLL 3,950,592 at 23:06), and **verified live on stock UE 5.7** by the Curfew
session the same night - four expressions seeded, `all=True`, zero remaining, `ok:true`. That is the
half this machine could not do: the SDK editor was closed and the 5.7 engine was holding a Live
Coding lock. T356 still runs it on the next regression pass here. The third instance this session of *a clear that
cannot prove it cleared*, after the World Partition save and `Build.bat`'s exit code.


## 19. `spawn_many` spawned nothing and reported success — FIXED

Found 2026-08-26 by scanning for the shape issue 18 turned out to be, rather than by a report.

`spawn_many` set `spawned`, `failed` and `errors[]` correctly and returned `ok: true` regardless. So
a request for fifty actors where all fifty failed answered:

```json
{ "ok": true, "spawned": 0, "failed": 50, "errors": [ ... ] }
```

Everything checking the status rather than reading the arithmetic saw a clean spawn.

### The lens that found it, which is worth more than the fix

`audit_postconditions.py` looks for handlers that mutate and never read the result back. It would
never have flagged this one, because `spawn_many` **does** read back - it counts precisely what
happened and reports it. The defect is one layer up: **the handler computed the truth and then did
not act on its own answer.**

That is the same defect as issue 18, where `delete_material_expression` returned correct `deleted`
and `remaining` counts alongside `ok: true`. Stated as a rule: *an endpoint that computes an outcome
count must decide what that count MEANS, rather than reporting it and leaving the caller to notice.*

A scan for the shape - handlers that write an outcome count into the response and never branch on it
- returned 7 candidates across 303 endpoints. Of those, this was the clearest defect;
`apply_graph_patch` already has real rollback, and the DataTable pair report per-row detail. The
others are on the reading list rather than presumed broken.

### The fix draws the line at ZERO, deliberately

Total failure now fails. A **partial** spawn stays `ok: true` with the counts and a `partialNote`,
which matches `batch`: the spawned actors really are in the level, they are not rolled back, and
failing the whole call would imply an undo that did not happen. The note says explicitly to
re-request only the failures, because re-running the whole list would duplicate the ones that worked.

Refusing to guess where "mostly worked" ends is why the threshold is zero rather than a ratio.

### Status

Fixed, built on 5.3 (DLL 3,950,592 at 23:25), and VERIFIED LIVE: test_spawn_many.py passes 27/27 against a running editor.
`test_spawn_many.py` covers the endpoint and should gain a total-failure case.


## 20. The safety gate refused `save_package` and permitted two roads to Save — FIXED

Found 2026-08-26 while designing an in-panel write-mode dropdown, by asking whether an agent could
reach the dropdown widget. It could not - but the question exposed something worse that had nothing
to do with the dropdown.

In `scratch` mode:

| call | result |
|---|---|
| `save_package` | refused by the gate |
| `send_editor_key {key:"S", modifiers:{ctrl:true}}` | **permitted** — and Ctrl+S is Save |
| `invoke_editor_command {context:"LevelEditor", command:"Save"}` | **permitted** |

### Why they were missed

The unsafe list was built by asking **"does this endpoint mutate?"**. Neither of these writes
anything - `send_editor_key` delivers a key event, `invoke_editor_command` executes a registered
`FUICommandInfo`. Both answer "no" to that question and both are perfectly reasonable tools.

The question that matters is **"can this endpoint REACH something that mutates?"**, and against that
one they answer yes immediately.

`invoke_editor_command` already HAS a deny-list, which is part of why it looked covered. That list
guards against **modal hangs** - commands that open a dialog and freeze the bridge (PM-011). Guarding
against a hang and guarding against a privilege are different questions that produce similar-looking
code, and the presence of one made it easy to assume the other.

### The same shape as the batch bypass, hours apart

Issue in `docs/15`: `batch` dispatched straight out of `Handlers()` without passing the gate. This is
that lesson at a different layer - **a control enforced at one choke point is only as good as the
claim that there is one choke point.** There, the second road was another dispatcher. Here, it is an
endpoint that drives the editor's own UI.

### The fix

Both are now on the unsafe list, gated wholesale rather than filtered - for the same reason as
`exec_console`: they take an arbitrary key or command NAME, so no subset is knowably safe, and a
denylist over a namespace someone else populates is the guard shape that always loses.

`invoke_editor_tab` and `open_asset_editor` are deliberately **not** gated. They open UI and cannot
execute anything, and diagnosis is the entire point of scratch mode.

### The property this buys, which matters for the dropdown

With `send_editor_key` gated, an agent in scratch mode cannot deliver keystrokes at all - so it
cannot drive a focused combo box, and **an in-panel dropdown becomes safe by construction rather than
by hoping nobody thinks of it.** The gate ends up protecting its own control surface, which is the
property a gate should have.

### Status

Fixed, built on 5.3 (DLL 3,969,024 at 23:38), and VERIFIED LIVE: test_safety_gate.py passes 44/44 against a running editor, T635 included. `test_safety_gate.py` T635 asserts both are refused,
refused *by the gate* specifically, that the refusal holds through `batch` as well, and that
`invoke_editor_tab` still works. Not run live - the SDK editor is closed.

## 21. Three filesystem reaches the safety gate does not cover — one defect, two design questions

Surfaced 2026-08-27 by a design workflow, then **verified by reading the handlers** rather than taken
on the agent's word. All three claims are true. What they *mean* differs a lot, and separating that is
most of the value here.

### The one that is a defect: `export_asset`'s relative path is not confined

```cpp
else if (FPaths::IsRelative(RequestedFile))
{
    // A relative path is resolved against the bridge's own export root rather than the
    // process CWD, which in the editor is not where anyone thinks it is.
    FullOutPath = MifExportRootDir() / RequestedFile;      // MifBridgeExport.cpp:560
}
...
FPaths::NormalizeFilename(FullOutPath);
FullOutPath = FPaths::ConvertRelativePathToFull(FullOutPath);   // :566-567 — collapses ..
```

The comment describes containment: *"resolved against the bridge's own export root"*. It is not
containment. `ConvertRelativePathToFull` collapses `..`, so
`../../../../Users/andre/Documents/something` resolves straight out of the export root and the file
is written there.

**A comment that misstates what the code does is the specific thing this project keeps finding**, and
it is worse than no comment: it stops the next reader checking.

Absolute paths are used verbatim (`:562`), which is at least honest about itself.

### The one that is a real inconsistency with the gate's own contract

`export_asset` **writes files to disk** and is **not on the unsafe list**, so it writes in `scratch`
mode. `overwrite` defaults to **true** (`:568`) and `CreateDirectoryTree` will make whatever
directories are needed (`:581`).

The gate's stated premise is that nothing reaches disk. That is *not quite* what it means in practice
— it means no **package** is saved, and an exported FBX is not a package — but the distinction is
nowhere in the documentation, and "nothing is saved" is what the docs actually say.

So either the docs are imprecise or the gate is incomplete. **This one needs Andre**, because it is a
question about what the gate is *for*, not a bug:

- Gating `export_asset` in scratch makes the contract literal and costs the mesh round-trip workflow,
  which is the whole point of the Blender pipeline.
- Leaving it means "nothing is saved" needs rewording to "no package is saved, and exports go to
  `Saved/MifBridge/Export` unless you say otherwise".

The second is probably right, and it is not mine to decide.

### The two that are working as designed

**`read_modloader_log` reads any caller-named path** (`MifBridgePipeline.cpp:45-70`). Defaults to
UE4SS's log; any other path is read if it exists. It is a log reader and reading a named log is its
job. Worth knowing it is an arbitrary file *read* with no confinement, but reads are permitted in
scratch by design and this discloses to a caller who already drives the editor.

**`set_cvar` accepts any registered console variable** with no allowlist
(`MifBridgeConsole.cpp:165-176`). Also its job. Gating it wholesale would break a great deal for a
threat it does not carry — and the specific attack worth worrying about is already covered:
`test_safety_gate.py` T633 asserts `set_cvar` cannot change the write mode, because the mode is
deliberately not a cvar.

### Why the separation is the point

The three gate bypasses fixed earlier tonight were unambiguous: **the gate refused X and permitted a
road to X.** A contradiction, no judgement needed.

These are not that. Two are endpoints doing exactly what they exist to do, and reporting them as
"security findings" alongside the real ones would devalue the real ones. The reviewing agent listed
all three at similar weight; the difference only appears when you ask *what is the contradiction*, and
for two of them there is not one.

### Fixed here

Only the illusory containment — the comment now says what the code does, and the response reports the
resolved path so a caller can see where the file actually went. The gate question is filed for Andre.


## 23. My build verification counted `: error ` and missed `: fatal error ` — FIXED

> **Renumbered from 21 on 2026-08-27.** It was filed as 21 while issue 21 already existed - I picked
> the next number without checking, which is the same class of mistake as the duplicate spec entries
> found the same night. The commit that introduced this says "filed as docs/06 issues 21 and 22";
> this is the 21 it meant. Issue 22 kept its number because `make_release.py` and
> `parity_check.py` both cite it.

Found 2026-08-27, by a build that reported **0 errors** and produced a DLL missing five endpoints.

Every build all night was verified with `grep -c ": error "`. The log said:

```
MifBridgePCG.cpp(42): fatal error C1083: Cannot open include file: 'EditorActorSubsystem.h'
```

`: fatal error ` does not contain `: error `. The check said clean. I then spent three rounds hunting
for why `UnrealEditor-MifBridge.dll` had never linked — **the log had already told me, and my filter
had thrown the message away.**

This is the same family as the entries above about `Build.bat` exiting 0 on a failed build, and it is
worse in one way: those are the tool lying, this was my own check lying, and it had been lying all
night on every build that happened to succeed anyway.

### Fixed by `tools/buildcheck.py`

Three independent signals, because each alone has been wrong here before:

1. `error <code>` **or** `fatal error` **or** `LNK<n>` — fatal and link errors are the two shapes a
   naive error grep misses, and both have cost time on this project.
2. `Result: Failed` anywhere in the log. The process exit code is **not consulted at all**.
3. The expected binary's mtime moved — and its absence is named separately, since a failed LINK
   *deletes* the DLL.

```bash
python tools/buildcheck.py <log> --dll <path> --since <epoch>
```

Its ignore list is deliberately short (`warning`, `[Upgrade]`, `error C4996`). A generous one would
recreate the original bug somewhere new.


## 22. I re-broke issue 17 the same night I documented it

Adding PCG to `MifBridge.Build.cs` without adding it to `MifBridge.uplugin` stopped the editor
loading at all:

```
Plugin 'MifKismetReconstructor' failed to load because module 'MifKismetReconstructor'
could not be loaded.
```

The named module is **not** the problem — it is downstream of the real one. MifBridge linked against
a plugin the project had never enabled, so the module chain failed and the error surfaced on whatever
was next in it.

That is exactly **issue 17**, whose recorded fix is to declare the plugin in the `.uplugin` as
`Optional: true, Enabled: true` so UBT enables it transitively. Thirteen plugins were already declared
that way. I added the fourteenth to `Build.cs` only.

**The lesson is not "remember the uplugin".** It is that `Build.cs` and `MifBridge.uplugin` are two
files that must agree and nothing checks that they do — the same shape as the hook drift found earlier
today. Worth a `parity_check.py` rule.


## 24. Two removals reported success without checking anything was removed — FIXED

Found 2026-08-27 by auditing offline for the lens the night work calls most productive: handlers that
report success while doing something else.

```cpp
SCS->RemoveNodeAndPromoteChildren(Node);          // void
Out->SetStringField(TEXT("removed"), Name);        // reported regardless

FBlueprintEditorUtils::RemoveGraph(Blueprint, Graph, Default);   // void
Out->SetStringField(TEXT("removed"), Name);                      // reported regardless
```

Both engine calls are **`void` in 5.3 and 5.7** — verified in both trees. Neither can refuse out
loud, so `remove_component` and `remove_function` reported `removed` whether or not anything was.

### This is PM-007 again, and one sibling had already learned it

`remove_variable` carries this comment:

> `FBlueprintEditorUtils::RemoveMemberVariable` is VOID and early-returns when the variable is …

and re-queries with `FindNewVariableIndex` afterwards, failing if it is still there. The lesson was
learned in one of the three removals and never carried to the other two.

**The audit that found it was wrong first**, which is worth recording. Its first version looked for a
read-back feeding the RESPONSE and flagged 51 of 51 mutating handlers — including `remove_variable`,
whose verification feeds a `Fail()` guard instead. A verification that REFUSES is stronger than one
that reports, and the check penalised it. Corrected to count any re-query: 19 of 133, and only the
two removals mattered.

### Fixed

Both re-query and fail by name. `remove_component` also now reports **`childrenPromoted`** and lists
them: `RemoveNodeAndPromoteChildren` reparents children rather than deleting them, and a caller who
got back only `removed: true` had no idea its children moved — the same shape as `remove_tree_widget`
deleting a subtree and reporting one line.

The likely real-world hit for `remove_component` is an **inherited** component: declared on a parent
class, not removable here, only overridable. The refusal now says that.

## 25. Five bool returns the engine gave us and we threw away — FIXED

Found 2026-08-27 by turning issue 24's lens into a sweep instead of a hunch. Issue 24 was two
handlers spotted by reading. This asks the question mechanically, of the whole plugin.

**The method**, because it is reusable and the numbers are the point:

| | |
|---|---|
| bare-statement calls in the plugin | 513 distinct names |
| not defined in our own sources | 385 |
| declared `bool <Name>(` in the engine headers | 92 |
| after excluding names where discarding is universal (container mutators, JSON setters, `Modify`, `MarkPackageDirty`) | **56** |
| read by hand | 56 |
| **real** | **5** |

The other 51 split two ways, and both are worth naming. Some were **false matches on a same-named
overload that returns void** — `FMaterialUpdateContext::AddMaterial`, `UPanelWidget::AddChild`
(returns a `UPanelSlot*`), `USceneComponent::UpdateBounds`, `FEditorViewportClient::SetViewRotation`.
The rest were **already covered by a read-back**, which is the stronger check: `rename_widget` ignores
`Rename`'s bool and re-finds the widget through the tree instead; `add_reroute` ignores
`TryCreateConnection`'s and checks `LinkedTo` instead. Those bare calls are correct and now say so in
a comment, so the next sweep does not "fix" them.

### The five

| endpoint | call | what it meant |
|---|---|---|
| `snap_actors_to_ground` | `SetActorRotation` | rotates with **sweep on**, so tilting an actor into the slope it just landed on can be refused. Counted as `snapped` with nothing saying its rotation never changed. Now `alignRefused`. |
| `add_struct_member`, `create_struct` | `ChangeVariableDefaultValue` | refuses a default that does not parse for the member type. `default:"abc"` on an int left the member with **no** default while the response reported the one asked for. |
| `spawn_actor` (Level) | `SetStaticMesh` | the identical block in `MifBridgePIE.cpp` already read the mesh back; this copy did not. |
| `remove_node` | `RemoveNode` ×2 | see below — the worst of the five. |
| `remove_widget_animation_track` | `RemovePossessable` | `removedBinding` was reported from the **request flag**, not from anything observed. |

### `remove_node` had three problems, and the third is the one that matters

`FBlueprintEditorUtils::RemoveNode` is **void on both engines**; `UEdGraph::RemoveNode` returns a bool
that was discarded. Those are issue 24 again. But the endpoint also had a **fall-through**: a node with
no owning blueprint whose outer is not a `UEdGraph` matched neither branch, so nothing was attempted
at all — and control ran straight on to `Out->SetStringField(TEXT("removed"), Guid)`.

Not "did the removal work" but "was a removal even tried". Verification is now by pointer against the
graph's node list, captured before the call: a guid scan would be wrong, because a reused guid — which
this endpoint's own `graphId` parameter exists to disambiguate — could match a **different** node and
report a failure that never happened.

### Two adjacent-call asymmetries, which is what makes this lens keep paying

Three of the five sit **next to a sibling that already checks**. `snap_actors_to_ground` tests
`SetActorLocation`'s return twenty lines above the `SetActorRotation` that ignores its own, with a
comment explaining why discarding it was wrong. `AddStructMemberNamed` checks `RenameVariable` on the
line before the `ChangeVariableDefaultValue` it ignores. `spawn_actor` exists in two near-identical
copies and only one reads the mesh back.

So the highest-yield place to look is not a list of dangerous functions. It is **beside a check that
already exists** — someone learned the lesson there and applied it to one line.

### Coverage, including what is NOT covered

`tools/test_unchecked_returns.py` (T720–T723) covers the struct defaults; `test_snap_ground.py`
T66/T67 covers `alignRefused`. `remove_node` and `remove_widget_animation_track` are **not covered on
the success path**: `remove_node` needs `confirm:true`, and `tools/scratch_confirm.py` cannot unblock
it because the endpoint is addressed purely by guid and no payload can prove it scratch-only.

That last point corrected a claim in `scratch_confirm.py` itself, which listed eleven endpoints it
restored coverage for. It restores nine. `remove_node` and `rename_event` carry no path parameter and
never could — now stated in its docstring, in its refusal message, and pinned by its self-test.

## 26. Five messages told callers to run endpoints that do not exist — FIXED

Found 2026-08-27, immediately after issue 25, by asking the same kind of question mechanically.

MifBridge's error messages are unusually helpful: most name the endpoint you should have called
instead. That is the point of them — and it means a **wrong name is worse than no advice**, because
the caller follows the instruction and gets `not an endpoint on this build`, having been sent there by
the bridge itself.

| named in a message | reality |
|---|---|
| `delete_node` ×4 | the endpoint is `remove_node` — and it needs `confirm:true`, so the messages now say that too |
| `list_widgets` | the endpoint is `list_tree_widgets`, and this one sat in a `RejectUnknownParams` hint — exactly where somebody looks after already getting the call wrong once |
| `set_view_mode` ×2 | has never existed under any name; used to explain a limitation |
| `set_material_instance_layers` ×2 | "ships with … a later batch" — a promise shaped like a fact |
| `create_water_zone` | see below; not a typo at all |

### The one that was not a naming mistake

`create_water_body`'s parameter help said *"a body finds its own AWaterZone by overlap; create the
zone separately with `create_water_zone`"*. Since UE 5.1 a water body overlapping **no** `AWaterZone`
does not render at all, so the write half of the water family could author water that **could never be
seen** — and its own response note said so, while offering nothing that could fix it.

The advice had nowhere to send anyone because the capability was missing. So `create_water_zone` was
built rather than the sentence deleted. It reports `bodiesNowCovered` and **names** the bodies still
outside every zone, because nobody creates a zone for its own sake. Spawned through
`UWaterZoneActorFactory` for the same reason `create_water_body` is: a raw `SpawnActor` gets no
far-distance material and the wrong render target resolution — the same hole that endpoint had already
dug once and documented.

This also answers Andre's standing question about the 5.7 audit — *"we added water view endpoints i
think but maybe not the water build"*. Read and write both existed; what was missing was the piece
that makes the writes visible.

### A test was holding the wrong name in place

`test_recipes.py` asserted `"delete_node" in err`. It had pinned the mistake for as long as the
message carried it — a test can only protect the behaviour it was told to expect, and it was told the
wrong thing. Now asserts `remove_node`, with a note saying why.

### The check, and the rule that makes it usable

`tools/audit_message_endpoints.py`, wired into the same habit as `parity_check` and `spec_check`. Naively
it reports eight things and five are noise, which is how a check gets ignored. Two rules fix that:

* **the token IS the whole literal** → it is an identifier, not advice. `TEXT("save_maps")` is a
  parameter alias; `TEXT("save_all")` is an entry in the forbidden-editor-command list.
* **the token sits in an `aliases: …` span** → it names a parameter. `"maps (aliases: saveMaps,
  save_maps)"` is documentation, not a suggestion.

With those, five hits and five were real.

## 27. Auditing 139 commits found 22 real defects — six fixed, thirteen still open (2026-08-30)

A separate session landed 139 commits on 2026-08-28/29: +18388/-691, endpoints 320 → 351, suites
75 → 100, 31 new endpoints. All five of this repo's audits passed on it, and that is exactly the
point worth recording: **those audits check structure, not truth.** `parity_check` proves every
endpoint is reachable; nothing in the toolchain asks whether a commit message's claim is true, or
whether a new handler reports success for work it did not do. That gap is what this pass covered.

Method: eight lenses read the range independently — silent success in the new endpoints, crash-guard
completeness, 5.3-vs-5.7 correctness, test quality, commit claims vs code, gate coverage, the
uncommitted work, and regressions in the 691 deletions. Every finding then went to a skeptic
instructed to refute by default. **34 raised, 22 survived, 12 refuted** — the refutation pass killed
a third of them, including two where the finder had the mechanism backwards. A finder alone would
have produced a list a third of which was wrong, which is how a report gets ignored.

Six are fixed in 3ffc095 (gate, IK Rig, water, GAS, both log readers, and the panel ODR violation);
see that commit for each mechanism. The rest are recorded here rather than lost.

### Still open

* **`add_mvvm_binding` creates bindings that cannot compile.** It validates that the source property
  EXISTS on the viewmodel and stops. Every binding mode it offers except `oneTimeToDestination`
  requires the source field to be registered FieldNotify, so the MVVM compiler rejects the rest at
  compile time — after the endpoint has already reported success. `set_variable_flags` gained a
  `fieldNotify` flag in this same range (c924450), so the bridge can already set what this endpoint
  cannot check.
* **`add_simplified_collision`'s cooked guard over-refuses.** It refuses all eight shapes when
  `GetMeshDescription(0)` is null, but only box/sphere/capsule reach MeshDescription. The k-DOP
  shapes fit their hull from RENDER data, which every cooked mesh has, so they cannot crash and are
  being refused anyway. c7aa495's "every shape shares the failure mode" is false on both engines.
* **Two MVVM version guards are bounded one minor too high.** `MifBridgeMVVM.cpp:191` and `:330` use
  `>= 5.7` for a `UMVVMEditorSubsystem` API that changed in **5.6**. Also, four new sites in this
  range hand-write `ENGINE_MAJOR_VERSION >= 5 && ENGINE_MINOR_VERSION >= N` instead of
  `MIF_ENGINE_AT_LEAST`, which `MifBridgeVersion.h` exists to prevent and docs/02 §14 names.
* **`add_gameplay_tag` was declined on a false premise.** 7e3e32d concluded "no public runtime API";
  `IGameplayTagsEditorModule` exposes two on both 5.3 and 5.7. The accurate statement is that
  `UGameplayTagsManager`'s mutating API is private in the *runtime* module — the *editor* module is
  the supported route. A wrong decline permanently closes a buildable feature, which is why this one
  matters more than its size.
* **Two incomplete fixes, both the house shape** — a fix applied at one call site of a pattern that
  exists at many. `945f1f0`'s `wait_for_pie_state` timeout enforcement reached 1 of 4 copies of that
  helper; `958213a`'s editor-world actor-leak cleanup reached 1 of 8 spawn sites, one of them 30
  lines above the fix in the same file. An uncleaned actor spawns into the persistent editor world
  and survives every later PIE session.
* **Four test-quality gaps.** `958213a` describes "stopped trusting the volume-count proxy" for
  T1606; what it did was delete `move_actor_to`'s only postcondition and replace it with a check
  that cannot fail. `test_simplified_collision_guard.py` T932 cannot distinguish a working
  `remove_collision` from a no-op, and on a second run its crash-survival check goes vacuous too.
  `test_duplicate_cooked_guard.py` T942 is named "the new asset really exists" and reads that from
  the writer's own response rather than the registry — the endpoint asserting its own success.
  `test_mvvm.py`'s docstring describes a negative compile case (T1506) that does not exist in the
  file.

### The lesson worth keeping

Two of the six fixed defects were **already documented and then reintroduced**. The dead 64 MB size
guard was found on 2026-07-26 (`docs/audit/work/J_dds2_project.md:342`, which even prescribed the
tail-read) and was afterwards copied verbatim into a brand-new endpoint. And the gate's own comment
said *"a comment saying 'all three' is exactly what was true before someone added a fourth"* — and a
fourth was added, in a range where `git diff -- MifBridgeSafety.cpp` is empty. Writing a defect down
does not prevent it. A CHECK prevents it. `test_safety_gate` T636 already derives the Exec-caller
list from source rather than trusting the comment; the same should exist for key injection, so an
endpoint that reaches `UGameViewportClient::InputKey` or `FSlateApplication::Process*Event` cannot be
added outside the gate silently.

### Resolution, 2026-08-30 — twelve of thirteen closed

All but one of the open items above are fixed, across four commits. Not listed as "done" without
saying which, because a closed item nobody can trace is how the stale entries corrected earlier in
this same file got that way:

* `10b786a` — the MVVM version guards (a real **build break on 5.6**, not a style issue: both APIs
  are already in their 5.7 form on 5.6, so the `#else` branch called a 3-arg overload that does not
  exist there), all four hand-written guards converted to `MIF_ENGINE_AT_LEAST`,
  `add_simplified_collision`'s over-refusal narrowed to the three shapes that actually need
  MeshDescription, and both incomplete fixes closed by moving the duplicated helper into `mifaudit`
  — `wait_for_pie_state` and `cleanup_level_actor`. Deduplicating is the only version of those two
  fixes that stays fixed.
* `70a8108` — `add_mvvm_binding` now reports `sourceIsFieldNotify` and warns when the mode needs
  notification the source cannot provide. Reported rather than refused: a false negative would block
  a legitimate binding, and the silence was the defect, not the permissiveness.
* `925112e` — the four test-quality gaps.

**This entry once carried `add_gameplay_tag` as unresolved** — on the grounds that the decline rested on a
false premise and the feature was buildable, needing specification rather than a one-line reversal.

> **CLOSED — and this line was stale the day it was written (corrected 2026-08-31).**
> `add_gameplay_tag` was BUILT on 2026-08-30, the same day as this resolution note, and the note was
> never updated. Verified live rather than by reading: it is registered and bound
> (`MifBridgeCommon.cpp:418`), it refuses an empty call with *"tag is required - the full dotted
> name, e.g. 'Ability.Melee.Heavy'. NOTHING was added."*, and it accepts `tag`, `comment`, `source`
> and a `transient` flag that "registers for THIS EDITOR SESSION only and writes nothing to disk".
> Two suites cover it; `test_gameplay_tag_authoring.py` runs **19 PASS 0 FAIL** against the current
> build, including that a tag with internal spaces is ACCEPTED because Unreal permits it — the
> bridge declining to invent a stricter rule than the engine.
>
> The build also corrected the approach the earlier research assumed:
> `UGameplayTagsManager::AddTagTableRow` is `private:` (GameplayTagsManager.h:739, friended to
> `SAddNewGameplayTagSourceWidget` and two others), which a header read had missed and only
> `error C2248` surfaced. That is recorded in the spec at the "GameplayTags authoring - BUILT
> 2026-08-30" entry.
>
> So docs/06 now has NO open items: 27's last one was closed a day after it was filed, and 28 was
> fixed in source on 2026-08-31.

One thing this pass produced that is worth more than any single fix: rewriting
`test_simplified_collision_guard.py` was necessary because **my own** guard change invalidated it,
and its docstring turned out to contain the same error the guard did — it said every shape "needs
the same real geometry ... confirmed by re-running all four shape families and getting a clean
refusal every time". That confirmed the *guard's* behaviour, read back as though it were the
*engine's*. A refusal-only test can only ever see what the guard did, never what the engine would
have done, so it cannot tell a correct guard from a blanket one. The suite now proves the guard is
narrow — that the shapes which should work, do.

## 28. delete_asset then create_asset at the same path is an unrecoverable dead end — STILL OPEN; the obvious fix is WORSE than the bug (2026-08-30)

Found by running `test_input_mapping.py` a second time in the same editor session. Its cleanup had
already passed - `find_assets {pathPrefix:"/Game/_MifInput"}` returned count 0 - and the next run
still could not create the assets back.

The three endpoints disagree, and two of the errors point at each other:

```
find_assets  {pathPrefix:"/Game/_MifInput"}          -> ok, count 0
create_asset {path:".../IA_MifTest2"}                -> "an asset already exists ... delete it first"
delete_asset {path:".../IA_MifTest2", confirm:true}  -> "no asset found at package '...'"
```

So an agent told to delete it first is then told there is nothing to delete, and the path stays
unusable for the rest of the editor session. There is no way out from the bridge: the loop is closed.
Restarting the editor clears it, which is exactly the kind of remedy an agent driving the editor
cannot discover from the responses.

### Why it happens

`delete_asset` unregisters the asset and the registry stops reporting it, but the UObject is still
resident - deletion marks it garbage and it survives until a GC pass. `create_asset`'s existence
check finds that resident object and refuses. `delete_asset`'s own lookup goes through the registry,
which has already forgotten it. Each endpoint is individually consistent with the source it consults,
and the pair is incoherent.

This bites hardest on never-saved scratch assets, which is to say the ones every suite creates, so
any suite that runs twice without an editor restart can hit it. It is also the shape most likely to
be hit by an agent iterating - create, test, delete, adjust, create again.

### Reproduced again before touching it, 2026-08-31

Not taken on trust. Against the live editor, on a scratch asset created for the purpose:

```
find_assets  {pathPrefix:"/Game/_MifScratch"}   -> count 2, MifIAProbe present
delete_asset {path:".../MifIAProbe", confirm}   -> ok, numDeleted 1, deleted true
find_assets  {pathPrefix:"/Game/_MifScratch"}   -> count 1, MifIAProbe gone
create_asset {path:".../MifIAProbe", class:...} -> "an asset already exists at ... delete it first"
delete_asset {path:".../MifIAProbe", confirm}   -> "no asset found at package ..."
```

The closed loop, exactly as filed.

### Fixed in source

The smaller of the two proposed fixes: a garbage object is not an existing asset. `IsValid()` is
false for one, so wrapping the existence lookup in it makes `create_asset` and `delete_asset` agree
about what is there. No lifetime is changed and nothing is renamed - it is a pure predicate, so the
only behaviour that moves is that a corpse stops blocking creation, which is the whole defect.

FOUR SITES, not one. The same lookup guards four create paths, and fixing only the reproduced one
would have left the identical dead end behind `create_blueprint`:

| file | line | endpoint |
|---|---|---|
| `MifBridgeUserTypes.cpp` | 73 | `create_asset` (the reproduced one) |
| `MifBridgeNodes2.cpp` | 1637 | `create_blueprint` |
| `MifBridgeMetaHuman.cpp` | 94 | `create_metahuman_character` |
| `MifBridgeMaterials.cpp` | 970 | `create_material` / `create_material_function` |

`MifBridgeMaterials.cpp` used `StaticFindObject(...) != nullptr` rather than `StaticLoadObject`, the
same flaw in a different spelling; its `FPackageName::DoesPackageExist` half is the DISK question and
is untouched. `MifBridgeImport.cpp` and `MifBridgeThumbnail.cpp` share the lookup but not the defect -
both offer `overwrite:true`, so neither closes the loop, and both were left alone.

### The IsValid() fix was tried, and REVERTED the same day

`IsValid(StaticLoadObject(...))` looks like the minimal correct fix and this issue recommended it. It
is not safe, and the reason is in `StaticAllocateObject`:

```
Obj = StaticFindObjectFastInternal( /*Class=*/ NULL, InOuter, InName, true );   UObjectGlobals.cpp:3323
if (Obj && !Obj->GetClass()->IsChildOf(InClass))
        UE_LOG(LogUObjectGlobals, Fatal, ...);                                  UObjectGlobals.cpp:3326
```

That lookup excludes only `Unreachable`, **not** `Garbage` (`UObjectHash.cpp:712`,
`ExclusiveInternalFlags |= EInternalObjectFlags::Unreachable`), so it finds exactly the corpse the
guard was taught to ignore. Then, if its class is not a parent of the class being created, the engine
calls `UE_LOG(..., Fatal, ...)` — which terminates the editor.

So the sequence

1. `create_asset` `/Game/X/Foo` class `Blueprint`
2. `delete_asset` `/Game/X/Foo` — object is garbage, still resident
3. `create_asset` `/Game/X/Foo` class `DataTable`

went from *refused with a confusing message* to **editor terminated**. Refusing is annoying. Fatal
takes whatever was unsaved with it. The guard is back to its original form in all four places.

**What that leaves.** The dead end is real and still unfixed, and the remedy is the OTHER one this
issue proposed: rename the doomed object to the transient package, as `ObjectTools` does, so no
object holds the name when `NewObject` runs. That cannot be tested with the editor closed, and it
changes object lifetime rather than reading a flag, so it is not something to land unverified — this
entry exists so the next attempt starts from the crash rather than rediscovering it.

**The general lesson.** The minimal change that makes two endpoints agree can be worse than the
disagreement. "Treat a garbage object as absent" is only safe if everything downstream also treats it
as absent, and `StaticAllocateObject` does not.

### What is proven, and what is not

COMPILE-verified on UE 5.3 installed, Development, BUILD OK with a linked DLL and a verified mtime.
NOT behaviour-verified: the running editor loads a DLL built before this change, and loading a new one
means `live_coding_compile`, which requires `confirm:true` and whose own refusal says a bad patch can
destabilise the process holding unsaved work. That is a decision for a human at the keyboard, not for
an overnight run. So the reproduction above is of the BUG, not of the fix.

The second proposed remedy - renaming the doomed object to the transient package, which is what the
editor's own delete does - was deliberately NOT attempted. It changes object lifetime rather than
reading a flag, and it cannot be tested here tonight. If `IsValid()` alone turns out to be
insufficient because the name is still taken, that is the next thing to try and it is written down
here rather than guessed at now.

### Workaround in the meantime

Suites should suffix scratch asset names per run, which is already the house pattern elsewhere
(`test_sequence_keys.py` uses `LS_%d % st`). `test_input_mapping.py` did not and now does.
