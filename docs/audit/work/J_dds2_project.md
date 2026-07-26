# Axis J — DDS2 project-specific
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork "CookedEditorModKit"). Agent: phase-1 breadth._
_Phase-2 adversarial verification: 2026-07-26. Every cited engine/plugin file re-opened; all 8 proposals verified (0 corrected, 0 demoted); all 10 negatives spot-checked, 0 overturned. Live bridge re-probed (POST /api/*, X-Mif-Token) for negatives #6/#9 and container counts._
_Phase-2 second independent pass (2026-07-26, later same day): every file:line re-opened from scratch, all signatures re-matched verbatim, Mount/ScanPathsSynchronousInternal hazard-swept (no FMessageDialog/FScopedSlowTask/WaitForProc/Sleep in either path — the one Sleep(3.0f) in IPlatformFilePak.cpp:8855 is inside the pak.AsyncFileTest console command, outside Mount 8022–8198), live bridge re-probed (find_assets Population → 2 billboard hits; unknown-param `recursive:false` still silently accepted; list_mounted_containers → 3 containers 623/11,548,076/7,123 B, ioDispatcherInitialized:true, 37,131/25,285/11,846 counts identical). One verdict amended (read_modloader_log — live-log counts drifted because UE4SS.log is rewritten per game session; see entry). 0 corrected, 0 demoted, 0 negatives overturned._
_Live instrument: MifBridge on http://127.0.0.1:8791 (relaunched session, editor world Untitled —
no map loaded; registry/class/table probes unaffected, no level probes attempted)._

> Session note: the live editor was found shut down at sweep start (a previous automation session
> issued `Cmd: QUIT_EDITOR` at 05:44:56 UTC — clean exit, per the prior
> `D:/DDS2SDK/Game/Saved/Logs/DrugDealerSimulator2*.log`). Relaunched with the same command line
> (`D:/UE532/Engine/Binaries/Win64/UnrealEditor.exe "D:\DDS2SDK\Game\DrugDealerSimulator2.uproject"`).
> First relaunch raced a concurrent second instance and both died (see Negative result #10);
> second single-instance relaunch booted clean and served every live probe below. One find_assets
> probe with 156 routes bound was also served by the PREVIOUS session before it went down
> (37,131-asset count — later re-confirmed identical in the relaunched session).

## Surface inventory

### Plugin handlers read (full files, D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private)
- `MifBridgeCooked.cpp` (1017 lines) — H_list_mounted_containers, H_find_assets, H_describe_package,
  H_diagnose_landscape, H_diagnose_landscape_draws. All read-only. Key facts recorded below.
- `MifBridgePipeline.cpp` (143 lines) — H_read_modloader_log (tails UE4SS.log, params `path`,
  `lines` 1..5000 default 80, `filter` substring), H_trigger_cook (**PLAN-ONLY**: hardcoded
  GameRoot `C:/SteamLibrary/steamapps/common/Drug Dealer Simulator 2/DrugDealerSimulator2` and
  retoc at `C:/Users/andre/.cargo/bin/retoc.exe`; returns `executed:false` + a 6-step retoc
  to-legacy → byte-patch → to-zen → deploy plan. It cooks NOTHING and there is no status poll
  because there is no process).
- `MifBridgeDataTables.cpp` lines 1–170 — list_datatables `{filter}`, read_datatable
  `{path, maxRows<=10000}` via `UDataTable::GetTableAsJSON()` (WITH_EDITOR), get_datatable_row
  `{path, rowName}` (serialises the WHOLE table then linear-searches the row — O(table) per call).
- `MifBridge.Build.cs` — confirmed **PakFile is NOT a current module dep** (list matches brief).

### Live-install / mod-loader filesystem swept (read directly on disk)
- `D:/DDS2SDK/Game/GameInstallDirectory.txt` → `C:\SteamLibrary\...\DrugDealerSimulator2\Content\Paks`
- Live Paks dir: `global.utoc/.ucas` + `pakchunk0-Windows.pak/.ucas(16.9 GB)/.utoc` +
  `pakchunk0optional-Windows.*` = the 3 IoStore containers the modkit mounts (editor log
  confirms: `LogPakFile: Display: [ModKit] Found 3 IoStore container(s)`).
- `Content/Paks/Mods/` (flat override _P paks): BiggerPackages_P, LargeBolivars_P, MifCentrifuge_P
  (+ .retired files). `Content/Paks/LogicMods/` (BPModLoader ModActor mods, one dir each):
  BotanistExpansion_P, BrandosCartelExpansion_P, BrandosDDS2Helper_P, DriveableScooter,
  EthanolExtraction, MifCore, SpecialClientMarker.
- UE4SS at `<game>/Binaries/Win64/ue4ss/`: UE4SS.dll v3.0.1, UE4SS.log (112 KB live sample read),
  `Mods/` = BPML_GenericFunctions, BPModLoaderMod, BotanistExpansion_Lua, CartelDemandFlags,
  CheatManagerEnablerMod, ConsoleCommandsMod, ConsoleEnablerMod, DriveableScooter,
  EthanolExtraction_Lua, Keybinds, LineTraceMod, MifEconLogger, MifTools, SpecialClientMarker,
  SplitScreenMod, mods.txt, shared/.
- UE4SS.log line-shape histogram over the full live log (1,398 lines): 998 `[ts] message`,
  ~350 `[ts] [Lua] [ModName] message`, ~40 `[ts] [HookTag] message`, 2 lines with no timestamp
  (`##### MEMBER OFFSETS START ...`). Format is regular enough for structured parsing.

### Engine headers read (D:/UE532/Engine/Source)
- `Runtime/PakFile/Public/IPlatformFilePak.h` lines 2199–2360 (Mount/Unmount/MountAllPakFiles/
  ReloadPakReaders/MountModKitGameContainers/GetTypeName/GetMountedPakFilenames).
- `Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h` lines 149–151, 546–597
  (ScanPathsSynchronous / ScanFilesSynchronous / ScanModifiedAssetFiles; `class IAssetRegistry`
  itself carries NO export macro — pure-virtual interface reached via FAssetRegistryModule).
- `Runtime/Core/Public/IO/IoDispatcher.h` lines 700–750 (FIoStoreReader — every method CORE_API).
- `Runtime/Core/Public/GenericPlatform/GenericPlatformProcess.h` lines 444–529 (CreateProc /
  IsProcRunning / GetProcReturnCode / TerminateProc — all `static CORE_API`).
- `Runtime/Core/Public/Misc/CoreDelegates.h` lines 115–119 (GetOnPakFileMounted2).
- `Runtime/Core/Public/HAL/PlatformFileManager.h` lines 16–51 (FindPlatformFile CORE_API).

### Editor-session facts (from D:/DDS2SDK/Game/Saved/Logs/DrugDealerSimulator2.log)
- `LogPakFile: Initializing PakPlatformFile` — **FPakPlatformFile IS the active platform-file
  layer in this modkit editor** (also `LogPakFile: Destroying PakPlatformFile` at shutdown).
- `Initialized I/O dispatcher file backend. Running with -useiostore without the global container.`
- Editor binary: `D:/UE532/Engine/Binaries/Win64/UnrealEditor.exe`, cmdline = just the .uproject.
- MifBridge binds 156 routes at module-load time, frame [0] — the HTTP listener is up ~3 min
  before the map finishes loading (requests during map load queue/time out: observed live).

## DDS2 systems map (for modding agents)

All facts below were pulled LIVE through the bridge (probe → result). The game module is native
C++ (`/Script/DrugDealerSimulator2`) with cooked BPGC children; `describe_class` works on BOTH.

### Content geography (find_assets histograms)
- Registry totals (list_mounted_containers): **37,131 assets; 25,285 container-only; 11,846
  loose; 425 loaded** at idle. 3 IoStore containers (global.utoc 623 B, pakchunk0-Windows.utoc
  11,548,076 B, pakchunk0optional-Windows.utoc 7,123 B).
- `/Game` = 26,797 assets. `/Game/Blueprints` = 1,160: NPC 453, Enviro 169, Pawns 144,
  Furniture 143, LabEquipment 94, QuickTravel 31, Hideouts 25, Interfaces 22, Structures 17,
  SystemClasses 12, Objects 11, SpawnedEffects 10, Components 8, WorldControllers 6, Libraries 4,
  Enums 3.
- DataTables: **379 total** (list_datatables). Core spine `/Game/DataTables/`: ShopOffers 99,
  Databases 95 (+IslaSombra/ overrides ⇒ 122 database tables), TownRepEvents 14, Configs 4,
  Balance 2. DLC plugins mount their own: `/DDS2Casino/DataTables/Databases` 27,
  `/ChristmasDlc/Data/*`. Mod tables live LOOSE under `/Game/MODS/<Mod>/` (BrandosCartelExpansion_P
  31, BotanistExpansion_p 27 …).
- `/Game/MODS` = 2,780 assets, **all origin=loose** — the SDK-side mod-authoring area
  (BotanistExpansion_p 1,767; BrandosCartelExpansion_P 229; DriveableScooter 3).

### Load-bearing classes (describe_class, live)
| Class | Parent | Surface | Key members (verbatim from describe_class) |
|---|---|---|---|
| `DDS2_GameMode_C` (/Game/Blueprints/SystemClasses/DDS2_GameMode) | `/Script/DrugDealerSimulator2.DrugDealerSimulator2GameModeBase` | 227 fns / 98 props / 29 dispatchers | fns TryGenerateClient, GenerateRandomClientConfig, DrugSaleConsequences, TownChangeReputation, RequestActivateTask, RequestChangeTaskStatus, RegisterTown; props BP_QuestManager, TownManagerMap, CurGeneratedClients, ClientTimeouts, CurSpawnedTraders; disp TimeSkipped, DayNightSwitch, BankMoneyChanged, TownRepLevelChanged, TownDemandChanged, TownDebtChanged, ClientFail, NewKeyObtained |
| `BP_QuestManager_C` (/Game/Blueprints/Components/BP_QuestManager) — an ActorComponent owned by the GameMode | `/Script/DrugDealerSimulator2.QuestManager` (native) | 36/10/4 | fns RegisterNewTask, GetTaskStatus, GetTaskList, GetTaskHistory, GetClientTaskData, ChangeTaskStatus(ByName), ShareTaskToPlayer; props TaskDatabase, TaskList, TaskNameMap, TaskClientMap, TasksHistory; disp QuestListUpdate, QuestTaskUpdate |
| `BP_TownStatusManager_C` (/Game/Blueprints/WorldControllers/BP_TownStatusManager) — per-town world actor; this IS the population/economy manager | `/Script/DrugDealerSimulator2.TownStatusManager` (native, 139/36/16 on its own) | 181/64/18 | fns UpdateRepDemand, GetDrugDatabasePrice, CheckCanSpawnRandomClient, SpawnMapClient, FindSpawnPoint, GenerateTutorialClients, RegisterEnemy, CheckEnemySpawnChance, BuyRepEvent, GetTownRepEventList, PartialPackageSale; props SpawnedClientsMap, ClientSpawnLocations, ClientSpawns, WorkerSpawnSpots, RepTownLevel, RepTownReputation, InfluencerBalance, RepEventsWorking; disp TownReputationChanged, DeadClientListChanged |
| `BP_DDS2_GameInstance_C` (/Game/Blueprints/SystemClasses/BP_DDS2_GameInstance) | `/Script/AdvancedSessions.AdvancedFriendsGameInstance` | 18/13/4 | fns ActivateDlcGameFeaturePlugin, MountDlcGameFeaturePlugin, LoadDlcGameFeaturePlugin, FindLocalAvailableDlc, RunAllDlcActivations; props CurCartel, CartelName, ActiveDlcs, PendingGameFeaturePlugins — DLC = GameFeatures plugins |
| `DialogueObject_C` (/Game/Blueprints/Objects/DialogueObject) — plain UObject dialogue runner | `/Script/CoreUObject.Object` | 58/13/0 | fns InitiateDialogue, SelectResponse, RunDialogueTree, GraphStep, DisplayDialogueStep, EndDialogue, OpenTrade, GivePlayerItem, TakePlayerMoneyCash, `Activate Task`, ChangeTaskStatus, UnlockDialogueTag; props DialogueResponses, GraphResponses, CurGraphIndex, QuestionTags, RewardOnTag. **Function names may contain SPACES** (`Give Player Money Cash`, `Check Player Has Quest Item`) — endpoint param handling must not assume identifier-safe names |
| `BP_BaseNPC_C` (/Game/Blueprints/Pawns/NPC/BP_BaseNPC) | `/Script/DrugDealerSimulator2.BaseNPC` (native) | 281/82/23 | fns MainInteraction, ClientInteraction, OpenTrade, CheckDialogueCondition, DialActivateTask, DialChangeTaskStatus, GetShopID, PlayDialogueSound; props DialogueObject, CurDialogue, ShopID, CharacterID, GenCharacterGuid, NPCBody, Influence; disp ScriptedEvent. A LOOSE editable child exists: `/Game/Blueprints/NPC/IslaSombra/ISL/BP_BaseNPC_Editable` (class Blueprint, not BPGC) |
| `BP_DDS2_PlayerController_C`, `BP_DDS2_CameraManager_C`, `BP_MainMenu_GM_C`, `BP_LobbyGM_C`, `BP_ConsoleCommands_C` | — | — | rest of /Game/Blueprints/SystemClasses (12 assets, all container) |

Other managers found (Components folder): `BP_CartelManagerComponent_C`, `BP_CommunicationManager_C`,
`BP_StatsManager_C`. WorldControllers: `PrisonCampController_C`, `PrisonRingController_C`,
`PrisonEscapePlace_C`, `Ultra_Dynamic_Weather_BTR_C`.

### Data spine (the tables a mod agent actually edits)
122 `*Database` tables under `/Game/DataTables/Databases[/IslaSombra]`, including: QuestlineDatabase
(rowStruct `QuestlineData`, 5 rows) / IS_QuestlineDatabase; TaskDatabase (rowStruct `TaskData`,
22 rows) / **IS_TaskDatabase (254 rows — the real quest content)**; DrugDatabase (rowStruct
`DrugData`: DrugName, DrugSubstance(s), AveragePricePerGram …); ItemDatabase; ShopDatabase /
IS_ShopDatabase + 99 ShopOffers tables; TownDatabase / IS_TownDatabase; CharacterDatabase /
IS_CharacterDatabase; DialogueTagDatabase + DialogueTagCategoryDatabase; ProductionChainDatabase;
CraftingRecipiesDatabase; LaunderingOptionDatabase; ReputationConsequencesDatabase;
StressEventDatabase; UsageEffectsDatabase; WorldKeyDatabase.
`TaskData` row fields (from read_datatable): Name, DevNotatka, TaskName, TaskShortDescription,
TaskLongDescription, TaskImage, AutoFocusTask, RequiredMoney, RequiredItems, RelatedQuestline,
TasksOnComplete, TasksOnFailure, FailTasksOnComplete, RecordedMessagesOnComplete, RewardOnComplete,
CartelXPOnComplete, IsAreaTask, AreaRange, SharingPolicy, OneTimeTask, NarrationOnActive,
NarrationOnCompleted.

### How the big systems hang together (derived, each edge probe-verified)
- **Quests**: static content = QuestlineDatabase + TaskDatabase rows (DataTable-driven, so
  authoring = write_datatable_rows on a LOOSE copy / mod table). Runtime state = GameMode's
  `BP_QuestManager` component (`TaskList`, `TaskNameMap`, `TasksHistory`) — PIE-only, reachable
  via list_pie_actors → get_property.
- **Economy/population**: per-town `BP_TownStatusManager` actors registered into GameMode's
  `TownManagerMap`; demand/pricing via `UpdateRepDemand`/`GetDrugDatabasePrice` against
  DrugDatabase; client NPCs generated by GameMode `TryGenerateClient`/`GenerateRandomClientConfig`
  into `CurGeneratedClients`, spawned at `ClientSpawnLocations`/`RandomClientSpawnPoint_C` markers.
  There is NO single "population manager" class — population = TownStatusManager + GameMode.
- **Dialogue**: NPC (`BP_BaseNPC_C.DialogueObject`) owns a `DialogueObject_C` graph-runner
  (`GraphResponses`/`RunDialogueTree`); dialogue side-effects call straight into quest/task and
  trade functions. Not a UEdGraph — no graph endpoints applicable; it is data on the CDO,
  readable via get_property/list_object_properties.
- **Shops**: NPC `ShopID` → ShopDatabase/IS_ShopDatabase row → per-shop ShopOffers table (99 of
  them). Pure DataTable composition.
- **DLC**: GameFeatures plugins activated by the GameInstance (ChristmasDlc, DDS2Casino mount
  their own /PluginName/ content roots — visible in find_assets/list_datatables already).

## Compositions (no new endpoint needed)

Diffed against all 159 covered endpoints. These candidate ideas are already reachable:

- **list_quests / describe_quest** — quests are DataTable-driven. `list_quests` =
  `read_datatable {path:"/Game/DataTables/Databases/IslaSombra/IS_QuestlineDatabase.IS_QuestlineDatabase"}`
  (+ base QuestlineDatabase + DLC variants); `describe_quest` = `get_datatable_row` on
  IS_TaskDatabase (254 rows) filtered client-side by `RelatedQuestline`. Row JSON is complete on
  cooked tables (verified live — see cooked-behaviour section). No endpoint needed.
- **describe_shop** — `get_property {objectPath:"<NPC>.Default__<NPC>_C", propertyPath:"ShopID"}`
  → `get_datatable_row` ShopDatabase → `read_datatable` the named ShopOffers table.
- **runtime quest/economy state during PIE** — `list_pie_actors` → `get_property` with the PIE
  actor path and propertyPath `TaskList` / `SpawnedClientsMap` / `RepTownReputation`. (The
  managers hold runtime-only state; there is nothing here existing reads can't compose.)
- **describe dialogue tree** — `list_object_properties` / `get_property` on
  `DialogueObject_C` CDO or a placed NPC's `DialogueObject` instance (`DialogueResponses`,
  `GraphResponses`). It is object data, not a graph asset.
- **game_data_index** — `list_datatables {filter:"/Game/DataTables/Databases"}` already returns
  the 122-table spine; filter param verified live.
- **"what is cooked vs editable"** — `find_assets {origin:"loose"}` vs `{origin:"container"}`
  already splits the world exactly (25,285 container / 11,846 loose, verified).
- **cook/pak plan** — `trigger_cook` already returns the verified retoc plan with pinned paths;
  what is MISSING is execution+polling, proposed below as `mod_package_request`/`_status`.

## Proposed endpoints

### verify_pak_contents
**Purpose**: enumerate what is actually inside a .utoc/.ucas (or retoc trio) BEFORE/AFTER deploying a mod — today an agent ships a pak blind and can only infer content from runtime behaviour.
**Engine API**:
```cpp
UE_NODISCARD CORE_API FIoStatus Initialize(const TCHAR* ContainerPath, const TMap<FGuid, FAES::FAESKey>& InDecryptionKeys);
CORE_API EIoContainerFlags GetContainerFlags() const;
CORE_API void EnumerateChunks(TFunction<bool(FIoStoreTocChunkInfo&&)>&& Callback) const;
CORE_API void GetFilenames(TArray<FString>& OutFileList) const;
```
`FIoStoreReader` — Runtime/Core/Public/IO/IoDispatcher.h:716, :719, :721, :740. Flags enum (`Indexed = (1 << 3)`, `Encrypted = (1 << 1)`) at IoDispatcher.h:459–467.
**Export**: `CORE_API` (per-method, class FIoStoreReader at IoDispatcher.h:710) | **Module**: none — Core already linked | **Guards**: none
**Bucket**: read-only — opens the container file directly; touches no UObject state, no mount.
**Async**: no (pure file read; an 11.5 MB utoc TOC parses in well under a frame; refuse >100 MB utoc with an error rather than stalling).
**Params**: | name | aliases | type | default | required |
| utocPath | path | string (absolute .utoc, or .pak — sibling .utoc derived via `FPaths::ChangeExtension`) | — | yes |
| filter | contains | string substring on filenames | "" | no |
| limit | — | int 1..5000 | 200 | no |
Unrecognised parameter ⇒ error naming it.
**Failure modes**:
- file missing ⇒ `"utocPath not found: <path> — pass the absolute path to the .utoc (or its sibling .pak)"`
- `Initialize` returns bad status (corrupt/wrong version) ⇒ surface `FIoStatus::ToString()` verbatim.
- container lacks `EIoContainerFlags::Indexed` ⇒ chunks enumerable but **no filenames**; return `indexed:false`, `chunkCount`, and message `"container has no directory index — filenames unavailable; chunk IDs only"` (base-game pakchunk0 may be in this state; retoc-built mod containers are indexed).
- `Encrypted` flag set and no key registered ⇒ `"container is encrypted — no AES key available"` (DDS2 base containers mount without keys, so expected unencrypted).
**Cooked**: this endpoint's entire purpose is cooked content; works identically with the editor open because it reads the file, not the mount.
**Verify**: `verify_pak_contents {utocPath:"C:/SteamLibrary/.../Content/Paks/Mods/MifCentrifuge_P.utoc"}` → `fileCount` equals `retoc list` row count for the same container; filenames include the expected `ExportBundleData` package path.
**Score**: U4 E4 R5 → tier 1 — closes the "did my repack actually contain the asset" loop that today needs an out-of-editor retoc run.
**Phase-2 verdict**: CONFIRMED — all four signatures verbatim at IoDispatcher.h:716/:719/:721/:740 (class FIoStoreReader at :710, every method CORE_API — no PakFile module needed, compiles against Core alone); EIoContainerFlags `Encrypted=(1<<1)`, `Indexed=(1<<3)` verified at IoDispatcher.h:459–467; `FIoStatus::ToString()` exists and is CORE_API (Runtime/Core/Public/IO/IoStatus.h:64) so the failure-mode surfacing compiles. Bucket/async/params/cooked all consistent; no hidden blocking (pure file read). Name collides with nothing in the 160-endpoint list.

### mount_pak
**Purpose**: mount a mod's retoc trio (or any pak/IoStore container) into the RUNNING editor so its packages become loadable without restarting the session.
**Engine API**:
```cpp
PAKFILE_API bool Mount(const TCHAR* InPakFilename, uint32 PakOrder, const TCHAR* InPath = NULL, bool bLoadIndex = true);
```
`FPakPlatformFile` — Runtime/PakFile/Public/IPlatformFilePak.h:2306. Instance located via:
```cpp
CORE_API IPlatformFile* FindPlatformFile( const TCHAR* Name );
```
FPlatformFileManager — Runtime/Core/Public/HAL/PlatformFileManager.h:51, with `FPakPlatformFile::GetTypeName()` (inline, returns `TEXT("PakFile")`, IPlatformFilePak.h:2201–2204). Editor log proves the layer is live in this modkit editor: `LogPakFile: Initializing PakPlatformFile`.
Mount of a `.pak` **also mounts the sibling `.utoc`** through the IoDispatcher backend and registers it with the package store — verified in source: `FString UtocPath = FPaths::ChangeExtension(InPakFilename, TEXT(".utoc")); ... IoDispatcherFileBackend->Mount(*UtocPath, PakOrder, ...); PackageStoreBackend->Mount(Pak->IoContainerHeader.Get(), PakOrder);` Runtime/PakFile/Private/IPlatformFilePak.cpp:8112–8124. Post-mount notification delegate exists: `static CORE_API TTSMulticastDelegate<void(const IPakFile&)>& GetOnPakFileMounted2();` Core/Public/Misc/CoreDelegates.h:119.
**Export**: `PAKFILE_API` (method-level; verified verbatim) | **Module**: **NEW dep `PakFile`** (runtime module, editor links it already at engine level; adding to MifBridge.Build.cs is editor-only usage — MifBridge never leaks to runtime) | **Guards**: none for Mount itself (`MountModKitGameContainers` is `#if WITH_EDITOR`, IPlatformFilePak.h:2308–2316, but we do not call it)
**Bucket**: self-managed — mutates process-global file-system state; must NOT be inside the blanket transaction (Ctrl-Z cannot unmount a container; an undo entry would be a lie).
**Async**: no — Mount is synchronous (base-game 16.9 GB ucas mounts during boot in this session; a mod trio is milliseconds).
**Params**: | name | aliases | type | default | required |
| pakPath | path | string absolute .pak (trio: sibling .utoc/.ucas auto-picked-up per engine behaviour above) | — | yes |
| order | pakOrder | int 0..1000, mapped onto PakOrder (higher = wins conflicts; default above base-game so _P overrides behave like runtime) | 500 | no |
Unrecognised parameter ⇒ error naming it.
**Failure modes** (spelled out — this is the risky one):
- pak layer absent (`FindPlatformFile(TEXT("PakFile"))` null) ⇒ `"no PakPlatformFile in this session — editor was launched without pak layer"` (not expected in the modkit editor, but the check is mandatory).
- file missing / Mount returns false ⇒ `"Mount failed for <path> — LogPakFile has the reason (invalid pak / missing sibling .utoc)"`.
- **already-loaded package shadowing**: mounting an override _P for a package that is ALREADY loaded in this session does NOT touch in-memory objects; the override is only seen by future loads. Return `warning:"N of the container's packages are currently loaded; in-memory objects unchanged"` (count via `FindPackage` over the container's filenames from FIoStoreReader).
- **asset registry blindness**: the modkit editor uses a PREMADE registry (`bUsePremadeInEditor = true` unless `-DisablePremadeAssetRegistry`, Runtime/AssetRegistry/Private/AssetRegistry.cpp:186–192); a freshly mounted container's packages stay invisible to find_assets. Return `registryVisible:false` and point at refresh_asset_registry / the direct-load composition below. Registry purge protection for mounted containers exists engine-side (`ModKit_IsPackageInMountedIoStoreContainer`, AssetRegistry.cpp:61–70).
- mounting the SAME pak twice ⇒ engine tolerates but double-entry; handler must pre-check via `GetMountedPakFilenames` (FORCEINLINE, IPlatformFilePak.h:2229) and error `"already mounted: <path> — unmount_pak first"`.
**Cooked**: cooked content is the whole point; the mounted packages load through the modkit's cooked-editor path (EditorPackageLoader).
**Verify**: mount → `verify_pak_contents` filenames → `describe_package {package:<one of them>}` flips `origin` to `container` + `existsOnDisk:false`; `get_property` on a known object inside the pak returns a value. Numbers: `list_mounted_containers.assetCounts` unchanged (registry blind — expected) but `GetMountedPakFilenames` count +1.
**Score**: U4 E3 R2 → tier 2 — mounting over a live session is inherently risky (shadowing, registry drift); the endpoint is honest about both instead of pretending.
**Phase-2 verdict**: CONFIRMED — Mount signature verbatim PAKFILE_API at IPlatformFilePak.h:2306; FindPlatformFile CORE_API at PlatformFileManager.h:51; GetTypeName inline at :2201–2204; GetMountedPakFilenames FORCEINLINE at :2229; MountModKitGameContainers inside `#if WITH_EDITOR` at :2308–2316 as claimed. The load-bearing sibling-utoc claim re-verified in the implementation: `FPaths::ChangeExtension(InPakFilename, TEXT(".utoc"))` at IPlatformFilePak.cpp:8112, `IoDispatcherFileBackend->Mount(*UtocPath, PakOrder, ...)` at :8119, `PackageStoreBackend->Mount(Pak->IoContainerHeader.Get(), PakOrder)` at :8124 (utoc mount gated on `IoDispatcherFileBackend.IsValid()` at :8099 — live session reports `ioDispatcherInitialized:true`, re-probed 2026-07-26). Premade-registry blindness re-verified: `bUsePremadeInEditor = true` unless `-DisablePremadeAssetRegistry` at AssetRegistry.cpp:186–192 (exact lines 188/191), `ModKit_IsPackageInMountedIoStoreContainer` → `FIoDispatcher::Get().DoesChunkExist(...)` at AssetRegistry.cpp:62–69. GetOnPakFileMounted2 verbatim at CoreDelegates.h:119. PakFile confirmed a Runtime module (Runtime/PakFile/PakFile.Build.cs exists) and confirmed absent from MifBridge.Build.cs deps. Mount path read end-to-end: no modal dialogs, no multi-frame waits — synchronous-inline as claimed. Bucket self-managed correct (process-global, not undoable).

### unmount_pak
**Purpose**: undo mount_pak in the same session (iterate: repack → remount).
**Engine API**:
```cpp
PAKFILE_API bool Unmount(const TCHAR* InPakFilename);
```
FPakPlatformFile — Runtime/PakFile/Public/IPlatformFilePak.h:2318.
**Export**: `PAKFILE_API` | **Module**: NEW dep `PakFile` (same as mount_pak) | **Guards**: none
**Bucket**: self-managed — same reasoning as mount_pak.
**Async**: no.
**Params**: | pakPath | path | string | — | yes | — must equal the path given to mount_pak. Unrecognised ⇒ error.
**Failure modes**:
- not currently mounted ⇒ `"not mounted: <path> (list via GetMountedPakFilenames)"`.
- **objects still live from this container** ⇒ subsequent lazy loads (textures' bulk data, streamed mips) fail or crash. Handler must count live packages from the container (FindPackage over its filename list) and refuse with `"N packages from this container are loaded — unmounting would leave dangling bulk-data references; restart the editor instead"` unless `force:true` (bool, default false).
**Cooked**: container-only concept; same as mount.
**Verify**: `GetMountedPakFilenames` count −1; `describe_package` on a container package flips `inRegistry`/load behaviour back (fresh loads fail with file-not-found).
**Score**: U2 E3 R1 → tier 3 — the refuse-when-loaded guard is the only thing separating this from a crash button; exotic but completes the pair.
**Phase-2 verdict**: CONFIRMED — `PAKFILE_API bool Unmount(const TCHAR* InPakFilename);` verbatim at IPlatformFilePak.h:2318. Same module/bucket reasoning as mount_pak (verified there). The refuse-when-loaded guard is correctly mandatory; keep it non-optional in implementation review.

### refresh_asset_registry
**Purpose**: make the asset registry learn about NEW loose files (mod authoring drops files into /Game/MODS out-of-editor: retoc to-legacy output, copied .uasset packs) without restarting; the loose half of the mount story.
**Engine API**:
```cpp
virtual void ScanPathsSynchronous(const TArray<FString>& InPaths, bool bForceRescan = false, bool bIgnoreDenyListScanFilters = false) = 0;
virtual void ScanModifiedAssetFiles(const TArray<FString>& InFilePaths) = 0;
virtual void AppendState(const FAssetRegistryState& InState) = 0;
```
IAssetRegistry — Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:550, :597, :729. Optional registry-blob lane: `ASSETREGISTRY_API bool Load(FArchive& Ar, const FAssetRegistryLoadOptions& Options = FAssetRegistryLoadOptions(), FAssetRegistryVersion::Type* OutVersion = nullptr);` FAssetRegistryState — AssetRegistryState.h:541.
**Export**: `class IAssetRegistry` has NO export macro (IAssetRegistry.h:150) — irrelevant: all calls are virtual dispatch through `FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get()`, the exact pattern MifBridgeCooked.cpp already uses. `ScanPathsSynchronous`/`ScanModifiedAssetFiles` are additionally `UFUNCTION(BlueprintCallable)`. | **Module**: none — AssetRegistry already linked | **Guards**: none
**Bucket**: read-only in transaction terms (registry is not undoable state; no FScopedTransaction) — implement as `self-managed` with no transaction to avoid an empty undo entry.
**Async**: no for ScanPathsSynchronous on targeted paths (it is synchronous by contract; restrict to explicit subpaths, refuse bare "/Game" with `"path too broad — pass a subfolder like /Game/MODS/<YourMod>"` to bound the stall).
**Params**: | name | aliases | type | default | required |
| paths | path | array of content paths (or single string) | — | yes |
| forceRescan | force | bool | false | no |
| registryBlob | appendState | string absolute path to an AssetRegistry.bin to Load+AppendState (for mod paks that carry one) | "" | no |
Unrecognised ⇒ error.
**Failure modes**:
- path outside a mounted content root ⇒ `"path not mounted: <p> — mount point must exist (FPackageName::MountPointExists)"`.
- **IoStore-only packages are NOT scannable**: ScanPathsSynchronous walks the file system; zen-container chunks have no file entries, so a mount_pak'd trio will not appear (this is why the modkit ships a premade registry — AssetRegistry.cpp:186). The endpoint must say so in its response when a scanned path yields 0 new assets but the pak layer holds a matching container. registryBlob is the honest lane for that case.
- registryBlob unreadable/wrong version ⇒ surface `FAssetRegistryVersion` mismatch verbatim.
**Cooked**: refuses to help for container-only content (documented above); works for loose files regardless of cooked flags.
**Verify**: drop a known .uasset under /Game/MODS/Test, call with that folder → `find_assets {pathPrefix:"/Game/MODS/Test"}` count goes 0 → N; delta reported as `assetsAdded`.
**Score**: U3 E4 R3 → tier 2 — the missing half of out-of-editor file drops; design must not overpromise the IoStore case.
**Phase-2 verdict**: CONFIRMED — ScanPathsSynchronous verbatim at IAssetRegistry.h:550 and ScanModifiedAssetFiles at :597, both genuinely `UFUNCTION(BlueprintCallable)` (:549/:596); AppendState verbatim at :729 (NOT a UFUNCTION — entry correctly does not claim it); `class IAssetRegistry` carries no export macro at :150 exactly as stated, and the module-singleton dispatch route is the same one MifBridgeCooked.cpp already uses; `ASSETREGISTRY_API bool Load(...)` verbatim at AssetRegistryState.h:541. Hazard sweep of UAssetRegistryImpl::ScanPathsSynchronousInternal (AssetRegistry.cpp:3561–3604): lock + scan only, no modal dialog, no FScopedSlowTask, completes synchronously — the breadth guard (refuse bare `/Game`) is the right and sufficient stall bound. IoStore-blindness failure mode re-verified against AssetRegistry.cpp:186–192 (premade registry) — honest as written.

### list_native_classes
**Purpose**: enumerate the native class surface of a /Script module (above all `/Script/DrugDealerSimulator2`) — today describe_class needs a name the agent has no way to discover; the game's native API is effectively invisible.
**Engine API**:
```cpp
COREUOBJECT_API void GetObjectsWithPackage(const class UPackage* Outer, TArray<UObject *>& Results, bool bIncludeNestedObjects = true, EObjectFlags ExclusionFlags = RF_NoFlags, EInternalObjectFlags ExclusionInternalFlags = EInternalObjectFlags::None);
COREUOBJECT_API void GetDerivedClasses(const UClass* ClassToLookFor, TArray<UClass *>& Results, bool bRecursive = true);
```
Runtime/CoreUObject/Public/UObject/UObjectHash.h:155, :209. Package via `FindPackage(nullptr, TEXT("/Script/DrugDealerSimulator2"))` (pattern already used in MifBridgeCooked.cpp H_describe_package).
**Export**: `COREUOBJECT_API` (both, verbatim) | **Module**: none — CoreUObject already linked | **Guards**: none
**Bucket**: read-only — pure reflection walk.
**Async**: no.
**Params**: | name | aliases | type | default | required |
| module | package, scriptPackage | string ("/Script/X" or bare "X") | — | yes (unless parentClass given) |
| parentClass | baseClass | string — filter to classes deriving from this (GetDerivedClasses when given alone; intersection when both) | "" | no |
| nameContains | filter | string substring | "" | no |
| limit | — | int 1..2000 | 200 | no |
Unrecognised ⇒ error; empty module AND empty parentClass ⇒ error naming both.
**Failure modes**:
- module not found ⇒ `"script package not found: /Script/<X> — module not loaded in this editor (native modules load at boot; check spelling against the .uproject/plugin list)"`.
- parentClass unresolvable ⇒ ResolveClassStrict-style error naming the param.
**Cooked**: native classes are never cooked-stripped — full fidelity. (BPGCs are covered by find_assets already; this endpoint should EXCLUDE them by default to stay orthogonal, `includeBlueprintClasses:false`.)
**Verify**: `list_native_classes {module:"DrugDealerSimulator2"}` returns N ≥ 4 known names (QuestManager, TownStatusManager, BaseNPC, DrugDealerSimulator2GameModeBase — all confirmed live via describe_class parents); `{parentClass:"TownStatusManager"}` returns ≥1 (BP_TownStatusManager_C when includeBlueprintClasses:true).
**Score**: U4 E5 R5 → tier 1 — one TObjectIterator-free walk unlocks discovery of the entire native game API that this axis had to find by parent-chain guessing.
**Phase-2 verdict**: CONFIRMED — both signatures verbatim COREUOBJECT_API at UObjectHash.h:155 (GetObjectsWithPackage) and :209 (GetDerivedClasses); the `FindPackage(nullptr, ...)` precedent is real (MifBridgeCooked.cpp:325). Read-only bucket correct; no async needed; no name collision. Cleanest entry in the axis.

### mod_package_request / mod_package_status
**Purpose**: actually EXECUTE the retoc pack/deploy lane that trigger_cook only plans, from the editor session, with a poll — closes the authoring→live-game loop (edit loose asset → to-zen repack → deploy to Content/Paks/Mods|LogicMods → read_modloader_log).
**Engine API**:
```cpp
static CORE_API FProcHandle CreateProc( const TCHAR* URL, const TCHAR* Parms, bool bLaunchDetached, bool bLaunchHidden, bool bLaunchReallyHidden, uint32* OutProcessID, int32 PriorityModifier, const TCHAR* OptionalWorkingDirectory, void* PipeWriteChild, void* PipeReadChild = nullptr);
static CORE_API bool IsProcRunning( FProcHandle & ProcessHandle );
static CORE_API bool GetProcReturnCode( FProcHandle & ProcHandle, int32* ReturnCode );
static CORE_API void TerminateProc( FProcHandle & ProcessHandle, bool KillTree = false );
static CORE_API bool CreatePipe(void*& ReadPipe, void*& WritePipe, bool bWritePipeLocal = false);
static CORE_API FString ReadPipe( void* ReadPipe );
```
FGenericPlatformProcess — Runtime/Core/Public/GenericPlatform/GenericPlatformProcess.h:444, :478, :529, :499, :661, :670.
**Export**: `CORE_API` (all, verbatim) | **Module**: none — Core already linked | **Guards**: none
**Bucket**: self-managed — spawns an external process; nothing transactable.
**Async**: **request + poll, mandatory** (invariant 3): `mod_package_request` returns `{jobId, pid, command}` immediately after CreateProc (detached=false, hidden=true, pipes attached); `mod_package_status {jobId}` reports `{running, exitCode|null, stdoutTail (ReadPipe drained per poll, ring-buffered 200 lines), step:"to-legacy"|"to-zen"|"deploy", deployedFiles[]}`. One job at a time; second request while running ⇒ error `"job <id> still running — poll mod_package_status or cancel:true"`.
**Params** (request): | name | aliases | type | default | required |
| mode | step | enum "to-legacy" \| "to-zen" \| "deploy" \| "pack-and-deploy" | — | yes |
| inputDir / outputDir / filter / version | — | strings mirroring the trigger_cook plan (`--version UE5_3` default) | plan defaults | mode-dependent, strict |
| deployTo | — | enum "Mods" \| "LogicMods/<ModName>" | "Mods" | for deploy modes |
| cancel | — | bool (status endpoint: TerminateProc the running job) | false | no |
Executable path: pinned `C:/Users/andre/.cargo/bin/retoc.exe` (exists, verified on disk 2026-07-26; same constant trigger_cook uses). Unrecognised ⇒ error.
**Failure modes**:
- retoc.exe missing ⇒ `"retoc not found at <path> — install via cargo or fix MifBridgePipeline path"`.
- non-zero exit ⇒ status carries exitCode + last 50 stdout lines (retoc prints its own errors; **do NOT trust exit codes alone** — the UnrealPak precedent of exit 255 on success is documented in trigger_cook's plan; retoc is exit-0-clean but the tail is still returned).
- deploy while the live game holds the .ucas locked ⇒ copy fails; error `"deploy failed: <file> locked — the retoc lane runs with the game open but a PREVIOUS same-name pak may be held; close the game or deploy under a new name"`.
- editor-side warning when deploying an override for a container currently mounted in the EDITOR (same shadowing note as mount_pak).
**Cooked**: operates on cooked artefacts by design; the editor's own assets are untouched.
**Verify**: request pack-and-deploy → poll until exitCode==0 → `verify_pak_contents` on the produced .utoc lists the expected package path → files present in `list_ue4ss_mods` (below) with fresh mtimes → `read_modloader_log {filter:"<ModName>"}` after a game run.
**Score**: U5 E2 R2 → tier 2 — highest-leverage item in this axis: turns the documented-but-manual mod pipeline into a polled bridge lane. Needs design care (job table, single-flight, pipe draining on the game thread is fine at poll time).
**Phase-2 verdict**: CONFIRMED — all six signatures verbatim `static CORE_API` at GenericPlatformProcess.h:444 (CreateProc, the pipe-taking overload as designed), :478 (IsProcRunning), :529 (GetProcReturnCode), :499 (TerminateProc), :661 (CreatePipe), :670 (ReadPipe). `C:/Users/andre/.cargo/bin/retoc.exe` re-verified present on disk 2026-07-26 (phase-2). Request+poll design satisfies invariant 3; no WaitForProc anywhere — correctly non-blocking. Bucket self-managed correct. Implementation note upheld from the design: never call FPlatformProcess::WaitForProc in the handler; drain ReadPipe only at poll time.

### read_modloader_log (revision: structured mode)
**Purpose**: structured events instead of raw lines from UE4SS.log — agents currently regex a text blob to answer "did MY mod print X after time T".
**Engine API**: none new — same `FFileHelper::LoadFileToStringArray` the handler already uses (MifBridgePipeline.cpp:56). The FORMAT is the verifiable surface, measured on the live 112 KB log (1,398 lines): `[YYYY-MM-DD HH:MM:SS.NNNNNNN] message` (998), `[ts] [Lua] [ModName] message` (~350 across 12 mod names), `[ts] [HookTag] message` (~40), 2 known no-timestamp lines (`##### MEMBER OFFSETS ...`). Parse regex: `^\[([0-9\- :\.]+)\] (?:\[(Lua)\] \[([^\]]+)\] )?(.*)$`; UE4SS v3.0.1 pins the format.
**Export**: n/a | **Module**: none | **Guards**: none
**Bucket**: read-only.
**Async**: no (existing 64 MB size guard stays).
**Params** (all additive, back-compatible): | name | aliases | type | default | required |
| structured | parse | bool → returns `events[{ts, channel:"lua"|"hook"|"core", mod, message}]` | false | no |
| mod | modName | string — exact match on the `[Lua] [X]` tag | "" | no |
| since | after | string timestamp prefix-comparable (`"2026-07-26 01:07"`) — format is lexically sortable, verified | "" | no |
| path / lines / filter | — | as today | — | no |
Unrecognised ⇒ error (today's handler silently ignores unknown keys — fix in the same change).
**Failure modes**: unparseable line in structured mode ⇒ emit as `channel:"raw"` never drop; `since` malformed ⇒ error naming format; UE4SS format drift (major version change) ⇒ raw fallback keeps working.
**Cooked**: n/a (file read of the live game's log).
**Verify**: structured count == raw `matched` count for the same filter; `{mod:"MifEcon"}` returns exactly the 18 `[Lua] [MifEcon]` lines counted in the live sample.
**Score**: U3 E5 R5 → tier 1 — trivial, format verified against a real log, kills a whole class of agent-side regex bugs.
**Phase-2 verdict**: CONFIRMED — `FFileHelper::LoadFileToStringArray` is at MifBridgePipeline.cpp:56 exactly; today's handler reads only `path`/`lines`(1..5000, default 80)/`filter` and silently ignores unknown keys (re-verified in code, lines 31–37), so the strictness fix belongs in this change as stated. Format re-histogrammed against the LIVE log twice on 2026-07-26 — counts differ between passes (1,423/363 vs 1,408/348/16-MifEcon) and are LOWER than an earlier snapshot, proving **UE4SS.log is rewritten per game session** (a log cannot shrink otherwise): design consequence, `since` timestamps and any cached counts are only valid within one game session, and the endpoint response should carry the log file's own mtime/size so agents can detect rotation. Structure claim itself holds in every pass (regex matches all but ~9 lines, blanks + `##### MEMBER OFFSETS`; the `channel:"raw"` never-drop fallback absorbs them). Precision fix: the "64 MB size guard" at MifBridgePipeline.cpp:50–53 only sets `truncatedRead:true` — LoadFileToStringArray still reads the ENTIRE file (line 56 runs unconditionally); the revision should make the guard real (refuse or tail-read past the cap), not inherit the flag-only behaviour. All snapshot-exact counts in the Verify line are illustrative, not assertions.

### list_ue4ss_mods
**Purpose**: one call answering "what mods are installed/enabled/deployed, and did my deploy actually land" — the deploy-side read that pairs with mod_package_request (and that four separate on-disk locations currently make guess-work).
**Engine API**:
```cpp
virtual void FindFilesRecursive( TArray<FString>& FileNames, const TCHAR* StartDirectory, const TCHAR* Filename, bool Files, bool Directories, bool bClearFileNames=true) = 0;
virtual FDateTime GetTimeStamp( const TCHAR* Path ) = 0;
virtual int64 FileSize( const TCHAR* Filename )=0;
```
IFileManager — Runtime/Core/Public/HAL/FileManager.h:147, :219, :272 (accessed via `IFileManager::Get()`, the exact pattern H_read_modloader_log already uses for FileSize). mods.txt/enabled.txt parsed with `FFileHelper::LoadFileToStringArray`.
**Export**: pure-virtual interface via `IFileManager::Get()` — no link concern (already called from this plugin) | **Module**: none — Core | **Guards**: none
**Bucket**: read-only.
**Async**: no (four small directories).
**Params**: | name | aliases | type | default | required |
| gameRoot | root | string | the MifBridgePipeline GameRoot constant | no |
| section | — | enum "lua" \| "paks" \| "logicmods" \| "all" | "all" | no |
Unrecognised ⇒ error.
**Returns**: `{luaMods:[{name, enabledInModsTxt, hasEnabledTxt, files}], pakMods:[{name, trioComplete(pak+ucas+utoc), sizeBytes, mtime, retired:bool(.retired/.bak suffixes)}], logicMods:[{name, files, mtime}], modsTxtOrder:[...]}` — swept live on 2026-07-26: 15 Lua mod dirs, 3 active pak trios + retired variants, 7 LogicMods dirs; the `.bak/.pre*` suffix zoo in Mods/LogicMods is real and MUST be classified, not filtered out.
**Failure modes**: gameRoot missing ⇒ error with the configPath fallback chain (GameInstallDirectory.txt → constant); mods.txt absent ⇒ `modsTxtOrder:null` + warning (BPModLoader still loads by directory scan).
**Cooked**: n/a — live-install filesystem.
**Verify**: counts match `ls` of the four directories (15/3/7 today); after a mod_package_request deploy, the new trio appears with `trioComplete:true` and mtime within the job window.
**Score**: U4 E5 R5 → tier 1 — cheap, read-only, and it is the verification read every deploy mutation needs (house rule: mutations ship with their check).
**Phase-2 verdict**: CONFIRMED — all three IFileManager signatures verbatim at FileManager.h:147 (FindFilesRecursive), :219 (GetTimeStamp), :272 (FileSize); `class IFileManager` (FileManager.h:57) indeed carries no export macro, and the `IFileManager::Get()` route is already exercised by H_read_modloader_log (MifBridgePipeline.cpp:49). Live counts re-swept on disk 2026-07-26: 15 Lua mod dirs (+`shared/`), 3 active pak trios + a retired MifCore_P trio, 7 LogicMods dirs — matches the Returns claim exactly.

## Cooked-content behaviours verified live (feeds 03_GAPS_AND_RISKS)

Each row: exact probe → exact result, run 2026-07-26 against the live bridge.

1. **read_datatable fully serialises cooked container DataTables.**
   Probe: `read_datatable {path:"/Game/DataTables/Databases/QuestlineDatabase.QuestlineDatabase", maxRows:1}`
   → `ok:true, rowStruct:"QuestlineData", rowCount:5`, complete row JSON. Same for TaskDatabase
   (TaskData, 22 rows), DrugDatabase (DrugData, 8 rows), IS_TaskDatabase (254 rows). Native row
   structs survive cook with full field names — DataTable modding needs NO new endpoint support.
2. **get_datatable_row row-addresses cooked tables.** Probe: `get_datatable_row {path:...TaskDatabase,
   rowName:"TASK-TESTING-CIASTON"}` → full 22-field row.
3. **describe_package exposes cook-level truth for container packages.** Probe:
   `describe_package {package:"/Game/Blueprints/Pawns/NPC/BP_BaseNPC"}` →
   `origin:"container", existsOnDisk:false, flags:{cooked:true, filterEditorOnly:true,
   isCookedForEditor:true}`, exports = **BP_BaseNPC_C + Default__BP_BaseNPC_C + PackageMetaData
   only** — no UBlueprint export = graph stripped, confirmed at the object level.
4. **get_property works on cooked-BPGC CDOs.** Probe: `get_property {objectPath:
   "/Game/Blueprints/Pawns/NPC/BP_BaseNPC.Default__BP_BaseNPC_C", propertyPath:"JumpMaxCount"}`
   → `type:"int32", value:"1"` (and `ShopID` → `FName None`).
5. **get_property / list_object_properties work on cooked material instances.** Probe:
   `get_property {objectPath:"/Game/Blueprints/Enviro/PoleCableMat_Inst.PoleCableMat_Inst",
   propertyPath:"Parent"}` → resolves to `/Game/Blueprints/Enviro/PoleCableMat.PoleCableMat`;
   list_object_properties → 37 props incl. ScalarParameterValues/VectorParameterValues/
   TextureParameterValues (⇒ cooked MI parameter READS are compositions; only writes need the
   set_material_parameter lane).
6. **describe_class reflects native /Script classes AND cooked BPGCs identically.** Probes:
   `describe_class {class:"/Script/DrugDealerSimulator2.TownStatusManager"}` → 139 fns/36 props/16
   dispatchers; `{class:".../BP_TownStatusManager.BP_TownStatusManager_C"}` → 181/64/18.
7. **list_blueprints vs find_assets split confirmed**: list_blueprints → 920 UBlueprint assets
   (loose/editable only); find_assets sees all 37,131 incl. 25,285 container-only BPGC-bearing
   packages. Agents must use find_assets for base-game discovery.
8. *(observed while probing)* **find_assets silently ignores unknown parameters** — probe with
   `{"path":..., "recursive":false}` returned ok using neither (handler reads only pathPrefix/
   class/nameContains/origin/recursiveClasses/limit). Live instance of invariant-4's #1 bug
   class in a shipped endpoint; get_property by contrast errors correctly (`"propertyPath
   required"`). Fix candidate for the param-strictness pass.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

_Phase-2 spot-checks (2026-07-26), none overturned:_
_#1 re-verified on disk — no UnrealPak\* in D:/UE532/Engine/Binaries/Win64; stronger than stated: `D:/DDS2SDK/Engine/Binaries/Win64` does not exist at all (only UnrealEditor-IoStoreUtilities.dll + IoStoreOnDemand.dll in UE532, no standalone tool exe)._
_#2 all four citations re-verified verbatim (AssetRegistry.cpp:186–192 exact at lines 188/191; :62–69 ModKit helper; IAssetRegistry.h:729; AssetRegistryState.h:541)._
_#3 re-verified — H_trigger_cook spans MifBridgePipeline.cpp:96–142, `executed:false` at :108, pinned constants at :16–17._
_#4 architectural claim, consistent with everything read (UE4SS mods live in the shipping game process; bridge is editor-side); not overturnable by citation — stands._
_#5 re-verified — IAssetRegistry.h:150 and `class IFileManager` at FileManager.h:57, neither carries an export macro._
_#6 RE-PROBED LIVE via POST /api/find_assets {nameContains:"Population"} → exactly 2 hits, both `/Game/Billboards/bill_Population{Human,Animal}` (Texture2D). Confirmed._
_#7 consistent with the verified describe_package export list (BP_BaseNPC: BPGC+CDO+PackageMetaData only); not re-probed._
_#8 re-verified in code — MifBridgeDataTables.cpp:135–153 is exactly GetTableAsJSON → linear Name scan._
_#9 re-verified in code (handler reads only class/pathPrefix/nameContains/origin/recursiveClasses/limit, MifBridgeCooked.cpp:193–198) AND live (probe with unknown `recursive:false` returned ok:true, no error)._
_#10 observational one-shot, not re-runnable; log-evidence-based — stands as reported._

1. **No UnrealPak.exe on this machine** — checked `D:/UE532/Engine/Binaries/Win64/` and
   `D:/DDS2SDK/Engine/Binaries/Win64/`: no UnrealPak, no IoStore tool exe (only
   UnrealEditor-IoStoreUtilities.dll). The "ModKit UnrealPak lane" mentioned in trigger_cook's
   caveats is impossible here; retoc is the ONLY pack lane. Any packaging endpoint must not
   fall back to UnrealPak.
2. **ScanPathsSynchronous cannot see IoStore-mounted packages** — zen containers expose chunks,
   not files; the modkit compensates with a premade registry loaded at boot
   (`UE::AssetRegistry::Premade::IsEnabled`, `bUsePremadeInEditor = true`,
   Runtime/AssetRegistry/Private/AssetRegistry.cpp:186–192) plus purge protection
   (`ModKit_IsPackageInMountedIoStoreContainer` → `FIoDispatcher::Get().DoesChunkExist(...)`,
   AssetRegistry.cpp:61–70). Consequence: a mount_pak'd mod trio is loadable by exact path but
   invisible to find_assets; only an AppendState of a carried registry blob
   (IAssetRegistry.h:729 + FAssetRegistryState::Load, AssetRegistryState.h:541,
   ASSETREGISTRY_API) can fix discovery, and retoc does not emit such blobs today.
3. **trigger_cook is plan-only by design, not fire-and-forget** — MifBridgePipeline.cpp:96–142
   returns `executed:false` + a command plan; there is no process and therefore nothing to poll.
   A `cook_status` endpoint as originally hypothesised has no substrate; the real gap is
   executing the retoc lane (proposed: mod_package_request/_status). Also note both live-install
   constants are hardcoded (`GameRoot`, `RetocExe`, MifBridgePipeline.cpp:16–17) — path drift on
   another machine silently breaks read_modloader_log's default too.
4. **No editor→live-game control channel exists** — the UE4SS Lua mods (MifTools, MifEconLogger
   expose `mif.*` console commands) run in the SHIPPING game process; the bridge can only read
   their log file back (one-way). Driving the live game would need a socket/file-command mod on
   the UE4SS side — out of scope for MifBridge endpoints, worth a docs note so agents stop
   looking for it.
5. **`IAssetRegistry` and `IFileManager` carry no export macros** (IAssetRegistry.h:150,
   FileManager.h) — fine via module-singleton virtual dispatch, but any temptation to link
   against their concrete impls (UAssetRegistryImpl, FFileManagerGeneric) is a dead end; record
   so nobody burns time on it.
6. **No population-manager class exists to introspect** — swept `nameContains:"Population"`
   (2 hits, both billboards) and the Enviro/Population folder (7 assets, spawn-point markers).
   Population is emergent from TownStatusManager + GameMode client generation (systems map
   above). A "describe_population" endpoint would have nothing to bind to.
7. **DDS2 dialogue is not a graph asset** — DialogueObject_C is a plain UObject with response
   arrays; no UEdGraph, so none of the graph endpoints apply, and no dialogue-editor API exists
   to wrap. Introspection is fully covered by get_property; authoring cooked dialogues is the
   same reconstruction problem as any cooked BP (MifKismetReconstructor territory).
8. **get_datatable_row is O(whole table) per row** — it serialises the entire table via
   GetTableAsJSON then linear-scans for the row (MifBridgeDataTables.cpp:135–153). On
   IS_TaskDatabase (254 rows) fine; on ItemDatabase-scale tables an agent looping rows pays
   quadratic cost. Quality fix, not a new endpoint: serialise just the requested row
   (FDataTableExporterJSON has per-row paths — not verified here, hence listed as a gap note).
9. **find_assets accepts-and-ignores unknown params** (verified live, see cooked-behaviours #8).
   Same audit should sweep every handler; the brief's invariant 4 is violated by at least one
   shipped read endpoint.
10. **Two editor instances race on the project** — observed live: a second UnrealEditor on the
    same .uproject produced `LogSQLiteDatabase: ... disk I/O error` (AssetSearch FileInfo.db
    lock) and both instances died without crash records. Any future auto-relaunch logic in the
    bridge tooling must check for an existing instance first (port 8791 bind failure is NOT the
    first symptom — the log-name suffix `_2` is).

## UNVERIFIED

- `FDataTableExporterJSON` per-row serialisation path for the get_datatable_row quality fix —
  did not open DataTableJSON.h to verify signatures/exports.
- Whether retoc can emit an AssetRegistry.bin alongside to-zen output (would upgrade
  refresh_asset_registry's registryBlob lane from theory to practice) — external tool docs not
  consulted.
- Whether `ScanModifiedAssetFiles` picks up in-place edits to LOOSE /Game/MODS assets faster
  than a full ScanPathsSynchronous (behavioural claim; needs a timed probe after implementation).
- PakOrder constants: which numeric order the modkit uses for base containers (needed to pick
  mount_pak's default 500 sensibly) — MountModKitGameContainers' order value not read.
- BPModLoaderMod's exact pak-load mechanism inside the GAME (it logs ModFile: *.pak/.ucas/.utoc
  per LogicMods dir) — inferred from log shape only.

## Coverage log

**Done**: plugin handler files read in full (MifBridgeCooked.cpp, MifBridgePipeline.cpp,
MifBridgeDataTables.cpp §1–170, MifBridge.Build.cs, recipe header region of MifBridgeRecipes.cpp);
docs/06_CAPABILITY_ROADMAP.md §Blocking/§High/§Impossible cross-checked (no collisions with this
axis' proposals); live-install filesystem swept (Paks, Mods, LogicMods, ue4ss tree, UE4SS.log
format histogram); engine headers verified with line numbers (IPlatformFilePak.h/.cpp,
IAssetRegistry.h, AssetRegistryState.h, AssetRegistry.cpp ModKit regions, IoDispatcher.h,
GenericPlatformProcess.h, PlatformFileManager.h, FileManager.h, CoreDelegates.h, UObjectHash.h);
live bridge probes: pie_status, find_assets ×12 sweeps, list_mounted_containers, list_datatables,
read_datatable ×4, get_datatable_row, describe_class ×7, describe_package, get_property ×4,
list_object_properties, list_blueprints. Editor relaunch documented (session note at top).

**Not covered / for phase-2**: per-class full member dumps beyond the keyword extracts (raw JSON
retained in the session scratchpad only — re-probe if needed: the table above names every class
path); AnimBP surface of BaseNPC_AnimBP_C (describe_animation exists — walking-NPC axis
territory); the 22 BP interfaces' member lists; DLC plugin (DDS2Casino/ChristmasDlc) manager
classes; StringTables; the /Game/DataTables/Configs + Balance tables' row structs; PakOrder
constant read (UNVERIFIED list); no PIE probes (editor world was Untitled — start_pie is
mutating and was out of bounds for this agent).
