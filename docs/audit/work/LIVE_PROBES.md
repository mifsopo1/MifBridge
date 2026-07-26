# LIVE_PROBES — Phase-2 read-only bridge probes

Run date: 2026-07-26. Bridge base: `http://127.0.0.1:8791/api/`, token `dev`.
All probes read-only per the allowed-endpoint list. Raw results trimmed to relevant fields.

## Probe 0 — self_audit (bridge health)

Command: `POST /api/self_audit {}`

Result (trimmed):
```json
{"ok":true,"endpointCount":160,"healthy":true,"policyContradictions":[],
 "buildDate":"Jul 26 2026","buildTime":"01:49:20",
 "engineVersion":"5.3.2-0+++UE5+Release-5.3-CookedEditorModKit"}
```

Interpretation: bridge is back up, healthy, fresh build (today), all 160 endpoints registered; the Phase-1 "bridge DOWN" condition is cleared and every queued probe below could run.

## G1 — NPC routing classes (describe_class)

### Asset-path resolution (find_assets nameContains)

| Name | Resolved path | origin |
|---|---|---|
| BP_SegmentedPathTaskMarker_C | `/Game/Blueprints/Enviro/Markers/BP_SegmentedPathTaskMarker.BP_SegmentedPathTaskMarker_C` | container |
| BP_OponentPatrolRoute_C | `/Game/Blueprints/Pawns/NPC/Oponents/Behaviour/BP_OponentPatrolRoute.BP_OponentPatrolRoute_C` | container |
| BP_OponentAIController_C | `/Game/Blueprints/Pawns/NPC/Oponents/BP_OponentAIController.BP_OponentAIController_C` | container |
| BP_BaseNPC_C | `/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C` | container |

(`BaseNPC` search also surfaced `BaseNPC_AnimBP_C` at `/Game/Blueprints/Pawns/NPC/Animation/BaseNPC_AnimBP` and the loose editable child `/Game/Blueprints/NPC/IslaSombra/ISL/BP_BaseNPC_Editable`.)

### BP_SegmentedPathTaskMarker_C — parent `/Script/Engine.Actor`

Own functions: `OnRep_PathActive()`, `SegmentOverlapp(OverlappedComponent, OtherActor, OtherComp, OtherBodyIndex, bFromSweep, SweepResult)`, `AddPathBox(Distance: double)`, `TaskUpdate(TaskGuid: FGuid, TaskData: FTaskItemData)`.
Own properties: `TaskMarker: UChildActorComponent*`, **`PathSpline: USplineComponent*`**, `DefaultSceneRoot`, `PathPoints: TArray`, `BoxExtent: FVector`, `RelatedTasks: TArray`, `CurPathPoint: int32`, `BoxMap: TMap`, `PathActive: bool` (replicated, has OnRep).

Interpretation: confirms the Phase-1 spline-walk story — PathSpline is a real USplineComponent property; progress is tracked as `CurPathPoint` over `PathPoints`, with per-segment overlap boxes (`AddPathBox`/`BoxMap`/`SegmentOverlapp`) and task binding (`TaskUpdate` + `RelatedTasks`). `set_spline_points` targets exactly this surface.

### BP_OponentPatrolRoute_C — parent `/Script/Engine.Actor`

Own functions: `OponentDestroyed(DestroyedActor)`, `CheckPathAllowed(Querier: APawn*, Allow: bool)`, `GetNextPatrolPoint(Pawn, OutPoint: FVector)`, `RegisterPatrol(PatrolLeader: APawn*)`, `GetClosestPoint(Querier, OutPoint)`, `GetPatrolLocation(OutLoc)`, `SetupEnds()`.
Own properties: `Spline: USplineComponent*`, `StartSphere`/`EndSphere: USphereComponent*`, `Billboard`/`Billboard1: UBillboardComponent*`, `PatrolDirections: TMap`, `StartRoute`/`EndRoute: ABP_OponentPatrolRoute_C*` (routes chain to each other), `ChanceMultiplier: double`, `AllowThugs: bool`, `AllowPolice: bool`, `AllowMilitia: bool`.

