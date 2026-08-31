# MifBridge — postmortem log

One entry per bug that cost real time. Symptom → root cause → fix → prevention.
Newest first.

---

## A guard I had just written would have refused every landscape on 5.7

**Date** 2026-08-31

**Symptom.** None, on the engine I was testing. Every suite was green, the endpoint behaved exactly
as designed, and the regression was invisible.

It was found by a **compiler**, on an engine with no editor I was allowed to use.

**What it was.** Earlier the same night I added `RefuseIfEditLayers()` - a guard stopping
`sculpt_landscape` and `import_landscape_heightmap` from writing the merged heightmap on a landscape
whose edit-layer composite would discard the write. It asked the obvious question:

```cpp
if (!Landscape || !Landscape->HasLayersContent()) { return false; }
```

On UE 5.7 that function is:

```cpp
// LandscapeEditLayers.cpp:6747
bool ALandscape::HasLayersContent() const
{
	return true;
}
```

Unconditionally. 5.7 deprecated non-edit-layer landscapes entirely - *"all landscapes use the edit
layer system now"* - so the predicate is a **constant** there. The guard would have refused every
landscape on 5.7, disabling two endpoints for every user of that engine, and nothing on 5.3 could
ever have shown it because 5.3 returns the real answer.

**How it was found.** A probe build of the plugin against a stock 5.7. Not a test - a compile. The
deprecation warning is what pointed at it:

```
warning C4996: 'ALandscape::HasLayersContent': Non-edit layer landscapes are deprecated,
all landscapes use the edit layer system now. - Please update your code to the new API
before upgrading to the next release, otherwise your project will no longer compile.
```

Reading the 5.7 body to see what the "new API" replaced is what turned a routine deprecation warning
into a shipped-regression catch. **The warning did not say the function had become a constant.**
That was only in the source.

**Fix.** `HasEditLayers()` reads the edit-layer STACK on 5.6+ (`ReadEditLayers().Num() > 0`) - a real
answer on any engine - and keeps the old call only under `#if !MIF_ENGINE_AT_LEAST(5, 6)`, where it
is neither deprecated nor constant.

**And a deliberate asymmetry, which is the part worth arguing with.** On 5.6+ the guard now WARNS
instead of refusing. It is true that a 5.7 landscape always has edit layers, and it may well be true
that the merged write is discarded there too - but that was measured on 5.3 and cannot be measured
on 5.7 here, because the only 5.7 editor on this machine is running someone else's work. Refusing
across an entire engine version on an inference is worse than the hazard it would prevent, so the
response carries `editLayerWarning` naming the 5.3 measurement and telling the caller to re-export
and compare. The finding reaches them; the endpoint is not taken away from them.

**Prevention - two rules, and the second is the one I would have missed.**

> A deprecated engine function may have been replaced by a CONSTANT. `UE_DEPRECATED` tells you the
> call is going away; it does not tell you the answer changed. Read the body.

This project already had the sibling rule from `ForEachActorDesc` - deprecated-but-EMPTY, compiles
and iterates nothing. Deprecated-but-CONSTANT is the same trap returning `true` instead of doing
nothing, and it is more dangerous, because an empty function usually shows up as "nothing happened"
while a constant `true` shows up as confident wrong behaviour.

> A guard added on one engine is UNTESTED on every other engine until a compiler has seen it there.

The 5.6+ branch of this code had been reasoned about carefully against the headers and was right
about the API. It was wrong about the SEMANTICS, and only building it surfaced that.

**Also found, not fixed, filed.** `create_landscape` calls `CanHaveLayersContent` and
`ToggleCanHaveLayersContent`, both `UE_DEPRECATED(5.7)`, the latter with *"Use
ConvertNonEditLayerLandscape"*. Its documented behaviour - "create_landscape deliberately turns edit
layers OFF" - cannot hold on 5.7, where that is not a thing a landscape can be. That is pre-existing
and needs its own measurement on a real 5.7 editor.


## A helper named for the level it does not read, and three messages that claimed "every actor"

**Date** 2026-08-31

**Symptom.** `test_layers` had been 17 PASS / 0 FAIL all session. Run again after other suites, it
was 14 / 3. The three failures were all L102, asserting that adding an actor to a classic layer on a
World Partition map is refused. It was not refused - `modify_actor_layers` returned `ok:true` with
`membershipsChanged: 1`, while `list_layers` in the same run still reported
`levelIsPartitioned: true` and a note reading *"nothing can be added to a layer here however the
call is spelled"*.

**Root cause.** The engine's predicate is per-ACTOR and reads that actor's own level:

```cpp
// AActor::SupportsLayers, ActorEditor.cpp:982
const bool bIsPartitionedActor = GetLevel()->bIsPartitioned;
```

Not the world's. An earlier suite in the same editor had run `add_sublevel` +
`set_current_sublevel`, so new actors were being created in a **classic streaming sublevel of a
partitioned world** - where `SupportsLayers` is true and classic Layers work exactly as they always
did.

The bridge could not see the difference because its helper was:

```cpp
bool MifCurrentLevelIsPartitioned()          // reads World->PersistentLevel
```

**The name was the bug's disguise.** Every one of its three callers reads "current" as "the level I
am working in", and that is the one level it does not report. Two of them phrased their answer as a
claim about *every actor*, which a single counterexample disproves - and which this codebase would
have caught long ago in anyone else's code.

**Fix.** Renamed to `MifPersistentLevelIsPartitioned`, added `MifEditingLevelIsPartitioned`
(`World->GetCurrentLevel()`), and `list_layers` now reports **both** - `levelIsPartitioned` kept as
it was, plus `currentLevelIsPartitioned`, which is the field that actually predicts whether an add
will work. The mixed case gets its own note saying actors placed now *can* hold layers while ones
already in the partitioned persistent level cannot. The refusal and `notValidNote` say "these actors
live in a partitioned level" instead of "every actor".

**What made it visible, and it was luck.** Suites are normally run one at a time against a fresh
editor, where the persistent and current levels agree and the claim looks true. This surfaced only
because a *different* suite - `test_uncovered_reads7`, changed an hour earlier to discover its own
sublevel fixture - left a sublevel current. Cross-suite state contamination is usually a nuisance;
here it was the only thing that produced the counterexample.

**Prevention.**

> When a helper reports a property of "the level", name which level. `Current`, `Persistent` and
> `Editing` are three different answers and the wrong one still compiles.

And the second, which is the one worth carrying further:

> A message that says **every** is a claim strong enough to be disproved by one example. Prefer
> naming the thing the engine actually tests - here `GetLevel()->bIsPartitioned` - so the message
> stays true when the general statement stops being.

**Not fixed here, filed instead.** Suites can leave the editor's current level changed, and the next
suite inherits it. `test_uncovered_reads7` cannot restore it: `remove_sublevel` needs
`discardUnsaved`, which has no scratch exemption by design. Worth a suite-level "restore the current
level" convention rather than each suite defending itself.


## verticesChanged reported 0 for a deformation that had actually happened

**Date** 2026-08-31

**Symptom.** `apply_spline_to_landscape` returned `ok:true`, `splineLength:4088`, and
`verticesChanged:0` on a 2017x2017 World Partition landscape with two sculpt edit layers. A fresh
`export_landscape_heightmap` immediately afterwards was byte-identical to the one taken before, so
the two agreed and the obvious reading was that nothing had happened.

An export taken **one second later** differed. The deformation had worked the whole time.

**Root cause.** `ALandscapeProxy::EditorApplySpline` rasterizes inside a
`FScopedSetLandscapeEditingLayer`, and that scope's destructor does not composite - it calls
`RequestLayersContentUpdate(ELandscapeLayerUpdateMode::Update_All)`. On a landscape with edit
layers the write lands **in the layer**, and the composited heightmap everything else reads is
rebuilt on a later tick. This endpoint sampled the heightfield immediately after the call returned,
so it read pre-deformation heights and counted zero differences.

