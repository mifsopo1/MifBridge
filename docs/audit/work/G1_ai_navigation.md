# Axis G1 — AI, navigation, and NPC routing
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._

## Surface inventory

**Live editor bridge**: DOWN for the entire sweep. `curl http://127.0.0.1:8791/api/pie_status`
returned connection-refused (exit 7) on every attempt, retried repeatedly across the sweep
(start, middle, and immediately before writing this file); `tasklist` showed no
UnrealEditor.exe (only UnrealTraceServer.exe); netstat showed fresh TIME_WAIT client sockets to
:8791, i.e. the bridge served requests shortly before this sweep started and then the editor went
away. Every live `describe_class`/`find_assets` planned for this axis is queued in the Coverage
log for Phase-2. The project investigation below was done from the **cooked AssetRegistry.bin**
(D:/DDS2SDK/Game/AssetRegistry.bin, 8.9 MB, string-mined for `/Game/...` package paths), the SDK
C++ stubs (D:/DDS2SDK/Game/Source), loose Content, and MifBridge plugin source.

**Plugin files read** (D:/DDS2SDK/Game/Plugins/MifBridge):
- `Source/MifBridge/Private/MifBridgeNavigation.cpp` — all 4 existing nav handlers read in full
  (add_nav_volume, build_navmesh, nav_status, move_actor_to; the brush-scale trick, NavWorld()
  PIE-preference helper, and the "zero tiles builds successfully" warning pattern).
- `Source/MifBridge/Private/MifBridgePIE.cpp` (lines 1–120) — async request/poll doctrine,
  GetPIEWorld/CollectPIEWorlds, multi-world netMode reporting.
- `Source/MifBridge/Private/MifBridgeWorld.cpp` (lines 1–60, 220) + `MifBridgeHandlers.h:307` +
  `tools/ue5-mcp-bridge/server.py:985` — the spline story: set_spline_points exists BECAUSE
  BP_SegmentedPathTaskMarker.PathSpline is what walks NPCs in the shipped game.
- `Source/MifBridge/Private/MifBridgeUserTypes.cpp` (lines 215–260) — asset-creation precedent:
  package + object + `FAssetRegistryModule::AssetCreated` + `MarkPackageDirty`, no factory.
- `Source/MifBridge/MifBridge.Build.cs` — NavigationSystem and AIModule ALREADY linked (lines
  38–39). Nearly every proposal below is "none — already linked".

**Engine headers read** (paths relative to D:/UE532/Engine/Source):
- Runtime/NavigationSystem/Public/`NavigationSystem.h` (lines 290–529 read), `NavigationPath.h`
  (full), `NavigationData.h` (FNavigationPath region), `NavModifierVolume.h`,
  NavAreas/`NavArea.h` + directory listing (4 stock area classes + meta), NavMesh/`RecastNavMesh.h`
  (class decl 635, generation properties 740–775, tile APIs 1095–1371).
- Runtime/AIModule/Classes/`AIController.h` (92–269, 386–444), Navigation/`PathFollowingComponent.h`
  (status enums 33–70, 140–200; component APIs 215–420), `AITypes.h` (FAIMoveRequest 493–552),
  Blueprint/`AIBlueprintHelperLibrary.h`, BehaviorTree/`BlackboardData.h` (full to line 119),
  BehaviorTree/`BehaviorTree.h`, `BTCompositeNode.h`, Tasks/`BTTask_MoveTo.h`,
  EnvironmentQuery/`EnvQueryManager.h`, `EnvQueryInstanceBlueprintWrapper.h` (full),
  `EnvQueryTypes.h` (FEnvQueryResult 514–584), Navigation/`NavLinkProxy.h` (full),
  Navigation/`CrowdFollowingComponent.h` (class decl), Perception/`AIPerceptionComponent.h`
  (201–380).
- Runtime/Engine/Classes/AI/Navigation/`NavLinkDefinition.h` (1–150), GameFramework/`Controller.h`
  (Possess region), GameFramework/`Pawn.h` (SpawnDefaultController region).
- Editor/BehaviorTreeEditor: `Classes/` directory listing (19 headers), `BehaviorTreeFactory.h`
  (full), `BlackboardDataFactory.h` (full), `BehaviorTreeGraph.h` + `BehaviorTreeGraphNode.h`
  (class decls), `Public/BehaviorTreeEditor.h` (class decl).
- Plugins: AI/ directory listing (AISupport, EnvironmentQueryEditor, HTNPlanner, MLAdapter,
  MassAI, MassCrowd); `EnvironmentQueryEditor.uplugin` (EnabledByDefault:true, module type
  UncookedOnly), its `EnvironmentQueryFactory.h`; `MassCrowd.uplugin` + `MassAI.uplugin`
  (both EnabledByDefault:false).

**Project artifacts read**: `Source/DrugDealerSimulator2/Public/BaseNPC.h` (full — SDK stub),
`TownData.h` (population DataTable properties), loose Content sweep of
`Content/Blueprints/**` (8 loose .uassets, incl. `NPC/IslaSombra/ISL/BP_BaseNPC_Editable.uasset`).

---

## Tier-0 investigation: what ACTUALLY moves NPCs in DDS2

Mission item: "nothing currently makes NPCs walk a route — investigate what actually drives pawn
movement in this project and what an endpoint would need to expose."

### Finding: four distinct movement subsystems, all riding the stock AIModule stack

**1. The wander/route system the shipped game uses for walking NPCs — spline markers.**
`/Game/Blueprints/Enviro/Markers/BP_SegmentedPathTaskMarker` (cooked BPGC, confirmed in
AssetRegistry.bin; sibling `BP_WorldTaskMarker` in the same folder) owns a `PathSpline`
USplineComponent that NPC routing follows. This is not conjecture — it is the documented reason
`set_spline_points` exists (MifBridgeWorld.cpp:10 "The shipped game walks its NPCs along
BP_SegmentedPathTaskMarker, whose PathSpline is a USplineComponent", MifBridgeHandlers.h:307,
server.py:985). There is also an editor billboard sprite `bill_NPCMovePath` in /Game/Billboards —
i.e. the game has a dedicated placed-in-level "NPC move path" marker actor family.

**2. Combat/opponent NPCs — a full BehaviorTree stack.** From the cooked registry, all under
`/Game/Blueprints/Pawns/NPC/Oponents/`:
- Pawn: `BP_NPC_OponentBase` (+ `NPC_OponentThug`, etc.), anim `OponentAnimBP`.
- Controller: `BP_OponentAIController` (AIController subclass).
- Blackboards: `Behaviour/OponentBB`, `Behaviour/TimmyBB`.
- BehaviorTrees: `OponentCivilian_BT`, `OponentMilitia_Raid_BT`, `OponentMuscle_BT`,
  `OponentThug_BT`, `TestBT`, and the subtree `SharedBT/SharedBT_Patrol`.
- ~25 Blueprint BT tasks: `BP_TaskBeginPatrol`, `BP_TaskConsiderPatrol`, `BP_TaskFindPatrolRoute`,
  `BP_TaskGetPatrolLocation`, `BP_TaskMoveToCustom`, `BP_TaskChopperMoveTo`,
  `BP_TaskExecuteLostPath`, `BP_TaskTeleportToSpawn`, `BP_TaskSetPawnAsLocation`, …
- Services: `BP_ServiceUpdatePatrolLoc`, `BP_ServiceCheckPlayerClose`,
  `BP_ServiceGetFormationLocation`, …; Decorators folder present.
- EQS: `Behaviour/EQ/EQ_FindMeleeSpot`, `EQ_FindObservePoint`, `EQ_FindRetreatSpot`,
  `EQ_FindChopperSearch…`, context `EQ/Context/EQC_GetLastSight`.
- Patrol DATA is placed actors: `Behaviour/BP_OponentPatrolRoute` and
  `Behaviour/BP_OponentRestPoint` (+ billboard `bill_OponentRestSpot`) — a task
  (`BP_TaskFindPatrolRoute`) locates a route actor, `BP_TaskGetPatrolLocation` reads successive
  locations from it, movement itself is an AI MoveTo.

**3. Quest/scripted NPCs — walk-path helper actors.** `/Game/Blueprints/Pawns/NPC/NPCHelpers/`:
`BP_QuestNPCWalkPath`, `BP_WalkPathAccountant`, `BP_WalkPathVolcanoGuide`,
`Behavior/PathFollowerBossJam`; plus `/Game/Blueprints/NPC/IslaSombra/VOLCANO/NPC_GuidePathFollow`.
Base pawn: `/Game/Blueprints/Pawns/NPC/BP_BaseNPC` (cooked; the loose editable child
`BP_BaseNPC_Editable.uasset` exists in Content/Blueprints/NPC/IslaSombra/ISL/).

**4. Ambient population — mostly STATIC.** `/Game/Blueprints/Enviro/Population/Humans/` contains
`BP_EnviroHumanStatic`, `BP_EnviroHumanBeggar`, `RandomClientSpawnPoint` (+ Animals/, Parties/
subfolders, billboards `bill_PopulationHuman`/`bill_PopulationAnimal`). The names say it: the
"living city" ambience is largely stationary humans plus spawn points for clients; there is no
evidence of a streaming crowd/flow-field system. Population VARIETY is DataTable-driven
(SDK stub `TownData.h:104-116`: `PopulationClothPresets`, `PopulationFacePool`,
`PopulationHairPool`, `PopulationFacialHairPool`, `PopulationSpecialMeshPool`). Pets:
`/Game/Blueprints/NPC/Pets/PetDogBT` + `Pets/EQS/EQ_TeleportToMaster`.

**Engine mechanics underneath all four**: `ABaseNPC : public ACharacter`
(D:/DDS2SDK/Game/Source/DrugDealerSimulator2/Public/BaseNPC.h:7 — SDK stub, DRUGDEALERSIMULATOR2_API)
→ every NPC has a UCharacterMovementComponent; every "walk somewhere" is
`AAIController::MoveTo*` → `UPathFollowingComponent` → CharacterMovement on the recast navmesh.
No Mass/crowd plugins: MassAI and MassCrowd are `EnabledByDefault:false` in their .uplugins and
absent from the .uproject plugin list. Whether `BP_OponentAIController` swaps in
`UCrowdFollowingComponent` (AIModule, MinimalAPI — Navigation/CrowdFollowingComponent.h:37-38)
for detour-crowd avoidance is NOT determinable from the registry — Phase-2 `describe_class` item.

### What an endpoint therefore needs to expose

