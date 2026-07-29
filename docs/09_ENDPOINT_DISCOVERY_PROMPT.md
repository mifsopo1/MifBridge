# Fable session prompt — exhaustive MifBridge endpoint discovery

Copy everything below the line into a fresh Fable session. It is written for a long unattended run
(hours). It deliberately front-loads the invariants so the agent cannot invent APIs, and it demands
engine-source citations for every claim.

---

## MISSION

You are auditing an Unreal Engine 5.3.2 editor plugin called **MifBridge** to find **every endpoint it
could expose but currently does not**. MifBridge is a localhost HTTP bridge that lets an AI agent
drive the Unreal Editor programmatically. It currently has ~156 endpoints. The working hypothesis is
that this covers a small fraction of what the editor can actually do, and your job is to prove or
disprove that with a **ranked, verified, implementable catalogue**.

This is a research and specification task. **Do not write plugin code.** Produce a catalogue that a
subsequent implementation session can execute against without re-deriving anything.

Work for as long as it takes. Depth beats speed. A shallow list of 500 plausible-sounding endpoint
names is worthless; 150 endpoints with verified signatures, module dependencies and failure modes is
the deliverable.

## ENVIRONMENT — verify these before starting, do not assume

| Thing | Path |
|---|---|
| Engine (source build) | `D:/UE532` |
| Engine source root | `D:/UE532/Engine/Source` |
| Project | `D:/DDS2SDK/Game/DrugDealerSimulator2.uproject` |
| Plugin | `D:/DDS2SDK/Game/Plugins/MifBridge` |
| Handler declarations | `Source/MifBridge/Private/MifBridgeHandlers.h` |
| Handler registry | `Source/MifBridge/Private/MifBridgeCommon.cpp` |
| Python MCP layer | `tools/ue5-mcp-bridge/server.py` |
| Existing docs | `docs/00_ARCHITECTURE.md` … `docs/08_LANDSCAPE.md` |

The game is *Drug Dealer Simulator 2*. Much of `/Game/**` is **cooked content mounted from .pak
containers** — assets exist in the registry but their graphs are stripped and their materials carry
only the shader permutations that were cooked. This matters: some editor APIs behave differently or
fail on cooked assets, and an endpoint that only works on loose assets must say so.

## ARCHITECTURE INVARIANTS — read these files first, they are the contract

1. **Three-way 1:1 registry.** Every endpoint appears in exactly three places:
   - `MIF_DECL(name);` in `MifBridgeHandlers.h`
   - `MIF_BIND(name);` in `MifBridgeCommon.cpp`
   - `@mcp.tool() def name(...)` in `server.py`

   Drift between these is a recurring bug class. Any endpoint you propose must specify all three.

2. **Transaction policy has three buckets**, enforced in `MifBridgeCommon.cpp`:
   - `IsReadOnlyEndpoint` — pure queries. No `FScopedTransaction`; otherwise every read pushes an
     empty entry onto the undo stack.
   - `IsSelfManagedEndpoint` — the handler opens its own tight transaction, or none. Required for
     anything that **compiles a Blueprint, swaps the UWorld, or builds and registers new UObjects/
     textures**. A full `FKismetEditorUtilities::CompileBlueprint` inside an outer transaction causes
     reinstancing + Ctrl-Z = dead CDO = editor crash.
   - everything else — wrapped in one blanket transaction by `RunEndpoint`.

   **For every endpoint you propose, state its bucket and justify it.**

3. **Handlers run ON the game thread, SYNCHRONOUSLY and inline, POST-world-tick.** They are *not*
   dispatched via `AsyncTask(ENamedThreads::GameThread, …)` — this brief used to say that and it was
   backwards; the source comment says **"Do NOT reach for AsyncTask"** and explains why
   (`MifBridgeServer.cpp:229-265`). `FHttpServerModule` is an `FTSTickerObjectBase`, so the handler is
   already on the game thread, called from `FTSTicker::GetCoreTicker().Tick()` after
   `GEngine->Tick()` has finished the whole world tick, outside every tick group. Consequences:
   - Blocking in a handler deadlocks the HTTP server — and worse than an async model would: the
     handler is holding the very ticker that would have to advance whatever it is waiting on.
   - Anything asynchronous (PIE start, navmesh build, shader compile, asset cook) must **request and
     return**, with a separate status endpoint to poll. Never wait.
   - Anything that tears down or swaps the `UWorld` must be **deferred a tick**
     (`GEditor->GetTimerManager()->SetTimerForNextTick`). Doing it inline trips
     `Assertion failed: !LevelList.Contains(TickTaskLevel)` (`TickTaskManager.cpp:1458`) and kills the
     editor. This was learned the hard way — see `docs/01_POSTMORTEMS.md`.