The count was not wrong about what it measured. It measured the right array at the wrong moment.

**Why this is the worst possible shape of answer here.** `verticesChanged` exists *because*
`EditorApplySpline` returns `void` - it is this endpoint's entire answer to "judge by postcondition,
never by the engine's return value". A caller doing the correct thing, checking
`verticesChanged > 0`, would conclude nothing happened and quite reasonably retry or report failure,
while the terrain had already moved under them. A silent no-op is bad; a **confident false zero
about a change that did occur** is worse, because it survives exactly the check that is supposed to
catch it.

**Fix.** Flush the composite before sampling:

```cpp
if (Landscape->HasLayersContent())
{
    Landscape->ForceUpdateLayersContent();
}
```

Called with **no argument** deliberately. 5.3 and 5.6 declare
`ForceUpdateLayersContent(bool bInIntermediateRender = false)`; 5.7 splits it into a plain
`ForceUpdateLayersContent()` plus a `UE_DEPRECATED(5.7)` `(bool)` overload. The no-arg spelling binds
the default argument on the old engines and the non-deprecated overload on 5.7, so one line is
correct on all three - whereas passing an explicit `false` compiles everywhere and picks the
deprecated overload on 5.7.

**How it was found, which is the transferable part.** Not by reading the handler. The suite asserted
`verticesChanged > 0` and it failed; the first two explanations I reached for were both wrong, and
both were about my own fixture:

1. *The spline is too short.* It was - `add_component` gives a SplineComponent UE's default two
   points 100uu apart and the suite never set any, so a 400uu brush was carving a 100uu line across
   a 201600uu landscape. Fixed with `set_spline_points`; still zero.
2. *The spline is flush with the ground.* It was - `snapToGround` puts the points exactly on the
   surface, and raising terrain to meet a spline already at terrain height is a no-op by
   construction. Fixed with `groundOffset:600`; still zero.

Only after those did the count get treated as the suspect rather than the fixture. The thing that
settled it was **polling**: re-export at 1s, 2s, 4s, 8s and print whether the heightmap ever
differs. It differed at one second, which converted "the endpoint does nothing" into "the endpoint
measures too early" in a single run.

**Prevention.** Two fixture defects hid an endpoint defect, and that is the normal order - an
inadequate fixture makes a suite that cannot fail, only mislead, because its zero looks like the
endpoint's fault. So the suite now asserts the fixture *before* trusting the result
(`splineLength > 1000`, with failure text naming the fixture as the culprit), and the permanent
assertion is no longer `verticesChanged > 0` but that **the count and an independent settled
re-export agree**:

> Either number alone is plausible. The contradiction between them is the defect.

That formulation generalises past this endpoint: wherever a handler reports a count it measured
itself, the assertion worth having is not that the count is non-zero but that it agrees with a
different endpoint looking at the same world.

**The audit found two more, and they are a DIFFERENT bug wearing the same symptom.**

`sculpt_landscape` and `import_landscape_heightmap` both call
`FLandscapeEditDataInterface::SetHeightData` with **no** `FScopedSetLandscapeEditingLayer`. So they
do not write a layer at all - they write the merged composite, and the next edit-layer update
regenerates that composite from the layers and throws the write away. Measured identically for
both on the same landscape: `ok:true` (import also reporting **zero mismatches** from its own
read-back), an export immediately afterwards differs, and an export two seconds later is
byte-identical to the one taken *before*.

So there are two faults here, not one, and the distinction decides the fix:

| | writes through the layer? | what went wrong | fix |
|---|---|---|---|
| `apply_spline_to_landscape` | yes, engine opens the scope | write persisted, **measurement** ran early | `ForceUpdateLayersContent()` before sampling |
| `sculpt_landscape` | no | write is **discarded** | refuse on a layered landscape |
| `import_landscape_heightmap` | no | write is **discarded** | refuse on a layered landscape |

"landscape writer + edit layers" is not by itself the bug. Writing the *merged result* is. Giving
the spline endpoint the refusal would have broken a working feature, and giving the other two the
flush would have made them flush away their own writes more promptly.

`import_landscape_heightmap` had refused a `layer` **parameter** for a long time, with the words
"without it the write silently lands on the merged result instead - a wrong answer that looks like
a right one". That note was correct and it guarded the wrong thing: it refused the parameter while
the *situation* - any layered landscape - had the identical fault whether or not a layer was named.

**And it had been hiding a whole suite.** `test_landscape_heightmap` asserted a round-trip against
the project's ambient landscape, which has two edit layers, so T8001/T8002 were verifying writes
that evaporated a second later. They passed because they re-exported **immediately**, inside the
window before the composite runs. Nothing inside the suite could have detected it. It now builds
its own fixture with `create_landscape`, which leaves edit layers off deliberately - which also
drops its dependency on this project's terrain, a separately-filed item.

Repairing it surfaced two more fixture bugs of the same family, both invisible while there was only
one landscape in the level: it read `landscapes[0]` rather than the landscape under test, and its
collision probes were hardcoded world coordinates that only land on terrain centred near the origin
at roughly this project's size. The probes also have to sit **on vertices** - a trace returns the
interpolated surface while the heightmap sample is a vertex, so an off-vertex probe compares two
different things and is wrong by however much the terrain slopes across one quad. That read as a
533uu collision failure and was arithmetic.


## A scanner reported "no findings" because it could not see the shape of the bug

**Date** 2026-08-31

**Symptom.** `audit_advice_gaps.py` - written that same day to find messages advising an
operation that does not exist - printed *"no advice naming an unknown operation - every 'use X'
/ 'X first' in a message names something this bridge can actually do."*

Minutes later, grepping the source for an unrelated reason, three live mentions of
`list_endpoints` turned up in `MifBridgeSetupView.cpp`. `list_endpoints` is not an endpoint. The
bridge itself refuses it: *"'list_endpoints' is not an endpoint on this build (445 are
registered)."* The scanner built to catch exactly this had just declared the file clean.

**Root cause.** The matcher required a verb adjacent to the name - `use X`, `call X`, `X first`.
Both live sites were **menus**, which have no verb:

    "  list_endpoints             - the current endpoint list\n"    <- a help block for agents
    LOCTEXT("T2T", "list_endpoints")                                <- a UI card title

A menu is a worse place for a stale name than prose, not a better one. Prose is one person's
suggestion; a list reads as authoritative inventory. The card in question said in its own body
*"Every endpoint this build actually registers. If it is not in here, it does not exist,
whatever the agent believes."* The card promising to be the source of truth was itself naming a
tool that did not exist.

**Fix.** Two arms added. `MENU` matches an indented snake_case name followed by a spaced dash,
anywhere in the bridge's `.cpp` messages. `BARE` matches a literal that is nothing but an
endpoint-shaped name, **only on lines containing LOCTEXT**. Both sites in `MifBridgeSetupView`
were corrected to `self_audit`, which is what actually lists endpoints (`{summaryOnly:true}` for
the counts alone).

**The tuning is the interesting part, and it happened in two wrong steps first.** Turned on
everywhere, the new pattern produced **128 findings against the old scanner's 0**, every one a
Blender addon docstring: `merge_threshold - distance below which ...` is a menu of a function's
*parameters*, which is what a docstring is for. Restricted to `.cpp` it still gave **37**, all
noise of a different kind - blocklist arrays, `StartsWith` prefixes, enum-parsing comparisons,
comments. C++ is full of bare snake_case literals that assert nothing. Only the LOCTEXT
restriction made the bare-name arm usable, because LOCTEXT is text shown to a person by
definition. Final state: **1 finding on the buggy source, naming all three sites, and 0 after
the fix.**