The engine mechanism is completely stock, so the bridge does not need to reproduce any DDS2
system to make NPCs walk — it needs four primitives, of which the project has zero today at
PIE time beyond fire-and-forget `move_actor_to`:
1. **Issue a move with an ID and acceptance radius** and **poll it** (`pie_move_pawn` +
   `pie_move_status` below). `move_actor_to` uses `SimpleMoveToLocation` which returns void —
   no request id, no acceptance radius, no completion signal, single destination only.
2. **Multi-point routes** — FAIMoveRequest holds exactly ONE goal (`SetGoalLocation(const
   FVector&)`, AITypes.h:547); there is no engine-side chaining. Leg sequencing must live in the
   plugin (design decision + justification in pie_move_pawn below).
3. **A numeric ground truth for "can it even get there"** — `find_path` below; this is the
   verification story for every routing endpoint AND for navmesh/link/modifier mutations.
4. **Game-native route authoring already exists as a composition**: spawn the cooked marker class
   + `set_spline_points` + `snap_actors_to_ground` (documented in Compositions). What is missing
   is making an arbitrary AI pawn follow that spline in PIE without the game's task system —
   closed by `pie_move_pawn` accepting a `route` array sampled from `get_spline_points`.

---

## Proposed endpoints

### find_path
**Purpose**: synchronous point-to-point pathfinding returning path points + length + partial
flag — the numeric verification primitive for ALL routing, navmesh, nav-link, and nav-modifier
work (mutation endpoints elsewhere in this file cite it as their proof method).
**Engine API**:
```cpp
/** Finds path instantly, in a FindPath Synchronously.
 *	@param PathfindingContext could be one of following: NavigationData (like Navmesh actor), Pawn or Controller. This parameter determines parameters of specific pathfinding query */
UFUNCTION(BlueprintCallable, Category = "AI|Navigation", meta = (WorldContext="WorldContextObject"))
static NAVIGATIONSYSTEM_API UNavigationPath* FindPathToLocationSynchronously(UObject* WorldContextObject, const FVector& PathStart, const FVector& PathEnd, AActor* PathfindingContext = NULL, TSubclassOf<UNavigationQueryFilter> FilterClass = NULL);
```
Runtime/NavigationSystem/Public/NavigationSystem.h:506. Result object:
```cpp
UPROPERTY(BlueprintReadOnly, Category = Navigation)
TArray<FVector> PathPoints;                                  // NavigationPath.h:29-30
UFUNCTION(BlueprintCallable, Category = "AI|Navigation")
NAVIGATIONSYSTEM_API double GetPathLength() const;           // NavigationPath.h:64-65
UFUNCTION(BlueprintCallable, Category = "AI|Navigation")
NAVIGATIONSYSTEM_API double GetPathCost() const;             // NavigationPath.h:67-68
UFUNCTION(BlueprintCallable, Category = "AI|Navigation")
NAVIGATIONSYSTEM_API bool IsPartial() const;                 // NavigationPath.h:70-71
UFUNCTION(BlueprintCallable, Category = "AI|Navigation")
NAVIGATIONSYSTEM_API bool IsValid() const;                   // NavigationPath.h:73-74
```
Runtime/NavigationSystem/Public/NavigationPath.h (UNavigationPath is `UCLASS(BlueprintType,
MinimalAPI)` at :21 with method-level NAVIGATIONSYSTEM_API — all methods above callable+linkable).
**Export**: NAVIGATIONSYSTEM_API (method-level on both classes) | **Module**: none — NavigationSystem already linked (MifBridge.Build.cs:38) | **Guards**: none
**Bucket**: read-only — pure query; the returned UNavigationPath is transient and not kept.
**Async**: no — "Synchronously" is the contract; recast A* on built tiles is sub-millisecond at DDS2 scales.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| start | from | {x,y,z} | — | yes (strict: missing ⇒ error naming `start`) |
| end | to, goal | {x,y,z} | — | yes |
| world | — | string: `auto`\|`pie`\|`editor` | auto (PIE if running, else editor — matches NavWorld() in MifBridgeNavigation.cpp:30) | no |
| contextActor | actor | string actor path/label | none | no (resolves agent properties from that pawn/controller) |
Unrecognised parameter ⇒ error naming it.
**Failure modes**:
- No nav system / zero tiles ⇒ `"no navigable data in <world> — run add_nav_volume + build_navmesh, then nav_status until ready=true"` (distinct from partial-path, which is a SUCCESS with partial=true).
- Result null or !IsValid() ⇒ `"pathfinding failed: start or end could not be projected to the navmesh (nearest tile > cell extent); try project_to_navmesh on both points first"`.
- `contextActor` not found ⇒ `"contextActor '<q>' not found in <world> world"`.
**Cooked**: works — pathfinding consumes the in-memory navmesh of whatever map is loaded, including cooked base-game maps whose navmesh shipped in the .pak. (Newly BUILT nav on a cooked map cannot be saved with the map — session-only; that caveat belongs to build_navmesh, not here.)
**Verify**: flat 20000×20000 navmesh, start=(0,0,z), end=(1000,0,z): `length` within 5% of 1000, `partial=false`, PathPoints.Num()>=2. Spawn a 500-unit-wide blocking box mid-line (spawn_actor_in_level), rebuild, re-query: `length` > 1414 (detour) or `partial=true`. Numbers, no pixels.
**Score**: U5 E1 R5 → tier 0 — the verification story for the whole axis; prevents the documented "nav build reports success with zero tiles" class of silent failure from propagating into routing work.
**Phase-2 verdict**: CONFIRMED — all signatures re-read verbatim (NavigationSystem.h:503-506; NavigationPath.h:21, :29-30, :64-74, all method-level NAVIGATIONSYSTEM_API). Implementation checked for hidden hazards: UNavigationSystemV1::FindPathToLocationSynchronously (NavigationSystem.cpp:1907-1926) is a straight sync query, no modal/blocking calls; the result UNavigationPath is NewObject'd with the nav system as outer and consumed within the same game-thread slice — no GC window.

### project_to_navmesh
**Purpose**: snap an arbitrary world point onto the navmesh (or report that it cannot be) —
the pre-flight check that turns "pawn refuses to move" mysteries into numbers.
**Engine API**:
```cpp
UFUNCTION(BlueprintPure, Category = "AI|Navigation", meta = (WorldContext = "WorldContextObject", DisplayName = "ProjectPointToNavigation", ScriptName = "ProjectPointToNavigation"))
static NAVIGATIONSYSTEM_API bool K2_ProjectPointToNavigation(UObject* WorldContextObject, const FVector& Point, FVector& ProjectedLocation, ANavigationData* NavData, TSubclassOf<UNavigationQueryFilter> FilterClass, const FVector QueryExtent = FVector::ZeroVector);
```
Runtime/NavigationSystem/Public/NavigationSystem.h:468-469.
**Export**: NAVIGATIONSYSTEM_API | **Module**: none — already linked | **Guards**: none
**Bucket**: read-only — pure query.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| point | location | {x,y,z} | — | yes |
| extent | queryExtent | {x,y,z} | engine default (zero ⇒ nav system default extent) | no |
| world | — | `auto`\|`pie`\|`editor` | auto | no |
Unrecognised ⇒ error.
**Failure modes**: returns `{projected:false}` (NOT an HTTP error) when no polygon within extent — with note `"no navmesh within extent {…} of point; increase extent or check nav_status tiles>0"`. No nav system ⇒ same error text as find_path.
**Cooked**: works (same reasoning as find_path).
**Verify**: point 300 units above known-walkable ground ⇒ projected=true and |projected.z − ground.z| < agent height; point 50000 units outside all bounds volumes ⇒ projected=false. Pair with trace_ground to get the ground truth Z.
**Score**: U3 E1 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — signature verbatim at NavigationSystem.h:468-469 (re-read); pure query, no hazards.