4. **Silent parameter ignore is the most damaging bug class in this codebase.** An endpoint that
   accepts a parameter and quietly drops it returns `ok:true` and sends the caller to debug the wrong
   subsystem. Multiple real examples are in `docs/01_POSTMORTEMS.md` (`trace_ground` read top-level
   `x`/`y` while callers passed `location:{}`, so every trace silently ran at the world origin).
   **Every endpoint you specify must list its accepted parameter spellings and state what happens on
   an unrecognised one.**

## STEP 0 — establish the baseline

1. Read `docs/00_ARCHITECTURE.md`, `01_POSTMORTEMS.md`, `02_GOTCHAS.md`, `06_CAPABILITY_ROADMAP.md`,
   `08_LANDSCAPE.md`. These encode failures already paid for. Do not propose anything that
   re-introduces a documented trap.
2. Get the **live** endpoint list rather than trusting any doc:
   ```
   curl -s -X POST http://127.0.0.1:8791/api/self_audit \
        -H "X-Mif-Token: dev" -H "Content-Type: application/json" -d '{}'
   ```
   (Requires the editor running. If it is not, parse `MIF_DECL(` from `MifBridgeHandlers.h`.)
3. Produce `docs/audit/00_BASELINE.md`: every existing endpoint, its file, its bucket, one line on
   what it does. This is your "already covered" set — you will diff every proposal against it.

## STEP 1 — systematic surface sweep

Do **not** brainstorm endpoint names. Enumerate the engine's actual API surface and derive endpoints
from what is genuinely callable. Work through every axis below. For each, search
`D:/UE532/Engine/Source` for the exported, editor-callable entry points.

Treat each axis as its own deep dive. Record findings as you go — do not hold them in context.

### A. Editor core
- `UEditorEngine` / `GEditor` public API (`Editor/UnrealEd/Classes/Editor/EditorEngine.h`) — every
  `UNREALED_API` method.
- Every `UEditorSubsystem` subclass in the engine and in enabled plugins.
- `FEditorFileUtils`, `UEditorLoadingAndSavingUtils`, `FEditorDelegates`.
- Viewport and camera control (`FLevelEditorViewportClient`, `FEditorViewportClient`) — **known gap:
  there is currently no way to move the editor camera or frame actors.**
- Selection (`USelection`, `GEditor->SelectActor`), outliner folders, actor grouping.
- Transaction/undo introspection (`GEditor->Trans`, `UTransBuffer`) — list, describe, undo, redo.
- Editor preferences, `UDeveloperSettings` subclasses, console variables (`IConsoleManager`).

### B. Assets and the registry
- `IAssetRegistry` full surface: dependency and referencer graphs, tags, filters.
- `IAssetTools`: create/duplicate/rename/delete/import/export/consolidate/migrate.
- Every `UFactory` subclass — each is potentially a "create asset of type X" endpoint. Enumerate them.
- Asset validation (`UEditorValidatorBase`, `EditorValidatorSubsystem`), `MapCheck`.
- Redirectors and fixup, source control (`ISourceControlProvider`).
- Package/bulk operations, `UPackage::Save`, cook-on-the-fly, `IAssetCompilingManager`.

### C. Blueprints and graphs
- Every `UK2Node_*` subclass in `BlueprintGraph` and beyond — which are already spawnable via
  MifBridge, and which are not? This alone may be dozens of endpoints.
- `FBlueprintEditorUtils` full surface: interfaces, macros, delegates, timelines, components,
  child actor templates, SCS node manipulation.
