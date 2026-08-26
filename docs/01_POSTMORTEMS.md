# MifBridge — postmortem log

One entry per bug that cost real time. Symptom → root cause → fix → prevention.
Newest first.

---

## PM-012 — the harness knew what was happening and did not say it (2026-08-26)

**Symptom.** A full two-pass sweep appeared to stop. `test_transactions.py` sat for 568 seconds with
**0s CPU over a 4s sample, no TCP connections and no child processes**, while the editor stayed idle
and answered every other caller instantly. The log showed nothing at all — not the suite's name, not a
warning, not a partial result. Working out merely *which suite* was stuck took a `Get-CimInstance`
query against process command lines.

**Root cause — three separate holes, one shape.** None of them is the stall itself, which is still
unexplained. All three are why the stall was unreadable:

1. **`run_all_suites` printed a suite's name only when it FINISHED.** A suite that hangs therefore
   produces no line whatsoever, and the log simply stops mid-run. Indistinguishable from the runner
   itself dying.
2. **`wait_for_bridge` reported "port bound but not answering" — and said nothing when the IDENTITY
   check failed.** That branch slept 5s and looped, silently, for the full 900-second timeout. The
   reason was in hand the entire time: `require_sdk_bridge` *returns* it, and the loop threw it away.
3. **The log was block-buffered.** `run_all_suites` was launched without `-u`, so a stalled run flushed
   nothing — a zero-length log for minutes at a time, which is exactly when you most want output.

Each one independently converts "the harness is telling you something" into "the harness is silent".

**Fix.** Name the suite *before* running it, flushed. Print the identity-check reason once after a 60s
grace, and again only if it changes. Run the sweep with `python -u`. Say so on the timeout path rather
than leaving `rc=-99` to be interpreted.

**And a fourth hole, found by walking into it.** Nothing stopped a second process driving the same
editor. Mid-sweep I ran `test_transactions` by hand; it issued `undo_transactions` against the
**global** undo stack, reverted work belonging to whichever suite the sweep was inside, and turned
`test_idempotence` red *in the sweep's own results*. Neither run's failures named the cause — mine
reported a variable that would not stay deleted, the sweep reported an unrelated idempotence check.
`run_all_suites` now holds `.sweep-lock` and exports `MIF_SWEEP` so its own children are exempt by
construction; everything else gets warned through `wait_for_bridge`. A **stale** lock from a dead
sweep is ignored — liveness is checked, not inferred from the file existing, or one crash would wedge
the harness forever.

**Prevention:**

1. **A long-running harness must narrate before it blocks, not after it succeeds.** Every one of these
   holes is the same mistake: the information existed and was only emitted on the happy path.
2. **A retry loop that can fail for two different reasons must report both.** `wait_for_bridge`
   carefully distinguished LOADING from BLOCKED and then said nothing about the third case.
3. **Anything that owns a shared resource should say so.** The undo buffer is one stack for the whole
   editor, so "suites run sequentially" is only true if nothing else is running.
4. **Record the disproofs.** Ten PowerShell processes were alive during the stall and looked exactly
   like a leak from mifaudit's six `subprocess.run` calls. They shared one unrelated parent and the
   oldest was five days old. `stdin=subprocess.DEVNULL` was added anyway — it closes a real hole — but
   it is labelled hardening, not a fix, because the cause is still unknown and a plausible story that
   was ruled out is worth more written down than forgotten.

---

## PM-011 — `set_variable_type` hung the whole bridge on an ordinary three-call sequence (2026-08-26)

**Symptom.** `set_variable_type` never returned. Not an error, not a crash — no response at all, and
every subsequent request timed out too. The editor process was still alive and Windows reported
`Responding: True`.

The sequence that did it was not exotic: `add_variable` a float, `add_variable_get` a node for it,
`set_variable_type` to int. Three calls, all ordinary.

**Root cause.** `FBlueprintEditorUtils::ChangeMemberVariableType` counts the nodes that reference the
variable, and if there are **any** — in this Blueprint or in any loaded *child* Blueprint — it opens
an `FSuppressableWarningDialog` first (`BlueprintEditorUtils.cpp:5035`; the local-variable sibling
does the same at `:5605`):

```cpp
if (AllVariableNodes.Num())
{
    if (!VerifyUserWantsVariableTypeChanged(VariableName))  // -> ShowModal()
    {
        return;
    }
```

Handlers run inline on the game thread inside the HTTP ticker, so the modal did not "ask a question"
— it stopped the ticker. The socket was never read again.

Note which case is the guarded one. Retyping a variable that has **no** nodes is the case nobody
needs; retyping one that **has** nodes is the entire purpose of the endpoint. The modal was on the
main path, not on an edge.

**Why the existing defences did not catch it.**

1. `tools/audit_modals.py` models the guard as `TGuardValue<bool>(GIsRunningUnattendedScript, true)`,
   which is what neutralises `FMessageDialog::Open`. `FSuppressableWarningDialog` never goes through
   `FMessageDialog` — it calls `GEditor->EditorAddModalWindow` directly. Two dialog classes; only one
   was modelled, and the inventory listed no `FSuppressableWarningDialog` sites except the one in
   `rename_variable` that had already been closed by hand.
2. The first probe of this endpoint passed. It retyped a variable that had no nodes yet, so it never
   armed the dialog. A test that does not create the precondition passes against the broken build.

**Fix.** `FMifScopedDialogSuppression` (declared in `MifBridgeHandlers.h`, defined in
`MifBridgeCommon.cpp`). `FSuppressableWarningDialog::ShowModal()` reads
`[SuppressableDialogs]<Key>` from `GEditorPerProjectIni` **before** it shows anything and returns
`Suppressed` when the flag is set (`Dialogs.cpp`); both engine verify-functions treat `Suppressed` as
consent, so the operation proceeds. That is the right answer for a bridge: the caller already gave
their consent by calling the endpoint.

The guard sets the flag for the duration of one engine call and **restores** the caller's own
setting, removing the key entirely when they had none. Andre drives this same editor by hand, and
leaving his "warn me before I retype a variable" preference switched off would be a side effect
nobody asked for.

Refusing — the tactic `rename_variable` uses for its RepNotify modal — was not available here.
Refusing every variable that has referencing nodes would refuse the endpoint's whole reason to exist.

**Prevention:**

1. `tools/test_modal_hazards.py` (T360–T364). Every assertion is ultimately *did the call come back*,
   because a hang leaves no response to inspect. T360 asserts the **precondition** explicitly — that
   the variable really has a referencing node — so the suite cannot quietly stop testing the hazard
   the way the original probe did.
2. **The two dialog classes need two different guards, and the difference is now written down** in
   `02_GOTCHAS.md` §8.
3. **Diagnosing this class, in the order that actually settles it.** A hung bridge and a crashed
   bridge look identical from the client:

   ```bash
   powershell -NoProfile -Command "(Get-Process -Id <pid>).CPU"   # sample twice, ~5s apart
   ```

   A CPU delta near zero means *blocked*, not spinning — which rules out an infinite loop before any
   guessing starts. Then enumerate the process's visible windows rather than trusting
   `MainWindowTitle`, which keeps reporting the main window while a modal sits in front of it. The
   window titled "Change Variable Type" named the bug outright.

---

## PM-008 — the build loop: an aborted build costs more than the build, and "is it live?" never needed one

**Symptom.** Three separate costs in one session, all in the build loop rather than in any code.

1. A **project-only** build that should have been ~30 compile actions started **2,673**. Nothing in
   the project had changed to justify it.
2. Time was spent planning a rebuild purely to find out whether an engine-side change was already in
   the running editor.
3. A build failed on a held DLL / Live Coding mutex *after* the kill step had been run and reported
   success.

**Root cause.**

1. **Aborting a UE build leaves partial engine intermediates.** The previous action was an
   **engine-target** build that was cancelled part-way. UBT/UHT had already written some `.obj`s,
   `.dep.json`s and generated headers and not others, and the makefile state no longer matched the
   tree. The next invocation — even scoped to the *project* target — re-derived what was stale and
   found a large fraction of the engine modules in that state, because the project target links
   against them. **The 2,673 actions were the abort being paid for, not new work.** An aborted build
   is not free and is not a no-op; it is a debt taken out against the next build.