Interpretation: patrol routes are **spline actors chained into a graph** (StartRoute/EndRoute links, PatrolDirections map) with per-faction gates (Thugs/Police/Militia) and a spawn-weight `ChanceMultiplier`; pawns query `GetNextPatrolPoint`/`GetClosestPoint`. This is a second, distinct spline-based movement lane, exactly as the Phase-1 four-subsystem map claimed.

### BP_OponentAIController_C — parent `/Script/AIModule.DetourCrowdAIController`

Own functions: `RetryInit()` only.
Own properties: `OponentPerceptionComponent: UOponentPerceptionComponent_C*`, `AIPerception: UAIPerceptionComponent*`, `MyBehaviorTree: UBehaviorTree*`, `AllowOponentDestroy: bool`.

Interpretation: **hard confirmation of crowd steering** — opponents inherit `ADetourCrowdAIController` (UCrowdFollowingComponent path-following), carry a stock `UAIPerceptionComponent` plus a BP perception wrapper, and run a single `MyBehaviorTree` asset. Resolves the Phase-1 UNVERIFIED "CrowdFollowingComponent? perception config?" line for this class.

### BP_BaseNPC_C — parent `/Script/DrugDealerSimulator2.BaseNPC` (native)

281 functions / 82 properties total; 113 own functions, 33 own properties, 1 own dispatcher (`ScriptedEvent(EventID: FString)`).
Own surface is dialogue/trade/meeting/task oriented, NOT locomotion: `DialOpenDialogueStep`, `SelectedResponse`, `DialActivateTask`, `DialChangeTaskStatus`, `CheckQuestStatus`, `OpenTrade`, `StartMeeting`/`EndMeeting`/`TryLeaveMeeting`, `TryDiscoverBusiness`/`TryDiscoverWorker`/`TryNegotiateShare`, `InfluencerGivePackage`, `UnlockDialogueTag`, `BoundTaskUpdate(TaskGuid, TaskData: FTaskItemData)`, `GameWasLoaded`, `GameBeingSavedPre`, replication OnReps (`OnRep_NPCHidden`, `OnRep_NpcAtMeeting`, `OnRep_CurLookAtLoc`).
Notable own properties: `CharacterID: FName`, `DialogueObject: UClass*`, `CurDialogue: UDialogueObject_C*`, `ShopID: FName`, `NPCBody: UChildActorComponent*`, `Influence: USphereComponent*`, `OneTimeEvents: TArray`, `TasksListShow`/`TaskListHide: TArray`, `CurLookAtLoc: FVector_NetQuantize10`, `BaseAnimation: UAnimSequenceBase*`.

Interpretation: BP_BaseNPC_C is the interaction/dialogue/quest shell on top of a **native locomotion base** (`/Script/DrugDealerSimulator2.BaseNPC`) — movement logic lives native-side, matching the Phase-1 "native spine + BP composition" claim. `BoundTaskUpdate(FGuid, FTaskItemData)` mirrors SegmentedPathTaskMarker's `TaskUpdate`, tying NPCs to the same task-data plumbing.

### Population/crowd asset families (find_assets nameContains)

- `Population` → 2 hits, both **Texture2D billboards**: `/Game/Billboards/bill_PopulationHuman`, `/Game/Billboards/bill_PopulationAnimal` (editor-sprite textures for population marker actors; human vs animal split).
- `Pedestrian` → 0. `Crowd` → 0. `Citizen` → 0.
- `Walker` → 2 hits, both SoundWaves (music tracks by artist "Katori Walker") — red herring.
- describe_class on bare `PopulationHuman` / `PopulationAnimal` / `PopulationMarker` → `class not found` (not native classes; likely unloaded BP marker actors that use those billboards).

Interpretation: there is **no asset family named Pedestrian/Crowd/Citizen** — ambient population naming is "Population(Human|Animal)", and crowd behavior lives at the native/controller level (DetourCrowd), not as content assets. Consistent with Phase-1 registry mining.