- Blueprint diffing, compilation results introspection, bytecode/disassembly.
- Function libraries, macro libraries, interfaces, enums, structs.
- Animation Blueprints specifically: state machines, transitions, blend nodes, anim graph.
- **Cooked Blueprint handling** — what can be read/reconstructed from a `BlueprintGeneratedClass`
  when graphs are stripped? See the existing reconstructor work.

### D. Materials and rendering
- `UMaterialEditingLibrary` — full surface. Material graph authoring is currently a hard gap:
  MifBridge can create material *instances* but not materials or expressions.
- `UMaterialExpression` subclasses — every node type.
- Material functions, parameter collections, layers/blends.
- Runtime virtual textures, virtual textures, texture build/compression settings.
- Shader compilation status and diagnostics (see the existing `diagnose_landscape_draws`).
- Lumen/Nanite settings, LOD generation, HLOD, lightmass/lighting build.
- Post-process, scene capture, render targets, `UKismetRenderingLibrary`.

### E. Geometry and meshes
- **`GeometryScript` (`GeometryScriptingCore` / `GeometryScriptingEditor`)** — a very large API for
  procedural mesh authoring: booleans, extrudes, remesh, simplify, UV ops, mesh from spline, voxel
  ops. Treat this as a major axis; it may be the single biggest untapped surface.
- Static mesh editing: LODs, collision (simple/complex/convex decomposition), sockets, Nanite,
  lightmap UVs, mesh merging, `UStaticMeshEditorSubsystem`.
- Skeletal mesh: sockets, physics assets, morph targets, LODs, skin weights.
- Modeling Tools Editor Mode, `UGeometryScriptLibrary_*`.
- Procedural Content Generation (PCG) if present in 5.3 — graphs, nodes, execution.

### F. World and level
- World Partition: data layers, level instances, packed level actors, HLOD layers, streaming.
- Sublevels, streaming volumes, world composition.
- Landscape beyond what exists: **landscape splines** (roads/rivers that deform terrain), grass types,
  edit layers, layer blending, heightmap import/export, `ALandscapeStreamingProxy`.
- Foliage: `UFoliageType`, procedural foliage volumes/spawners, instanced foliage actor manipulation.
- Water/Landmass plugins if enabled.
- Environment: lighting scenarios, reflection captures, lightmass importance volumes.

### G. Gameplay systems
- AI: behaviour trees, blackboards, EQS, `UAIPerceptionSystem`, nav modifiers, nav links, nav areas.
  **Known gap: nothing currently makes NPCs walk a route.** Investigate what actually drives pawn
  movement in this project and what an endpoint would need to expose.
- Sequencer / `ULevelSequence` — tracks, keys, bindings, playback, rendering. Large surface.
- Gameplay Ability System if present.
- Input: enhanced input actions, mapping contexts.
- Physics: constraints, physical materials, simulation, Chaos destruction/geometry collections.
- Audio: sound cues, MetaSounds, attenuation, submixes, audio capture.
- Niagara: systems, emitters, parameters, spawning — VFX is currently untouched.
- UMG: widget hierarchy, animations, bindings, slots, designer layout.

### H. Data
- DataTables, CurveTables, CompositeDataTables, `UDataAsset` / `UPrimaryDataAsset`.
- Struct and enum authoring beyond what exists.
- Localization: string tables, cultures, gathering.
- Config/ini manipulation, `UDeveloperSettings`, asset manager settings.
- Save games, `USaveGame` inspection.

### I. Diagnostics and observation
- Profiling: `stat` commands, Unreal Insights traces, memory reports, `UEngine::Exec`.
- Log capture and filtering, message log categories.
- Screenshot and high-res shot, scene capture beyond current `capture_camera`.
- Runtime object inspection during PIE, live property watch.
- Automation/functional test framework, `FAutomationTestFramework`, Gauntlet.
- Determinism: what state must be captured for a scene to be reproducible?

### J. Project specific (DDS2)
- The mod loader, `.pak` mounting, cooked-content quirks.
- The game's own systems reachable from the editor: population, dialogue, shops, quests, economy.
- `/Game/Blueprints/Enviro/**`, `/Game/Blueprints/NPC/**` — what is scriptable?

## STEP 2 — verification rules (non-negotiable)

For **every** proposed endpoint you must record:

1. **The exact engine API it would call**, with `file:line` from `D:/UE532/Engine/Source`, and the
   full signature copied verbatim. If you cannot cite it, it does not go in the catalogue.