### random_reachable_point
**Purpose**: generate a random navmesh location reachable from an origin — the primitive for
"scatter N ambient NPCs and have them wander" without hand-authoring every destination.
**Engine API**:
```cpp
UFUNCTION(BlueprintPure, Category = "AI|Navigation", meta = (WorldContext = "WorldContextObject", DisplayName = "GetRandomReachablePointInRadius", ScriptName = "GetRandomReachablePointInRadius"))
static NAVIGATIONSYSTEM_API bool K2_GetRandomReachablePointInRadius(UObject* WorldContextObject, const FVector& Origin, FVector& RandomLocation, float Radius, ANavigationData* NavData = NULL, TSubclassOf<UNavigationQueryFilter> FilterClass = NULL);
```
Runtime/NavigationSystem/Public/NavigationSystem.h:473-474.
**Export**: NAVIGATIONSYSTEM_API | **Module**: none — already linked | **Guards**: none
**Bucket**: read-only.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| origin | center, location | {x,y,z} | — | yes |
| radius | — | number > 0 | — | yes (0/negative ⇒ error naming `radius`) |
| count | — | int 1..100 | 1 | no (loops the call; each independent) |
| world | — | `auto`\|`pie`\|`editor` | auto | no |
**Failure modes**: origin unprojectable ⇒ `{points:[]}` + note `"origin is not on/near the navmesh — project_to_navmesh it first"`; per-point failures reported as `requested` vs `returned` counts (never silently short).
**Cooked**: works.
**Verify**: request count=20 radius=3000 from a navmesh point: `returned==20`, every point satisfies |p−origin| ≤ 3000 (2D), and find_path(origin→p) returns partial=false for each (reachability is the function's contract — spot-check 3).
**Score**: U3 E1 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — signature verbatim at NavigationSystem.h:473-474 (re-read); pure query, no hazards.

### nav_raycast
**Purpose**: 2D navigable-space raycast — "is there a straight walkable line from A to B" —
distinguishes 'detour needed' from 'unreachable' with one cheap number.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category="AI|Navigation", meta=(WorldContext="WorldContextObject" ))
static NAVIGATIONSYSTEM_API bool NavigationRaycast(UObject* WorldContextObject, const FVector& RayStart, const FVector& RayEnd, FVector& HitLocation, TSubclassOf<UNavigationQueryFilter> FilterClass = NULL, AController* Querier = NULL);
```
Runtime/NavigationSystem/Public/NavigationSystem.h:518-519 (returns true when OBSTRUCTED; HitLocation = obstruction point, else RayEnd — comment at :514-517 read).
**Export**: NAVIGATIONSYSTEM_API | **Module**: none — already linked | **Guards**: none
**Bucket**: read-only. **Async**: no.
**Params**: | start | from | {x,y,z} | — | yes | / | end | to | {x,y,z} | — | yes | / | world | — | enum as above | auto | no |. Unrecognised ⇒ error.
**Failure modes**: no nav data ⇒ engine returns obstructed=true with HitLocation=start; the handler must detect tiles==0 first and error explicitly (`"nav_raycast with no navmesh always reports obstructed — build first"`) rather than return a misleading true.
**Cooked**: works.
**Verify**: clear line: obstructed=false, hit==end (exact). Line through the blocking box from the find_path test: obstructed=true, |hit−start| < |end−start|, and hit is within box XY bounds ±cell size.
**Score**: U2 E1 R5 → tier 2 — cheap but genuinely additive (find_path can't distinguish "detour" from "no straight line" without geometry math client-side).
**Phase-2 verdict**: CONFIRMED — signature verbatim at NavigationSystem.h:518-519; the header comment ":517 'Also, true when no navigation data present'" re-read — the tiles==0 pre-check in Failure modes is load-bearing, keep it.

### navmesh_tile_info
**Purpose**: per-tile poly counts and bounds — turns nav_status's single `tiles` number into a
spatial map, so "the navmesh built but half the town is empty" becomes visible as numbers.
**Engine API**:
```cpp
NAVIGATIONSYSTEM_API FBox GetNavMeshTileBounds(int32 TileIndex) const;             // RecastNavMesh.h:1123
NAVIGATIONSYSTEM_API bool GetNavMeshTileXY(int32 TileIndex, int32& OutX, int32& OutY, int32& Layer) const; // RecastNavMesh.h:1126
NAVIGATIONSYSTEM_API int32 GetNavMeshTilesCount() const;                           // RecastNavMesh.h:1141
NAVIGATIONSYSTEM_API bool GetPolysInTile(int32 TileIndex, TArray<FNavPoly>& Polys) const; // RecastNavMesh.h:1371
NAVIGATIONSYSTEM_API float GetTileSizeUU() const;                                  // RecastNavMesh.h:1114
```
All on `ARecastNavMesh` — `UCLASS(config=Engine, defaultconfig, …, notplaceable, MinimalAPI)` at
RecastNavMesh.h:635-636; method-level NAVIGATIONSYSTEM_API throughout (verified per method above).
`FNavPoly` struct at :293. Precedent: nav_status already calls GetNavMeshTilesCount
(MifBridgeNavigation.cpp:157).
**Export**: NAVIGATIONSYSTEM_API (method-level) | **Module**: none — already linked | **Guards**: none
**Bucket**: read-only. **Async**: no (iterating ~hundreds of tiles is one frame; cap below keeps it bounded).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| detail | perTile | bool | false | no (false ⇒ summary only: tiles, totalPolys, emptyTiles, tileSizeUU) |
| maxTiles | limit | int 1..2048 | 256 | no (detail mode pagination cap) |
| offset | — | int ≥ 0 | 0 | no |
| world | — | `auto`\|`pie`\|`editor` | auto | no |
**Failure modes**: no ARecastNavMesh in world ⇒ `"no ARecastNavMesh actor — nav has never been built here (add_nav_volume + build_navmesh)"`. TileIndex iteration uses count from the same frame — no TOCTOU across ticks because the handler runs in one game-thread slice.
**Cooked**: works — cooked maps ship tiles; this is exactly how you PROVE the shipped navmesh covers the street you plan to route NPCs down.
**Verify**: after the standard 20000×20000 build: `tiles>0`, `totalPolys>0`, `sum(perTile.polyCount)==totalPolys`, every non-empty tile bounds intersects the bounds volume. After adding a NavArea_Null modifier volume over half the area (endpoint below): totalPolys drops; the delta is the proof the modifier took effect.
**Score**: U4 E2 R5 → tier 1 — directly extends the axis's own precedent that tile COUNT (not "ok") is the honest signal.
**Phase-2 verdict**: CONFIRMED — all five method signatures verbatim (RecastNavMesh.h:1114, :1123, :1126, :1141, :1371, each NAVIGATIONSYSTEM_API); FNavPoly at :293; class MinimalAPI at :635-636. Read-only iteration, no hazards.

### list_nav_areas
**Purpose**: enumerate every UNavArea class loaded (engine stock + any cooked game areas) with
cost numbers, so area-class parameters elsewhere (add_nav_modifier_volume, add_nav_link) take a
validated name instead of a guessed string.
**Engine API**: pure reflection — `TObjectIterator<UClass>` + `IsChildOf(UNavArea::StaticClass())`.
Properties read per class CDO:
```cpp
UCLASS(DefaultToInstanced, abstract, Config=Engine, Blueprintable, MinimalAPI)
class UNavArea : public UNavAreaBase                       // NavArea.h:14-15
	float DefaultCost;                                     // NavArea.h:22
	float FixedAreaEnteringCost;                           // NavArea.h:27
	FColor DrawColor;                                      // NavArea.h:32
```
Runtime/NavigationSystem/Public/NavAreas/NavArea.h. Stock subclasses on disk (directory read):
NavArea_Default, NavArea_LowHeight, NavArea_Null, NavArea_Obstacle, NavAreaMeta,
NavAreaMeta_SwitchByAgent.
**Export**: MinimalAPI class ⇒ StaticClass() linkable; property reads via FProperty reflection (no direct member link needed) | **Module**: none — already linked | **Guards**: none
**Bucket**: read-only. **Async**: no.
**Params**: | includeAbstract | — | bool | false | no |. Unrecognised ⇒ error.
**Failure modes**: none meaningful; empty result impossible (stock areas always registered).
**Cooked**: works — cooked BPGC nav areas (if DDS2 defines any) appear like native classes.
**Verify**: result contains NavArea_Default with DefaultCost==1 and NavArea_Null; count ≥ 4; every entry's class path resolves via describe_class.
**Score**: U2 E1 R5 → tier 1 (enabler for the two mutation endpoints below).
**Phase-2 verdict**: CONFIRMED — NavArea.h:14-15 UCLASS verbatim; DefaultCost :22, FixedAreaEnteringCost :27, DrawColor :32 re-read; NavAreas/ directory re-listed (6 stock headers, matches). Note FixedAreaEnteringCost is `protected` — the proposed FProperty-reflection read is the correct (and only) linkable route; do not attempt a direct member access.

### pie_move_pawn
**Purpose**: issue a tracked, tunable nav move (single destination OR multi-point route) to an
AI-controlled pawn in PIE — the walking-NPC endpoint. Supersedes-but-does-not-replace
move_actor_to (which stays: fire-and-forget SimpleMoveToLocation, no id, no radius, no route).
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "AI|Navigation", Meta = (AdvancedDisplay = "bStopOnOverlap,bCanStrafe,bAllowPartialPath"))
AIMODULE_API EPathFollowingRequestResult::Type MoveToLocation(const FVector& Dest, float AcceptanceRadius = -1, bool bStopOnOverlap = true,
	bool bUsePathfinding = true, bool bProjectDestinationToNavigation = false, bool bCanStrafe = true,
	TSubclassOf<UNavigationQueryFilter> FilterClass = NULL, bool bAllowPartialPath = true);
```
Runtime/AIModule/Classes/AIController.h:192-195. Class is `UCLASS(ClassGroup = AI, BlueprintType,
Blueprintable, MinimalAPI)` :92 — method-level AIMODULE_API, linkable. Supporting:
```cpp
AIMODULE_API FAIRequestID GetCurrentMoveRequestID() const;      // AIController.h:239
static AIMODULE_API AAIController* GetAIController(AActor* ControlledActor); // Blueprint/AIBlueprintHelperLibrary.h:50
namespace EPathFollowingRequestResult { enum Type : int { Failed, AlreadyAtGoal, RequestSuccessful }; } // Navigation/PathFollowingComponent.h:154-160
```
**Route-chaining decision**: `FAIMoveRequest` carries exactly ONE goal —
`AIMODULE_API void SetGoalLocation(const FVector& InGoalLocation);` (AITypes.h:547, struct decl
:493) — there is no engine-side multi-goal chaining, so the alternatives are (a) client-side
sequencing over HTTP or (b) plugin-side leg queue. **Decision: plugin-side queue advanced by an
FTSTicker.** Rationale: (a) costs one HTTP round-trip of standstill per waypoint (visible hitching
at every corner of a patrol route — exactly the artifact this endpoint exists to remove) and dies
if the agent disconnects mid-route; (b) is ~40 lines: `TMap<FObjectKey, FRouteState>` in the
module (weak controller ref, TArray<FVector> legs, index, last FAIRequestID), a ticker that per
frame checks `GetMoveStatus()==EPathFollowingStatus::Idle` (PathFollowingComponent.h:332
`FORCEINLINE EPathFollowingStatus::Type GetStatus() const`) and issues the next MoveToLocation.
No UFUNCTION/UObject needed (rejected ReceiveMoveCompleted dynamic-delegate binding for exactly
that reason — it would force a helper UObject; polling a FORCEINLINE getter from a ticker is
simpler and unkillable). Ticker self-unregisters when the map empties. PIE teardown clears the
map — weak refs make a stale controller a no-op, never a crash.
**Export**: AIMODULE_API | **Module**: none — AIModule already linked (MifBridge.Build.cs:39) | **Guards**: none
**Bucket**: self-managed (no transaction) — mutates PIE runtime state only; editor undo does not
apply to the PlayWorld, and a blanket FScopedTransaction would push a junk entry per move.
**Async**: request+poll — this call returns `{moveId, result}` immediately (the walk itself takes
seconds-minutes); poll `pie_move_status`. Matches the PIE doctrine in MifBridgePIE.cpp:6-15.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| actor | actorPath, pawn | string (path/name/label in PIE world) | — | yes |
| dest | location, to | {x,y,z} | — | exactly one of dest/route |
| route | points, waypoints | [{x,y,z}...] (min 2) | — | exactly one of dest/route |
| acceptanceRadius | radius | number | -1 (engine default) | no |
| projectGoal | projectDestinationToNavigation | bool | true | no |
| allowPartial | allowPartialPath | bool | true | no |
| usePathfinding | — | bool | true | no |
Both dest AND route ⇒ error `"pass either dest or route, not both"`. Unrecognised ⇒ error.
**Failure modes**:
- No PIE ⇒ same text as move_actor_to (MifBridgeNavigation.cpp:183): `"needs a running PIE session — AI controllers only exist at runtime. start_pie first."`
- Actor is not a pawn / pawn has no AIController ⇒ `"'<label>' has no AIController — controller class is '<cls>'; use pie_possess {mode:'ai'} to spawn its default controller, or check AutoPossessAI"` (GetAIController returning null distinguishes player-controlled from uncontrolled).
- MoveToLocation returns Failed ⇒ `{moveId:null, result:"failed"}` + `"move request failed — usually goal unprojectable; run project_to_navmesh on dest"`; AlreadyAtGoal returned honestly as `result:"alreadyAtGoal"`.
- route with <2 points ⇒ error naming `route`.
**Cooked**: works — cooked BPGC pawns/controllers (BP_OponentAIController_C etc.) are live
runtime objects in PIE; nothing here touches assets. NOTE: issuing a manual move to an NPC whose
BehaviorTree is running will have the BT's next MoveTo stomp it — status will show the OTHER
request id; report `requestIdChanged:true` in status rather than pretending. For deterministic
tests use a pawn whose BT is idle (BP_BaseNPC_Editable) or stop the brain (Phase-2:
brain-component pause endpoint candidate).
**Verify**: pie_move_status.distToFinalGoal strictly decreases across >=3 polls at 1 s intervals;
terminal state has status="idle", legIndex==totalLegs, and |pawnLocation - finalGoal| <=
acceptanceRadius + capsule radius. Cross-check pawn location via list_pie_actors.
**Score**: U5 E3 R3 → tier 0 — closes the mission's walking-NPC gap.
**Phase-2 verdict**: CONFIRMED — MoveToLocation verbatim at AIController.h:192-195 (class MinimalAPI :92, method AIMODULE_API); GetCurrentMoveRequestID :239; GetAIController AIBlueprintHelperLibrary.h:50; EPathFollowingRequestResult PathFollowingComponent.h:153-162; FAIMoveRequest single-goal claim re-verified (AITypes.h:493, SetGoalLocation :547 — one GoalLocation member, no chaining). Watch-item resolved: the FTSTicker leg queue COMPLIES with the game-thread/no-blocking invariant — the core ticker fires once per frame on the game thread, each tick is a FORCEINLINE status read plus at most one MoveToLocation call, nothing waits, and weak refs make PIE teardown a no-op. One design refinement for the implementer: `GetStatus()==Idle` alone cannot distinguish a completed leg from an aborted/failed one — gate leg-advance on `DidMoveReachGoal()` (PathFollowingComponent.h:338, FORCEINLINE) and otherwise mark the route failed in status, or a blocked pawn will silently skip legs.