2. **Nobody checked the binary.** Whether a compiled change is present in a shipped DLL is a question
   about a *file on disk*, and answering it takes seconds: **scan the built DLL for the exported
   symbol, and compare source mtime against binary mtime.** No build, no editor launch. That is how
   `RunTransientBlueprintReconstruct` was confirmed already live:
   ```bash
   grep -c -a "RunTransientBlueprintReconstruct" \
     "D:/UE532/Engine/Binaries/Win64/UnrealEditor-Kismet.dll"   # -> 1, the symbol is in the image
   ls -la --time-style=+%Y-%m-%d_%H:%M:%S \
     "D:/UE532/Engine/Binaries/Win64/UnrealEditor-Kismet.dll" \
     "D:/UE532/Engine/Source/Editor/Kismet/Public/CompiledBlueprintReconstructor.h"
   # DLL  2026-07-28 15:25:26   >   header 2026-07-26 15:55:09   -> binary is NEWER than source
   ```
   Symbol present **and** binary newer than source ⇒ the change is live. Either half alone is
   inconclusive: an old DLL can carry the symbol from an earlier revision, and a fresh timestamp
   proves only that *something* was rebuilt.
3. **The kill step named only `UnrealEditor.exe`.** Two other processes hold the same locks:
   - **`UnrealEditor-Cmd.exe`** — a mod **COOK** runs as `UnrealEditor-Cmd`, not `UnrealEditor`, and
     it holds the plugin DLLs open exactly like an interactive editor. A cook running in another
     window looks like "the editor is closed" to a kill step that greps for the wrong name, and the
     build dies on `LNK1104: cannot open file`.
   - **`LiveCodingConsole.exe`** — it holds a **mutex that UBT checks independently** of any DLL
     lock. It survives the editor exiting, so it is still there after the editor is closed, and it
     produces *"Unable to build while Live Coding is active"* with no editor running.

**Fix.**

* **Never abort a UE build.** If the wrong target was started, let it finish and then start the right
  one — finishing is almost always cheaper than the recompile the abort causes. If an abort is
  genuinely unavoidable, expect the next build to be long and do not read its action count as a
  signal about the code.
* **Answer "is it live?" from the binary, not from a build** — the two commands above.
* **The kill step targets three names**, and is verified rather than assumed:
  ```bash
  powershell -NoProfile -Command \
    "Get-Process UnrealEditor,UnrealEditor-Cmd,LiveCodingConsole -ErrorAction SilentlyContinue |
       Select-Object Id,ProcessName,MainWindowTitle"
  ```
  Nothing returned is the only acceptable state before invoking UBT. Note the cook case is a *real
  job someone is running* — check what it is before killing it.

**Prevention.**

1. **A build is the most expensive way to learn anything.** Before starting one, ask what question it
   answers and whether the filesystem already answers it. Symbol presence, timestamps and file sizes
   are free.
2. **An interrupted build leaves state, and the state is invisible.** There is no marker saying "the
   last build was aborted" — the only tell is the next build's action count. If a build is
   inexplicably large, suspect an earlier abort before suspecting the code.
3. **"The editor is closed" is not the invariant UBT tests.** It tests *no process holds these files
   and no Live Coding mutex exists*. Name every process that can violate it, not the one usually
   responsible. Same family as PM-002/PM-003/PM-006/PM-007: a plausible proxy standing in for the
   thing actually being checked.

**Cost.** One 2,673-action rebuild, one avoidable rebuild plan, and one failed build cycle.

---

## PM-010 — I mirrored the engine's CREATE path and not its DELETE path, and the gap was a hard crash

**Symptom.** Reported from real use on 2026-08-25 (QOLCrafting_P / `WBP_QOL_DropZone`, crash GUID
`UECC-Windows-2A82EB2E400C3FC119CD1E859837B612_0000`). `remove_widget_animation` on "ArrowLoop"
returned `{"ok": true, "removed": "ArrowLoop", "remaining": 0}`. The very next
`add_widget_animation` with the same name killed the editor:

```
Fatal error: Obj.cpp line 265
Renaming an object  WidgetAnimation ...:WidgetAnimation_0
on top of an existing object  WidgetAnimation ...:ArrowLoop
is not allowed
```

The caller saw `ConnectionResetError 10054`, then `WinError 10061` — the process was gone. Nothing had
been saved, so the removal existed only in memory.

**Root cause.** One missing line. The remove handler did:

```cpp
WBP->Animations.Remove(Anim);
```

That detaches the animation from the array. The `UWidgetAnimation` **UObject stays alive** under the
same outer, still owning the object name "ArrowLoop". `add_widget_animation` then does
`Anim->Rename(*Name)` onto that outer, and CoreUObject refuses to rename over a live object with an
assert — which in a handler is a dead editor, not an error response.

Nothing in the bridge could see the debris either: `FindAnimation` searches only `WBP->Animations`, so
the verification step in the remove handler ("re-find it to prove it is gone") passed with the object
still there holding the name. **A read-back that queries a different structure than the one that will
be written proves nothing.**

**Why it got in.** `add_widget_animation` was written by mirroring the engine's own create path
(`AnimationTabSummoner.cpp:589`) and the comment in that handler says so. The delete path is thirty
lines further down the same file and does the thing that was missed, with the reason stated outright
(`AnimationTabSummoner.cpp:823-829`):

```cpp
const FScopedTransaction Transaction(LOCTEXT("DeleteAnimationTransaction", "Delete Animation"));
WidgetBlueprint->Modify();
// Rename the animation and move it to the transient package to avoid collisions.
SelectedAnimation->Animation->Rename( NULL, GetTransientPackage() );
WidgetAnimations.Remove(SelectedAnimation->Animation);
```

A null name requests a fresh unique one; the transient package moves the object out of the widget's
namespace. The `MovieScene` is outered to the animation, so it travels along and needs no separate
handling.

**Fix.** Three changes, because the reporter asked for three and each covers a different failure.

1. `remove_widget_animation` now performs that rename before removing from the array, and reports
   `removedFromAnimationsArray` and `objectNameReusable` separately — detaching and freeing the name
   are different things, and only the second makes recreation safe.
2. `add_widget_animation` checks `FindObject<UObject>(WBP, *Name)` and refuses before mutating
   anything. Fixing (1) does not make this redundant: an animation hand-deleted in the UMG designer,
   or removed by an older build, leaves the same debris.
3. `set_widget_animation_range` was added, because the destructive sequence was only ever attempted to
   change a playback range from 0.5s to 1.5s and nothing could edit one in place. The best fix for a
   dangerous workflow is often to remove the reason anyone runs it.

**Prevention.** Two rules, both general.

- **If you mirror an engine path, mirror its INVERSE too.** Create and delete are written together in
  the engine and make assumptions about each other. Reading only the half you need leaves the other
  half's invariant unmaintained — here, "the name is free when the object is gone".
- **Verify against the structure that the next operation will actually consult.** The remove handler
  re-queried the `Animations` array, which is not what `Rename` collides against. The check to write
  is the one that would have failed: is the NAME free?


## PM-009 — a public engine "add" function that allocates but does not initialise, and the crash two lines later

**Symptom.** The first live call to the new `foliageType` mode of `add_foliage_instances` killed the
editor outright. From the test harness it looked like a network fault —
`ConnectionResetError: [WinError 10054] An existing connection was forcibly closed` — because the
process holding the HTTP server had gone. The log had the real story:
`EXCEPTION_ACCESS_VIOLATION reading address 0x0000000000000000` in
`FFoliageInfo::AddInstancesImpl()` at `InstancedFoliage.cpp:2294`, one frame below
`H_add_foliage_instances`.

**Root cause.** Line 2294 is the FIRST statement of `AddInstancesImpl`:

