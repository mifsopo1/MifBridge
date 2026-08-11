# Fable 5 session prompt — full-scope endpoint expansion (MifBridge + MifKismetReconstructor)

Copy everything below the line into a fresh Fable 5 session. Written for an unattended multi-hour run.
It front-loads every invariant so the agent cannot invent APIs, and it demands engine/plugin source
citations for every claim. Unlike `09_ENDPOINT_DISCOVERY_PROMPT.md` (catalogue only), this one
**implements**.

---

# MISSION

Two sibling Unreal Engine 5.3.2 editor plugins live in the same project:

- **MifBridge** — a localhost HTTP bridge that lets an AI agent drive the Unreal Editor. ~160 endpoints.
- **MifKismetReconstructor** — reconstructs editable K2 Blueprint graphs from *cooked* Kismet bytecode.
  It has a rich internal toolkit reachable **only through console commands**, i.e. invisible to any
  agent driving the bridge.

Your job is to **maximise the number of correct, verified, useful endpoints MifBridge exposes**, drawing
from three sources in priority order:

1. **MifKismetReconstructor's existing capability** — already written, already working, currently
   unreachable over HTTP. Highest value per unit of effort in the entire project.
2. **The other 20 plugins** installed in this project, each of which may export usable APIs.
3. **The engine itself** — the vast unexplored surface.

Work as long as it takes. Depth beats breadth-with-no-verification. **500 plausible endpoint names are
worthless; 100 endpoints that compile, are registered correctly, and are proven to work is the goal.**

# ENVIRONMENT — verify, never assume

| Thing | Path |
|---|---|
| Engine (source build) | `D:/UE532` |
| Engine source | `D:/UE532/Engine/Source` |
| Project | `D:/DDS2SDK/Game/DrugDealerSimulator2.uproject` |
| MifBridge | `D:/DDS2SDK/Game/Plugins/MifBridge` |
| MifKismetReconstructor | `D:/DDS2SDK/Game/Plugins/MifKismetReconstructor` |
| All plugins | `D:/DDS2SDK/Game/Plugins/` (22 of them) |
| Bridge endpoint | `http://127.0.0.1:8791/api/<name>`, header `X-Mif-Token: dev` |

The game is *Drug Dealer Simulator 2*. Most of `/Game/**` is **cooked content mounted from .pak
containers**: assets appear in the asset registry but their Blueprint graphs are stripped and their
materials carry only the shader permutations that were cooked. This is *why* MifKismetReconstructor
exists, and it constrains what many endpoints can do. An endpoint that only works on loose assets must
say so in its own error text.

# BUILD / TEST LOOP — you will run this many times, get it right

```bash
# 1. the editor holds the DLL; it must be fully gone (check for MULTIPLE instances)
tasklist | grep -iE "UnrealEditor|LiveCoding"
taskkill //IM UnrealEditor.exe //F ; taskkill //IM LiveCodingConsole.exe //F
# wait until the count is 0, then sleep ~6s more — a closing editor keeps a file handle
# briefly after the process list clears, which produces a spurious LNK1104

# 2. build
cd /d/DDS2SDK/Game
"/d/UE532/Engine/Build/BatchFiles/Build.bat" DrugDealerSimulator2Editor Win64 Development \
  -Project="D:/DDS2SDK/Game/DrugDealerSimulator2.uproject" -WaitMutex -FromMsBuild

# 3. relaunch and wait for the bridge
nohup "/d/UE532/Engine/Binaries/Win64/UnrealEditor.exe" \
  "D:/DDS2SDK/Game/DrugDealerSimulator2.uproject" >/dev/null 2>&1 &
# poll POST /api/self_audit until it answers

# 4. verify
curl -s -X POST http://127.0.0.1:8791/api/self_audit -H "X-Mif-Token: dev" \
     -H "Content-Type: application/json" -d '{}'
```

Known build-loop failure modes, all already paid for:
- `Unable to build while Live Coding is active` → LiveCodingConsole.exe, or a **second** editor instance.
- `LNK1104: cannot open file …dll` → residual handle. Wait and retry; not a code error.
- **Adding a new .cpp reshuffles the unity blob** and can surface a pre-existing compile error in a file
  that had not been recompiled for several builds. If a build breaks in a file you did not touch,
  that is why — fix it, do not revert your work.

# NON-NEGOTIABLE ARCHITECTURE CONTRACT