## G2 — census for tiering (find_assets class counts)

| Class filter | count | 3 sample paths |
|---|---|---|
| WidgetBlueprint (all) | 54 | `/MovieRenderPipeline/Blueprints/UI_MovieRenderPipelineInfoTableRow`, `/MovieRenderPipeline/Blueprints/UI_MovieRenderPipelineScreenOverlay`, `/MovieRenderPipeline/Blueprints/DefaultBurnIn` (all loose engine-plugin content) |
| WidgetBlueprint (pathPrefix /Game) | 35 | `/Game/GUI/Player/MasterScreenQueryWidget_Editable` (loose editable), `/Game/MODS/BotanistExpansion_p/GUI/MIF_SteelRackWidget`, `/Game/MODS/BotanistExpansion_p/GUI/MIF_RackCraftChild` (loose mod content) |
| **WidgetBlueprintGeneratedClass** | **279** | `/Game/GUI/Player/BigTabs/BigTabMasterWidget.BigTabMasterWidget_C`, `/Game/GUI/Inventory/SimpleTooltipWidget.SimpleTooltipWidget_C`, `/Game/GUI/FrontScreens/CartelScreenElements/Entries/CartelStatsEntry.CartelStatsEntry_C` (container) |
| InputAction | 62 | `/Game/Inputs/InputActions/InputAction_TossBackpack`, `InputAction_ListSelectUp`, `InputAction_ListSelectDown` (container, loaded) |
| InputMappingContext | 5 | `/Game/Inputs/InputMap_PlayerDefined2`, `/Game/Inputs/InputMap_DefaultCharacter`, `/DDS2Casino/Blueprints/Inputs/InputMap_Casino` (one lives in the DLC mount) |
| LevelSequence | 4 | `/Game/Blueprints/Pawns/LobbyLevelSequence`, `/Game/Blueprints/Pawns/MenuLevelSequence`, `/Game/Safe_House/showreel/showreel` (+ `/MovieRenderPipeline/.../LS_StillBlank`) |

Interpretation: the real UMG surface is the **279 cooked WidgetBlueprintGeneratedClass** assets — plain `WidgetBlueprint` (54, mostly MovieRenderPipeline plugin + loose editable/mod copies) badly under-counts it; any UMG-endpoint tiering must be sized against ~280 widgets. Enhanced Input is a real but small surface (62 actions / 5 contexts — one context is DLC-mounted). Sequencer is near-absent in game content (3 game LevelSequences, menu/lobby/showreel only) → sequencer endpoints deserve the lowest tier.

## H — data probes

### H1 — RamaSave stub-vs-real (describe_class)

All three classes resolve from `/Script/RamaSaveSystem` with full reflected surfaces:

- `RamaSaveEngine` — parent `/Script/Engine.Actor`, 130 funcs / 32 props. Own surface: async pipeline events `Async_SaveStarted/Finished/Cancelled/ProgressUpdate(Filename, Progress)`, `LoadProcessFinished(_PreActorFullyLoaded)`, `SaveCancelledForStreamingLevel`; props `RamaSaveComponents: TArray`, `SaveOnlyActorsWithTags: TArray`, `AsyncUnits: TArray`, `LoadParams: FRamaSaveEngineParams`, `Load_StreamingLevels: TArray`.
- `RamaSaveComponent` — parent ActorComponent, 28/19. Own: `RamaSave_PreSave/PostLoad`, `RamaSave_HasSaveTag(s)`, `GetActorStreamingLevelPackageName`, `GetActorIsInPersistentLevel`; props `RamaSave_PersistentActorUniqueID: FGuid`, `RamaSave_SaveTags`, `RamaSave_OwningActorVarsToSave` / `RamaSave_ComponentVarsToSave`, `RamaSave_SavePhysicsData`, `DestroyBeforeLoad`, `LoadedGameVersion: float`, `OwningActorTransform: FTransform`.
- `RamaSaveLibrary` — BlueprintFunctionLibrary, 29 static funcs: `RamaSave_SaveToFile(_WithTags)`, `RamaSave_LoadFromFile(WithTags)`, `RamaSave_LoadStaticDataFromFile → URamaSaveObject*`, `RamaSave_LoadStreamingStateFromFile`, `RamaSave_ClearLevel`, `RamaSave_CancelAsyncSaveProcess`, full file IO set (`RamaSave_FileExists/DeleteFile/CopyFile/RenameFile/RamaFileIO_GetFiles`), path helpers (`RamaSavePaths_SavedDir/GameRootDirectory/BinaryLocation`, `GetDocumentsFolder`), `RemoveLevelPIEPrefix`.