### pie_move_status
**Purpose**: the poll half of pie_move_pawn — numeric progress of a pawn's current move/route.
**Engine API**:
```cpp
AIMODULE_API EPathFollowingStatus::Type GetMoveStatus() const;   // AIController.h:248-249
AIMODULE_API bool HasPartialPath() const;                        // AIController.h:252-253
AIMODULE_API FVector GetImmediateMoveDestination() const;        // AIController.h:256-257
// on UPathFollowingComponent (UCLASS(config=Engine, MinimalAPI) :215-216):
AIMODULE_API FVector::FReal GetRemainingPathCost() const;        // Navigation/PathFollowingComponent.h:327
FORCEINLINE FAIRequestID GetCurrentRequestId() const { return CurrentRequestId; } // :340
FORCEINLINE const FNavPathSharedPtr GetPath() const { return Path; }             // :356
AIMODULE_API FString GetStatusDesc() const;                      // :361
AIMODULE_API FVector GetPathDestination() const;                 // :417
namespace EPathFollowingStatus { enum Type : int { Idle, Waiting, Paused, Moving }; } // :34-49
```
UPathFollowingComponent reached via `UPathFollowingComponent* GetPathFollowingComponent() const`
(AIController.h:440, inline). Remaining DISTANCE (not cost):
`NAVIGATIONSYSTEM_API virtual FVector::FReal GetLengthFromPosition(FVector SegmentStart, uint32 NextPathPointIndex) const;`
(Runtime/NavigationSystem/Public/NavigationData.h:271, on FNavigationPath) — or cheap fallback
|pawn - goal| Euclidean; report BOTH as `remainingPathLength` and `distToFinalGoal`.
**Export**: AIMODULE_API / NAVIGATIONSYSTEM_API method-level; inline getters compile in-place | **Module**: none — already linked | **Guards**: none
**Bucket**: read-only. **Async**: no (this IS the poll).
**Params**: | actor | actorPath, pawn | string | — | yes |. Unrecognised ⇒ error.
**Payload**: `{status:"idle|waiting|paused|moving", statusDesc, requestId, requestIdChanged,
legIndex, totalLegs, pawnLocation:{x,y,z}, immediateDestination:{x,y,z}, finalGoal:{x,y,z},
distToFinalGoal, remainingPathLength, remainingPathCost, partial, routeActive}` — route fields
null when the move was a plain dest or was issued by the game's own BT (still reported honestly).
**Failure modes**: no PIE / actor not found / no AIController — same texts as pie_move_pawn. No
active path ⇒ status idle with nulls, never an error (idle is an answer).
**Cooked**: works (runtime-only reads).
**Verify**: is itself the verifier for pie_move_pawn; self-check: while status=="moving",
remainingPathLength >= distToFinalGoal - 1 (path is never shorter than the straight line).
**Score**: U5 E2 R5 → tier 0 (pairs with pie_move_pawn; useless apart).
**Phase-2 verdict**: CONFIRMED — every citation verbatim (AIController.h:248-257, :440 inline; PathFollowingComponent.h:34-49, :327, :332, :340, :356, :361, :417; NavigationData.h:271). One note: GetPathDestination (:417) carries meta=(DeprecatedFunction) pointing at AIController::GetImmediateMoveDestination — it still links and works from C++ (not UE_DEPRECATED), but the entry already reads GetImmediateMoveDestination (:256-257); prefer that and drop the :417 call if trimming.

### pie_stop_move
**Purpose**: abort a pawn's current move/route cleanly (and clear any plugin-side route queue) —
without this, a bad route means waiting out the walk or tearing down PIE.
**Engine API**:
```cpp
AIMODULE_API virtual void StopMovement() override;               // AIController.h:230
AIMODULE_API bool PauseMove(FAIRequestID RequestToPause);        // AIController.h:224
AIMODULE_API bool ResumeMove(FAIRequestID RequestToResume);      // AIController.h:227
```
**Export**: AIMODULE_API | **Module**: none — already linked | **Guards**: none
**Bucket**: self-managed — PIE runtime state, same reasoning as pie_move_pawn.
**Async**: no (abort is immediate; next pie_move_status shows idle).
**Params**: | actor | actorPath, pawn | string | — | yes | / | mode | — | `stop`\|`pause`\|`resume` | stop | no |. pause/resume use the stored FAIRequestID from the route state; pausing a game-BT-owned move returns `paused:false` honestly (id mismatch). Unrecognised ⇒ error.
**Failure modes**: same resolution errors as pie_move_pawn; mode=resume with no paused request ⇒ `"nothing to resume for '<label>' — no paused moveId held by the bridge"`.
**Cooked**: works.
**Verify**: issue pie_move_pawn (long route), pie_stop_move, then pie_move_status: status=="idle", pawnLocation stable across two polls 1 s apart (delta < 5 units).
**Score**: U3 E1 R4 → tier 1.
**Phase-2 verdict**: CONFIRMED — StopMovement AIController.h:230, PauseMove :224, ResumeMove :227, all verbatim AIMODULE_API. No hazards (immediate, no dialogs, no waits).

### pie_possess
**Purpose**: fix the #1 reason move commands no-op — a pawn with no controller — either by
spawning the pawn's default AI controller, or by handing the player controller the pawn (drive
an NPC in first person for inspection).
**Engine API**:
```cpp
ENGINE_API virtual void Possess(APawn* InPawn) final;            // GameFramework/Controller.h:279
ENGINE_API virtual void UnPossess() final;                       // GameFramework/Controller.h:283
ENGINE_API virtual void SpawnDefaultController();                // GameFramework/Pawn.h:448
ENGINE_API AController* GetController() const;                   // GameFramework/Pawn.h:243
```
AController is `UCLASS(abstract, notplaceable, NotBlueprintable, ..., MinimalAPI)` :39-40; APawn is
MinimalAPI :41-42 — all four methods carry method-level ENGINE_API, linkable.
**Export**: ENGINE_API | **Module**: none — Engine linked | **Guards**: none
**Bucket**: self-managed — PIE runtime possession swap; not undoable, must not enter the transaction system.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| actor | actorPath, pawn | string | — | yes |
| mode | — | `ai`\|`player`\|`release` | ai | no |
mode=ai: if GetController()==null ⇒ SpawnDefaultController(); else no-op reporting existing class.
mode=player: first PIE PlayerController Possess(pawn) (its old pawn is auto-unpossessed by the engine).
mode=release: controller->UnPossess() (pawn goes brainless — stated in response note).
**Failure modes**: pawn's AIControllerClass is None with mode=ai ⇒ `"'<label>' has AIControllerClass=None — set it via set_property on the pawn (path: AIControllerClass) before possessing"`; mode=player with no local PlayerController (dedicated-server world) ⇒ error naming the world's netMode (worlds enumerable per MifBridgePIE.cpp CollectPIEWorlds).
**Cooked**: works — cooked pawns carry their AIControllerClass in the BPGC CDO.
**Verify**: response returns `{controllerClass, wasSpawned}`; pie_move_pawn on the same pawn now returns result:"requestSuccessful" where before it errored; pawn controller class name confirmed via get_property objectPath walk.
**Score**: U4 E2 R3 → tier 2 — needs PIE-teardown care but small surface.
**Phase-2 verdict**: CONFIRMED — Possess Controller.h:279 and UnPossess :283 verbatim (both ENGINE_API, `final`); SpawnDefaultController Pawn.h:448, GetController :243 verbatim; AController UCLASS :39-40, APawn UCLASS :41-42, both MinimalAPI with method-level ENGINE_API as claimed. Note Possess is UFUNCTION BlueprintAuthorityOnly — irrelevant for the direct C++ call. No modal/blocking in the possession path.

