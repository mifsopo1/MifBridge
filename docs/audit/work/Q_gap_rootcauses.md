# Axis Q — root causes for two known defects (repair entries)
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 depth (defect forensics)._
_Scope: docs/10_FULL_SCOPE_EXPANSION_PROMPT.md Phase 4 items 1 and 3. Both entries are `kind: behaviour change` repairs to EXISTING endpoints (axis-C style), not new endpoints._

## Surface inventory

Plugin source read (complete files or complete handlers):

- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgePIE.cpp` — all 601 lines: `GetPIEWorld` (:47-50), `CollectPIEWorlds` (:73-86), `WritePieStateInto` (:104-145), `H_start_pie` (:212-318), `H_stop_pie` (:321-343), `H_pie_status` (:346-363), `H_list_pie_actors` (:371-426), `H_spawn_actor_in_pie` (:472-598).
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeWorld.cpp:336-488` — `H_snap_actors_to_ground` complete.
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeSpatial.cpp:160-222` — `H_trace_ground` complete (comparison baseline).
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeCommon.cpp:240-330` — transaction-bucket sets (pie_status is read-only :272; snap_actors_to_ground is in neither set — ReadOnly :247-285, SelfManaged :296-328 — ⇒ transacted). _[Phase-2: line cites corrected from :266/:308.]_
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeServer.cpp:199` — `AsyncTask(ENamedThreads::GameThread, ...)` dispatch: every handler runs on the game thread, so no cross-thread read explains either defect.
- Git history of the plugin (it is a repo): _[Phase-2 corrected]_ the repo has 17 commits starting at `89dda64` "Initial commit"; `MifBridgePIE.cpp` is touched by exactly 3 (`f94b88c` → `7a4fba1` "Multiplayer Testing" → `a1b172e`), `git log -S WritePieStateInto` first hits at `f94b88c`, and `git grep pie_status b97d7eb` (the commit before `f94b88c`) finds nothing — no PIE code exists in the repo before `f94b88c`, whose `WritePieStateInto` already has today's queued-aware logic. The binary that produced the recorded misreport therefore predates the repo's PIE code (see Negative results).

Engine source read (regions cited below): `Editor/UnrealEd/Classes/Editor/EditorEngine.h` (:395-484, :1680-1751, :2446, :2915-2924, :3195-3240), `Editor/UnrealEd/Private/PlayLevel.cpp` (:600-700, :860-1110, :2540-2680, :2936-3030), `Editor/UnrealEd/Private/PlayLevelNewProcess.cpp` (:1-80), `Editor/UnrealEd/Private/EditorEngine.cpp` (:1806-1900, :2130-2245, :7387-7393), `Editor/UnrealEd/Public/PlayInEditorDataTypes.h` (complete, 281 lines), `Runtime/Engine/Classes/Engine/Engine.h` (:338-472, :3321-3346), `Runtime/Engine/Private/World.cpp` (:5770-5773), `Runtime/Engine/Private/UnrealEngine.cpp` (LoadMap teardown lines :14879, :14899, :15099), `Runtime/Engine/Classes/Engine/World.h` (:1919-1965, :266-281), `Runtime/Engine/Public/CollisionQueryParams.h` (:238-258), `Runtime/Engine/Private/PhysicsEngine/CollisionQueryFilterCallback.cpp` (:11-80), `Runtime/Engine/Classes/Components/PrimitiveComponent.h` (:2436-2438), `Runtime/Landscape/Private/LandscapeCollision.cpp` (:2961-2978, :2993-3010), `Runtime/Landscape/Classes/LandscapeProxy.h` (:927), `Runtime/Landscape/Classes/LandscapeHeightfieldCollisionComponent.h` (:31, :39-40, :316), `Engine/Config/BaseEngine.ini` (:2702).

Live probes (read-only, 2026-07-26):

- `POST /api/pie_status {}` (no PIE running) →
  `{"ok":true,"running":false,"startPending":false,"sessionActive":false,"worldHasBegunPlay":false,"stopPending":false,"simulating":false,"state":"stopped","editorWorld":"Untitled"}`
  — note: **no per-instance array, no queued/travelling/stopping distinction** in the live payload.
- `POST /api/self_audit {}` → `endpointCount: 160` (the live DLL now serves all 160; the brief's "156 of 160" note is stale).

---

## DEFECT 1 — `pie_status` reported `state:"stopped"` during a live PIE session

### The engine's actual PIE truth model (all citations verified by reading the file)

**The deferred start.** `RequestPlaySession` only stores a request:

```cpp
UNREALED_API void RequestPlaySession(const FRequestPlaySessionParams& InParams);
```
`Editor/UnrealEd/Classes/Editor/EditorEngine.h:1683`; implementation stores `PlaySessionRequest = InParams;` at `Editor/UnrealEd/Private/PlayLevel.cpp:950`. The request is consumed on the **next editor tick**:

```cpp
	// Kick off a Play Session request if one was queued up during the last frame.
	if (PlaySessionRequest.IsSet())
	{
		StartQueuedPlaySessionRequest();
```
`Editor/UnrealEd/Private/EditorEngine.cpp:1807-1810`, which resets `PlaySessionRequest` after the attempt (`PlayLevel.cpp:1066`) and creates the session record `PlayInEditorSessionInfo = FPlayInEditorSessionInfo();` (`PlayLevel.cpp:1097`).

**The three phase accessors** (all inline in the header, no export needed):

```cpp
	/** Returns true if we are currently either PIE/SIE in the editor, false if we are not (even if we would start next tick). See IsPlaySessionInProgress() */
	bool IsPlayingSessionInEditor() const { return PlayInEditorSessionInfo.IsSet(); }
	/** Returns true if we are going to start PIE/SIE on the next tick, false if we are not (or if we are already in progress). See IsPlaySessionInProgress() */
	bool IsPlaySessionRequestQueued() const { return PlaySessionRequest.IsSet(); }
	/** Returns true if Playing in Editor, Simulating in Editor, or are either of these queued to start on the next tick. */
	bool IsPlaySessionInProgress() const { return IsPlayingSessionInEditor() || IsPlaySessionRequestQueued(); }
```
`EditorEngine.h:1703-1708`. Also `IsSimulatingInEditor()` at `EditorEngine.h:1711` (checks `PlayInEditorSessionInfo->OriginalRequestParams.WorldType == EPlaySessionWorldType::SimulateInEditor`), `ShouldEndPlayMap()` at `EditorEngine.h:1745` (`return bRequestEndPlayMapQueued;`), `RequestEndPlayMap` at `EditorEngine.h:1740`, and

```cpp
	const TOptional<FPlayInEditorSessionInfo> GetPlayInEditorSessionInfo() const { return PlayInEditorSessionInfo; }
```
`EditorEngine.h:1721`. Session fields that matter for a status endpoint (`Editor/UnrealEd/Public/PlayInEditorDataTypes.h`): `int32 PIEInstanceCount;` (:247), `int32 NumClientInstancesCreated;` (:256), `int32 NumOutstandingPIELogins;` (:259, "Used to track when we've finished getting PIE started"), `bool bServerWasLaunched;` (:271), `FRequestPlaySessionParams OriginalRequestParams;` (:234).

**What `PlayWorld` actually is.** Declared:

```cpp
	/** A pointer to a UWorld that is the duplicated/saved-loaded to be played in with "Play From Here" 								*/
	UPROPERTY()
	TObjectPtr<class UWorld> PlayWorld;
```
`EditorEngine.h:407-409`. It is written in FIVE places, none of which make it a stable "the PIE world" handle:

1. At instance creation: `PlayWorld = PieWorldContext->World();` — `PlayLevel.cpp:2971` (inside `CreateInnerProcessPIEGameInstance`, once per instance — so after a multi-instance startup it points at the **last** instance created).
2. **Reassigned every tick** in the PIE tick loop: `PlayWorld = PieContext.World();` — `EditorEngine.cpp:1867`, inside `for (FWorldContext* PieContextPtr : LocalPieContextPtrs)` whose candidate list only admits contexts with `PieContext.World() != nullptr && PieContext.World()->ShouldTick()` (`EditorEngine.cpp:1845`), and the whole loop is gated by `if( FSlateThrottleManager::Get().IsAllowingExpensiveTasks() )` (`EditorEngine.cpp:1838`).
3. Reassigned again in the render loop: `PlayWorld = PieContext.World();` — `EditorEngine.cpp:2164` (iterates ALL PIE contexts; after the loop `PlayWorld` dangles on the **last** PIE context in `WorldList`).
4. During teardown: `PlayWorld = PieWorldContext.World();` then later `PlayWorld = NULL;` — `PlayLevel.cpp:684` and `:879`.
5. _[Phase-2 CORRECTED — the phase-1 draft had this mechanism wrong.]_ By `FWorldContext::SetCurrentWorld` retargeting: `PlayWorld` **IS** registered as an external reference on every in-process PIE context — `UGameInstance::InitializeForPlayInEditor` ties it explicitly:

```cpp
	WorldContext->AddRef(static_cast<UWorld*&>(EditorEngine->PlayWorld));	// Tie this context to this UEngine::PlayWorld*		// @fixme, needed still?
```
`Runtime/Engine/Private/GameInstance.cpp:350`. `SetCurrentWorld` (impl `Runtime/Engine/Private/UnrealEngine.cpp:1433-1460`) rewrites each registered pointer **only if it currently equals the context's outgoing world** (`:1445-1451`). Consequences: in a single-instance non-seamless travel, `PlayWorld` is nulled at `UnrealEngine.cpp:14899` and re-pointed at `:15099` — both inside the same synchronous `LoadMap` call, not across frames; in a multi-instance session every PIE context holds `&PlayWorld` in its `ExternalReferences`, so whichever context's `SetCurrentWorld` finds `PlayWorld` equal to its world clobbers it (another last-writer-wins site), and a travelling context whose world `PlayWorld` does NOT currently equal leaves it **stale** rather than null. GC is not the mechanism (the UPROPERTY would only be GC-nulled if it still pointed at a garbage world at collection time, which the synchronous retarget normally preempts). Full engine caller list of `FWorldContext::AddRef` (verified grep): `GameInstance.cpp:350`, `LevelEditorViewport.cpp:5134`, `PreviewScene.cpp:59`, `SActorEditorContext.cpp:20`, `LevelEditorSubsystem.cpp:778`, `GameViewportClient.cpp:476`.

**What travel does.** `TickWorldTravel(PieContext, TickDeltaSeconds);` is called per PIE context inside the tick loop (`EditorEngine.cpp:1895`). Non-seamless travel lands in `UEngine::LoadMap` (`Runtime/Engine/Private/UnrealEngine.cpp`): the old world is `MarkObjectsPendingKill()` (:14879), the context world is nulled — `WorldContext.SetCurrentWorld(nullptr);` (:14899) — and only re-set at the very end: `WorldContext.SetCurrentWorld(NewWorld);` (:15099); all of this is synchronous within one `TickWorldTravel` call. For a client connect there is additionally a multi-frame `PendingNetGame` phase (`Engine.h:365-366`). _[Phase-2 CORRECTED:]_ the context's world is **not** null during that phase — PIE clients are deliberately given a minimal entry world while they connect (`Runtime/Engine/Private/GameInstance.cpp:313-317`: "We are going to connect, so just load an empty world" → `CreatePIEWorldFromEntry`, made current at `:349`), so the truthful mid-connect signal is `PendingNetGame != nullptr` on a context whose world is not the game map — exactly what the repaired `travelling` predicate tests. A genuinely null-world context (transient, during swap/teardown) is **skipped** by the tick-loop candidate filter (`EditorEngine.cpp:1845`), so write-site 2 never re-points `PlayWorld` for it.

**The readiness signal is real:**

```cpp
bool UWorld::HasBegunPlay() const
{
	return bBegunPlay && PersistentLevel && PersistentLevel->Actors.Num();
}
```
`Runtime/Engine/Private/World.cpp:5770-5773`.

**Multi-instance enumeration** (the correct model): `const TIndirectArray<FWorldContext>& GetWorldContexts() const { return WorldList;	}` — `Engine.h:3346` (inline); `FWorldContext` members `TEnumAsByte<EWorldType::Type> WorldType;` (:345), `FSeamlessTravelHandler SeamlessTravelHandler;` (:347), `FString TravelURL;` (:352), `int32 PIEInstance;` (:406), `bool RunAsDedicated;` (:415), `bool bIsPrimaryPIEInstance;` (:421), `FORCEINLINE UWorld* World() const` (:466-469). Lookup by instance:

```cpp
	UNREALED_API FWorldContext* GetPIEWorldContext(int32 WorldPIEInstance = 0);
```
`EditorEngine.h:2446`; implementation matches `WorldContext.WorldType == EWorldType::PIE && WorldContext.PIEInstance == WorldPIEInstance` (`EditorEngine.cpp:7387-7393`).

### The exact holes in the current handler

`WritePieStateInto` (`MifBridgePIE.cpp:104-145`) derives everything from ONE pointer:

```cpp
		UWorld* GetPIEWorld()
		{
			return GEditor ? GEditor->PlayWorld : nullptr;
		}
```
(`MifBridgePIE.cpp:47-50`), then

```cpp
			const bool bRunning = PIEWorld != nullptr && PIEWorld->HasBegunPlay();
			const bool bQueued = GEditor && GEditor->IsPlaySessionInProgress() && !bRunning;
			...
			const TCHAR* State = bRunning ? TEXT("running") : bQueued ? TEXT("starting") : TEXT("stopped");
```
(`MifBridgePIE.cpp:113-126`). Cross-checked against the engine model above, the concrete holes are:

- **Hole A _(Phase-2 CORRECTED — symptom stands, mechanism rewritten)_ — travel makes `PlayWorld` null or stale while the session is live.** DDS2 travels off the opened map on every play session (IslaSombra → OpenWorld; the plugin's own comment, `MifBridgePIE.cpp:468-471`). The mechanism is `FWorldContext::SetCurrentWorld` retargeting the AddRef-tied `&PlayWorld` (write-site 5, corrected), not GC. The windows where a one-pointer derivation reads wrong mid-session:
  (a) single-instance non-seamless travel — `PlayWorld` is null between `UnrealEngine.cpp:14899` and `:15099`, but both are inside one synchronous `LoadMap`, so this window is observable to a bridge handler **only if** game-thread tasks are pumped mid-`LoadMap` (UNVERIFIED — now load-bearing for this sub-case, see UNVERIFIED);
  (b) a PIE client's multi-frame connect (co-op via the bridge's own `start_pie players>1`) — the context holds a minimal **entry world** (`GameInstance.cpp:313-317`), not the game map, so a `PlayWorld` left pointing there makes `bRunning`/actor queries reflect an empty placeholder world mid-session;
  (c) multi-instance sessions — the conditional retarget (`UnrealEngine.cpp:1445-1451`) plus write-sites 2/3 leave `PlayWorld` on an arbitrary, stale, or torn-down context while other instances play on;
  (d) Slate-throttled stretches — write-site 2 never runs (`EditorEngine.cpp:1838`) and null-world contexts are skipped (`:1845`), so a nulled/stale pointer stays wrong across frames.
  In every such window today's derivation reports non-running mid-session (`state:"starting"`, running:false); the recorded `state:"stopped"` is such a window under a running/stopped binary derivation (see Negative results on the unrecoverable binary). `list_pie_actors` in the same windows fails with "no PIE world — not playing" (`MifBridgePIE.cpp:376`), which is a false statement. The repair does not depend on which window fired — enumerating `GetWorldContexts()` sidesteps the pointer entirely.
- **Hole B — one arbitrary world stands for the whole session.** With `players>1` under one process there are several PIE contexts (server + clients); `GEditor->PlayWorld` ends each frame pointing at whichever PIE context the render loop visited **last** (`EditorEngine.cpp:2164`). `pie_status` reports `pieWorld`, `timeSeconds`, `pieActorCount`, `playerController`, `pawn` (`MifBridgePIE.cpp:130-137, 350-362`) for that one arbitrary world. The plugin already owns the correct enumerator — `CollectPIEWorlds` over `GEngine->GetWorldContexts()` filtering `EWorldType::PIE` (`MifBridgePIE.cpp:73-86`) — but only `spawn_actor_in_pie` uses it.
- **Hole C — no `stopping` state and no `queued`/`starting` split.** `stopPending` is emitted as a bool (`MifBridgePIE.cpp:122`, from `ShouldEndPlayMap()`, `EditorEngine.h:1745`) but the `state` word never says `stopping`; and between `start_pie` returning and the next editor tick the truthful phase is "queued, not yet consumed" (`IsPlaySessionRequestQueued()`, `EditorEngine.h:1706`), which the payload folds into `starting`.
- **Hole D — genuine "stopped while the user plays": out-of-process sessions.** If the session runs as a separate process (editor toolbar "Standalone Game", or multi-process PIE), the engine **deliberately wipes its own session state right after launching**:

```cpp
	// Now that we've launched the new process, we'll cancel the request so that the UI lets us go into PIE.
	// This doesn't clear our tracked sessions, so next time PIE is started it will close any standalone instances.
	CancelRequestPlaySession();
```
`Editor/UnrealEd/Private/PlayLevelNewProcess.cpp:57-59`, and `CancelRequestPlaySession` resets BOTH optionals (`PlayLevel.cpp:980-985`). After that, `IsPlaySessionInProgress()` is false and there are no PIE contexts — `pie_status` says `stopped` while the user is playing in the standalone window. The bridge itself always uses `EPlaySessionDestinationType::InProcess` (`MifBridgePIE.cpp:231`), so this arises only from user-initiated standalone sessions; it is **undetectable in-process by design** (nothing remains to observe) and must be documented, not "fixed".
- **Hole E (minor) — deprecated member read.** `simulating` is read from `GEditor->bIsSimulatingInEditor` (`MifBridgePIE.cpp:123`); the member carries a commented-out `UE_DEPRECATED(4.25, ...)` and engine code only touches it under deprecation pragmas (`EditorEngine.h:3229-3232`, `PlayLevel.cpp:650-653`). The accessor `IsSimulatingInEditor()` (`EditorEngine.h:1711`) is the supported source and is also correct during the starting phase.

### Repair entry

### pie_status  (repair — behaviour change)
**Phase-2 verdict**: CORRECTED — root-cause mechanism for Hole A rewritten: `PlayWorld` IS AddRef-tied to every in-process PIE context (`GameInstance.cpp:350`), `SetCurrentWorld` nulls/re-points it synchronously inside `LoadMap` (`UnrealEngine.cpp:1445-1451`, `:14899`, `:15099`), and PIE clients hold an entry world during connect (`GameInstance.cpp:313-317`) rather than a null world. The repair design itself — context enumeration, state machine, per-instance array, params, bucket, verification plan — re-verified unchanged (verify step 2's world-name-change arm already covers the corrected mechanism); all engine signatures re-read verbatim; bucket line cite fixed (:272).
**Purpose**: make PIE state reporting truthful across the full session lifecycle (queued → starting → running/simulating → travelling → stopping → stopped) and across ALL PIE instances, so a polling agent can never conclude "stopped" while a session is live in-process.
**Engine API**:
```cpp
bool IsPlayingSessionInEditor() const { return PlayInEditorSessionInfo.IsSet(); }
bool IsPlaySessionRequestQueued() const { return PlaySessionRequest.IsSet(); }
bool IsPlaySessionInProgress() const { return IsPlayingSessionInEditor() || IsPlaySessionRequestQueued(); }
bool IsSimulatingInEditor() const { return PlayInEditorSessionInfo.IsSet() && PlayInEditorSessionInfo->OriginalRequestParams.WorldType == EPlaySessionWorldType::SimulateInEditor; }
bool ShouldEndPlayMap() const { return bRequestEndPlayMapQueued; }
const TOptional<FPlayInEditorSessionInfo> GetPlayInEditorSessionInfo() const { return PlayInEditorSessionInfo; }
const TIndirectArray<FWorldContext>& GetWorldContexts() const { return WorldList;	}
FORCEINLINE UWorld* World() const
bool UWorld::HasBegunPlay() const
UNREALED_API FWorldContext* GetPIEWorldContext(int32 WorldPIEInstance = 0);
```
`Editor/UnrealEd/Classes/Editor/EditorEngine.h:1704`, `:1706`, `:1708`, `:1711`, `:1745`, `:1721`, `Runtime/Engine/Classes/Engine/Engine.h:3346`, `:466`, `Runtime/Engine/Private/World.cpp:5770` (decl `World.h`), `EditorEngine.h:2446`. Per-context fields: `Engine.h:345` (WorldType), `:352` (TravelURL), `:365-366` (PendingNetGame), `:406` (PIEInstance), `:415` (RunAsDedicated), `:421` (bIsPrimaryPIEInstance), `:347` + `World.h:268` (`FORCEINLINE bool IsInTransition() const` on FSeamlessTravelHandler).
**Export**: all accessors above are inline in headers (no link dependency); `GetPIEWorldContext` is `UNREALED_API`; `FWorldContext::SetCurrentWorld` is `ENGINE_API` (not needed). | **Module**: none — Engine + UnrealEd already linked. | **Guards**: none beyond the module being editor-only (handler already compiles in this TU).
**Bucket**: read-only — pure query; already in the ReadOnly set (`MifBridgeCommon.cpp:272`); unchanged.
**Async**: no — this IS the poll half of the existing `start_pie`/`stop_pie` request pair.
**Behaviour change (the fix)**:
1. **Never read `GEditor->PlayWorld`.** Enumerate `GEngine->GetWorldContexts()`, filter `WorldType == EWorldType::PIE` (the existing `CollectPIEWorlds`, `MifBridgePIE.cpp:73-86`, extended to keep contexts whose `World()` is null so travel is visible).
2. **State machine** (first match wins), each predicate cited above:
   - `stopping` ⇐ `GEditor->ShouldEndPlayMap()`
   - `queued` ⇐ `GEditor->IsPlaySessionRequestQueued()` (request stored, consumed next tick — `EditorEngine.cpp:1808-1810`)
   - `travelling` ⇐ `IsPlayingSessionInEditor()` && any PIE context has (`World()==nullptr` || `PendingNetGame != nullptr` || `!TravelURL.IsEmpty()` || `SeamlessTravelHandler.IsInTransition()`) — the session is live but a world is being swapped; actor queries will transiently fail and should be retried, NOT treated as session end
   - `simulating` ⇐ ≥1 PIE context world with `HasBegunPlay()` && `GEditor->IsSimulatingInEditor()`
   - `running` ⇐ ≥1 PIE context world with `HasBegunPlay()`
   - `starting` ⇐ `IsPlayingSessionInEditor()` (instances/logins still being created; expose `outstandingLogins` from `NumOutstandingPIELogins`, `PlayInEditorDataTypes.h:259`)
   - `stopped` ⇐ otherwise. Defensive: if PIE contexts exist but no session info (should be impossible in-process), report `state:"unknown"` plus the raw booleans rather than guessing — never fabricate `stopped` while a PIE world exists.
3. **Per-instance array** `instances:[{pieInstance, world|null, netMode, isServer, runAsDedicated, isPrimary, hasBegunPlay, actorCount, timeSeconds, travelUrl, pendingConnect, playerController?, pawn?, pawnClass?}]` — one entry per PIE context, built with the fields cited above; `playerController/pawn` move INTO the instance entries (currently reported for one arbitrary world, `MifBridgePIE.cpp:350-362`).
4. **Back-compat fields kept**: `running`, `startPending`, `sessionActive`, `stopPending`, `simulating`, `worldHasBegunPlay`, `editorWorld`, plus `pieWorld`/`timeSeconds`/`pieActorCount` mirroring the PRIMARY instance (`bIsPrimaryPIEInstance`, else first) so existing callers keep working. `session:{pieInstanceCount, clientsCreated, outstandingLogins, serverWasLaunched}` added from `FPlayInEditorSessionInfo` (:247, :256, :259, :271).
5. `simulating` read via `IsSimulatingInEditor()` accessor, not the deprecated member (Hole E).
6. Same state derivation reused by `list_pie_actors`/`spawn_actor_in_pie` error paths so their "not playing" message becomes "session live but travelling — retry" when that is the truth (Hole A's second symptom, `MifBridgePIE.cpp:376`, `:488`).
**Params**: | name | aliases | type | default | required |
| instance | pieInstance | int | (absent = all instances) | no |
Unrecognised parameter ⇒ error naming it (matches contract §4). `instance` filters the `instances` array to one `PIEInstance` and errors `"no PIE context with pieInstance=N — see instances[] of a parameterless call"` if absent.
**Failure modes**:
- No editor (`!GEditor`) ⇒ `"no editor"` (unchanged).
- `instance` given while stopped ⇒ error above (never silently returns global state).
- Out-of-process session (user's own "Standalone Game"): engine wipes session state at launch (`PlayLevelNewProcess.cpp:57-59`) ⇒ truthfully `stopped`; response gains a fixed documentation string `note:"out-of-process play sessions (Standalone Game) are not observable in-process"` so the limitation is stated at the point of confusion.
**Cooked**: works — PIE over cooked/pak-mounted maps is exactly the DDS2 flow; state derivation touches no asset data.
**Verify** (numbers; composable from existing endpoints, matches "start→poll until running→stop→poll until stopped" plus the misreport repro):
1. Baseline recorded 2026-07-26 (no PIE): live payload above; after fix expect same `state:"stopped"` plus `instances:[]`, `instanceCount:0`.
2. `start_pie {}` → assert response `requested:true`; immediate `pie_status` ⇒ `state ∈ {queued, starting}`; poll every 200 ms recording each state: assert the sequence is a subset of `queued→starting→running→travelling→running` with ≥1 `running` sample, and **0 `stopped` samples** between the start ack and the stop ack. On DDS2 the travel IslaSombra→OpenWorld must appear either as ≥1 `travelling` sample or as `instances[0].world` changing name between two `running` samples (UEDPIE_* names) — this is the regression test for the recorded defect: the pre-fix binary derivation reads `stopped` in exactly that window (Hole A).
3. While `running`: `list_pie_actors` ⇒ `count > 0`; assert `pie_status.instances[0].actorCount == list_pie_actors.matched` (same world, same tick order of magnitude).
4. `stop_pie` ⇒ `wasRunning:true`; poll ⇒ sequence subset of `stopping→stopped`; final `instances:[] && editorWorld` unchanged.
5. Multi-instance (the co-op case Hole B): `start_pie {players:2, netMode:"listen", oneProcess:true}` → poll until `running` ⇒ assert `instanceCount==2`, exactly one instance `isServer:true` (netMode `listenServer`), both `hasBegunPlay:true`, distinct `pieInstance` values; `spawn_actor_in_pie {netMode:"server"}` ⇒ its `targetWorld.world` equals the `isServer:true` instance's `world`. `stop_pie` → poll `stopped`.
**Score**: U5 E4 R5 → tier 0 — closes Phase-4 item 1; prevents the documented failure "agent aborts a live session because one poll said stopped".

---

## DEFECT 2 — `snap_actors_to_ground` reports ~112/303 "missed" on a flat landscape

### What the handler does today

`MifBridgeWorld.cpp:343-488`. Per target actor: ignore self + attached children (`:412-416`), trace straight down through the pivot from `Loc.Z + TraceHeight` to `Loc.Z - TraceHeight` (default `traceHeight` 100000, `:349, :418-419`):

```cpp
			TArray<FHitResult> Hits;
			World->LineTraceMultiByChannel(Hits, Start, End, ECC_WorldStatic, Params);
```
(`:425-426`), then scan `Hits` for the first entry whose actor `IsA<ALandscapeProxy>()` (default ground rule, `:440`), or matches `groundActor` (`:433-439`), or anything if `allowAnyHit` (`:441`). No qualifying entry ⇒ `++Missed`, actor untouched (`:443-448`).

The comment above the trace states the design assumption: *"MULTI-trace, then take the first hit that is actually GROUND"* (`:421-424`) — i.e., the author believed the multi-trace returns every hit along the ray.

### The engine contract says otherwise (root cause, confirmed)

```cpp
	/**
	 *  Trace a ray against the world using a specific channel and return overlapping hits and then first blocking hit
	 *  Results are sorted, so a blocking hit (if found) will be the last element of the array
	 *  Only the single closest blocking result will be generated, no tests will be done after that
	 ...
	 */
	bool LineTraceMultiByChannel(TArray<struct FHitResult>& OutHits,const FVector& Start,const FVector& End,ECollisionChannel TraceChannel,const FCollisionQueryParams& Params = FCollisionQueryParams::DefaultQueryParam, const FCollisionResponseParams& ResponseParam = FCollisionResponseParams::DefaultResponseParam) const;
```
`Runtime/Engine/Classes/Engine/World.h:1953-1965` (emphasis: line 1956, "Only the single closest blocking result will be generated, no tests will be done after that").

The hit type per shape is the **minimum** of the query's response and the shape's response:

```cpp
		// return minimum agreed-upon interaction
		return FMath::Min(QuerierHitType, ShapeHitType);
```
`Runtime/Engine/Private/PhysicsEngine/CollisionQueryFilterCallback.cpp:75-76` (inside `CalcQueryHitType`, `:11-80`). The handler passes no `ResponseParam`, so the default applies — "By default, every channel will be blocked":

```cpp
struct FCollisionResponseParams
{
	/** 
	 *	Collision Response container for trace filtering. ...
	 *	By default, every channel will be blocked
	 */
	struct FCollisionResponseContainer CollisionResponse;

	FCollisionResponseParams(ECollisionResponse DefaultResponse = ECR_Block)
	{
		CollisionResponse.SetAllChannels(DefaultResponse);
	}
	...
	static ENGINE_API FCollisionResponseParams DefaultResponseParam;
};
```
`Runtime/Engine/Public/CollisionQueryParams.h:238-258`.

Landscape collision blocks everything (so it CAN be the terminal hit when unobstructed):

```cpp
ULandscapeHeightfieldCollisionComponent::ULandscapeHeightfieldCollisionComponent(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	SetCollisionProfileName(UCollisionProfile::BlockAll_ProfileName);
```
`Runtime/Landscape/Private/LandscapeCollision.cpp:2961-2964`; the `BlockAll` profile is `ObjectTypeName="WorldStatic"`, blocks all (`Engine/Config/BaseEngine.ini:2702`).

**Therefore:** every static mesh with default blocking collision (roads, plaza floors, decks, neighbouring props — profile-typical `ECR_Block` vs `ECC_WorldStatic`) that lies between `Start` (1 km above the actor) and the landscape becomes the trace's terminal hit; **the landscape never enters `Hits` at all**; the ground filter finds nothing; the actor is counted "missed". The multi-trace under default responses is functionally identical to `trace_ground`'s `LineTraceSingleByChannel` (`MifBridgeSpatial.cpp:199-201`, `World.h:1929`) plus an is-it-landscape check on the single blocking hit.

This exactly reproduces the defect signature: only `missed` inflates (never a wrong snap — the filter is sound), and the miss population is "actors whose downward ray crosses any other blocking collision before the landscape". In a dense cooked town scene, 112/303 ≈ 37% of pivots sitting over roads/floor meshes/overlapping props is unremarkable. The doc's constraint ("the ground-only filter is correct and must stay") is satisfiable: the filter was never the bug — the truncated hit list was.

### Ranked hypotheses

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| H1 | First-blocking-hit truncation shadows the landscape (props/roads/floors between ray start and landscape) | **CONFIRMED as the mechanism** — code (`MifBridgeWorld.cpp:425-442`) + engine contract (`World.h:1956`) + default response (`CollisionQueryParams.h:243`) + min-interaction filter (`CollisionQueryFilterCallback.cpp:76`). Predicts miss-only inflation, matching the report. |
| H2 | Sub-population of H1: actors legitimately standing ON static-mesh ground (roads/floors). The landscape-only rule wants the landscape BELOW the road; truncation makes that unreachable | Same mechanism; distinguishable per-actor once the repaired echo lands (their `firstBlocker` is walkable geometry) |
| H3 | Actors outside the landscape XY extent, or over landscape visibility-mask holes (no collision there) | Plausible minor contributor; not verifiable without the scene. The repaired per-actor echo separates it: `hitCount==0` (nothing at all below) vs hits-without-landscape (H1) |
| H4 | Self-hit (actor's own roof blocks its trace) | RULED OUT — self and attached children are ignored (`MifBridgeWorld.cpp:412-416`) |
| H5 | `traceHeight` 100000 too short on a flat landscape | RULED OUT for this repro — only fails for actors >1 km above/below ground; echoing `startZ/endZ` per actor makes any residual case visible |
| H6 | Landscape does not respond to ECC_WorldStatic | RULED OUT — `BlockAll` profile (`LandscapeCollision.cpp:2964`, `BaseEngine.ini:2702`); also 191/303 DID hit landscape |

### Repair entry

### snap_actors_to_ground  (repair — behaviour change)
**Phase-2 verdict**: CONFIRMED — every citation re-read verbatim (`World.h:1956` truncation contract, `CollisionQueryParams.h:243/:247/:257` Block-all default, `CollisionQueryFilterCallback.cpp:75-76` min-interaction, `LandscapeCollision.cpp:2964` BlockAll, `BaseEngine.ini:2702`, `LandscapeProxy.h:927` LANDSCAPE_API, `PrimitiveComponent.h:2438` ENGINE_API); handler lines exact (`MifBridgeWorld.cpp:343-488`); bucket confirmed (in neither set, `MifBridgeCommon.cpp:247-285`/`:296-328`); fix semantics verified against the filter callback (ECR_Overlap ⇒ Min(Block,Touch)=Touch ⇒ untruncated sorted hit list; the handler already ignores the return bool, `:426`). One nuance, no impact on the finding: under default responses the multi-trace also returns Touch entries from overlap-profile shapes ahead of the terminal blocking hit, so "functionally identical to a single trace" is slightly overstated — irrelevant here because landscape collision is Block and can therefore only ever appear as the single terminal blocking hit.
**Purpose**: make the landscape reachable through intervening blocking geometry so "missed" means "genuinely no ground below", and make every remaining miss self-explaining per actor.
**Engine API**:
```cpp
bool LineTraceMultiByChannel(TArray<struct FHitResult>& OutHits,const FVector& Start,const FVector& End,ECollisionChannel TraceChannel,const FCollisionQueryParams& Params = FCollisionQueryParams::DefaultQueryParam, const FCollisionResponseParams& ResponseParam = FCollisionResponseParams::DefaultResponseParam) const;
FCollisionResponseParams(ECollisionResponse DefaultResponse = ECR_Block)
ENGINE_API virtual ECollisionResponse GetCollisionResponseToChannel(ECollisionChannel Channel) const override;
LANDSCAPE_API TOptional<float> GetHeightAtLocation(FVector Location, EHeightfieldSource HeightFieldSource = EHeightfieldSource::Complex) const;
```
`Runtime/Engine/Classes/Engine/World.h:1965`; `Runtime/Engine/Public/CollisionQueryParams.h:247`; `Runtime/Engine/Classes/Components/PrimitiveComponent.h:2438`; `Runtime/Landscape/Classes/LandscapeProxy.h:927`.
**Export**: `LineTraceMultiByChannel` — UWorld is `ENGINE_API` surface already linked (the handler calls it today); `FCollisionResponseParams` — header struct, `DefaultResponseParam` is `ENGINE_API` (`CollisionQueryParams.h:257`); `GetCollisionResponseToChannel` — `ENGINE_API` method-level; `GetHeightAtLocation` — `LANDSCAPE_API` method-level. | **Module**: none — Engine and Landscape already in MifBridge.Build.cs. | **Guards**: none new (editor-only module).
**Bucket**: transacted (unchanged) — moves actors under the blanket `FScopedTransaction`; it is in neither the ReadOnly nor SelfManaged set (`MifBridgeCommon.cpp:241-308`) and should stay that way (undoable actor moves are exactly what the blanket transaction is for).
**Async**: no — synchronous scene queries, one frame.
**Behaviour change (the fix)**:
1. **Penetrating trace**: pass `FCollisionResponseParams(ECR_Overlap)` as the trace's ResponseParam. Every shape's interaction becomes `Min(shape, Overlap) = Touch` (`CollisionQueryFilterCallback.cpp:76`), so no hit terminates the ray and `OutHits` contains **every** WorldStatic-interacting component along the segment, sorted near→far (`World.h:1955`). The ground scan then works as its author intended. NB: hits now have `bBlockingHit=false` — the scan must not filter on it.
2. **Ground rules unchanged** (the mandated part): default = first hit whose actor `IsA<ALandscapeProxy>()`; `groundActor` nominates by label/name/path as today. `allowAnyHit` preserves its old meaning exactly by taking the first hit whose component `GetCollisionResponseToChannel(ECC_WorldStatic) == ECR_Block` — the same component the old single-blocking trace would have stopped at.
3. **Per-actor diagnosis echo** — for every MISS always, and for every snap when `debug:true`: `{actor, startZ, endZ, hitCount, groundHit:{label, class, z}|null, firstBlocker:{label, class, z}|null, reason}` where `firstBlocker` is the nearest would-block hit (what the OLD code stopped at — it names the shadowing road/prop) and `reason ∈ {"no hits at all below", "hits but none matched ground rule", "hit list truncated"}`. Top level echoes `channel:"WorldStatic"` and `traceHeight`. This is what turns "112 missed" from a mystery into a work list.
4. **Cap**: `maxHits` per actor (default 64, clamp 8..256). If the array fills without a ground match, report `reason:"hit list truncated"` rather than "missed" — never let a cap masquerade as absence (house precedent: `droppedLines`, `MifBridgePIE.cpp:196`).
5. `debug:true` additionally cross-checks the landscape via `ALandscapeProxy::GetHeightAtLocation(FVector(X,Y,0))` on the hit proxy and echoes `landscapeZ` — a trace-independent second opinion on the height (heightfield read, `LandscapeCollision.cpp:2993-3010`).
**Params**: existing (`actorPaths[]`, `folder`, `labelContains`, `all`, `offset`, `alignToNormal`, `traceHeight`, `groundActor`/`ground`, `allowAnyHit`) unchanged, plus:
| name | aliases | type | default | required |
| maxHits | — | int | 64 (clamp 8..256) | no |
| debug | verbose | bool | false | no |
Unrecognised parameter ⇒ error naming it. Empty selector set still errors (`MifBridgeWorld.cpp:388` behaviour kept).
**Failure modes**:
- No editor world ⇒ `"no editor world"` (unchanged).
- Miss with `hitCount==0` ⇒ actor is outside all collision below (off-landscape XY, collision-less imported meshes, or landscape hole) — echoed as such; message: `"no collision found under <label> between Z=<start> and Z=<end> — check landscape extent/holes or pass a larger traceHeight"`.
- Miss with hits but no landscape ⇒ echo `firstBlocker` and message `"only non-landscape geometry under <label> (nearest: <blocker>) — pass groundActor to nominate it, or allowAnyHit"`.
- Very dense stacks exceeding `maxHits` ⇒ `reason:"hit list truncated"` + hint to raise `maxHits`.
**Cooked**: works — heightfield and static-mesh collision of pak-mounted content is live in the editor world (the 191 successful snaps prove it); no asset data is read.
**Verify** (numbers; all composable from existing endpoints):
1. `new_level` → `create_landscape` (flat, component grid, surface at Z=0) → `spawn_many` 12 cube actors on a grid at Z=5000 → `spawn_actor_in_level` one StaticMeshActor "Plate" (cube scaled 30×30×0.2) at Z=300 spanning 6 of the 12 columns.
2. **Defect reproduction (pre-fix build)**: `snap_actors_to_ground {labelContains:"Cube"}` ⇒ expect `snapped:6, missed:6` — every plate-shadowed cube misses. This is the 112/303 mechanism in miniature; it is the numeric repro the doc asked for.
3. **Post-fix**: same call ⇒ `snapped:12, missed:0`; each previously-missed cube's echo (debug:true) shows `groundHit.class` containing `LandscapeStreamingProxy|Landscape` and `firstBlocker.label=="Plate"`.
4. Per-cube assertion via `get_actor_bounds`: `|(origin.z − extent.z) − 0| ≤ 1.0` for 12/12 (bounds bottom sits on the landscape surface; `offset` 0).
5. `alignToNormal` unaffected: flat landscape ⇒ post-snap actor rotation pitch/roll within 0.1° of pre-snap values.
6. Field test on the real map: re-run the original 303-actor snap; assert `missed` collapses to only entries whose echo says `hitCount==0` (genuinely off-ground actors) — i.e., every residual miss is explained, none is silent.
**Score**: U4 E4 R4 → tier 0 — closes Phase-4 item 3; prevents the documented failure "112 unexplained misses on flat ground".

---

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

1. **`GEditor->PlayWorld` is not a session-liveness authority and never was.** _[Phase-2: CORRECTED — conclusion stands, supporting mechanism fixed.]_ It is a per-tick scratch pointer: reassigned to each ticked PIE context in turn (`EditorEngine.cpp:1867`), left on the LAST PIE context by the render loop (`EditorEngine.cpp:2164`), skipped for null-world contexts (`:1845`), gated behind Slate throttling (`:1838`, `:2154`) — all re-verified. The phase-1 GC claim was wrong: `PlayWorld` **is** among `FWorldContext::ExternalReferences` — every in-process PIE context ties it via `UGameInstance::InitializeForPlayInEditor` (`GameInstance.cpp:350`; full engine `AddRef` caller list: `GameInstance.cpp:350`, `LevelEditorViewport.cpp:5134`, `PreviewScene.cpp:59`, `SActorEditorContext.cpp:20`, `LevelEditorSubsystem.cpp:778`, `GameViewportClient.cpp:476`). That makes things worse, not better: `SetCurrentWorld`'s conditional retarget (`UnrealEngine.cpp:1445-1451`) means ANY travelling context nulls or re-points the shared pointer when it happens to match, and leaves it stale when it doesn't — one more last-writer-wins site on top of the tick/render loops. Any endpoint equating `PlayWorld==null` with "not playing" is structurally wrong. (`list_pie_actors` still does — repair rides along with the pie_status fix.)
2. **Out-of-process play sessions are unobservable in-process by engine design.** _[Phase-2: CONFIRMED — both citations re-read verbatim.]_ `UEditorEngine::StartPlayInNewProcessSession` calls `CancelRequestPlaySession()` immediately after spawning the processes (`PlayLevelNewProcess.cpp:57-59`), wiping `PlaySessionRequest` AND `PlayInEditorSessionInfo` (`PlayLevel.cpp:980-985`). A user playing a "Standalone Game" session will always read as `stopped`. No fix is possible without process enumeration; documented in the endpoint note instead. The bridge must never launch `NewProcess`/`Launcher` destinations if it wants observable sessions.
3. **The binary that produced the recorded `state:"stopped"` is unrecoverable.** _[Phase-2: CORRECTED — repo framing was wrong, conclusion stands.]_ The repo does NOT start at `f94b88c` (it has 17 commits from `89dda64`); but `git grep pie_status` at `b97d7eb` (the commit before `f94b88c`) finds nothing, `git log -S WritePieStateInto` first hits at `f94b88c`, and that first version already contains the queued-aware derivation — so no in-repo revision ever derived state purely from `PlayWorld`, and the misreporting build predates the repo's PIE code. The forensics above prove which engine windows make ANY `PlayWorld`-based derivation report non-running mid-session (mid-`LoadMap` null, entry-world client-connect frames, multi-instance last-writer/staleness, throttle-gated re-pointing), and the repro in the Verify section triggers the cross-frame windows deterministically on DDS2 (it always travels on play, `MifBridgePIE.cpp:468-471`).
4. **`UEditorEngine::bIsSimulatingInEditor` is deprecated-in-practice.** _[Phase-2: CONFIRMED — all three pragma sites re-read.]_ The member carries a commented-out `UE_DEPRECATED(4.25,...)` and engine writes are wrapped in deprecation pragmas (`EditorEngine.h:3229-3232`, `PlayLevel.cpp:650-653`, `:2928-2931`). Use `IsSimulatingInEditor()` (`EditorEngine.h:1711`).
5. **`LineTraceMultiByChannel` cannot see past the first blocking hit** (`World.h:1956`). _[Phase-2: CONFIRMED — grep re-run: the only multi-trace in the plugin is `MifBridgeWorld.cpp:426` (the snap handler); single-channel traces at `MifBridgeSpatial.cpp:200` (`trace_ground`) and `MifBridgeWorld.cpp:274` use first-blocking semantics correctly.]_ Any "scan the multi-trace hit list for X" design over a default-response channel trace is structurally broken whenever X can be occluded — the pattern to avoid anywhere else it appears in the bridge.
6. **`ULandscapeHeightfieldCollisionComponent::GetHeight` is not linkable from MifBridge.** _[Phase-2: CONFIRMED — `UCLASS(MinimalAPI, Within=LandscapeProxy)` at `:39`, unexported `GetHeight` at `:316`, `LANDSCAPE_API GetHeightAtLocation` at `LandscapeProxy.h:927`, all re-read verbatim.]_ The class is `UCLASS(MinimalAPI)` (`LandscapeHeightfieldCollisionComponent.h:39`) and the method has no export macro (`:316`). The exported equivalent is `ALandscapeProxy::GetHeightAtLocation` (`LANDSCAPE_API`, method-level, `LandscapeProxy.h:927`) — same precedent class as the documented `UpdateCollisionData` → `RecreateCollisionComponents` case.

## UNVERIFIED

- Whether DDS2's IslaSombra→OpenWorld transition is seamless or non-seamless (cooked game code; not inspectable). Both paths produce a world-swap the repaired `travelling` predicate detects (`UnrealEngine.cpp:14899-15099` intra-frame for LoadMap; `PendingNetGame`/`SeamlessTravelHandler` across frames for the others), so the repair does not depend on which — but see the next bullet for which windows are provably poll-observable.
- Whether game-thread TaskGraph tasks (the bridge's dispatch) can be pumped INSIDE a blocking `LoadMap`. _[Phase-2: this is now LOAD-BEARING for the single-instance travel sub-case — the phase-1 "post-GC remainder-of-frame window" does not exist (SetCurrentWorld re-points the AddRef-tied `PlayWorld` synchronously at `UnrealEngine.cpp:15099` before LoadMap returns). The cross-frame repro windows that remain proven: the entry-world client-connect phase (`GameInstance.cpp:313-317`) and multi-instance staleness/last-writer (`UnrealEngine.cpp:1445-1451`, `EditorEngine.cpp:2164`). Verify step 2's "world name changes between two running samples" arm covers single-instance travel without needing mid-LoadMap observability; a `travelling` sample in that scenario is possible but not guaranteed.]_
- Whether a PIE client's entry world reports `HasBegunPlay()==true` during the connect phase (would make today's code read `running` against an empty placeholder world rather than `starting`). Either way the repaired ordering reports `travelling` first (`PendingNetGame != nullptr` outranks `running` in the first-match-wins chain).
- Landscape visibility-mask holes as a contributor to the 112 misses (H3) — needs the repaired per-actor echo on the real map to quantify.
- PIEInstance numbering convention per topology (which index the dedicated server takes) — the repaired payload reports raw `PIEInstance` values rather than assuming an order.
- The composition of the original 112-miss population (roads vs props vs off-landscape) — verification step 6 measures it; not claimable in advance.

## Coverage log

Done: both Phase-4 defects root-caused against engine source with verbatim citations; two repair entries specced with all ten fields + scores; live pie_status/self_audit probes recorded (self_audit now reports 160 endpoints — the brief's "156 live" note is stale); plugin git history checked for the pre-fix binary (not recoverable); transaction buckets of both endpoints confirmed against `MifBridgeCommon.cpp`.
Phase-2 verification (2026-07-26): every engine and plugin citation re-opened; verdicts stamped (pie_status CORRECTED — Hole A mechanism; snap_actors_to_ground CONFIRMED); negative results 1 and 3 corrected, 2/4/5/6 confirmed; hazard hunt over the repair call paths found no modal/blocking/GC calls (both repairs use inline accessors + synchronous scene queries only). Engine-side notes from the hunt, pre-existing and untouched by the repairs: PIE startup on the engine's own tick can open a modal `FMessageDialog::Open` if instance init fails (`PlayLevel.cpp:2960`) and runs `CollectGarbage` per duplicated instance (`GameInstance.cpp:354-357`) — neither runs in a bridge handler stack since `start_pie` only queues.
Not covered (out of axis scope): Phase-4 items 2 (walking NPCs), 4 (material graph authoring), 5 (graph auto-layout); no changes proposed to `start_pie`/`stop_pie` beyond reusing the repaired state derivation in error messages; no live PIE start was performed (mutating — prohibited for this agent).