```cpp
Implementation->PreAddInstances(InSettings, InNewInstances.Num());
```

`Implementation` was null. `AInstancedFoliageActor::AddFoliageInfo` — which is public, `FOLIAGE_API`,
plainly named, and returns a live `FFoliageInfo&` — sets only the `IFA` back-pointer and the update
GUID (`InstancedFoliage.cpp:3013-3021`) and returns. It never creates `Implementation`. The engine
itself never calls it on its own: the only caller is `AddFoliageType`, which follows every branch with

```cpp
if (Info && !Info->Implementation.IsValid())
{
    Info->CreateImplementation(FoliageType);
    check(Info->Implementation.IsValid());
}
```

So the struct is deliberately half-built, and the initialisation lives in the wrapper rather than in
the thing named "Add". Reading the header alone cannot show that — both functions are exported, both
have plausible names, and the one that looks lower-level is the one that does not work.

**Second bug found while fixing the first.** `AddFoliageType` RETURNS a `UFoliageType*`, and it is not
always the one passed in. For a foliage-type blueprint, or a type that is neither an asset nor already
owned by the actor, it `DuplicateObject`s the type into the IFA and registers the copy
(`InstancedFoliage.cpp:3777-3801`). Adding instances against the original pointer would have keyed a
different `FFoliageInfo` than the one just prepared — the same bug again, but silent instead of fatal.
The handler now uses the returned pointer throughout, and reports `requestedFoliageType` plus a note
when the two differ, because the level owning its own copy means later edits to the source asset will
not reach the placed instances.

**Fix.** Call `AddFoliageType(Type, &Info)`, use its return value as the type for every subsequent
`AddInstance`, and refuse with an error if `Info->Implementation` is still invalid afterwards. That
last guard matters on its own: the engine asserts this with `check()`, and a `check()` inside a
handler terminates the editor instead of returning `ok:false`.

**Prevention.** The general shape, and it is not specific to Foliage: **an exported engine function
named `Add*` or `Create*` is not necessarily a complete constructor.** Before calling one from a
handler, find who calls it inside the engine. If the only in-engine caller is a wrapper that does more
work afterwards, that extra work is not optional and the wrapper is the real API. This is the same
lesson as the `FOLIAGE_API` linkage check three paragraphs earlier in the same handler — reading the
header tells you a symbol exists, not that calling it is correct.


## PM-007 — a FAILED call permanently added an override, because a cancelled transaction undoes nothing

**Symptom.** `override_inherited_component {blueprint:"…/NPC_MifAmbient", component:"Influence",
properties:{"SphereRadius":"not-a-float"}}` correctly returned **`ok:false`** — Batch L's
validate-before-import was working. A follow-up `get_inherited_component` then reported
**`overrideExists: true`**. The ICH override template the handler had minted *before* validating the
property was still on the asset. `list_transactions` showed `queueLength` unchanged (0) before and
after, so `RunEndpoint`'s `Transaction.Cancel()` had fired and left no undo entry — and the override
was there anyway. A call that reported failure had permanently changed the user's Blueprint: the
child now shadowed the parent for that component, with no undo step to reverse it and no reason for
the caller to look.

**Root cause.** Two engine facts, both read out of `D:/UE532`, either one sufficient on its own.

1. **Nothing was ever recorded.** `SaveToTransactionBuffer` stores an object only when it carries
   `RF_Transactional` (`Runtime/CoreUObject/Private/UObject/UObjectGlobals.cpp:3131-3134`). Neither
   object involved has it. The handler is
   `NewObject<UInheritableComponentHandler>(this, FName(TEXT("InheritableComponentHandler")))`
   (`Runtime/Engine/Private/BlueprintGeneratedClass.cpp:1202`) and the override template is
   `NewObject<UActorComponent>(GetOuter(), BestArchetype->GetClass(), Name, RF_ArchetypeObject | RF_Public | RF_InheritableComponentTemplate, BestArchetype)`
   (`Runtime/Engine/Private/InheritableComponentHandler.cpp:159-160`). `ICH->Modify()` therefore
   dirties the package, broadcasts `OnObjectModified`, and stores **nothing** for undo.

2. **`Cancel` does not revert anything — for any object, transactional or not.**
   `UTransBuffer::Cancel` (`Editor/UnrealEd/Private/EditorTransaction.cpp:1387-1437`) broadcasts
   `TransactionCanceled`, calls `GUndo->EndOperation()`, nulls `GUndo`, pops the transaction off
   `UndoBuffer` and restores `RemovedTransactions`. It never calls `FTransaction::Apply()`. The only
   two callers of `Apply` in the whole transaction system are `UTransBuffer::Undo` (`:1624`) and
   `::Redo` (`:1688`). The engine's own doc comment says exactly this and no more: *"Cancels the
   current transaction, no longer capture actions to be placed in the undo buffer"*
   (`Editor/UnrealEd/Classes/Editor/Transactor.h:514-519`).

So the central guarantee Batch K wrote into `RunEndpoint` — *"FAILURE ROLLS BACK … every handler is
atomic-on-failure without restructuring any of them"* — **was never true for anything**. It had
simply never been exercised, which `docs/audit/06_IMPLEMENTED.md` said in as many words at the time
("STILL UNPROVEN … no call had been found that MUTATES and then GENUINELY FAILS"). Batch L's fix to
`override_inherited_component` created the first such call, and it disproved the guarantee on its
first run.

> A **cancelled** transaction is a transaction that was **thrown away**, not one that was **undone**.

**Fix.** Order, not machinery.

* `H_override_inherited_component` now runs **guards → preflight → create → apply**. The whole
  `properties` object is type-checked against the component's own class *before* the ICH or the
  override template is minted, using the same `PrepareOneProperty` the writer uses (one
  implementation, so the preflight cannot drift from what it predicts). The probe object is the
  existing override when there is one, otherwise the parent's `ComponentTemplate` — the archetype
  `CreateOverridenComponentTemplate` duplicates, so same class, same layout, same answers. A refused
  call returns `created:false`, `nothingModified:true`,
  `outcome:"preflight-rejected-nothing-created"` and the per-property diagnostics, with nothing
  created.
* Belt and braces for what a type check cannot predict (an engine clamp, a `PostEditChangeProperty`
  rejection): if an apply still fails, the handler calls `RemoveOverridenComponentTemplate` itself —
  **but only when `created` was true in this call.** A pre-existing override is never deleted;
  destroying work the caller already had would be a worse bug than the one being fixed.
* The same reorder was applied to `add_component`, `add_foliage_instances`, `add_timeline` and
  `create_material_instance`, whose comments made the same false appeal to the cancel. Where a
  reorder was not safe (`add_pin`, `recipe_add_debug_print`, `create_struct`, `set_variable_flags`),
  the failure text now names exactly what is left behind and how to remove it. Full table:
  `docs/audit/06_IMPLEMENTED.md` § *Batch M*.
* `RunEndpoint`'s comment and its caller-visible violation message no longer claim a rollback.
  `Cancel()` stays: not leaving a bogus entry on the undo stack is a real benefit, because otherwise
  the user's next Ctrl-Z would undo a bridge action that reported failure instead of their own last
  edit.

**Prevention.**

1. **A cancelled transaction does not undo object creation — or anything else. Any handler that
   creates then validates must be reordered to validate then create.** This is the general rule and
   it has no exceptions in this codebase. There is no central mechanism that makes a failed call
   atomic; atomicity is a property of the order the handler is written in.
2. **Everything a validation needs is available before the creation.** The destination *class* is
   known before the instance exists, so property names, types and value text can all be checked
   against it. If a check seems to need the object, ask which object of the same class you already
   have — an archetype, a CDO, or the thing you were about to copy.
3. **Where a fallible step genuinely cannot precede the creation, undo the creation yourself** — and
   scope the undo to *what this call created*. Never clean up something the caller already had.
   `add_material_expression`, `add_tree_widget`, `add_event_dispatcher`, `spawn_actor_in_level`,
   `spawn_actor_in_pie` and `create_landscape` were already doing this and are the pattern to copy.