### pie_get_perception
**Purpose**: read-only dump of what an AI currently/ever perceives during PIE — turns "the guard
doesn't react" from a video-debugging session into two actor lists and counts.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "AI|Perception")
AIMODULE_API void GetCurrentlyPerceivedActors(TSubclassOf<UAISense> SenseToUse, TArray<AActor*>& OutActors) const; // Perception/AIPerceptionComponent.h:351-352
UFUNCTION(BlueprintCallable, Category = "AI|Perception")
AIMODULE_API void GetKnownPerceivedActors(TSubclassOf<UAISense> SenseToUse, TArray<AActor*>& OutActors) const;     // :355-356
UFUNCTION(BlueprintCallable, Category = "AI|Perception")
AIMODULE_API void GetPerceivedHostileActors(TArray<AActor*>& OutActors) const;                                     // :344-345
```
UAIPerceptionComponent is `UCLASS(..., config=Game, MinimalAPI)` :201-202, method-level AIMODULE_API.
Component reached via `UAIPerceptionComponent* GetAIPerceptionComponent()` (AIController.h:442,
inline) with fallback `pawn->FindComponentByClass<UAIPerceptionComponent>()` (perception may sit
on the pawn instead of the controller — both checked, which one hit is reported).
**Export**: AIMODULE_API | **Module**: none — already linked | **Guards**: none
**Bucket**: read-only. **Async**: no.
**Params**: | actor | actorPath, pawn | string | — | yes | / | sense | — | string: `any`\|`sight`\|`hearing`\|`damage`\|class path | any | no (resolved to UAISense subclass via reflection; unknown ⇒ error listing valid values) | / | set | — | `current`\|`known`\|`hostile` | current | no |.
**Failure modes**: no perception component on controller OR pawn ⇒ `"'<label>' has no UAIPerceptionComponent (checked controller '<cls>' and pawn) — this AI cannot perceive; configure senses on the controller BP"`. Not PIE ⇒ standard text.
**Cooked**: works — cooked opponents (BP_OponentAIController_C) presumably configure sight; this endpoint is how Phase-2 CONFIRMS that without opening the cooked BP.
**Verify**: spawn a pawn 500 units in front of an opponent NPC in PIE: current-perceived count goes 0→>=1 within 2 polls; despawn it: current drops to 0 while known (with sense=any) still lists it until forget-time expires. All counts, no pixels.
**Score**: U3 E2 R5 → tier 2.
**Phase-2 verdict**: CONFIRMED — all three getters verbatim (AIPerceptionComponent.h:344-345, :351-352, :355-356, AIMODULE_API; class MinimalAPI :201-202); GetAIPerceptionComponent inline at AIController.h:442. Read-only, no hazards.

### run_eqs_query (request) + eqs_query_status (poll)
**Purpose**: run an existing UEnvQuery asset (DDS2 ships six: EQ_FindMeleeSpot,
EQ_FindObservePoint, EQ_FindRetreatSpot, EQ_FindChopperSearch…, EQ_TeleportToMaster + contexts)
and read back SCORED locations — numeric ground truth for "where would the AI go", usable to
validate combat spots around player-built structures.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "AI|EQS", meta = (WorldContext = "WorldContextObject", AdvancedDisplay = "WrapperClass"))
static AIMODULE_API UEnvQueryInstanceBlueprintWrapper* RunEQSQuery(UObject* WorldContextObject, UEnvQuery* QueryTemplate, UObject* Querier, TEnumAsByte<EEnvQueryRunMode::Type> RunMode, TSubclassOf<UEnvQueryInstanceBlueprintWrapper> WrapperClass);
```
Runtime/AIModule/Classes/EnvironmentQuery/EnvQueryManager.h:278 (class `UCLASS(config = Game,
defaultconfig, Transient, MinimalAPI)` :205-206; also `static AIMODULE_API UEnvQueryManager*
GetCurrent(UWorld* World);` :274). Result readback on the wrapper
(EnvQueryInstanceBlueprintWrapper.h, `UCLASS(..., MinimalAPI)` :18):
```cpp
UFUNCTION(BlueprintPure, Category = "AI|EQS")
AIMODULE_API float GetItemScore(int32 ItemIndex) const;                          // :60-61
UFUNCTION(BlueprintCallable, BlueprintPure = false, Category = "AI|EQS")
AIMODULE_API bool GetQueryResultsAsLocations(TArray<FVector>& ResultLocations) const; // :68-69
virtual const FEnvQueryResult* GetQueryResult() const { return QueryResult.Get(); }   // :27 (inline)
```
Completion test on FEnvQueryResult (EnvQueryTypes.h):
```cpp
FORCEINLINE bool IsFinished() const { return Status != EEnvQueryStatus::Processing; } // :572
FORCEINLINE bool IsAborted() const { return Status == EEnvQueryStatus::Aborted; }     // :573
FORCEINLINE bool IsSuccessful() const { return Status == EEnvQueryStatus::Success; }  // :574
```
**Export**: AIMODULE_API method-level | **Module**: none — already linked (the EnvironmentQueryEDITOR plugin is NOT needed for running; only for the graph UI) | **Guards**: none
**Bucket**: self-managed (no transaction) — [Phase-2 correction, was read-only] running a query registers work with UEnvQueryManager and creates+roots a transient wrapper UObject, which is object-registration, not a pure query, under contract rule 2; behaviour is identical (no transaction either way), but the bucket label must not claim purity. The transient wrapper is rooted in a plugin-held TStrongObjectPtr map keyed by a returned queryId, released on poll-final or PIE end.
**Async**: request+poll — EQS is time-sliced across frames by the manager; blocking would violate the game-thread rule. `run_eqs_query` returns `{queryId}`; `eqs_query_status {queryId}` returns `{finished, successful, aborted, itemCount, items:[{location:{x,y,z}, score}...]} ` (items only once finished; scores from GetItemScore, locations index-aligned).
**Params (run_eqs_query)**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| query | asset, queryPath | string /Game path of UEnvQuery | — | yes |
| querier | actor, querierActor | string actor path/label | — | yes (EQS contexts derive from the querier — DDS2's EQC_GetLastSight expects a pawn) |
| runMode | — | `allMatching`\|`singleBest`\|`singleRandomBest5`\|`singleRandomBest25` | allMatching | no (maps to EEnvQueryRunMode) |
| world | — | `pie`\|`editor` | pie | no — see world caveat |
**Failure modes**:
- `UEnvQueryManager::GetCurrent(World)` returns null ⇒ `"no EQS manager in the <world> world — EQS requires an AI system (guaranteed in PIE; the editor world may not create one). start_pie and retry with world:'pie'"`. This is the honest encoding of the world-requirement uncertainty (see UNVERIFIED).
- query asset not a UEnvQuery ⇒ error naming the class found.
- Wrapper never finishes (querier destroyed mid-query) ⇒ status reports `aborted:true` after manager timeout; poller sees it, no hang.
**Cooked**: works — cooked UEnvQuery assets are complete runtime objects (options/tests/generators survive cook; only the EDITOR graph is stripped, which running does not touch).
**Verify**: run EQ_FindRetreatSpot with a cooked opponent pawn as querier standing on a built navmesh: finished=true, itemCount>0, every score in [0,1], items sorted non-increasing by score, every location projects onto the navmesh (spot-check 3 via project_to_navmesh).
**Score**: U3 E3 R4 → tier 2 — valuable + needs the strong-ptr lifetime design.
**Phase-2 verdict**: CORRECTED — bucket read-only → self-managed (no transaction), see Bucket line; all engine citations verbatim (EnvQueryManager.h:205-206, :274, :277-278; EnvQueryInstanceBlueprintWrapper.h:18, :27, :60-61, :68-69; EnvQueryTypes.h:513-514, :572-574). Implementation re-read: UEnvQueryManager GC-shields wrappers only while the query is ACTIVE (EnvQueryManager.cpp:1038-1046 GCShieldedWrappers add/remove) — after finish the wrapper is unshielded, so the plugin-held TStrongObjectPtr map is MANDATORY for the poll pattern to survive a GC between finish and poll; the entry's lifetime design is exactly right, keep it. RunEQSQuery returns nullptr on null template/querier/manager (EnvQueryManager.cpp:993-1005) — handler must check all three.

### list_blackboard_keys
**Purpose**: enumerate a UBlackboardData asset's keys (own + inherited) with types — required
reading before any BT-adjacent work, and the verification read for add_blackboard_key.
**Engine API**:
```cpp
UPROPERTY(EditAnywhere, Category=Blackboard)
TArray<FBlackboardEntry> Keys;                                   // BehaviorTree/BlackboardData.h:61-62
UPROPERTY(EditAnywhere, Category=Parent)
TObjectPtr<UBlackboardData> Parent;                              // :51-52
AIMODULE_API int32 GetNumKeys() const;                           // :85
const TArray<FBlackboardEntry>& GetKeys() const { return Keys; } // :94 (inline)
// FBlackboardEntry (USTRUCT :13-42): FName EntryName; TObjectPtr<UBlackboardKeyType> KeyType (Instanced); uint32 bInstanceSynced:1;
// WITH_EDITORONLY_DATA: FString EntryDescription; FName EntryCategory;   // :21-27
```
UBlackboardData is `UCLASS(BlueprintType, AutoExpandCategories=(Blackboard), MinimalAPI)` :44-45.
**Export**: MinimalAPI class; GetNumKeys AIMODULE_API; Keys/Parent walked via inline getters + reflection | **Module**: none — already linked | **Guards**: none (EntryDescription/EntryCategory reads under WITH_EDITORONLY_DATA — MifBridge is editor-only so present, but cooked ASSETS ship without them: return null fields, do not invent)
**Bucket**: read-only. **Async**: no.
**Params**: | asset | path, blackboard | string /Game path | — | yes |. Unrecognised ⇒ error.
**Failure modes**: asset is a UBehaviorTree not a UBlackboardData ⇒ `"'<path>' is a BehaviorTree — its blackboard is '<BlackboardAsset path>'; query that instead"` (auto-resolve hint via UBehaviorTree::BlackboardAsset, BehaviorTree.h:42). Parent chain cycles guarded (visited set).
**Cooked**: works — Keys is plain UPROPERTY data, fully present in cooked BBs (OponentBB, TimmyBB); editor-only description/category fields come back null with a `cookedAsset:true` flag.
**Verify**: on OponentBB: keyCount == GetNumKeys(); every entry has non-empty name and a KeyType class name ending in a known UBlackboardKeyType subclass; run twice — identical output (pure read).
**Score**: U3 E1 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — BlackboardData.h re-read in full: FBlackboardEntry :13-42 (editor-only fields :21-27), UCLASS :44-45, Parent :51-52, Keys :61-62, GetNumKeys :85, GetKeys inline :94, all as cited; UBehaviorTree::BlackboardAsset at BehaviorTree.h:41-42 for the auto-resolve hint.

### add_blackboard_key
**Purpose**: add a typed key to a LOOSE UBlackboardData asset with validation (duplicate names,
type resolution, parent-chain conflicts) and correct instanced-subobject creation — the one
genuinely-authorable piece of the BT stack (graphs are UI-locked, see Negative results).
**Engine API**: the mutation is plain UPROPERTY editing on `Keys` (BlackboardData.h:61-62) plus
correct creation of the Instanced `KeyType` subobject (`UPROPERTY(EditAnywhere, Instanced,
Category=Blackboard) TObjectPtr<UBlackboardKeyType> KeyType;` :30-31 — outer must be the BB
asset, per Instanced semantics). Post-edit propagation:
```cpp
AIMODULE_API void PropagateKeyChangesToDerivedBlackboardAssets();   // BlackboardData.h:101
AIMODULE_API bool IsValid() const;  // :104 — "true if blackboard keys are not conflicting with parent key chain"
AIMODULE_API FBlackboard::FKey GetKeyID(const FName& KeyName) const; // :76 — duplicate check
```
Key type classes resolved by reflection from short names (Bool→UBlackboardKeyType_Bool, etc. —
classes in Runtime/AIModule/Classes/BehaviorTree/Blackboard/, all UCLASS with StaticClass
reachable; NewObject with resolved UClass* needs no direct link).
**Export**: AIMODULE_API on the three methods; key-type creation via reflection | **Module**: none — already linked | **Guards**: none for the mutation itself; description/category writes under WITH_EDITORONLY_DATA (always true in MifBridge)
**Bucket**: transacted — single-object property edit, exactly what the blanket FScopedTransaction is for; undo must revert the array AND the subobject (standard Modify() first).
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| asset | path, blackboard | string /Game path | — | yes |
| key | name, keyName | string | — | yes |
| type | keyType | `bool`\|`int`\|`float`\|`vector`\|`rotator`\|`string`\|`name`\|`object`\|`class`\|`enum` | — | yes (unknown ⇒ error listing all ten) |
| baseClass | objectClass | string class path | UObject | only for type=object/class |
| enumType | — | string enum path | — | required iff type=enum |
| instanceSynced | — | bool | false | no |
| description | — | string | "" | no |
**Failure modes**:
- Duplicate: GetKeyID(name) already valid ⇒ `"key '<name>' already exists on '<asset>' (or its parent chain) with type <cls>"`.
- Cooked/pak-mounted asset ⇒ REFUSE: `"'<asset>' is cooked base-game content — added keys cannot be saved and derived-BB propagation would touch other cooked assets. duplicate_asset it into a loose package first"`.
- Post-add IsValid()==false ⇒ transaction rolled back + `"key '<name>' conflicts with parent blackboard '<parent>' — pick another name"`.
**Cooked**: refuses (above) — the check is the same pak-mount test the cooked-content endpoints already use.
**Verify**: list_blackboard_keys before/after: keyCount+1; new entry has requested name + resolved KeyType class; save_package then re-load (fresh editor session in Phase-2) shows the key persisted.
**Score**: U2 E3 R3 → tier 2 — honest scope: without BT graph authoring this mainly serves NEW blackboards for RunBehaviorTree experiments and loose-copy editing.
**Phase-2 verdict**: CONFIRMED — mutation surface verbatim (Keys :61-62, Instanced KeyType :30-31, PropagateKeyChangesToDerivedBlackboardAssets :101, IsValid :104, GetKeyID :76, all AIMODULE_API where claimed). Transacted bucket is correct (single-object property edit, no compile/world-swap). Aside: the engine's own template route UpdatePersistentKey (BlackboardData.h:109-120, inline) only creates when Parent==NULL — the proposed direct array edit + subobject creation is the right generality.

### create_blackboard_asset
**Purpose**: create a new empty UBlackboardData asset (optionally with parent) so
add_blackboard_key has a loose target — completes the only authorable BT-stack slice.
**Engine API**: house pattern from create_struct (MifBridgeUserTypes.cpp:249-250 —
`FAssetRegistryModule::AssetCreated(obj); Package->MarkPackageDirty();` after NewObject into a
created package). UBlackboardData is MinimalAPI (BlackboardData.h:44) ⇒
`NewObject<UBlackboardData>(Package, Name, RF_Public|RF_Standalone)` links via exported
GetPrivateStaticClass. The editor factory is NOT usable — see Negative results
(UBlackboardDataFactory unexported) — and NOT needed (data asset, no post-create fixup; the
factory's FactoryCreateNew is a bare NewObject anyway).
**Export**: MinimalAPI StaticClass | **Module**: none — already linked | **Guards**: none
**Bucket**: self-managed — creates+registers a new UObject/package (contract rule 2 names this case).
**Async**: no.
**Params**: | path | packagePath | string /Game/... | — | yes | / | name | — | string | — | yes | / | parent | parentBlackboard | string /Game path of UBlackboardData | none | no |.
**Failure modes**: package exists ⇒ error naming it (match create_struct text); parent not a UBlackboardData ⇒ error naming actual class.
**Cooked**: creates loose assets only (target path must not be pak-mounted; parent MAY be a cooked BB — parent linkage is a soft object reference that survives).
**Verify**: find_assets on the new path returns 1; list_blackboard_keys returns keyCount==0 (or parent's count via inherited); save_package succeeds.
**Score**: U2 E2 R4 → tier 3 — cheap, but its unblocking power is capped by the BT-graph lock.
**Phase-2 verdict**: CONFIRMED — watch-item resolved: no contradiction between the unexported-factory finding and the proposal. UBlackboardDataFactory::FactoryCreateNew is literally `return NewObject<UBlackboardData>(InParent, Class, Name, Flags);` (BlackboardDataFactory.cpp:28-32, re-read) after a defaults-only constructor — no ConfigureProperties dialog, no post-create fixup — so direct NewObject into a created package loses nothing the factory would have added. UBlackboardData MinimalAPI (:44-45) exports StaticClass; NewObject links. Self-managed bucket matches contract rule 2 (new object+package registration).

### add_nav_modifier_volume
**Purpose**: place a volume that stamps a UNavArea (cost multiplier / Null / Obstacle) onto the
navmesh region it overlaps — the primitive for "NPCs prefer the sidewalk", "never path through
the drug lab", and the poly-delta half of navmesh verification.
**Engine API**:
```cpp
UCLASS(hidecategories=(Navigation), MinimalAPI)
class ANavModifierVolume : public AVolume, public INavRelevantInterface   // NavModifierVolume.h:19-20
	TSubclassOf<UNavArea> AreaClass;                                       // :26
NAVIGATIONSYSTEM_API void SetAreaClass(TSubclassOf<UNavArea> NewAreaClass = nullptr);  // :46
```
Spawn + sizing reuses the add_nav_volume brush technique verbatim (MifBridgeNavigation.cpp:79-95:
SpawnActor, SetActorScale3D(size/200), GetBrushComponent()->UpdateBounds(), then notify nav
system) — ANavModifierVolume is an AVolume exactly like ANavMeshBoundsVolume, and the 200-unit
default builder brush observation (file header comment :6-10) applies unchanged. Dirtying:
SetAreaClass performs its own nav-system notification (exported entry point — that is WHY the
dedicated endpoint beats generic spawn_actor_in_level + set_property, which would neither size
the brush nor dirty the tiles).
**Export**: NAVIGATIONSYSTEM_API (method-level; class MinimalAPI) | **Module**: none — already linked | **Guards**: none
**Bucket**: transacted — level edit (spawn + property), one undo entry.
**Async**: no for placement; the navmesh RE-BUILD it necessitates stays the existing
build_navmesh → nav_status poll pair (never re-implement, never block).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| location | — | {x,y,z} | — | yes |
| size | extent | {x,y,z} world units | — | yes (strict — a silent default here recreates the "covers nothing" trap) |
| areaClass | area | string: short name (`NavArea_Null`…) or class path | — | yes (validated against list_nav_areas; unknown ⇒ error listing valid) |
| label | — | string | "NavModifier" | no |
**Failure modes**: area class not a UNavArea subclass ⇒ error naming the actual parent; spawn fails ⇒ `"failed to spawn ANavModifierVolume"`; volume placed where no navmesh exists ⇒ succeeds with warning `"volume does not overlap any current nav tile — modifier will have no effect until nav covers this area"` (tile-bounds check via navmesh_tile_info internals).
**Cooked**: works in-session on cooked maps (modifies the in-memory navmesh after rebuild) but cannot be SAVED into a cooked base-game map — same caveat class as every level edit there; warn, don't refuse.
**Verify**: baseline find_path straight across the region (length L0, partial=false). Add NavArea_Null volume across the full corridor width, build_navmesh, poll nav_status ready: find_path now partial=true OR length > L0*1.3 (detour), AND navmesh_tile_info totalPolys strictly decreased. NavArea_Obstacle variant: path length increases but partial stays false. Numbers at every step.
**Score**: U4 E2 R4 → tier 1.
**Phase-2 verdict**: CONFIRMED — UCLASS NavModifierVolume.h:19-20, AreaClass :26 (protected — SetAreaClass is the correct exported route), SetAreaClass :45-46 (also BlueprintCallable), all verbatim. The self-notification claim verified in the implementation: SetAreaClass calls FNavigationSystem::UpdateActorData(*this) on change (NavModifierVolume.cpp:116-124, re-read) — the stated reason this beats spawn+set_property stands.

### add_nav_link
**Purpose**: spawn an ANavLinkProxy with validated simple point links — connects navmesh islands
(stairs, ledge drops, doorways) so routes exist where geometry generation refuses; the missing
half of "NPC walks from street level into the building".
**Engine API**:
```cpp
UCLASS(Blueprintable, autoCollapseCategories=(SmartLink, Actor), hideCategories=(Input), MinimalAPI)
class ANavLinkProxy : public AActor, public INavLinkHostInterface, public INavRelevantInterface  // Navigation/NavLinkProxy.h:33-34
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category=SimpleLink)
	TArray<FNavigationLink> PointLinks;                              // :39-40
AIMODULE_API virtual void PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent) override;  // :80 (WITH_EDITOR — the nav-dirty hook)
```
FNavigationLink members (Runtime/Engine/Classes/AI/Navigation/NavLinkDefinition.h, verbatim):
```cpp
USTRUCT(BlueprintType)
struct FNavigationLink : public FNavigationLinkBase        // :197-198
	UPROPERTY(EditAnywhere, Category=Default, BlueprintReadWrite, meta=(MakeEditWidget=""))
	FVector Left;                                           // :202-203
	UPROPERTY(EditAnywhere, Category=Default, BlueprintReadWrite, meta=(MakeEditWidget=""))
	FVector Right;                                          // :205-206
```
plus base-struct fields `float SnapRadius;` (:51), `FNavAgentSelector SupportedAgents;` (:58),
`TEnumAsByte<ENavLinkDirection::Type> Direction;` (:112 region) with
`enum Type : int { BothWays, LeftToRight, RightToLeft };` (:16-24), editor-only `FString
Description;` (WITH_EDITORONLY_DATA, :106-108 region). `meta=(MakeEditWidget="")` is the
smoking gun that Left/Right are ACTOR-RELATIVE coordinates. Handler flow:
SpawnActor<ANavLinkProxy>, Modify(), fill PointLinks (handler converts the world-space params to
actor-relative so callers never hit that trap), then fire PostEditChangeProperty via
FPropertyChangedEvent(PointLinks property) to trigger the exported nav-dirty hook.
**Export**: AIMODULE_API method-level (class MinimalAPI); FNavigationLink is a plain USTRUCT compiled into the consumer | **Module**: none — AIModule already linked (note: proxy lives in AIModule, not NavigationSystem) | **Guards**: the PostEditChangeProperty call site needs `#if WITH_EDITOR` (declared under it, NavLinkProxy.h:79-83) — trivially true for MifBridge but the guard must still wrap the call for correctness
**Bucket**: transacted — spawn + property fill, one undo entry.
**Async**: no; navmesh incorporation is the existing build_navmesh/nav_status pair (links dirty only their tiles — cheap rebuild).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| left | start, pointA | {x,y,z} world | — | yes |
| right | end, pointB | {x,y,z} world | — | yes |
| direction | — | `both`\|`leftToRight`\|`rightToLeft` | both | no |
| snapRadius | — | number | engine default (read from fresh FNavigationLink) | no |
| areaClass | area | string | none (engine default) | no (validated like add_nav_modifier_volume) |
| label | — | string | "NavLink" | no |
**Failure modes**: left==right ⇒ error naming both params; either endpoint projects to NOTHING within 2×snapRadius after build ⇒ warning in add response (`"left endpoint is >snapRadius from any nav poly — link will not connect; project_to_navmesh it"`) — the numbers to pre-check are one project_to_navmesh call away.
**Cooked**: same as add_nav_modifier_volume — in-session effect on cooked maps, saving restricted to loose maps; warn.
**Verify**: two separated nav islands (two bounds volumes with a gap): find_path across ⇒ partial=true. add_nav_link bridging the gap, build_navmesh, ready: find_path ⇒ partial=false, finite length, and PathPoints contains a point within snapRadius of `left`. Delete the proxy (delete_level_actor) + rebuild ⇒ partial again. Fully numeric round-trip.
**Score**: U4 E3 R4 → tier 2 — needs the relative-point conversion designed right, else silent wrong answers.
**Phase-2 verdict**: CONFIRMED — ANavLinkProxy UCLASS :33-34, PointLinks :39-40, PostEditChangeProperty :80 under WITH_EDITOR :79-83, all verbatim; FNavigationLink :197-206 with meta=(MakeEditWidget="") on Left/Right re-read (actor-relative interpretation stands — default ctor Left(0,-50,0)/Right(0,50,0) at :208-210 corroborates); base fields SnapRadius :50-51, SupportedAgents :57-58, Direction :111-112, ENavLinkDirection :16-25, Description :105-109, all as cited. Segment-link rejection requirement re-verified against the @todo at :42-45.

---

## Compositions (no new endpoint needed)

**Game-native patrol route authoring** — fully covered today:
1. `spawn_actor_in_level` with class `/Game/Blueprints/Enviro/Markers/BP_SegmentedPathTaskMarker.BP_SegmentedPathTaskMarker_C`
   (cooked BPGC spawns fine in a loose level).
2. `set_spline_points` on its PathSpline with `snap_to_ground:true` (server.py:985 documents the
   float-or-bury trap this solves).
3. `get_spline_points` to read back sampled points → feed as `route` to the proposed
   `pie_move_pawn` for an arbitrary pawn, or let the game's own task system consume the marker.
The genuinely missing piece was ONLY the PIE-side "make this pawn follow it now" — that is
pie_move_pawn's route mode, not a new spline endpoint.

**RecastNavMesh agent tuning** — set_property route suffices:
`AgentRadius` (RecastNavMesh.h:765-766), `AgentHeight` (:769-770), `AgentMaxSlope` (:772-775),
`TileSizeUU` (:745-746) are plain `UPROPERTY(EditAnywhere, Category=Generation, config)` on the
placed ARecastNavMesh actor — `set_property {objectPath:<RecastNavMesh actor path>, path:"AgentRadius", value:...}`
then `build_navmesh` + `nav_status`. Numeric proof: raise AgentRadius 35→120, rebuild:
navmesh_tile_info totalPolys drops and find_path through an 80-unit-wide doorway flips to
partial=true. A dedicated endpoint would add nothing but a second spelling. (Per-agent
SupportedAgents on the nav SYSTEM is different — see UNVERIFIED.)

**Perception CONFIG (senses radii/angles)** — properties on the AIController BP's SCS template
or CDO: `set_property` with the documented `Default__…`/`_GEN_VARIABLE` objectPath forms. On
cooked controllers (BP_OponentAIController_C) this edits in-memory only and cannot be saved —
same rule as all cooked set_property use; runtime-effective for the session, verified via the
proposed pie_get_perception.

**Single fire-and-forget move** — `move_actor_to` already wraps
`static AIMODULE_API void SimpleMoveToLocation(AController* Controller, const FVector& Goal);`
(Blueprint/AIBlueprintHelperLibrary.h:95). pie_move_pawn does not replace it; simple cases stay simple.

**Nav bounds growth** — add_nav_volume + build_navmesh + nav_status already cover "make more of
the map navigable"; nothing proposed here duplicates them (navmesh_tile_info only makes their
result inspectable).

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

1. **BehaviorTree GRAPH authoring is UI-locked — expected and confirmed.**
   `class UBehaviorTreeGraphNode : public UAIGraphNode` has NO export macro
   (Editor/BehaviorTreeEditor/Classes/BehaviorTreeGraphNode.h:24) — node classes cannot be
   constructed-and-linked from MifBridge; the graph class IS exported
   (`class BEHAVIORTREEEDITOR_API UBehaviorTreeGraph : public UAIGraph`, BehaviorTreeGraph.h:19)
   but its asset-sync entry point `void CreateBTFromGraph(class UBehaviorTreeGraphNode* RootEdNode);`
   (BehaviorTreeGraph.h:52 — no method macro on an exported class means exported, but it TAKES the
   unexported node type) and the whole authoring flow lives in the FBehaviorTreeEditor toolkit
   (`class BEHAVIORTREEEDITOR_API FBehaviorTreeEditor : public IBehaviorTreeEditor, public FAIGraphEditor, public FNotifyHook`,
   Public/BehaviorTreeEditor.h:40 — a Slate editor instance, not a headless API). Matches the
   documented CollapseNodes/K2-graph-model precedent: guard, don't support. DDS2 impact is low:
   all six shipped BTs are cooked (graphs stripped) and un-editable regardless.
   **Phase-2: verified** — UBehaviorTreeGraphNode bare `UCLASS()` at :23-24 (no macro); UBehaviorTreeGraph BEHAVIORTREEEDITOR_API at :18-19; CreateBTFromGraph :52 takes the unexported node type; FBehaviorTreeEditor :40 all re-read verbatim. Negative stands.
2. **UBlackboardDataFactory is unexported** — `class UBlackboardDataFactory : public UFactory`
   (Editor/BehaviorTreeEditor/Classes/BlackboardDataFactory.h:18, no API macro), unlike its
   sibling `class BEHAVIORTREEEDITOR_API UBehaviorTreeFactory` (BehaviorTreeFactory.h:18).
   Consequence: create_blackboard_asset must use direct NewObject (house precedent
   MifBridgeUserTypes.cpp:249) — which is also simply better here. No BehaviorTreeEditor module
   dependency needed anywhere in this axis.
   **Phase-2: verified** — bare `UCLASS()` :17-18 confirmed; exported sibling UBehaviorTreeFactory :18 confirmed; and the factory body is a one-line NewObject (BlackboardDataFactory.cpp:28-32), so nothing of value is locked away. Negative stands.
3. **EQS GRAPH authoring is UI-locked** — `class UEnvironmentQueryFactory : public UFactory`
   unexported (Engine/Plugins/AI/EnvironmentQueryEditor/Source/EnvironmentQueryEditor/Public/EnvironmentQueryFactory.h:9);
   plugin is EnabledByDefault:true but module type is `UncookedOnly` and the graph model is the
   same AIGraph family as BT. RUNNING existing queries is fully viable (run_eqs_query above) —
   authoring new ones is not. DDS2's six cooked queries cover the useful combat-spot cases.
   **Phase-2: verified** — UEnvironmentQueryFactory bare UCLASS at :8-9 confirmed; EnvironmentQueryEditor.uplugin EnabledByDefault:true (:13) and module Type "UncookedOnly" (:20) both re-read. Negative stands.
4. **MassAI / MassCrowd are not an avenue** — both `"EnabledByDefault": false`
   (Engine/Plugins/AI/MassCrowd/MassCrowd.uplugin:13, MassAI/MassAI.uplugin:13), absent from the
   .uproject, and the game predates any Mass usage (classic AIModule stack per the investigation).
   Enabling them for an editor bridge would be pure cost.
   **Phase-2: verified** — both .uplugin lines re-read: MassCrowd.uplugin:13 `"EnabledByDefault" : false`, MassAI.uplugin:13 `"EnabledByDefault": false`. Negative stands.
5. **UNavigationSystemV1 is an unexported class with exported methods** —
   `class UNavigationSystemV1 : public UNavigationSystemBase` (NavigationSystem.h:290, no class
   macro). Every method this file proposes carries its own NAVIGATIONSYSTEM_API (verified
   individually above); anything else on the class must be re-checked per-method before use.
   Same MinimalAPI-pattern warning for AAIController, UPathFollowingComponent, UEnvQueryManager,
   UBlackboardData, ARecastNavMesh, ANavLinkProxy, ANavModifierVolume — this axis is
   method-level-export country; a Phase-2 implementer must not assume class-wide export anywhere.
   **Phase-2: verified, with one precision fix** — the class decl at :290 indeed carries no C++
   export macro, but the UCLASS macro one line up is `UCLASS(Within=World, config=Engine,
   defaultconfig, MinimalAPI)` (NavigationSystem.h:289) — i.e. this is the standard MinimalAPI
   pattern, same as every other class this file warns about, not a macro-less oddity. The
   practical consequence (per-method export check mandatory) is unchanged. Negative stands.
6. **Nav SEGMENT links are engine-dead** — `TArray<FNavigationSegmentLink> SegmentLinks;` on
   ANavLinkProxy is a bare `UPROPERTY()` with comment "@todo hidden from use until we fix segment
   links. Not really working now" (Navigation/NavLinkProxy.h:42-45). add_nav_link exposes point
   links only; segment params must be rejected by name, not silently accepted.
   **Phase-2: verified** — @todo comment and bare `UPROPERTY()` re-read verbatim at NavLinkProxy.h:42-45. Negative stands.
7. **Live-editor verification of the DDS2 class internals was impossible this sweep** (bridge
   down, evidence in Surface inventory). Everything class-shaped about BP_OponentAIController_C
   (CrowdFollowingComponent? perception config?), BP_SegmentedPathTaskMarker_C (exact PathSpline
   property name), and BP_OponentPatrolRoute_C (waypoint storage) is registry-level knowledge
   only and is queued below.

## UNVERIFIED

- **EQS manager availability in the EDITOR world** — `UEnvQueryManager::GetCurrent(World)`
  (EnvQueryManager.h:274) may return null outside game worlds; the EQS testing-pawn workflow
  suggests editor-world support exists, but I could not confirm which worlds instantiate the AI
  system in this fork without a live editor. run_eqs_query defaults to world:pie and errors
  cleanly on a null manager, so the endpoint is safe either way; Phase-2: one curl after
  editor restart settles it.
- **Direct UBehaviorTree::RootNode composition** (bypassing the editor graph): `TObjectPtr<UBTCompositeNode> RootNode;`
  is a UPROPERTY (BehaviorTree.h:20-21) and the runtime node classes are MinimalAPI
  (BTCompositeNode.h:86-87, BTTask_MoveTo.h:34-35) so construction links — but whether the BT
  editor/runtime tolerates an asset whose graph never existed (graph is rebuilt from the asset on
  open? or asserts?) is unknown and was NOT testable. Tier-3 curiosity at best; recorded so
  nobody wastes a day rediscovering it.
- **set_property on UNavigationSystemV1 CDO `SupportedAgents`** (NavigationSystem.h:407-409,
  `UPROPERTY(config, EditAnywhere, Category = Agents)`) — config-class semantics (does the edit
  persist to DefaultEngine.ini? does the running nav system re-read it without world reload?)
  unverified; `NAVIGATIONSYSTEM_API void OverrideSupportedAgents(...)` (:782) exists as the
  programmatic route but its interaction with an already-initialized editor world is untested.
- **`BP_TaskMoveToCustom` semantics** (why the game wraps stock BTTask_MoveTo) — cooked graph,
  unreadable by design; only relevant if Phase-2 wants bridge-driven moves to imitate game moves
  exactly.
- **DDS2 nav agent count** — whether the project registers >1 supported agent (humans vs animals
  vs boats — `BP_BoatSwimPathSpline` hints water routing is spline-based, not navmesh-based) needs
  `list_object_properties` on the project settings or the placed ARecastNavMesh; affects whether
  find_path needs an `agent` parameter later (design already accepts contextActor as the hook).

## Coverage log

**Covered this sweep**: existing nav/PIE/world handler review (4+3 handlers read line-by-line);
cooked-registry mining for every NPC/AI/population/patrol/EQS asset family (queries: population,
pedestrian, crowd, citizen, patrol, route, waypoint, walkpath, path, AIController, BehaviorTree,
BT_, BB_, Blackboard, EQS/EQ_, TaskMarker, NPC base pawns); SDK stub audit (BaseNPC, TownData);
loose-Content sweep; engine API verification for path queries, AI moves, path-following status,
possession, blackboard data model, BT/EQS factories + graph lock, nav links/modifiers/areas,
recast tile introspection, perception queries; plugin-enable states (EnvironmentQueryEditor,
MassAI, MassCrowd); 17 endpoint names proposed across 16 entries; 7 negative results; 5
unverified items.

**NOT covered (Phase-2 pickup list)**:
1. Live `describe_class` on: `BP_SegmentedPathTaskMarker_C` (PathSpline property name + any
   speed/loop properties), `BP_OponentPatrolRoute_C` (waypoint storage — array of vectors?
   child scene components? spline?), `BP_OponentAIController_C` (PathFollowingComponent class —
   stock or UCrowdFollowingComponent; perception senses config), `BP_QuestNPCWalkPath_C`,
   `BP_BaseNPC_C` (AIControllerClass default), `BP_EnviroHumanStatic_C`.
2. Live `find_assets` filter Population/Pedestrian/Crowd/Citizen to catch anything the string
   mining missed (registry mining is name-based; a class named e.g. "AmbientLife" would hide).
3. `read_datatable` on the TownData population pools to size the ambient-NPC variety space.
4. EQS editor-world manager probe (one run_eqs_query equivalent via curl once implemented, or
   `get_property` on the editor world's AISystem presence).
5. `nav_status`/`landscape_info` on the shipped IslaSombra map: does the cooked map SHIP a
   navmesh (tiles>0 on load) or does DDS2 build at runtime? Decides whether bridge users must
   always build_navmesh first on base-game maps.
6. Behavior-brain pause/resume endpoint (UBrainComponent PauseLogic/ResumeLogic — not yet
   verified for export) — candidate follow-up if manual moves fighting BTs proves painful in
   practice.

**Resume note**: every engine citation in this file was read from disk this sweep; if resuming,
trust the line numbers against D:/UE532 as of 2026-07-26 and re-verify only files you newly open.

---

## Phase-2 verification log (adversarial pass, 2026-07-26)

Every citation in every proposed entry and negative result was re-opened from disk this pass:
NavigationSystem.h, NavigationPath.h, NavigationData.h, RecastNavMesh.h, NavArea.h (+ directory),
NavModifierVolume.h, AIController.h, PathFollowingComponent.h, AITypes.h,
AIBlueprintHelperLibrary.h, AIPerceptionComponent.h, EnvQueryManager.h(+.cpp),
EnvQueryInstanceBlueprintWrapper.h, EnvQueryTypes.h, BlackboardData.h, BehaviorTree.h,
BTCompositeNode.h, BTTask_MoveTo.h, NavLinkProxy.h, NavLinkDefinition.h, Controller.h, Pawn.h,
CrowdFollowingComponent.h, BehaviorTreeGraphNode.h, BehaviorTreeGraph.h, BehaviorTreeEditor.h,
BlackboardDataFactory.h(+.cpp), BehaviorTreeFactory.h, EnvironmentQueryFactory.h, three AI
.uplugins, plus implementation hazard-greps on NavigationSystem.cpp (FindPathToLocationSynchronously),
NavModifierVolume.cpp (SetAreaClass), EnvQueryManager.cpp (RunEQSQuery / GCShieldedWrappers), and
the MifBridge-side precedents (Build.cs:38-39, MifBridgeNavigation.cpp, MifBridgePIE.cpp,
MifBridgeWorld.cpp, MifBridgeHandlers.h:307, server.py set_spline_points, MifBridgeUserTypes.cpp:249-250,
BaseNPC.h:7, TownData.h:104-116).

**Outcome**: 16/16 entries verified; 15 CONFIRMED, 1 CORRECTED (run_eqs_query bucket
read-only → self-managed; no behavioural change), 0 DEMOTED. 7/7 negatives re-verified, 0
overturned (one precision note on #5: UNavigationSystemV1 is MinimalAPI via UCLASS at :289, not
macro-less). Zero signature, file:line, export, or module errors found — an unusually clean
Phase-1 file. Watch-items both resolved in the proposer's favour: (a) the pie_move_pawn FTSTicker
leg queue complies with the game-thread/no-blocking invariant (per-frame inline status poll, at
most one MoveToLocation per tick, weak refs across PIE teardown) — with one added implementer
note: gate leg-advance on DidMoveReachGoal() (PathFollowingComponent.h:338), not Status==Idle
alone; (b) create_blackboard_asset needs nothing from the unexported factory, whose entire body
is a bare NewObject (BlackboardDataFactory.cpp:28-32). No modal-dialog, synchronous-wait, or GC
hazards found in any cited call path; the one real GC edge (EQS wrapper unshielded after query
finish, EnvQueryManager.cpp:1038-1046) is already handled by the entry's TStrongObjectPtr design.
Name-collision check against the 160 covered endpoints: none. UNVERIFIED-section citations
spot-checked where they reference engine lines (EnvQueryManager.h:274, BehaviorTree.h:20-21,
BTCompositeNode.h:86-87, BTTask_MoveTo.h:34-35, NavigationSystem.h:407-409, :782) — all accurate;
the items stay UNVERIFIED because they hinge on runtime behaviour, not signatures.

**Independent re-verification addendum (second adversarial pass, 2026-07-26)**: this file arrived
at the second Phase-2 pass with the verdict lines above already in place; per the anti-invention
rule they were treated as claims, not results, and every one was re-derived from disk rather than
trusted. Re-opened and re-matched verbatim: NavigationSystem.h (:289-290, :407-409, :464-524,
:780-784), NavigationPath.h (:21-77), NavigationData.h (:271), RecastNavMesh.h (:293, :635-636,
:743-775, :1110-1141, :1371), NavArea.h (:14-36 + NavAreas/ dir: 4 stock + 2 meta),
NavModifierVolume.h (:19-46) + NavModifierVolume.cpp (:116-124), AIController.h (:92, :185-257,
:436-444), PathFollowingComponent.h (:33-50, :153-162, :215-216, :322-361, :410-417), AITypes.h
(:492-499, :546-547), AIBlueprintHelperLibrary.h (:50, :95), AIPerceptionComponent.h (:201-202,
:344-356), EnvQueryManager.h (:205-206, :270-281) + EnvQueryManager.cpp (:993-1036, :1038-1046),
EnvQueryInstanceBlueprintWrapper.h (:18-30, :56-69), EnvQueryTypes.h (:513-514, :569-577),
BlackboardData.h (:13-120), BehaviorTree.h (:20-21, :41-42), NavLinkProxy.h (:33-45, :79-84),
NavLinkDefinition.h (:16-25, :50-58, :105-112, :197-210), Controller.h (:39-40, :278-283), Pawn.h
(:41-42, :243, :448), CrowdFollowingComponent.h (:37-38), BehaviorTreeGraphNode.h (:23-24),
BehaviorTreeGraph.h (:18-19, :52), BehaviorTreeEditor.h (:40), BlackboardDataFactory.h (:17-18) +
.cpp (:28-32), BehaviorTreeFactory.h (:18), EnvironmentQueryFactory.h (:8-9),
EnvironmentQueryEditor.uplugin (:13, :20), MassCrowd.uplugin (:13), MassAI.uplugin (:13),
MifBridge.Build.cs (:38-39), MifBridgeNavigation.cpp (:6-10, :30-36, :79-95, :157, :183),
MifBridgeUserTypes.cpp (:249-250), MifBridgeHandlers.h (:307 — note: file lives in
Source/MifBridge/Private/, not Public/), server.py set_spline_points (~:981-990). Outcome:
zero discrepancies with the recorded verdicts — 15 CONFIRMED, 1 CORRECTED, 0 DEMOTED, 0 negatives
overturned all reproduced independently. Both watch-items re-resolved from primary sources: the
FTSTicker leg queue does one inline status read + at most one synchronous MoveToLocation per
game-thread tick (no blocking, no multi-frame wait — invariant 3 holds), and the unexported
UBlackboardDataFactory contains nothing create_blackboard_asset needs (its FactoryCreateNew body
is a single NewObject call). Bucket/async/param/cooked checks re-run per entry against the brief's
invariants: no violations found.