Interpretation: the RamaSaveSystem module is **registered and fully reflected in the live editor** — complete UFUNCTION/UPROPERTY surface incl. the URamaSaveObject static-payload lane and per-actor save-tag/GUID model. Reflection cannot prove function bodies are non-stub, but the module DLL loads and registers everything, so bridge-side save/load orchestration is scriptable against this exact API. (Behavioral proof = one PIE save/load run, out of scope for read-only probes.)

### H2 — cooked curve data

`find_assets class=CurveFloat` → count 46 (e.g. `/Game/FloatCurves/FactorStatuses/Curve_AmphWearoff`, `Curve_SnakeBite`, `Curve_WeedWearoff` — origin container; plus loose `/Water/Curves/*`). `class=CurveTable` → count **0** (project uses CurveFloat assets, not curve tables).

`get_property {objectPath: /Game/FloatCurves/FactorStatuses/Curve_AmphWearoff.Curve_AmphWearoff, propertyPath: FloatCurve}` →
```
(Keys=((InterpMode=RCIM_Cubic,Value=0.100000,ArriveTangent=-0.500000),
 (InterpMode=RCIM_Cubic,TangentMode=RCTM_User,Time=0.100000,Value=1.000000,ArriveTangent=0.020413,LeaveTangent=0.020413),
 (InterpMode=RCIM_Cubic,TangentMode=RCTM_User,Time=0.800000,Value=0.900000,ArriveTangent=-0.336408,...),
 (InterpMode=RCIM_Cubic,TangentMode=RCTM_User,Time=1.000000,Value=0.000000,...)),
 DefaultValue=3.4e38,PreInfinityExtrap=RCCE_Constant,PostInfinityExtrap=RCCE_Constant)
```
`list_object_properties` on the same asset: 4 props (`FloatCurve: FRichCurve`, `bIsEventCurve`, `AssetImportData`, `ImportPath`).

Interpretation: **full FRichCurve key data (times, values, tangents, interp modes) survives cooking and is readable** on a container-origin asset via get_property — curve reads/edits from the bridge are viable on cooked content. Note the endpoints demand `objectPath`/`propertyPath` (not `object`/`property`) — error strings self-document this.

### H3 — get_datatable_row against a *Database table

`list_datatables {}` → **379 tables total, 187 with "Database" in the name** (the Phase-1 "122 *Database tables" figure is an under-count at live-registry level — the 187 includes DLC + MODS variants, e.g. `/ChristmasDlc/Data/DT_ChristmasContainerConfigs`, `/Game/MODS/Brandos*/...`).

`read_datatable /Game/DataTables/Databases/CurrencyDatabase` → `{rowStruct: "CurrencyData", rowCount: 2, rows: [...]}` (rows carry a `Name` field per row).
`get_datatable_row {path, rowName: "DOLAR"}` →
```json
{"ok":true,"path":".../CurrencyDatabase.CurrencyDatabase","rowName":"DOLAR",
 "row":{"Name":"DOLAR","CurrencyName":"NSLOCTEXT(\"\", \"F3AA3F74428B1773A7A380BEB40E7BD3\", \"Dolary\")",
        "CurrencyDescription":"","CurrencyValue":65}}
```
Param is `rowName` (error self-documents); wrong row → `row '<x>' not found in <Table>` clean error. FText fields serialize as NSLOCTEXT literals.