2. **Export check.** Is the symbol actually exported (`ENGINE_API`, `UNREALED_API`, `LANDSCAPE_API`…)?
   A non-exported method cannot be called from another module. This has already bitten this project:
   `ULandscapeComponent::UpdateCollisionData` is declared without `LANDSCAPE_API` and failed to link;
   `ALandscapeProxy::RecreateCollisionComponents()` was the exported equivalent.
3. **Module dependency** it would add to `MifBridge.Build.cs`, and whether that module is
   editor-only, runtime, or a plugin that may not be enabled.
4. **`WITH_EDITOR` guards** required.
5. **Transaction bucket** (read-only / self-managed / transacted) with justification.
6. **Async?** If the operation completes over multiple frames, specify the request + poll pair.
7. **Parameter spec**: names, accepted alternate spellings, types, defaults, and what happens on an
   unrecognised parameter (must be an error, never silence).
8. **Failure modes**: what goes wrong, and what the error message should say. Prefer messages that
   name the parameter and the fix.
9. **Cooked-content behaviour**: does it work on assets mounted from `.pak`? Many will not.
10. **Verification method**: how would an implementer prove it works, using *numbers* not screenshots
    where possible. (House rule from `docs/02_GOTCHAS.md`: "numbers for correctness, pixels for
    taste".)

**Anti-invention rule.** If you are unsure an API exists, search for it. If you still cannot confirm
it, put it in a separate `UNVERIFIED` section — never in the main catalogue. A confidently wrong
signature costs an implementer more than a missing entry.

## STEP 3 — prioritisation

Score every endpoint on three axes (1–5) and compute a rank:

- **Unblocks** — how much currently-impossible work does it enable? (An endpoint that unlocks a whole
  category, like material graph authoring, scores 5.)
- **Effort** — implementation cost, inverted (trivial = 5).
- **Risk** — crash/corruption potential, inverted (safe read = 5).

Group results into:
- **Tier 0 — closes a known gap.** Things this project has already hit and worked around: editor
  camera control, walking NPC routing, material graph authoring, level-material assignment
  validation, PIE status reliability, per-actor cull/LOD overrides.
- **Tier 1 — high leverage, low risk.**
- **Tier 2 — valuable, needs design.**
- **Tier 3 — exotic / speculative.**

## STEP 4 — deliverables

Write these files into `docs/audit/`:

1. `00_BASELINE.md` — current ~156 endpoints, bucketed.
2. `01_CATALOGUE.md` — the full proposed set, one section per axis (A–J), each entry carrying all
   ten verification fields from Step 2. This is the main artefact.
3. `02_RANKED.md` — the same endpoints sorted by score, with Tier 0–3 grouping and a suggested
   implementation order in batches that share module dependencies (so each build cycle adds the
   fewest new modules).
4. `03_GAPS_AND_RISKS.md` — engine APIs that look attractive but are **not viable**, and why:
   non-exported symbols, editor-only-in-a-way-that-breaks, cooked-content limitations, known engine
   bugs in 5.3.2. Negative results are as valuable as positive ones and stop the next session
   re-treading them.
5. `04_OPEN_QUESTIONS.md` — anything needing a human decision or a live editor experiment.

Keep a running progress log so an interrupted run can resume: after each axis, append what you
covered and what remains.

## SCOPE AND STYLE

- **Breadth first, then depth.** Complete a shallow pass over all ten axes before deep-diving, so an
  interrupted run still has full coverage.
- Prefer **many small composable endpoints** over few god-endpoints, matching the existing style.
- Every endpoint must be **useful to an agent that cannot see the screen**. Favour ones that return
  structured, checkable data. When proposing a mutation, propose its verification query alongside.
- Do not propose endpoints that merely wrap `run_console`. Console commands are already reachable.
- Where an endpoint would have prevented a specific documented failure, say so explicitly — that is
  the strongest possible justification.

## SUCCESS CRITERIA

The run succeeded if a subsequent implementation session can pick any Tier 0 or Tier 1 entry and
implement it **without opening the engine source to look anything up**, because the signature, module,
guards, bucket, parameters and failure modes are all already written down and cited.
