# Handoff prompts — MifKismetReconstructor

Two pieces of work that belong to **MifKismetReconstructor**, not MifBridge. MifBridge edits live
editor graphs; anything that has to recover logic from *cooked bytecode* or synthesise an editable
asset from a native class is reconstructor territory.

Paste one of these into a fresh session opened on
`D:\DDS2SDK\Game\Plugins\MifKismetReconstructor`.

---

## Prompt 1 — Animation Blueprint reconstruction

> **Context.** MifKismetReconstructor decompiles cooked `UBlueprintGeneratedClass` bytecode back into
> editable Blueprint graphs. Its console commands are `mif.kr.Reconstruct`, `mif.kr.DumpBP`,
> `mif.kr.DumpFull`, `mif.kr.Events`, `mif.kr.AnalyzeUbergraph`, `mif.kr.VerifyFidelity`,
> `mif.kr.DriftCensus`, `mif.kr.ClassifyIntentional`, `mif.kr.ListBP`, `mif.kr.FindBP`,
> `mif.kr.LatentResume`. Source layout: `Private/AssetGeneration/` (KismetGraphDecompiler,
> KismetBytecodeTransformer), `Private/Analysis/` (ubergraph analyzer + slicer),
> `Private/Verify/` (fidelity verifier, drift classifier), `Private/Toolkit/` (bytecode
> disassembler → JSON).
>
> **The gap.** Cooked **Animation** Blueprints reconstruct poorly or not at all. A cooked
> `UAnimBlueprintGeneratedClass` is not just a bytecode container like a regular BP: the AnimGraph is
> **not bytecode**. It is compiled into a baked node array plus a state-machine description —
> `UAnimBlueprintGeneratedClass::AnimNodeProperties`, `GetAnimNodeProperties()`,
> `BakedStateMachines` (`FBakedAnimationStateMachine` → `States` / `Transitions`, each carrying an
> index into the node array), `AnimNotifies`, and the `EvaluateGraphExposedInputs` copy records.
> The event graph (`AnimNotify_*`, `BlueprintUpdateAnimation`, …) IS ordinary bytecode and should
> already reconstruct through the existing path.
>
> **What I want.**
> 1. **Investigate first, then report before writing code.** Read
>    `Engine/Classes/Animation/AnimBlueprintGeneratedClass.h` and `AnimStateMachineTypes.h` in
>    `D:\UE532\Engine\Source\Runtime\Engine\` and confirm exactly what survives cooking. State
>    plainly which parts are recoverable and which are lossy — do not promise a full round-trip if
>    the data is not there.
> 2. Extend the dumper (`mif.kr.DumpBP` / a new `mif.kr.DumpAnim`) to emit, as JSON: every anim node
>    with its class and resolved property values, every baked state machine with its states,
>    transition rules and entry state, and the notify track.
> 3. Then, if and only if step 1 shows it is feasible, reconstruct an editable `UAnimBlueprint`:
>    recreate the AnimGraph nodes (`UAnimGraphNode_*` from the `AnimGraph` editor module), rebuild
>    state machines as real `UAnimStateNode` / `UAnimStateTransitionNode` graphs, and re-link pose
>    pins from the baked link indices.
> 4. Wire it into `mif.kr.VerifyFidelity` so anim reconstruction is measured the same way as regular
>    BP reconstruction, and report the fidelity number honestly.
>
> **Constraints.** UE 5.3, engine at `D:\UE532` (source fork — build with
> `D:\UE532\Engine\Build\BatchFiles\Build.bat DrugDealerSimulator2Editor Win64 Development
> -Project="D:\DDS2SDK\Game\DrugDealerSimulator2.uproject" -WaitMutex`, editor closed or Live Coding
> blocks it). Read the real engine source before asserting any API exists — do not guess signatures.
> Test target: `D:\DDS2SDK\Game\Content\CityScooter\Blueprints\ABP_Scooter_01a.uasset` is an
> uncooked ABP usable as a ground-truth reference (author → cook → reconstruct → diff).
>
> Write a postmortem in `docs/` for anything that costs more than 30 minutes to diagnose.

---

## Prompt 2 — C++ parent → editable Blueprint (callable stubs + reparent)

> **Context.** As above. `CreateEditableBlueprintCopy` (in the engine fork's Kismet module, declared
> in `CompiledBlueprintReconstructor.h`) mints an editable child/sibling of a cooked Blueprint;
> MifBridge exposes it as the `create_editable_child` endpoint with
> `variant: child | sibling | uncooked | sibling_full | full`.
>
> **The gap.** When the chain bottoms out at a **native C++ class**, the editable copy gives you
> nothing to work with. C++ function bodies cannot be decompiled into Blueprint nodes — that part is
> genuinely impossible and should not be attempted. But the useful 90% is: the copy should still let
> you *hook into* the native parent.
>
> **What I want — "callable stubs + reparent".**
> 1. The editable copy keeps the **native parent class** as its parent, so all C++ behaviour still
>    runs unchanged at runtime. Do not try to replace it.
> 2. Generate **override-ready event stubs** for every `BlueprintImplementableEvent` /
>    `BlueprintNativeEvent` on the native chain, so they appear in the graph ready to extend
>    (`UK2Node_Event` with `bOverrideFunction`, or the `FBlueprintEditorUtils` override path —
>    check what the "Override Function" dropdown actually does and mirror it).
> 3. Emit a **manifest** of everything callable on the native chain: every `BlueprintCallable`
>    `UFUNCTION` with its full signature (param names, types, const/pure, static), and every
>    `BlueprintVisible` / `BlueprintReadWrite` `FProperty` with its type and flags — walking the
>    whole ancestry, not just the immediate parent. Write it beside the asset as JSON **and** log it.
> 4. For any native function that is **not** Blueprint-exposed, list it separately as
>    "native-only, not callable from Blueprint" rather than omitting it — the absence is information.
>
> **Explicitly out of scope:** translating C++ statement bodies into Blueprint nodes. If asked to,
> say why it cannot work rather than emitting graphs that look right and are wrong.
>
> **Note:** MifBridge's `describe_class` endpoint already dumps callable functions, properties and
> dispatchers for a class — read
> `D:\DDS2SDK\Game\Plugins\MifBridge\Source\MifBridge\Private\MifBridgeIntrospect.cpp`
> (`H_describe_class`) and reuse its shape so the two tools agree, rather than inventing a second
> format.
>
> **Constraints.** Same engine/build/verification rules as Prompt 1.

---

## Why these are not MifBridge's job

| Work | Owner | Why |
|---|---|---|
| Reading/editing live editor graphs | **MifBridge** | It drives `UnrealEd`'s graph API on loaded assets |
| Animation *asset* data (sequences, montages, blend spaces) | **MifBridge** | `describe_animation` / `list_animations` — plain reflection, no decompilation |
| Nested graphs (anim state machines, collapsed nodes) | **MifBridge** | Now handled — `GatherGraphs` recurses `UEdGraphNode::GetSubGraphs()` |
| Cooked bytecode → graphs | **Reconstructor** | Needs a bytecode disassembler and a decompiler |
| Baked AnimGraph → editable AnimGraph | **Reconstructor** | Same, plus anim-specific baked structures |
| Synthesising an editable asset from a native class | **Reconstructor** | Depends on the engine-fork `CreateEditableBlueprintCopy` |