4. **Never assert a guarantee in a comment that no test exercises.** This one sat in the source for
   two batches, was quoted by five other comments as justification for writing handlers
   mutate-first, and was wrong from the day it was written. The tell was already in the audit: a
   guarantee documented as *"asserted, not tested"* is a guarantee that is probably false.
5. Same family as PM-002/PM-003/PM-006 — a plausible default standing in for something nobody
   verified. Here the default was *"the framework will clean up after me"*.

**Cost.** One live probe, plus the retroactive cost: every failed mutating call made through this
bridge since Batch K may have left a partial edit on an asset while telling the caller it had failed.

---

### PM-007 now has a regression test (added 2026-08-26)

`tools/test_inherited_components.py`, 37 checks. The fix described above had been in the handler for
weeks with nothing exercising it - and its symptom is invisible from the caller's side, because the
call correctly reports failure. A documented fix with no regression test is one the next person to
reorder that function can quietly undo.

The test asserts the postcondition rather than the return value: after a refused override, does
`get_inherited_component` still report `overrideExists: false`. That is a different question from "did
it fail", and it is the one PM-007 was about.

Writing it turned up that there are TWO rejection paths, not one:

* **Pre-flight** - the value fails the type check against the parent template (unknown property, text
  into a float, a struct into a bool). This is PM-007's path, and its message ends "NOTHING WAS
  CREATED OR MODIFIED".
* **Engine-apply** - the value passes pre-flight and the engine itself refuses it (garbage into
  `RelativeScale3D`). Its message says "did not apply" and promises nothing about what was left.

Both are tested, and both in their PARTIAL form - one good property alongside one bad - because a
half-applied batch is the shape a whole-batch check would wave through. All four leave
`overrideExists:false` and `existingOverrideCount:0`.

A note for anyone extending it: `revert_inherited_component` requires `confirm=true`, which the audit
harness strips, so only its refusal is covered. That gap is stated in the suite rather than hidden.

## PM-006 — a write was verified, and the value was still garbage

**Symptom.** `override_inherited_component {component:"Influence", properties:{"SphereRadius":"not-a-float"}}`
returned **`ok:true, applied:true`** with **`wanted:"0.000000"`**. The property really was written,
the read-back really did match, and the value the caller sent was meaningless. Nothing in the
response suggested a problem — the only clue was that a radius nobody asked for was now 0.

**Root cause.** Two engine facts and one wrong assumption on top of them.

`FNumericProperty::ImportText` for a floating-point property accepts only `[+-.0-9]`, stops at the
first character it cannot read, and has **no "nothing consumed" guard**
(`PropertyNumeric.cpp:125-137`). `"not-a-float"` therefore consumed nothing, left the destination at
0.0, and **returned success**. The same shape applies across the reflection system: an unrecognised
bool word is taken as `False` (`PropertyBool.cpp:384-397`), an unrecognised enum entry name imports
as 0 — i.e. the *first* entry — and an unresolvable object path is stored as null with success
reported (`PropertyBaseObject.cpp:388/422`).

The assumption was the anti-silence guard added in Batch F: *"after a successful import the leaf is
re-exported and compared with the pre-write export; if the caller asked for a change and the property
is byte-identical afterwards, the call FAILS."* That guard is correct and it caught real bugs. It
simply cannot catch this one:

```
wanted  = export(scratch after import)   -> "0.000000"
after   = export(live value)             -> "0.000000"
wanted == after -> verified
```

**Both sides are derived from the same misparse, so they agree.** The comparison proves the bytes
moved from the scratch buffer to the property. It says nothing about whether those bytes are what
the caller meant.

> **Verifying that the write LANDED does not verify that the VALUE WAS UNDERSTOOD.**
> A post-write check can only ever compare the system against itself.

**Fix.** Validate the caller's text against the **target property's type** *before* importing, in
ONE shared place — `MifBridge::ValidatePropertyText` (declared in `MifBridgeHandlers.h`, defined in
`MifBridgeNodes5.cpp`), reached from all four converters that turn caller input into property text:
`CanonicaliseLeaf` (the typed-JSON path), `set_property`'s string fast-path,
`override_inherited_component`'s string fast-path, and the material-expression writer. Numeric text
must parse **whole** — a prefix like `"12abc"` is refused because UE would take the 12, and exponent
form is refused because UE's parser cannot read it and would take only the mantissa. Bools must be a
recognised literal, enums a real entry with the valid ones listed in the error, hard object refs a
resolvable path or an explicit `None`. Where a kind cannot be pre-checked (a struct or container
handed over as export text) the response says so in `typeValidated` / `typeValidationNote` rather
than implying a guarantee that was not made. PM-003's scratch-buffer discipline is untouched:
validation happens before the scratch import, and the scratch import remains the only thing that
touches a value.

**Prevention.**

1. **A verification must not share an input with the thing it verifies.** If `wanted` and `after`
   both come from the same parse, the comparison is a tautology dressed as a check. Ask what the
   check would still catch if the parser were wrong — if the answer is "nothing", it is the wrong
   check.
2. **Validate at the boundary, not after the effect.** Type checking belongs where caller text meets
   the destination type. Anything downstream is measuring a system that has already agreed with
   itself.
3. **Every "lenient parser" in UE is a silent-default generator.** `ImportText*`, `Atod`, `Atoi`,
   `LexTryParseString` and `FJsonValueString::TryGetNumber` all stop at the first unreadable
   character and report success for the prefix. Treat "the parser returned true" as "it consumed
   something", never as "it consumed all of it" — `MifBridge::ParseWholeNumber` exists to be the one
   place that asks the stronger question.
4. This is the same family as PM-002 and PM-001: a plausible default standing in for a value the
   caller never gave. The difference is
   that here the default was manufactured *by the engine's own parser*, downstream of every guard the
   bridge had, which is why it survived a guard specifically written to catch silent writes.

**Cost.** Zero build time — it was found by a live probe, not by a failure. The cost is retroactive
and unbounded: **every numeric, bool, enum and object-ref property written through the bridge before
this fix was verified by a check that could not detect a misparse**, so any of them may hold a
plausible wrong value that was reported as applied.

---

## PM-004 — `create_function` minted a permanent duplicate exec pin

**Symptom.** Every function created through `create_function` with `outputs` carried two
`execute` input pins on its Return node. The Blueprint compiled, but raised a warning that
could not be cleared: there was no endpoint to remove a pin, so the artifact was permanent.

**Root cause.** `MifBridge::PlaceAndInit` mirrored the engine's standard node-spawn sequence:

```cpp
Graph->AddNode(...); Node->CreateNewGuid(); Node->PostPlacedNewNode(); Node->AllocateDefaultPins();
```

