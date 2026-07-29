# MifBridge — postmortem log

One entry per bug that cost real time. Symptom → root cause → fix → prevention.
Newest first.

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
