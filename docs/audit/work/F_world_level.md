# Axis F — World and level
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._
_Phase-2 adversarial verification: 2026-07-26. All 25 entries re-verified against source; verdicts appended per entry (14 CONFIRMED / 10 CORRECTED / 1 DEMOTED). See "Phase-2 verification log" at the bottom._

## What the DDS2 world actually is (live-bridge probe, 2026-07-26)

Probed the running editor before it restarted mid-session (results captured; the editor came back
up on an `Untitled` map, so re-probes of IslaSombra were not possible without calling the
forbidden `load_level`):

- `list_level_actors {}` → `world: "IslaSombra"`, **matched: 4545 actors, all in
  `/Game/Maps/IslaSombra/IslaSombra.IslaSombra:PersistentLevel`**.
- `list_level_actors {classFilter:"WorldDataLayers"}` → exactly one actor:
  `PersistentLevel.WorldDataLayers`, class `/Script/Engine.WorldDataLayers`. An `AWorldDataLayers`
  actor is created only for worlds initialized as World Partition (it is the WP data-layer
  container), so **IslaSombra is a World Partition world** — but a *cooked* one:
- `describe_package {path:"/Game/Maps/IslaSombra"}` → `existsOnDisk: false, origin: "container",
  inRegistry: false, loaded: false`. The map is .pak-mounted cooked content. Per
  docs/06_CAPABILITY_ROADMAP.md, editing/saving cooked base-game maps is a documented IMPOSSIBLE.
- Every one of the 4545 probed actors lives in the PersistentLevel (cooked WP flattens the
  always-loaded set into the persistent level; the streaming cells are separate cooked cell
  packages). No `LevelInstance`/`LandscapeStreamingProxy` actors were observed before the editor
  restarted (probes returned before completion; treated as UNVERIFIED, not as absence).

**Consequence for tier scores.** The WP *editor* workflow (data-layer authoring, HLOD builds,
OFPA, WP conversion) only pays off on maps you can SAVE — i.e. new mod maps made by
`new_level`/`save_level_as`, which are plain non-partitioned levels. So:
- WP-authoring surface (data layer editing, HLOD layers, WP conversion) → tier 2–3 here, and
  mostly negative results (see below).
- WP *introspection* of the open cooked world (which data layers exist, their runtime state) →
  cheap and real, tier 2.
- The sublevel/streaming-level surface (UEditorLevelUtils) targets exactly the maps mods CAN make
  and save, and is fully exported → that is where the tier-1 scores go.
- Landscape splines / EditorApplySpline operate on any ALandscape in the open world, including a
  scratch landscape made by `create_landscape` → tier 0–1 (closes the documented town-road gap,
  docs/08_LANDSCAPE.md road-kit workaround).

## Surface inventory

Headers actually opened (all paths relative to D:/UE532/Engine/Source unless noted):

| Area | Files read |
|---|---|
| Sublevels | Editor/UnrealEd/Public/EditorLevelUtils.h (all 346 lines); Runtime/Engine/Classes/Engine/LevelStreaming.h (class decl + exported setters); Runtime/Engine/Classes/Engine/LevelStreamingVolume.h (lines 20–56); Runtime/Engine/Classes/Engine/World.h (streaming-level accessors 821–1084); Runtime/Engine/Classes/Engine/Level.h (SetLightingScenario region) |
| Landscape splines | Runtime/Landscape/Classes/LandscapeSplinesComponent.h (101–245); LandscapeSplineControlPoint.h (38–289); LandscapeSplineSegment.h (14–347); LandscapeInfo.h (121–124, 222, 258–303, 441–442); LandscapeProxy.h (412, 845–874, 1088–1152); ILandscapeSplineInterface.h (26–32); Editor/LandscapeEditor/Private/LandscapeEdModeSplineTools.cpp (282–541, the authoring sequence to mirror) |
| Landscape data | Runtime/Landscape/Classes/Landscape.h (bCanHaveLayersContent 461); MifBridgeLandscape.cpp read in full for the sculpt/paint/landscape_info/create_landscape contracts |
| MifBridge existing handlers read first | MifBridgeLandscape.cpp (H_create_landscape, H_sculpt_landscape 321–473, H_paint_landscape 482+, H_landscape_info 675–768); MifBridgeAuthoring.cpp (H_add_foliage_instances 428–478 — **it is a HISM holder actor, NOT AInstancedFoliageActor foliage**); MifBridgeWorld.cpp (H_set_spline_points 223–300, H_get_spline_points 304–334, H_load_level, H_snap_actors_to_ground) |

(Inventory for foliage / water / world-partition / environment appended below as swept.)

## Proposed endpoints

### list_sublevels
**Purpose**: Enumerate every streaming level of the open world with load/visibility state — the read half every other sublevel mutation verifies against; also reports whether the world is partitioned.
**Engine API**:
```cpp
const TArray<ULevelStreaming*>& GetStreamingLevels() const { return StreamingLevels; }
```
Runtime/Engine/Classes/Engine/World.h:1037 (UWorld inline accessor; per-level data read off `ULevelStreaming` UPROPERTYs and `ULevelStreaming::GetLoadedLevel()` — inline, LevelStreaming.h:523; package name via `ENGINE_API virtual FName GetWorldAssetPackageFName() const;` LevelStreaming.h:486).
**Export**: inline accessors + `ENGINE_API` on GetWorldAssetPackageFName (ULevelStreaming is `UCLASS(abstract, editinlinenew, BlueprintType, Within=World, MinimalAPI)` LevelStreaming.h:135 — every method used here is either inline or method-exported) | **Module**: none — Engine already linked | **Guards**: none
**Bucket**: read-only — pure query.
**Async**: no
**Params**: | name | aliases | type | default | required |
| world | — | string ("editor") | "editor" | no (reserved; only editor world supported) |
Unrecognised parameter → error naming it.
**Failure modes**:
- No editor world → `"no editor world"`.
- (Not a failure) zero streaming levels → `{count:0, isPartitioned:<bool>}` — a WP world legitimately has none in-editor.
**Cooked**: works — reads in-memory world state regardless of package origin; on the cooked IslaSombra it reports the flattened persistent-level reality, which is itself diagnostic.
**Verify**: `count` equals the Levels panel row count minus one (persistent); after `add_sublevel` the count increments by exactly 1.
**Score**: U4 E5 R5 → tier 1
**Phase-2 verdict**: CORRECTED — phase-1 cited World.h:821, which is `FStreamingLevelsToConsider::GetStreamingLevels` (a private-consideration container, wrong class). The real UWorld accessor is World.h:1037 with return type `const TArray<ULevelStreaming*>&` (not TObjectPtr); citation and signature fixed in place. All LevelStreaming.h citations (135 MinimalAPI decl, 427/466 setters, 486 GetWorldAssetPackageFName, 523 GetLoadedLevel) re-verified verbatim.

### add_sublevel
**Purpose**: Add an existing level package to the open world as a streaming sublevel (with optional transform) — the missing half of multi-level composition; today only whole-map load_level exists.
**Engine API**:
```cpp
static UNREALED_API ULevelStreaming* AddLevelToWorld(UWorld* InWorld, const TCHAR* LevelPackageName, TSubclassOf<ULevelStreaming> LevelStreamingClass, const FTransform& LevelTransform = FTransform::Identity);
```
Editor/UnrealEd/Public/EditorLevelUtils.h:223
**Export**: `UNREALED_API` (method-level; class is `UCLASS(transient)` unexported — fine, all used methods carry the macro) | **Module**: none — UnrealEd already linked | **Guards**: none (UnrealEd is editor-only by definition)
**Bucket**: self-managed — AddLevelToWorld internally flushes levels, broadcasts LevelAdded, and can trigger a world-composition refresh; wrapping that registration cascade in the blanket transaction risks a half-registered ULevel on undo. Handler runs it untransacted and reports; removal is the inverse endpoint, not Ctrl-Z.
**Async**: no (synchronous for editor sublevels; the level is loaded before return)
**Params**: | name | aliases | type | default | required |
| path | packagePath, level | string | — | yes (strict: empty ⇒ error `"path is required, e.g. /Game/Maps/TownDistrict"`) |
| streamingClass | class | string enum: "alwaysloaded" (ULevelStreamingAlwaysLoaded) / "dynamic" (ULevelStreamingDynamic) | "alwaysloaded" | no |
| location | — | {x,y,z} | 0,0,0 | no |
| rotation | — | {x,y,z} (yaw only honoured by level transform rules) | 0,0,0 | no |
Unrecognised parameter → error naming it. Unknown streamingClass string → error listing both accepted values.
**Failure modes**:
- Package does not exist / is not a map → returns nullptr → `"AddLevelToWorld failed for '<path>' — package missing or not a ULevel (find_assets to check)"`.
- Level already in world → engine no-ops → report `alreadyPresent:true` instead of error.
- Cooked container package (existsOnDisk=false) → refuse up front: `"'<path>' is cooked .pak content — cooked sublevels cannot be added to an editable world"`.
**Cooked**: refuses for .pak-mounted level packages (no loose .umap to load as a sublevel); works for any level created via new_level/save_level_as.
**Verify**: list_sublevels count +1; the new entry's packageName equals the request path; spawn an actor, set_current_sublevel to it, and list_level_actors shows the actor's path rooted in the sublevel package.
**Score**: U5 E4 R4 → tier 1
**Phase-2 verdict**: CORRECTED — signature verbatim at EditorLevelUtils.h:223, but the "level already in world → engine no-ops" failure mode is WRONG: `AddLevelToWorld_Internal` shows a MODAL `FSuppressableWarningDialog::ShowModal()` when the package is already present or is the persistent level (EditorLevelUtils.cpp:441-451) — a deadlock mid-HTTP. The handler MUST pre-check with `FLevelUtils::FindStreamingLevel(InWorld, PackageName)` (static ENGINE_API, Runtime/Engine/Public/LevelUtils.h:35/44) plus a persistent-level name compare, and return `alreadyPresent:true` WITHOUT calling the engine. Also note AddLevelToWorld runs an FScopedSlowTask dialog (cpp:387-388, progress-only, safe) and loads the level synchronously — fine for the load_level-sized precedent.

### create_sublevel
**Purpose**: Create a brand-new empty streaming level inside the open world and save it to a given package path in one call (no SaveAs dialog), so an agent can partition a town build into districts.
**Engine API**:
```cpp
static UNREALED_API ULevelStreaming* CreateNewStreamingLevelForWorld(UWorld& World, TSubclassOf<ULevelStreaming> LevelStreamingClass, const FString& DefaultFilename = TEXT(""), bool bMoveSelectedActorsIntoNewLevel = false, UWorld* InTemplateWorld = nullptr, bool bInUseSaveAs = true, TFunction<void(ULevel*)> InPreSaveLevelOperation = TFunction<void(ULevel*)>(), const FTransform& InTransform = FTransform::Identity);
```
Editor/UnrealEd/Public/EditorLevelUtils.h:152 — call with `bInUseSaveAs=false` and a real filename (converted from /Game path exactly the way H_save_level_as already does with PackagePathToMapFilename) so no modal dialog can appear.
**Export**: `UNREALED_API` | **Module**: none — UnrealEd linked | **Guards**: none
**Bucket**: self-managed — creates a UWorld/ULevel pair, saves a package, and registers a streaming level; same class of object-creation cascade as create_landscape (precedent: self-managed).
**Async**: no
**Params**: | name | aliases | type | default | required |
| path | packagePath | string | — | yes (strict) |
| streamingClass | class | string enum as add_sublevel | "alwaysloaded" | no |
| makeCurrent | — | bool | false | no (calls MakeLevelCurrent(ULevelStreaming*), EditorLevelUtils.h:53) |
Unrecognised → error.
**Failure modes**:
- Path already exists → refuse before calling: `"'<path>' already exists — use add_sublevel to attach it"`.
- Returns nullptr (invalid path / read-only dir) → `"CreateNewStreamingLevelForWorld failed — is '<path>' under a writable mount?"`.
- MODAL RISK if bInUseSaveAs is ever true — the handler must hard-code false; a dialog mid-HTTP-request deadlocks the editor. Named explicitly so the implementer cannot miss it.
**Cooked**: n/a — always creates loose content; refuses paths that resolve into mounted containers.
**Verify**: describe_package on the new path shows existsOnDisk:true; list_sublevels count +1.
**Score**: U4 E3 R3 → tier 1
**Phase-2 verdict**: CONFIRMED — signature verbatim at EditorLevelUtils.h:152; the bInUseSaveAs branch verified in the implementation: `bInUseSaveAs=true` → `FEditorFileUtils::SaveLevelAs` (modal Save-As), `false` → `FEditorFileUtils::SaveLevel(DefaultFilename)` with no dialog (EditorLevelUtils.cpp:760-767) — the entry's hard-coded-false rule is exactly right and sufficient. Note the call also deactivates active editor modes on entry and reactivates the default mode on exit (cpp:722-735) — harmless for the bridge but will kick a user out of e.g. Landscape mode; worth reporting `editorModeReset:true`.

