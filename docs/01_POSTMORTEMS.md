# MifBridge — postmortem log

One entry per bug that cost real time. Symptom → root cause → fix → prevention.
Newest first.

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