Read `docs/00_ARCHITECTURE.md`, `01_POSTMORTEMS.md`, `02_GOTCHAS.md`, `08_LANDSCAPE.md` **before writing
any code**. They encode failures already paid for in full. Re-introducing one is the worst outcome of
this session.

### 1. Three-way 1:1 registry

Every endpoint exists in exactly three places. Drift between them is a recurring bug:

- `MIF_DECL(name);` — `Source/MifBridge/Private/MifBridgeHandlers.h`
- `MIF_BIND(name);` — `Source/MifBridge/Private/MifBridgeCommon.cpp`
- `@mcp.tool() def name(...)` — `tools/mcp-server/server.py`

After every batch assert `grep -c 'MIF_DECL(' … == grep -c 'MIF_BIND(' …`.

### 2. Transaction policy — three buckets, in `MifBridgeCommon.cpp`

- **`IsReadOnlyEndpoint`** — pure queries. Omitting it means `RunEndpoint` wraps a read in an
  `FScopedTransaction` and every query pushes an empty undo entry.
- **`IsSelfManagedEndpoint`** — the handler manages its own transaction or none. **Mandatory** for
  anything that compiles a Blueprint, swaps the `UWorld`, or creates+registers new `UObject`s/textures.
  A full `FKismetEditorUtilities::CompileBlueprint` inside an outer transaction = reinstancing +
  Ctrl-Z = dead CDO = editor crash.
- **default** — one blanket transaction from `RunEndpoint`.

State and justify the bucket for **every** endpoint you add. `self_audit` reports
`policyContradictions`; it must stay empty.

### 3. Handlers run ON the game thread, synchronously and inline, POST-world-tick

**Not** dispatched via `AsyncTask(ENamedThreads::GameThread, …)` — this brief used to say that and it
was backwards. `FHttpServerModule` is an `FTSTickerObjectBase`, so the handler is already on the game
thread, called from `FTSTicker::GetCoreTicker().Tick()` after `GEngine->Tick()` has completed the
entire world tick, outside every tick group. The source comment reads **"Do NOT reach for AsyncTask"**
and gives the crash it causes (`MifBridgeServer.cpp:229-265`: reinstancing mid-tick-group →
`check(!"Pure virtual not implemented")`, `EngineBaseTypes.h:409`). Therefore:

- **Never block.** Blocking deadlocks the HTTP listener, and does so harder than an async model
  would: a blocking handler occupies the ticker that would have to advance whatever it waits on.
- Anything asynchronous (PIE start, navmesh build, shader compile, cook, decompile of a large graph)
  must **request and return**, paired with a status endpoint to poll.
- Anything that tears down or swaps the `UWorld` must be **deferred one tick** via
  `GEditor->GetTimerManager()->SetTimerForNextTick(...)`. Inline world swap trips
  `Assertion failed: !LevelList.Contains(TickTaskLevel)` (`TickTaskManager.cpp:1458`) and kills the
  editor. `new_level`/`load_level` already do this — copy the pattern.

### 4. Silent parameter ignore is the most damaging bug class here

An endpoint that accepts a parameter and quietly drops it returns `ok:true` and sends the caller to
debug the wrong subsystem. Real examples from `01_POSTMORTEMS.md`:

- `trace_ground` read top-level `x`/`y` while callers passed `location:{}` → **every trace silently ran
  at the world origin**, and several downstream diagnoses were wrong for hours.
- `spawn_actor_in_level` accepted `mesh` and dropped it → spawned an empty actor reporting `ok`.

Rules: accept the spellings a caller would reasonably use (`JStrAny`), and **fail loudly** on a
parameter you cannot honour — never silently. Where a result could be misattributed, echo back what
was actually operated on (`trace_ground` now returns `traced:{x,y,z}`).

### 5. Verify with numbers, not vibes

House rule: *numbers for correctness, pixels for taste*. `"ok": true` means the HTTP call succeeded, it
does **not** mean the operation was correct. A cleanup script once reported success while stacking three
landscapes, because the confirm-guard error was piped to `/dev/null`. **Always parse `ok` from a
mutating call, and follow it with a query that proves the effect.**

### 6. Style

- CRLF line endings (`.gitattributes` enforces it).
- Comments explain **why**, never restate the code. Every non-obvious guard cites the failure it
  prevents.
- Small composable endpoints over god-endpoints.
- Errors name the parameter and the fix.