A scanner with 128 false positives fails the same way as one reporting 0 - nobody reads it. The
first is just louder about it.

**Prevention - the rule this earns.**

> A checker that reports "clean" has proved nothing until it has been run against a known
> instance of what it looks for.

Testing it against the *fixed* tree would have shown clean either way; the pass would have been
vacuous in the meta-layer, which is the same fault the suites were being corrected for all week.
The check that counts is the before/after: restore the buggy file, confirm the scanner names it,
restore the fix, confirm it goes quiet. That is two commands and it is the difference between a
tool and a comfort blanket.

It also generalises past this scanner. The endpoint refusal *"Pass editLayer naming one that
exists"* has the same defect one level down - advice naming a **parameter value** that no
endpoint can enumerate. Filed separately.


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

## A compile probe for one engine overwrote the other engine's shipping DLL (2026-08-26)

**Symptom.** After building the plugin against UE 5.7 in a throwaway probe project, the SDK's own
`Binaries/Win64/UnrealEditor-MifBridge.dll` had changed: 3,938,304 bytes at 22:18 (the 5.3 build)
became 3,745,792 bytes at 22:41 (the 5.7 build). A 5.7-compiled editor module cannot load in UE 5.3,
so the next start of the SDK editor would have failed to load MifBridge entirely. 62 object files in
the canonical `Intermediate/` were 5.7 output as well.

Caught by inspection, not by a failure - the SDK editor was closed at the time. Had it been open, the
DLL would have been locked, the probe build would have failed on a file-write error, and the cause
would have been obvious. Being closed is what let it happen silently.

**Root cause.** The probe generator junctioned the **plugin root** into the probe project:

```
probe57/Plugins/MifBridge  ->  D:/DDS2SDK/Game/Plugins/MifBridge
```

The junction was deliberate and the reasoning behind it was sound: a *copy* drifts the moment a fix
lands, so the probe would stop measuring the source of truth. What that reasoning missed is that the
plugin folder is not only an input. UnrealBuildTool writes a plugin's compiled output to
`<Project>/Plugins/<Name>/Binaries` and its object files to `.../Intermediate`. Through a root
junction, both of those resolve back into the canonical folder. The probe was reading canonical
source, which was intended, and writing canonical binaries, which was not.

**Nothing warned, and nothing could have.** The build printed `Result: Succeeded` because from its
own point of view it had succeeded - it was asked to build a plugin and it built one. There is no
layer that knows a directory is shared with a different engine's install.

**Fix.** Junction `Source/` only, and let the probe own its own `Binaries/` and `Intermediate/`:

```
probe57/Plugins/MifBridge/Source  ->  D:/DDS2SDK/Game/Plugins/MifBridge/Source
probe57/Plugins/MifBridge/MifBridge.uplugin   (copied - one small file, never written during a build)
```

This keeps the property that made a junction right in the first place - live source, no copy to drift
- while output lands in the disposable directory where it belongs.

**Prevention, and the general rule.**

1. **A junction is bidirectional. Link the narrowest thing that satisfies the requirement.** The
   requirement was "compile the real source", which needed `Source/`. Linking the parent granted
   write access to everything beside it. This is the same instinct as not granting a process more
   filesystem than it needs, and it is worth applying to every `mklink /J` in this repo.
2. **Ask what a build WRITES, not only what it reads.** The whole design conversation was about input
   fidelity - copy versus link, drift versus freshness. Output paths never came up, and output was
   the half that did damage.
3. **When two engines share a machine, treat every shared path as a hazard.** `docs/02_GOTCHAS.md`
   section 14 already covers sharing SOURCE between engines at length. This is the same problem one
   layer down, in build artifacts, and it is less visible because the paths are generated rather than
   written by hand.
4. **A build reporting success is not evidence that it built the right thing in the right place.**
   The existing rule in this log - never trust `Build.bat`'s exit code, check the DLL's mtime moved -
   would have caught this immediately if it had been applied to the DLL that was NOT supposed to
   move. Checking that the expected binary changed is half the check; the other half is that nothing
   else did.

**Related, found in the same session:** `Build.bat` returned **exit code 0** on a build that printed
`Result: Failed (OtherCompilationError)`. The 2026-08-15 entry in this log covers exit code 0 on a
build that did nothing; this is worse, because the build ran, failed, said so in its own output, and
still exited 0. Grep the log for `Result: Failed` and for `error`. Never branch on `$?`.


## The autopilot hook spent ~500 tokens a turn restating rules nobody needed twice (2026-08-27)

Not a crash - a cost, and one that compounds silently, which is why it survived 85 turns.

Andre: *"whatever can reduce token usage in our work please do so"*. Looking for the biggest lever,
it was not the thing that looked expensive.

### What I expected to find, and what was actually there

The obvious suspects were the workflows - two of them spent 1.2M and 1.7M subagent tokens. Large, but
**bounded and deliberate**: they ran once each and produced findings worth having.

The real cost was `~/.claude/hooks/autopilot-continue.js`. Its Stop-hook `reason` block ran to about
1,900 characters and was emitted **on every single turn**. Roughly 500 tokens, times every turn of an
indefinite autonomous run.

And the majority of it never changed. The counts moved; the rules block was byte-identical every
time - the same six bullets about `- [x]` versus `- [~]`, the same judging rule, the same reminder
about `self_audit`. Rules that are also in the spec file the hook names in its own text.

### The fix

Emit the standing rules **rarely** (every 20th continue) and the changing part **always**.

```
before: 1,900 chars  (~500 tokens)  every turn
after:    241 chars  (~60 tokens)   every turn, plus the full rules once per 20
```

### The general shape, which is what makes it worth a postmortem

**A per-turn cost is invisible in any single turn.** Nothing about turn 40 looks wasteful; the waste
is that turn 40 said the same thing as turns 1 through 39. Cost that recurs needs to be measured
across the run, not inspected at a point - and the instinct to look at the biggest *single* consumer
finds the wrong thing.

Same reasoning as the report watcher earlier the same night: polling is cheap per check and ruinous
per day, so the polling was moved off the model entirely. Both are the same question - *what am I
paying repeatedly for an answer I already have?*

### The other levers, in the order they are worth taking

1. **Do not poll for something that notifies you.** Background tasks report completion. Repeatedly
   grepping a log to watch progress is a tool call and a result for information that is about to
   arrive for free.
2. **Batch tool calls.** Independent commands in one call cost one round trip instead of five.
3. **Stack builds.** Andre raised the cap to 20 unbuilt changes. Building six at once costs one
   build; the constraint on stacking is *risk*, not tokens, and Live Coding already forces it.
4. **Spend workflow agents on questions worth 1M tokens.** They are the largest single line item and
   they earned it twice tonight - but a question answerable by one grep should get one grep.


## A READ endpoint killed the editor, because an editor-only getter is not a getter (2026-08-27)

**Symptom.** `analyze_skeletal_split` - a pure read, no writes, no transaction - took the editor down
on the first cooked mesh it touched. The HTTP call came back
`ConnectionResetError: [WinError 10054]`, which is what a dead process looks like from the client.

**Diagnosed in one command**, which is the part worth noting:

```
python tools/mifwatch.py
  >>> LAST CALL, NEVER RETURNED: analyze_skeletal_split
```

The crash journal named the endpoint that was in flight. PM-013 had to reconstruct the same kind of
answer by hand from log timestamps.

**Root cause.** The offending line looked completely safe:

```cpp
const FSkeletalMeshModel* Imported = Mesh->GetImportedModel();   // returns null when absent, surely
```

It is not a plain accessor. `USkeletalMesh::GetImportedModel()` calls
`WaitUntilAsyncPropertyReleased(ESkeletalMeshAsyncProperties::ImportedModel)` before returning. On a
**cooked** asset that property was stripped at cook time, so the engine is asked to wait for
something that will never arrive - and takes the process with it rather than returning null.