Interpretation: per-row reads work on cooked *Database tables and return the full struct as a flat JSON object with the row `Name` included; localization keys are preserved.

## I — payload shapes (verbatim)

### pie_status (raw, verbatim)
```json
{"ok":true,"running":false,"startPending":false,"sessionActive":false,"worldHasBegunPlay":false,"stopPending":false,"simulating":false,"state":"stopped","editorWorld":"Untitled"}
```

### list_level_actors (first 3 entries, verbatim)
```json
{"ok":true,"world":"Untitled","count":0,"matched":0,"truncated":false,"actors":[]}
```
(The open editor world is **Untitled** with zero actors from the editor-actor-subsystem view — there are no entries to show; the envelope shape `{ok, world, count, matched, truncated, actors[]}` is the payload fact.)

### scene_report top-level keys
`ok, actorCount, sceneMin{x,y,z}, sceneMax{x,y,z}, sceneSize{x,y,z}, floating[], sunken[], tooTall[], floatingCount, sunkenCount, tooTallCount` — on the Untitled world: `actorCount:1`, all hazard lists empty. Note the off-by-one flavor vs list_level_actors (`count:0`): scene_report's bounds walk sees one actor that GetAllLevelActors filters out — harmless, but worth remembering when cross-checking counts between the two endpoints.