### remove_sublevel
**Purpose**: Detach a streaming sublevel from the open world (asset stays on disk) — inverse of add_sublevel.
**Engine API**:
```cpp
static UNREALED_API bool RemoveLevelFromWorld(ULevel* InLevel, bool bClearSelection = true, bool bResetTransBuffer = true);
```
Editor/UnrealEd/Public/EditorLevelUtils.h:247
**Export**: `UNREALED_API` | **Module**: none | **Guards**: none
**Bucket**: self-managed — note the default `bResetTransBuffer=true`: the engine itself nukes the undo buffer when a level is removed, which is fundamentally incompatible with running inside the blanket FScopedTransaction (the outer transaction would be destroyed under RunEndpoint's feet). Call with defaults, untransacted, and report `undoBufferReset:true` in the response.
**Async**: no
**Params**: | name | aliases | type | default | required |
| path | packagePath, level | string (package name as reported by list_sublevels) | — | yes (strict) |
Unrecognised → error.
**Failure modes**:
- Package not among current streaming levels → `"'<path>' is not a sublevel of the open world — list_sublevels shows what is"`.
- Attempting to remove the persistent level → refuse: `"cannot remove the persistent level — use load_level/new_level"`.
- Level is current → MakeLevelCurrent(persistent) first, then remove (document in response).
**Cooked**: works for whatever list_sublevels shows; the asset itself is untouched.
**Verify**: list_sublevels count −1; describe_package still shows the asset on disk.
**Score**: U3 E4 R3 → tier 1
**Phase-2 verdict**: CORRECTED — signature verbatim at EditorLevelUtils.h:247, self-managed bucket justification verified in source (RemoveLevelsFromWorld resets the transaction buffer at EditorLevelUtils.cpp:886-889 and ends with `GEditor->Cleanse` = forced GC, :909). Two hidden MODAL hazards added: (a) a locked level pops `FMessageDialog::Open` (cpp:830-834) — pre-check `FLevelUtils::IsLevelLocked(Level)` (static ENGINE_API, LevelUtils.h:91) and refuse with a structured error; (b) if the subsequent package unload fails, another `FMessageDialog::Open` fires (cpp:894-897) — rare (dirty/in-use package) but real; document as residual risk and keep the endpoint off dirty levels (check Package->IsDirty() first).

### set_sublevel_visibility
**Purpose**: Show/hide a sublevel in the editor viewport and set whether it should be loaded/visible at runtime — enables lighting-scenario workflows and decluttering during authoring.
**Engine API**:
```cpp
static UNREALED_API void SetLevelVisibility(ULevel* Level, const bool bShouldBeVisible, const bool bForceLayersVisible, const ELevelVisibilityDirtyMode ModifyMode = ELevelVisibilityDirtyMode::ModifyOnChange);
ENGINE_API void SetShouldBeVisibleInEditor(bool bInShouldBeVisibleInEditor);
ENGINE_API virtual void SetShouldBeLoaded(bool bInShouldBeLoaded);
```
Editor/UnrealEd/Public/EditorLevelUtils.h:282; Runtime/Engine/Classes/Engine/LevelStreaming.h:466; LevelStreaming.h:427
**Export**: `UNREALED_API` / `ENGINE_API` (method-level on MinimalAPI ULevelStreaming) | **Module**: none | **Guards**: none
**Bucket**: transacted — property-level state flips with standard Modify support (ModifyOnChange mode participates in the transaction).
**Async**: no
**Params**: | name | aliases | type | default | required |
| path | level, packagePath | string | — | yes (strict) |
| visible | editorVisible | bool | — | at least one of visible / shouldBeLoaded / shouldBeVisible required, else error `"nothing to change — pass visible, shouldBeLoaded or shouldBeVisible"` |
| shouldBeLoaded | — | bool | — | no (runtime flag) |
| shouldBeVisible | — | bool | — | no (runtime flag, ULevelStreaming UPROPERTY SetShouldBeVisible path) |
Unrecognised → error.
**Failure modes**:
- Sublevel not found → same message as remove_sublevel.
- Level not loaded (GetLoadedLevel()==nullptr) while editor-visibility requested → `"sublevel '<path>' has no loaded ULevel — set shouldBeLoaded:true first"`.
**Cooked**: works on in-memory streaming levels regardless of origin.
**Verify**: list_sublevels echoes the three booleans; list_level_actors on a hidden level's actor still finds it (visibility ≠ existence) — count unchanged.
**Score**: U3 E5 R4 → tier 1
**Phase-2 verdict**: CONFIRMED — all three signatures verbatim (EditorLevelUtils.h:282; LevelStreaming.h:466 — note it sits inside `#if WITH_EDITOR` (block at :464-473), fine in this editor-only module; LevelStreaming.h:427). The runtime `shouldBeVisible` route also verified: `ENGINE_API void SetShouldBeVisible(bool)` LevelStreaming.h:414 (BlueprintSetter of the UPROPERTY at :250).

### set_current_sublevel
**Purpose**: Route all subsequent spawn_actor_in_level/spawn_many output into a chosen sublevel — without this, sublevels exist but everything still lands in the persistent level.
**Engine API**:
```cpp
static UNREALED_API void MakeLevelCurrent(ULevel* InLevel, bool bEvenIfLocked = false);
```
Editor/UnrealEd/Public/EditorLevelUtils.h:86
**Export**: `UNREALED_API` | **Module**: none | **Guards**: none
**Bucket**: transacted — editor-state flip, cleanly undoable.
**Async**: no
**Params**: | name | aliases | type | default | required |
| path | level, packagePath | string; the literal "persistent" selects the persistent level | — | yes (strict) |
Unrecognised → error.
**Failure modes**: sublevel not found / not loaded → as above; locked level → report `lockedBypassed:false` and error advising bEvenIfLocked is deliberately not exposed.
**Cooked**: works (in-memory).
**Verify**: response echoes currentLevel; spawn_actor_in_level then list_level_actors → the new actor's actorPath is rooted in the sublevel's package, not the persistent one.
**Score**: U4 E5 R4 → tier 1 (pairs with add_sublevel; without it sublevels are decoration)
**Phase-2 verdict**: CORRECTED — signature verbatim at EditorLevelUtils.h:86, but the locked-level failure mode is not a soft error: `MakeLevelCurrent` itself opens a MODAL `FMessageDialog` when the level is locked and bEvenIfLocked is false (EditorLevelUtils.cpp:555-589) — deadlock mid-HTTP. The handler MUST pre-check `FLevelUtils::IsLevelLocked(Level)` (static ENGINE_API, LevelUtils.h:91) and return the structured error itself, never letting the engine hit its dialog branch.

### move_actors_to_sublevel
**Purpose**: Rehome existing placed actors into a sublevel (e.g. migrate a finished district out of the persistent level) with a numeric moved/failed report.
**Engine API**:
```cpp
static UNREALED_API int32 MoveActorsToLevel(const TArray<AActor*>& ActorsToMove, ULevel* DestLevel, bool bWarnAboutReferences = true, bool bWarnAboutRenaming = true, bool bMoveAllOrFail = false, TArray<AActor*>* OutActors = nullptr);
```
Editor/UnrealEd/Public/EditorLevelUtils.h:100
**Export**: `UNREALED_API` | **Module**: none | **Guards**: none
**Bucket**: self-managed — the move is implemented as cut+paste-rename across packages (actors are destroyed in one level and recreated in another); an outer blanket transaction spanning the destroy/recreate of arbitrary actor graphs is the documented dead-CDO-shaped hazard class. MUST pass `bWarnAboutReferences=false, bWarnAboutRenaming=false` — both true pops MODAL dialogs (deadlock, same as create_sublevel note).
**Async**: no
**Params**: | name | aliases | type | default | required |
| actorPaths | actors | string[] | — | yes (strict, non-empty) |
| destination | dest, level, path | string (sublevel package or "persistent") | — | yes (strict) |
| allOrFail | — | bool | false | no (maps to bMoveAllOrFail) |
Unrecognised → error.
**Failure modes**:
- Any actorPath unresolved → error naming the first missing path (before moving anything).
- Return int < requested count with allOrFail:false → `moved:<n>, failed:<m>, movedPaths:[...]` (OutActors gives the NEW paths — old paths are dead after the move; the response must say so).
- Destination == source level for all actors → moved:0 with note, not an error.
**Cooked**: source actors in the cooked persistent level can be moved in memory, but the world cannot be saved (documented impossible) — response carries `warning:"source world is cooked; this change cannot be saved"` when the world package is container-origin.
**Verify**: moved count equals actorPaths length; each movedPath from the response resolves via get_actor_bounds; old paths return actor-not-found.
**Score**: U3 E3 R3 → tier 2 (valuable, but the new-path handover needs careful response design)
**Phase-2 verdict**: CONFIRMED — signature verbatim at EditorLevelUtils.h:100; the modal claims verified in the implementation: bWarnAboutReferences feeds `GEditor->CopySelectedActorsToClipboard` (EditorLevelUtils.cpp:182) and bWarnAboutRenaming gates a rename prompt (:250) — the entry's MUST-pass-false rule is correct and mandatory. Cut/paste-rename mechanism confirmed (implemented via clipboard copy in CopyOrMoveActorsToLevel, cpp:91+); note it also mutates the editor selection as a side effect — report `selectionChanged:true`.

### set_sublevel_streaming
**Purpose**: Change a sublevel's streaming class (always-loaded ↔ dynamic) and its level transform — decides what streams in game without re-adding the level.
**Engine API**:
```cpp
static UNREALED_API ULevelStreaming* SetStreamingClassForLevel(ULevelStreaming* InLevel, TSubclassOf<ULevelStreaming> LevelStreamingClass);
ENGINE_API virtual void SetWorldAsset(const TSoftObjectPtr<UWorld>& NewWorldAsset);
```
Editor/UnrealEd/Public/EditorLevelUtils.h:238; LevelStreaming.h:479 (transform via the `LevelTransform` UPROPERTY on ULevelStreaming through the existing set_property machinery — the dedicated endpoint exists for the class swap, which set_property cannot do).
**Export**: `UNREALED_API` / `ENGINE_API` | **Module**: none | **Guards**: none
**Bucket**: self-managed — SetStreamingClassForLevel REPLACES the ULevelStreaming object (returns the new one); undoing an object-identity swap mid-array is not a property revert. Report old/new object paths.
**Async**: no
**Params**: | name | aliases | type | default | required |
| path | level, packagePath | string | — | yes (strict) |
| streamingClass | class | "alwaysloaded" / "dynamic" | — | yes (strict) |
Unrecognised → error.
**Failure modes**: unknown class string → error listing accepted values; sublevel not found → standard message; already that class → `changed:false`, no-op success.
**Cooked**: in-memory works; save constraint as move_actors_to_sublevel.
**Verify**: list_sublevels shows the new streamingClass string; the ULevelStreaming objectPath changed (returned pair differs).
**Score**: U3 E4 R3 → tier 2
**Phase-2 verdict**: CORRECTED — signatures verbatim (EditorLevelUtils.h:238, LevelStreaming.h:479) and the object-replacement claim verified in source (implementation removes the streaming level and re-adds via AddLevelToWorld, returning the NEW object and copying transform/volumes/color across, EditorLevelUtils.cpp:514-548). Hidden CRASH hazard added: the implementation runs `check(Level)` on `InLevel->GetLoadedLevel()` (cpp:524-525) — calling this on an unloaded sublevel is a hard assert, not an error. Handler MUST pre-check GetLoadedLevel()!=nullptr and error "sublevel '<path>' is not loaded — set shouldBeLoaded:true first".

## Compositions (no new endpoint needed) — part 1

- **Level streaming volumes**: `ALevelStreamingVolume` is spawnable by the existing
  `spawn_actor_in_level` (class `/Script/Engine.LevelStreamingVolume`; UCLASS is MinimalAPI,
  Runtime/Engine/Classes/Engine/LevelStreamingVolume.h:28–31, but spawning goes through UClass, not
  linked symbols). The volume→level link is TWO writable arrays: `ALevelStreamingVolume::
  StreamingLevelNames` (`TArray<FName>`, LevelStreamingVolume.h:34–35) and the inverse
  `ULevelStreaming::EditorStreamingVolumes` (`TArray<TObjectPtr<ALevelStreamingVolume>>`,
  LevelStreaming.h:318). Both are plain UPROPERTYs reachable by `set_property` on the volume actor
  and on the streaming-level object path reported by list_sublevels. Composition:
  `spawn_actor_in_level` → `set_actor_transform` (size via scale on the brush) → `set_property`
  StreamingLevelNames. Only worth a dedicated endpoint if set_property proves unable to address
  TArray<FName> element-wise — flag for phase 2 to test, not to build.
  **Phase-2 note**: citations verified (LevelStreamingVolume.h:28-31 UCLASS MinimalAPI, :34-35;
  LevelStreaming.h:316-318), with one accuracy fix: `StreamingLevelNames` is
  `VisibleAnywhere, BlueprintReadOnly` (:34) — i.e. CPF_EditConst — so it is NOT a plainly
  "writable" array; whether the set_property machinery honours or bypasses EditConst decides if
  this composition works at all. The live test phase-1 requested is therefore mandatory, and the
  inverse route (`ULevelStreaming::EditorStreamingVolumes`, plain EditAnywhere at :317-318) is the
  safer of the two to drive.
- **Lighting scenarios**: flag a sublevel's loaded ULevel via
  `ENGINE_API void SetLightingScenario(bool bNewIsLightingScenario);`
  (Runtime/Engine/Classes/Engine/Level.h:1090, backing bit `bIsLightingScenario` Level.h:547).
  This is one exported setter on an object list_sublevels already exposes — fold it into
  set_sublevel_visibility as an optional `lightingScenario` bool parameter rather than a new
  endpoint (same object resolution, same bucket). Noted here so the parameter is not forgotten.
- **Lightmass importance volume**: `spawn_actor_in_level` with
  `/Script/Engine.LightmassImportanceVolume` covers it (generic spawn; per-brief note only).

### apply_spline_to_landscape
**Purpose**: Deform (and optionally paint) the landscape along ANY existing USplineComponent — a road/river bed carved from the same spline that set_spline_points just authored. Closes the town-road gap documented in docs/08_LANDSCAPE.md (road-kit workaround) with a single exported call.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "Landscape|Editor")
LANDSCAPE_API void EditorApplySpline(USplineComponent* InSplineComponent, float StartWidth = 200, float EndWidth = 200, float StartSideFalloff = 200, float EndSideFalloff = 200, float StartRoll = 0, float EndRoll = 0, int32 NumSubdivisions = 20, bool bRaiseHeights = true, bool bLowerHeights = true, ULandscapeLayerInfoObject* PaintLayer = nullptr, FName EditLayerName = TEXT(""));
```
Runtime/Landscape/Classes/LandscapeProxy.h:868–869 (declaration sits outside the WITH_EDITORONLY_DATA block that ends at LandscapeProxy.h:836; "Editor-time blueprint functions" section).
**Export**: `LANDSCAPE_API` (method-level, UFUNCTION BlueprintCallable) | **Module**: none — Landscape already linked | **Guards**: none at the call site (function internally no-ops outside editor; MifBridge is editor-only anyway)
**Bucket**: transacted — same hazard class as sculpt_landscape (heightmap+weightmap writes through FLandscapeEditDataInterface), which ships transacted today. After the call, mirror sculpt_landscape's collision epilogue (`RecreateCollisionComponents()` + `PostEditChange()`, MifBridgeLandscape.cpp:449–460) or traces will still hit the old terrain.
**Async**: no (synchronous; NumSubdivisions bounded by param validation, cap at 100)
**Params**: | name | aliases | type | default | required |
| actorPath | actor | string (actor owning the spline) | — | yes (strict) |
| component | componentName | string | first USplineComponent | no |
| landscape | — | string (label/path; FindLandscape convention of MifBridgeLandscape.cpp:67) | sole landscape | no |
| startWidth / endWidth | width (sets both) | number (world units, spline-local space) | 200 | no |
| startFalloff / endFalloff | falloff (sets both) | number | 200 | no |
| startRoll / endRoll | — | number (degrees) | 0 | no |
| subdivisions | numSubdivisions | int 1..100 | 20 | no |
| raise | raiseHeights | bool | true | no |
| lower | lowerHeights | bool | true | no |
| paintLayer | layerInfo | string (ULandscapeLayerInfoObject path) | none — skip painting | no |
Unrecognised → error. raise:false AND lower:false → error `"raise and lower are both false — nothing would change"`.
**Failure modes**:
- Actor/spline not found → same messages as set_spline_points (MifBridgeWorld.cpp:229–239).
- No landscape → `"no landscape found — call create_landscape first"`.
- paintLayer given but not among the landscape's layers → engine silently does nothing (header line 865 says so verbatim) — PRE-CHECK against ULandscapeInfo::Layers (as landscape_info does, MifBridgeLandscape.cpp:746–756) and error: `"layerInfo '<path>' is not configured on this landscape — landscape_info lists the layers"`. This silent no-op is exactly the #1 bug class the contract bans.
- Spline entirely off the landscape → heights unchanged; detect via a before/after checksum of the affected extent and warn `verticesTouched:0`.
**Cooked**: works on any in-memory ALandscape, including a create_landscape scratch one (loose). On the cooked IslaSombra landscape it will edit in memory but the map cannot be saved — same warning convention as move_actors_to_sublevel.
**Verify**: trace_ground at 3 points along the spline before/after — Z must move toward the spline (numeric delta); with paintLayer, a paint-weight readback (see export_weightmap below) shows non-zero weight along the corridor.
**Score**: U5 E4 R4 → tier 0 — closes the documented road story (docs/07_TOWN_BUILD_PLAN.md, docs/08_LANDSCAPE.md); composes with existing set_spline_points + spawn_actor_in_level
**Phase-2 verdict**: CORRECTED — signature verbatim at LandscapeProxy.h:868-869 and the PaintLayer silent-no-op pre-check stands (header comment :865 verified). Two hazards from the implementation (Runtime/Landscape/Private/LandscapeBlueprintSupport.cpp:19-44) added: (a) `GetLandscapeInfo()->LandscapeActor.Get()` is dereferenced UNGUARDED — a landscape without a registered ULandscapeInfo crashes; pre-check GetLandscapeInfo()!=nullptr, and a null LandscapeActor (proxy-only WP landscape) silently no-ops — detect and report; (b) on a layers-enabled landscape (`HasLayersContent()`), an unresolvable EditLayerName (the default empty name included, unless a reserved splines layer applies) logs an error and RETURNS WITHOUT DEFORMING (cpp:26-31) — a silent no-op through the bridge. Handler must pre-check `Landscape->HasLayersContent()` and error "landscape has edit layers — pass editLayerName naming an existing layer" (create_landscape scratch landscapes have layers off, so the tier-0 road path is unaffected). Tier 0 stands.

### create_landscape_spline
**Purpose**: Author a REAL landscape spline (control points + segments on the landscape's ULandscapeSplinesComponent) from a world-space point list — the native roads/rivers primitive with per-point width/falloff and paint-layer metadata, which apply_landscape_splines can then raster into the terrain. Unlike apply_spline_to_landscape (fire-and-forget deform along a plain spline), the landscape spline persists as an editable structure and can carry spline meshes.
**Engine API** (the full authoring path — every piece verified):
```cpp
// ownership + creation of the component when absent:
UPROPERTY(EditAnywhere, ...)  TObjectPtr<ULandscapeSplinesComponent> SplineComponent;   // LandscapeProxy.h:412
virtual ULandscapeSplinesComponent* GetSplinesComponent() const override { return SplineComponent; }  // LandscapeProxy.h:1088
LANDSCAPE_API virtual void CreateSplineComponent() override;                            // LandscapeProxy.h:1151
// mutable public inline accessors (bypass the protected arrays legitimately):
TArray<TObjectPtr<ULandscapeSplineControlPoint>>& GetControlPoints() { return ControlPoints; }  // LandscapeSplinesComponent.h:161
TArray<TObjectPtr<ULandscapeSplineSegment>>& GetSegments() { return Segments; }                 // LandscapeSplinesComponent.h:164
// per-object data written directly (public UPROPERTYs, no linkage needed):
FVector Location;   // LandscapeSplineControlPoint.h:57     float Width;        // :65
FRotator Rotation;  // LandscapeSplineControlPoint.h:61     float SideFalloff;  // :73
float EndFalloff;   // LandscapeSplineControlPoint.h:89     FName LayerName;    // :101
FLandscapeSplineSegmentConnection Connections[2];  // LandscapeSplineSegment.h:193-194
float TangentLen; FName SocketName; TObjectPtr<ULandscapeSplineControlPoint> ControlPoint; // LandscapeSplineSegment.h:101-110
uint32 bRaiseTerrain:1; uint32 bLowerTerrain:1;    // LandscapeSplineSegment.h:206,210
// rebuild entry points — declared virtual and PUBLIC in WITH_EDITOR blocks:
virtual void UpdateSplinePoints(bool bUpdateCollision = true, bool bUpdateAttachedSegments = true, bool bUpdateMeshLevel = false); // LandscapeSplineControlPoint.h:265
virtual void UpdateSplinePoints(bool bUpdateCollision = true, bool bUpdateMeshLevel = false); // LandscapeSplineSegment.h:347
virtual void AutoFlipTangents();  // LandscapeSplineSegment.h:342
virtual void AutoCalcRotation();  // LandscapeSplineControlPoint.h:255
```
Authoring sequence mirrored verbatim from the editor tool: Editor/LandscapeEditor/Private/LandscapeEdModeSplineTools.cpp:282–401 (AddSegment) and :481–541 (AddControlPoint) — NewObject with `RF_Transactional` outered to the splines component, append to array, wire `Connections[0/1].ControlPoint`, `TangentLen = (EndLocation-StartLocation).Size()`, `ConnectedSegments.Add(FLandscapeSplineConnection(NewSegment, 0/1))` (inline ctor, LandscapeSplineControlPoint.h:26–30), then UpdateSplinePoints.
**Export**: classes are `UCLASS(MinimalAPI)` (LandscapeSplinesComponent.h:101, LandscapeSplineControlPoint.h:49, LandscapeSplineSegment.h:187). Route: (a) `NewObject<T>` works — MinimalAPI exports StaticClass registration; (b) all data writes are public UPROPERTY member access — no linkage; (c) UpdateSplinePoints / AutoCalcRotation / AutoFlipTangents are **unexported but public and virtual — called through the object pointer they dispatch via vtable, which needs no import symbol**. This is an explicit, deliberate reliance on vtable dispatch: it stays valid as long as MifBridge compiles with the same WITH_EDITOR value as the Landscape module (both editor-only — true by construction). GetBestConnectionTo / GetConnectionLocationAndRotation (LandscapeSplineControlPoint.h:243,249) are also virtual → same route. `CreateSplineComponent` is LANDSCAPE_API proper.
**Module**: none — Landscape already linked | **Guards**: the UpdateSplinePoints/AutoCalcRotation declarations live inside `#if WITH_EDITOR` (block ends LandscapeSplineControlPoint.h:274) — call sites need `#if WITH_EDITOR` (MifBridge is editor-only; guard is belt-and-braces).
**Bucket**: transacted — the editor tool itself runs FScopedTransaction around exactly this sequence (LandscapeEdModeSplineTools.cpp:284,483); objects are created RF_Transactional; no compile/world-swap.
**Async**: no (UpdateSplinePoints with bUpdateCollision=true on the final pass only, false while iterating — the editor's own interactive pattern, LandscapeSplinesComponent.h:177 comment).
**Params**: | name | aliases | type | default | required |
| points | — | [{x,y,z}] world-space, ≥2 | — | yes (strict) |
| landscape | actorPath | string | sole landscape | no |
| width | halfWidth | number (world units; ControlPoint.Width is HALF-width per DisplayName meta :64) | 500 | no |
| sideFalloff | falloff | number | 200 | no |
| endFalloff | — | number | 200 | no |
| layerName | paintLayer | string (FName written to both control points and segments) | none | no |
| raiseTerrain / lowerTerrain | — | bool | true/true | no |
| snapToGround | — | bool (trace each point like set_spline_points does, MifBridgeWorld.cpp:267–278) | true | no |
| apply | applySplines | bool — run ULandscapeInfo::ApplySplines at the end | false | no |
Unrecognised → error. Points are transformed world→spline-component-local before writing Location (component transform inverse — the editor passes LocalLocation, LandscapeEdModeSplineTools.cpp:481,490).
**Failure modes**:
- <2 points → `"points[] needs at least 2 entries"`.
- No landscape / no ULandscapeInfo → create_landscape-style messages.
- SplineComponent null → call CreateSplineComponent() first (exported), then RegisterComponent; report `splineComponentCreated:true`.
- layerName not declared by the landscape material → same pre-check + error as apply_spline_to_landscape (silent-no-op ban).
**Cooked**: works on loose landscapes; in-memory only on the cooked map (warning field).
**Verify**: list_landscape_splines (below) reports controlPoints == N and segments == N-1 with matching world locations (±1uu); with apply:true, trace_ground deltas along the path as in apply_spline_to_landscape.
**Score**: U5 E2 R3 → tier 1 — the native primitive behind every road/river in the shipped game’s toolset; effort is real (connection wiring) but the sequence is copied line-for-line from the editor tool
**Phase-2 verdict**: CONFIRMED — every accessor re-verified one by one: mutable inline `GetControlPoints()`/`GetSegments()` (LandscapeSplinesComponent.h:161/:164, class MinimalAPI :101); control-point public UPROPERTYs Location :57, Rotation :61, Width :65 (Half-Width meta :64), SideFalloff :73, EndFalloff :89, LayerName :101 (inside WITH_EDITORONLY_DATA from :91 — fine, editor module); `ConnectedSegments` is PUBLIC (declared under `public:` at :208-210 — the one accessor phase-1 used without citing; now cited); FLandscapeSplineConnection inline ctor :26-30; segment Connections[2] :193-194 public, TangentLen/SocketName/ControlPoint :102-110, bRaise/bLowerTerrain :206/:210, LayerName :202; unexported public virtuals UpdateSplinePoints (ControlPoint :265 verbatim incl. 3-param default set, Segment :347 2-param), AutoCalcRotation :255, AutoFlipTangents :342, GetBestConnectionTo :243, GetConnectionLocationAndRotation :249 — all inside `#if WITH_EDITOR` (ends :274/:356), so the vtable-dispatch route requires WITH_EDITOR parity, which holds (both modules editor-built); classes are non-final so MSVC cannot devirtualize a call through an object pointer — no import symbol needed. Proxy side verified: SplineComponent UPROPERTY :411-412, GetSplinesComponent inline override :1088, `LANDSCAPE_API virtual void CreateSplineComponent()` :1151 (WITH_EDITOR block from :1134). Editor tool sequence matches line-for-line: AddSegment = LandscapeEdModeSplineTools.cpp:282-401 (FScopedTransaction :284, NewObject RF_Transactional :313, Connections wiring :316-320, TangentLen :328-329, ConnectedSegments.Add :368-369, UpdateSplinePoints cascade :371-400), AddControlPoint = :481-543 (FScopedTransaction :483, LocalLocation → Location :490). The world→component-local transform note is correct.

### list_landscape_splines
**Purpose**: Read back every landscape-spline control point and segment (locations, widths, connections, layer names) — the verification read for create_landscape_spline and the aim-assist for apply_landscape_splines.
**Engine API**:
```cpp
const TArray<TObjectPtr<ULandscapeSplineControlPoint>>& GetControlPoints() const { return ControlPoints; } // LandscapeSplinesComponent.h:160
const TArray<TObjectPtr<ULandscapeSplineSegment>>& GetSegments() const { return Segments; }                // LandscapeSplinesComponent.h:163
LANDSCAPE_API TArray<TScriptInterface<ILandscapeSplineInterface>> GetSplineActors() const;                 // LandscapeInfo.h:442
```
Plus public UPROPERTY reads (Location/Rotation/Width/SideFalloff/EndFalloff/LayerName/Connections — citations in create_landscape_spline above).
**Export**: inline accessors + LANDSCAPE_API GetSplineActors | **Module**: none | **Guards**: none for data reads
**Bucket**: read-only — pure query.
**Async**: no
**Params**: | name | aliases | type | default | required |
| landscape | actorPath | string | all landscapes | no |
Unrecognised → error.
**Failure modes**: no landscape → empty list + note (mirror landscape_info's count:0 convention, MifBridgeLandscape.cpp:763–766).
**Cooked**: works; if the cooked world stripped spline editor data (ControlPoints/Segments are TextExportTransient but serialized), report `splineComponent:null` honestly rather than guessing.
**Verify**: counts round-trip against create_landscape_spline inputs; control-point world positions (component transform × Location) match requested points within 1uu.
**Score**: U3 E4 R5 → tier 1 (mutation without its reader violates the house verification rule)
**Phase-2 verdict**: CONFIRMED — const accessors verbatim at LandscapeSplinesComponent.h:160/:163; `GetSplineActors` LANDSCAPE_API verbatim at LandscapeInfo.h:442, with one guard nuance: it sits inside `#if WITH_EDITOR` (block starts :438), so the "Guards: none" claim is loose — call site should carry the same belt-and-braces `#if WITH_EDITOR` as create_landscape_spline (always true here).

### apply_landscape_splines
**Purpose**: Rasterise ALL landscape splines into the heightmap/weightmaps (the "Deform Landscape to Splines" button) — separate endpoint so width/layer tweaks made via set_property on control-point object paths can be re-applied without re-authoring.
**Engine API**:
```cpp
LANDSCAPE_API bool ApplySplines(bool bOnlySelected, TSet<TObjectPtr<ULandscapeComponent>>* OutModifiedComponents = nullptr, bool bMarkPackageDirty = true);
```
Runtime/Landscape/Classes/LandscapeInfo.h:222 (class is `UCLASS(Transient) class ULandscapeInfo : public UObject`, LandscapeInfo.h:121–122 — unexported class, method-level LANDSCAPE_API, the same pattern existing handlers already link against). The editor invokes it exactly this way: Editor/LandscapeEditor/Private/LandscapeEdMode.cpp:4268 and the MiscTools "Apply Splines" button (LandscapeEditorDetailCustomization_MiscTools.cpp:108–135).
**Export**: `LANDSCAPE_API` method-level | **Module**: none | **Guards**: none (editor build)
**Bucket**: transacted — heightmap/weightmap writes; sculpt_landscape precedent (MifBridgeCommon.cpp:277–300 shows only create_landscape is self-managed among landscape endpoints). Pass bOnlySelected=false always. Collect OutModifiedComponents and run the sculpt collision epilogue (RecreateCollisionComponents + PostEditChange, MifBridgeLandscape.cpp:455–460).
**Async**: no — synchronous; cost bounded by spline extent, not map size.
**Params**: | name | aliases | type | default | required |
| landscape | actorPath | string | sole landscape | no |
Unrecognised → error.
**Failure modes**:
- No splines authored → ApplySplines returns false → response `applied:false` with note "no landscape splines to apply — create_landscape_spline first" (not a throw).
- No ULandscapeInfo → standard message.
**Cooked**: in-memory caveat as every landscape mutation on the cooked map.
**Verify**: returns modifiedComponents count from OutModifiedComponents; trace_ground before/after along a segment shows Z convergence to the spline; landscape_info componentsWithoutWeightmap must not increase.
**Score**: U4 E4 R4 → tier 1
**Phase-2 verdict**: CONFIRMED — signature verbatim at LandscapeInfo.h:222 (UCLASS(Transient) unexported class + method-level LANDSCAPE_API confirmed at :121-122); both editor invocation sites verified (LandscapeEdMode.cpp:4268, LandscapeEditorDetailCustomization_MiscTools.cpp:108-135). Implementation audited (Runtime/Landscape/Private/LandscapeSplineRaster.cpp:564-601): synchronous, no modal, no slow-task; it tolerates a null LandscapeActor and scopes the edit layer itself (FScopedSetLandscapeEditingLayer, :571-573), and returns false when there are no registered splines (:614-617) — matching the entry's applied:false design. Bucket precedent re-verified against MifBridgeCommon.cpp:277-301.

### export_heightmap
**Purpose**: Dump landscape height data (whole extent or window) to a 16-bit file an agent or external tool can inspect/diff — the missing bulk-read that makes terrain work numerically verifiable beyond spot traces.
**Engine API**:
```cpp
LANDSCAPE_API void GetHeightDataFast(const int32 X1, const int32 Y1, const int32 X2, const int32 Y2, uint16* Data, int32 Stride, uint16* NormalData = NULL, UTexture2D* InHeightmap = nullptr);
```
Runtime/Landscape/Public/LandscapeEdit.h:198 — the exact call H_sculpt_landscape already makes (MifBridgeLandscape.cpp:376), extended to file output. 16-bit grayscale PNG via IImageWrapper (Runtime/ImageWrapper module: IImageWrapperModule::CreateImageWrapper(EImageFormat::PNG), SetRaw ERGBFormat::Gray 16-bit), or raw little-endian .r16 via FFileHelper::SaveArrayToFile (Core).
**Export**: `LANDSCAPE_API` (the whole FLandscapeEditDataInterface accessor block is method-exported — LandscapeEdit.h:177–231 read) | **Module**: **ImageWrapper — NEW dependency** (engine runtime module, no plugin; only needed for PNG — .r16 needs nothing new) | **Guards**: none beyond editor build
**Bucket**: read-only — GetHeightDataFast does not mutate.
**Async**: no — a 1009×1009 window is ~2MB and completes in-frame; REFUSE windows over 4033×4033 with an error suggesting region windows rather than blocking the frame.
**Params**: | name | aliases | type | default | required |
| file | path, outFile | string (absolute; parent dir must exist) | — | yes (strict) |
| format | — | "png16" / "r16" | "png16" | no |
| landscape | actorPath | string | sole landscape | no |
| minX / minY / maxX / maxY | region | ints (vertex space, same space sculpt's area report uses) | full extent | no |
Unrecognised → error.
**Failure modes**: unwritable path → error naming the directory; region outside extent → clamp AND report the actual window written (never silently emit a different size than requested); window > 4033² → error suggesting windows.
**Cooked**: WORKS against the cooked IslaSombra landscape — height data is live in the loaded world; this is the one landscape endpoint fully useful on base-game terrain (read-only).
**Verify**: response reports width/height/min/max/mean of the uint16 data. sculpt_landscape a known +500uu bump, re-export, mean rises by the predicted amount (bump volume / texel count).
**Score**: U4 E4 R5 → tier 1
**Phase-2 verdict**: CONFIRMED — GetHeightDataFast verbatim at LandscapeEdit.h:198; the whole accessor block :177-231 re-read and is method-level LANDSCAPE_API as claimed; the sculpt precedent constructs FLandscapeEditDataInterface and calls this exact overload (MifBridgeLandscape.cpp:372-376), proving linkability. Bonus alternative worth noting for the implementer: `LANDSCAPE_API void ExportHeightmap(const FString& Filename[, const FIntRect& ExportRegion])` exists on ULandscapeInfo (LandscapeInfo.h:218-219) — a one-call file dump, though it gives less control over format/stats than the proposed route.

### import_heightmap
**Purpose**: Push a 16-bit heightmap file onto an existing landscape region — round-trip partner of export_heightmap; enables external terrain sources (erosion sims, real-world DEMs) without create_landscape's procedural modes.
**Engine API**:
```cpp
LANDSCAPE_API void SetHeightData(int32 X1, int32 Y1, int32 X2, int32 Y2, const uint16* InData, int32 InStride, bool InCalcNormals, const uint16* InNormalData = nullptr, const uint16* InHeightAlphaBlendData = nullptr, const uint8* InHeightRaiseLowerData = nullptr, bool InCreateComponents = false, UTexture2D* InHeightmap = nullptr, UTexture2D* InXYOffsetmapTexture = nullptr,
```
Runtime/Landscape/Public/LandscapeEdit.h:177 (declaration continues with defaulted params; H_sculpt_landscape calls it at MifBridgeLandscape.cpp:443 with InCalcNormals=true then Flush + collision epilogue :444–460 — reuse that epilogue verbatim).
**Export**: `LANDSCAPE_API` | **Module**: ImageWrapper (as export_heightmap) | **Guards**: none
**Bucket**: transacted — sculpt_landscape precedent, identical write path.
**Async**: no (same 4033² window rule).
**Params**: | name | aliases | type | default | required |
| file | path | string | — | yes (strict; must exist and decode to 16-bit single channel) |
| format | — | "png16" / "r16" | inferred from extension | no |
| landscape | actorPath | string | sole landscape | no |
| minX / minY | origin | ints | extent min | no |
| width / height | — | ints | from PNG header | required for r16 (raw has no header) — error "r16 needs width and height" |
Unrecognised → error.
**Failure modes**: dimension mismatch vs landscape extent → error stating both sizes; 8-bit PNG → error "heightmap must be 16-bit — 8-bit input quantises to 256 levels (visible terracing)"; decode failure names the file.
**Cooked**: in-memory only on cooked map (warning field), full on loose landscapes.
**Verify**: export_heightmap of the same window returns identical min/max/mean (lossless round-trip); trace_ground at a chosen texel matches HeightToWorld of the file value within one height step (0.78uu at scale 100).
**Score**: U4 E3 R3 → tier 1
**Phase-2 verdict**: CONFIRMED — SetHeightData verbatim at LandscapeEdit.h:177-178 (declaration continues onto :178 with InUpdateBounds/InUpdateCollision/InGenerateMips defaults, as the entry notes); the sculpt call + collision epilogue precedent re-verified at MifBridgeLandscape.cpp:443-460 exactly as cited.

### export_weightmap
**Purpose**: Dump a paint layer's weights (0–255) to an 8-bit PNG — numerically verifies paint_landscape / apply_spline_to_landscape corridors and audits layer coverage.
**Engine API**:
```cpp
LANDSCAPE_API void GetWeightDataFast(ULandscapeLayerInfoObject* LayerInfo, const int32 X1, const int32 Y1, const int32 X2, const int32 Y2, uint8* Data, int32 Stride);
```
Runtime/Landscape/Public/LandscapeEdit.h:221
**Export**: `LANDSCAPE_API` | **Module**: ImageWrapper for PNG8, none for raw | **Guards**: none
**Bucket**: read-only.
**Async**: no (same window cap).
**Params**: as export_heightmap, plus:
| layerInfo | layer | string (ULandscapeLayerInfoObject path, resolved like paint_landscape MifBridgeLandscape.cpp:492–500) | — | yes (strict) |
Unrecognised → error.
**Failure modes**: layerInfo unknown → paint_landscape's message; layer never painted → all-zero file with note "layer has no weight in this window" (a zero image is a valid answer, not an error).
**Cooked**: works read-only against the cooked landscape.
**Verify**: response carries nonZeroTexels and mean; paint_landscape a 1000uu disc at weight 1.0 → re-export → nonZeroTexels within 10% of the disc's texel area.
**Score**: U3 E4 R5 → tier 1
**Phase-2 verdict**: CONFIRMED — GetWeightDataFast verbatim at LandscapeEdit.h:221; layer-resolution precedent verified at MifBridgeLandscape.cpp:492-500. Alternative one-call route exists if ever preferred: ULandscapeInfo::ExportLayer (LANDSCAPE_API, LandscapeInfo.h:220-221).

### paint_foliage
**Purpose**: Add instances to the REAL foliage system (AInstancedFoliageActor + FFoliageInfo) for a UFoliageType — unlike the existing add_foliage_instances, which builds a detached HISM holder actor (read first: MifBridgeAuthoring.cpp:428–478) and is therefore invisible to the foliage tools, foliage stat counts, and procedural systems.
**Engine API**:
```cpp
static FOLIAGE_API AInstancedFoliageActor* GetInstancedFoliageActorForCurrentLevel(const UWorld* InWorld, bool bCreateIfNone = false);   // InstancedFoliageActor.h:155
FOLIAGE_API FFoliageInfo* FindOrAddMesh(UFoliageType* InType);                                                                          // InstancedFoliageActor.h:232
FOLIAGE_API FFoliageInfo* AddMesh(UStaticMesh* InMesh, UFoliageType** OutSettings = nullptr, const UFoliageType_InstancedStaticMesh* DefaultSettings = nullptr); // InstancedFoliageActor.h:236
FOLIAGE_API void AddInstances(const UFoliageType* InSettings, const TArray<const FFoliageInstance*>& InNewInstances);                    // InstancedFoliage.h:335
```
All paths relative to Runtime/Foliage/Public/. FFoliageInstance carries public `FVector Location; FRotator Rotation; FVector3f DrawScale3D;` (inherited from FFoliageInstancePlacementInfo, InstancedFoliage.h:49–62; FFoliageInstance : public FFoliageInstancePlacementInfo at :81, GetInstanceWorldTransform at :99).
**Export**: `FOLIAGE_API` method-level throughout (AInstancedFoliageActor is `UCLASS(notplaceable, ..., MinimalAPI, NotBlueprintable)` InstancedFoliageActor.h:27 — every method used is exported; struct methods on FFoliageInfo are exported at InstancedFoliage.h:333–338). The tempting static `AInstancedFoliageActor::AddInstances(UObject*, UFoliageType*, const TArray<FTransform>&)` (InstancedFoliageActor.h:284–285) is UFUNCTION(BlueprintCallable) but UNEXPORTED — usable only via reflection; the exported FFoliageInfo route above is strictly better (no reflection, returns the info for counting).
**Module**: none — Foliage already in MifBridge.Build.cs | **Guards**: the AddInstances/AddMesh block sits in `#if WITH_EDITOR` (ends InstancedFoliageActor.h:289) — call sites guarded (always true in this editor-only module).
**Bucket**: transacted — instance-array append with Modify() support; no object-registration cascade (the IFA and HISM components already exist or are created RF_Transactional by the exported helpers).
**Async**: no — cap instances per call at 10000 (one HISM rebuild); larger sets are batched by the caller.
**Params**: | name | aliases | type | default | required |
| foliageType | type | string (UFoliageType asset path) — OR — | — | one of foliageType / mesh required, both → error "pass foliageType or mesh, not both" |
| mesh | staticMesh | string (UStaticMesh path; goes through AddMesh which auto-creates a transient type — response returns the created type path) | — | see above |
| instances | — | [{x,y,z,yaw?,pitch?,roll?,scale?}] explicit placement — OR — | — | one of instances / scatter |
| scatter | — | {center:{x,y}, radius, count, minScale?, maxScale?, seed?} — positions drawn uniformly in the disc, ground-traced like set_spline_points snapToGround (MifBridgeWorld.cpp:267–278), aligned yaw random | — | see above |
Unrecognised → error.
**Failure modes**:
- Neither instances nor scatter → error naming both.
- Foliage type asset not found → error with the path; mesh not found → add_foliage_instances message precedent (MifBridgeAuthoring.cpp:435).
- Scatter traces that miss ground → those instances skipped, `missedGround:<n>` reported (never silently place at trace origin).
**Cooked**: UFoliageType assets from cooked containers LOAD fine (data assets, not stripped) — foliageType may point at base-game types; the IFA lives in the current editable level. Placing into the cooked persistent level works in memory but cannot be saved (standard warning).
**Verify**: response `{added:<n>, totalForType:<FFoliageInfo::Instances.Num()>}` (Instances array public, InstancedFoliage.h:283); list_foliage (below) confirms the same count; check_overlaps on a sample instance location finds the HISM.
**Score**: U4 E3 R4 → tier 1
**Phase-2 verdict**: CONFIRMED — all four signatures verbatim (InstancedFoliageActor.h:155/:232/:236; InstancedFoliage.h:335); FFoliageInstance layout verified (FFoliageInstancePlacementInfo members :51-56, inheritance :81, GetInstanceWorldTransform :97-100); the unexported-statics warning re-verified (AddInstances/RemoveAllInstances UFUNCTION-no-macro at :284-288, inside WITH_EDITOR ending :289). One precision: `Instances` (:283) is inside `#if WITH_EDITORONLY_DATA` (:275-296) — present in this editor build, but see the list_foliage verdict for the cooked-content consequence.

### list_foliage
**Purpose**: Enumerate foliage types present in the world with instance counts (optionally within a radius) — the reader that makes paint_foliage/remove_foliage_instances verifiable, and the first census tool for base-game foliage.
**Engine API**:
```cpp
FOLIAGE_API bool ForEachFoliageInfo(TFunctionRef<bool(UFoliageType* FoliageType, FFoliageInfo& FoliageInfo)> InOperation);  // InstancedFoliageActor.h:46
static FOLIAGE_API AInstancedFoliageActor* GetInstancedFoliageActorForCurrentLevel(const UWorld* InWorld, bool bCreateIfNone = false); // :155
TArray<FFoliageInstance> Instances;   // InstancedFoliage.h:283 (public member read)
```
Iterate ALL IFAs via TActorIterator<AInstancedFoliageActor> (one per level), not just the current level's.
**Export**: `FOLIAGE_API` | **Module**: none — Foliage linked | **Guards**: FoliageInfos map is WITH_EDITORONLY? (FoliageInfos at InstancedFoliageActor.h:43 is compiled unconditionally; ForEachFoliageInfo exported unconditionally)
**Bucket**: read-only.
**Async**: no
**Params**: | name | aliases | type | default | required |
| center + radius | area | {x,y} + number — count only instances inside | none (count all) | no |
| type | foliageType | string — restrict to one type | all | no |
Unrecognised → error.
**Failure modes**: none fatal; no IFA in any level → `{types:[], note:"no AInstancedFoliageActor in this world"}`.
**Cooked**: works — cooked IFAs keep their instance arrays (runtime data). This is the endpoint that finally answers "what foliage does IslaSombra actually use".
**Verify**: after paint_foliage added N, the type's count rises by exactly N; radius filter of the scatter disc returns ≥ N−missedGround.
**Score**: U4 E4 R5 → tier 1
**Phase-2 verdict**: CORRECTED — signatures verbatim (ForEachFoliageInfo :46, GetInstancedFoliageActorForCurrentLevel :155, Instances :283), but the COOKED claim is wrong: `FFoliageInfo::Instances` is editor-only data (`#if WITH_EDITORONLY_DATA`, InstancedFoliage.h:275-296) and is serialized ONLY when `!Ar.ArIsFilterEditorOnly` (InstancedFoliage.cpp operator<< at :503-514) — cooked packages are saved editor-filtered, so .pak-mounted IFAs load with EMPTY Instances arrays. The FoliageInfos map itself DOES survive (`Ar << FoliageInfos` unconditional for modern versions, InstancedFoliage.cpp:4386), so on cooked content the endpoint enumerates TYPES fine but per-type counts and area filters from Instances read 0. Fix: take counts from the runtime implementation instead — `FFoliageInfo::GetComponent()` (FOLIAGE_API, InstancedFoliage.h:305) → HISM `GetInstanceCount()`, and report `countSource:"editorInstances"|"component"` per type; area filtering on cooked foliage requires the component's per-instance transforms, or honestly report `areaFilter:unsupported` for cooked IFAs. Cooked line downgraded from "works" to "degraded (types + component counts only)". Still the endpoint that answers the IslaSombra census question — just via the component path.

### remove_foliage_instances
**Purpose**: Delete foliage instances by type and/or area — cleanup half of the foliage story (clearing a building footprint before placement).
**Engine API**:
```cpp
FOLIAGE_API void RemoveInstances(TArrayView<const int32> InInstancesToRemove, bool RebuildFoliageTree);  // InstancedFoliage.h:338
FOLIAGE_API FFoliageInfo* FindInfo(const UFoliageType* InType);                                          // InstancedFoliageActor.h:143
```
Indices gathered by scanning the public Instances array (InstancedFoliage.h:283) against the area predicate.
**Export**: `FOLIAGE_API` | **Module**: none | **Guards**: WITH_EDITOR block as paint_foliage
**Bucket**: transacted — index-array removal with Modify().
**Async**: no (RebuildFoliageTree=true once per type, after the last removal — same batching logic as set_spline_points' single UpdateSpline, MifBridgeWorld.cpp:287–289).
**Params**: | name | aliases | type | default | required |
| type | foliageType | string | all types | no |
| center + radius | area | {x,y} + number | — | required unless type given AND all:true (`all:true` without area wipes the whole type — deliberate two-key confirmation, mirroring snap_actors_to_ground's refusal to default to everything, MifBridgeWorld.cpp:360–361) |
Unrecognised → error.
**Failure modes**: neither type nor area → error `"pass type, area, or type+all:true"`; type unknown → error with path.
**Cooked**: in-memory on cooked levels (standard warning).
**Verify**: list_foliage count for the type drops by exactly the response's `removed:<n>`; re-query with the same area returns 0.
**Score**: U3 E3 R4 → tier 1
**Phase-2 verdict**: CORRECTED — signatures verbatim (RemoveInstances InstancedFoliage.h:338, FindInfo InstancedFoliageActor.h:143), but the cooked line inherits the list_foliage finding: index gathering scans `Instances`, which is EMPTY on .pak-mounted IFAs (editor-only data stripped at cook — citations in the list_foliage verdict). So this endpoint cannot see or remove BASE-GAME cooked foliage at all; it works fully on foliage placed in-session or in loose levels. Cooked line corrected from "in-memory (standard warning)" to "refuses for cooked IFAs with error 'cooked foliage instances carry no editor instance data — only foliage painted in this editor session can be removed'".

### create_foliage_type
**Purpose**: Author a UFoliageType_InstancedStaticMesh ASSET (density/scale/alignment rules around a chosen mesh) so paint_foliage and the procedural system have a reusable, saveable brush definition.
**Engine API**:
```cpp
UCLASS(hidecategories=Object, editinlinenew, MinimalAPI)
class UFoliageType_InstancedStaticMesh : public UFoliageType   // FoliageType_InstancedStaticMesh.h:12-13
UPROPERTY(EditAnywhere, BlueprintReadWrite, Category=Mesh, meta=(DisplayThumbnail="true"))
TObjectPtr<UStaticMesh> Mesh;                                  // FoliageType_InstancedStaticMesh.h:17-18
void SetStaticMesh(UStaticMesh* InStaticMesh)                  // FoliageType_InstancedStaticMesh.h:51-53 (inline)
```
No factory needed: NewObject<UFoliageType_InstancedStaticMesh>(Package, Name, RF_Public|RF_Standalone) in a fresh package, SetStaticMesh (inline), tune UFoliageType UPROPERTYs (Density/ScaleX-Y-Z/AlignToNormal etc. — all public UPROPERTYs on UFoliageType, FoliageType.h:104+), then the existing save_package endpoint persists it. (FoliageTypeFactory exists only in editor-private FoliageEdit — not needed, see Negative results.)
**Export**: MinimalAPI class — NewObject works via exported StaticClass; ALL property writes are UPROPERTY member access or inline setters — zero linked symbols | **Module**: none | **Guards**: none
**Bucket**: self-managed — creates+registers a new asset package (create_landscape precedent class: object creation at package scope, MifBridgeCommon.cpp:295).
**Async**: no
**Params**: | name | aliases | type | default | required |
| path | assetPath | string (/Game/... target) | — | yes (strict) |
| mesh | staticMesh | string | — | yes (strict) |
| density | — | number (per 10m²) | class default | no |
| minScale / maxScale | — | number | class default | no |
| alignToNormal | — | bool | class default | no |
| randomYaw | — | bool | class default | no |
Unrecognised → error. (Deliberately a small curated set — everything else is reachable by set_property on the created asset; the endpoint exists for the package+mesh wiring, not to mirror 100 UPROPERTYs.)
**Failure modes**: path exists → error advising duplicate_asset; mesh missing → standard message.
**Cooked**: creates loose assets only; the MESH may come from cooked content (works — mesh assets load from paks).
**Verify**: find_assets at the path returns the new asset; list_object_properties shows Mesh set; paint_foliage with the new type places instances (count check).
**Score**: U3 E3 R4 → tier 2
**Phase-2 verdict**: CONFIRMED — class decl :12-13, Mesh UPROPERTY :17-18, SetStaticMesh inline :51-55 all verbatim; one precision: SetStaticMesh sits inside `#if WITH_EDITOR` (:43-56) and calls the virtual UpdateBounds() — vtable dispatch, no import needed, fine in this editor-only module. The no-factory claim verified: FoliageTypeFactory exists only as Editor/FoliageEdit/Private/FoliageTypeFactory.h/.cpp (module-private, unreachable) — the NewObject route is correct.

### resimulate_procedural_foliage
**Purpose**: Run a procedural foliage simulation and spawn its instances into the world — biome-scale vegetation from one call, using volumes placed by the generic spawner.
**Engine API**:
```cpp
FOLIAGE_API void Simulate(int32 NumSteps = -1);                                                     // ProceduralFoliageSpawner.h:57
FOLIAGE_API bool GenerateProceduralContent(TArray<FDesiredFoliageInstance>& OutInstances);           // ProceduralFoliageComponent.h:117
FOLIAGE_API bool ResimulateProceduralFoliage(TFunctionRef<void(const TArray<FDesiredFoliageInstance>&)> AddInstancesFunc); // ProceduralFoliageComponent.h:111
FOLIAGE_API void RemoveProceduralContent(bool bInRebuildTree = true);                                // ProceduralFoliageComponent.h:121
```
Runtime/Foliage/Public/. UProceduralFoliageSpawner is `UCLASS(BlueprintType, Blueprintable, MinimalAPI)` (ProceduralFoliageSpawner.h:16) with method exports. AProceduralFoliageVolume spawns via the existing spawn_actor_in_level.
**Export**: `FOLIAGE_API` method-level | **Module**: none | **Guards**: ResimulateProceduralFoliage/GenerateProceduralContent are editor-side (WITH_EDITOR in cpp) — guard call sites
**Bucket**: self-managed — the simulation allocates tile objects and the instance-add callback mass-creates foliage instances; a tight per-call transaction around only the instance placement, none around Simulate.
**Async**: POTENTIALLY — Simulate(-1) runs to convergence and can take seconds on big volumes. Phase-1 design: synchronous with a hard step cap (numSteps ≤ 20 default 10) and a documented "raise at your own frame-hitch risk" — a request/poll pair is overkill for a tool invoked rarely. Flag for phase-2 review.
**Params**: | name | aliases | type | default | required |
| volume | actorPath | string (AProceduralFoliageVolume) | — | yes (strict) |
| numSteps | steps | int 1..20 | 10 | no |
| clearExisting | — | bool (RemoveProceduralContent first) | true | no |
Unrecognised → error.
**Failure modes**: volume has no spawner assigned → error naming the FoliageSpawner property and suggesting set_property; spawner has no foliage types → simulated but zero instances → report `spawned:0` with the spawner's type count.
**Cooked**: spawner assets from paks load; instances land in editable levels (standard cooked-save warning if targeting the cooked persistent level).
**Verify**: response `{spawned:<n>}`; list_foliage total across types rises by n.
**Score**: U3 E2 R3 → tier 2 (needs the step-cap design decision)
**Phase-2 verdict**: CONFIRMED — all four signatures verbatim (ProceduralFoliageSpawner.h:57; ProceduralFoliageComponent.h:111/:117/:121, all FOLIAGE_API). Guard precision: the component methods are NOT header-guarded (the header's `#if WITH_EDITOR` block only starts at :144) — the entry's "guard call sites" instruction is safe either way. The synchronous-with-step-cap design remains flagged for the merge review as phase-1 requested; Simulate drives FScopedSlowTask-style long work, so keep the cap conservative.

### set_water_body_profile
**Purpose**: Set the per-point water parameters (depth, river width, flow velocity) that live in UWaterSplineMetadata curves — set_spline_points can already reshape a water body's course (see Compositions) but CANNOT touch these curves, and they are what make a river look like a river.
**Engine API**:
```cpp
class WATER_API AWaterBody : public AActor, public IWaterBrushActorInterface        // WaterBodyActor.h:30 (class-level export)
UWaterSplineComponent* GetWaterSpline() const { return SplineComp; }                // WaterBodyActor.h:79 (inline)
UWaterBodyComponent* GetWaterBodyComponent() const { return WaterBodyComponent; }   // WaterBodyActor.h:91 (inline)
class WATER_API UWaterSplineComponent : public USplineComponent                     // WaterSplineComponent.h:25
class WATER_API UWaterSplineMetadata : public USplineMetadata                       // WaterSplineMetadata.h:56
UPROPERTY(EditAnywhere, Category="Water") FInterpCurveFloat Depth;                  // WaterSplineMetadata.h:80-81
UPROPERTY(EditAnywhere, Category = "Water") FInterpCurveFloat WaterVelocityScalar;  // WaterSplineMetadata.h:84-85
UPROPERTY(EditAnywhere, Category = "Water") FInterpCurveFloat RiverWidth;           // WaterSplineMetadata.h:88-89
class WATER_API UWaterBodyComponent : public UPrimitiveComponent                    // WaterBodyComponent.h:121
void UpdateAll(const FOnWaterBodyChangedParams& InParams);                          // WaterBodyComponent.h:134
struct FOnWaterBodyChangedParams { ... bool bShapeOrPositionChanged = false; ... }  // WaterBodyComponent.h:98-115 (inline ctor)
```
All paths relative to D:/UE532/Engine/Plugins/Experimental/Water/Source/Runtime/Public/.
**Export**: class-level `WATER_API` on every class touched (UpdateAll needs no method macro — the class exports it) | **Module**: **Water — NEW dependency** (runtime module of the Water plugin, which is PROJECT-ENABLED in DrugDealerSimulator2.uproject per the brief) | **Guards**: none for the curve writes; UpdateAll is compiled unconditionally
**Bucket**: transacted — property writes + a rebuild call, no object registration.
**Async**: no
**Params**: | name | aliases | type | default | required |
| actorPath | actor | string (any AWaterBody subclass) | — | yes (strict) |
| depths | depth | number[] — one per spline point, or a single value broadcast | — | at least one of depths/widths/velocities |
| widths | riverWidth | number[] (river bodies only — ignored+ERROR on lake/ocean, never silent) | — | see above |
| velocities | velocity | number[] | — | see above |
Array length must equal the water spline's point count → error stating both numbers otherwise.
**Failure modes**:
- Actor is not an AWaterBody → `"'<actor>' is not a water body (class <cls>)"`.
- widths on a non-river → error naming the body type (RiverWidth curve only drives river geometry).
- Point-count mismatch → error with counts.
**Cooked**: water body actors in the cooked persistent level are editable in memory only (standard warning); new water bodies in mod levels fully work.
**Verify**: read back the metadata curves via list_object_properties on the WaterSplineMetadata objectPath (points echo numerically); water surface Z at a spline point via trace against WaterBodyComponent's collision or get_actor_bounds delta.
**Score**: U4 E3 R3 → tier 1 (Water is project-enabled and the shipped island is surrounded by it; pairs with the spawn composition below)
**Phase-2 verdict**: CONFIRMED — every citation verified verbatim: AWaterBody class-level WATER_API (WaterBodyActor.h:30; note the UCLASS is Abstract — actorPath must resolve to a concrete River/Lake/Ocean/Custom subclass, all WATER_API per WaterBodyRiverActor.h:26 et al.), GetWaterSpline :79 and GetWaterBodyComponent :91 inline, UWaterSplineComponent WATER_API (WaterSplineComponent.h:25), UWaterSplineMetadata WATER_API (WaterSplineMetadata.h:55-56) with Depth :80-81 / WaterVelocityScalar :84-85 / RiverWidth :88-89 (RiverWidth's "Rivers Only" comment at :87 confirms the non-river error rule), FOnWaterBodyChangedParams :98-115 inline ctor, UpdateAll :134 compiled unconditionally on the class-exported UWaterBodyComponent (:120-121). The pre-existing-AWaterZone question stays in UNVERIFIED as phase-1 flagged.

### build_reflection_captures
**Purpose**: Recapture every reflection capture in the world after geometry/lighting changes — the LEVEL half of the story (spawn captures with spawn_actor_in_level, recapture here, report count); axis D may claim the renderer half, coordinate at merge.
**Engine API**:
```cpp
UNREALED_API void BuildReflectionCaptures(UWorld* World = GWorld);
```
Editor/UnrealEd/Classes/Editor/EditorEngine.h:2321 (UEditorEngine method; call as GEditor->BuildReflectionCaptures(World)).
**Export**: `UNREALED_API` | **Module**: none — UnrealEd linked | **Guards**: none
**Bucket**: self-managed — it re-uploads capture data and marks packages dirty internally; no meaningful undo semantics (a recapture is not user data), so no transaction at all.
**Async**: no, but SLOW-SYNCHRONOUS: it flushes rendering commands and reads back every capture on the game thread. Response must carry the elapsed ms and the handler should refuse when a shader compile is in flight (report pendingShaderJobs from GShaderCompilingManager) rather than stall multi-second inside a capture of half-compiled materials.
**Params**: none (unrecognised → error). Count of captures reported from a TActorIterator<AReflectionCapture> pass before/after.
**Failure modes**: zero capture actors → `built:0` + note "spawn ReflectionCapture actors first (spawn_actor_in_level /Script/Engine.SphereReflectionCapture)"; feature level below SM5 → engine skips, report honestly.
**Cooked**: works — captures live in the loaded world; cooked map cannot persist the result (warning).
**Verify**: response `{captures:<n>, elapsedMs:<t>}`; capture_camera before/after on a mirror-adjacent view differs (pixels for taste); MapCheck warning count for "reflection captures need to be rebuilt" drops to zero via run_console_captured MAPCHECK.
**Score**: U3 E4 R3 → tier 2
**Phase-2 verdict**: CORRECTED — signature verbatim at EditorEngine.h:2321, but the implementation (EditorEngine.cpp:3969-3993) makes two of the entry's soft warnings HARD requirements: (a) it calls `FAssetCompilingManager::Get().FinishAllCompilation()` unconditionally when the shader compiler exists (:3978-3982) — an unbounded blocking wait for EVERY in-flight shader/asset compile on the game thread, so the "refuse when pendingShaderJobs>0" mitigation is MANDATORY, not advisory (also check FAssetCompilingManager::GetNumRemainingAssets()==0, since it waits on all asset compilation, not just shaders); (b) feature level below SM5 is `check(World->GetFeatureLevel() >= ERHIFeatureLevel::SM5)` (:3989) — a hard assert/CRASH, not an engine skip; the handler must pre-check the feature level and refuse. It also runs a GWarn slow task (progress UI only, no input). Cross-axis: also proposed by axis D — at merge keep ONE endpoint; this LEVEL-side spec plus these two pre-checks is the safer of the two to keep.

### list_data_layers
**Purpose**: Read-only census of World Partition data layers in the open world (name, runtime state, visibility) — the only WP surface worth having against a cooked WP map like IslaSombra (see world probe above): it answers "what layers exist and what is currently on" without pretending the map is editable.
**Engine API**:
```cpp
static UDataLayerManager* GetDataLayerManager(const T* InObject)                    // DataLayerManager.h:49-52 (template inline, routes via WorldPartition->GetDataLayerManager())
ENGINE_API void ForEachDataLayerInstance(TFunctionRef<bool(UDataLayerInstance*)> Func); // DataLayerManager.h:89
ENGINE_API EDataLayerRuntimeState GetDataLayerInstanceRuntimeState(const UDataLayerInstance* InDataLayerInstance) const; // DataLayerManager.h:70
virtual FString GetDataLayerShortName() const { return TEXT("Invalid Data Layer"); }    // DataLayerInstance.h:150 (virtual → vtable dispatch)
virtual FString GetDataLayerFullName() const { return TEXT("Invalid Data Layer"); }     // DataLayerInstance.h:151
```
Runtime/Engine/Public/WorldPartition/DataLayer/. World-partition presence check: `bool IsPartitionedWorld() const { return GetWorldPartition() != nullptr; }` Runtime/Engine/Classes/Engine/World.h:2715 (inline).
**Export**: UDataLayerManager is `UCLASS(Config = Engine, Within = WorldPartition, MinimalAPI)` (DataLayerManager.h:42) with method-level ENGINE_API on everything used; UDataLayerInstance is MinimalAPI with ENGINE_API methods (SetVisible :73, SetIsLoadedInEditor :75) and virtual inline name accessors | **Module**: none — Engine linked | **Guards**: none for reads
**Bucket**: read-only.
**Async**: no
**Params**: none beyond optional | world | — | "editor" | (unrecognised → error).
**Failure modes**: non-WP world → `{isPartitioned:false, layers:[]}` — a valid answer, not an error (new_level maps will hit this constantly).
**Cooked**: WORKS and is the point — IslaSombra is cooked WP; this reads live instances.
**Verify**: layer count is stable across two calls; on a non-WP scratch map returns isPartitioned:false; cross-check one known layer name against the DataLayers outliner.
**Score**: U3 E4 R5 → tier 2 (introspection only; mutation deliberately excluded — see Negative results)
**Phase-2 verdict**: CONFIRMED — all citations verified: UCLASS MinimalAPI DataLayerManager.h:42, template inline GetDataLayerManager :48-53, ENGINE_API GetDataLayerInstanceRuntimeState :70 and ForEachDataLayerInstance :89(-90 const overload), DataLayerInstance.h virtual name accessors :150-151 and exported setters :73/:75, World.h IsPartitionedWorld inline :2715. Implementation tip: the template route pulls in FWorldPartitionHelpers — simpler to resolve via `World->GetDataLayerManager()` (World.h:2709-2710, exported through class-level ENGINE_API on UWorld) and null-check for non-WP worlds.

### create_grass_type
**Purpose**: Author a ULandscapeGrassType asset (mesh + density + placement rules) so scratch-landscape materials (authored by the material axis) can emit procedural grass; completes the create_landscape → material → grass chain for mod maps.
**Engine API**:
```cpp
UCLASS(MinimalAPI)
class ULandscapeGrassType : public UObject          // LandscapeGrassType.h:151-152
UPROPERTY(EditAnywhere, Category = Grass)
TArray<FGrassVariety> GrassVarieties;               // LandscapeGrassType.h:156-157
UPROPERTY(EditAnywhere, Category=Grass)
TObjectPtr<UStaticMesh> GrassMesh;                  // LandscapeGrassType.h:35-36 (FGrassVariety member; struct at :29)
```
Runtime/Landscape/Classes/. Same no-factory route as create_foliage_type: NewObject in a fresh package (RF_Public|RF_Standalone), fill GrassVarieties[0].GrassMesh + GrassDensity, save via existing save_package.
**Export**: MinimalAPI — NewObject via exported StaticClass; all writes are public UPROPERTY member access | **Module**: none — Landscape linked | **Guards**: none
**Bucket**: self-managed — asset-package creation (create_foliage_type precedent).
**Async**: no
**Params**: | name | aliases | type | default | required |
| path | assetPath | string | — | yes (strict) |
| mesh | grassMesh | string | — | yes (strict) |
| density | grassDensity | number (per 10m²) | struct default | no |
| randomRotation / alignToSurface | — | bool | struct defaults | no |
Unrecognised → error.
**Failure modes**: standard asset-creation errors; PLUS the honesty note in the response every time: `note:"a grass type renders only when a landscape material's GrassOutput node references it — cooked base-game materials cannot be edited to add one"`.
**Cooked**: creates loose assets; useless against cooked materials (that is the material axis's problem, and the note says so).
**Verify**: find_assets finds it; list_object_properties shows GrassVarieties[0].GrassMesh set; end-to-end proof requires a loose material with a GrassOutput node (cross-axis).
**Score**: U2 E4 R4 → tier 2 (utility gated on material-axis work; the asset half is trivial and safe)
**Phase-2 verdict**: CORRECTED — class/property citations verified (UCLASS(MinimalAPI) LandscapeGrassType.h:151-152, GrassVarieties :156-157, FGrassVariety :29, GrassMesh :35-36), but the "zero linked symbols" claim is FALSE for the array append: `FGrassVariety` declares a non-inline constructor (LandscapeGrassType.h:33) defined WITHOUT export in Runtime/Landscape/Private/LandscapeGrass.cpp:1948 — `GrassVarieties.AddDefaulted()`/`Emplace()`/copy-construction from MifBridge will not link. Fix: add the element via reflection — find the `GrassVarieties` FArrayProperty, use FScriptArrayHelper::AddValue (element initialization runs through the UScriptStruct's CppStructOps registered inside the Landscape module), then write GrassMesh/GrassDensity into the element through the same reflection path (or the existing set_property machinery on `GrassVarieties[0].GrassMesh` after the element exists). Effort unchanged; the endpoint remains viable.

## Surface inventory — addendum (second half of the sweep)

| Area | Files read |
|---|---|
| Landscape edit data | Runtime/Landscape/Public/LandscapeEdit.h (FLandscapeEditDataInterface exported accessor block 177–231); Runtime/Landscape/Classes/Landscape.h (edit-layer API 261–345, bCanHaveLayersContent 461); LandscapeGrassType.h (FGrassVariety 29–86, class 151–157) |
| Foliage | Runtime/Foliage/Public/InstancedFoliageActor.h (27–300: FOLIAGE_API surface, WITH_EDITOR block ends 289); InstancedFoliage.h (FFoliageInstancePlacementInfo 49–62, FFoliageInstance 81–106, FFoliageInfo instance methods 333–338, Instances member 283); FoliageType.h (104); FoliageType_InstancedStaticMesh.h (12–53); ProceduralFoliageSpawner.h (16–82); ProceduralFoliageComponent.h (42–128) |
| Water (plugin, project-enabled) | D:/UE532/Engine/Plugins/Experimental/Water/Source/Runtime/Public/: WaterBodyActor.h (10–101), WaterBodyComponent.h (98–134, 328, 412–417), WaterSplineComponent.h (25), WaterSplineMetadata.h (42–105), WaterBodyRiverActor.h (14–28); full Public/ dir listed (22 headers incl. Lake/Ocean/Custom actors, WaterBodyExclusionVolume, WaterZone not opened — see UNVERIFIED) |
| World Partition / Level Instances | Runtime/Engine/Public/WorldPartition/WorldPartitionSubsystem.h (50–51); WorldPartition/DataLayer/DataLayerManager.h (29–90); DataLayerInstance.h (46–151); Runtime/Engine/Classes/Engine/World.h (2702–2724); Editor/DataLayerEditor/Public/DataLayer/DataLayerEditorSubsystem.h (67–68); Runtime/Engine/Public/LevelInstance/LevelInstanceSubsystem.h (65–101); LevelInstance/LevelInstanceTypes.h (68–104) |
| Landmass (plugin, project-enabled) | D:/UE532/Engine/Plugins/Experimental/Landmass full header census (find: 4 source headers only — Editor/Public/{LandmassActor.h, LandmassBPEditorExtension.h, LandmassEditorModule.h}, Runtime/Public/{BrushEffectsList.h, FalloffSettings.h, LandmassModule.h, TerrainCarvingSettings.h}); LandmassActor.h (9–30) |
| Oceanology (third-party, project-enabled) | D:/DDS2SDK/Game/Plugins/Oceanology_Plugin/Source — FULL C++ source present, exports exist (`class OCEANOLOGY_PLUGIN_API AOceanologyWaterParent : public AActor` OceanologyWaterParent.h:34); Public/ dir enumerated (60+ headers) |
| Environment | Editor/UnrealEd/Classes/Editor/EditorEngine.h:2318–2321 (BuildReflectionCaptures) |
| Bucket precedent | MifBridgeCommon.cpp 167–168, 277–300 (self-managed set; sculpt/paint are transacted, create_landscape self-managed) |

## Compositions (no new endpoint needed) — part 2

- **Spawn a water body**: `AWaterBodyRiver` / `AWaterBodyLake` / `AWaterBodyOcean` / `AWaterBodyCustom`
  are concrete `UCLASS(Blueprintable)` `WATER_API` actor classes (WaterBodyRiverActor.h:25–26 read;
  Lake/Ocean/Custom headers present in the same dir) → existing `spawn_actor_in_level` with class
  `/Script/Water.WaterBodyRiver` etc. spawns them. **The water spline is already drivable**:
  `UWaterSplineComponent : public USplineComponent` (WaterSplineComponent.h:25) and
  H_set_spline_points finds splines via `FindSpline(Actor, ...)` over USplineComponent
  (MifBridgeWorld.cpp:232) and ends with `Actor->PostEditChange()` (:291), which routes into the
  water body's property-changed chain. Course-reshaping a river is therefore:
  spawn_actor_in_level → set_spline_points(snapToGround) → set_water_body_profile (new, above).
  Only the metadata curves needed a new endpoint.
- **Oceanology**: full source ships in the project plugin with `OCEANOLOGY_PLUGIN_API` exports
  (OceanologyWaterParent.h:34). Its actors are spawnable via spawn_actor_in_level and configured
  via set_property (all tuning lives in UPROPERTY structs). No dedicated endpoint proposed —
  revisit only if a mission needs its Quadtree/wave APIs, which would add a plugin-module dep.
- **Landscape-spline tuning after creation**: every ULandscapeSplineControlPoint / Segment is a
  UObject with a stable objectPath under the splines component — `set_property` +
  `list_object_properties` already edit Width/SideFalloff/LayerName/Mesh per point. The
  create_landscape_spline endpoint exists for the connection topology + rebuild, not for field
  edits. After set_property edits, call apply_landscape_splines to re-rasterise
  (and note: UpdateSplinePoints does NOT run on naked set_property — a `rebuild:true` param on
  apply_landscape_splines that calls RebuildAllSplines covers this:
  `virtual void RebuildAllSplines(bool bBuildCollision = true);` LandscapeSplinesComponent.h:178,
  public virtual → vtable route, same as create).
- **Lightmass importance volume / post-process volume / nav bounds around a district**: generic
  spawn_actor_in_level + set_actor_transform; no endpoint.
- **ALandscapeStreamingProxy enumeration**: landscape_info already iterates
  `TActorIterator<ALandscape>` only (MifBridgeLandscape.cpp:681) — WIDEN it to
  `TActorIterator<ALandscapeProxy>` (base iteration is a one-line change inside the existing
  endpoint, reporting a `type` field "landscape"/"streamingProxy") rather than adding a new
  endpoint. Flagged for the landscape_info owner; ALandscapeStreamingProxy is
  `UCLASS(MinimalAPI, notplaceable)` in LandscapeStreamingProxy.h — iteration + UPROPERTY reads
  only, no new symbols.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

1. **Landscape spline authoring has NO fully-exported path** — the honest statement of the
   blocker the create_landscape_spline entry engineers around: `ULandscapeSplinesComponent`,
   `ULandscapeSplineControlPoint`, `ULandscapeSplineSegment` are all `UCLASS(MinimalAPI)`
   (LandscapeSplinesComponent.h:101, LandscapeSplineControlPoint.h:49, LandscapeSplineSegment.h:187)
   and the rebuild entry points (`UpdateSplinePoints`, `AutoCalcRotation`, `AutoFlipTangents`)
   carry NO export macro (LandscapeSplineControlPoint.h:255–265, LandscapeSplineSegment.h:342–347).
   The editor's own tool accesses the protected arrays as a `friend class FLandscapeToolSplines`
   (LandscapeSplinesComponent.h declares the friend; tool in editor-private
   LandscapeEdModeSplineTools.cpp). Viable ONLY because (a) the arrays have public mutable inline
   accessors (LandscapeSplinesComponent.h:161,164) and (b) every needed method is public
   **virtual** → vtable dispatch without import. If phase-2 rejects the vtable technique, the
   fallback is `ALandscapeProxy::EditorApplySpline` (fully exported, proposal above) and the road
   story still closes — only persistent spline meshes are lost.
   **Phase-2: CONFIRMED** — MinimalAPI decls re-read (:101/:49/:187), unexported public virtuals
   re-read (:255/:265, :342/:347), and the friend is declared at LandscapeSplineControlPoint.h:287
   (`friend class FLandscapeToolSplines`). The vtable route holds; see create_landscape_spline verdict.
2. **Landscape edit layers**: fully exported API exists (`LANDSCAPE_API int32 CreateLayer(FName)`
   Landscape.h:285, `ToggleCanHaveLayersContent()` :345, `RequestLayersContentUpdateForceAll(...)`
   :281) — but `create_landscape` deliberately sets `bCanHaveLayersContent = false`
   (MifBridgeLandscape.cpp:250) BECAUSE sculpt/paint write through FLandscapeEditDataInterface
   directly, and with layers on those writes land in a never-composited layer (comment at
   :246–249). Adding edit-layer endpoints is NOT blocked by exports — it is blocked by a
   CONTRACT: every existing height/weight endpoint would need a layer-aware rewrite
   (SetHeightData→layer-scoped, plus composite scheduling). Recommend: keep layers off in
   phase 1; a future `enable_landscape_layers` migration endpoint must own the sculpt/paint
   rewrite in the same change. Do not ship CreateLayer alone — it would silently divorce
   sculpt_landscape from the visible terrain (the exact silent-failure class the contract bans).
   **Phase-2: CONFIRMED** — all four citations verbatim (Landscape.h:281/:285/:345/:461); the
   create_landscape comment + `bCanHaveLayersContent = false` re-read at MifBridgeLandscape.cpp:246-250.
   Phase-2 adds a live proof of the hazard: EditorApplySpline's implementation refuses/no-ops on a
   layers-enabled landscape with an unresolved layer name (LandscapeBlueprintSupport.cpp:26-31).
3. **Landmass is a dead end for scripting**: the plugin's only actor, `ALandmassActor`, lives in
   the EDITOR module (Plugins/Experimental/Landmass/Source/Editor/Public/LandmassActor.h),
   is UNEXPORTED (`UCLASS(Blueprintable)` :9–10, no LANDMASS*_API) with a handful of
   BlueprintNativeEvent editor-tick hooks (:16–30). The actual brush behaviour is Blueprint
   content (CustomBrush_Landmass) driving landscape EDIT LAYERS — blocked by (2) anyway. Runtime
   module contains only settings structs (BrushEffectsList.h, FalloffSettings.h,
   TerrainCarvingSettings.h — full census above). Nothing to bridge; water-brush terrain carving
   (WaterBrushManager) is the same story.
   **Phase-2: CONFIRMED** — LandmassActor.h re-read: UCLASS(Blueprintable, ...) with NO export macro
   at :9-10, BlueprintNativeEvent editor-tick hooks at :16-31.
4. **`AInstancedFoliageActor::AddInstances` / `RemoveAllInstances` statics are unexported**
   (InstancedFoliageActor.h:284–288, UFUNCTION(BlueprintCallable) but no FOLIAGE_API, inside
   WITH_EDITOR) — reflection-callable, but the exported `FFoliageInfo::AddInstances` route
   (InstancedFoliage.h:335) is superior and is what paint_foliage uses. Recorded so nobody
   "upgrades" to the statics later and wonders why the link fails outside reflection.
   **Phase-2: CONFIRMED** — statics re-read at InstancedFoliageActor.h:284-288 (UFUNCTION, no
   FOLIAGE_API), WITH_EDITOR block ends :289.
5. **`UEditorLevelUtils::GetLevels` is unexported** (EditorLevelUtils.h:329–330 — UFUNCTION but
   no UNREALED_API). Irrelevant: `UWorld::GetLevels()` is reachable; listed to prevent a wasted
   attempt.
   **Phase-2: CONFIRMED** — re-read at :329-330, no export macro.
6. **World Partition conversion of existing maps is commandlet-only**:
   UWorldPartitionConvertCommandlet (Editor/UnrealEd/Classes/Commandlets/
   WorldPartitionConvertCommandlet.h) — runs as a separate process over LOOSE packages; cannot
   run in-process mid-frame, and the only WP map in the project is cooked (probe above). No
   endpoint. Mod maps stay non-WP; sublevels are their streaming story.
   **Phase-2: CONFIRMED** — header exists at the cited path
   (Editor/UnrealEd/Classes/Commandlets/WorldPartitionConvertCommandlet.h).
7. **Data-layer MUTATION deliberately excluded**: `UDataLayerEditorSubsystem` is exported
   (`class DATALAYEREDITOR_API UDataLayerEditorSubsystem final : public UEditorSubsystem`,
   Editor/DataLayerEditor/Public/DataLayer/DataLayerEditorSubsystem.h:67–68, module
   DataLayerEditor would be a NEW editor-only dep) and UDataLayerInstance has exported setters
   (SetVisible, DataLayerInstance.h:73). Excluded on value, not exports: the only WP world is
   cooked (unsaveable), and CreateDataLayer needs a UDataLayerAsset authoring flow on top. If a
   future mission ships WP mod maps, revisit with the citations above.
   **Phase-2: CONFIRMED** — `UCLASS() class DATALAYEREDITOR_API UDataLayerEditorSubsystem final :
   public UEditorSubsystem, public IActorEditorContextClient, public FTickableGameObject` verified
   at DataLayerEditorSubsystem.h:67-68; SetVisible ENGINE_API at DataLayerInstance.h:73.
8. **Cooked-map streaming cells**: the persistent-level flattening observed in the probe means
   per-cell streaming levels of cooked WP maps appear/disappear outside ULevelStreaming editor
   conventions; list_sublevels reports what UWorld exposes and must NOT pretend cooked cells are
   editable sublevels (they will show as unnamed/transient streaming levels if loaded). Response
   should tag entries `transient:true` when the streaming level's package has no on-disk file.
   **Phase-2: reviewed** — design guidance, no engine citation to falsify; consistent with the
   verified UWorld::GetStreamingLevels surface (World.h:1037).

## UNVERIFIED

### create_level_instance (DEMOTED from Proposed endpoints by Phase-2)
**Phase-2 verdict**: DEMOTED — the sole entry point is MODAL in 5.3.2 and the entry missed it.
`ULevelInstanceSubsystem::CreateLevelInstanceFrom` (decl verified verbatim, LevelInstanceSubsystem.h:98;
impl Runtime/Engine/Private/LevelInstance/LevelInstanceSubsystem.cpp:898) internally calls
`EditorLevelUtils::CreateNewStreamingLevelForWorld(..., /*bUseSaveAs*/true, ...)` with the flag
HARD-CODED true (LevelInstanceSubsystem.cpp:999-1000). That branch is
`FEditorFileUtils::SaveLevelAs` (EditorLevelUtils.cpp:760-762) → `SaveAsImplementation` — a modal
Save-As dialog (FileHelpers.cpp:1469-1486). `FNewLevelInstanceParams::LevelPackageName`
(LevelInstanceTypes.h:103-104) only seeds the filename variable, which the SaveAs branch then
ignores; `bPromptForSave`/`bAlwaysShowDialog` (:98/:107) are consumed by the editor-module UI, not
by the subsystem. A modal dialog mid-HTTP request deadlocks the editor — the exact hazard class
the contract bans, with no dialog-free exported path found this pass. Original phase-1 entry
preserved below for a future revisit (a 5.4+ engine change or a bridge-side reimplementation of
the move+save sequence could resurrect it).

<details>
<summary>Original phase-1 entry (citations verified, design blocked on the modal)</summary>

**Purpose**: Pack a set of placed actors into a Level Instance (reusable sub-level actor — the modern prefab). Works in NON-partitioned levels too, so a mod map can turn a finished building + props into one placeable unit.
**Engine API**:
```cpp
ENGINE_API ILevelInstanceInterface* CreateLevelInstanceFrom(const TArray<AActor*>& ActorsToMove, const FNewLevelInstanceParams& CreationParams);  // LevelInstanceSubsystem.h:98
ENGINE_API bool BreakLevelInstance(ILevelInstanceInterface* LevelInstance, uint32 Levels = 1, TArray<AActor*>* OutMovedActors = nullptr);          // LevelInstanceSubsystem.h:101
struct FNewLevelInstanceParams {
  ELevelInstanceCreationType Type = ELevelInstanceCreationType::LevelInstance;   // LevelInstanceTypes.h:88-89
  ELevelInstancePivotType PivotType = ELevelInstancePivotType::CenterMinZ;       // :91-92
  FString LevelPackageName = TEXT("");                                           // :103-104
  ... }
```
Runtime/Engine/Public/LevelInstance/. ULevelInstanceSubsystem is a UWorldSubsystem — obtain via World->GetSubsystem<ULevelInstanceSubsystem>(). The exported block sits inside `#if WITH_EDITOR` (LevelInstanceSubsystem.h:65) — editor builds only, which MifBridge is.
**Export**: `ENGINE_API` method-level | **Module**: none — Engine linked | **Guards**: `#if WITH_EDITOR` around the call site
**Bucket**: self-managed — creates a new level package on disk, MOVES actors into it, and spawns the instance actor; the same destroy/recreate hazard as move_actors_to_sublevel, plus a package save. Old actor paths die — response must return the new instance actorPath and the created package.
**Async**: no (synchronous save of a small level).
**Params**: | name | aliases | type | default | required |
| actorPaths | actors | string[] | — | yes (strict, non-empty) |
| path | packagePath | string (target /Game/... package for the instance level) | — | yes (strict; refuse existing) |
| pivot | pivotType | "centerMinZ" / "center" / "actor" | "centerMinZ" | no |
Unrecognised → error.
**Failure modes**: any actor unresolved → error naming it before any move; package exists → error advising add_sublevel instead; CreateLevelInstanceFrom returns null → report engine log line (actors may be non-movable, e.g. landscape).
**Cooked**: source actors from the cooked persistent level cannot be saved into a new package cleanly (their assets stay pak-referenced — fine; the LEVEL is new and loose) — flag `sourcesCooked:true` when detected but proceed; refuse only if the world itself cannot spawn the instance actor.
**Verify**: response `{instanceActor, packagePath, movedActors:<n>}`; list_level_actors count drops by n and gains 1 (the instance); describe_package on the new path shows existsOnDisk:true.
**Score**: U3 E2 R2 → tier 2 (high ceiling for town-kit reuse; effort in path handover design)

</details>

- Whether spawned AWaterBody* actors in 5.3.2 render without a pre-existing `AWaterZone` in the
  level (WaterZoneActor.h present in the plugin dir but NOT opened this sweep) — the
  spawn-composition may need a zone auto-spawn step; phase-2 must open WaterZoneActor.h before
  implementing set_water_body_profile's verify step.
- Live probes for `LevelInstance` / `LandscapeStreamingProxy` / `WaterBody` / `LandscapeSpline`
  actors on IslaSombra returned empty because the editor went down mid-probe (HTTP 000) —
  presence/absence on the real map is UNKNOWN, not "none". Re-run the four classFilter probes
  when the editor is next open on IslaSombra.
- `AWorldSettings.WorldPartition` object pointer could not be read via get_property before the
  restart — WP determination rests on the WorldDataLayers actor evidence (strong but single-source).
- FInterpCurveFloat writes via set_property (for water metadata curves) — the proposal assumes
  direct member writes in C++ (safe); whether the generic set_property path can also address
  `Points[i].OutVal` inside FInterpCurveFloat is untested (irrelevant to the endpoint, relevant
  to the "could this have been a composition" question — if set_property CAN, set_water_body_profile
  drops to a thin UpdateAll wrapper and might merge into a generic post_edit endpoint; phase-2 call).
- `ULandscapeInfo::GetSplineActors` (LandscapeInfo.h:442) return shape on a plain (non-WP,
  non-spline-actor) landscape — expected to contain just the ALandscapeProxy; not executed.

## Coverage log

- DONE: live world probe (partial — editor restarted mid-session; captured world identity, WP
  marker, cooked origin, 4545-actor persistent level); sublevel surface (EditorLevelUtils.h in
  full); landscape splines (all three class headers + editor tool sequence + both exported apply
  paths); heightmap/weightmap IO (LandscapeEdit.h exported block); grass types; edit layers
  (negative w/ contract analysis); foliage (IFA + FFoliageInfo + types + procedural); water
  plugin (actor/component/spline/metadata headers); Oceanology (source census, exports
  confirmed); Landmass (full header census — negative); WP subsystem/data layers/level
  instances/convert commandlet; reflection captures; streaming volumes + lighting scenarios
  (compositions); MifBridge handlers read first: landscape (full), foliage holder, spline
  points, world/level, common bucket table.
- NOT SWEPT (out of time, low expected yield for this axis): HLOD layers (UHLODLayer asset +
  WP HLOD build — cooked-map blocked like data layers, and non-WP mod maps use no WP HLOD);
  packed level actors (APackedLevelActor — same family as level instances, phase-2 can extend
  create_level_instance's params); world composition (legacy, superseded); Riverology plugin
  (project-enabled, unopened — same census treatment as Oceanology recommended);
  ULevelStreamingDynamic::LoadLevelInstance (runtime-spawn streaming — PIE-domain, axis overlap
  with PIE owner).
- Proposal count: 25 main + 5 composition routes + 8 negative results.

## Phase-2 verification log (2026-07-26, adversarial pass)

- Every proposed endpoint's citations re-opened under D:/UE532/Engine/Source and the MifBridge
  sources; every implementation behind a mutation grepped for modal dialogs, blocking waits, and
  asserts (EditorLevelUtils.cpp, EditorEngine.cpp, LevelInstanceSubsystem.cpp, FileHelpers.cpp,
  LandscapeBlueprintSupport.cpp, LandscapeSplineRaster.cpp, InstancedFoliage.cpp).
- Verdicts: 14 CONFIRMED, 10 CORRECTED (fixes appended in place), 1 DEMOTED
  (create_level_instance → UNVERIFIED: CreateLevelInstanceFrom hard-codes a modal SaveAs).
- Recurring phase-1 blind spot: engine-side modal dialogs on "benign" editor utilities —
  AddLevelToWorld (already-present warning, ShowModal), MakeLevelCurrent + RemoveLevelsFromWorld
  (locked-level FMessageDialog), CreateLevelInstanceFrom (SaveAs) — every sublevel mutation now
  carries an explicit pre-check requirement.
- Second blind spot: cooked-content claims made from header reads alone — FFoliageInfo::Instances
  is stripped from cooked packages (serializer check), so list_foliage/remove_foliage_instances
  cooked behaviour was corrected from header-level optimism to serializer-level reality.
- Name collisions vs the 160 covered endpoints: none. Cross-axis: build_reflection_captures is
  also proposed by axis D — merge to ONE endpoint; this file's spec now carries the two mandatory
  pre-checks (compile-manager idle + SM5 feature level).