I had already read the declaration and seen the `WaitUntilAsyncPropertyReleased` call in it. I read it
as a threading detail rather than as the thing that would kill the editor.

**Fix.** Test the package flag **before** touching the accessor:

```cpp
const bool bCooked = Pkg && Pkg->HasAnyPackageFlags(PKG_Cooked);
if (bCooked) { /* answer from the flag - GetImportedModel is never called */ }
```

A cooked mesh gets the same answer, arrived at without the call that crashes.

### The general rule, and it is broader than this function

On a cooked asset, **"does this editor-only accessor return null?" is the wrong question**, because
the accessor may not survive being asked. The right question is **"is this cooked?"**, asked first.

`docs/02_GOTCHAS.md` section 6c says cooked assets keep runtime data and lose editor data. This adds
the sharp edge: some of the getters for that lost data **assert or hang rather than returning null**.
Null-checking the result assumes you get a result.

**And it was a READ.** Every safety habit on this project is built around mutations - transactions,
confirm gates, the write-mode gate, scratch paths. None of them apply to a read, and none of them
would have helped. A read that only ever observes can still terminate the process, and the only
protection is knowing which observations are safe to make.

**What it bought.** The measurement the crash was for: all 30 DDS2 skeletal meshes sampled are cooked
and **none** has an imported model. A mesh splitter cannot build new mesh assets from DDS2 content at
all - there is nothing to build from. That is a real answer to a real question, and worth the
restart.

## The compile probe cost a full engine rebuild, on the engine it was safest to skip (2026-08-27)

**Symptom.** A three-file plugin change built against 5.7 in 4.7 seconds. The same change against
5.3 started rebuilding the whole editor:

```
------ Building 2849 action(s) started ------
[423/2849] Compile [x64] Module.Renderer.26.cpp
```

Renderer, Sequencer, AnimGraph - engine modules that no MifBridge change can possibly affect.

**Root cause, stated by UBT itself** four lines above the action count, which is why this was found
in one command rather than by guessing:

```
Invalidating makefile for DrugDealerSimulator2Editor (SharedPCH.Core.Cpp20.cpp modified)
```

Minutes earlier I had generated a compile probe against `D:/UE532` and tried to build it. The probe
deliberately asks for the strictest settings the engine offers:

| | build settings | include order | C++ |
|---|---|---|---|
| probe (`make_engine_probe.py`) | `BuildSettingsVersion.Latest` | `Latest` | C++20 |
| real project (`DrugDealerSimulator2Editor.Target.cs`) | `V2` | `Unreal5_0` | C++17 |

`D:/UE532` is a **source** engine, so its engine modules compile into intermediates SHARED by every
target built against it. Two different settings sets cannot share one PCH, so asking for C++20 wrote
`SharedPCH.Core.Cpp20.cpp` over the C++17 one the real project was using, and the next real build had
to redo the engine. It thrashes both ways: the next probe would invalidate it straight back.

The probe never even reached the link step - it was refused by Live Coding - and still cost the full
rebuild. **Generating and starting the probe was enough.**

**The 5.7 probe is genuinely free**, which is what made this easy to miss. An INSTALLED engine ships
its modules prebuilt and never recompiles them, so no shared intermediate exists to thrash. The same
command is free on one engine and expensive on the other, and nothing about the command says so.

**Fix.** `refuse_source_engine()` in `tools/make_engine_probe.py`, called BEFORE anything is
generated - a refusal that has already written files is not a refusal. It exits 1 with the reason and
the measured cost. `--force` warns and continues, for whoever genuinely wants it.

The test is `Engine/Build/InstalledBuild.txt` rather than a hardcoded `D:/UE532`, because this plugin
is built against engines on machines this repo has never seen. Installed engines drop that file;
source engines do not.

**Prevention, and the part worth keeping.** There was never a reason to probe 5.3 at all. An engine
is a source build BECAUSE a real project builds against it - and that build is the *better* compile
check: same compiler, same settings, and it produces the binary the editor actually loads. The probe
exists for an engine with no project to build, which on this machine means 5.7 and only 5.7.

So the rule is not "be careful with the probe". It is:

> **Probe an engine you have no project for. Build the project for the engine you have one for.**

PM-014 was the same script writing 5.7 binaries over the 5.3 DLL, fixed by junctioning `Source/`
alone. That fix was correct and is still in place - this is a second, independent way the same tool
reaches across engines, through the shared intermediates rather than through the output directory.
Narrowing what a tool LINKS did not narrow what it INVALIDATES.

## Three assertions that could not fail, and the tool that now finds them (2026-08-27)

**Symptom.** Three times in one day a test was written, run, reported `PASS`, and proved nothing.

| | what it asserted | what was actually true |
|---|---|---|
| `test_unchecked_returns` T722 | struct members keyed on `name` | the field is the mangled `name_index_guid`, so the lookup found nothing, compared `None` against `""`, and passed |
| `test_cooked_class_trap` T755 | `all("cooked" in b for b in rows)` | the field was PRESENT on every row and WRONG on 301 of 1475 |
| `mifwatch` regression | called `analyse()` and printed the length | the bug only appeared on **serialisation** |

Two were caught only because an assertion *beside* them failed loudly. The third was caught by
running the tool a second way.

**Root cause, and the mechanically detectable half.** `all([])` is `True`. An assertion of the shape
`all(<predicate> for x in <collection>)` passes when the collection is empty — which is very often
the exact failure the assertion was written to catch. A call that returns *nothing* sails through
every check written to inspect its results.

The other two shapes are the same disease: a lookup that silently misses, and a presence test
standing in for a correctness test.

**Fix.** Guards on the three genuinely unguarded sites, and `tools/audit_vacuous_checks.py` to stop
it recurring.

**The numbers matter, because a noisy audit gets ignored.**

```
60  raw all(...) assertions across the suites
43  had a non-empty guard right beside them
11  candidates after excluding literal tuples, which can never be empty
 3  genuinely unguarded
```