Interpretation: payload shapes match the source-derived claims in I_diagnostics.md exactly (and independently reproduce the Phase-2 re-probe already recorded at I_diagnostics.md negative #8 — same bytes).

## F — world census

**SKIPPED by rule**: `pie_status.editorWorld == "Untitled"` and `list_level_actors.count == 0` — no real map is open. LevelInstance / LandscapeStreamingProxy / WaterBody* / Oceanology counts and landscape_info would census an empty default world and produce misleading zeros. Re-run after a `load_level` of the real overworld (a mutation, out of scope for this read-only pass).

## J — DLC leftovers

### list_mounted_containers
```json
{"ok":true,"ioDispatcherInitialized":true,
 "gameInstallDir":"C:/SteamLibrary/steamapps/common/Drug Dealer Simulator 2/DrugDealerSimulator2/Content/Paks",
 "containerCount":3,
 "containers":["global.utoc (623 B)","pakchunk0-Windows.utoc (11,548,076 B)","pakchunk0optional-Windows.utoc (7,123 B)"],
 "assetCounts":{"total":37131,"containerOnly":25285,"loose":11846,"loaded":6348}}
```
DLC mount roots visible in the registry: `/DDS2Casino/...`, `/ChristmasDlc/...` (both origin container — they ride pakchunk0, not separate utocs).

### GameFeatureData (the actual "DLC manager" assets)
`find_assets class=GameFeatureData` → exactly 2: `/ChristmasDlc/ChristmasDlc.ChristmasDlc` and `/DDS2Casino/DDS2Casino.DDS2Casino` (both `/Script/GameFeatures.GameFeatureData`, container, unloaded). Confirms J's "DLC = GameFeatures plugins activated by the GameInstance" model.

### describe_class on DLC manager classes
- **BP_CasinoSoundManager_C** (`/DDS2Casino/Blueprints/BP_CasinoSoundManager`) — parent **`/Script/DDS2CasinoRuntime.DDS2CasinoSoundManagerSubsystemBase`** — a NATIVE DLC runtime module (`DDS2CasinoRuntime`) exists and reflects in this editor. Own surface: `RequestSound(Equipment: ABP_CasinoEquipmentBase_C*, Sound: UMetaSoundSource*, TriggerName)`, `GetAudioComponent(...)`, `Find Closest Equipment Of(...)`, `ResetInteracted()`; props `MetaSoundInstances: TMap`, `MetaSoundInteracted: UMetaSoundSource*`, `AudioCompInteracted: UAudioComponent*`. (Casino audio is MetaSounds-based, pooled per equipment actor.)
- **BP_ChristmasPresentHandlerComponent_C** (`/ChristmasDlc/Blueprints/BP_ChristmasPresentHandlerComponent`) — parent plain `/Script/Engine.ActorComponent`. Own surface (38 fns): present-roll pipeline `AwardPlayerWithChristmasItem/Furniture/Vehicle`, `Player(Clothes|Weapons|Vehicles|Furniture)PresentOpened`, `DropPresentBag`, quest hooks `OnQuestTaskUpdated(TaskGuid, FTaskItemData)`, `ChangeChristmasTaskStatus(ETaskStatus)`, `ActivateChristmasTask`, `AttemptChristmasTaskLaunch`, `DebugFinishPreChristmasTask`; props: 4 `TSoftObjectPtr<UDataTable>` roll tables (`ClothesRollsDataTable`, `WeaponsRollsDataTable`, `FurnitureRollsDataTable`, `VehiclesRollsDataTable`) + `PresentStringDelimiter: FString`. Same FTaskItemData/ETaskStatus quest plumbing as the base game.
- ChristmasDlc content census: 367 assets — 84 Texture2D, 44 BlueprintGeneratedClass (mostly `BP_Christmas*` furniture/decor), 32 Material, **27 DataTable**, 1 GameFeatureData, 1 UserDefinedStruct, 1 StringTable. DDS2Casino: 146 BlueprintGeneratedClass incl. equipment lane (`BP_CasinoEquipmentBase_C` + Blackjack/Poker/Roulette/OneArmedBandit T1 variants, `BPI_Casino*` interfaces, `BP_QuestStarter`/`BP_QuestTimer`).

### Row-struct names for two Configs/Balance tables (read_datatable)
- `/Game/DataTables/Configs/ContainerConfigs` → rowStruct **`ContainerConfig`**, 61 rows, first row `PLAYER-CHARACTER`, fields `[Name, BaseContainerSize, BaseGridMaxWidth, BaseConcealedSlots, SlotDivision, SpecialSlotList]`.
- `/Game/DataTables/Balance/BalanceFlagsDatabase` → rowStruct **`BalanceFlagData`**, 170 rows, first row `STAMINA-USAGE-PLAYER`, fields `[Name, FlagName, FlagDescription, FlagValue, HasTestingValue, FlagTestingModeValue]`.

### Datatable census (context for the "122 *Database" figure)
`list_datatables` total **379**: by root — `/Game/DataTables` 214, `/Game/MODS` 76 (loose mod tables), `/ChristmasDlc/Data` 27, `/DDS2Casino` 33 (27 DataTables + 6 GUI), `/Game/Audio` 23, misc 6. "Database"-named: 187 everywhere / 102 under non-MODS `/Game`; exactly 95 live under `/Game/DataTables/Databases/`.

## Implications — Phase-1 claims confirmed / refuted

**Confirmed:**
1. **G1_ai_navigation.md "four distinct movement subsystems, all riding the stock AIModule stack"** — all four lanes now have live class-shape evidence: (a) task spline walk = `BP_SegmentedPathTaskMarker_C.PathSpline: USplineComponent*` + `PathPoints`/`CurPathPoint`; (b) patrol = `BP_OponentPatrolRoute_C` spline actors chained via `StartRoute`/`EndRoute` with faction gates; (c) opponents = `BP_OponentAIController_C : ADetourCrowdAIController` with `MyBehaviorTree` + AIPerception; (d) NPC base = native `/Script/DrugDealerSimulator2.BaseNPC` under a dialogue-only BP shell. CONFIRMED, and the UNVERIFIED entry "Everything class-shaped about BP_OponentAIController_C (CrowdFollowingComponent? perception config?) ... queued" is now RESOLVED: it IS the DetourCrowd controller, perception is stock `UAIPerceptionComponent` + BP wrapper component.
2. **G1 spline story ("set_spline_points exists BECAUSE BP_SegmentedPathTaskMarker.PathSpline walks NPCs", Surface inventory)** — property name confirmed verbatim: `PathSpline`.
3. **J_dds2_project.md "DLC = GameFeatures plugins activated by the GameInstance"** — exactly 2 GameFeatureData assets (`/ChristmasDlc/ChristmasDlc`, `/DDS2Casino/DDS2Casino`) exist; both DLC roots are container-origin inside pakchunk0 (3 utocs total, no per-DLC utoc). NEW fact for J: a native `DDS2CasinoRuntime` module reflects live (`DDS2CasinoSoundManagerSubsystemBase`) — the casino DLC ships native code, not just content.
4. **J "quests/shops/dialogue are compositions over the native spine"** — BP_BaseNPC_C's 113 own functions are all dialogue/trade/task/meeting surface over native `BaseNPC`; DLC quest hooks reuse the same `FTaskItemData`/`ETaskStatus`/TaskGuid plumbing (`BoundTaskUpdate`, `OnQuestTaskUpdated`).
5. **I_diagnostics.md payload-shape claims (list_level_actors envelope via GetAllLevelActors, MifBridgeLevel.cpp:130-199; pie_status)** — live payloads match byte-for-byte, independently reproducing the re-probe recorded at I_diagnostics.md negative #8.
6. **H_data.md cooked-curve readability** — full FRichCurve keys (tangents, interp modes, extrap) read back via get_property on a container-origin CurveFloat; curve endpoints (read_curve/set_curve_keys designs) are viable against cooked content. Also: CurveTable count is **0** — curve-TABLE proposals have zero project demand; CurveFloat (46) is the real surface.
7. **H RamaSave**: all three classes reflect fully from `/Script/RamaSaveSystem` (engine actor + component + 29-function library). Consistent with "real plugin binaries loaded"; note H_data.md's Surface-inventory observation that the SDK SOURCE has stub bodies — reflection cannot distinguish stub bodies from real ones, so behavioral proof (a PIE save/load) remains the outstanding test. Partially resolves stub-vs-real: the reflected API contract is complete and scriptable.

**Refined (not refuted):**
8. **PROGRESS.md J-entry "122 *Database tables"** — live registry (mods + DLC visible) counts 187 Database-named tables total; scoped to non-MODS `/Game` it is 102, and `/Game/DataTables/Databases/` proper holds 95. The 122 figure matches no live scope exactly — whatever filter produced it, use `list_datatables` scoping (379 total / 214 under `/Game/DataTables`) as the authoritative census going forward.
9. **PROGRESS.md J-entry "premade asset registry leaves mounts invisible to find_assets"** — needs refinement: DLC content under `/DDS2Casino` and `/ChristmasDlc` IS visible to find_assets/list_datatables in this session (origin container). The invisibility claim applies to NEWLY mounted paks (mount_pak lane), not the boot-time-mounted DLC containers.

**Refuted:**
10. **Any expectation of a Pedestrian/Crowd/Citizen ambient-population asset family (G1 registry-mining queries)** — live registry confirms zero assets under those names; the only "Population" assets are two billboard sprite textures (Human/Animal). Ambient population is not an asset-named system; crowd behavior enters via DetourCrowd at the controller level.

**Bridge-quality notes for endpoint docs:** get_property/list_object_properties demand `objectPath`/`propertyPath` (not `object`/`property`); get_datatable_row demands `rowName`; all three self-document via error strings. describe_class output for BP classes includes ALL inherited members — Phase-3 tooling should diff against the parent class (as done here) or grow an `ownOnly` flag.

**G2 tiering implication (G2_sequencer_umg_input.md):** UMG endpoints should be tiered against the 279 cooked WidgetBlueprintGeneratedClass assets (not the 54/35 WidgetBlueprint count); Enhanced Input is small-but-real (62 InputActions / 5 contexts, 1 DLC-mounted); Sequencer surface is 3 game LevelSequences (menu/lobby/showreel) → lowest tier.