That is correct for almost every `UK2Node` — `PostPlacedNewNode` does not normally touch pins.
The function **terminators** are the exception. `UK2Node_FunctionResult::PostPlacedNewNode()` calls
`SyncWithEntryNode()`, which on a freshly created node always sees a signature mismatch
(`FunctionReference` is empty, the entry node's is not) and calls `ReconstructNode()` — fully
allocating the pins. The follow-up `AllocateDefaultPins()` then runs:

```cpp
// K2Node_FunctionResult.cpp
void UK2Node_FunctionResult::AllocateDefaultPins()
{
    CreatePin(EGPD_Input, UEdGraphSchema_K2::PC_Exec, UEdGraphSchema_K2::PN_Execute);   // no FindPin guard
    ...
}
```

with **no** `FindPin` guard — unlike `UK2Node_EditablePinBase::AllocateDefaultPins`, which does
check `!FindPin(...)` before creating each user pin. So the exec pin got created twice.

**Fix.** `PlaceAndInit` now only calls `AllocateDefaultPins()` when `Node->Pins.Num() == 0`.
Any node whose `PostPlacedNewNode` already produced pins is left alone; every other node is
unaffected. `create_function` additionally sweeps its Return node for duplicate
name+direction pins (keeping a wired copy) and reports `duplicatePinsRemoved`.

**Prevention.** When mirroring an engine call sequence, check whether the specific node class
overrides the hooks involved. "The engine does it this way" is only true for the node classes the
engine actually spawns through that path — `UK2Node_FunctionResult` is created by
`AddFunctionGraph`/`CreateFunctionGraphTerminators`, not by the generic action-menu path.

**Repairing existing assets.** `remove_pin` with `confirm=true` removes the duplicate
(`kind: "duplicate"`), keeping whichever copy is wired.

---

## PM-003 — a failed `set_property` destroyed the value it failed to set

**Symptom.** A `set_property` call with a value the property could not parse did not merely fail —
it left the property **wiped**. A typo in a struct literal cost the original value.

**Root cause.** `FProperty::ImportText_Direct` parses **in place**. It can consume and zero the
destination before deciding the text is invalid, and it returns `nullptr` on failure *after*
having already damaged the target. The handler passed the property's real address straight in:

```cpp
R = Leaf->ImportText_Direct(*ImportStr, LeafAddr, LeafOwner, PPF_None, &ErrText);
```

Second, smaller bug in the same bracket: `PostEditChangeProperty` fired **unconditionally**, so a
failed import still told listeners and instanced archetypes that the value had changed.

**Fix.** Import into a scratch buffer seeded from the current value, and only publish on success:

```cpp
void* Scratch = FMemory::Malloc(Leaf->GetSize(), Leaf->GetMinAlignment());   // engine's own idiom
Leaf->InitializeValue(Scratch);
Leaf->CopyCompleteValue(Scratch, LeafAddr);        // start from the current value
R = Leaf->ImportText_Direct(*ImportStr, Scratch, LeafOwner, PPF_None, &ErrText);
if (R) { /* transaction: Modify, PreEditChange, CopyCompleteValue(LeafAddr, Scratch), PostEditChange */ }
Leaf->DestroyValue(Scratch); FMemory::Free(Scratch);
```

Seeding from the current value preserves partial-struct-literal semantics (`(X=5)` leaves Y and Z
alone), matching the Details panel. `GetSize()` spans `ArrayDim`, so C-array properties round-trip.
`PostEditChangeProperty` now only fires on a write that actually happened.

**Prevention.** Never hand a live address to a parser that can fail. Treat every
`ImportText*`/deserialize-in-place API as destructive-on-failure unless documented otherwise.

---

## PM-002 — `create_blueprint` silently accepted an invalid `blueprintType`

**Symptom.** `blueprintType: "Widget"` (a natural guess for `"WidgetBlueprint"`) produced an asset
that looked fine in the content browser but had no `WidgetTree`, no designer tab, and failed every
widget endpoint afterwards. In one case a downstream call crashed the editor.

**Root cause.** The type dispatch was an `if / else if` chain ending in a bare `else` that treated
**any** unrecognised string as `Normal`:

```cpp
if (BpTypeStr.Equals(TEXT("FunctionLibrary"))) { ... }
else if (BpTypeStr.Equals(TEXT("Interface")))  { ... }
else if (BpTypeStr.Equals(TEXT("WidgetBlueprint"))) { ... }
else { ParentClass = ResolveClass(ParentName, nullptr); }    // <-- swallows typos
```

`"Widget"` fell through to the last branch, resolved `parentClass: "UserWidget"`, passed
`CanCreateBlueprintOfClass(UUserWidget)` (which is legitimately `true`), and produced a **plain
`UBlueprint` parented to `UserWidget`** — not a `UWidgetBlueprint`. A plain `UBlueprint` has no
`WidgetTree` member at all, so anything reaching for one dereferenced a field that did not exist.

**Fix.** Explicit allowlist checked *before* the dispatch; unknown values are rejected with a
message naming the valid set and calling out the `Widget` / `WidgetBlueprint` confusion directly.

**Prevention.** A string-to-enum dispatch must never have a silent default. Validate against an
explicit list first and fail loudly. This is the same class of bug as the silent-self-class
fallback in PM-001.

**Note.** `add_tree_widget` itself was already safe — it resolves through
`ResolveWidgetBlueprintField`, which does `Cast<UWidgetBlueprint>` and fails cleanly on a plain
`UBlueprint`. The crash came from creating the malformed asset in the first place.

---

## PM-001 — an omitted class parameter silently targeted the blueprint itself

**Symptom.** `add_cast` with any key other than `targetClass` produced a cast of the blueprint to
**itself**. It compiled clean, always succeeded at runtime, and was nearly invisible in review.
Each affected endpoint cost a probe to discover.

**Root cause.** `ResolveClass` treats an empty name as "self" by design, so that
`add_function_call` can mean "call this on myself":

```cpp
if (N.IsEmpty() || N.Equals(TEXT("self"))) { return ContextBP->SkeletonGeneratedClass ?: ContextBP->GeneratedClass; }
```

Endpoints where the class is **mandatory** called the same helper. A missing or misspelled key
produced an empty string, which resolved to the blueprint's own class — and then passed the
downstream sanity check, because the guard was a type test that self happens to satisfy:

- `add_spawn_actor` — `IsChildOf(AActor)` passes on an Actor BP → spawns a copy of itself
- `add_create_widget` — `IsChildOf(UUserWidget)` passes on a Widget BP → creates itself
- `add_tree_widget` — `IsChildOf(UWidget)` passes on a Widget BP → self-referencing child
- `add_cast` / `add_class_cast` — no type guard at all → self-cast

**Fix.** New `ResolveClassStrict` / `ResolveClassStrictField` reject an empty name with a message
naming the parameter. Applied at every site where the class is required. The strict variants also
accept the common alternate spellings (`class`, `castTo`, `to`, `targetType`) so the original
mistake now succeeds instead of failing.

**Prevention.** A helper with a convenience default is dangerous when the default is *plausible*.
Where a value is mandatory, use an API that cannot express the default — hence a separate strict
entry point rather than a flag on the existing one.

---

## `spawn_actor_in_level` silently discarded `mesh` — four ground rebuilds spent on the wrong problem

**Symptom.** Spawning a road mesh to measure it returned `ok: true` with a valid `actorPath`, and
`get_actor_bounds` then reported `hasBounds: false` and a size of `0 x 0 x 0` for every mesh tried.
The obvious reading — "these cooked meshes can't be loaded from a container" — was wrong.

**Root cause.** `H_spawn_actor_in_level` never read a `mesh` parameter at all. It spawned a bare
`AStaticMeshActor` with no mesh assigned and reported success. `spawn_many` (a different file,
`MifBridgeAuthoring.cpp`) *does* accept `mesh`, which is why bulk placement had always worked and
single spawns had never been noticed as broken.

**Fix.** `spawn_actor_in_level` now honours `mesh`/`staticMesh`, and fails loudly with a message
naming the actor class when the class has no `UStaticMeshComponent`. Note the mobility dance: a
spawned `StaticMeshActor` defaults to Static mobility, which refuses `SetStaticMesh` — the handler
flips to Movable, assigns, and restores.

**Prevention.** This is the same failure mode as the eight parameter-naming traps, and the same as
the `blueprintType` bug: **an ignored parameter is worse than a rejected one**, because the caller
gets a success response and then debugs the wrong subsystem. Any handler that accepts an optional
parameter must either act on it or explain why it could not.

**Cost.** Real cost was not the measurement detour but the four preceding ground rebuilds (stretched
plane → mixed materials → tile grid → 2,116 instanced rock meshes). Each failed *differently*, which
kept suggesting the next parameter tweak. The actual problem was a missing capability — see
`08_LANDSCAPE.md`. **When repeated attempts at one goal each fail in a new way, stop tuning and ask
what capability is absent.**

---

## Binding an RVT with no valid pages is worse than not binding one

**Symptom.** Terrain rendered black with white speckle. Diagnosed as an unbound runtime virtual
texture, since `DDS2_Landscape_MasterMat` samples one. Built `bind_landscape_rvt`, bound
`RVT_IslaSombraLandscape` + `...Height`, created the volumes, verified all of it in `landscape_info`.
Terrain stayed black — and every building and road in the level turned blown-out white.

**Root cause.** The conclusion was backwards. Binding an RVT that has no valid pages does not fix the
terrain; it breaks *every other material that samples that RVT*. Buildings and roads sample the
landscape RVT to blend their bases into the ground, so they went from correct to white the moment the
RVT existed but had nothing in it. Deleting the two `ARuntimeVirtualTextureVolume` actors restored
buildings, roads and props immediately.

**What actually proved it.** A `/Engine/BasicShapes/Cube` with `BasicShapeMaterial` spawned beside a
building rendered perfectly — correct albedo, correct shading, correct cast shadow — while the
building next to it was pure white and the ground was pure black. That single frame ruled out
exposure, lighting and the capture pipeline in one shot, and pointed at "specific materials" rather
than "the scene". Reach for a known-good reference object *early*; it is far cheaper than another
round of cvar tweaking.

**Fix.** Do not bind the shipped RVTs into a scratch level. The black terrain was a separate problem,
solved by assigning a landscape material that does not sample an RVT — `set_property` on
`LandscapeMaterial` applies in place, no rebuild needed.

**Prevention.** `bind_landscape_rvt` is still correct *when the RVT is genuinely populated*, but its
note must say what binding costs when it is not. An RVT is a scene-wide contract, not a per-actor
setting: turning it on changes every material that reads it, including ones you were not looking at.

**Cost.** Several rounds spent on exposure, sun angle, scalability and auto-exposure cvars — all of
which were fine — because the failing thing (terrain) and the thing that broke (buildings) were never
considered as one symptom with one cause.


---

## PM-005 — Two files defined the same helper, and the unity build turned it into a C2084 nobody could place

**Symptom.** A build that had been fine failed with
`error C2084: 'void MifBridge::<unnamed-namespace>::EmitAssetIdentity(...)': function already has a
body`, naming a file whose author had not touched the other copy and had no reason to know it
existed. Later, the same class of failure again with `CollectPIEWorlds`. Between them they cost more
than half a day and one broken mid-session DLL, and the "fix" the first time was to move one copy —
which left the pattern intact.

**Root cause.** Unreal builds this module as a **unity build**: UBT concatenates the `.cpp` files
into `Module.MifBridge.1.cpp`, `…2.cpp`, `…3.cpp`, closing a blob once the cumulative source size
reaches `NumIncludedBytesPerUnityCPP` (393,216 by default). Inside one blob, every file is one
translation unit, and:

* **`[namespace.unnamed]/1`** — *all* unnamed-namespace definitions in a translation unit are the
  **same** namespace. `namespace MifBridge { namespace { … } }` written in two files that land in one
  blob is **one** `MifBridge::<unique>`. Anonymous namespaces give you **zero** isolation here.
* **`static` is no better.** Two file-scope `static` functions of the same name at the same namespace
  scope in one TU are a redefinition, exactly like two non-static ones.
* Mixing the two forms is *worse*, not better: an unnamed-namespace copy and a `static` copy in one
  TU produce **C2668 ambiguous call** at call sites in the file that has neither — a much harder
  error to read than C2084.

**The part that makes this recur.** Blob membership is a function of file **sizes**, not of intent.
Two colliding helpers can sit in different blobs for months and then collide because somebody added
200 lines to an unrelated file. When the audit measured it, `EditorWorld()` — defined identically in
`MifBridgeStreaming.cpp` and `MifBridgeWorld.cpp` — was **~8 KB of source growth anywhere in blob 1**
away from a hard C2084, and `Vec3` already shared blob 2 with a second definition, compiling only
because the two arities happened to differ (so one file was silently calling the other's
implementation).

**Fix.** Every shared helper has exactly ONE definition, in `Private/MifBridgeCommon.cpp`, declared
in `Private/MifBridgeHandlers.h`. Batch K evicted the survivors: `EditorWorld` (3 copies),
`JsonTypeName` (2, already diverged in caller-visible error text — one said `bool`, the other
`boolean`), `NormalizeBoolLiteral` (2), `Vec3` (2), `ResolvePropertyPath` (3), `ParsePinSpecs` (2),
and the actor finder (**5**, under five different names).

**Prevention — do all four:**

1. **Before adding any file-local helper**, grep the module:
   `grep -rn "\<YourHelperName\>" Source/MifBridge/Private/*.cpp`. A hit anywhere means promote,
   not copy.
2. **Never rely on "they're in different blobs."** It is not a property you control and it moves on
   its own.
3. **A differently-NAMED copy is not safe either** — it is worse, because the compiler never tells
   you. Five identical actor finders under five names survived three audits precisely because they
   never broke the build; the cost was that a fix to the matching rule landed in one of five places.
4. **"EVICTION CLAUSE" comments with no trigger are how a temporary duplicate becomes permanent.**
   Two of them sat in this codebase across multiple batches doing nothing. If a helper must be
   duplicated, the clause needs a *named* trigger event, not "when the fence lifts".

**Cost.** Two build breaks, one of them leaving the module unbuildable mid-session, plus the audit
pass that had to enumerate blob membership by hand to work out how close the rest were.

---

## `splice_into_exec` on a MACRO INSTANCE pin crashes the editor (2026-08-13)

**Symptom.** Editor died mid-request with

```
Assertion failed: OwningNode [File:D:\UE532\...\EdGraph\EdGraphPin.h] [Line: 427]
  MifBridge::SpliceExecAfter()   MifBridgeCommon.cpp:2844
  MifBridge::H_splice_into_exec() MifBridgeNodes.cpp:2018
```

**Repro.** `splice_into_exec` with `afterNode` = a `K2Node_MacroInstance` (a **ForEachLoop**) and
`afterPin` = `"Completed"`. The same endpoint had just run **nine** times without incident in the
same session against ordinary nodes — a `K2Node_Event`'s `then`, four `K2Node_IfThenElse` (`then` /
`else`), and a `K2Node_SwitchEnum`'s per-value pins. Plain nodes are fine; the macro instance is not.