Roughly one real finding in four candidates. So the tool **reports and never edits**, and carries a
baseline — `audit_vacuous_baseline.txt` — so only a NEW one surfaces. The seven accepted entries were
each read first; most are filtered subsets that may legitimately be empty ("every 16-byte parameter
reports 4 floats" is fine on an asset with no 16-byte parameters).

**Prevention, and the honest limit.** This finds one shape of the problem. It cannot find a lookup
keyed on the wrong field, or a presence test that should have been a value test — T755 passed this
audit while being wrong, because `all("cooked" in b ...)` *is* guarded. The rule the tool cannot
enforce:

> Assert the VALUE, not the presence. `"x" in row` is satisfied by a row that carries `x` and lies
> about it.

Proved it fires before trusting it: a synthetic unguarded `all()` was injected, reported, and
reverted. A check that cannot fail is not a check — including this one.

### The limit lasted about an hour

The presence-vs-value half turned out to be detectable after all, in one narrow shape:
`all("field" in row for row in rows)` — a key asserted present on *every* row and never checked for
what it holds. That is precisely how the 301 mislabelled rows passed.

Narrow is the whole trick. The broad reading — any condition that is only a membership test — matches
**202 of 1795** checks here, and nearly all of them are substring assertions on error text
(`"BlockAll" in error`), which *are* value assertions and exactly right. Restricting it to presence
across a **collection** cuts 202 to **8**, of which **3** were worth strengthening:

| | was | now also asserts |
|---|---|---|
| `list_bones` T221 | `refPose` is present | it carries location/rotation/scale as x/y/z, and they are not all identity |
| `list_material_parameters` T121 | `value` key exists | at least one is non-null — a key emitted empty on every row satisfied the old one |
| `selfpin` T21 | `sourcesAfter` present | it is non-empty on rows that reported `replacedExisting` |

**And the tool was noisy before it was useful.** One injected assertion reported *four* times: the
span gatherer ran on paren depth alone, so it swallowed the following `check(` and matched it twice
per rule. Four findings for one problem is exactly the noise that gets a tool ignored. It now stops
at the next `check(`, and one bad assertion reports once per rule it actually breaks.

## `chainCount:0` on every IK Retargeter endpoint, on every UE 5.6+ engine, silently (2026-08-28)

**Symptom.** `set_retarget_rigs`, `auto_map_retarget_chains`, `set_retarget_chain_mapping` and
`list_retarget_chain_mapping` all reported `chainCount:0` on a UE 5.7 probe - not an error, `ok:true`,
just an empty mapping array where two real chains should have been. Compiled clean. Ran clean. The
kind of wrong that never trips a build.

**Root cause, and it took three tries to find all of it,** because the engine's own header comments
actively pointed the wrong way twice.

*First bug.* `UIKRetargeterController::SetIKRig()`'s reinit loop only fires when
`SourceOrTarget == Source` ("we do NOT auto-update the target IK rig as this may be overridden" -
its own comment), and even then resolves the target through `GetTargetIKRigForOp()`, which only ever
returns a per-op CUSTOM override and never falls back to the retargeter's global target. A
default-created retargeter's ops never get a working chain mapping through `SetIKRig` alone, on
either side. Fixed by also calling `AssignIKRigToAllOps()` - a separate, public, documented API whose
own comment says exactly what was needed ("Force all ops to use the assigned IK Rig and update their
chain mappings") but which `SetIKRig` never delegates to.

*Second bug, found only by reading the .cpp, not the header:*

```cpp
const FRetargetChainMapping* UIKRetargeterController::GetChainMapping(const FName InOpName) const
{
    for (int32 OpIndex = 0; OpIndex < GetNumRetargetOps(); ++OpIndex)
    {
        FIKRetargetOpBase* Op = GetRetargetOpByIndex(OpIndex);
        if (InOpName != NAME_None && Op->GetName() != InOpName) { continue; }
        return Op->GetChainMapping();   // returns whatever op 0 has, null or not
    }
    return nullptr;
}
```

The header comment says this "returns the first chain mapping it finds" when called with `NAME_None`.
The code does not skip nulls - passing `NAME_None` makes the `continue` condition false on iteration
one, so it unconditionally returns op index 0's mapping. Op 0 in `AddDefaultOps()`'s fixed order is
always "Pelvis Motion", which never owns a chain mapping. So this overload returns null on **every**
normally-configured retargeter, not "the first real one" - reproduced live: a retargeter whose "FK
Chains" op (index 1) held a fully populated, correct `ChainMap` (confirmed by dumping the raw
`RetargetOps` array via `get_property`) still read back `chainCount:0` through this call. Fixed by
walking the ops directly and taking the first non-null mapping - the behaviour the comment describes,
just not what the function does.

*Third bug, found live while verifying the first two:* `FName::ToString()` on `NAME_None` renders the
literal string `"None"`, not empty. `bMapped = !SourceName.IsEmpty()` therefore reported `mapped:true`
for a chain an exact-mode auto-map had genuinely left unmapped. The 5.3 read path carried the identical
bug - just never triggered, because nothing had run `auto_map_retarget_chains` in exact mode against a
genuinely-unmappable chain on 5.3 before. Fixed by checking `IsNone()` before stringifying, on both
branches.

**Fix.** All three, in `MifBridgeIKRig.cpp`: call `AssignIKRigToAllOps()` after `SetIKRig()`; read chain
mappings by walking `GetRetargetOpByIndex()` instead of the ambiguous convenience overload; treat
`NAME_None` as empty before it becomes a JSON string.

**Verified live**, not just compiled: built a real cross-rig pair on the standard UE5 mannequin
skeleton - `LeftArm`/`LeftArm` scores 1.0, `RightLeg`/`LeftArm` (no leg chain existed to compete)
scores 0.5333 and is flagged low-confidence, and exact mode correctly reports the same chain
`mapped:false` once nothing matches.

### The general rule

A UE_DEPRECATED warning names the symptom (this will stop compiling), not necessarily the actual
present-day behaviour. `GetChainMapping(NAME_None)`'s bug had nothing to do with the deprecation that
led to the investigation - it was a pre-existing, undeprecated bug in a "convenience" overload that
just happened to be adjacent to the code this session was migrating. Reading the .cpp body of anything
being touched, not trusting the header's doc comment, is what found it. The header would have shipped
the fix broken.

## `FStaticMeshBatchRelevance::LODIndex` reported garbage on every UE 5.4+ engine (2026-08-28)

**Symptom.** `diagnose_landscape_draws`'s `"lod"` field, on any engine 5.4 or newer including 5.7.
Nothing crashed, nothing refused - the field just held a number that did not mean what the field name
said it meant, on a diagnostic endpoint whose entire purpose is explaining why a landscape does or
does not draw.

**Root cause.** Found systematically, not by suspicion: a full `-Rebuild` of the whole module (not an
incremental build, which only recompiles touched files and had already let two other fixes slip past
unnoticed for a build cycle) surfaced every remaining deprecation warning in one pass. Most of them
were the unremarkable "will be made private, use the getter" shape. This one's wording was different:

```cpp
UE_DEPRECATED(5.4, "Public LODIndex member is deprecated and doesn't contain valid data anymore! "
                    "Use GetLODIndex() function instead.")
int8 LODIndex : 1;
```

"Doesn't contain valid data anymore" is not a forward-compatibility notice, it is a present-tense
statement that the field is already wrong. `GetLODIndex()` reads a different, correctly-packed
member (`UnsignedLODIndex`) the deprecated field no longer tracks. On 5.3 the field is a plain, valid
`int8` with no deprecation at all - so the bug is specific to the exact engine range (5.4, 5.5, 5.6,
5.7) this plugin actually targets on its newer side.

**Fix.** `GetLODIndex()` on 5.4+, the field itself on 5.3 (the only option there, and correct there).

**Verified against real content:** `diagnose_landscape_draws` against 256 real DDS2 landscape
components, post-fix, on the real editor - reports a clean `lod: 0,1,2,3,4,5` sequence per component
(6 static meshes, 6 LODs, correctly ordered by decreasing screen-size threshold), which is exactly
what the six real LODs of a landscape component should look like, and which the deprecated field could
not have produced by construction once it stopped being written to.

### The general rule

Most `UE_DEPRECATED` messages in this codebase's experience are "this will stop compiling on a future
engine" - true, but not urgent, and safe to batch with the other renames in the same sweep. This one
read the same at a glance. The tell is in the adverb: "doesn't work anymore" is a claim about NOW,
not about SOON. Every deprecation message in a sweep like this is worth reading in full, not
pattern-matched against the eleven other ones that turned out to be cosmetic.

## A stale, day-old Blender process made a passing test suite look broken (2026-08-28)

**Symptom.** `test_blender_mesh.py`'s T767 ("a closed mesh has no boundary to skirt, and it says so")
failed during a full regression sweep run to confirm today's UE-side fixes hadn't broken anything else.
`extrude_skirt(boundaryOnly=True)` against what the test believed was a fresh factory cube returned
`ok:true` instead of the expected refusal - meaning the mesh it ran against already had real boundary
edges, i.e. was not the closed cube the test's whole design assumes.

**Root cause.** `run_all_suites.py` globs every `tools/test_*.py` and runs it against whatever answers
on the Unreal bridge port - it has no Blender lifecycle management of any kind (confirmed by reading
`run_blender_suites.py`'s own docstring: "nothing in that runner knows how to start a Blender"). When
the sweep reached `test_blender_mesh.py`, it silently reused whatever was already listening on
Blender's port. `ping`'s response named the PID; `Get-Process -Id <that PID>` named its `StartTime`:
2026-08-27 22:04:01 - over four and a half hours, and one calendar day, before the sweep that hit
T767. `test_blender_mesh.py`'s own docstring says it is "SELF-CONTAINED ON PURPOSE" by exporting the
factory-startup Cube once and reusing that export all suite long - a guarantee that instance had long
since stopped satisfying, from whatever unrelated activity had run against it across the hours in
between.

**Confirmed, not just theorised - the first two attempts at confirming it made the diagnosis take
much longer than it should have.** Two different runner scripts (`run_all_suites.py`, then
`run_blender_suites.py --only 4.4`) each appeared to hang for several minutes producing zero output,
and were killed as apparently stuck. Neither was actually stuck: Python fully buffers stdout when it
is not attached to a terminal, so a script doing real work - including, the second time, genuinely
cold-starting a whole copy of Blender - writes nothing to a redirected log until it exits or the buffer
fills. Killing both processes early lost the confirmation runs entirely and cost real time before the
buffering explanation was recognised. The diagnosis that actually landed came from bypassing both
wrapper scripts: killing the stale instance by hand, launching Blender 4.4 directly with the exact
`--background --factory-startup` invocation `run_blender_suites.py` uses, and running
`test_blender_mesh.py` against it directly. PASS 78, FAIL 0 - every check, including T767.

**Fix.** None needed to MifBridge or to the test. The finding is entirely about which Blender instance
a sweep talks to.

### The general rule, twice

A test-running wrapper that does not manage the lifecycle of what it is testing against will silently
adopt whatever state that thing is already in - and a long-lived headless process is exactly the kind
of state that accumulates invisibly across hours nobody was watching it. `run_all_suites.py` including
`test_blender_*.py` in its glob was itself a latent trap: it runs, it reports real PASS/FAIL numbers,
and nothing about a green or red result distinguishes "tested against what the suite assumes" from
"tested against four hours of somebody else's leftovers." Blender suites need `run_blender_suites.py`
specifically, which owns Blender's whole lifecycle end to end, not an ad-hoc sweep that happens to find
a port already answering.

Second: a background process that produces no output is not evidence it is stuck. Redirected stdout
buffering looks identical to a hang for as long as you are only watching the log file. Checking whether
the process is still alive, and whether the thing it should be doing had visibly happened (here, a
fresh PID with a fresh StartTime once Blender actually launched), is the real signal; an empty log
file on its own is not.

## `endpoints_current.json` was silently stale for two days, and the tool reading it never said so (2026-08-28)

**Symptom.** Andre asked me to check on the standing UE 5.7 probe editor, which had exited on its own
during a long idle stretch. Relaunching it (to answer that question) meant setting up the bridge again,
and while doing unrelated follow-up work I ran `coverage_gaps.py` to see whether two just-added
endpoints (`list_virtual_bones`, `list_morph_targets`) showed up as covered. They did not appear
anywhere in the report at all - not covered, not uncovered, simply absent.

**Root cause.** `coverage_gaps.py` reads its list of "every endpoint that exists" from
`tools/endpoints_current.json` - a plain JSON array, described in its own file header and in
`README.md`/`FEATURE_PARITY_SPEC.md` as "a snapshot of self_audit... regenerated from the live editor".
Nothing in the repository actually did that regeneration. It was a hand-written file dated
2026-08-26, 286 names. By the time this was noticed the real surface (confirmed by both a live
`self_audit` and an independent static `MIF_DECL` count from `MifBridgeHandlers.h`, which agreed
exactly at 334) had grown by 60 added endpoints and lost 12 removed or renamed ones across two days of
real feature work - the IK Rig fixes, deprecation sweep, MVVM, water bodies, data layers, and both of
today's own new skeletal endpoints, none of which the snapshot had ever heard of.

`coverage_gaps.py` had no way to know this. It loaded the JSON file, trusted it completely, and
computed "named in a suite" / "named nowhere" over whatever universe the file happened to contain -
286 stale names instead of 334 real ones - with **no signal anywhere in its output** that the input
itself might be wrong. A tool built specifically to catch silent gaps had become one.

**How much this actually cost, honestly.** The direct fix - once the mismatch was noticed - took
maybe twenty minutes: pull a live `self_audit`, diff it against the old snapshot, regenerate it,
re-run the report. The 30-minute-plus cost is upstream of that: an unrelated port-configuration bug
(below) ate most of the wall-clock time getting a live editor to answer at all, and the deeper cost is
unmeasurable - every coverage judgement anyone made by reading this tool's output across the last two
days, including some inside this very session, was silently computed over the wrong endpoint universe
and nobody could have known.

**A second, independent bug surfaced while chasing this one.** Relaunching the probe editor to get a
live `self_audit` reading, I never set `MIF_BRIDGE_PORT` in its environment - the plugin's own code
(`MifBridge.cpp`) falls back to **8791, the exact same default DDS2's real editor uses**, when that
variable is unset or unusable. I had assumed the probe was still on 8801 from an earlier point in this
session and polled that port for several minutes while the editor sat ready on 8791 the whole time.
Andre caught it by asking a direct question ("make sure ports aren't setup dual") rather than me
noticing it myself. Nothing collided this time only because DDS2's editor happened to be closed at
that moment - if both had been running, one would have silently failed to bind (the plugin's own code
comment names exactly this failure mode: an editor "failed with 'HttpListener unable to bind to
127.0.0.1:8791', and got counted as the cook's").

**Fix.** Both bugs, separately:
  - `coverage_gaps.py` now diffs its snapshot against a static `MIF_DECL` extraction from
    `MifBridgeHandlers.h` on every run and prints a loud, impossible-to-miss warning naming every
    added/removed endpoint on any disagreement - staying editor-free for the check itself, since the
    static extraction needs no running process.
  - `tools/refresh_endpoints_snapshot.py` is new: the regeneration step that never existed. Pulls a
    live `self_audit` (the documented authority - it reports endpoints "actually dispatching", a
    stronger claim than a declaration list alone) and rewrites the snapshot for real, in the same
    format and CRLF convention.
  - The port mistake has no code fix - it is a process-discipline one. Recorded here so the next probe
    launch (mine or anyone's) sets `MIF_BRIDGE_PORT=8801` explicitly rather than assuming a value
    carried over from an earlier point in a session, since Bash tool calls do not share persistent
    shell state and nothing enforces the assumption being true.

### The general rule, twice

A snapshot file with no regeneration mechanism does not become stale gracefully - it becomes wrong
silently, and the tool reading it has no way to distinguish "current" from "two days old and missing
a fifth of the real surface" unless something is built specifically to check. Documentation saying a
file is "regenerated from X" is a claim about intent, not a description of what actually happens; if
nothing in the repository performs that regeneration, the doc comment is describing a process that
does not exist. The fix that actually prevents recurrence is not "regenerate it once and move on" - it
is a live disagreement check that fires on every future run, so the *next* time this drifts, the tool
says so instead of quietly answering wrong.

Second: a configuration value that depends on environment state does not persist across process
launches just because it was true earlier in the same conversation. `MIF_BRIDGE_PORT` was set correctly
for the probe at some earlier point this session; every *later* launch needed it set again, explicitly,
and assuming otherwise cost several minutes of polling the wrong port before a direct question from
Andre - not my own process discipline - caught it.

## `duplicate_asset` on a cooked StaticMesh crashed the editor - same root cause as the Niagara guard, one subsystem over (2026-08-28)

**Symptom.** Mid-way through a coverage batch, a live probe of `duplicate_asset` against a real DDS2
static mesh (`S_Volcano_02`, Brushify content) returned nothing - the HTTP connection was forcibly
closed. `Get-Process` on the editor's PID a moment later found no such process. The whole editor was
gone.

**Root cause.** `D:\DDS2SDK\Game\Saved\Crashes\UECC-Windows-.../DrugDealerSimulator2.log` had the
exact moment:

```
LogStaticMesh: Display: Building static mesh SM_ProbeMesh...
LogWindows: Error: appError called: Assertion failed: Owner->IsMeshDescriptionValid(0)
  [File:D:\UE532\Engine\Source\Runtime\Engine\Private\StaticMesh.cpp] [Line: 3086]
Bad MeshDescription on /Game/_MifReads7/SM_ProbeMesh.SM_ProbeMesh
```

`duplicate_asset`'s handler was already carrying a guard and a five-line comment for exactly this
SHAPE of bug, just for a different asset type: `MifBridgeAssetOps.cpp` refuses duplicating a cooked
`NiagaraSystem`/`NiagaraEmitter` because cook strips editor-only emitter data that the copy's
`PostLoad` then dereferences, crashing inside Niagara's own code. A cooked `StaticMesh` has the
identical structure: cook strips the editable `MeshDescription` bulk data (not needed at runtime,
which reads the baked render/collision buffers instead), and `AssetTools.DuplicateAsset`'s
post-duplicate rebuild step (`UStaticMesh::Build`) unconditionally assumes that data exists. DDS2 is
built from `D:/UE532`, "Brando's cooked-editor fork" per this project's own standing notes - its
content-heavy Brushify meshes are cooked assets, exactly the shape this bug needs. This is a hard
`checkf`-style assertion, not a caught exception, so - same as the Niagara case - it takes the whole
process down rather than returning an error, and there is no MifBridge frame anywhere near the top of
the crash stack.

**Fix.** Extended the SAME guard block `duplicate_asset` already had for Niagara: added a
`bStaticMesh` check alongside `bNiagara`, both gated on `PKG_Cooked`, refusing with a message that
names the real mechanism (`UStaticMesh::Build`, the exact assertion text and line) rather than a
generic failure. Checked by class NAME, matching the existing Niagara guard's own reasoning:
recognising an asset in order to refuse it should not require a hard dependency on that asset type's
whole module, and a string check keeps working in a build where the module is not compiled in at all.

**Verified against real content:** re-ran the EXACT call that crashed the editor
(`duplicate_asset` on `S_Volcano_02`) after a real `Build.bat` on both engines this plugin
targets - DDS2's actual 5.3.2 and the 5.7 probe, both `buildcheck.py`-clean on all three signals. The
call now refuses cleanly with the new message, and `self_audit` answers immediately afterward,
confirming the editor is genuinely still alive rather than merely appearing to respond before a
delayed crash. Also re-verified the pre-existing Niagara refusal still fires (the guard block was
restructured, not just extended, so this was a real regression risk, not a formality) and that an
ordinary, non-cooked scratch Blueprint still duplicates successfully - the widened guard did not
become "refuse everything of a checked class," only cooked instances of one.
New regression suite: `tools/test_duplicate_cooked_guard.py`, both refusals plus the still-works
control case, 11/11 PASS.

### The general rule

A crash-class bug found in one asset type is a reason to search the SAME endpoint for siblings with
the same shape, not just fix the one instance and move on. `duplicate_asset` already had a five-line
comment explaining precisely this failure mode for Niagara; the comment described the mechanism
generally enough ("cook strips editor-only data that the copy's re-initialisation then dereferences")
that a StaticMesh instance of the same bug should have been suspected the day the Niagara guard was
written, not discovered by accident nine days later mid-way through an unrelated coverage sweep. When
a guard's own justification names a GENERAL mechanism rather than something specific to one class,
check whether other classes share that mechanism before considering the guard complete.

## add_simplified_collision crashed the editor on a cooked StaticMesh - a second, genuinely different bug found the same day as duplicate_asset's (2026-08-28)

**Symptom.** Immediately after fixing and shipping the `duplicate_asset` cooked-StaticMesh crash (see
the postmortem above this one), testing `add_simplified_collision{shape:"box"}` against the SAME real
DDS2 mesh (`S_Volcano_02`) also took the editor down. The reasoning that led to testing it live at all
was that `add_simplified_collision`/`remove_collision` operate on `BodySetup`/`AggGeom` - simple
collision primitives - a data path that looked genuinely different from the `MeshDescription` bulk
data that had just crashed `duplicate_asset`. That reasoning was WRONG for `add_simplified_collision`
specifically (right for `remove_collision`, see below).

**Root cause.** The crash dump (`UECC-Windows-.../CrashContext.runtime-xml`) named the exception
directly: `EXCEPTION_ACCESS_VIOLATION reading address 0x0000000000000050`, both stack frames inside
`UnrealEditor-MeshDescription.dll`. Rather than guess from that alone, read the actual engine source
(`D:/UE532/Engine/Source/Editor/UnrealEd/Private/GeomFitUtils.cpp`) and found the exact line:

```cpp
int32 GenerateBoxAsSimpleCollision(UStaticMesh* StaticMesh)
{
    ...
    StaticMesh->GetMeshDescription(0)->ComputeBoundingBox().GetCenterAndExtents(Center, Extents);
```

No null check. On a cooked mesh, `GetMeshDescription(0)` returns `nullptr` (the editor-only geometry
bulk data is stripped, same underlying fact as the `duplicate_asset` incident), and the arrow
dereference is the access violation - reading offset `0x50` is consistent with touching an early
member of a null `FMeshDescription*`. The sphere/capsule generator (`CalcBoundingSphere`) takes an
`FMeshDescription*` parameter directly and dereferences it on its first line too, so every shape this
endpoint can produce shares the identical failure mode, not just the box shape that happened to be
tested first - confirmed afterward by driving all four shape families against the same mesh post-fix
and getting a clean refusal every time, rather than trusting that one function's fix generalised.

**Fix.** Added a direct check in `MifBridgeCollision.cpp`'s `H_add_simplified_collision`:
`if (!Mesh->GetMeshDescription(0))`, refused before any shape generator runs, naming the real
mechanism in the response. Checked against the LITERAL condition about to be dereferenced rather than
inferred from a `PKG_Cooked` package flag (the technique `duplicate_asset`'s guard from earlier the
same day uses) - a deliberately different, more precise technique, chosen because the precise
condition was cheap to check directly here.

`remove_collision` was NOT touched, on purpose, after actually reading its handler rather than
assuming the whole `MifBridgeCollision.cpp` file needed the same treatment: it calls
`BS->RemoveSimpleCollision()` on the existing `AggGeom` primitive array, which needs no mesh geometry
at all. Verified live rather than left as an inference - a real `remove_collision{confirm:true}` call
against the same mesh, immediately after the crash investigation, succeeded and reported
`self_audit` still answering.

**Verified against real content:** re-ran the EXACT call that crashed the editor
(`add_simplified_collision{shape:"box"}` on `S_Volcano_02`) after a real `Build.bat` on both engines
this plugin targets - DDS2's actual 5.3.2 and the 5.7 probe, `buildcheck.py`-clean on all three
signals both times. Confirmed the DLL's build timestamp matched the fresh build before trusting the
retest. All four shape families (box, sphere, capsule, k-DOP) now refuse cleanly with `self_audit`
answering immediately after each one. New regression suite:
`tools/test_simplified_collision_guard.py`, T930-T932, 24/24 PASS - including a real
`remove_collision` removal against real content (consistent with this whole project's "nothing is
ever saved, so it reverts on restart" precedent, and necessary here because there is no
`create_static_mesh` endpoint to build a genuinely disposable mesh instead).

### The general rule

Two crashes in one endpoint FAMILY, on the same mesh, in the same investigation, both caused by a
missing null check on `GetMeshDescription()` in engine code this plugin calls into - but two
DIFFERENT functions, in two different files, requiring two different fixes. The lesson from the first
crash's own postmortem ("check whether other classes share a guard's general mechanism") does not
fully cover this one: the mechanism here was shared between `duplicate_asset` and
`add_simplified_collision`, but they are not siblings of the SAME guard - they needed two separate,
independently-verified fixes in two separate handler files. Superficial reasoning about "this data
path looks different" (BodySetup vs. render data) was wrong for one of the two functions in the same
source file and right for the other - the only way to know which was true for each was to read that
SPECIFIC function's own body, not to reason by analogy from the file or class it lives in.

## The full regression sweep had never once finished, and six suites were wrong about the product (2026-08-30)

**Symptom.** `suite_results.json` showed 204 runs over 102 distinct suites, 0 failures - and there
were 144 suites on disk. Forty-two had never appeared in a sweep at all. Nobody had noticed, because
the summary line reports what ran, not what did not.

**First root cause: the sweep could not finish.** `run_all_suites.py` always stalled on the first
PIE suite. Starting PIE saturates the game thread, the bridge stops answering, and the runner's own
recovery - `M.launch_editor()` after `wait_for_bridge` fails - assumed a failed probe meant a dead
editor. That is true when a suite CRASHES the editor, which is the case it was written for, and
false here: the editor was alive and responsive the whole time. So it launched a SECOND editor, both
raced for port 8791, and the run sat there until it was killed by hand. Two editors on one project
is also a way to lose work, since both hold the same packages.

Because it never finished, the 42 newest suites - the least-tested code in the repo - were also the
least swept. The worst way round.

**Fixes.** `launch_editor` now kills a survivor before relaunching, says so loudly, and REFUSES to
launch if the kill did not take, because making the problem worse is not recovering from it. PIE
suites are skipped by default, named in the output with a line stating they were not verified, and
`--with-pie` runs them attended. The skip list is DERIVED from the sources - a suite that invokes
`start_pie` starts PIE - because a hand-kept list is one forgotten entry from hanging the sweep
again.

**Second root cause, and the more interesting one: the suites were wrong, not the product.** With the
sweep finishing, six suites failed. NOT ONE was a defect in a handler:

| Suite | What was actually wrong |
| --- | --- |
| `test_partition_actors` | asserted `loadedInEditor == list_level_actors`, comparing two different sets |
| `test_source_control` | asserted per-action refusal wording on a project with no revision control provider, where the endpoint correctly refuses earlier |
| `test_create_struct_init`, `test_set_struct_member` | took the first non-scratch asset `find_assets` returned and called it cooked, without testing cookedness |
| `test_move_actors_to_level` | assumed a spawned probe lands in the persistent level; `spawn_actor_in_level` uses the CURRENT one |
| `test_safety_gate` | returned 1 (FAILED) for a deliberate, correct bail-out that meant 2 (SKIPPED) |
| `test_unknown_endpoint` | a real, pre-existing suggestion-ranking weakness - the only product finding |
| `test_blender_creation`, `test_blender_material` | probed with a call that RAISES when Blender is absent, so they reported FAILED where they meant SKIPPED |

**The pattern, which matters more than any of the fixes.** Four of them are one shape: *asserting a
specific outcome without establishing the precondition that makes it the expected one.* Endpoints in
this repo deliberately refuse on the most fundamental failure first, so a test naming a late refusal
has to confirm execution reaches it. `test_partition_actors` assumed a pristine map;
`test_source_control` assumed a provider; the struct suites assumed cookedness;
`test_move_actors_to_level` assumed a current level.

Every fix was the same move: **check the property instead of assuming it, and print the input chosen**
so a future failure reports its own conditions. `test_consolidate` was fixed this way in the morning
and the diagnosis written into its comment - and the same bug was then written three more times the
same day, twice in brand-new suites. Writing the warning down demonstrably does not prevent the
mistake. Only a test does.

**Two process notes worth as much as the fixes.**

*The data was already there.* An hour went into planning tooling to capture which checks failed.
`run_all_suites.py` already records a 25-line tail per failure into `suite_results.json`. One read
gave every failing check name AND the numbers that settled `test_partition_actors` outright -
descriptors constant at 74, `list_level_actors` climbing 80 to 169 as other suites spawned actors.
Read what exists before building.

*"NOT REPEAT-SAFE" is not always contamination.* The runner flags a suite that passes on run 1 and
fails on run 2, and the obvious reading is state surviving between runs. An hour went into hunting
that for the struct suites. There was none: `find_assets` ordering is simply not stable, so the two
runs picked different assets. The flag says the runs differ, not why.

**Prevention.** `tools/audit_undefined_names.py` was added the same day for a related reason - a
`NameError` in `launch_editor`'s recovery path (`PORT` where the module says `BRIDGE_PORT`) killed a
288-run sweep at run 90, and nothing could have caught it: `py_compile` cannot see a runtime name
error, and that code only executes when the bridge is already down. Error branches and recovery
paths are where this class of bug lives, because a green test run never reaches them.

---

## Reading a bpy datablock after the call that freed it - three instances in one night (2026-08-31)

**Symptom.** `boolean_op` passed on Blender 3.6, 4.2 and 4.4 and failed on 5.0.1 with
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xfe in position 0`, raised from inside a plain
`modifier.name`. Hours later `bake_texture` failed its own error path with
`ReferenceError: StructRNA of type Image has been removed`. Neither message names a lifetime
problem, and the first one does not even look like one - it reads as a text-encoding bug.

**Root cause.** Both endpoints kept using a Python handle to a datablock that an operator had already
destroyed.

* `bpy.ops.object.modifier_apply` FREES the modifier. `boolean_op` then read `modifier.name` for its
  post-apply check, `modifier.name` again in the failure cleanup, and `modifier.solver` when building
  the response. Every one of those is a read of released RNA memory - undefined behaviour that
  happened to return the old string on three Blender versions and garbage bytes on the fourth. That
  is also why the FIRST boolean in the suite survived and the second did not: freed-memory reads are
  not deterministic, so the same code passes and fails in the same run.
* `bake_texture` removed the image with `bpy.data.images.remove(image)` on its failure path and then
  read `image.is_dirty` while formatting the error message.

A third instance was latent rather than observed: `decimate_mesh` reads `mod.name` and passes `mod`
to `remove()` inside an `except` that covers `modifier_apply` - so the handle may already be freed
exactly when the handler runs. It has no multi-user guard, and applying a modifier to shared mesh
data is precisely what makes `modifier_apply` raise, so its except path is more reachable than
`boolean_op`'s was.

**Fix.** Take what is needed as PYTHON VALUES before the destroying call, and look the object up
again by name afterwards instead of holding the handle:

```python
mod_name = str(modifier.name)          # before
bpy.ops.object.modifier_apply(modifier=mod_name)
leftover = target.modifiers.get(mod_name)   # after - a fresh lookup, not the old handle
```

`apply_modifier` in `ops_rig.py` had always done this correctly; the other two now match it.

**Prevention.** The rule is not "remember this API frees things" - I knew that, had just fixed it in
`boolean_op`, and wrote it again in `bake_texture` the same night. The rule that actually holds is
mechanical:

> Anything you will need AFTER a destructive call must be copied into a plain Python value BEFORE it.
> That includes values used only to build an error message.

The error-message case is the one that slips through, because it lives on a path nobody exercises in
the happy case. Both instances here were in failure handlers.

Worth knowing for diagnosis: Blender reports this two different ways depending on how far the freed
memory has been reused. `ReferenceError: StructRNA of type X has been removed` is the honest one. A
`UnicodeDecodeError` out of a plain attribute read is the SAME bug wearing a disguise - the bytes
behind a freed `FName`-like field are no longer text. If a version-specific text-decoding error
appears where no text is being decoded, look for a lifetime problem rather than an encoding one.

