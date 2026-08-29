# Self-audit findings — 2026-07-29

> **RE-CHECKED 2026-08-29, ONE MONTH LATER - LARGELY ACTIONED, NOT A LIVE TODO LIST.** Sampled all
> nine numbered CRITICAL/HIGH findings against current source rather than assumed either way: #2
> (set_variable_default wiping on non-string), #3 (batch's degraded inline backup), #4
> (rename_variable/remove_variable silent no-ops + modal hazard), #5 (splice paths discarding
> TryCreateConnection), #6 (paint_landscape's unchecked layer-membership promise), #7
> (set_spline_points clearing before validating), #8 (batch silently dropping non-object ops[]
> entries), #9 (spawn_actor_in_pie missing mesh support) are ALL fixed, several with a comment at the
> fix site quoting this exact finding's own language. #10's raw coverage stat (132/203 handlers with
> no RejectUnknownParams) is obsolete on its face - the codebase now has more RejectUnknownParams call
> sites (395) than handler functions (370). #1 (RunEndpoint's transaction never rolling back a failed
> mutation) was already self-corrected in place in this very file (see the STRUCK block below) and is
> tracked as an ongoing architectural fact via `docs/01_POSTMORTEMS.md` PM-007, not an unfixed bug.
> **`FEATURE_PARITY_SPEC.md` is the current, actively-maintained source of truth.** The MEDIUM/LOW
> findings below (#11 onward) were NOT individually re-verified this pass - the sample size and hit
> rate above make them likely also fixed, but that is an inference, not a check; read one before
> trusting it fixed OR broken. This file stays as a historical record of a thorough, largely-heeded
> audit, not a place to look for new work.

Six independent adversarial auditors over everything implemented this session (and the
pre-existing code it touches). Read-only pass; findings ranked by severity.



---

# Dimension: anti-silence

I audited all 203 handlers (191 MifBridge + 12 MifKismetReconstructor) by segmenting every `H_*(In, Out)` body, diffing doc-comment `in:`/`out:` keys against keys the code actually reads/emits, and checking guard coverage, loop-drop paths, and write-verification. The bridge was answering during the audit, so guard-absence claims below are live-confirmed.

---

## CRITICAL

**1. `RunEndpoint` never cancels the transaction on failure — `ok:false` is delivered with the partial mutation committed**
`MifBridgeCommon.cpp:597-599`
```cpp
FScopedTransaction Transaction(FText::Format(LOCTEXT("BridgeEditFmt", ...)));
if (Fn) { (*Fn)(In, Out); } else { Ext->Handler(In, Out); }
}   // commits unconditionally
```
`Transaction.Cancel()` appears nowhere in the plugin (verified by grep across all 41 files). 23 transacted handlers reach a `Fail(Out, ...)` *after* they have already called `Modify()`/mutated: `add_pin` (Nodes.cpp:1339,1366,1380), `add_tree_widget` (Widgets.cpp:242,257), `add_variable` (Introspect.cpp:817,829,860), `add_component` (Components.cpp:96), `override_inherited_component` (Inherited.cpp:875,979), `remove_pin` (Nodes.cpp:1550), `write_datatable_rows`, `delete_material_expression`, `connect_material_expressions`, `add_custom_event`, `add_nav_volume`, `add_interface`, `implement_interface_function`, `set_actor_transform`, `set_component_transform`, `add_material_expression`, `connect_material_property`, `add_foliage_instances`, `delete_datatable_rows`, `set_sublevel_visibility`, `spawn_actor_in_pie`.
**Why it matters:** this is the mirror image of the ok:true-having-done-nothing bug and is worse — the caller is told the operation failed, reasonably assumes nothing changed, and the edit is already in the asset. It also silently breaks `00_ARCHITECTURE.md`'s stated contract ("Ctrl-Z undoes the entire bridge action") because the user must now *know* to undo a call that reported failure. Every "mutate-then-Fail" finding below inherits its severity from this one.
**Fix (one place, ~4 lines):** `IsOk()` already exists at `MifBridgeCommon.cpp:610`.
```cpp
FScopedTransaction Transaction(...);
if (Fn) { (*Fn)(In, Out); } else { Ext->Handler(In, Out); }
if (!IsOk(Out)) { Transaction.Cancel(); }
```
~~This makes every handler atomic-on-failure for free and retires ~20 individual "restructure to validate-before-write" TODOs.~~

> **STRUCK — this sentence was false, and it is the origin of the false guarantee.** `Transaction.Cancel()` **discards the undo entry; it reverts NOTHING, for any object, transactional or not.** `UTransBuffer::Cancel` (`Editor/UnrealEd/Private/EditorTransaction.cpp:1387-1437`) broadcasts `TransactionCanceled`, ends the operation, nulls `GUndo` and pops the transaction off `UndoBuffer` — **it never calls `FTransaction::Apply()`**, whose only callers are `UTransBuffer::Undo` (`:1624`) and `::Redo` (`:1688`). A cancelled transaction is one that was *thrown away*, not one that was *undone*.
>
> The `Cancel()` was still added and is still worth having — without it a failed call leaves a bogus entry on the undo stack and the user's next Ctrl-Z undoes a bridge action that reported failure instead of their own last edit. But it retired **no** TODO. The ~20 "restructure to validate-before-write" items were real work, and the fix above did not do any of it. Proven live: `override_inherited_component` returned `ok:false` and the ICH override template it had minted first was still on the asset, with `queueLength` unchanged at 0.
>
> **What is actually true today.** There is **no blanket rollback**. Atomicity is a property of the order each handler is written in, nothing more:
> - **5 handlers were reordered to validate before creating** — `override_inherited_component`, `add_component`, `add_foliage_instances`, `add_timeline`, `create_material_instance`.
> - **4 more name what they leave behind** and how to remove it, because a reorder was not safe — `add_pin`, `recipe_add_debug_print`, `create_struct`, `set_variable_flags`.
> - Every other mutating handler is atomic on failure only if it happens to validate first. Assume it does not.
>
> Full account: `docs/01_POSTMORTEMS.md` **PM-007**; per-handler table in `06_IMPLEMENTED.md` § *Batch M*.

**2. `set_variable_default` wipes the default when `value` is absent or is not a JSON string, and never verifies the write**
`MifBridgeIntrospect.cpp:947` and `:960`
```cpp
const FString Value = JStr(In, TEXT("value"));   // no HasField check
...
Var.DefaultValue = Value;                        // raw assignment, no type validation
...
Out->SetStringField(TEXT("default"), Value);     // echoes the REQUEST, not a read-back
```
`JStr` → `TryGetStringField` → `FJsonValue::TryGetString` returns **false** for array, object and null (`JsonValue.h:69`), so `JStr` falls through to `""`. Three live failure modes, all `ok:true`:
- `{name:"Health", defaultValue:"100"}` (the key `add_variable` uses is `default`, this endpoint's is `value` — two sibling endpoints, two spellings, neither documented, neither guarded) → **Health's default is wiped to `""`**.
- `{name:"Items", value:["a","b"]}` → wiped to `""`. This is byte-for-byte the bug `set_property` was hardened against (`MifBridgeNodes5.cpp:840`: *"JStr returned "" for a JSON array and FArrayProperty accepted "" as 'empty the array' WITH SUCCESS"*), still live in the sibling endpoint.
- `{name:"Health", value:"banana"}` on an int → stored verbatim, `ok:true, default:"banana"`.
**Why it matters:** PM-003 exactly — a call that failed to specify destroyed the value it was meant to set — plus the array bug the audit already paid for once.
**Fix:** require `In->HasField(TEXT("value"))` and Fail otherwise; accept the `default`/`defaultValue` aliases; route the value through `JsonToPropertyText` (already in `MifBridgeNodes5.cpp`) against `Var.VarType`; snapshot `Var.DefaultValue` before and emit `valueBefore`/`valueAfter`/`changed` the way `set_property` does.

**3. `batch`'s inline backup is a degraded copy of `backup_blueprint` and can silently produce no backup while reporting one**
`MifBridgeNodes.cpp:1759-1771` vs the correct `MifBridgeIntrospect.cpp:157-188`
```cpp
const FString FileName = FPackageName::LongPackageNameToFilename(
    Package->GetName(), FPackageName::GetAssetPackageExtension());   // hardcoded .uasset
if (FPaths::FileExists(FileName))
{
    IFileManager::Get().Copy(*(FileName + TEXT(".bak")), *FileName, true, true);  // return DISCARDED
    Out->SetStringField(TEXT("backup"), FileName + TEXT(".bak"));                 // claimed regardless
}
```
Four defects, all on the safety net, all silent:
- **`.umap`**: `backup_blueprint` fixed this exact bug (its comment at :166-169 documents it) by branching on `Package->ContainsMap()`. This copy did not get the fix, so for a World package the `.uasset` path does not exist, `FileExists` is false, and `backup:true` **silently produces no backup at all**.
- `IFileManager::Copy` returns `uint32` (`FileManager.h:111`); `backup_blueprint` checks `== COPY_OK` and Fails, this one discards it — `Out["backup"]` can name a `.bak` that was never written.
- Asset not on disk → `backup_blueprint` Fails, this one silently skips.
- `blueprintId`/`path` absent or unresolvable → silently no backup (line 1759 short-circuits), then batch proceeds to mutate.
**Why it matters:** a caller passes `backup:true` precisely because the next thing is destructive. All four paths hand back `ok:true` and let the batch run against a backup that does not exist.
**Fix:** extract `bool BackupPackage(UPackage*, FString& OutPath, FString& OutError)` from `H_backup_blueprint` into `MifBridgeHandlers.h`, call it from both, and make `backup:true` with a failed/skipped backup a `Fail` on `batch` (not a silent proceed).

---

## HIGH

**4. `rename_variable` and `remove_variable` report success for confirmed no-ops; `rename_variable` can also block the game thread on a modal**
`MifBridgeIntrospect.cpp:912` and `:935`
Both engine calls are `void` and early-return when the variable is absent (`BlueprintEditorUtils.cpp:4609-4610` for Remove, `:4823-4824` for Rename), and neither handler checks existence:
- `remove_variable {name:"Typo", confirm:true}` → `ok:true, removed:"Typo"`, nothing removed. Also silently no-ops on an *inherited* variable (only `Blueprint->NewVariables` is searched).
- `rename_variable {oldName:"Typo", newName:"Health", confirm:true}` → `ok:true, name:"Health"`, nothing renamed. Same when `newName == oldName` (`:4821`).
- Worse: if the variable has a RepNotify function, `RenameMemberVariable` calls `VerifyUserWantsRepNotifyVariableNameChanged` (`:4837`), which pops an `FSuppressableWarningDialog` — a **modal, on the game thread, inside the HTTP handler**. The bridge hangs until a human clicks. If they click *No*, the engine reverts the name (`:4841`) and the handler still returns `ok:true, name:<NewName>`. This is the same modal hazard `delete_asset` guards against with `bShowConfirmation=false` and `save_dirty_packages` documents at length — `rename_variable` got neither treatment.
**Why it matters:** `delete_datatable_rows` in the same plugin does this correctly (`MifBridgeDataTables.cpp:670-674` emits `notFound[]`). This is drift, not an unknown.
**Fix:** call `FBlueprintEditorUtils::FindNewVariableIndex` first and Fail with the near-miss names when `INDEX_NONE`; refuse `newName == oldName`; refuse a rename when `NewVariables[i].RepNotifyFunc != NAME_None` with a message pointing at `set_variable_flags` to clear it first — never let the engine's modal path be reachable from HTTP.

**5. Three splice paths break the exec chain, then discard `TryCreateConnection`, then report a count of links they may not have made**
`MifBridgeRecipes.cpp:41,42,50` (`SpliceAfter`, used by `recipe_add_debug_print` / `recipe_reset_and_loop` / `recipe_override_and_call_parent`), `MifBridgeRecipes.cpp:360,369,372` (`recipe_splice_before_parent`), `MifBridgeNodes.cpp:1716,1717,1726` (`splice_into_exec`)
```cpp
Schema->BreakPinLinks(*AfterOut, true);          // destroy first, unconditionally
Schema->TryCreateConnection(AfterOut, CallIn);   // bool DISCARDED (EdGraphSchema.h:777)
for (UEdGraphPin* Target : OldTargets) { ... Schema->TryCreateConnection(CallOut, Target); }  // DISCARDED
return OldTargets.Num();                          // reported as reconnectedTargets / upstreamCount
```
`SpliceAfter`'s own comment says *"Atomically insert"* — it is not atomic. If either connection is refused (wrong pin type, wildcard that did not resolve, single-link exec already occupied), the exec chain is left **severed** and the caller gets `ok:true` with `reconnectedTargets: N` / `upstreamCount: N` naming links that do not exist. Combined with finding #1 there is no rollback. Contrast `DoConnect` (`MifBridgeNodes.cpp:596-606`), which does it right: `CanCreateConnection` → Fail on `CONNECT_RESPONSE_DISALLOW` → report the actual `connected` bool.
**Why it matters:** a silently broken exec chain compiles clean and fails at runtime — the `add_cast`/PM-001 profile, in the endpoints whose entire selling point is "one call wires the whole cluster".
**Fix:** pre-check every pair with `CanCreateConnection` **before** the `BreakPinLinks`, Fail on DISALLOW; then count actual `TryCreateConnection` successes and emit that count (not `OldTargets.Num()`), failing if it is short. Note `DoConnect` has the same ordering bug in its `bBreakFirst` branch (`MifBridgeNodes.cpp:592-593` runs before the `CanCreateConnection` at `:596`), so `reconnect_pin` destroys the old wires and *then* returns `ok:false` on a disallowed connection.

**6. `paint_landscape` never checks the layer belongs to the landscape — its own error message promises a check the code does not perform**
`MifBridgeLandscape.cpp:493-499`
`LoadLayerInfo` just `LoadObject`s any `ULandscapeLayerInfoObject` from any path. The failure message says *"it must be one of the layers this landscape's material declares"* — but nothing tests that. `Info->Layers` is right there and `landscape_info` already iterates it (`:748`). `FLandscapeEditDataInterface::SetAlphaData` on an unregistered layer does not no-op: it takes the `UpdateLayerIdx == INDEX_NONE` branch (`LandscapeEditInterface.cpp:2797`) and **allocates a new weightmap channel**, and because `bWeightAdjust` normalises, it pushes the real layers' weights down. A later `FixupWeightmaps` then deletes the allocation with a MapCheck warning (`LandscapeEdit.cpp:929-931`), so the paint appears and later vanishes.
**Why it matters:** `ok:true, verticesTouched:N` for a call that dimmed the layers you *were* using and painted one that will be garbage-collected. Same shape as the RVT postmortem: the endpoint succeeded and broke something the caller was not looking at.
**Fix:** after `LoadLayerInfo`, `if (Info->GetLayerInfoIndex(LayerInfo) == INDEX_NONE) Fail(...)` listing the layer names from `Info->Layers`.

**7. `set_spline_points` clears the spline before validating any point, and silently drops non-object entries**
`MifBridgeWorld.cpp:257` then `:263`
```cpp
Spline->ClearSplinePoints(false);            // destroy first
...
if (!Val.IsValid() || !Val->TryGetObject(Obj) || !Obj) { continue; }   // silent skip
```
`points: [[0,0,0],[100,0,0]]` (arrays rather than `{x,y,z}` — the obvious guess, and there is no guard to reject it) returns `ok:true, pointCount:0` **with the existing route destroyed**. `ReadVec` also defaults typo'd keys to 0 with no report. Additionally `snapToGround` is honoured only when `space != "local"` (`:267`) — with `space:"local"` it is silently ignored.
**Why it matters:** PM-003 shape on live patrol routes; `pointCount:0` is technically present but `ok:true` says the call worked.
**Fix:** parse all points into a `TArray<FVector>` and Fail with the offending index *before* `ClearSplinePoints`; Fail when `snapToGround` is combined with `space:"local"` rather than dropping it; add `RejectUnknownParams`.

**8. `batch` silently discards non-object entries in `ops[]`**
`MifBridgeNodes.cpp:1790-1794`
```cpp
if (!OpValue.IsValid() || !OpValue->TryGetObject(OpObjectPtr) || OpObjectPtr == nullptr) { continue; }
```
The entry never appears in `results[]` and `opCount` under-counts it. `batch {ops:["add_branch"]}` (strings instead of objects) → `ok:true, opCount:0, results:[]`. Empty `ops` likewise.
**Why it matters:** batch is the highest-traffic mutating endpoint and its response *is* the audit trail; a dropped op is invisible in exactly the artefact you would check.
**Fix:** emit `{ok:false, error:"ops[i] is not an object"}` into `results[]` and set `bAllOk = false`; Fail on an empty `ops` array.

**9. `spawn_actor_in_pie` is the unfixed sibling of the `spawn_actor_in_level` postmortem**
`MifBridgePIE.cpp:504` — no `RejectUnknownParams`, no `mesh`/`staticMesh` support.
`spawn_actor_in_pie {actorClass:"StaticMeshActor", mesh:"/Game/..."}` reproduces the postmortem exactly: a bare `AStaticMeshActor`, `ok:true`, empty bounds. The fix landed on one endpoint and was never swept to its sibling.
Related, same class: `spawn_actor_in_level`'s `in:` line (`MifBridgeLevel.cpp:202`) **still reads `{ actorClass, location?, rotation?, scale?, label?, folder? }`** — `mesh`/`staticMesh` and the `class` alias were added to the code at `:255` but never to the contract comment, and there is no guard, so `material:` is still silently dropped there too.
**Fix:** port the `mesh` block from `MifBridgeLevel.cpp:255-282` to `H_spawn_actor_in_pie`; add `RejectUnknownParams` to both with a KeyNote for `material`; update the `in:` line at `MifBridgeLevel.cpp:202`.

**10. Guard and contract coverage: 132 of 203 handlers have no `RejectUnknownParams`; 89 have neither a guard nor an `in:`/`out:` block**
Live-confirmed read-only: `list_variables {blueprintId:..., totallyBogusKey:123}` → `{"ok":true,"count":0,"variables":[]}`; same for `list_graphs` and `describe_class`.
Whole files with **zero** guards, all of them mutating: `MifBridgeLandscape.cpp` (create/sculpt/paint/bind), `MifBridgeWorld.cpp` (new_level/load_level/set_spline_points/snap_actors_to_ground), `MifBridgeWidgets.cpp`, `MifBridgeComponents.cpp`, `MifBridgeInterfaces.cpp`, `MifBridgeDelegates.cpp`, `MifBridgeUserTypes.cpp`, `MifBridgeRecipes.cpp`, `MifBridgeNavigation.cpp`, `MifBridgeViewport.cpp`, `MifBridgePIE.cpp`, `MifBridgeIntrospect.cpp`, `MifBridgeNodes2/3/4/6/7.cpp`. 108 handlers have no `in:` doc line at all, so for over half the surface **there is no contract to cross-check** — which is why this bug class keeps coming back one endpoint at a time.
**Fix (architectural, stops the regression):** move the accepted-key list into registration. Change `MIF_BIND` to carry `{FHandlerFn, std::initializer_list<const TCHAR*> Keys, const TCHAR* Summary}` and have `RunEndpoint` apply `RejectUnknownParams` centrally before dispatch. A missing list then becomes a visible registry hole rather than an invisible one, and `self_audit` (`MifBridgeCommon.cpp:480`) can emit `hasParamGuard` per endpoint alongside `bucket` — making coverage a number you can watch instead of a sweep you have to redo.

---

## MEDIUM

**11. `create_material_instance` — two live silent drops plus a dead output variable**
`MifBridgeAuthoring.cpp:450, 457, 470`. `TArray<TSharedPtr<FJsonValue>> Unknown;` is declared at `:450` and **never written or emitted** — the vestige of the `unknownParameters` reporting `set_material_parameter` has and this does not. A `scalars` entry that is not a number (`:457`) or a `vectors` entry that is not an object (`:470`) is skipped in silence and simply not counted in `parametersApplied`; unlike its sibling, it never checks the parent exposes the name, so `parametersApplied` counts writes the material ignores. The TODO at `:401-409` documents all of this and it is still live. **Fix:** lift the validate-then-write bracket from `set_material_parameter` (`:599-666`) — with #1 in place, the "half-configured asset" objection in that TODO disappears.

**12. Unresolved `actorPaths[]` entries are silently dropped in `snap_actors_to_ground` and `duplicate_actors`**
`MifBridgeWorld.cpp:371`, `MifBridgeAuthoring.cpp:335`. In `snap_actors_to_ground`, if *every* path is bogus the else-branch at `:375` is never reached, so `Targets` is empty and the response is `ok:true, considered:0, snapped:0, missed:0` — a total no-op reported as success. `duplicate_actors` also silently swallows `if (!Copy) { continue; }` (`MifBridgeAuthoring.cpp:376`), so `duplicated` can be short of `sourceCount × count` with no reason given. **Fix:** both already have the right pattern next door — `select_level_actors` (`MifBridgeLevel.cpp:441-449`) emits `notFound[]`. Copy it, and Fail when the resolved target set is empty.

**13. `add_component` silently drops `location`/`rotation`/`scale` for non-scene components**
`MifBridgeComponents.cpp:110-127` — the transform block is inside `if (USceneComponent* SceneTemplate = Cast<...>)`. Adding a `UAudioComponent`-style non-scene component with a transform returns `ok:true` having ignored it. `set_component_transform` (`:245-250`) Fails correctly in the same situation, so the two disagree. **Fix:** if any of the three keys is present and the template is not a `USceneComponent`, Fail with the same message `set_component_transform` uses.

**14. `add_timeline` silently discards its entire configuration when the template lookup fails**
`MifBridgeNodes3.cpp:110-141` — `length`, `autoPlay`, `loop` and `floatTracks[]` all live inside `if (Template)`. A null `Template` yields a bare timeline, `ok:true`, and `floatTracks` is not even present in the response. Per-item: a non-string or empty entry in `floatTracks[]` is skipped at `:127-131` without report. **Fix:** Fail when `FindTimelineTemplateByVariableName` returns null; Fail (or report `skipped[]`) on bad track entries.

**15. `TrySetDefaultValue` return discarded — a rejected pin default is invisible**
`MifBridgeNodes.cpp:1654` (`set_pin_default`) and `:1390` (`add_pin`). The schema silently refuses a value that does not parse for the pin type. `set_pin_default` at least re-serialises the pin so the truth is *in* the payload, but there is no `changed`/`applied` flag and no Fail; `add_pin`'s `out:` block (`:1157`) does not mention the default at all, so the caller has nothing to inspect. **Fix:** snapshot `Pin->DefaultValue`/`DefaultObject` before and after, emit `defaultBefore`/`defaultAfter`/`changed`, and Fail when the caller asked for a change that did not land — the same shape `set_property` uses at `MifBridgeNodes5.cpp:935-950`.

**16. `add_tree_widget` silently drops `x`/`y`/`autoSize` for non-canvas parents, and orphans a widget on `AddChild` failure**
`MifBridgeWidgets.cpp:252-268`. The placement block is inside `if (UCanvasPanelSlot* CSlot = Cast<...>)`; adding to a `VerticalBox` with `x:100, y:50` returns `ok:true` having ignored both. Separately, `AddChild` failing at `:255` Fails *after* `ConstructWidget` at `:236`, leaving an orphan `UWidget` in the tree's outer (see #1). No guard, no doc block. **Fix:** Fail when placement keys are given for a non-canvas slot; on `AddChild` failure, `NewWidget->MarkAsGarbage()` before failing (or rely on #1).

**17. DataTables: `ok:true` on a replace that wrote nothing, silent per-row drops, and a missing guard on the destructive sibling**
`MifBridgeDataTables.cpp:542` — the `replace` path sets `replaced:false` and returns **without** `Fail` when `CreateTableFromJSONString` reports problems, i.e. `ok:true` for a call that changed nothing. `:569-572` skips a non-object `rows[]` entry with no warning while the missing-`Name` case right below it *does* warn (`:580`). `:665-669` skips a non-string `rowNames[]` entry silently. And `delete_datatable_rows` (`:642`) has **no `RejectUnknownParams`** while its sibling `write_datatable_rows` (`:494`) does — in the same file. **Fix:** `Fail` when `bReplaced` is false; add the missing per-entry warnings; add the guard to `delete_datatable_rows`.

**18. `create_blueprint` documents `overwrite?: false`, which no line reads**
`MifBridgeNodes2.cpp:1061` advertises it; `:1157-1163` says *"Overwrite is deliberately NOT supported here"* and there is no guard, so `overwrite:true` is dropped and the caller gets *"a Blueprint already exists"* with no hint that the parameter they passed does nothing. Third instance of the same class this session (after `create_material_instance.textures` and `duplicate_actors.rotationOffset`). The `in:` line also omits `blueprintType`, which the handler does read (`:1082`) and which caused PM-002. **Fix:** delete `overwrite` from the doc line, add `RejectUnknownParams` with a KeyNote explaining it, add `blueprintType` to the `in:` line.

**19. `set_actor_transform`'s `relative` applies to location and rotation but not scale**
`MifBridgeLevel.cpp:332-337` — `relative:true` deltas `Location` and `RotVec` only; `Scale` stays absolute. The doc line (`:303`) says `relative?` with no qualification. **Fix:** either multiply scale, or Fail when `relative` is combined with `scale`.

---

## LOW

**20.** `describe_animation` documents `numKeys?` in its `out:` block (`MifBridgeAnimation.cpp:90`); the literal appears nowhere else in the plugin — a documented field that is never emitted.
**21.** `sculpt_landscape`'s unknown-`mode` refusal lives *inside* the per-vertex loop (`MifBridgeLandscape.cpp:432-436`), so when the brush covers zero vertices (radius smaller than one quad) an invalid mode is never detected and the call returns `ok:true, verticesTouched:0`. Also `amount` is read only for raise/lower and `targetZ` only for flatten — the other combinations are silently ignored. **Fix:** validate `mode` against the allowlist before the loop, and Fail on an inapplicable `amount`/`targetZ`.
**22.** `add_switch_string` silently drops non-string / empty `cases[]` entries (`MifBridgeNodes3.cpp:305-311`); `cases` is undocumented (no doc block).
**23.** `set_material_parameter` never calls `MIC->Modify()` (TODO at `MifBridgeAuthoring.cpp:513-515`), so its writes sit outside the blanket transaction and Ctrl-Z does not restore the previous parameter values. Not an anti-silence bug, but it means `undo_transactions` reports success over an edit it did not revert.

---

## Clean

- **MifKismetReconstructor** — 12/12 handlers carry `KrRejectUnknownParams` **and** an `in:`/`out:` block. `kr_reconstruct_request` (`:2107-2124`) states cross-parameter rules as explicit errors rather than dropping them, and uses `KeyNote` to refuse `open`/`save`/`compile`/`wait` by name. No silent-drop path found in this dimension.
- **`MifBridgeStreaming.cpp`** — the reference implementation. `set_sublevel_visibility` (`:842-960`) reads back **every** write, records each field that did not take in `ignored[]` with a reason, and Fails when nothing took. `add_sublevel`/`remove_sublevel` report deferred ops with poll instructions rather than claiming completion.
- **`set_property`** (`MifBridgeNodes5.cpp:751`) — guard, PM-003 scratch-buffer import, before/after/staged three-way compare, `coerced` reporting, and a hard Fail when the import succeeded but the readback is byte-identical. This is the bar the rest should be measured against.
- **`MifBridgeUndo.cpp`** — `undo_transactions`/`redo_transactions` distinguish partial progress (`stoppedEarly` + `reason`) from zero progress (Fail), and `save_dirty_packages` enumerates skips explicitly.
- **`MifBridgeAssetOps.cpp`** — `audit_unused`'s exclusion arithmetic is numerically checkable by construction (`refsTotal - refs == excludedRefs == excludedReferencers.length`), and `backup_blueprint` (`Introspect.cpp:157`) is the correct backup implementation that #3 should be calling.
- Confirm-gating (dimension e) is **complete**: all 25 destructive endpoints I checked (`delete_asset`, `rename_asset`, `delete_level_actor`, `remove_component`, `remove_function`, `remove_interface`, `remove_variable`, `rename_variable`, `remove_node`, `remove_pin`, `rename_function`, `rename_event`, `rename_event_dispatcher`, `remove_struct_member`, `remove_enum_value`, `write_datatable_rows`, `delete_datatable_rows`, `remove_inherited_component`, …) gate on `confirm=true`. I found no destructive op missing its gate.

---

**Verdict:** The anti-silence invariant is enforced *exemplarily* in the endpoints it was consciously applied to (Streaming, set_property, Undo, AssetOps, all of MifKismetReconstructor) and essentially unenforced across the 132 handlers that never received a guard — but the highest-value finding is not the coverage gap: it is that `RunEndpoint` commits the transaction on `ok:false` (`MifBridgeCommon.cpp:597-599`), which silently inverts the invariant for the 23 handlers that mutate before they fail, and one four-line `Transaction.Cancel()` retires most of the deferred "restructure to validate-before-write" TODOs at once.

---

# Dimension: registry-buckets

## Findings — registry integrity and transaction buckets

---

### 1. HIGH — `describe_class` MCP tool is 100% broken: sends `className`, handler reads only `class`
**`D:/DDS2SDK/Game/Plugins/MifBridge/tools/ue5-mcp-bridge/server.py:855`** vs **`D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeIntrospect.cpp:317`**

```python
return _post("describe_class", className=class_name)      # server.py:855
```
```cpp
const FString Name = JStr(In, TEXT("class"));             // MifBridgeIntrospect.cpp:317
if (Name.IsEmpty()) { Fail(Out, TEXT("class is required")); return; }
```
`H_describe_class` reads exactly one key, `class`, and has **no** `RejectUnknownParams` guard — so `className` is silently dropped and the endpoint answers `"class is required"` to a caller that plainly supplied a class. Every MCP invocation of this tool fails, and the error blames the caller for omitting something it did send. Batch B3's own live-proof recipe (`06_IMPLEMENTED.md:111`) is written as a raw curl with `{"class": "Actor"}`, which is why the bucket move was proven while the wrapper stayed broken.

**Fix (pick one):** either `server.py:855` → `_post("describe_class", **{"class": class_name})`, or (better, matches house alias style) make the handler `JStrAny(In, { TEXT("class"), TEXT("className") })` and add a `RejectUnknownParams` guard so the next mismatch is named rather than silent.

---

### 2. HIGH — `list_enum_values` MCP tool is 100% broken: sends `enumName`, handler reads only `enum`
**`server.py:861`** vs **`MifBridgeNodes3.cpp:200`**

```python
return _post("list_enum_values", enumName=enum_name)      # server.py:861
```
```cpp
const FString Name = JStr(In, TEXT("enum"));              # MifBridgeNodes3.cpp:200
if (Name.IsEmpty()) { Fail(Out, TEXT("enum is required")); return; }
```
Identical failure shape to #1, identical blind spot (unguarded handler). Note that two *other* tools in the same file already spell this key correctly for a different endpoint — `server.py:485` and `server.py:507` send `enumName` to `add_switch_enum` / `add_enum_literal`, which **do** read `enumName`. So `enumName` is the plugin's usual spelling and `list_enum_values` is the odd one out.

**Fix:** make `H_list_enum_values` read `JStrAny(In, { TEXT("enum"), TEXT("enumName") })` — that aligns it with its two siblings rather than making the wrapper the exception — and add the missing guard.

---

### 3. HIGH — `batch` cannot dispatch any external (`kr_*`) endpoint; the reconstructor's compensating code is dead
**`MifBridgeNodes.cpp:1778` and `:1805`**

```cpp
const TMap<FString, FHandlerFn>& Registry = Handlers();        // :1778  built-ins ONLY
...
else if (const FHandlerFn* Fn = Registry.Find(OpName)) { ... } // :1805
else { Fail(OpOut, FString::Printf(TEXT("unknown op: '%s'"), *OpName)); }
```
`RunEndpoint` (`MifBridgeCommon.cpp:572-598`) resolves built-ins **then** `ExternalRegistry()`. `H_batch` consults only `Handlers()`. Every one of the 12 `kr_*` endpoints therefore returns `unknown op: 'kr_dump_blueprint'` inside a batch — the seven ReadOnly ones for no reason at all.

This also makes an explicit source claim false in two places:
- `MifKrBridgeEndpoints.cpp:131-140` special-cases the `op` routing key "or every ReadOnly `kr_*` endpoint fails with 'unrecognised parameter op' the moment it is called inside batch". That code path is unreachable — a `kr_*` op never reaches `KrRejectUnknownParams` at all.
- `MifBridgeCommon.cpp:322-323` says "Externals are first-class from here down"; they are first-class for routing and for `self_audit`, but not for the one endpoint whose entire purpose is composition.

**Fix:** export a lookup from `MifBridgeCommon.cpp` (e.g. `const FExternalEndpointDesc* FindExternalEndpoint(const FString&)` declared in `MifBridgeHandlers.h`, since `ExternalRegistry()` is a file-static) and mirror `RunEndpoint`'s order at `:1805`: `Registry.Find(OpName)` → external → fail. The `IsCompileHeavyEndpoint(OpName)` gate at `:1801` already handles external `SelfManaged` correctly (it derives from `IsSelfManagedEndpoint`, which consults the external registry), so the four Wave-3 `kr_*` endpoints stay correctly fenced out for free.

---

### 4. MEDIUM — `create_material_instance` is in the transacted bucket, and two source comments assert it is not
**`MifBridgeCommon.cpp:435-438`**, **`MifBridgeAuthoring.cpp:401-403`**, **`MifBridgeAuthoring.cpp:441-482`**

`IsSelfManagedEndpoint` justifies `create_material` / `create_material_function` with:
> `// New-asset creation with explicit AssetCreated + MarkPackageDirty — the`
> `// create_material_instance precedent (untransacted).`

and `H_create_material_instance`'s own TODO says its silent drops are "deliberately not fixed here, because it creates the asset before applying parameters **(self-managed bucket, no blanket transaction)**".

`create_material_instance` appears in **neither** bucket set — it is transacted. Both comments are false, and the "precedent" cited to place its two siblings does not exist. Behaviourally it does exactly what the self-managed siblings do — `FactoryCreateNew` a new `RF_Transactional` asset, `MIC->PostEditChange()` (`:480`), `FAssetRegistryModule::AssetCreated` (`:481`), `Package->MarkPackageDirty()` (`:482`) — but does it *inside* `RunEndpoint`'s `FScopedTransaction`. `UMaterialInstance::PostEditChangeProperty` runs `InitResources()` + `UpdateStaticPermutation()` (UE532 `MaterialInstance.cpp:4053, 4066`), i.e. material-resource/static-permutation rebuild, which is the same shader-state-teardown family the file cites as the reason `recompile_material` is self-managed. `MarkPackageDirty` inside the transaction also records an `FPackageRecord` for a package that has never existed on disk.

**Fix:** add `TEXT("create_material_instance")` to the `SelfManaged` set beside `create_material` at `MifBridgeCommon.cpp:438` (which is what both comments already claim), and drop the now-redundant "(self-managed bucket…)" parenthetical from `MifBridgeAuthoring.cpp:403` only if you instead decide to leave it transacted — do not leave the code and the two comments disagreeing.

---

### 5. MEDIUM — three pure asset-registry reads are still in the transacted bucket (the empty-undo-entry class, remaining instances)
**`MifBridgeAssetOps.cpp:261` `H_get_referencers`**, **`:292` `H_get_dependencies`**, **`:439` `H_audit_unused`**

All three are `Registry().GetReferencers()` / `GetDependencies()` / `GetAssets()` plus JSON serialisation. Zero `Modify()`, zero `MarkPackageDirty`, zero object creation — verified by reading the full bodies. None is in `IsReadOnlyEndpoint`, so `RunEndpoint:597` wraps each in `FScopedTransaction` and every call pushes an empty `"Mif Bridge: get_referencers"` entry onto the undo stack. That is exactly the defect Batch B3 fixed for `describe_class`/`list_enum_values` and Batch B fixed for `list_transactions`/`list_dirty_packages`.

The omission is demonstrably an oversight, not a choice: their immediate file-neighbours and shape-twins `find_assets` and `describe_package` **are** in the read-only set (`MifBridgeCommon.cpp:369`). `audit_unused` is the worst of the three — it iterates the whole corpus and is the endpoint most likely to be run repeatedly while tuning `excludeReferencers`.

**Fix:** add `TEXT("get_referencers"), TEXT("get_dependencies"), TEXT("audit_unused")` to the `ReadOnly` set at `MifBridgeCommon.cpp:369`, next to `find_assets`/`describe_package`.

---

### 6. MEDIUM — `create_blueprint`'s `overwrite` parameter does nothing, and the handler's own doc comment advertises it
**`server.py:297,299`** vs **`MifBridgeNodes2.cpp:1061`** vs **`MifBridgeNodes2.cpp:1157-1162`**

```python
def create_blueprint(path, parent_class="Actor", overwrite: bool = False)   # server.py:297
return _post("create_blueprint", path=path, parentClass=parent_class, overwrite=overwrite)
```
```cpp
//   in:  { path: ..., parentClass?: "Actor" (default), overwrite?: false }   // :1061
...
// Refuse to clobber silently ... Overwrite is deliberately NOT supported      // :1157
Fail(Out, ...("a Blueprint already exists at '%s' — pick a new path or delete it first"));
```
`overwrite` is read nowhere in the handler, and `H_create_blueprint` has no `RejectUnknownParams` guard, so it is silently dropped. An agent that sets `overwrite=True` gets a bare "already exists" failure with nothing saying the flag it just used is not implemented. The `in:` comment at `:1061` contradicts the decision recorded 96 lines below it in the same function.

**Fix:** drop `overwrite` from the tool signature and from `_post` (`server.py:297,299`), delete `overwrite?: false` from the `in:` line at `MifBridgeNodes2.cpp:1061`, and — since this handler is unguarded — add a `RejectUnknownParams` with a `KeyNote` for `overwrite` reading "not supported — delete the existing asset first (delete_asset)". That converts the next occurrence from silence into a named refusal, per the house rule at `MifBridgeHandlers.h:63-71`.

---

### 7. LOW — `build_navmesh` is transacted but records nothing
**`MifBridgeNavigation.cpp:108-133`**

The handler validates, calls `Nav->Build()`, and returns. It calls `Modify()` on nothing; navmesh tile generation is asynchronous and happens over subsequent frames, outside the transaction entirely. Result: one empty undo entry per call, same class as #5. Its stated precedent is already in the codebase — `start_pie`/`stop_pie` are read-only because they "only QUEUE a request" (`MifBridgeCommon.cpp:356-357`), which is precisely what `build_navmesh` does.

**Fix:** add `TEXT("build_navmesh")` to the read-only set beside `nav_status`.

---

### 8. LOW — `create_struct` / `create_enum` mint new assets inside the blanket transaction, unlike every other asset-minting endpoint
**`MifBridgeUserTypes.cpp:273-274` (`H_create_struct`)**, **`:491-492` (`H_create_enum`)**

Both do `CreatePackage` → `NewObject` → `FAssetRegistryModule::AssetCreated` → `Package->MarkPackageDirty()` while transacted. Every other endpoint with that exact shape is self-managed (`create_blueprint`, `create_material`, `create_material_function`) or should be (#4). Ctrl-Z after `create_struct` rolls the struct back to its first-`Modify()` state (placeholder members) rather than removing it, leaving a half-built asset in the content browser.

Lower severity than #4 because the engine itself transacts user-defined-struct edits — `FStructureEditorUtils::AddVariable`/`RemoveVariable` open their **own** `FScopedTransaction` (UE532 `StructureEditorUtils.cpp:285, 322`) around the reinstance-and-recompile in `OnStructureChanged`, so the struct compile inside a transaction is engine-sanctioned and is **not** the dead-CDO hazard. Only the package/asset-creation half is out of line.

**Fix:** either move both to `SelfManaged` for consistency with the other four asset creators, or add a one-line comment at `MifBridgeCommon.cpp:438` recording why struct/enum creation is the deliberate exception. Do not leave it undocumented — it currently reads as an oversight.

---

### 9. LOW — stale counts, paths and line references in the very documents that define this invariant
All independently verifiable, all wrong now:

| File:line | Says | Actual |
|---|---|---|
| `docs/00_ARCHITECTURE.md:107-116` | server.py lives at `C:\Users\andre\Documents\GitHub\Eddie_v2\tools\ue5-mcp-bridge\server.py`; exposes **82 of 102** endpoints; lists 20 endpoints with no MCP tool | That path **does not exist** (`Eddie_v2/tools` is absent). The file is at `D:/DDS2SDK/Game/Plugins/MifBridge/tools/ue5-mcp-bridge/server.py`, and all 20 listed endpoints now have tools |
| `README.md:46` | "silently (82 tools against 102 endpoints)" | 203 tools / 203 endpoints — parity, so the README documents drift that no longer exists |
| `README.md:127` | "## Capabilities (102 HTTP endpoints)" | 191 built-in + 12 external = 203 |
| `Public/MifBridgeEndpointRegistry.h:4` | "`MifBridgeCommon.cpp:29-245` (176 built-ins live)" | `MifBridgeCommon.cpp:34-267`, **191** built-ins |
| `Public/MifBridgeEndpointRegistry.h:30` and `MifBridgeCommon.cpp:281, :323` | "`MifBridgeServer.cpp:88-108`" (route-bind loop) | `MifBridgeServer.cpp:117-138` |
| `Public/MifBridgeEndpointRegistry.h:53` | "policyContradictions, `MifBridgeCommon.cpp:390-401`" | `MifBridgeCommon.cpp:539-547` |
| `Public/MifBridgeEndpointRegistry.h:57` | "IsCompileHeavyEndpoint … `MifBridgeCommon.cpp:416`" | `MifBridgeCommon.cpp:556` |
| `Public/MifBridgeEndpointRegistry.h:61` | "`MifBridgeCommon.cpp:445`" (the `FScopedTransaction`) | `MifBridgeCommon.cpp:597` |
| `MifBridgeNodes.cpp:1740` | "`op` … tolerated centrally (`MifBridgeCommon.cpp:669`)" | `MifBridgeCommon.cpp:710` |
| `MifKrBridgeEndpoints.cpp:4116` | "the **six** reads are ReadOnly" | **seven** (`kr_reconstruct_status` is the seventh) |

The architecture doc also still describes the source layout as ending at `Nodes6.cpp` (`00_ARCHITECTURE.md:44`) with no row for `Nodes7`, `Streaming`, `PIE`, `Materials`, `Undo`, `UserTypes`, `Landscape`, `Spatial`, `World`, `Level`, `Viewport`, `Navigation`, `Inherited`, `Cooked`.

**Fix:** regenerate the counts and re-anchor the line refs in the same commit that lands any of #1–#8; the `sed`-based parity command at `00_ARCHITECTURE.md:118-124` still works once its `<path>` is pointed at the real server.py — it just needs the plugin-side list unioned with `kr_*`, which now has no automated source.

---

## Clean — verified, no problems found

- **`MIF_DECL` set == `MIF_BIND` set, exactly.** 191 each; zero in DECL-not-BIND, zero in BIND-not-DECL, zero duplicates on either side. The only non-conforming lines in either file are the `#define`/`#undef` pair and three prose comments.
- **`IsReadOnlyEndpoint` ∩ `IsSelfManagedEndpoint` = ∅.** 59 read-only, 26 self-managed, 106 transacted, 191 total; every name in both sets exists in `MIF_DECL` (no orphan bucket entries). `self_audit`'s `policyContradictions` (`MifBridgeCommon.cpp:539-547`) computes the same check at runtime from the same predicates dispatch uses, not a second copy.
- **Every full-compile handler is self-managed.** Grepping `FKismetEditorUtilities::CompileBlueprint` / `CompileSynchronously` / `CompileBlueprintInto` / `AssetCreated` / `CollectGarbage` / `CreateNewMapForEditing` / `RemoveLevelsFromWorld` across all 41 plugin `.cpp` files and mapping each hit to its enclosing handler: the only compiles outside `SelfManaged` are `compile` and `validate`, which sit in `ReadOnly` (also untransacted, so the hazard is fenced) and are additionally force-added by `IsCompileHeavyEndpoint` at `MifBridgeCommon.cpp:564-565`.
- **World swap/teardown coverage is complete.** `new_level`, `load_level`, `save_level_as`, `add_sublevel`, `remove_sublevel`, `set_sublevel_streaming`, `pie_load_level_instance`, `pie_unload_level_instance` are all self-managed, each with a specific engine-source citation.
- **`IsCompileHeavyEndpoint` derives from `IsSelfManagedEndpoint`** rather than duplicating a literal list, so external `SelfManaged` endpoints are fenced out of `batch` automatically — the drift the old hardcoded list had is structurally gone.
- **External `kr_*` endpoints: exactly one bucket each, by construction.** 12 `Reg()` calls ↔ 12 `H_kr_*` definitions; bucket is a single `EEndpointBucket` enum field so the twin-set contradiction is unrepresentable; `Provider` set on all 12; registration is refused after `MarkRouteTableLive()` (`MifBridgeServer.cpp:141`), which fires after the bind loop. **Zero `kr_` occurrences in `MifBridgeHandlers.h` or `MifBridgeCommon.cpp`** — the by-design absence holds.
- **server.py endpoint coverage is exact.** 203 `@mcp.tool` decorators ↔ 203 endpoints (191 built-in + 12 external): no tool posts to a non-existent endpoint, no endpoint lacks a tool, no duplicate tool names, no endpoint served by two tools. All 203 defs are above the `__main__` guard at `server.py:1743`. Only two tools are named differently from their endpoint (`compile_blueprint`→`compile`, `validate_blueprint`→`validate`), which is deliberate and does not break the `_post`-based parity diff.
- **No tool sends a parameter that a guarded handler would reject.** Comparing every `_post` kwarg against each handler's `RejectUnknownParams` accepted set: **zero** hard mismatches across all 203 tools. (The three real mismatches — findings #1, #2, #6 — are all against *unguarded* handlers, which is exactly why they survived.)
- **No guard advertises a parameter that is never read.** The one candidate (`audit_unused.excludeReferencers`) is read via the spellings table at `MifBridgeAssetOps.cpp:335`.

---

**Verdict:** The `MIF_DECL`/`MIF_BIND` invariant and the three-bucket policy are structurally sound — sets are symmetric, buckets are disjoint, and no compile-heavy or world-swapping handler is transacted — but the bucket table has three pure reads and one asset-minting endpoint in the wrong bucket (one of them contradicted by its own source comments), `batch` silently cannot reach any external `kr_*` endpoint, and two MCP tools (`describe_class`, `list_enum_values`) are non-functional due to parameter-name drift against unguarded handlers.

---

# Dimension: unity-and-dupes

## DIMENSION: duplicated / divergent helpers + unity-build hazards

**Ground truth used** (not inferred): the live unity blobs at
`D:/DDS2SDK/Game/Plugins/MifBridge/Intermediate/Build/Win64/x64/UnrealEditor/Development/MifBridge/Module.MifBridge.{1,2,3}.cpp` (regenerated 22:06:44 by the running build):

| blob | files | bytes |
|---|---|---|
| 1 | `MifBridge.cpp` … `MifBridgeLevel.cpp` (15) | 402,324 |
| 2 | `MifBridgeMaterials.cpp` … `MifBridgeStreaming.cpp` (16) | 440,803 |
| 3 | `MifBridgeUndo.cpp` … `MifBridgeWorld.cpp` (5) | 94,982 |

I reproduced UBT's partitioning exactly (alphabetical, close blob when cumulative ≥ `NumIncludedBytesPerUnityCPP` = 393,216; no override in `%APPDATA%/Unreal Engine/UnrealBuildTool/BuildConfiguration.xml` or either `.Target.cs`). That model is what makes the severities below numeric rather than hand-wavy.

**The rule that matters and is not written down anywhere in `docs/`:** `[namespace.unnamed]/1` — *all* unnamed-namespace-definitions **in one translation unit** are the **same** namespace. `namespace MifBridge { namespace { … } }` in two .cpp files that share a blob is **one** `MifBridge::<unique>`. Anonymous namespaces give you exactly zero protection in a unity build; `static` gives you zero protection too, because two `static` functions of the same name at the same namespace scope in one TU are a redefinition. This is why `EmitAssetIdentity` / `CollectPIEWorlds` blew up with C2084.

---

### CRITICAL — 1. `EditorWorld()` is defined twice, verbatim, and is ~8 KB of source from a hard C2084

- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeStreaming.cpp:89` — `UWorld* EditorWorld()`, in `namespace MifBridge { namespace { … } }`
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeWorld.cpp:36` — `UWorld* EditorWorld()`, same nesting, **byte-identical body**

Identical name, identical signature, same (merged) unnamed namespace. Today they survive only because Streaming is the last file of blob 2 and World is in blob 3. That is an accident of file sizes, and the margin is tiny. Sensitivity sweep (I ran the exact UBT partition against perturbed sizes):

- **+8,021 bytes** added to *any* of the 14 files `MifBridge.cpp` … `MifBridgeLandscape.cpp` pushes `MifBridgeLevel.cpp` out of blob 1 → blob 2 becomes `Level…Spatial` → **`MifBridgeStreaming.cpp` lands in blob 3 with `MifBridgeWorld.cpp`** → `error C2084: 'UWorld *MifBridge::<unnamed-namespace>::EditorWorld(void)': function already has a body`.
- **+16,652 bytes** added to any of `Materials` … `Spatial` does the same thing from the other side.

8 KB is ~200 lines. This session added 64 KB to Streaming alone. This fires on the next batch, and the failure will look unrelated to whoever triggers it.

**Fix (do this one first):** promote a single `UWorld* EditorWorld();` to `MifBridgeHandlers.h` (next to `CollectPIEWorlds`, which took exactly this route), define it once in `MifBridgeCommon.cpp`, delete both file-local copies, and fold in `MifBridgePIE.cpp:53 GetEditorWorld()` (third byte-identical copy, different name) at the same time.

---

### HIGH — 2. `JsonTypeName(EJson)` is duplicated **and the two copies already disagree**

- `MifBridgeAuthoring.cpp:107` — `const TCHAR* JsonTypeName(EJson Type)`, in `MifBridge::<anon>`; `EJson::Boolean` → **`"boolean"`**
- `MifBridgeNodes5.cpp:187` — `static const TCHAR* JsonTypeName(EJson T)`, at `MifBridge` scope; `EJson::Boolean` → **`"bool"`**

Two defects in one:

1. **Live, caller-visible divergence (not latent).** `set_material_parameter` (Authoring:169, 588, 608) refuses with *"…got boolean"* while `set_property` (Nodes5:293…524, via `RefuseValue`) refuses with *"…got bool"*. Two endpoints spell the same JSON type two ways in error text a caller is expected to parse. This is the drift class the codebase claims to fix on sight.
2. **Build hazard, C2668 not C2084.** `MifBridgeNodes5.cpp` has **no** anonymous namespace (verified: all 14 of its file-scope helpers are directly in `namespace MifBridge`). Co-locate the two files and every call site in Nodes5 sees both `MifBridge::JsonTypeName` and `MifBridge::<unnamed>::JsonTypeName` via the unnamed namespace's implicit using-directive → *ambiguous call to overloaded function*. Blobs 1 vs 2 today.

**Fix:** promote one `const TCHAR* JsonTypeName(EJson);` into `MifBridgeHandlers.h` / `MifBridgeCommon.cpp`, delete both, and pick one spelling deliberately (`"boolean"` matches the JSON spec noun; changing Nodes5's is the caller-visible edit — note it).

---

### HIGH — 3. `NormalizeBoolLiteral` duplicated; the eviction clause has no trigger

- `MifBridgeInherited.cpp:334` — in `MifBridge::<anon>`
- `MifBridgeNodes5.cpp:69` — `static`, at `MifBridge` scope

Same signature, same semantics (bodies differ only in brace style). Same C2668 shape as #2 (Nodes5's call sites are directly in `namespace MifBridge`). Blobs 1 vs 2 today.

`MifBridgeInherited.cpp:323-330` documents the duplication with an "EVICTION CLAUSE … the moment the ownership fence lifts". **The fence has lifted** — this audit is the trigger and nothing else will be. An eviction clause with no scheduled trigger is a permanent duplicate. Same block also covers `ResolvePropertyPath` (see #6b).

**Fix:** promote `NormalizeBoolLiteral` to `MifBridgeHandlers.h` / `MifBridgeCommon.cpp`; delete both copies; delete the eviction-clause comment (a clause that has been honoured must not stay as a standing invitation).

---

### HIGH — 4. `Vec3` — two definitions **already sharing blob 2**, surviving only on arity

- `MifBridgeSpatial.cpp:59` — `TSharedRef<FJsonObject> Vec3(const FVector& V)`
- `MifBridgeStreaming.cpp:315` — `TSharedRef<FJsonObject> Vec3(double X, double Y, double Z)`

Both in `MifBridge::<anon>`, **both in blob 2 right now** — this is the only intra-blob duplicate name in the module today. It compiles solely because the arities differ, so the two files silently share one cross-file overload set: `Vec3(SomeFVector)` written in `MifBridgeStreaming.cpp` resolves to Spatial's implementation and nobody would notice. Any signature change on either side (adding an `FVector` overload in Streaming, or normalising Spatial's to 3 doubles) is an immediate C2084 in the current layout.

**Fix:** promote one `TSharedRef<FJsonObject> Vec3(const FVector&);` (plus a 3-double overload if wanted) to `MifBridgeHandlers.h` / `MifBridgeCommon.cpp` and delete both. This one is not a "next batch" risk — it is a same-blob landmine today.

---

### MEDIUM — 5. `ValidateNewAssetPath` — same name, different signature, different contract

- `MifBridgeMaterials.cpp:698` — `bool ValidateNewAssetPath(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out, FString& OutPath, FString& OutAssetName)`
- `MifBridgeUserTypes.cpp:38` — `bool ValidateNewAssetPath(const FString& Path, FString& OutAssetName, FString& OutError)`

Both `MifBridge::<anon>`. Blobs 2 vs 3. If co-located they merge into one overload set with two entirely different validation policies and two different failure conventions (writes `Fail(Out)` vs returns an error string). A future 3-arg call added to `MifBridgeMaterials.cpp` would compile and silently run UserTypes' rules.

**Fix:** rename at minimum (`ValidateNewMaterialAssetPath` / `ValidateNewUserTypePath`); better, promote one path-validation helper and have Materials' JSON-shaped wrapper call it.

---

### MEDIUM — 6. Body-identical helpers under different names (divergence risk, no build error)

Measured with normalised-body diff over all 414 file-scope free functions:

**(a) Five byte-identical actor finders** — one fix will land in one of them:
`MifBridgeAuthoring.cpp:53 FindActor` · `MifBridgeNavigation.cpp:38 FindNavActor` · `MifBridgeSpatial.cpp:44 FindActorByPathOrLabel` · `MifBridgeViewport.cpp:71 FindVpActor` · `MifBridgeWorld.cpp:41 FindWorldActor` (all `MifBridge::<anon>`, ratio 1.00). **Promote one `AActor* FindActorInWorld(UWorld*, const FString&)`.**

**(b) Three copies of the property dot-walk:**
`MifBridgeNodes5.cpp:679 ResolvePropertyPath` · `MifBridgeInherited.cpp:346 ResolvePropertyPathLocal` (ratio **1.00**) · `MifBridgeNodes6.cpp:58 ResolveReadPropertyPath` (0.98, `const void*&` read variant). PM-003's write bracket exists in triplicate; a PM-003-class fix applied to one leaves the other two vulnerable. **Promote one, with a `const`/non-`const` pair.**

**(c) `MifBridgeDelegates.cpp:23 ParseDispatcherParams` vs `MifBridgeNodes2.cpp:80 ParsePinSpecs`** — ratio ~1.00, identical signature shape `(In, Field, TArray<TPair<FName,FEdGraphPinType>>&, FString&)`. Delegates' comment says "kept file-local to avoid header/type coupling", but `MifBridgeHandlers.h` already forward-declares `struct FEdGraphPinType` (line 22) and declares `MakePinType` — the stated reason does not hold. **Promote `ParsePinSpecs`.**

**(d) `MifBridgeStreaming.cpp:138 NormalizeLevelPackagePath` vs `MifBridgeWorld.cpp:58 PackagePathToMapFilename`** — 0.83; the first 20 lines are identical and the Streaming copy carries its own eviction clause (`Streaming.cpp:134-137`, citation to `MifBridgeWorld.cpp:58` is **correct**). Same "no trigger" problem as #3. **Promote the normalisation half; leave the `LongPackageNameToFilename` tail in World.**

**(e) Behavioural divergence, not just duplication — five "current world" helpers, two different answers:**
| helper | prefers PIE world? |
|---|---|
| `MifBridgeSpatial.cpp:36 SpatialWorld` | **yes** (`GEditor->PlayWorld` first) |
| `MifBridgeNavigation.cpp:30 NavWorld` | **yes** (identical body, ratio 1.00) |
| `MifBridgeStreaming.cpp:89 EditorWorld` | no |
| `MifBridgeWorld.cpp:36 EditorWorld` | no |
| `MifBridgePIE.cpp:53 GetEditorWorld` | no |

During PIE, `check_overlaps`/`scene_report`/`move_actor_to` answer about the **play** world while `snap_actors_to_ground`/`get_spline_points`/`list_sublevels` answer about the **editor** world — with no field in the response saying which. That is the same "silent wrong answer" axis `CollectPIEWorlds` was created to close. **Fix:** promote exactly two named helpers — `EditorWorld()` (editor context, always) and `ActiveWorld()` (PIE-preferring) — delete all five, and have every endpoint that can be called during PIE emit which one it used.

---

### MEDIUM — 7. `MifBridgeHandlers.h:279-286` mis-signals file ownership — the exact input that produces duplicates

The comment block "`// Inherited components (MifBridgeInherited.cpp) — …`" (line 279) is followed by four `MIF_DECL`s. Three are in `MifBridgeInherited.cpp` (`get_inherited_component` :649, `override_inherited_component` :791, `revert_inherited_component` :1002); the fourth, `MIF_DECL(set_component_transform)` at line 286, is defined in **`MifBridgeComponents.cpp:225`**. An agent working an ownership fence on "the inherited-components file" reads this header, believes it owns `set_component_transform`, doesn't find it, and writes a new one. **Fix:** move `MIF_DECL(set_component_transform);` under the `MifBridgeComponents.cpp` group, or split the comment.

---

### MEDIUM — 8. Stale line citations in the PUBLIC header, including a wrong endpoint count

`Source/MifBridge/Public/MifBridgeEndpointRegistry.h` is the cross-plugin contract and every load-bearing citation in it is wrong:

| claim in the header | actual |
|---|---|
| "`MIF_BIND`'d … in `Private/MifBridgeCommon.cpp:29-245` (**176** built-ins live)" | binds span **41-263**; there are **191** |
| "internal `FHandlerFn` (`Private/MifBridgeHandlers.h:24`)" | line **27** (24 is blank) |
| "`RejectUnknownParams`, `Private/MifBridgeHandlers.h:65-67`" | declared at **72** (65-67 is mid-comment) |
| "`policyContradictions`, `Private/MifBridgeCommon.cpp:390-401`" | **388** (comment) / **547** (the field) |
| "`IsCompileHeavyEndpoint` … `Private/MifBridgeCommon.cpp:416`" | **556** |
| "`RunEndpoint` wraps … `Private/MifBridgeCommon.cpp:445`" | **597** |
| "routes bound … `Private/MifBridgeServer.cpp:88-108`" | `Start()` at **103**, `GetEndpointNames()` at **117** |

`MifKismetReconstructor/Source/MifKismetReconstructor/Private/MifKrBridgeEndpoints.cpp:118` mirrors the stale `MifBridgeHandlers.h:65-67`. Wrong citations are the *mechanism* of this whole finding set: the next agent jumps to the cited line, finds nothing, and writes a local copy. **Fix:** re-derive the seven line refs and the 176→191 count in the same commit as any of the above.

---

### LOW — 9. No unity-build rule exists in the contract docs, and there is no postmortem for the C2084

`grep -i "unity|C2084|already has a body|eviction"` over `docs/00_ARCHITECTURE.md`, `docs/01_POSTMORTEMS.md`, `docs/02_GOTCHAS.md` → **zero hits**. `01_POSTMORTEMS.md` has PM-001…PM-004 plus two unnumbered entries; none covers the duplicate-helper build failure that already cost this session. Per the standing rule ("a bug that cost >30 minutes gets a postmortem so it never returns"), this bug class is currently undocumented and will return. **Fix:** add PM-005 stating (a) unnamed namespaces merge per-TU under unity, (b) `static` does not protect either, (c) before adding any file-local helper, `grep -rn "\<Name\>(" Private/*.cpp`, (d) blob membership is a function of file *sizes* and moves on its own — never rely on "they're in different blobs".

---

### Clean — verified, not assumed

- **New files are compile-plausible.** `MifBridgeInherited.cpp` and `MifBridgeStreaming.cpp`: every call name resolves to a file-local definition, a `MifBridgeHandlers.h` declaration, or an engine symbol whose header is included. The scary-looking `FMessageDialog::Open`, `FSuppressableWarningDialog::ShowModal`, `Algo::AnyOf`, `FBlueprintCompilationManager::CompileSynchronously`, `FSubobjectData::GetObjectForBlueprint`, `FKismetEditorUtilities::CompileBlueprint` occurrences are **comments only** — no missing include. The one real one, `FPropertyChangedEvent`/`EPropertyChangeType::ValueSet` at `MifBridgeInherited.cpp:504`, is covered by `UObject/UnrealType.h` (line 130); `ELevelVisibilityDirtyMode` at `MifBridgeStreaming.cpp:912` is covered by `EditorLevelUtils.h` (line 68).
- **Engine API signatures check out against UE 5.3 headers**: `UEditorLevelUtils::SetLevelVisibility` (EditorLevelUtils.h:282, 4-arg form matches), `RemoveLevelFromWorld` (:247), `SetStreamingClassForLevel` (:238), `MakeLevelCurrent(ULevel*,bool)` (:86), `AddLevelToWorld` (:223), `FLevelUtils::FindStreamingLevel(UWorld*,const TCHAR*)` (LevelUtils.h:44), `ULevelStreamingDynamic::LoadLevelInstance` (LevelStreamingDynamic.h:80 — the 8-arg overload the call at `MifBridgeStreaming.cpp:1229` uses), `ULevelStreamingAlwaysLoaded::ShouldBeLoaded` hardcoded `true` (LevelStreamingAlwaysLoaded.h:27, matching the comment at `Streaming.cpp:836`).
- **No cross-module private-header leak.** `MifKrBridgeEndpoints.cpp:60` includes only `MifBridgeEndpointRegistry.h` (Public). No reconstructor .cpp includes any MifBridge Private header.
- **Registry invariant holds exactly**: 191 `MIF_DECL` = 191 `H_*` definitions = 191 `MIF_BIND`, zero orphans, zero duplicate binds, zero handler defined twice.
- **No namespace-scope variable or type collisions** anywhere in the module. `FEntry` in `MifBridgeCooked.cpp:919` and `MifBridgeSpatial.cpp:116` are **function-local** structs — harmless.
- **No file-local helper shadows a `MifBridgeHandlers.h`-declared name.** `CompileBlueprintInto` (Introspect:991) and `CreateFunctionGraph` (Nodes2:40) are single external definitions, correct.
- **`ResolveClassAllowingCppPrefix` / `DoAddVariableNode`** (their own eviction clauses in the audit log) remain single-copy in `MifBridgeNodes.cpp` — those clauses were honoured.
- **`EmitAssetIdentity` and `CollectPIEWorlds` are genuinely fixed** — one definition each in `MifBridgeCommon.cpp:749` / `:758`, declared at `MifBridgeHandlers.h:81` / `:88`, no residual copies.
- **`MifKismetReconstructor` is clean.** All 15 .cpp files share **one** unity blob (`Module.MifKismetReconstructor.cpp`) — the worst-case configuration — and have **zero** duplicate free-function names (confirmed by two independent parsers). Headers there define no non-inline namespace-scope functions.

---

**VERDICT:** Not clean — five same-name duplicates survive (`EditorWorld`, `JsonTypeName`, `NormalizeBoolLiteral`, `Vec3`, `ValidateNewAssetPath`), `Vec3` is already co-located in blob 2 and `EditorWorld` becomes a hard C2084 after only ~8 KB of source growth anywhere in blob 1, `JsonTypeName`'s two copies have *already* diverged in caller-visible error text, and roughly a dozen more helpers are byte-identical under different names — the "eviction clause" convention is producing permanent duplicates because no clause has a trigger.

---

# Dimension: hazards

Audit complete. Findings for the **modal / blocking / GC / crash** dimension, ranked.

---

## 0. [CRITICAL — but already repaired at 22:15 while I was auditing] Orphaned `CollectPIEWorlds` body broke the build

`D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeStreaming.cpp`

At the 22:09 revision the de-duplication that promoted `CollectPIEWorlds` into `MifBridgeCommon.cpp:757` (declared `MifBridgeHandlers.h:88`) deleted only the **signature + `{` + `if (!GEngine) return;`** in this file and left the loop body behind at namespace scope:

```
// … Merge these two into a shared helper the next time MifBridgePIE.cpp is edited.
    for (const FWorldContext& Ctx : GEngine->GetWorldContexts())
    { … OutWorlds.Add(Ctx.World()); }
}            <- closed the anonymous namespace 300 lines early
```

`MifBridgePIE.cpp` got the same edit correctly (whole function removed). This was a hard compile error — undeclared `OutWorlds`, a statement at namespace scope, and unbalanced braces through EOF — i.e. the in-flight build could not have produced a MifBridge DLL. **Re-read at 22:15 shows it fixed** (file now 1389 lines, comment replaced with a "do NOT re-add a file-local copy" note). Reporting it because it confirms the build was broken, and because the live editor is still on the pre-Batch-I DLL: `self_audit` answers on 127.0.0.1:8791 with `endpointCount:165`, and **none** of `list_sublevels / add_sublevel / … / get_inherited_component / audit_unused / get_referencers / kr_drift_census` are registered. Nothing from this session has ever run.

---

## 1. HIGH — `audit_unused` blocks the game thread unboundedly; freezes the bridge on a well-formed request

`MifBridgeAssetOps.cpp:471`, `:473`, `:497-503`

```cpp
if (JBool(In, TEXT("rescan"), false)) { Reg.ScanPathsSynchronous({ Prefix }, true); }  // :471
Reg.WaitForCompletion();                                                               // :473
…
for (const FAssetData& A : Assets) { Reg.GetReferencers(A.PackageName, Refs); … }       // :497-503
```

Three separate unbounded stalls, all inline on the game thread (since `9a1add8` every handler runs inline in the HTTP ticker — `MifBridgeServer.cpp:259-264`):

- `ScanPathsSynchronous(Prefix, /*bForceRescan*/true)` with a caller-supplied prefix. `{"pathPrefix":"/Game","rescan":true}` is accepted (the only validation at `:449` is "non-empty and starts with `/`") and forces a full synchronous re-scan of all of `/Game`. On DDS2 that is minutes.
- `WaitForCompletion()` is **unconditional** — it blocks even when `rescan` was not asked for, until the background registry scan finishes.
- `limit` (`:465`, `:554`) caps only the output array. `Reg.GetReferencers` is deliberately called for *every* asset under the prefix ("continue, NOT break", `:556-562`), so cost is O(all assets under prefix) regardless of `limit`.

During any of these the `FTSTicker` stops, the socket is not read, and every other bridge call times out with no response — the exact §8 failure mode. `docs/02_GOTCHAS.md:510` requires "every endpoint spec must state its modal/blocking hazards"; the `audit_unused` comment block (`:434-438`) states none.

**Fix:** (a) refuse `rescan` for prefixes with fewer than 2 path segments (`/Game`, `/`), naming the reason; (b) replace the unconditional wait with `if (Reg.IsLoadingAssets()) { Fail(Out, "asset registry is still scanning — retry once it settles"); return; }`, or only wait when `rescan` was passed; (c) add a hard cap on `Assets.Num()` with an explicit `Fail` above it rather than scanning anyway; (d) document all three in the endpoint's comment block.

---

## 2. MEDIUM — `set_sublevel_visibility` / `set_current_sublevel` run a full synchronous level-streaming flush inside the handler *and* inside the blanket transaction

`MifBridgeStreaming.cpp:911` and `:1055`

Both call `UEditorLevelUtils::SetLevelVisibility` synchronously (`set_current_sublevel` reaches it via `MakeLevelCurrent`, `EditorLevelUtils.cpp:585`). That engine function:

- opens its own `FScopedTransaction` (`EditorLevelUtils.cpp:1198`),
- calls `Level->OwningWorld->FlushLevelStreaming()` (`:1234`) → **`FlushAsyncLoading()`** inside a `while (bLevelsPendingVisibility)` loop (`World.cpp:4533`, `:4544-4554`),
- registers/unregisters an entire level's actors via `AddToWorld` / `RemoveFromWorld` (`:1240-1250`).

Neither endpoint is in the SelfManaged set (`MifBridgeCommon.cpp:403-464` lists only `add_sublevel`, `remove_sublevel`, `set_sublevel_streaming`, `pie_load/unload_level_instance`), so both run inside `RunEndpoint`'s blanket `FScopedTransaction`, and neither defers.

This is internally inconsistent: the same file defers `add_sublevel` (`:518-522`) because `AddLevelToWorld` is "a registration cascade that must not ride RunEndpoint's blanket transaction", and `SetLevelVisibility` performs a strictly comparable cascade plus a blocking async-loading flush that `add_sublevel` does not have. The file's own hazard header (`:25-45`) enumerates "FOUR reachable ways to hang" and does not mention the flush at all.

**Fix:** move `set_sublevel_visibility` and `set_current_sublevel` into the SelfManaged bucket and route their `visible` / `MakeLevelCurrent` branches through the existing next-tick op log the way `add_sublevel` does; at minimum add the flush to the hazard header and to both endpoint comment blocks so the poll contract is honest.

---

## 3. MEDIUM — the "FOUR reachable hazards, all made unreachable" claim in `MifBridgeStreaming.cpp:25-45` is not exhaustive

Verified in `D:/UE532` — three more exist on the wrapped paths:

- **`EditorLevelUtils.cpp:387-388`** — `UEditorLevelUtils::AddLevelToWorld` *itself* unconditionally runs `FScopedSlowTask SlowTask(0, …); SlowTask.MakeDialog();`. Reached by `add_sublevel` (`MifBridgeStreaming.cpp:625`) and by `set_sublevel_streaming` via `SetStreamingClassForLevel` (`EditorLevelUtils.cpp:531`, bridge `:1153`). It is a progress window, not a user-blocking modal — but while it is up, `FFeedbackContextEditor` ticks **Slate only** (`FeedbackContextEditor.cpp:419-441`), never `FTSTicker`, so the HTTP server is unreachable for the whole level load. Both call sites are deferred so no response is pending, but concurrent requests stall.
- **`EditorLevelUtils.cpp:527`** — `check(Level->OwningWorld)` in `SetStreamingClassForLevel`. The bridge pre-checks `check(InLevel)` (:516) and `check(Level)` (:525) at `MifBridgeStreaming.cpp:1114` and `:1148`, but not this third one. One extra `if (!Streaming->GetLoadedLevel()->OwningWorld)` in the deferred lambda closes it.
- **`EditorLevelUtils.cpp:1237` / `:1255`** — `check(Level->bIsVisible == bShouldBeVisible)` immediately after the flush. Reachable in principle from `set_sublevel_visibility`. In practice it holds for editor worlds (`World.cpp:3121`: `bConsiderTimeLimit &= bMatchStarted && bIsGameWorld`, so `AddToWorld` never returns partial in-editor), so this is **not** a live crash — but it does mean the graceful branch at `MifBridgeStreaming.cpp:919-923` ("SetLevelVisibility did not take … the level streaming flush did not complete this frame") is **dead code**: the engine asserts on that exact condition before it can return. Either delete the branch or state that it is unreachable, so a future reader does not treat it as the safety net.

---

## 4. MEDIUM — `pie_load_level_instance`'s failure discrimination is unreachable, so the name-collision case reports the wrong cause

`MifBridgeStreaming.cpp:1230-1246`

```cpp
Fail(Out, bFound ? "…already exists…pass a different nameOverride…"
                 : "no level package '%s' in the asset registry…");
```

`ULevelStreamingDynamic::LoadLevelInstance(Params, bOutSuccess)` sets `bOutSuccess = false` on entry (`LevelStreaming.cpp:2495`), and `LoadLevelInstance_Internal` returns `nullptr` on the already-exists branch (`:2545-2551`) **without** setting it. So whenever `Instance == nullptr`, `bFound` is always `false`: a caller who passes a colliding `nameOverride` is told the package does not exist in the asset registry, which is false and sends them chasing the wrong thing. The comment at `:1237-1239` ("true-with-null means a level instance under that name already exists") describes a state the engine never produces.

**Fix:** detect the collision before the call — replicate `LevelStreaming.cpp:2545`'s predicate (`GetStreamingLevels().ContainsByPredicate` on `UWorld::ConvertToPIEPackageName(name, PIEInstance)`) when `nameOverride` is non-empty — or fold both possibilities into one message.

---

## 5. LOW — deferred-op ring can silently drop a mutating call's result

`MifBridgeStreaming.cpp:289` — `while (OpLog().Num() > 16) OpLog().RemoveAt(0);`

`FinishOp` (`:305-320`) walks the ring and returns silently when the id is gone. If more than 16 deferred verbs (`add_sublevel` / `remove_sublevel` / `set_sublevel_streaming`) are issued before the next tick, the earliest opIds are evicted before their lambdas run and their outcomes vanish — `list_sublevels.ops[]` will never contain them and the caller polls forever. That is the "never silence a mutating call" failure `02_GOTCHAS` exists to prevent, in the one place designed to prevent it. **Fix:** never evict an entry with `bCompleted == false` (evict the oldest *completed* entry instead), or log a warning when `FinishOp` finds no matching id.

---

## 6. LOW — undeclared blocking render-thread sync in a read endpoint

`MifBridgeCooked.cpp:958-1001` — `ENQUEUE_RENDER_COMMAND(MifBridgeLandscapeDraws)(…)` followed by `FlushRenderingCommands()`. Bounded, but it is a hard game/render-thread sync per call with the ticker stopped; on a heavy landscape scene that is tens-to-hundreds of ms of bridge unavailability, and the endpoint's comment does not say so. The `FPrimitiveSceneProxy*` raw pointers captured at `:951` are safe only because nothing else runs on the game thread between the gather and the flush — worth stating in the comment rather than leaving it to be inferred.

---

## 7. LOW — `save_dirty_packages` is not as prompt-free as its header claims

`MifBridgeUndo.cpp:648` → `FEditorFileUtils::SaveLevel` → `SaveWorld` opens `FScopedSlowTask … MakeDialog(true)` **per map** (`FileHelpers.cpp:767-768`). Same ticker-stall class as finding 3; the header at `:535-551` enumerates three hazards it avoids and does not mention this fourth.

Credit where due, verified correct: the read-only pre-check at `MifBridgeUndo.cpp:630` is exactly what makes `FileHelpers.cpp:756`'s `FMessageDialog` unreachable, and `GetDirtyWorldPackages` / `GetDirtyContentPackages` (`FileHelpers.cpp:5173`, `:5298`) really are GC-free as claimed.

---

## What is clean

- **Raw un-rooted `UObject*` across ticks / across HTTP calls: clean.** No static `UObject*` cache anywhere in either plugin. Every deferred lambda in `MifBridgeStreaming.cpp` (`:602`, `:734`, `:1139`) captures `TWeakObjectPtr<UWorld>` + package-name **strings** and re-resolves after the tick; every KR plan struct (`MifKrBridgeEndpoints.cpp:1851`, `:2870`) and `FKrSweepState` (`:3090`) holds paths only, and `MifKrJobManager.h:41` holds strings/ints/`TSharedPtr<FJsonObject>`.
- **`CollectGarbage` in a handler: clean.** The only two in new code (`MifKrBridgeEndpoints.cpp:3339`, `:3501`) run in next-tick sweep slices, between Blueprints, with no live raw pointers — matching the engine harness cadence.
- **`recompile_material` (`MifBridgeMaterials.cpp:1547`)**: verified correct. `UMaterialEditingLibrary::RecompileMaterial`'s tail really does run `CollectGarbage` twice and `FScopedSlowTask::MakeDialog(true)`; the handler bypasses it and replicates only the non-blocking core, and `UpdateMaterialFunction` (`MaterialEditingLibrary.cpp:985-1032`) really is enqueue-only as claimed.
- **`add_sublevel` / `remove_sublevel` / `set_sublevel_streaming` modal guards**: all four documented dialogs verified against `EditorLevelUtils.cpp:441-451`, `:588`, `:832`, `:896` and `check(Level)` at `:525`. The guards replicate the engine's own predicates (`FLevelUtils::FindStreamingLevel`, `IsLevelLocked`, `IsDirty`) rather than a paraphrase, and are re-run inside the deferred lambdas. The refusal to call `MakeLevelCurrent(persistent)` before removal (`:727-731`) is correct reasoning — it would have *added* the dialog.
- **`FinishAllCompilation` / `FlushShaderCompiles` / user-facing `FMessageDialog` / `OpenFileDialog` in new bridge code: none.** `shader_compile_status` polls `GShaderCompilingManager` read-only.
- **`MifBridgeServer.cpp:226-301`** (the `9a1add8` inline-dispatch fix): the off-game-thread path's shared `FMifPendingCall` lifetime and the "do not touch `Pending->Out` on timeout" reasoning at `:289-291` are correct.
- **`check()`s reachable from bad input: none found** in the named files. `MifBridgeCommon.cpp:1363`'s `check(Fields.size() > 0)` is on a compile-time `initializer_list`, not on request data. The `2a78a9a` cooked-`EditorData` fix is the right shape (guarded in the one resolver, not per-handler).

---

**Verdict: not clean — one build-breaking regression (self-repaired mid-audit) plus one HIGH unbounded game-thread stall in `audit_unused` and one MEDIUM undeclared `FlushAsyncLoading` in the two non-deferred sublevel endpoints; the level-streaming modal guards themselves are genuinely correct and verified against engine source.**

---

# Dimension: doc-truth

# Documentation truthfulness audit — findings

Ground truth I established first (bridge is down; everything below is from source + git):

- **Built-in endpoints in the registry today: 191** (`grep -c "MIF_DECL("` = 192 and `grep -c "MIF_BIND("` = 192, each including the `#define` line; MIF_DECL and MIF_BIND name-sets are **identical, diff empty**).
- **External endpoints registered: 12** (`MifKrBridgeEndpoints.cpp:4123–4175`).
- **server.py: 203 `@mcp.tool`** — name-set diff against 191+12 is **empty in both directions**. server.py is fully in sync.
- Live `self_audit.endpointCount` after the running build will therefore be **203**, not any number this log states.

---

### 1. CRITICAL — `06_IMPLEMENTED.md:545` "Batch D verdict: COMPLETE — 10 endpoints live-proven" is false for 4 of the 10

The recorded proof chain (`:522–528`) evidences only **6**: `create_material`, `add_material_expression`, `list_material_expressions`, `recompile_material`, `shader_compile_status`, `layout_material_expressions`.

- `create_material_function` — grep of the whole 3,725-line file: appears at `:336` (spec), `:481` (deviation), `:520` (bucket list). **No call, no response, anywhere.**
- `delete_material_expression` — appears at `:385` (spec), `:596/:627/:638` (D.1 prose), `:703/:709` (curls *to run*). **Never executed.**
- `connect_material_expressions` / `connect_material_property` — Batch D's planned steps 3–5 (`:455–461`) demanded `connectionCount: 2` and two `propertyBindings` rows. Not in the proof chain. Their only recorded outcome is D.1's block at `:998–1001`, which reports **`connectionCount:1`**.
- Batch D's own **Finding D-1** (`:533–535`) states the addressing those connect calls used *failed*: "Addressing by `ParameterName` … or by a unique class short name … fails."

`:518–520` ("all ten buckets exactly as specified") is a *registration* check being read as a *functional* proof.

**Why it matters:** this is the flagship Tier-0 batch. A next session will treat `delete_material_expression` and `create_material_function` as exercised code and build a material pipeline on top of two endpoints that have never been called once.

**Fix:** rewrite `:545` to "6 of 10 live-proven — `create_material_function` and `delete_material_expression` NOT exercised; `connect_material_expressions`/`connect_material_property` first exercised in D.1 with `connectionCount:1`." Strike or annotate the numbers at `:455–461` as never achieved.

---

### 2. CRITICAL — `06_IMPLEMENTED.md:1384` "Wave 1 (8 kr_* endpoints) … BUILD PASS, ALL PROVEN" is contradicted by its own body

- **`kr_analyze_ubergraph`**: no recorded result anywhere. Only planned curls at `:1129–1131`. Its stated pass conditions (`invariant` ends in `holds`, `unreached == 0`, `events.recovered == events.total`) were never reported. The build-pass block `:1402–1422` simply does not mention it.
- **`kr_reconstruct_request` function mode**: pass conditions at `:1144–1148` require `compile.measured:true`, `compile.errors == 0`, `result.graphNodes > 2`. The recorded result (`:1416–1419`) is a **copy-mode** child of `RaidAreaSphere`. The function-mode branch — the one that runs `MifReconstructFunctionIntoGraph` + `CompileBlueprint` with an `FCompilerResultsLog` — has zero evidence.
- **Busy-slot refusal**: `:1424` "**Not exercised**".

So the heading claims 8/8 where the transcript supports 6/8 minus one branch.

**Fix:** retitle to "6 of 8 proven" and list the three gaps inline, next to the heading, not 40 lines below it.

---

### 3. HIGH — Five shipped endpoints have no section in the implementation log at all

Registry went **175 → 180 built-ins** between Batch E and Batch I with nothing recorded. Verified by diffing `MIF_DECL` sets across git:

| Endpoint | Commit |
|---|---|
| `delete_datatable_rows` | `64d0a04` |
| `add_enhanced_input_action` | `d432712` |
| `get_referencers`, `get_dependencies`, `audit_unused` | `a245cce` |

All five are in `MifBridgeHandlers.h`, `MifBridgeCommon.cpp` and `server.py` today.

Batch I *noticed* the drift and rationalised it away instead of investigating: `:2641–2643` — *"the brief's stated starting figure said 181 raw each. Actual on-disk state … raw DECL 182 / raw BIND 184."* That 5-endpoint delta is the whole answer and it was left unexplained.

**Why it matters:** the file's opening line calls itself the implementation log with "one section per batch". Anything absent reads as not existing. `audit_unused` in particular is the subject of Batch H, which extends an endpoint the log never records as having landed.

**Fix:** add a short "landed outside this log" table naming the five, their commits, and their spec status.

---

### 4. HIGH — Every gating `self_audit` assertion in the file is wrong and will fail after the pending build

| Line | Asserts | Reality after this build |
|---|---|---|
| `:925` | `endpointCount == 177` | Was already wrong when written — recorded result 30 lines later (`:978`) is **176** |
| `:1086` | `endpointCount == 183 and externalEndpointCount == 8` | **203 / 12** |
| `:2451` (H-1) | `endpointCount == 188` | **203** |
| `:2787` (Batch I gate 0) | "188 endpoints" | **203** |

**Why it matters:** these are written as *gates*. An agent running H-1 or the Batch I gate after the build sees an assertion failure and concludes the build is broken, when it is correct.

**Fix:** replace the hard-coded totals with `endpointCount == <baseline> + <delta>` read from a pre-build call, or update all four to 203/12 and add a note that any future batch must re-baseline.

---

### 5. HIGH — Batch R phase 1 states one endpoint count in its plan and a different one in its result, and the plan was never struck

- `:804` — "`grep -c` = **176 / 176**"
- `:826–827` — "**176 / 176 before and after this batch**"
- `:941` — "`self_audit.endpointCount` is **177** (176 built-ins + 1 external)"
- `:967–969` — "Endpoint count 176 → **177** … MIF_DECL / MIF_BIND stay at **176** each"

Then `:978` — "`endpointCount 176 (175 built-in + 1 external)`" and `:986` — "MIF_DECL == MIF_BIND == **175** real invocations".

Root cause: the plan read the raw `grep -c "MIF_DECL("`, which counts the `#define MIF_DECL(Name)` line. Batch I diagnosed exactly this at `:2634–2639`, but Batch R's stale numbers were never corrected. The same conflation is why finding #4 exists.

**Fix:** correct `:804/:826/:941/:967–969` to 175/176, and state once (near the ground rules at `:8`) that the checkable invariant is the *unique-name set*, never a raw grep count.

---

### 6. HIGH — `01_CATALOGUE.md` and `02_RANKED.md` mark almost nothing as implemented

- `01_CATALOGUE.md`: **zero** implementation markers. `grep -c "✅"` = 0; no "implemented"/"shipped"/"delivered" anywhere.
- `02_RANKED.md`: exactly **5** ✅, all on one line (`:283`) — `list_transactions`, `undo_transactions`, `redo_transactions`, `list_dirty_packages`, `save_dirty_packages`, i.e. Batch C only.

> **CORRECTED 2026-07-29 — the "41" below was overstated and is the number that circulated.** It
> counted a *behaviour-change* entry as delivered whenever the endpoint **NAME** was live. For those
> entries the name was live *before the catalogue was written* — the entry asks for a change to what
> the endpoint DOES, so a live name delivers nothing. **Eight of the 29 built-ins listed below are
> behaviour-change entries**, and none of the eight had had its specced change land at the time of the
> R3 reconciliation: `add_component`, `connect_pins`, `list_components`, `list_variables`,
> `pie_status`, `read_modloader_log`, `rename_function`, `snap_actors_to_ground`.
>
> By this section's own method the corrected figure is **33** (21 built-in names + 12 `kr_*`), not 41.
> The authority is `work/R3_REMAINING_WORK.md`, which reconciled all **250** catalogue rows line by
> line against source and `self_audit` on 2026-07-28: **34 SHIPPED · 2 SHIPPED (PARTIAL) · 9
> SUPERSEDED · 2 WITHDRAWN · 203 STILL OPEN**. Per-entry status for the eight, with current source
> evidence, is in `06_IMPLEMENTED.md` § *Delivery-status correction*.

**~~41~~ catalogue/ranked entries are live today.** The ~~29~~ built-ins are: `add_component` *(behaviour change — NOT landed)*, `add_material_expression`, `add_sublevel`, `connect_material_expressions`, `connect_material_property`, `connect_pins` *(behaviour change — NOT landed)*, `create_material`, `create_material_function`, `delete_material_expression`, `layout_material_expressions`, `list_components` *(behaviour change — landed later, in Batch N, source-only)*, `list_dirty_packages`, `list_material_expressions`, `list_sublevels`, `list_transactions`, `list_variables` *(behaviour change — NOT landed)*, `pie_status` *(behaviour change — PARTIAL)*, `read_modloader_log` *(behaviour change — NOT landed)*, `recompile_material`, `redo_transactions`, `remove_sublevel`, `rename_function` *(behaviour change — NOT landed)*, `save_dirty_packages`, `set_current_sublevel`, `set_sublevel_streaming`, `set_sublevel_visibility`, `shader_compile_status`, `snap_actors_to_ground` *(behaviour change — PARTIAL)*, `undo_transactions` — plus all 12 `kr_*`.

And **`PROGRESS.md:228` states "02_RANKED marks live-proven endpoints ✅"** — true for 5, false for the other 36.

**Fix:** add an `Impl` column to both files, and correct `PROGRESS.md:228`.

> **The proposed source for that column was itself the bug.** "Sourced from the live `MIF_DECL` set +
> the provider registration list" derives delivery from whether the endpoint NAME exists — which is
> precisely what overstated the count. A name check is valid only for *new-endpoint* entries. A
> behaviour-change entry is delivered only when the specced behaviour is present in the handler body,
> which nothing but reading the handler can establish. Both files now carry the corrected status
> for the eight behaviour-change entries.

---

### 7. HIGH — Shipped code contradicts its catalogue spec; a duplicate implementation is being invited

- **`get_referencers` / `get_dependencies`** shipped as flat package-name lists. `MifBridgeAssetOps.cpp:292` is a bare `Registry().GetDependencies(FName(*Pkg), Deps)` emitting `{package, packageName, count, dependencies[]}` — **no hard/soft/game/build edge classification**. `01_CATALOGUE.md:74–75` still lists `get_asset_dependencies` / `get_asset_referencers` as unimplemented Tier-1 CONFIRMED entries whose stated purpose is *"with hard/soft/game/build edge classification"*. Next implementer builds a second, parallel dependency endpoint.
- **`kr_batch_reconstruct`** is registered (`MifKrBridgeEndpoints.cpp:4173`) but appears in neither file as an active entry — `02_RANKED.md`'s "Removed from ranking" section lists `kr_batch_reconstruct_request`/`_status` as *merged duplicates*, and the Batch R list of 10 omits it.
- **11 shipped endpoints absent from both files entirely**: `kr_batch_reconstruct`, `pie_load_level_instance`, `pie_unload_level_instance`, `get_inherited_component`, `override_inherited_component`, `revert_inherited_component`, `delete_datatable_rows`, `add_enhanced_input_action`, `get_referencers`, `get_dependencies`, `audit_unused`.

**Fix:** mark `get_asset_dependencies`/`get_asset_referencers` as SUPERSEDED-BY with the shipped names and the delta (edge classification still missing); un-merge `kr_batch_reconstruct`; add the 11 as post-catalogue entries.

---

### 8. MEDIUM — `06_IMPLEMENTED.md:124` "Batch B+C build + live proofs — ALL PASS" / `:148` "Batch C verdict: COMPLETE": `save_dirty_packages` was never proven to save

Recorded evidence (`:144–146`) is only the **skip** branch — an untitled `/Temp/Untitled_0` world package echoing "no on-disk destination". Batch C's own required proof (`:268–272`) demanded `saved[]` length 2 followed by `list_dirty_packages` showing 0; and `list_dirty_packages`' `kind:"content"` branch (`:238–241`) was never run either. The endpoint whose entire purpose is writing packages to disk has zero evidence that it writes anything.

**Fix:** downgrade `:148` to "COMPLETE — save path UNPROVEN (only the skip branch exercised)" and keep `:268–272` as an open gate.

---

### 9. MEDIUM — `00_ARCHITECTURE.md` is false in four places

- **`:60`** — "`server.py` (separate repo: `Eddie_v2/tools/ue5-mcp-bridge/`)". **False.** `C:/Users/andre/Documents/GitHub/Eddie_v2/tools/ue5-mcp-bridge/` does not exist. The only `server.py` is `D:/DDS2SDK/Game/Plugins/MifBridge/tools/ue5-mcp-bridge/server.py`, inside this plugin, tracked by this plugin's git.
- **`:103–124`** — the whole "Known sync hazard" section. `:107` claims "exposes **82** of the plugin's **102** endpoints — 20 endpoints … have no MCP tool". Actual: **203 of 203, zero gap** (name-set diff empty both directions). The 20-name list at `:111–116` is obsolete — 14 of them have tools today (`describe_class`, `get_property`, `set_property`, `list_object_properties`, `delete_asset`, `rename_asset`, `duplicate_asset`, `list_enum_values`, `describe_animation`, `list_animations`, `remove_pin`, `set_variable_flags`, `create_editable_child`, `add_widget_binding`).
- **`:45`** — "`MifBridgeNodes5.cpp` | Generic reflection property **get/set**". Nodes5 owns `H_set_property` only (`MifBridgeNodes5.cpp:748`); `H_get_property` (`MifBridgeNodes6.cpp:119`) and `H_list_object_properties` (`:149`) are in Nodes6. Batch F flagged this at `:1624–1626` and `:1972–1977` and left it — and it *already caused* a mis-scoped batch (Batch F's brief was written off this stale row).
- **`:37–52`** — the source-layout table lists ~14 of the 38 `Private/*.cpp` files. Every file this audit created is missing: `MifBridgeMaterials.cpp`, `MifBridgeUndo.cpp`, `MifBridgeStreaming.cpp`, `MifBridgeInherited.cpp` — plus `MifBridgeSpatial`, `MifBridgeUserTypes`, `MifBridgeNavigation`, `MifBridgeFunctions`, `MifBridgeLandscape`, `MifBridgeLevel`, `MifBridgeWorld`, `MifBridgePIE`, `MifBridgeViewport`, `MifBridgeNodes7`. And `:54–61` "files that MUST stay in sync" never mentions the external-provider route (`Public/MifBridgeEndpointRegistry.h` + one `RegisterExternalEndpoint` call in the provider), which is how **12 of 203** endpoints now register.

**Fix:** delete `:103–124` outright and replace with "server.py lives at `tools/ue5-mcp-bridge/server.py` in this plugin; sync is checkable with the diff below"; correct `:45` and `:60`; regenerate the layout table; add the provider path as step 7.

---

### 10. MEDIUM — `02_GOTCHAS.md:116–131` (§3) routes agents to a debug-gated console path that has been superseded

§3 tells the reader: *"To read the logic — decompile with the reconstructor via `run_console`: `mif.kr.Reconstruct <BP>` (also `mif.kr.DumpBP`, `mif.kr.DumpFull`, `mif.kr.Events`, `mif.kr.AnalyzeUbergraph`, `mif.kr.VerifyFidelity`)"*.

All of those have first-class HTTP endpoints now (`kr_disassemble_function`, `kr_dump_blueprint`, `kr_list_events`, `kr_analyze_ubergraph`, `kr_verify_fidelity`, plus `kr_list_cooked_blueprints` for discovery). `06_IMPLEMENTED.md:1406` explicitly claims *"This closes 02_GOTCHAS.md §3, which still routes agents to run_console for exactly this"* — the close was announced and never applied.

Worse: `06_IMPLEMENTED.md:857–860` records that those console commands are file-static **and** inside `#if MIF_KR_DEBUG` (`MifReconstructorDebug.h:9–11`, *"Ship OFF (set to 0) before any release"*). §3 is pointing at the one route that vanishes in a release build, while the durable route goes undocumented.

**Fix:** rewrite §3's "To read the logic" bullet to name the `kr_*` endpoints, and demote the console commands to a parenthetical marked debug-only.

---

### 11. MEDIUM — `02_GOTCHAS.md:464–470` (§7) "Compile-heavy ops run alone" lists 8 of ~25 self-managed endpoints

`IsSelfManagedEndpoint` (`MifBridgeCommon.cpp:401–465`) now also holds: `set_function_flags`, `delete_asset`, `rename_asset`, `duplicate_asset`, `create_landscape`, `new_level`, `load_level`, `save_level_as`, `undo_transactions`, `redo_transactions`, `save_dirty_packages`, `create_material`, `create_material_function`, `recompile_material`, `add_sublevel`, `remove_sublevel`, `set_sublevel_streaming`, `pie_load_level_instance`, `pie_unload_level_instance` — plus every external declaring `EEndpointBucket::SelfManaged` (`kr_reconstruct_request` and the four verify-family endpoints, via the fallback at `:459–465`).

A reader who trusts §7 will nest e.g. `remove_sublevel` inside `batch` — the one endpoint that *resets the transaction buffer* and then runs a `Fatal` stale-reference sweep (`06_IMPLEMENTED.md:2716–2722`).

**Fix:** replace the hand-written list with a pointer to `IsSelfManagedEndpoint` and to `self_audit.transactionBuckets.selfManaged`, which is the live source of truth.

---

### 12. MEDIUM — `06_IMPLEMENTED.md:2127–2130` (W3-6) is a proof that cannot fire, and contradicts the code's own honesty note

W3-6 backgrounds `kr_verify_fidelity` and expects the following `kr_drift_census` to be **refused** naming the running jobId. But `kr_verify_fidelity` is an *atomic* kind, and `kr_reconstruct_status`'s own emitted note says why that cannot happen (`MifKrBridgeEndpoints.cpp:2336`): *"single-Blueprint jobs (reconstruct, verify, classify) are ATOMIC: the HTTP listener is a game-thread ticker, so while the job runs no request is read off the socket at all."* This is the identical reasoning already recorded at `06_IMPLEMENTED.md:1424–1426` for Wave 1 — and then repeated as a pass condition anyway.

**Fix:** rewrite W3-6 the only way it can work — start a **sliced** kind first (`kr_drift_census {"maxCount":5}`, which pumps HTTP between Blueprints, `MifKrBridgeEndpoints.cpp:2437`), then fire `kr_verify_fidelity` mid-sweep and assert the refusal. Or delete W3-6 and state that the guard is unreachable for atomic kinds by construction.

---

### 13. LOW/MEDIUM — the ground rule at `06_IMPLEMENTED.md:8` is contradicted by six later sections

`:8` — *"Registry sync asserted after every batch: `MIF_DECL count == MIF_BIND count`, **server.py updated**."*

Batches F (`:1952`), Wave 3 step 2 (`:2221`), H (`:2583`), I (`:2914`), G (`:3222`) and J (`:3544`) each end with a *"What server.py needs (NOT touched — a later agent owns it)"* section. The rule was abandoned mid-file without amendment, and the abandonment is why the file cannot be used to answer "is server.py current?".

**Fix:** amend `:8` to describe the actual policy (registry sync per batch; server.py sync per session, verified by name-set diff) and add the diff command.

---

### 14. LOW — six "owed" sections are already done and will cause duplicated work

Verified against source:

- **server.py is complete.** `add_variable_get`/`add_variable_set` have `target_class` (`server.py:204/211`); `audit_unused` has `exclude_referencers` (`:1368–1370`); `set_property` takes `value: Any` (`:834–835`); the three inherited-component tools exist (`:571/:577`); all 8 streaming tools and all 12 `kr_*` tools exist. Name-set diff against the registry is empty both ways.
- **Batch J's registry is wired.** `:3724–3725` says "Registry owed: 3 MIF_DECL + 3 MIF_BIND + 1 IsReadOnlyEndpoint entry, taking MIF_DECL/MIF_BIND from 188 to 191." All three `MIF_DECL`s are present and `get_inherited_component` is in the read-only set at `MifBridgeCommon.cpp:344`.

**Fix:** convert each "owed" section header to "**LANDED** — see server.py:\<line\>", or the next agent re-implements 20+ wrappers.

---

### 15. LOW — Batch A's proof transcript is not literal

`:27` records `spawn_actor_in_level {mesh:/Engine/BasicShapes/Cube, location:{10000,10000,100}}` → spawned. `H_spawn_actor_in_level` (`MifBridgeLevel.cpp:212–219`) requires `actorClass` via `ResolveClassStrict` and `Fail`s without it — the call as written returns an error, not the recorded bounds. (`mesh`/`staticMesh` is a genuine optional key at `MifBridgeLevel.cpp:257` — but the handler's own `in:` comment at `MifBridgeLevel.cpp:202` still omits it, the exact PM-005 class Batch D.1 and Batch H swept for elsewhere.)

**Fix:** re-record the call with `actorClass`, and add `mesh?/staticMesh?` to the `in:` comment.

---

### 16. LOW — internal ordering contradiction between Batch H and Batch I

`:2300–2301` (Batch H) — "live `self_audit.endpointCount` was **188** before the change and must still be 188 after." `:2634` (Batch I, printed *after* H) — "MIF_DECL 180 → **188**." Both are true in wall-clock order (I ran first), but the file's order says the count was 188 before the batch that created it.

**Fix:** add a wall-clock timestamp to the Batch I heading (H already has `2026-07-28`), or reorder.

---

### Clean in this dimension

- **No endpoint claimed as delivered is missing from the registry.** I checked every name asserted live across Batches A, C, D, R1, R2, I, J and Wave 3 against `MIF_DECL`/`MIF_BIND`/`Reg(TEXT(...))`/server.py: all present, all four sets consistent.
- **`MIF_DECL` and `MIF_BIND` name-sets are byte-identical** (diff empty) — the one invariant the log claims to hold every batch actually does.
- **No "PASS" line quotes a response body that is itself an error.** The failures here are omissions and unearned scope, not doctored transcripts — and the log's two most important negative findings (D-1 addressing failure `:533`, D-2 silent no-op `:537`) are recorded honestly and in detail.
- **`01_POSTMORTEMS.md`'s PM-003 scratch-buffer discipline is genuinely upheld** in the new code (`MifBridgeInherited.cpp`, `MifBridgeNodes5.cpp` `FScratchValue`) and correctly cited.
- **Batch I's raw-count explanation (`:2634–2643`) is the only arithmetic in the file that is exactly right**, including the `#undef` and the two prose `MIF_BIND` mentions — verified: raw DECL 193, raw BIND 195, unique 191/191.

---

**Verdict: FAIL — the implementation log's headline verdicts overclaim (Batch D "10 live-proven" is 6; Wave 1 "ALL PROVEN" is 6 of 8; Batch C "COMPLETE" never proved `save_dirty_packages` saves), five shipped endpoints are absent from it entirely, all four of its gating endpoint-count asserts are now wrong, and the two contract docs (`00_ARCHITECTURE.md`, `02_GOTCHAS.md`) plus both catalogue files still describe a bridge that stopped existing ~40 endpoints ago.**

---

# Dimension: regressions

I have enough. The bridge came back up mid-audit (`buildDate Jul 28 2026 22:21:25`, `endpointCount 203`, `policyContradictions []`), so several findings below are live-confirmed rather than source-only.

## Dimension: regressions / behaviour changes to endpoints that already existed and already worked

---

### 1. MEDIUM-HIGH — `batch` cannot dispatch any externally-registered endpoint; it reports "unknown op" for endpoints the bridge *does* have
**`D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeNodes.cpp:1778` and `:1805`**

```cpp
const TMap<FString, FHandlerFn>& Registry = Handlers();   // :1778  built-ins ONLY
...
else if (const FHandlerFn* Fn = Registry.Find(OpName)) { (*Fn)(OpIn, OpOut); }   // :1805
else { Fail(OpOut, FString::Printf(TEXT("unknown op: '%s'"), *OpName)); }
```

`RunEndpoint` (`MifBridgeCommon.cpp:572-575`), `IsReadOnlyEndpoint` (`:389`) and `IsSelfManagedEndpoint` (`:468`) were all taught about `ExternalRegistry()` this session. `H_batch` was not. So the compile-heavy **ban** in batch honours external descriptors while the **dispatcher** below it does not — a half-integration.

Live-proven against the running DLL:
```
POST /api/batch {"ops":[{"op":"kr_list_events","asset":"X"}], "compileAtEnd":false}
  -> {"ok":false,"op":"kr_list_events","error":"unknown op: 'kr_list_events'"}
POST /api/self_audit -> endpointCount 203, includes kr_list_events
```
All 12 `kr_*` endpoints (11 read-only + `kr_reconstruct_request`) are unreachable from `batch`, and the error text asserts something false — exactly the "confidently wrong answer" class the strict-params work exists to kill.

**Fix:** in `H_batch`, on the `Registry.Find` miss, fall back to `ExternalRegistry().Find(OpName)->Handler` before failing. The bucket ban immediately above already consults it, so no new policy is needed. (`ExternalRegistry()` is file-static in `MifBridgeCommon.cpp:275`; expose a `const FExternalEndpointDesc* FindExternalEndpoint(const FString&)` in `MifBridgeHandlers.h`, or move the lookup into a shared `DispatchOne()`.)

---

### 2. MEDIUM — `set_material_parameter` turned a partial-success into an all-or-nothing failure, without saying so anywhere a caller reads
**`MifBridgeAuthoring.cpp:601-620`** (baseline: `git show a1b172e:.../MifBridgeAuthoring.cpp`)

Baseline skipped a malformed map entry and applied the rest:
```cpp
if (!Pair.Value.IsValid() || !Pair.Value->TryGetNumber(V)) { continue; }   // baseline
```
Current code `Fail()`s the whole call before the first write:
```cpp
if (!Pair.Value.IsValid() || !Pair.Value->TryGetNumber(V))
{ Fail(Out, ...("scalars['%s'] must be a number (got %s)...")); return; }   // :604-611
```

An existing caller sending `scalars:{"Tiling":4,"Comment":"x"}` used to get `ok:true, applied:1`; it now gets `ok:false` and **zero** writes. `server.py:1122`'s docstring documents only the *all-names-unknown* error ("if NONE of the names exist... the call now ERRORS"), not the type-validation abort, and `docs/02_GOTCHAS.md` does not mention it at all.

**Fix:** either (a) document it — one clause in the `server.py` docstring plus a line in GOTCHAS §4c — or (b) keep the pre-validate pass but emit `rejectedParameters:[{name, reason}]` alongside the existing `unknownParameters[]` and still apply the well-formed entries, matching the endpoint's own "unknown names are reported, not fatal" convention.

---

### 3. MEDIUM — `set_material_parameter` is in the transacted bucket but records nothing, so the next Ctrl-Z undoes the *previous* action
**`MifBridgeAuthoring.cpp:513` (the TODO), writes at `:637-652`, `MIC->PostEditChange()` at `:668`**

The handler calls `SetScalarParameterValueEditorOnly` / `SetVectorParameterValueEditorOnly` / `PostEditChange()` / `MarkPackageDirty()` but never `MIC->Modify()`. Bucket confirmed live: `transacted`. `RunEndpoint` therefore opens `FScopedTransaction`, the transaction accumulates zero records, and `UTransBuffer::End` sees `FTransaction::IsTransient() == true` (`D:/UE532/.../EditorTransaction.cpp:515-527` — `return !bHasChanges`), pops the entry and restores `UndoCount` from `PreviousUndoCount`. Net effect: the material edit is **not undoable**, and a user pressing Ctrl-Z after it silently reverts whatever they did *before* it.

This session rewrote the entire body of this handler (gather → validate → apply) and left the TODO in place.

**Fix:** `MIC->Modify();` immediately before the first `Set*ParameterValueEditorOnly` in each apply loop (`:637`, `:642`). One line; the handler is already inside the blanket transaction.

---

### 4. MEDIUM — `set_property` is refused inside `batch`, while `docs/02_GOTCHAS.md` §5d tells callers to batch it
**`MifBridgeCommon.cpp:556-566` (`IsCompileHeavyEndpoint` derives from `IsSelfManagedEndpoint`) + `MifBridgeNodes.cpp:1800`**

Live-proven:
```
POST /api/batch {"ops":[{"op":"set_property","objectPath":"/Engine/EngineMaterials/DefaultMaterial",
                        "propertyPath":"TwoSided","value":"True"}]}
  -> "op 'set_property' is not allowed inside batch (it runs a full compile ...)"
```
The stated reason is only true for the **widget-BP branch** (`MifBridgeNodes5.cpp:964-971`, `CompileBlueprint`). The `objectPath` branch — CDO edits, component templates (`<BP>_C:Mesh_GEN_VARIABLE`), node properties, placed actors — compiles nothing. `docs/02_GOTCHAS.md:335` literally says *"the widget branch of `set_property` runs a full compile on every write… **Batch what you can.**"* — advice the code refuses to honour. `docs/02_GOTCHAS.md:456` also lists `set_property (widget-BP branch)` as the compile-heavy case, i.e. the docs already know the ban is over-broad.

Not introduced this session (`a1b172e` already derived `IsCompileHeavyEndpoint` from the bucket), but the session added the strict-params guard to `set_property` and expanded GOTCHAS §5d without reconciling the two.

**Fix (smallest correct one):** split the endpoint's bucket decision from its batchability — have `H_set_property` refuse *itself* when `widgetName` is present and a batch is open, and drop `set_property` from the compile-heavy set. Cheapest alternative: correct GOTCHAS §5d to say `set_property` cannot be batched at all, and change the batch refusal text so it names the widget branch rather than claiming an unconditional compile.

---

### 5. MEDIUM — this session recorded a false premise about `create_material_instance`'s bucket, and used it to justify its two new siblings' buckets
**`MifBridgeAuthoring.cpp:401-403` and `MifBridgeCommon.cpp:435-437`**

`H_create_material_instance` (`MifBridgeAuthoring.cpp:410`) does `CreatePackage` → `FactoryCreateNew` → `FAssetRegistryModule::AssetCreated(MIC)` (`:481`) → `Package->MarkPackageDirty()` (`:482`) — asset creation — and is in the **transacted** bucket (confirmed live: `create_material_instance -> ['transacted']`). Two new comments say otherwise:

- `MifBridgeAuthoring.cpp:402`: *"…because it creates the asset before applying parameters (**self-managed bucket, no blanket transaction**)"* — false; `RunEndpoint` wraps it.
- `MifBridgeCommon.cpp:436`: `create_material` / `create_material_function` were placed in `IsSelfManagedEndpoint` citing *"the **create_material_instance precedent (untransacted)**"* — the cited precedent does not exist.

The plugin's own rule (`MifBridgeCommon.cpp:429-434`, and `delete_asset`/`rename_asset`/`duplicate_asset` all being SelfManaged) is that asset-lifecycle ops must not run inside the blanket transaction. `create_material_instance` violates it.

The bucket itself did **not** change this session — the regression is that a wrong fact about an existing endpoint is now written into the contract file and was reasoned from.

**Fix:** add `TEXT("create_material_instance")` to the `SelfManaged` set next to `create_material` (`MifBridgeCommon.cpp:438`), and correct both comments. Note this also makes it compile-heavy → banned in batch; that is consistent with its siblings.

---

### 6. LOW-MEDIUM — `batch`'s `compileAtEnd` silently skips ops that address the blueprint through the `path` alias the new guards advertise
**`MifBridgeNodes.cpp:1821` / `:1829`**

```cpp
if (OpIn->HasField(TEXT("graphId")))       { ... Touched.Add(...) }
else if (OpIn->HasField(TEXT("blueprintId"))) { ... Touched.Add(...) }
// `path` is never consulted
```

`H_add_override_event` (`MifBridgeNodes.cpp:898-901`) and `H_add_pin` (`:1171-1180`) both now *advertise* `path` as an accepted alias for `blueprintId` in their guard summaries, and both are transacted (batchable). So:

```json
{"ops":[{"op":"add_pin","path":"/Game/BP/BP_X","function":"F","name":"N","type":"int"}]}
```
mutates BP_X, `Touched` stays empty, `compiles: []` is returned, and the blueprint is left structurally modified but uncompiled — while the response reads `ok:true`. Before the guards, no caller had any reason to know `path` worked here.

**Fix:** in both `Touched` branches use the same spelling set the handlers do — `JStrAny(OpIn, { TEXT("blueprintId"), TEXT("path") })` — and test presence with `JHasAny`.

---

### 7. LOW — `add_variable_get` / `add_variable_set` gained a hard refusal on the **self-member** path that no previous caller could hit
**`MifBridgeNodes.cpp:440-456`** (baseline had no such check: `git show a1b172e:.../MifBridgeNodes.cpp`, `H_add_variable_set` went straight to `SetSelfMember`)

```cpp
if (FProperty* SelfProperty = FindAnyProperty(SelfClass, Var))
{ if (!CheckMemberAccessible(Blueprint, SelfClass, SelfProperty, Access, AccessError)) { Fail(...); return; } }
```

`CheckMemberAccessible` delegates to `FBlueprintEditorUtils::IsProperty{Readable,Writable}InBlueprint`, which is engine-accurate — so the refusals are correct — but a call that previously returned a placed node plus a `warning` now returns `ok:false`. Concretely, `add_variable_set {var:"Mesh"}` in a Character BP (ACharacter::Mesh is `BlueprintReadOnly`) flips from success-with-a-node to hard failure, as does any Get/Set of an inherited property lacking `CPF_BlueprintVisible` or carrying `meta=(BlueprintPrivate)` on a parent Blueprint.

I checked the two plausible false-positive classes and both are safe: BP-authored variables and event dispatchers both get `CPF_BlueprintVisible` (`D:/UE532/.../BlueprintEditorUtils.cpp:4571-4576`), and a variable that does not yet resolve on the skeleton keeps the old warning path (`FindAnyProperty` returns null → gate skipped).

**Fix:** none required to the logic — but `server.py:205/212` documents the writability check only for `add_variable_set`. Add the same sentence to `add_variable_get`'s docstring and a row in `docs/02_GOTCHAS.md` §1, so the new failure mode is discoverable.

---

### 8. LOW — `get_referencers`, `get_dependencies`, `audit_unused` are pure reads in the transacted bucket
**`MifBridgeCommon.cpp:338-380` (`IsReadOnlyEndpoint`), handlers at `MifBridgeAssetOps.cpp:261`, `:291`, `:440`**

Confirmed live: all three report `['transacted']` while `find_assets` / `describe_package` are `readOnly`. None calls `Modify()`; `audit_unused` additionally holds the transaction open across `IAssetRegistry::ScanPathsSynchronous` + `WaitForCompletion` (`MifBridgeAssetOps.cpp:471-475`).

`docs/audit/06_IMPLEMENTED.md:2605-2613` already flags this and left it. Its reasoning ("the engine discards the empty transaction") is correct, and I verified *why*: `UTransBuffer::BeginInternal` (`D:/UE532/.../TransBuffer.h:78-88`) truncates the redo stack at Begin, but `End()` restores it when `FTransaction::IsTransient()` (`EditorTransaction.cpp:1309-1329`) — which is true for a record-free transaction. So the undo/redo stacks genuinely survive today. The exposure is that this only holds *while* the three handlers record nothing; the moment any of them gains a `Modify()` the redo stack starts dying silently, which is precisely the A/B loop `redo_transactions`' own docstring (`server.py:1423`) tells callers to rely on.

**Fix:** the one-liner the log already names — add the three names to the `ReadOnly` set in `MifBridgeCommon.cpp` alongside the cooked-introspection block (`:379`).

---

### 9. LOW — `add_function_call` accepts `asMessage` in its guard but cannot honour `asMessage:false`
**`MifBridgeNodes.cpp:721-726`**

```cpp
const bool bWantMessage = JBoolAny(In, { TEXT("asMessage"), TEXT("message") }, false)
    || (TargetClass->HasAnyClassFlags(CLASS_Interface) && !ClassName.Equals(TEXT("self"), ESearchCase::IgnoreCase));
```
An explicit `asMessage:false` on an interface call against an external class is silently overridden — there is no way to author a non-Message interface call. Pre-existing (identical at `a1b172e`), but the session's new guard (`:623-637`) now *advertises* `asMessage` as an honoured parameter, which is the "accepted but ignored" shape `01_POSTMORTEMS.md` (`spawn_actor_in_level`'s dropped `mesh`) says must never ship.

**Fix:** distinguish "absent" from "explicitly false" with `JHasAny(In, {TEXT("asMessage"), TEXT("message")})` and let an explicit `false` suppress the auto-Message path, or add a KeyNote stating the override is unconditional.

---

### 10. LOW — a `set_property` that fails its own verification still leaves the package dirty
**`MifBridgeNodes5.cpp:895` (`LeafOwner->MarkPackageDirty()`) vs the failure return at `:938-951`**

The code comments the trade-off honestly (`:911-916`), but the consequence is user-visible through two endpoints added this same session: `list_dirty_packages` and `save_dirty_packages` will now report/save a package whose only "change" was a write that provably did not land.

**Fix:** capture `Package->IsDirty()` before the write and restore it on the `bRequestedChange && !bChanged` path, or state the behaviour in `server.py`'s `set_property` docstring (which currently promises only "a value that fails to **parse** leaves the property UNCHANGED").

---

### 11. LOW — `RejectUnknownParams` tolerates `op` on every endpoint, including standalone calls
**`MifBridgeCommon.cpp:705-713`**

The central `op` skip is the right fix for the batch regression, but it is unconditional: `find_assets {"op":"typo"}` over raw HTTP is silently accepted. Cost is one ignored key on a direct call; noted only because "an ignored parameter is worse than a rejected one" is this session's own stated rule.

**Fix (optional):** thread a `bInBatch` flag, or accept it as a documented exception in `docs/02_GOTCHAS.md` §1.

---

## Checked mechanically and clean

- **(a) guard accepted-keys vs `server.py`** — all 63 guarded endpoints cross-checked against every `_post(...)` kwarg (including the `**{"class": ...}` / `**{"property": ...}` dict-splat forms). **0 mismatches.** Guarded-endpoint set and `_post` endpoint set are identical.
- **(a) guard accepted-keys vs what the handler actually reads** — every `JStr/JBool/JInt/JNum(Any)`, `JHasAny`, `In->TryGet*Field` literal in each guarded handler, plus the fields consumed indirectly by `ResolveBlueprintField` (`blueprintId`,`path`), `ResolveGraphField` (`graphId`), `ResolveNodeField` (`nodeGuid`/`node`/`guid`/`nodeId`) and `ResolveClassStrictField`. **No guard rejects a key its own handler would honour.**
- **(a) docs vs guards** — every `endpoint {json}` example in `README.md`, `docs/00_ARCHITECTURE.md`, `docs/02_GOTCHAS.md`, `docs/05_DESIGN_SPEC.md`, `docs/06`, `docs/08` and `docs/audit/06_IMPLEMENTED.md` replayed against the guards. Only hits are the five deliberate negative-test examples in the session log (`find_assets {"recursive":false}` etc.). The alias tables in GOTCHAS §1 are supersets-correct against every guard.
- **(b) renamed-not-aliased parameters** — diffed the read-key set of every handler present in both `a1b172e` and the working tree. **Zero keys lost.** The only two hits (`H_add_variable_get`/`_set`) are the bodies moving into `DoAddVariableNode`, whose accepted set is a strict superset of the baseline reads. Every claimed alias is genuinely additive.
- **(c) response fields** — diffed the `Out->Set*Field` key+type map of every surviving handler. **Zero fields removed, zero retyped.** The row-shape changes (`find_assets.assets[]`, `describe_package.registryAssets[]`/`exports[]`, `list_mounted_containers.containers[]`, `audit_unused.assets[]`) are all additive with the legacy keys re-emitted byte-identical (`MifBridgeCooked.cpp:320-324`, `:394-406`, `:188-190`; `MifBridgeAssetOps.cpp:127-131`).
- **(c) `read_datatable` FText default** — the new `textFormat:"export"` default maps to `EDataTableExportFlags::None`, which is exactly what baseline `GetTableAsJSON()` used. No value change for existing callers.
- **(c) wire encoding** — I suspected the ~163 non-ASCII message literals (88 added this session, no BOM, no `/utf-8` in `Build.cs`) were mojibaking. They are not: raw response bytes are correct UTF-8 (`E2 80 94`). The `â€"` I saw was my own reader opening the JSON with the Windows ANSI default. **Non-finding, cleared.**
- **(d) bucket changes to existing endpoints** — only `describe_class` and `list_enum_values` moved (transacted → readOnly). Both verified to contain no `Modify()` / `MarkPackageDirty` / `MarkBlueprintAs*`. No existing endpoint lost a transaction it needed. `policyContradictions` is `[]` live.
- **(e) composite sub-calls** — `H_batch` injects only `op`, now tolerated centrally; `H_recipe_override_and_call_parent` (`MifBridgeRecipes.cpp:305-310`) injects only `callParent`, which `H_add_override_event`'s guard accepts, and the recipe's own `server.py` parameter set (`blueprintId, event, interfaceOrParent, x, y`) is fully inside that guard. `H_recipe_add_debug_print` / `_reset_and_loop` / `_splice_before_parent` / `_argmax_over_components` call no guarded handler. `compile` / `validate` are unguarded. `MifBridgeServer.cpp` injects nothing into the param object.
- Registry sync: `MIF_DECL` 191 == `MIF_BIND` 191 == `_post` built-in names 191; 203 `@mcp.tool` == 203 live endpoints. No drift.

---

**Verdict: the parameter-guard sweep itself is clean — no renamed parameters, no removed or retyped response fields, no guard that rejects what `server.py`, the docs, `batch` or the `recipe_*` family actually send; the real damage is elsewhere in the composite/bucket layer, where `batch` silently reports "unknown op" for all 12 externally-registered `kr_*` endpoints, `set_material_parameter` quietly became all-or-nothing and is still not undoable, and two new comments record a false bucket for `create_material_instance` that a sibling endpoint's bucket was then derived from.**