**Root cause (INFERRED — not read from the source).** `SpliceExecAfter` walks the pin's existing
links and dereferences `Pin->GetOwningNode()`. On a macro instance the exec pins are tunnel
boundaries, and at least one linked pin reaches a node the splice code does not expect, so
`OwningNode` fails its check. `MifBridgeCommon.cpp:2844` is the line to read before fixing — this
explanation has NOT been verified against the source and must be confirmed there.

**Cost.** The edited blueprint had compiled clean (0 errors / 0 warnings) but had **not been saved**,
so an entire graph rewire was lost. Nothing was corrupted: the pre-edit `backup_blueprint` `.uasset.bak`
was byte-identical to the on-disk original.

**Prevention:**

1. **Do not `splice_into_exec` onto a macro instance pin** (`ForEachLoop`, `ForLoop`, `WhileLoop`,
   `IsValid`, `Gate`, `DoOnce`, `FlipFlop`, …). Wire it by hand instead — `disconnect_pin` the macro's
   exec-out, then two `connect_pins` (macro → new node, new node → the original target). That is three
   calls and does not touch the crashing path.
2. **`save_blueprint` after every stage, never batch-then-save.** A clean compile is not durability.
   This is the same lesson as the unsaved-struct crash — it recurred because the earlier fix was
   remembered as being about *struct creation* rather than about *unsaved editor state in general*.
3. If the editor does die, check `Saved/Autosaves/PackageRestoreData.json` before relaunching. It
   makes the next launch show a restore modal that has hung this editor before (see
   `dds2-editor-restore-modal-hang`); move it aside and redo the edit from a script instead.

---

## MifBlender generation: 3 node drifts, a half-blind validator, and a broken mesh handoff (2026-08-15)

**Symptom.** `gen_asset` died 72 s in, at the first Hunyuan3D node:

```
node 'Hy3DGenerateMesh' has no input(s) attention_mode. It accepts: force_offload,
guidance_scale, image, mask, pipeline, scheduler, seed, steps.
```