# PHASE 0 — baseline (do this first, do not skip)

1. Start the editor; call `self_audit`; record the live endpoint list, transaction buckets and
   `policyContradictions`.
2. Write `docs/audit/00_BASELINE.md` — every current endpoint, its source file, bucket, one-line purpose.
3. `git rev-parse HEAD` and record it. Commit at the end of each phase so a crash never loses more than
   one phase.

# PHASE 1 — MifKismetReconstructor (HIGHEST VALUE — do this before anything else)

This plugin's capability is written and working but reachable **only from the console**, so an agent
driving MifBridge cannot use any of it. Exposing it is nearly pure gain.

### What is there

Exported (callable cross-module today):
```
MIFKISMETRECONSTRUCTOR_API FKismetBytecodeDisassemblerJson   Public/Toolkit/KismetBytecodeDisassemblerJson.h
MIFKISMETRECONSTRUCTOR_API FPropertyTypeHelper               Public/Toolkit/PropertyTypeHelper.h
```

Internal subsystems (`Private/`) — read every one of these and decide what deserves an endpoint:
- `Analysis/MifUbergraphAnalyzer.cpp`, `MifUbergraphSlicer.cpp`
- `AssetGeneration/KismetBytecodeTransformer.cpp`, `KismetGraphDecompiler.cpp`,
  `KismetGraphDecompiler_Reconstruct.cpp`, `KismetIntermediateFormat.h`
- `Verify/MifFidelityVerifier.cpp`, `MifDriftClassifier.cpp`
- `MifBlueprintDumper.cpp`, `MifReconstructCommand.cpp`, `MifReconstructEvent.cpp`

Eleven console commands, each an obvious endpoint candidate:
```
mif.kr.ListBP        mif.kr.FindBP       mif.kr.DumpBP        mif.kr.DumpFull
mif.kr.Events        mif.kr.Reconstruct  mif.kr.AnalyzeUbergraph
mif.kr.VerifyFidelity  mif.kr.DriftCensus  mif.kr.ClassifyIntentional
mif.kr.LatentResume
```

### Tasks

1. **Read the implementation of every console command.** For each, determine the underlying function,
   its signature, and whether it is exported. Console commands print to the log; endpoints must return
   **structured JSON**. Do **not** implement an endpoint by shelling out to `run_console` — that is
   explicitly forbidden. Call the underlying code.
