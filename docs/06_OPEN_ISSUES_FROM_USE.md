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

Author's own priority ranking, "by what actually costs me time now". Status as of the
**2026-07-29 11:21 build (220 endpoints)**, each verified by a live call, not by reading the diff.

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

## 13. Four more from the same hunt — VERIFIED, NOT YET FIXED

Each of these I confirmed against the source myself; they are queued behind the eight in section 12
rather than written, because writing unbuilt code into a file that already holds tested code is how a
commit stops meaning what it says.

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