**Repro.** Any `gen_asset` / `gen_mesh` / `gen_texture` call against the currently installed
ComfyUI-Hunyuan3DWrapper. The Flux half is unaffected — all 8 of its nodes still match.

**Root cause.** Four separate defects, only the first of which announced itself:

1. `Hy3DGenerateMesh` lost `attention_mode`; it moved to **`Hy3DModelLoader`**, where it is optional
   (`sdpa` / `sageattn`). Setting it on the sampler is now a hard reject.
2. `DownloadAndLoadHy3DDelightModel` gained a **required** `model` (`hunyuan3d-delight-v2-0`).
3. `DownloadAndLoadHy3DPaintModel` gained a **required** `model` (`hunyuan3d-paint-v2-0`, or
   `-turbo`). Both were being sent `"inputs": {}`, which used to mean "take the default".
4. **`_check_inputs` only ever tested one direction.** It rejected inputs the node does not declare
   and never checked that every *required* input is present. So drift 1 was caught instantly and
   drifts 2 and 3 passed the addon's own validator, to fail at ComfyUI queue time — minutes in,
   after the shape stage had already been paid for.

**A fifth, independent defect** surfaced behind them: `op_gen_texture` passes an absolute
`mesh_path` straight into `Hy3DUploadMesh.mesh`, but that input is an **enum over ComfyUI's `input`
directory** (`{"mesh": [[]]}` — empty on a clean install). The addon has `_upload_image` for images
and **no mesh equivalent**, so texturing a mesh that lives anywhere else is an unconditional
`HTTP 400`. `gen_asset` routes through the same call, so the whole textured path was broken, not
just the standalone op. Worse, ComfyUI puts the real reason in the *body* of the 400 — the bare
`urllib` message is `HTTP Error 400: Bad Request`, which says nothing and cost a debug cycle.