2. **Decide the coupling model and justify it in writing.** MifBridge currently integrates via a
   *delegate* (`MifBridgeReconstruct.cpp:73` — "graphs are filled with decompiled nodes iff the
   MifKismetReconstructor delegate is bound"), deliberately so MifBridge still loads without the
   reconstructor present. Options:
   - **(a) Hard link** — add `MifKismetReconstructor` to `MifBridge.Build.cs` and a `"Plugins"`
     dependency in `MifBridge.uplugin`. Compile-time checked, but **MifBridge will not load without it**.
   - **(b) Extend the soft-delegate/registration pattern** — MifKismetReconstructor registers its own
     endpoints into MifBridge's registry at load. Endpoints appear only when installed.
   - **Strong recommendation: (b)**, and generalise it into a documented registration interface any
     `Mif*` plugin can use. Then have `self_audit` report a `provider` per endpoint so a caller can see
     which plugin supplied it. If you choose (a), you must state why the coupling is acceptable.
3. **Implement.** Target endpoints (name them as you see fit, these are the capabilities):
   - list / find cooked Blueprints
   - dump a cooked Blueprint's structure as JSON (functions, properties, events, bytecode)
   - disassemble a specific function's bytecode to JSON
   - analyse / slice an ubergraph
   - reconstruct an editable copy (**async — request + poll**, decompiling a large graph is slow, and
     it compiles a Blueprint, so it is **self-managed**)
   - verify fidelity of a reconstruction; drift census; classify intentional drift
   - resume latent actions
4. **Prove each one** against a real cooked Blueprint in this project, and record the asset path used.

**Why this is first:** "cooked Blueprint graphs are unreadable" was the #1 item on the original
capability gap list. The reconstructor already solves it; only the plumbing is missing.

# PHASE 2 — the other 20 plugins

For **each** plugin in `D:/DDS2SDK/Game/Plugins/`, enumerate exported symbols:

```bash
grep -rn "_API " --include=*.h <plugin>/Source/*/Public/ | head -40
```

Then decide whether it unlocks anything an agent would want. Known-interesting:

| Plugin | Likely value |
|---|---|
| `BlueprintAssist`, `ElectronicNodes`, `AutoSizeComments`, `FlatNodes` | **Graph auto-layout.** An agent that spawns 40 nodes leaves them in a heap at the origin. A `format_graph` endpoint would be immediately useful and is currently impossible. |
| `NS_BlueprintToText` | Another Blueprint-serialisation angle; may complement the reconstructor |
| `Oceanology_Plugin`, `Riverology_Plugin` | Water bodies, rivers, ocean — level authoring |
| `FGearPlugin` | Vehicles |
| `AdvancedSessions`, `AdvancedSteamSessions` | Multiplayer/session control (this project does co-op PIE testing) |
| `Hermes-main`, `RedTalaria-master`, `Plugins_RamaThumb` | Read them and judge |
| `GameFeatures` | Game feature plugin state — note the log already shows `ChristmasDlc` / `DDS2Casino` failing to register; an endpoint reporting feature state would be genuinely useful |
| `DLSS`, `NIS`, `Streamline`, `BugSplat` | Probably not agent-useful; confirm and record as negative results |

Record every plugin you rule out **and why**. Negative results stop the next session re-treading them.

# PHASE 3 — engine surface

Only after Phases 1–2. Work these axes; for each, enumerate what is genuinely callable in **this**
engine build, not what you remember from other versions.

**A. Editor core** — `UEditorEngine`/`GEditor` public API; every `UEditorSubsystem`; selection,
outliner folders, grouping; transaction/undo introspection (`GEditor->Trans`); `IConsoleManager`;
`UDeveloperSettings`.

**B. Assets** — `IAssetRegistry` dependency/referencer graphs; `IAssetTools` create/duplicate/rename/
import/export/migrate; **every `UFactory` subclass** (each is a "create asset of type X"); validation;
redirector fixup; source control; `IAssetCompilingManager`.

**C. Blueprints** — every `UK2Node_*` subclass (which are spawnable today, which are not — this alone
may be dozens); full `FBlueprintEditorUtils`; interfaces, macros, timelines, SCS nodes; Blueprint diff;
compile-result introspection; Anim Blueprint state machines and transitions.

**D. Materials** — `UMaterialEditingLibrary` in full. **Material graph authoring is a hard gap today**:
MifBridge can create material *instances* but not materials or expressions. Every
`UMaterialExpression` subclass. Material functions, parameter collections, layers. Shader-compilation
diagnostics (note `diagnose_landscape_draws` already exists — extend the idea).

**E. Geometry** — **`GeometryScript` (`GeometryScriptingCore`/`GeometryScriptingEditor`) is likely the
single biggest untapped surface in the engine**: booleans, extrude, remesh, simplify, UV ops, mesh from
spline, voxel ops. Also `UStaticMeshEditorSubsystem`, LODs, collision generation, sockets, Nanite,
skeletal mesh + physics assets, PCG if present.

**F. World** — World Partition, data layers, level instances, packed level actors, HLOD; **landscape
splines** (roads/rivers that deform terrain — directly relevant, see `08_LANDSCAPE.md`); landscape grass
types, edit layers, heightmap import/export; foliage types and procedural foliage.

**G. Gameplay** — **AI is a known open gap: nothing currently makes an NPC walk a route.** Investigate
behaviour trees, blackboards, EQS, nav links/areas/modifiers, and what actually drives pawn movement in
*this* project. Also Sequencer (large), Niagara (untouched), UMG, physics/Chaos, audio/MetaSounds,
enhanced input.

**H. Data** — DataTables, CurveTables, DataAssets, struct/enum authoring, localization, config, savegames.

**I. Diagnostics** — profiling and `stat` capture, Insights, memory reports, log filtering, high-res
screenshots, live PIE property watch, automation/functional tests.

# PHASE 4 — close the known open gaps

These are real, hit in practice, and each is worth more than a dozen speculative endpoints:

1. **`pie_status` misreports.** It has returned `state: stopped` during a live PIE session the user was
   playing in. Suspect the two-world trap (editor world vs `GEditor->PlayWorld`) or a race against the
   deferred PIE start. Fix it and prove it across start → play → stop.
2. **Walking NPCs.** Splines (`set_spline_points`), navmesh and patrol routes all exist; nothing drives
   pawns along them. `RandomClientSpawnPoint` turned out to be a *static* client. Find what actually
   moves an NPC in this project and expose it.
3. **`snap_actors_to_ground` reports ~112/303 "missed"** on a flat landscape where every actor should
   hit. Root-cause it. The ground-only filter is correct and must stay; the miss rate is unexplained.
4. **Material graph authoring** — see Phase 3D.
5. **Graph auto-layout** — see Phase 2.

# VERIFICATION RULES — apply to every endpoint you add

1. **Cite the API**: `file:line` plus the verbatim signature. If you cannot cite it, do not implement it.
2. **Check the export macro.** A symbol declared without `ENGINE_API`/`UNREALED_API`/`LANDSCAPE_API`/etc
   **cannot be linked from another module**. This has already cost this project a build:
   `ULandscapeComponent::UpdateCollisionData` is not exported; `ALandscapeProxy::RecreateCollisionComponents()`
   was the exported equivalent.
3. **Read the enum/constant, do not guess it.** Two builds were lost to invented names: `MATUSAGE_Landscape`
   does not exist, and viewport types are named by **plane** (`LVT_OrthoXY` = top), not direction.
4. **Module + `.uplugin` dependency** each addition requires; note if it is a plugin that may be absent.
5. **`WITH_EDITOR` guards** where needed.
6. **Transaction bucket** + justification.
7. **Async?** If it spans frames, ship the request/poll pair.
8. **Parameter spec**: names, accepted alternates, types, defaults, behaviour on unknown input.
9. **Cooked-content behaviour** — many things behave differently on `.pak` assets.
10. **A live proof**: the exact curl call and the response that demonstrates it works.

**Anti-invention rule.** Unsure an API exists? Search. Still unsure? It goes in `UNVERIFIED`, never into
the code. A confidently wrong signature costs more than a missing feature.

# WORKING RHYTHM

Work in **batches of 5–15 related endpoints** that share module dependencies, so each build cycle adds
the fewest new modules:

1. Research + cite → 2. Implement (`.cpp`, `MIF_DECL`, `MIF_BIND`, `server.py`) → 3. Assert
DECL==BIND → 4. Build → 5. Relaunch → 6. `self_audit` (`policyContradictions` empty) → 7. Live-test each
endpoint → 8. Document → 9. `git commit` → 10. Next batch.

**Never leave the tree unbuildable between batches.** If a batch will not compile, fix or remove it
before moving on.

# DELIVERABLES

Code:
- New/modified handler `.cpp` files, registry entries, `server.py` tools, `Build.cs`/`.uplugin` updates.
- A commit per batch, conventional messages explaining **why**.

Docs, in `docs/`:
- `audit/00_BASELINE.md` — endpoints before you started.
- `audit/01_IMPLEMENTED.md` — everything you added: name, bucket, module, API cited, live-test call and
  response.
- `audit/02_CATALOGUE.md` — verified but not implemented, ranked (unblocks × effort × risk), with a
  suggested batch order.
- `audit/03_GAPS_AND_RISKS.md` — attractive-looking APIs that are **not viable** and why: non-exported
  symbols, cooked-content limits, 5.3.2 engine bugs, plugins ruled out. Negative results are deliverables.
- `audit/04_OPEN_QUESTIONS.md` — anything needing a human decision.
- Update `01_POSTMORTEMS.md` for any non-obvious failure you hit, in the existing
  symptom → root cause → fix → prevention format.
- Update `README.md` with the new endpoint count.

Keep a running progress log so an interrupted run resumes cleanly: after each batch, append what landed
and what remains.

# SUCCESS CRITERIA

- MifBridge's endpoint count is **substantially** higher and **every** new endpoint has a recorded live
  proof.
- `self_audit` reports zero policy contradictions and `MIF_DECL == MIF_BIND`.
- The tree builds clean.
- MifKismetReconstructor's capability is reachable over HTTP without console commands.
- The catalogue is good enough that the *next* session can implement any entry without opening engine
  source to look anything up.
- No documented trap from `01_POSTMORTEMS.md` / `02_GOTCHAS.md` has been re-introduced.

# FINAL INSTRUCTION

Prefer **correct and proven** over **many**. An endpoint that returns `ok:true` while doing nothing is
worse than no endpoint, because it costs the next person hours of debugging the wrong subsystem. That
exact failure has happened repeatedly in this codebase and is the reason most of the rules above exist.