**Cost.** One failed run, one wrong root-cause call (the first reference image was blamed on framing
before `PROMPT_SUFFIX` turned out to force `orthographic side profile` **deliberately** — the
addon's stated reason is that a three-quarter view yields a subtly sheared mesh), and a stale
deployed addon that masked the fix.

**Prevention:**

1. **`_check_inputs` now tests both directions** — unknown inputs *and* absent required ones. Drift
   moves both ways; checking one way is worse than it looks, because it reads as coverage.
2. **Audit every node in every workflow against `/object_info` at once, not one failure at a time.**
   One query returned all three drifts in a second. Fixing only the node that happened to fail first
   would have bought exactly one more minute before the next one.
3. **A mesh handoff needs an upload helper.** Until `gen_texture` gets one, chain shape → paint in a
   single graph (feed `Hy3DPostprocessMesh` straight into `Hy3DMeshUVWrap`) so the mesh never leaves
   the workflow. That is also faster than a file round-trip. **`gen_texture` on a standalone mesh
   path remains broken and is the open item here.**
4. **Always read the body of a ComfyUI 400.** `urllib.error.HTTPError.read()` carries `node_errors`;
   the exception's `str()` carries nothing.
5. **The live addon is a real copy, not a junction.** It loads from
   `%APPDATA%\Blender Foundation\Blender\4.4\scripts\addons\MifBlender\`, so editing the repo under
   `tools/blender-addon/` changes nothing until it is synced and Blender is restarted. Verify with a
   `grep` of the deployed file, not the repo one.

**Also worth knowing: a `gen_*` call makes Blender show "Not Responding", and that is correct.**
Every op runs inline on the main thread (`server.py::_drain_timer` → `_execute`) and `_wait` is a
blocking `time.sleep(2.0)` poll loop, so a multi-minute generation owns the main thread throughout
and the window never redraws. To keep Blender interactive, drive ComfyUI from the calling process
and use the addon only for the (millisecond) import.

---

## `Build.bat` exits 0 when it never built anything (2026-08-15)

**Symptom.** A plugin rebuild reported success — `[exited with code 0]`, no error — and the plugin's
behaviour was unchanged, because the DLL had not been touched:

```
Total execution time: 0.44 seconds
Unable to find project 'D:\UE532\Engine\Source\"D:\DDS2SDK\Game\DrugDealerSimulator2.uproject"'.
[exited with code 0]
```

**Root cause.** The `-Project=` argument was passed with escaped inner quotes through
`cmd /c "... -Project=\"D:\...\uproject\" ..."`. The quotes survived into the argument *value*, so UBT
saw a path with literal `"` characters, failed to match it against any known project, and resolved it
relative to the engine's `Source` directory. **It then exited 0.** Two independent tells that nothing
happened: a **0.44 second** total execution time (a real incremental link is ~30 s) and no `Compile` /
`Link` lines at all.

**Cost.** Nearly reported a new C++ endpoint as built and ready to test. The next step would have been a
live call against a DLL that did not contain it, and the resulting "unknown endpoint" would have been
debugged as a binding problem rather than a build that never ran.

**Prevention:**

0. **The `built <date>` string the bridge reports is not evidence either** (added 2026-08-26). It is
   baked in at compile time by whichever translation unit holds it, and a unity build only recompiles
   the blobs that changed. After a second build touching one file, the DLL mtime had moved and the
   bridge still reported the *previous* build's timestamp — because the blob holding the version
   string had not been recompiled. Using it to answer "is my change live?" gives a confident wrong
   answer. What actually settles it, in increasing order of certainty: the DLL's mtime moved, and the
   DLL literally contains a string from the change:

   ```bash
   python -c "raw=open('Binaries/Win64/UnrealEditor-MifBridge.dll','rb').read(); print('the text from your change'.encode('utf-16-le') in raw)"
   ```

   UE string literals are UTF-16 in the binary, so search for the wide encoding — an ASCII search
   returns "missing" for a string that is definitely there.

1. **Never trust `Build.bat`'s exit code. Verify the DLL's mtime moved.** This is already the rule in
   memory `dds2-reconstructor-build-engine` ("verify DLL mtime moved") — it was written for a
   *different* failure (building against the wrong engine) and applies verbatim here. Record the
   timestamp *before* building so the comparison is possible:
   ```bash
   find "D:/DDS2SDK/Game" -name "UnrealEditor-MifBridge.dll" -printf "%TY-%Tm-%Td %TH:%TM  %s bytes\n"
   ```
2. **Do not quote `-Project=`.** None of these paths contain spaces, so the quotes buy nothing and
   cost this. The form that works from bash:
   ```bash
   cmd //c "D:\UE532\Engine\Build\BatchFiles\Build.bat DrugDealerSimulator2Editor Win64 Development -Project=D:\DDS2SDK\Game\DrugDealerSimulator2.uproject -WaitMutex"
   ```
3. **A build that took under a second did not build.** Treat sub-second "success" as failure and read
   the output before believing it.

## A modal dialog is a deadlock, and "no dialog" engine APIs still raise them (2026-08-25)

**Symptom.** The endpoint sweep reported `duplicate_asset` as a **critical crasher** — "bridge died on
well-formed but nonexistent references". The editor produced a crash dump
(`EXCEPTION_ACCESS_VIOLATION`) and the user saw a fatal-error dialog.

**It was not a crash.** The editor's own log named the real event:

```
Message dialog closed, result: Yes, title: Message,
text: An object [Nope] of class [Blueprint] already exists in file [/Game/_MifAuditGhost_.../Nope].
```

`duplicate_asset` opened a **modal dialog** and the editor sat on it. MifBridge handlers run
synchronously inline on the game thread, which is the same thread the HTTP server answers on, so a
modal stops the bridge answering *anything*. From outside it is indistinguishable from a crash — and
it is worse than a crash, because the editor looks alive. The access violation came later, from the
fuzzer force-relaunching the unresponsive editor.

**Root cause.** Two call sites carried the comment `// headless — no dialog`. That is wrong in a
specific and general way:

> In `AssetTools` and `ObjectTools`, the "no dialog" flag suppresses the **picker**, never the
> **validation**.

`IAssetTools::DuplicateAsset` really does pass `bWithDialog=false`, but that flag only reaches the
*overwrite* prompt at the very end, inside `ObjectTools::DuplicateSingleObject`. Long before that,
`PerformDuplicateAsset` calls `CanCreateAsset` (`AssetTools.cpp:4287`), which calls
`FMessageDialog::Open` **unconditionally** for an invalid name, a clash with a map file, or an
existing destination. `PerformDuplicateAsset` opens another itself if the source object is null.

**The part that makes this severe, not merely annoying.** The dialog it raised was destructive:
*"Do you want to replace the existing object? If you click 'Yes', the existing object will be
deleted."* Meanwhile `duplicate_asset`'s own guard text promised it *"never overwrites; it fails if
newPath is already taken"*. It did not fail. It waited for a human, and a "Yes" would have deleted the
asset.

**Cost.** An editor kill and a relaunch, a crash misattributed to a crasher that did not exist, and an
hour of engine-source reading. Most of that hour was productive only because the *log* was read
instead of the crash dump — the callstack was unsymbolicated and said nothing useful.

**Fix.** Three sites, one root cause:

* `duplicate_asset` — refuse a taken destination **before** calling AssetTools, so the documented
  promise is real rather than incidental, plus the guard below.
* `rename_asset` — same false claim, same guard.
* `delete_asset` — **the one no sweep could have found.** It already passed `bShowConfirmation:false`,
  and `ObjectTools.cpp:2833` opens a dialog that flag does not gate at all, fired whenever the
  `OnAssetsCanDelete` delegate vetoes — which happens for ordinary reasons such as an asset editor
  still holding the asset open. It is DENY-listed in the fuzzer; it was found by auditing the pattern
  after the first one.

The guard is `TGuardValue<bool>(GIsRunningUnattendedScript, true)`. `FMessageDialog::Open` shows UI
only when `!FApp::IsUnattended() && !GIsRunningUnattendedScript` (`MessageDialog.cpp:172`); otherwise
it logs and returns the **default** — `No` for a YesNo — so a destructive prompt is *declined* rather
than blocked on.

**Prevention:**

1. **Treat every `AssetTools` / `ObjectTools` "no dialog" flag as covering the picker only.** Read the
   validation path before believing a call is headless. `bWithDialog=false` and
   `bShowConfirmation=false` both proved insufficient.
2. **`tools/audit_modals.py`** now enforces this. It reports every MifBridge call into a known
   prompting API as guarded or not, **and** re-verifies that the engine lines cited as proof still
   contain what they are quoted as saying — so the audit cannot rot silently against a future engine.
   Run it like `parity_check.py`.
3. **A "crash" that produces no useful callstack may not be a crash.** Read
   `Saved/Logs/<Project>.log` from the crash folder before the dump. The log named this in one line;
   the dump was unsymbolicated addresses.
4. **This was already documented, twice, and it still happened.** `02_GOTCHAS.md` section 8 is
   titled *"The bridge stops answering but the editor is alive — look for a modal window"* and even
   says the symptom is indistinguishable from a crash. `MifBridgeUndo.cpp:539` calls a modal on the
   game thread *"a deadlock, not a dialog"*. What was missing was never the threading model — it was
   knowing **which calls can prompt**, and that the "no dialog" flags do not cover validation. That
   list is now a table in section 8 and is enforced by `tools/audit_modals.py`. A hazard documented
   in the abstract does not prevent anything until it names the specific calls.


## `snap_actors_to_ground` missed every actor standing over a prop (2026-08-25)

**Symptom.** Reported as *"misses ~112 of 303 actors on flat ground"*. Reported honestly as `missed`,
not as a wrong snap — the endpoint said so.

**Root cause.** The handler traced once with `LineTraceMultiByChannel` and then searched the results
for the first hit that was ground, on the stated belief that a MULTI trace sees everything along the
ray where a single trace stops at the first blocker. From `Engine/World.h`, on **both** multi variants:

> Only the single closest blocking result will be generated, no tests will be done after that

A multi trace returns overlaps plus **one** blocking hit. Every static mesh blocks `WorldStatic`, so
for an actor standing over another actor the results held exactly one hit — the prop — and the
landscape underneath was never in them. The 191 that worked were over open landscape; the 112 that
failed each had something beneath them.

**What makes this worth recording.** The previous change was aimed at a *real* bug — a palm snapping
onto a shack roof, the scene walking upward a layer per call — and it *did* stop that bug, by
**missing** rather than by finding the real ground. So the symptom changed from "snapped wrong" to
"missed", the fix looked like it worked, and the incorrect belief was written down as a confident
comment that reads exactly like a correct one.

**Fix.** Ignore each non-ground blocker and trace again, bounded at 32 so a deep stack cannot spin.
Giving up is reported separately (`missedUnderDeepStack`) from an honest "there is nothing below this
actor", because those are different problems with different fixes.

**Prevention:**

1. **A comment asserting what the engine does needs a test, not prose.** This is the same failure as
   `add_timeline`, whose comment claimed `PostPlacedNewNode` built the timeline template on a node
   with no such override. Both read as authoritative; neither was checked.
2. **When a fix changes the symptom rather than removing it, suspect it.** "Snapped wrong" becoming
   "missed" was the tell, and it was visible in the numbers for a while before anyone looked.
3. `tools/test_snap_ground.py` builds a purpose-made column (floor, blocker, subject) and asserts the
   subject lands on the **floor**, not on the blocker — plus three checks that the fix did not regress
   into "snap onto whatever you hit first", which is the original bug.

## A guard that checked the blueprint while its comment promised the graph (2026-08-26)

**Symptom.** `add_anim_node` aimed at the EventGraph of a valid Animation Blueprint terminated the
editor mid-request. The caller saw a closed socket; the log said:

```
Fatal error: [Casts.cpp:10] Cast of EdGraph /Game/_MifAnim/ABP_66339:EventGraph to AnimationGraph failed
  FAnimStateMachineNodeNameValidator::FAnimStateMachineNodeNameValidator()  AnimGraphNode_StateMachineBase.cpp:46
  UAnimGraphNode_StateMachineBase::MakeNameValidator()
  UAnimGraphNode_StateMachineBase::PostPlacedNewNode()
  MifBridge::H_add_anim_node()                                             MifBridgeAnimation.cpp:676
```

**Root cause.** The handler carried this guard:

```cpp
// An anim node in a non-anim GRAPH compiles to nothing and is a confusing thing to debug, so
// refuse it here rather than let it sit in an EventGraph looking placed.
if (!Blueprint->IsA<UAnimBlueprint>())
```

The comment describes a check on the GRAPH. The code performs one on the BLUEPRINT. An Animation
Blueprint has both an AnimGraph and an EventGraph, so a node aimed at the EventGraph of a perfectly
valid ABP passed the guard. `PostPlacedNewNode` then built a name validator that CastChecks its graph
to `UAnimationGraph`, and a failed `CastChecked` terminates the process rather than returning null.

**The gap between the comment and the code was the gap between an error message and a dead editor.**

**Cost.** One editor, and it was found only because the issue was re-opened and PROBED. It had already
been filed as a documentation mismatch and dismissed once as a false positive - on the reasoning that
a guard was present, without asking what the guard actually checked.

**Prevention:**

1. **A guard's comment is a claim about behaviour and needs a test, not prose.** This is the same
   lesson as the snap_actors_to_ground postmortem, arrived at from the other side: there the code was
   right and the comment was vague; here the comment was right and the code was narrower. Both were
   invisible until something executed them. test_anim_nodes T550 is that test.
2. **Check the thing the engine will touch.** The node is placed in a GRAPH and its PostPlacedNewNode
   casts that GRAPH. Validating the owning asset instead is validating a proxy for the real question.
3. **A `CastChecked` in engine code reached from a handler is a live grenade.** gotchas section 6c
   already records this for cooked assets; this is the same hazard from a different direction - the
   object was perfectly valid, it was simply the wrong TYPE for where it was being used.
4. **When a filed issue is dismissed as a false positive, the dismissal deserves the same standard of
   evidence as the finding.** Reading that a guard exists is not the same as establishing it guards
   the thing the comment names.
