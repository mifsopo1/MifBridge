# Axis A — Editor core

_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork "CookedEditorModKit"). Agent: phase-1 breadth._

Note on export style in this fork: many UnrealEd headers use **method-level** `UNREALED_API`
(e.g. `EditorEngine.h`, `EditorActorSubsystem.h`) with `UCLASS(MinimalAPI)` on the class. Every
export claim below was checked against the actual line, not the class declaration.

## Surface inventory

Headers actually read (paths relative to `D:/UE532/Engine/Source` unless noted):

| Header | Lines | What was extracted |
|---|---|---|
| Editor/UnrealEd/Classes/Editor/EditorEngine.h | 3314 | transaction block (:927-938), selection virtuals (:1442-1469), selection getters (:1854-1900) |
| Editor/UnrealEd/Classes/Editor/Transactor.h | 681 | FTransaction (:36-484), UTransactor (:486-680) full read |
| Editor/UnrealEd/Classes/Editor/TransBuffer.h | — | UTransBuffer decl (:14-45), public UndoBuffer/UndoCount fields |
| Editor/UnrealEd/Public/FileHelpers.h | 706 | UEditorLoadingAndSavingUtils (:39-176), FEditorFileUtils save/dirty API (:383-437) |
| Editor/UnrealEd/Public/Subsystems/EditorActorSubsystem.h | 291 | full read |
| Editor/Blutility/Public/EditorUtilitySubsystem.h | 165 | full read |
| MifBridge/Private/MifBridgeLevel.cpp (plugin) | — | H_select_level_actors semantics (:408-459) — already does get/clear/set for ACTORS |

(Inventory continues below — subsystem enumeration, then per-target findings.)

### UEditorSubsystem subclass enumeration

Command: `grep -rn ": public UEditorSubsystem" --glob *.h` over `D:/UE532/Engine/Source/Editor`
(26 hits), `D:/UE532/Engine/Source/Runtime` (0 hits), `D:/UE532/Engine/Plugins` (28 hits).
**Total: 54 subclasses.** One line each:

**Engine Source/Editor (26):**

| Subsystem | Header (Editor/…) | Export | Verdict for MifBridge |
|---|---|---|---|
| UEditorActorSubsystem | UnrealEd/Public/Subsystems/EditorActorSubsystem.h:44 | MinimalAPI + per-method UNREALED_API | mostly covered (spawn/destroy/duplicate/select exist); gap = component selection only |
| UEditorAssetSubsystem | UnrealEd/Public/Subsystems/EditorAssetSubsystem.h:24 | see inventory below | mostly covered (find/rename/duplicate/delete/save exist); metadata-tag get/set is the residual gap — LOW value, skipped |
| UAssetEditorSubsystem | UnrealEd/Public/Subsystems/AssetEditorSubsystem.h:93 | see below | PROPOSED: close_asset_editors (open exists for BPs via open_blueprint) |
| ULayersSubsystem | UnrealEd/Public/Layers/LayersSubsystem.h:44 | see below | PROPOSED: list_layers / modify_actor_layers |
| UUnrealEditorSubsystem | UnrealEd/Public/Subsystems/UnrealEditorSubsystem.h:15 | — | not useful as endpoint: GetEditorWorld/GetGameWorld are internal plumbing the bridge already does |
| UImportSubsystem | UnrealEd/Public/Subsystems/ImportSubsystem.h:33 | — | import events only — asset-import axis territory, no endpoint here |
| UBrushEditingSubsystem | UnrealEd/Public/Subsystems/BrushEditingSubsystem.h:16 | — | BSP brush editing — legacy geometry workflow, not useful for this game |
| UPanelExtensionSubsystem | UnrealEd/Public/Subsystems/PanelExtensionSubsystem.h:82 | — | Slate panel extension registry — UI-only, no agent value |
| UActorEditorContextSubsystem | UnrealEd/Public/Subsystems/ActorEditorContextSubsystem.h:19 | — | drives "current folder/layer/data-layer context" for NEW actors; niche, composition with set_actor_folder covers the need |
| UEditorUtilitySubsystem | Blutility/Public/EditorUtilitySubsystem.h:39 | BLUTILITY_API (class) | PROPOSED: run_editor_utility |
| ULevelEditorSubsystem | LevelEditor/Public/LevelEditorSubsystem.h:36 | LEVELEDITOR_API (class) | pilot/eject, game-view toggle, SaveCurrentLevel; PROPOSED: pilot_actor (viewport already covered otherwise) |
| UStaticMeshEditorSubsystem | StaticMeshEditor/Public/StaticMeshEditorSubsystem.h:25 | STATICMESHEDITOR_API | mesh-asset axis (LODs/collision/sockets) — out of axis A scope, flagged for Phase-2 asset axis |
| USkeletalMeshEditorSubsystem | SkeletalMeshEditor/Public/SkeletalMeshEditorSubsystem.h:19 | SKELETALMESHEDITOR_API | same — asset axis |
| UDataLayerEditorSubsystem | DataLayerEditor/Public/DataLayer/DataLayerEditorSubsystem.h:68 | DATALAYEREDITOR_API | World Partition data layers — DDS2 maps are non-WP (levels streamed classically); no value until a WP map exists |
| UContentBundleEditorSubsystem | WorldPartitionEditor/Public/WorldPartition/ContentBundle/ContentBundleEditorSubsystem.h:93 | WORLDPARTITIONEDITOR_API | WP content bundles — same reason, not useful |
| UPlacementSubsystem | EditorFramework/Public/Subsystems/PlacementSubsystem.h:92 | EDITORFRAMEWORK_API | asset-factory placement; spawn_actor_in_level already covers placement |
| UEditorElementSubsystem | EditorFramework/Public/Subsystems/EditorElementSubsystem.h:20 | EDITORFRAMEWORK_API | TypedElement handle plumbing — internal representation layer, no direct agent verb |
| UAssetEditorUISubsystem | EditorFramework/Public/Toolkits/AssetEditorModeUILayer.h:42 | EDITORFRAMEWORK_API | toolkit UI layering — UI-only |
| UContentBrowserDataSubsystem | ContentBrowserData/Public/ContentBrowserDataSubsystem.h:104 | CONTENTBROWSERDATA_API | virtual-path item enumeration; find_assets (AssetRegistry) already covers queries |
| UEditorConfigSubsystem | EditorConfig/Public/EditorConfigSubsystem.h:14 | — | JSON editor-config persistence — internal |
| UEditorMetadataOverrides | EditorConfig/Public/EditorMetadataOverrides.h:82 | — | metadata override store — internal |
| UEditorInteractiveGizmoSubsystem | Experimental/EditorInteractiveToolsFramework/Public/EditorInteractiveGizmoSubsystem.h:39 | EDITORINTERACTIVETOOLSFRAMEWORK_API | gizmo registry — UI-only |
| ULightEditorSubsystem | LevelEditor/Private/LightEditorSubsystem.h:41 | none (Private header) | UNLINKABLE from plugin — private header; light ops go through set_property anyway |
| UClassTemplateEditorSubsystem | GameProjectGeneration/Public/ClassTemplateEditorSubsystem.h:81 | GAMEPROJECTGENERATION_API | C++ class templates — no use in a cooked-editor modkit (no C++ compile) |
| UStatusBarSubsystem | StatusBar/Public/StatusBarSubsystem.h:56 | STATUSBAR_API | status-bar UI — no agent value |
| UFoliageEditorSubsystem | FoliageEdit/Public/FoliageEditorSubsystem.h:13 | none | unexported; foliage covered by add_foliage_instances |

**Engine Plugins (28):** UVisualStudioToolsBlueprintBreakpointExtension (private, VS integration — no),
USequencerPlaylistsSubsystem (private, VP — no), UPlacementModeSubsystem (private — no),
UExampleCharacterFXEditorSubsystem (example code — no), UGeneratedNaniteDisplacedMeshEditorSubsystem
(Nanite displaced mesh cache — no), UMoviePipelineQueueSubsystem (MRQ — render axis, out of scope A),
UCinePrestreamingEditorSubsystem (no), UWebAPIEditorSubsystem (no), UEnhancedInputEditorSubsystem
(input-editor axis), UWaveFunctionCollapseSubsystem (WFC plugin, disabled by project — no),
UUVEditorSubsystem (UV editor UI — no), UStallLogSubsystem (perf logging — no),
UEditorValidatorSubsystem (DataValidation — validate endpoint exists; deeper validation = QA axis),
UAssetReferencingPolicySubsystem (no), ULevelSequenceEditorSubsystem (Sequencer axis — explicitly
another axis per brief), UEditorGeometryGenerationSubsystem (GeometryScripting — geometry axis),
UMeshPaintModeSubsystem (mesh paint — exotic, skip), UWaterEditorSubsystem (Water plugin —
landscape/water axis), UVPScoutingSubsystem (VP — no), UTypedElementDataStorage*Subsystem ×3
(experimental TEDS — no), UToolPresetAssetSubsystem (no), UUserToolboxSubsystem (no),
UMetaSoundEditorSubsystem (audio axis), UMassActorEditorSubsystem / UMassEntityEditorSubsystem
(Mass not used by DDS2 — no), UMVVMEditorSubsystem (UMG viewmodel — UMG axis if ever).

**Axis-A conclusions from the enumeration**: the four called out by the mission
(UEditorActorSubsystem, UEditorAssetSubsystem, ULayersSubsystem, UEditorUtilitySubsystem) are the
right ones; everything else is either another axis's domain, UI-only, WP-only, or unexported.

## Proposed endpoints

### list_transactions
**Purpose**: Introspect the undo buffer — indices, titles, contexts, object counts, sizes — so an agent can see exactly what its mutations did and decide what to roll back. (The bridge mutates constantly and has NO undo visibility today.)
**Engine API**:
```cpp
UNREALED_API virtual int32 GetQueueLength( ) const PURE_VIRTUAL(UTransactor::GetQueueLength,return 0;);
UNREALED_API virtual int32 GetUndoCount( ) const PURE_VIRTUAL(UTransactor::GetUndoCount,return 0;);
UNREALED_API virtual const FTransaction* GetTransaction( int32 QueueIndex ) const PURE_VIRTUAL(UTransactor::GetTransaction,return nullptr;);
UNREALED_API virtual FTransactionContext GetUndoContext( bool bCheckWhetherUndoPossible = true ) PURE_VIRTUAL(UTransactor::GetUndoContext,return FTransactionContext(););
UNREALED_API virtual bool CanUndo( FText* Text=nullptr ) PURE_VIRTUAL(UTransactor::CanUndo,return false;);
UNREALED_API virtual bool CanRedo( FText* Text=nullptr ) PURE_VIRTUAL(UTransactor::CanRedo,return false;);
UNREALED_API virtual int32 GetCurrentUndoBarrier() const PURE_VIRTUAL(UTransactor::GetCurrentUndoBarrier(), { return INDEX_NONE; });
```
Editor/UnrealEd/Classes/Editor/Transactor.h:555, :597, :573, :583, :539, :548, :626.
Per-transaction detail (FTransaction, same header): `FText GetTitle()` (inline, :398), `FGuid GetId()` (inline, :386), `UNREALED_API void GetTransactionObjects(TArray<UObject*>& Objects) const;` (:460), `UNREALED_API int32 GetRecordCount() const;` (:461), `UNREALED_API SIZE_T DataSize() const;` (:383). Accessed via `GEditor->Trans` (`TObjectPtr<class UTransactor> Trans;` EditorEngine.h:307).
**Export check (the one the mission asked about)**: `UTransactor` is `UCLASS(abstract, transient, MinimalAPI)` (Transactor.h:486) but every introspection method carries method-level `UNREALED_API` — verified line by line above. The concrete `UTransBuffer` is `UCLASS(transient, MinimalAPI)` (TransBuffer.h:14) and most of ITS methods are NOT individually exported, BUT its data members are public (`TArray<TSharedRef<FTransaction>> UndoBuffer;` TransBuffer.h:22, `int32 UndoCount;` TransBuffer.h:25) — field access needs no export. Everything needed is reachable through the exported UTransactor virtuals; casting to UTransBuffer is optional and legal for field reads only. `FTransaction` is a plain class with method-level UNREALED_API on the two non-inline getters used.
**Export**: method-level UNREALED_API (see above) | **Module**: none — UnrealEd already linked | **Guards**: none (UnrealEd is editor-only by definition)
**Bucket**: read-only — pure query of the transactor.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| limit | count, max | int | 20 | no |
| offset | start | int | 0 (0 = newest end) | no |
| includeObjects | include_objects | bool | false | no |
Unrecognised parameter ⇒ `error: unknown parameter 'X' (accepted: limit, offset, includeObjects)`.
**Returns**: `{ queueLength, undoCount, currentIndex (= queueLength - undoCount - 1), canUndo, canRedo, undoBarrier, nextUndoTitle, transactions: [{ index, id, title, context, primaryObject, recordCount, dataSizeBytes, objects?: [paths] }] }`.
**Failure modes**:
- `GEditor->Trans` null (commandlet / -NoTransBuffer): `error: no transaction buffer available (editor running without undo?)`.
- offset >= queueLength: return empty list + queueLength (not an error — lets pollers drain).
**Cooked**: works — the undo buffer is editor state, independent of pak mounting.
**Verify**: run `set_actor_transform` on a test actor, then list_transactions: queueLength increased by exactly 1, the new entry's recordCount >= 1, dataSizeBytes > 0, currentIndex == queueLength-1.
**Score**: U5 E2 R5 → tier 0 — closes the mission-named "no undo endpoint" gap's read half.
**Phase-2 verdict**: CONFIRMED — every Transactor.h line (:539/:548/:555/:573/:583/:597/:626), the FTransaction getters (:383/:386/:398/:460/:461), TransBuffer.h:14/:22/:25 and EditorEngine.h:307 re-read verbatim. All introspection virtuals carry method-level UNREALED_API as claimed.

### undo_transactions
**Purpose**: Undo the last N transactions (or down to a queue index) — the rollback half of the gap; today a bad mutation means manual Ctrl-Z at the keyboard.
**Engine API**:
```cpp
UNREALED_API bool UndoTransaction(bool bCanRedo = true);
```
Editor/UnrealEd/Classes/Editor/EditorEngine.h:934. (Preferred over raw `UTransactor::Undo` — the UEditorEngine wrapper also handles editor-side notification; `virtual bool Undo(bool bCanRedo = true)` Transactor.h:635 has NO export macro but is virtual/PURE_VIRTUAL, callable via vtable — not needed.)
**Export**: method-level `UNREALED_API` on UndoTransaction (EditorEngine.h:934) | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: self-managed — MUST NOT run inside the blanket FScopedTransaction: beginning/running undo while a transaction is open violates the engine's own invariant (`ensure(!GIsTransacting)` in UTransBuffer::BeginInternal, TransBuffer.h:74). Handler calls UndoTransaction bare, in a loop.
**Async**: no — each UndoTransaction applies synchronously on the game thread. Cap N (50) per call to bound frame time.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| count | n, steps | int | 1 | no |
| toIndex | to_index | int | — | no (mutually exclusive with count; undoes until currentIndex == toIndex) |
| allowRedo | allow_redo, canRedo | bool | true | no |
count<1 or count>50 ⇒ `error: count must be 1..50`. Both count and toIndex ⇒ `error: pass either count or toIndex, not both`.
**Returns**: `{ undone: int, stoppedEarly: bool, reason?: string, queueLength, undoCount, currentIndex, titlesUndone: [...] }` (titles captured via GetUndoContext before each step).
**Failure modes**:
- CanUndo() false at step k: stop, return undone=k, reason = the FText out-param of CanUndo (undo barrier reached / buffer empty). NOT an HTTP error if k>0.
- PIE active: world-affecting undo is unreliable during PIE; report `reason: "blocked during PIE — stop_pie first"` when CanUndo fails while PIE runs.
- Transactor active (should be impossible outside a RunEndpoint bug): `error: transactor is active; cannot undo mid-transaction`.
**Cooked**: works — undo state is editor-side.
**Verify**: set_actor_transform actor to Z=500 (read back 500 via get_property) → undo_transactions{count:1} → get_property shows original Z; list_transactions shows undoCount +1, queueLength unchanged.
**Score**: U5 E2 R3 → tier 0 — the mission-named gap. Risk 3 only because undoing a blueprint-reinstancing transaction invalidates cached object pointers; document "re-resolve object paths after undo".
**Phase-2 verdict**: CONFIRMED — EditorEngine.h:934 verbatim; implementation re-read (Editor/UnrealEd/Private/EditorServer.cpp:1411-1420): no modal, no blocking wait — bare `GIsSavingPackage || IsGarbageCollecting()` guard then `Trans->Undo`. PostUndo path does run FBlueprintCompileReinstancer::BatchReplaceInstancesOfClass (EditorServer.cpp:1406) — the re-resolve-paths warning above is exactly right. TransBuffer.h:74 `ensure(!GIsTransacting)` confirmed.

### redo_transactions
**Purpose**: Redo the last N undone transactions — lets an agent A/B a change numerically (measure, undo, re-measure, redo).
**Engine API**:
```cpp
UNREALED_API bool RedoTransaction();
```
Editor/UnrealEd/Classes/Editor/EditorEngine.h:935.
**Export**: method-level `UNREALED_API` | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: self-managed — same GIsTransacting invariant as undo.
**Async**: no. Same 1..50 cap.
**Params**: | count | n, steps | int | 1 | no | ; | toIndex | to_index | int | — | no | (redo while currentIndex < toIndex). Unrecognised ⇒ error.
**Returns**: `{ redone, stoppedEarly, reason?, queueLength, undoCount, currentIndex }`.
**Failure modes**: CanRedo() false (nothing undone, or a new transaction wiped the redo stack — UTransBuffer::BeginInternal removes redoable entries on new Begin, TransBuffer.h:80-90): stop early with reason. ANY bridge mutation between undo and redo kills the redo stack — document loudly in server.py docstring.
**Cooked**: works.
**Verify**: after the undo_transactions verify above, redo_transactions{count:1} → get_property shows Z=500 again; undoCount back down by 1.
**Score**: U4 E1 R3 → tier 0 (same gap, trivial once undo exists).
**Phase-2 verdict**: CONFIRMED — EditorEngine.h:935 verbatim; impl EditorServer.cpp:1422-1431 same non-modal shape as undo; redo-stack wipe behaviour verified at TransBuffer.h:80-90 as cited.

### list_dirty_packages
**Purpose**: Enumerate every unsaved package (map vs content split) so an agent knows exactly what a crash would lose and what save_dirty_packages will touch.
**Engine API**:
```cpp
UNREALED_API static void GetDirtyWorldPackages(TArray<UPackage*>& OutDirtyPackages, const FShouldIgnorePackageFunctionRef& ShouldIgnorePackageFunction = FShouldIgnorePackage::Default);
UNREALED_API static void GetDirtyContentPackages(TArray<UPackage*>& OutDirtyPackages, const FShouldIgnorePackageFunctionRef& ShouldIgnorePackageFunction = FShouldIgnorePackage::Default);
```
Editor/UnrealEd/Public/FileHelpers.h:402, :409 (class FEditorFileUtils, FileHelpers.h:183).
**Export**: method-level `UNREALED_API static` (FEditorFileUtils the class is unexported — only members carry the macro; verified at :402/:409) | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: read-only.
**Async**: no.
**Params**: none (any parameter ⇒ `error: list_dirty_packages takes no parameters`).
**Returns**: `{ mapPackages: [{ package, isCookedOrigin }], contentPackages: [...], counts: { maps, content } }` — isCookedOrigin flags dirty packages whose file lives inside a mounted .pak (unsaveable).
**Failure modes**: none meaningful; empty arrays are the clean-state success case.
**Cooked**: works, and adds value: REPORTS which dirty packages are cooked-origin (a dirty cooked base-game map can never be saved — documented IMPOSSIBLE in docs/06).
**Verify**: dirty exactly one asset via set_variable_default → counts.content == 1 and the package path matches; after save_package it reports 0.
**Score**: U4 E1 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — FileHelpers.h:402/:409 verbatim, method-level `UNREALED_API static` on an unexported class (:183) as claimed. NOTE: `list_dirty_packages` is ALSO proposed by axis B — overlap flagged, not resolved here; the final catalogue must dedupe to one owner.

### save_dirty_packages
**Purpose**: One-call "save everything" (checkout-free, prompt-free) — today an agent must track and save each package individually (save_blueprint / save_package / save_level_as) and loses work when it forgets one.
**Engine API**:
```cpp
UNREALED_API static bool SaveDirtyPackages(const bool bPromptUserToSave, const bool bSaveMapPackages, const bool bSaveContentPackages, const bool bFastSave = false, const bool bNotifyNoPackagesSaved = false, const bool bCanBeDeclined = true, bool* bOutPackagesNeededSaving = NULL, const FShouldIgnorePackageFunctionRef& ShouldIgnorePackageFunction = FShouldIgnorePackage::Default);
```
Editor/UnrealEd/Public/FileHelpers.h:383. Call with bPromptUserToSave=false, bFastSave=true (bFastSave skips the source-control checkout flow — the "checkout-free save" the mission asked for), bCanBeDeclined=false, and a ShouldIgnorePackageFunction that filters cooked-origin packages.
Scripting-grade alternative in the same header: `static UNREALED_API bool SaveDirtyPackages(const bool bSaveMapPackages, const bool bSaveContentPackages);` FileHelpers.h:108 on `class UEditorLoadingAndSavingUtils : public UObject` (FileHelpers.h:39) — implementation verified prompt-free BUT it runs the source-control checkout flow (Editor/UnrealEd/Private/FileHelpers.cpp:5551-5555 → InternalCheckoutAndSavePackages(Packages, /*bUseDialog*/false)) and has no ignore-filter; the FEditorFileUtils overload with bFastSave=true is the checkout-free path the mission asked for.
**Export**: method-level `UNREALED_API static` (FileHelpers.h:383) | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: self-managed — saving is not undoable and must not sit inside the blanket transaction (a wrapping transaction would record package-dirty-flag state into undo, cf. FTransaction::FPackageRecord, Transactor.h:240-254).
**Async**: no for mod-scale package counts; if Phase-2 measures multi-second saves on large maps, promote to request/poll then. Synchronous is deliberate: the return payload IS the save proof.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| maps | saveMaps, save_maps | bool | true | no |
| content | saveContent, save_content | bool | true | no |
| dryRun | dry_run | bool | false | no — dryRun returns what WOULD be saved |
**Returns**: `{ saved: [...], failed: [{ package, reason }], skippedCookedOrigin: [...], neededSaving: bool }`.
**Failure modes**:
- Dirty cooked-origin package (edited base-game map): pre-filter via ShouldIgnorePackageFunction, report under skippedCookedOrigin with `reason: "cooked package — cannot be saved"` instead of failing the call.
- Read-only file on disk: failed[] entry `reason: "file is read-only: <path>"`.
- During PIE: `error: cannot save map packages during PIE — stop_pie first (or pass maps=false for a content-only save)`.
**Cooked**: degraded by design — cooked-origin packages are skipped and reported; loose agent-created packages save normally.
**Verify**: dirty 2 content packages (list_dirty_packages shows 2) → save_dirty_packages → saved[] length 2, failed empty → list_dirty_packages shows 0.
**Score**: U4 E2 R3 → tier 1 — prevents the "forgot one package before crash" failure class.
**Phase-2 verdict**: CORRECTED — signature citations (FileHelpers.h:383, :108) are verbatim-correct, but the implementation read (Editor/UnrealEd/Private/FileHelpers.cpp) found THREE hazards the entry missed:
1. **MODAL on failed save even with bFastSave=true**: InternalSavePackages' fast branch hardcodes `bUseDialog = true` (FileHelpers.cpp:3822-3828) and routes failures to InternalWarnUserAboutFailedSave → `FMessageDialog::Open(EAppMsgType::Ok, ...)` (FileHelpers.cpp:3620-3640). Any failed package pops a blocking modal mid-frame. Handler must NOT call FEditorFileUtils::SaveDirtyPackages directly; it must enumerate via GetDirtyWorldPackages/GetDirtyContentPackages and save per-package with a non-dialog path (or wrap the call in a `TGuardValue<bool> Guard(GIsRunningUnattendedScript, true)` — the engine's own no-dialog trick, cf. FileHelpers.cpp:5476).
2. **Silent skips contradict the claimed failure reporting**: the fast path pre-filters to packages that already exist on disk AND are writable (FileHelpers.cpp:3703-3746); read-only files and never-saved packages are skipped WITHOUT entering FailedPackages — so the promised `failed[{reason:"file is read-only"}]` entries never materialise from the engine call. The handler must pre-scan (DoesPackageExist + IsReadOnly) and build `skipped`/`failed` itself.
3. **Mid-frame GC**: InternalGetDirtyPackages runs `CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS)` whenever content packages are included (FileHelpers.cpp:3642-3647) — any unrooted UObject the bridge holds across this call dies. Same GC fires via the UEditorLoadingAndSavingUtils route.
Effort downgraded E2 → E3 (handler-side enumeration + pre-scan design now required). Bucket (self-managed) and cooked-content claims stand.

### list_actor_folders
**Purpose**: Enumerate the World Outliner folder tree (paths + per-folder actor counts) — agents that spawn many actors (spawn_many exists) currently dump everything at root with no way to see or use scene organisation.
**Engine API**:
```cpp
static UNREALED_API FActorFolders& Get();
UNREALED_API void ForEachFolder(UWorld& InWorld, TFunctionRef<bool(const FFolder&)> Operation);
UNREALED_API bool ContainsFolder(UWorld& InWorld, const FFolder& InFolder);
static UNREALED_API void GetActorsFromFolders(UWorld& InWorld, const TArray<FName>& InPaths, TArray<AActor*>& OutActors, const FFolder::FRootObject& InFolderRootObject = FFolder::GetInvalidRootObject());
```
Editor/UnrealEd/Public/EditorActorFolders.h:46, :149, :140, :102.
**Export**: method-level `UNREALED_API` (class FActorFolders itself unexported; all used members carry the macro) | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: read-only.
**Async**: no.
**Params**: | withCounts | with_counts | bool | true | no | (per-folder actor count via GetActorsFromFolders). Unrecognised ⇒ error.
**Returns**: `{ folders: [{ path, actorCount }], total }`.
**Failure modes**: no editor world ⇒ `error: no editor world`. Empty world ⇒ empty list (success).
**Cooked**: works — folders are editor-world metadata; on a cooked base-game map the folder set is whatever the cook preserved (often empty) — report what exists, never fail.
**Verify**: create folder via set_actor_folder (below) with 3 actors → list_actor_folders shows the path with actorCount == 3.
**Score**: U3 E1 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — EditorActorFolders.h:46/:102/:140/:149 re-read verbatim; FActorFolders is an unexported **struct** (:32), all used members method-level UNREALED_API as claimed. Also repaired: this entry's `### list_actor_folders` heading had been eaten by the save_dirty_packages verdict insertion — restored.

### set_actor_folder
**Purpose**: Move actors into an Outliner folder (creating it on demand) — organise agent-spawned content so a human can navigate the outliner afterwards; also the folder-scoped counterpart to select_level_actors.
**Engine API**:
```cpp
ENGINE_API void SetFolderPath(const FName& NewFolderPath);          // AActor
ENGINE_API void SetFolderPath_Recursively(const FName& NewFolderPath);
ENGINE_API FName GetFolderPath() const;
UNREALED_API bool CreateFolder(UWorld& InWorld, const FFolder& InFolder);   // FActorFolders
UNREALED_API void DeleteFolder(UWorld& InWorld, const FFolder& InFolderToDelete);
UNREALED_API bool RenameFolderInWorld(UWorld& InWorld, const FFolder& OldPath, const FFolder& NewPath);
```
Runtime/Engine/Classes/GameFramework/Actor.h:2542, :2548, :2517; Editor/UnrealEd/Public/EditorActorFolders.h:122, :131, :134.
**Export**: ENGINE_API on the AActor methods (method-level, verified at the cited lines); UNREALED_API on FActorFolders members | **Module**: none — Engine + UnrealEd both linked | **Guards**: the AActor folder/label API sits inside `#if WITH_EDITOR` (opens Actor.h:2353, closes :2579 — verified by grepping the guard boundaries). MifBridge is editor-only so this compiles as-is; no extra call-site guard needed. Same guard already covers SetActorLabel, which the existing set_actor_label endpoint calls — proven pattern.
**Bucket**: transacted — SetFolderPath modifies the actor; folder create/delete/rename broadcast events; standard blanket transaction gives one undo entry.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| actorPaths | actors, actor_paths | string[] | — | yes for op=move (strict: empty ⇒ `error: actorPaths is required`) |
| folder | folderPath, path | string | — | yes ("" allowed for op=move ⇒ move to root) |
| op | operation | string: move/create/delete/rename | move | no |
| newFolder | new_folder | string | — | required iff op=rename |
| recursive | — | bool | false | no (SetFolderPath_Recursively for attached children) |
**Failure modes**:
- Actor path not found: per-actor notFound[] (mirror select_level_actors behaviour), moved count in payload.
- op=delete on non-empty folder: folder delete moves actors to parent (engine behaviour) — report affectedActors count.
- op=rename with missing newFolder: `error: newFolder is required when op=rename`.
- Unknown op string: `error: op must be one of move|create|delete|rename`.
**Cooked**: works — folder assignment is editor-session state on loaded actors; saving it into a cooked base-game map is impossible (map unsaveable) — flag `unsaveable: true` in response when the world package is cooked-origin.
**Verify**: move 3 actors → list_actor_folders shows actorCount 3; get_property on one actor is not applicable (FolderPath is not a UPROPERTY — that is WHY this endpoint exists instead of set_property); re-query via list_actor_folders + GetFolderPath echo in response.
**Score**: U3 E2 R4 → tier 1.
**Phase-2 verdict**: CONFIRMED — Actor.h:2517/:2542/:2548 verbatim (ENGINE_API method-level); WITH_EDITOR guard boundaries re-verified (#if at Actor.h:2353, #endif at :2579); EditorActorFolders.h:122/:131/:134 verbatim.

### group_actors / ungroup_actors (pair)
**Purpose**: Create/disband AGroupActor groups so multi-part props an agent assembles (e.g. spawn_many furniture sets) move as one unit under the editor gizmo — grouping is currently unreachable.
**Engine API**:
```cpp
static UNREALED_API UActorGroupingUtils* Get();
UNREALED_API virtual AGroupActor* GroupActors(const TArray<AActor*>& ActorsToGroup);
UNREALED_API virtual void UngroupActors(const TArray<AActor*>& ActorsToUngroup);
static UNREALED_API void SetGroupingActive(bool bInGroupingActive);
static bool IsGroupingActive() { return bGroupingActive; }   // inline
```
Editor/UnrealEd/Public/ActorGroupingUtils.h:27, :39, :51, :21, :18. Class is `UCLASS(transient, MinimalAPI)` (:12-13) — method-level exports verified.
**Export**: method-level `UNREALED_API` | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: self-managed — GroupActors/UngroupActors open their OWN FScopedTransaction internally (verified in Editor/UnrealEd/Private/ActorGroupingUtils.cpp — GroupActors starts :51 with its transaction at :88 "Regroup Ctrl+G"; UngroupActors starts :157 with its transaction at :181 "Disband Group"). Wrapping them in the blanket transaction would nest (legal) but produce a mislabeled outer undo entry; run bare.
**Async**: no.
**Params** (group_actors): | actorPaths | actors | string[] | — | yes, >= 2 entries (`error: actorPaths needs at least 2 actors to group`) |; | name | label | string | auto | no (SetActorLabel on the AGroupActor afterwards) |.
**Params** (ungroup_actors): | actorPaths | actors | string[] | — | yes | — any actor in a group disbands that group.
**Failure modes**:
- Grouping disabled in editor prefs (IsGroupingActive false): auto-enable via SetGroupingActive(true), report `groupingWasEnabled: true` (do NOT silently fail — this is the documented "silent ignore" bug class).
- GroupActors returns nullptr (all actors invalid/locked): `error: grouping failed — no valid actors among N inputs`.
- Actor already in a group: engine regroups (removes from old) — report `regrouped: [...]`.
**Cooked**: works — AGroupActor is spawned into the editor world; same unsaveable-map caveat as set_actor_folder.
**Verify**: group 3 actors → response returns groupActorPath; list_level_actors shows one new AGroupActor; move the group via set_actor_transform on the group actor → get_actor_bounds of members shifted by the same delta (numeric). Ungroup → AGroupActor gone from list_level_actors.
**Score**: U3 E2 R4 → tier 2 (valuable, needs the enable-grouping design note).
**Phase-2 verdict**: CONFIRMED — ActorGroupingUtils.h:12-13/:18/:21/:27/:39/:51 verbatim; impl re-read: transactions at ActorGroupingUtils.cpp:88 ("Regroup Ctrl+G") and :181 ("Disband Group") as cited; no FMessageDialog anywhere in the .cpp (grep 0) — the cross-level failure is a non-modal notification toast + nullptr return (cpp:124-129). Two failure-mode additions the proposer under-specified: (a) actors spanning multiple levels ⇒ nullptr (cpp:58-68, :124-129) — surface as `error: actors are in different levels`; (b) group-actor inputs are filtered out (cpp:71-76), so >= 2 NON-group actors must survive filtering or GroupActors returns nullptr (cpp:81).

### list_layers
**Purpose**: Enumerate editor layers with actor counts + visibility — the read half of layer management (layers gate visibility for capture_camera comparisons and bulk-hide workflows).
**Engine API**:
```cpp
UNREALED_API virtual void AddAllLayerNamesTo(TArray< FName >& OutLayerNames) const final;
UNREALED_API ULayer* GetLayer(const FName& LayerName) const;
UNREALED_API TArray< AActor* > GetActorsFromLayer(const FName& LayerName) const;
```
Editor/UnrealEd/Public/Layers/LayersSubsystem.h:551, :526, :455. Class `ULayersSubsystem : public UEditorSubsystem` (:44) is MinimalAPI-style (no class macro) with method-level UNREALED_API — verified per line. Obtain via `GEditor->GetEditorSubsystem<ULayersSubsystem>()`.
**Export**: method-level `UNREALED_API` | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: read-only.
**Async**: no.
**Params**: | withActors | with_actors | bool | false | no |. Unrecognised ⇒ error.
**Returns**: `{ layers: [{ name, actorCount, visible, actors?: [...] }] }` (visible via ULayer properties — read through get_property route on the ULayer object if needed; actorCount from GetActorsFromLayer().Num()).
**Failure modes**: none; empty layer list is success.
**Cooked**: works — layers live in the editor world's ULayersSubsystem state.
**Verify**: modify_actor_layers adds 2 actors to layer "AgentWork" → list_layers shows actorCount == 2.
**Score**: U2 E1 R5 → tier 2.
**Phase-2 verdict**: CONFIRMED — LayersSubsystem.h:455/:526/:551 verbatim; class is explicitly `UCLASS(MinimalAPI)` (:43-44) with method-level UNREALED_API, matching the export claim.

### modify_actor_layers
**Purpose**: Add/remove actors to/from named layers and toggle layer visibility — bulk show/hide of agent work areas without touching per-actor bHidden (which set_property would fight with the layer system over).
**Engine API**:
```cpp
UNREALED_API bool AddActorsToLayer(const TArray< AActor* >& Actors, const FName& LayerName);
UNREALED_API bool RemoveActorsFromLayer(const TArray< AActor* >& Actors, const FName& LayerName, const bool bUpdateStats = true);
UNREALED_API ULayer* CreateLayer(const FName& LayerName);
UNREALED_API virtual void DeleteLayer(const FName& LayerToDelete) final;
UNREALED_API virtual void SetLayerVisibility(const FName& LayerName, const bool bIsVisible) final;
UNREALED_API bool IsLayer(const FName& LayerName);
```
Editor/UnrealEd/Public/Layers/LayersSubsystem.h:167, :210, :573, :588, :488, :534.
**Export**: method-level `UNREALED_API` | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: transacted — AddActorsToLayer modifies actors' Layers arrays and layer objects; one blanket transaction = one undo entry. (Do NOT reach for set_property on AActor::Layers — it bypasses ULayersSubsystem stats/visibility bookkeeping; that side-effect handling is exactly why this is a dedicated endpoint.)
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| layer | layerName, layer_name | string | — | yes (strict) |
| op | operation | string: add/remove/create/delete/set_visibility | add | no |
| actorPaths | actors | string[] | — | required for add/remove |
| visible | — | bool | — | required for set_visibility |
op=add auto-creates the layer if missing (CreateLayer) and reports `created: true`.
**Failure modes**:
- op=remove on non-member actors: report perActor results, not silent (removed / notInLayer / notFound arrays).
- op=delete on missing layer: `error: layer 'X' does not exist (IsLayer returned false)`.
- visible param present but op != set_visibility: `error: 'visible' only valid with op=set_visibility` (anti-silent-ignore).
**Cooked**: works; layer membership on cooked-map actors is session-only (map unsaveable) — same `unsaveable` flag as set_actor_folder.
**Verify**: add 2 actors → list_layers actorCount 2; set_visibility false → actors' components' IsVisible() false via get_property on SceneComponent bVisible / scene_report; remove 1 → actorCount 1.
**Score**: U2 E2 R4 → tier 2.
**Phase-2 verdict**: CONFIRMED — LayersSubsystem.h:167/:210/:488/:534/:573/:588 all verbatim; LayersSubsystem.cpp greps clean of FMessageDialog/EAppMsgType (no modal hazard).

### run_editor_utility
**Purpose**: Execute an Editor Utility Blueprint's Run event by asset path — an escape hatch that lets an agent (or the user) package arbitrary editor logic as an EUB once and invoke it through the bridge without a new endpoint per task.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "Development|Editor")
bool TryRun(UObject* Asset);
UFUNCTION(BlueprintCallable, Category = "Development|Editor")
bool CanRun(UObject* Asset) const;
```
Editor/Blutility/Public/EditorUtilitySubsystem.h:67, :70. Class `class BLUTILITY_API UEditorUtilitySubsystem : public UEditorSubsystem` (:39) — CLASS-level export, all members linkable. Obtain via `GEditor->GetEditorSubsystem<UEditorUtilitySubsystem>()`.
Implementation read (Editor/Blutility/Private/EditorUtilitySubsystem.cpp:133-176): TryRun instantiates the generated class and calls its `Run` UFunction via ProcessEvent under FEditorScriptExecutionGuard.
**Export**: BLUTILITY_API (class-level) | **Module**: **Blutility — NEW dependency** (editor-only module in Engine/Source/Editor/Blutility; no plugin, always present in editor builds) | **Guards**: none
**Bucket**: self-managed — the utility graph can do ANYTHING (spawn, compile, delete); wrapping unknown user logic in the blanket transaction invites the reinstancing-inside-transaction crash the contract bans. Handler runs TryRun bare; the EUB is responsible for its own transactions.
**Async**: no for the call itself (TryRun is synchronous ProcessEvent). Long-running EUBs will hitch the editor — document "keep Run() fast; use UEditorUtilityTask for long work" and cap nothing (agent's own asset).
**Params**: | assetPath | asset, path | string | — | yes (strict) |. Unrecognised ⇒ error.
**Returns**: `{ ran: bool, assetPath, class }` — plus `canRun` precheck result when ran=false.
**Failure modes**:
- Asset not found / not loadable: `error: assetPath 'X' could not be loaded`.
- No Run function: TryRun returns false (engine logs "Missing function named 'Run'") ⇒ `error: class has no 'Run' event — add a Run custom event to the Editor Utility Blueprint`.
- Actor-derived class: engine refuses (EditorUtilitySubsystem.cpp:153-157) ⇒ surface that exact reason.
**Cooked**: DEGRADED — TryRun's UBlueprint cast path (cpp:142) needs the UBlueprint asset; cooked pak content ships only UBlueprintGeneratedClass, and FindFunctionByName on the *class of* a UBlueprintGeneratedClass asset finds nothing. Works for LOOSE EUBs (agent-created via create_blueprint with EditorUtilityObject parent, or user-authored in the modkit project). Say so in server.py docstring.
**Verify**: create a minimal EUB whose Run sets a well-known CVar (e.g. `mif.EUBRan 1`) → run_editor_utility → get_cvar (below) returns 1. Numeric, closed loop.
**Score**: U4 E2 R3 → tier 1 — one endpoint that manufactures new capabilities without recompiling the bridge.
**Phase-2 verdict**: CONFIRMED — EditorUtilitySubsystem.h:39 (`class BLUTILITY_API`), :67/:70 verbatim; impl re-read: TryRun spans cpp:133-176, UBlueprint cast :142-145, Actor-class refusal :153-157 ("functions on actors can only be called when spawned in a world"), sync ProcessEvent under FEditorScriptExecutionGuard :166-167 — all exactly as cited. Blutility.Build.cs exists under Engine/Source/Editor/Blutility (editor-only, no plugin) — NEW-dependency claim stands. Note: instances are retained in the subsystem's ObjectInstances map (cpp:163-164), so no GC hazard on the bridge side.
### get_cvar
**Purpose**: Structured READ of a console variable — value, type, flags, help — with an error when it doesn't exist. run_console can only SET; run_console_captured returns unstructured log text an agent must regex. This is the numeric getter the house rule demands.
**Engine API**:
```cpp
virtual IConsoleVariable* FindConsoleVariable(const TCHAR* Name, bool bTrackFrequentCalls = true) const = 0;
virtual IConsoleObject* FindConsoleObject(const TCHAR* Name, bool bTrackFrequentCalls = true) const = 0;
// IConsoleVariable / IConsoleObject accessors (all pure virtual → vtable dispatch, no export needed):
virtual bool GetBool() const = 0;            // :456
virtual int32 GetInt() const = 0;            // :461
virtual float GetFloat() const = 0;          // :463
virtual FString GetString() const = 0;       // :465
virtual const TCHAR* GetHelp() const = 0;    // :346
virtual EConsoleVariableFlags GetFlags() const = 0;  // :354
virtual bool IsVariableBool() const { return false; }  // :387 (+Int/Float/String :388-:390)
FORCEINLINE static IConsoleManager& Get()    // :1026 — inline, reads CORE_API Singleton (:1057)
```
Runtime/Core/Public/HAL/IConsoleManager.h:926, :933, and lines as annotated.
**Export**: no export needed — IConsoleManager::Get() is FORCEINLINE over `static CORE_API IConsoleManager* Singleton` (:1057); all accessors are pure-virtual interface calls. Core is linked. This is the exception that proves the export rule: virtual dispatch requires no symbol.
**Module**: none — Core | **Guards**: none
**Bucket**: read-only.
**Async**: no.
**Params**: | name | cvar, key | string | — | yes (strict) |. Unrecognised ⇒ error.
**Returns**: `{ name, found: true, kind: "variable"|"command", type: bool|int|float|string, value, valueString, flags: ["ECVF_Cheat", "ECVF_SetByConsole", ...], setBy, help }`.
**Failure modes**: unknown name ⇒ `error: console object 'X' not found (names are case-insensitive; try list_cvars with a prefix)`. Name resolves to a command ⇒ kind:"command", no value fields (NOT an error — agents probe).
**Cooked**: works — console state is process state.
**Verify**: run_console `r.VSync 1` → get_cvar r.VSync returns value 1, setBy "SetByConsole"; run_console `r.VSync 0` → value 0.
**Score**: U4 E1 R5 → tier 1 — closes the "SET without GET" asymmetry named in the mission.
**Phase-2 verdict**: CONFIRMED — IConsoleManager.h:346/:354/:387-390/:456/:461/:463/:465/:926/:933/:1026/:1057 all re-read verbatim; `static CORE_API IConsoleManager* Singleton` (:1057) grounds the no-export-needed argument exactly as stated.

### list_cvars
**Purpose**: Enumerate console variables/commands by prefix or substring with values+flags — discoverability for the thousands of engine knobs run_console can already set blind.
**Engine API**:
```cpp
virtual void ForEachConsoleObjectThatStartsWith( const FConsoleObjectVisitor& Visitor, const TCHAR* ThatStartsWith = TEXT("")) const = 0;
virtual void ForEachConsoleObjectThatContains(const FConsoleObjectVisitor& Visitor, const TCHAR* ThatContains) const = 0;
```
Runtime/Core/Public/HAL/IConsoleManager.h:984, :991.
**Export**: pure virtual via exported singleton — see get_cvar | **Module**: none — Core | **Guards**: none
**Bucket**: read-only.
**Async**: no — but REQUIRE a non-empty filter of >= 2 chars (a bare "" walk of ~15k objects building JSON mid-frame is a self-inflicted hitch): `error: prefix (or contains) of at least 2 characters is required`.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| prefix | startsWith, starts_with | string | — | one of prefix/contains required |
| contains | substring | string | — | — |
| limit | max | int | 200 | no |
| kind | type | string: all/variables/commands | variables | no |
| withHelp | with_help | bool | false | no |
**Returns**: `{ count, truncated: bool, items: [{ name, kind, type?, valueString?, setBy?, help? }] }`.
**Failure modes**: both prefix and contains ⇒ `error: pass prefix or contains, not both`. Zero matches ⇒ count 0 (success).
**Cooked**: works.
**Verify**: list_cvars prefix "r.DLSS" (project has DLSS plugin) → count > 0 and every returned name starts with "r.DLSS" — count must equal the number reachable via `run_console_captured "help r.DLSS"`-style spot checks; get_cvar on any listed name succeeds.
**Score**: U3 E2 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — IConsoleManager.h:984/:991 verbatim; the >= 2-char filter requirement correctly pre-empts the full-registry-walk hitch.

### list_developer_settings
**Purpose**: Enumerate every UDeveloperSettings subclass (class path, config file, container/category/section) — the structured index that makes the EXISTING set_property-on-CDO route usable for editor/project preferences without guessing class names.
**Engine API**:
```cpp
COREUOBJECT_API void GetDerivedClasses(const UClass* ClassToLookFor, TArray<UClass *>& Results, bool bRecursive = true);
DEVELOPERSETTINGS_API virtual FName GetContainerName() const;
DEVELOPERSETTINGS_API virtual FName GetCategoryName() const;
DEVELOPERSETTINGS_API virtual FName GetSectionName() const;
DEVELOPERSETTINGS_API virtual FText GetSectionText() const;      // inside #if WITH_EDITOR (DeveloperSettings.h:37)
COREUOBJECT_API const FString GetConfigName() const;             // UClass
```
Runtime/CoreUObject/Public/UObject/UObjectHash.h:209; Runtime/DeveloperSettings/Public/Engine/DeveloperSettings.h:31, :33, :35, :39 (class is `UCLASS(Abstract, MinimalAPI)` :22-23, method-level exports); Runtime/CoreUObject/Public/UObject/Class.h:3201 (plus public field `FName ClassConfigName;` :2785).
**Export**: method-level DEVELOPERSETTINGS_API / COREUOBJECT_API as cited | **Module**: **DeveloperSettings — NEW dependency** (runtime module, Engine/Source/Runtime/DeveloperSettings; tiny, no plugin). CoreUObject already linked. | **Guards**: GetSectionText/GetSectionDescription need WITH_EDITOR — MifBridge is editor-only, fine.
**Bucket**: read-only.
**Async**: no (one GetDerivedClasses walk + per-class virtual calls on CDOs; ~200 classes, trivial).
**Params**: | filter | contains | string | — | no (substring on class name) |; | category | — | string | — | no |. Unrecognised ⇒ error.
**Returns**: `{ count, settings: [{ classPath, objectPath (the "Default__…" CDO path ready to paste into set_property/get_property/list_object_properties), configFile, container, category, section, displayName }] }`.
**Failure modes**: abstract classes and CDO-less skeletons are skipped silently is NOT allowed — report `skipped: n` with reason "abstract". Filter matching nothing ⇒ count 0.
**Cooked**: works — class registry is in-process, not pak content.
**Verify**: response must contain Engine's own URendererSettings (verified subclass: `class URendererSettings : public UDeveloperSettings`, Runtime/Engine/Classes/Engine/RendererSettings.h:288) and the count is stable across two calls; pick one entry, get_property on its objectPath succeeds (round-trip proof). NOTE: editor-preference classes like ULevelEditorViewportSettings derive from plain UObject, NOT UDeveloperSettings (`class ULevelEditorViewportSettings : public UObject`, Editor/UnrealEd/Classes/Settings/LevelEditorViewportSettings.h:266-267) — the endpoint should therefore ALSO enumerate `GetDerivedClasses(UObject)` filtered to classes with a non-NAME_None ClassConfigName under a `scope: "config-objects"` option, or agents will wrongly conclude viewport prefs don't exist. Document both scopes.
**Score**: U4 E1 R5 → tier 1 — multiplies the value of the existing set_property CDO route (grid snapping, autosave, viewport prefs all become addressable).
**Phase-2 verdict**: CONFIRMED — all five headers re-read: UObjectHash.h:209 verbatim; DeveloperSettings.h `UCLASS(Abstract, MinimalAPI)` :22-23, DEVELOPERSETTINGS_API method-level :31/:33/:35, WITH_EDITOR block opens :37 with GetSectionText at :39; Class.h:2785 (`FName ClassConfigName;`) and :3201 verbatim; URendererSettings : UDeveloperSettings at RendererSettings.h:288; ULevelEditorViewportSettings : UObject at LevelEditorViewportSettings.h:265-267 — the config-objects-scope caveat is well-founded.

### get_viewport_state
**Purpose**: Read the active level viewport's render configuration — view mode, game view, realtime, resolution, and show-flag diffs — so capture_camera comparisons are reproducible (an agent that can't see the screen must KNOW it captured wireframe vs lit).
**Engine API**:
```cpp
UNREALED_API EViewModeIndex GetViewMode() const;
virtual bool IsInGameView() const override { return bInGameViewMode; }   // inline
bool IsRealtime() const                                                    // inline (:395)
UNREALED_API void GetViewportDimensions( FIntPoint& OutOrigin, FIntPoint& OutSize );
ENGINE_API bool GetSingleFlag(uint32 Index) const;                          // FEngineShowFlags
ENGINE_API static FString FindNameByIndex(uint32 InIndex);
template <class T> static void IterateAllFlags(T& Sink)                     // inline (:337)
```
Editor/UnrealEd/Public/EditorViewportClient.h:926, :1195, :395, :964; Runtime/Engine/Public/ShowFlags.h:281, :327, :337. Viewport client obtained exactly as the shipped viewport endpoints do: `GEditor->GetLevelViewportClients()` (already used at MifBridge/Private/MifBridgeViewport.cpp:36 — proven link path). `FEngineShowFlags EngineShowFlags` is a public member of FEditorViewportClient.
**Export**: method-level UNREALED_API / ENGINE_API as cited; inlines need nothing | **Module**: none — UnrealEd + Engine linked | **Guards**: none
**Bucket**: read-only.
**Async**: no.
**Params**: | viewportIndex | viewport_index, index | int | active/first perspective (same resolution rule as get_viewport_camera) | no |; | showFlags | show_flags | string: none/diff/all | diff | no — diff returns only flags differing from FEngineShowFlags(ESFIM_Editor) defaults.
**Returns**: `{ viewMode: "Lit"(enum name), viewModeIndex, gameView, realtime, realtimeOverride, size: {x,y}, showFlagsDiff: [{ name, value }] }`.
**Failure modes**: no level viewport clients (headless) ⇒ `error: no level viewport available`. viewportIndex out of range ⇒ error naming the valid range.
**Cooked**: works.
**Verify**: set_view_mode wireframe → get_viewport_state.viewModeIndex == VMI_Wireframe (2); toggle back; size matches capture_camera output dimensions.
**Score**: U3 E2 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — EditorViewportClient.h:395/:926/:964/:1195 and ShowFlags.h:281/:327/:337 all verbatim; the proven link path at MifBridgeViewport.cpp:36 (`GEditor->GetLevelViewportClients()`) re-verified in the plugin source.

### set_view_mode
**Purpose**: Switch the level viewport between lit/unlit/wireframe/detail-lighting etc., toggle game view, realtime, and individual show flags — turns capture_camera into a diagnostic instrument (wireframe screenshots localize geometry bugs; unlit isolates lighting from texture issues).
**Engine API**:
```cpp
UNREALED_API virtual void SetViewMode(EViewModeIndex InViewModeIndex);
UNREALED_API void SetGameView(bool bGameViewEnable);
UNREALED_API void SetRealtime(bool bInRealtime);
ENGINE_API void SetSingleFlag(uint32 Index, bool bSet);          // FEngineShowFlags
ENGINE_API static int32 FindIndexByName(const TCHAR* Name, const TCHAR *CommaSeparatedNames = 0);
```
Editor/UnrealEd/Public/EditorViewportClient.h:910, :1189, :392; Runtime/Engine/Public/ShowFlags.h:275, :311.
**Export**: method-level UNREALED_API / ENGINE_API, verified | **Module**: none | **Guards**: none
**Bucket**: self-managed (no transaction) — viewport client state is not transactional; the blanket transaction would push an empty undo entry, exactly what bucket rules exist to prevent.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| viewMode | view_mode, mode | string: lit/unlit/wireframe/detail_lighting/lighting_only/light_complexity/shader_complexity/lightmap_density/reflections/collision_pawn/collision_visibility | — | no |
| gameView | game_view | bool | — | no |
| realtime | — | bool | — | no |
| showFlags | show_flags | object {flagName: bool} | — | no (each name resolved via FindIndexByName; unknown name ⇒ error naming it) |
| viewportIndex | index | int | active | no |
At least one of viewMode/gameView/realtime/showFlags required ⇒ else `error: nothing to do — pass viewMode, gameView, realtime and/or showFlags`.
**Returns**: the full get_viewport_state payload AFTER the change (mutation ships its own verification read).
**Failure modes**: unknown viewMode string ⇒ error listing accepted values; unknown show flag ⇒ `error: unknown show flag 'X' (FindIndexByName returned -1)`; no viewport ⇒ as above.
**Cooked**: works.
**Verify**: set wireframe → returned viewModeIndex == 2 AND a capture_camera image byte-size drops dramatically vs lit (numeric proxy); set showFlags {"Fog": false} → get_viewport_state diff contains Fog=false.
**Score**: U4 E2 R4 → tier 1 — pairs with capture_camera for the "pixels for taste" half of the house rule.
**Phase-2 verdict**: CONFIRMED — EditorViewportClient.h:392/:910/:1189 and ShowFlags.h:275/:311 verbatim; bucket call (self-managed, non-transactional viewport state) matches the contract.

### get_editor_modes
**Purpose**: Report which editor modes are active (Default, Landscape, Foliage, Modeling…) — lets an agent detect "user left landscape mode open" states that make sculpt/paint endpoints behave differently, before mutating.
**Engine API**:
```cpp
UNREALED_API class FEditorModeTools& GLevelEditorModeTools();
UNREALED_API bool GLevelEditorModeToolsIsValid();
UNREALED_API bool IsModeActive( FEditorModeID InID ) const;        // FEditorModeTools
UNREALED_API void ForEachEdMode(TFunctionRef<bool(UEdMode*)> InCalllback) const;
FEditorModeID GetID() const { return Info.ID; }                     // UEdMode, inline
```
Editor/UnrealEd/Public/Editor.h:686, :691; Editor/UnrealEd/Public/EditorModeManager.h:141, :547 (class FEditorModeTools, :36 — plain class, method-level exports); Editor/UnrealEd/Public/Tools/UEdMode.h:191.
**Export**: method-level UNREALED_API as cited | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: read-only.
**Async**: no.
**Params**: none ⇒ any parameter is an error.
**Returns**: `{ modes: [{ id, name }], isDefaultActive }` (ForEachEdMode only surfaces UEdMode-based modes; legacy FEdMode actives come via IsModeActive checks against the well-known IDs — list both, mark `legacy: true`).
**Failure modes**: GLevelEditorModeToolsIsValid() false (shutdown/startup race) ⇒ `error: level editor mode manager not available`.
**Cooked**: works.
**Verify**: activate landscape mode by hand (or via ActivateMode in a future set endpoint) → get_editor_modes contains "EM_Landscape"; deactivate → only Default remains. Count is exact.
**Score**: U2 E1 R5 → tier 2 — cheap insurance for the landscape/foliage endpoints. (A set_editor_mode WRITE twin via ActivateMode/DeactivateMode (EditorModeManager.h:84/:91) is viable and exported, but activating modes spawns toolkits/UI mid-frame from an HTTP handler — defer to Phase-2 with a deferred-tick design; recorded under UNVERIFIED as design-incomplete rather than proposed.)
**Phase-2 verdict**: CONFIRMED — Editor.h:686/:691, EditorModeManager.h:36/:84/:91/:141/:547 (the `InCalllback` typo is in the engine header itself — verbatim match), UEdMode.h:191 all re-read; the no-GetActiveModes()-in-5.3.2 workaround claim re-verified (grep 0 hits in EditorModeManager.h).

### select_components
**Purpose**: Component-level selection (the one selection layer select_level_actors cannot touch) — selecting a component focuses the details panel and drives per-component gizmos for human handoff mid-task.
**Engine API**:
```cpp
UNREALED_API virtual void SelectComponent(class UActorComponent* Component, bool bInSelected, bool bNotify, bool bSelectEvenIfHidden = false) override;   // UUnrealEdEngine
UNREALED_API class USelection* GetSelectedComponents() const;   // UEditorEngine
UNREALED_API int32 GetSelectedComponentCount() const;
UNREALED_API int32 Num() const;                                  // USelection
UNREALED_API UObject* GetSelectedObject(const int32 InIndex) const;
```
Editor/UnrealEd/Classes/Editor/UnrealEdEngine.h:177; Editor/UnrealEd/Classes/Editor/EditorEngine.h:1890, :1885; Editor/UnrealEd/Public/Selection.h:86, :91 (USelection is `class USelection : public UObject` :39, method-level UNREALED_API; the Runtime/Engine/Classes/Engine/Selection.h path is a 9-line WITH_EDITOR adapter that includes the UnrealEd header — cite the real one).
Call through `GEditor->SelectComponent(...)` — virtual dispatch lands in UUnrealEdEngine; the base declaration (EditorEngine.h:1446) is an empty inline, so even the unexported base costs nothing.
**Export**: UNREALED_API method-level on the override and getters, verified | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: transacted — selection sets are transactional (USelection::Modify override, Selection.h:287); matches whatever bucket select_level_actors uses today.
**Async**: no.
**Params**: | componentPaths | components | string[] | — | no |; | clear | — | bool | false | no |; | ownerActor | owner | string | — | no (with clear, scopes deselect to that actor's components) |. No params at all ⇒ returns current component selection (mirror of select_level_actors read behaviour).
**Returns**: `{ selected, notFound: [...], selection: [componentPaths] }` via GetSelectedComponents walk.
**Failure modes**: path resolves to non-component ⇒ per-item error entry `notComponent`; component's owner actor not in editor world (PIE object) ⇒ notFound with reason.
**Cooked**: works.
**Verify**: select 2 components → GetSelectedComponentCount()==2 echoed in payload; clear → 0.
**Score**: U2 E2 R4 → tier 2.
**Phase-2 verdict**: CONFIRMED — UnrealEdEngine.h:177, EditorEngine.h:1885/:1890, base inline stub at EditorEngine.h:1446, and Selection.h:38-39/:86/:91/:287 all verbatim; the Runtime/Engine/Classes/Engine/Selection.h adapter re-read (8-line WITH_EDITOR include shim — proposer said "9-line", immaterial). Virtual-dispatch-through-GEditor route sound.

### close_asset_editors
**Purpose**: Close open asset-editor tabs for an asset (or all) — open editors hold references that make delete_asset/rename_asset fail or prompt; agents need the headless "close it first" step.
**Engine API**:
```cpp
UNREALED_API int32 CloseAllEditorsForAsset(UObject* Asset);
UNREALED_API TArray<UObject*> GetAllEditedAssets();
UNREALED_API IAssetEditorInstance* FindEditorForAsset(UObject* Asset, bool bFocusIfOpen);
```
Editor/UnrealEd/Public/Subsystems/AssetEditorSubsystem.h:148, :161, :138. Class `UAssetEditorSubsystem : public UEditorSubsystem` (:93) — MinimalAPI-style, method-level UNREALED_API verified. Via `GEditor->GetEditorSubsystem<UAssetEditorSubsystem>()`.
**Export**: method-level UNREALED_API | **Module**: none — UnrealEd | **Guards**: none
**Bucket**: self-managed (no transaction) — closing UI toolkits is not undoable state.
**Async**: no — CloseAllEditorsForAsset requests close synchronously; Slate tab teardown completes next tick, so the response reports `closedRequested: n` and callers re-query with assetPath absent (list mode) to confirm 0.
**Params**: | assetPath | asset | string | — | no — absent ⇒ list currently edited assets only |; | all | — | bool | false | no — all=true closes every editor (GetAllEditedAssets loop) |.
**Returns**: `{ openEditors: [assetPaths], closedRequested: n }`.
**Failure modes**: assetPath given but asset not loaded ⇒ `error: asset 'X' is not loaded (nothing can be open for it)`; assetPath and all both set ⇒ error.
**Cooked**: works — closing editors is independent of asset origin (cooked assets CAN be open read-only in editors).
**Verify**: open_blueprint on a BP → close_asset_editors (list mode) shows 1 entry; close it → next list call shows 0.
**Score**: U2 E1 R4 → tier 2 — removes a known delete/rename failure mode.
**Phase-2 verdict**: CONFIRMED — AssetEditorSubsystem.h:93 (UCLASS(MinimalAPI) at :92)/:138/:148/:161 verbatim; impl re-read (AssetEditorSubsystem.cpp:302-314): CloseWindow request + close-event broadcast, zero FMessageDialog/FSlowTask/Wait hits in the whole .cpp — no modal or blocking hazard; the "teardown completes next tick" async note is accurate.

### pilot_actor
**Purpose**: Pilot/eject the viewport camera onto an actor — locks the editor camera to any actor so capture_camera can shoot from a moving NPC/camera actor's exact POV (composes with the walking-NPC Tier-0 gap work in the PIE axis).
**Engine API**:
```cpp
void PilotLevelActor(AActor* ActorToPilot, FName ViewportConfigKey = NAME_None);
void EjectPilotLevelActor(FName ViewportConfigKey = NAME_None);
AActor* GetPilotLevelActor(FName ViewportConfigKey = NAME_None);
```
Editor/LevelEditor/Public/LevelEditorSubsystem.h:47, :50, :53 — all `UFUNCTION(BlueprintCallable, meta=(DevelopmentOnly))`. Class `class LEVELEDITOR_API ULevelEditorSubsystem : public UEditorSubsystem` (LevelEditorSubsystem.h:36) — CLASS-level export, no per-method macro needed.
**Export**: LEVELEDITOR_API (class-level) | **Module**: **LevelEditor — NEW dependency** (editor-only module, Engine/Source/Editor/LevelEditor; no plugin) | **Guards**: none
**Bucket**: self-managed — camera piloting is viewport state, not transactional.
**Async**: no.
**Params**: | actorPath | actor | string | — | yes unless eject/query |; | op | — | string: pilot/eject/get | pilot | no |.
**Failure modes**: actor not found ⇒ strict error; already piloting another actor ⇒ auto-eject then pilot, report `previous: path`; op=eject when not piloting ⇒ `piloting: null` success (idempotent).
**Cooked**: works.
**Verify**: pilot actor at known transform → get_viewport_camera location equals actor location within 1uu; eject → get(op=get) returns null and camera stops tracking.
**Score**: U2 E2 R4 → tier 2.
**Phase-2 verdict**: CORRECTED — citations verbatim (LevelEditorSubsystem.h:36 class-level LEVELEDITOR_API, :47/:50/:53; LevelEditor.Build.cs present, editor-only, no plugin), but the impl read (Editor/LevelEditor/Private/LevelEditorSubsystem.cpp:160-173) exposes a missed failure mode: PilotLevelActor **silently no-ops when no level viewport resolves** (`if (LevelViewport.IsValid())` guard, no return value, no log) — the contract's #1 bug class. Handler must pre-check viewport availability and return `error: no level viewport available to pilot` instead of trusting the call. Also: GetPilotLevelActor prefers the cinematic actor lock over the pilot lock (cpp:208-212) — op=get should report which lock kind it returned. Effort stays E2; failure-mode table amended per above.

## Compositions (no new endpoint needed)

- **Get current actor selection**: `select_level_actors {}` — the shipped handler (MifBridgeLevel.cpp:412-459) already returns the `selection` array when called with no actorPaths; no mutation occurs. A dedicated get_selection would be a duplicate.
- **Deselect all actors**: `select_level_actors { clear: true }` — same handler, :419-422.
- **Select-by-query** (class/name/tag): `list_level_actors` with its filters → feed the returned paths to `select_level_actors`. Two calls, fully structured; a dedicated query-selector adds nothing.
- **Editor snapping settings** (grid/rotation/scale snap toggles and sizes): `set_property` / `get_property` on the CDO objectPath of ULevelEditorViewportSettings (`class ULevelEditorViewportSettings : public UObject`, UCLASS(config=EditorPerProjectUserSettings, MinimalAPI) — Editor/UnrealEd/Classes/Settings/LevelEditorViewportSettings.h:265-267; relevant UPROPERTYs verified: `bUsePowerOf2SnapSize` :391, `GridEnabled` :423, `RotGridEnabled` :427, `SnapScaleEnabled` :431). These are plain UPROPERTYs — the existing dot-path route applies. list_developer_settings' config-objects scope makes the class discoverable.
- **Editor preference WRITES generally**: set_property on `Default__<SettingsClass>` — list_developer_settings (proposed) is the missing index, not a new writer.
- **Snap actor to floor**: exists (`snap_actors_to_ground`).
- **Find Editor Utility Blueprints to run**: `find_assets` filtered by class EditorUtilityBlueprint → run_editor_utility. No list_editor_utilities endpoint needed.
- **Console command execution & CVar SET**: `run_console` / `run_console_captured` (per contract, never wrapped again); get_cvar/list_cvars are strictly the structured READ side.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

_Phase-2 (2026-07-26): all 10 negatives spot-verified against source; none overturned. Key re-reads: Transactor.h:635/:642 (no export macro — #2), EditorEngine.h:1443-1464 stubs vs UnrealEdEngine.h:174-179 overrides (#3), LightEditorSubsystem.h:40-41 UCLASS() in a Private header (#4), FoliageEditorSubsystem.h grep 0 export/UFUNCTION hits (#5), no GetActiveModes in EditorModeManager.h + GetActiveMode at :147 (#6), `class FEditorFileUtils` bare at FileHelpers.h:183 (#7), LevelEditorViewportSettings.h:265-267 (#8), EditorUtilitySubsystem.cpp:142-145 (#9), FileHelpers.cpp:5551-5554 → InternalCheckoutAndSavePackages(Packages, false) (#10)._

1. **UTransBuffer is UCLASS(transient, MinimalAPI)** (Editor/UnrealEd/Classes/Editor/TransBuffer.h:14-16) and its own methods (RedoUndo internals, `Undo/Redo` overrides) carry NO method-level export. NOT a blocker: (a) all introspection goes through the exported UTransactor virtuals (Transactor.h:503-626, each UNREALED_API), (b) `UndoBuffer`/`UndoCount` are public FIELDS (TransBuffer.h:22, :25 — field reads need no export), (c) undo/redo execution goes through `UEditorEngine::UndoTransaction/RedoTransaction` (EditorEngine.h:934-935, UNREALED_API). This is the exact export answer the mission asked for on the transaction target.
2. **`UTransactor::Undo(bool)` / `Redo()` have no export macro** (Transactor.h:635, :642) — callable only via vtable; fine in practice but do NOT reference them non-virtually.
3. **UEditorEngine::SelectActor/SelectNone/SelectComponent are empty inline stubs on the base class** (EditorEngine.h:1443-1464) — the real implementations are the UNREALED_API overrides on UUnrealEdEngine (UnrealEdEngine.h:174-179). Call through `GEditor` (virtual dispatch); calling the base non-virtually silently does nothing — a trap worth documenting for implementers.
4. **ULightEditorSubsystem is unusable**: declared in a PRIVATE header (Editor/LevelEditor/Private/LightEditorSubsystem.h:41), no export macro — cannot be included or linked from a plugin. Light workflows stay on set_property.
5. **UFoliageEditorSubsystem is unusable directly**: no export macro and zero UFUNCTION/UNREALED_API members in the header (Editor/FoliageEdit/Public/FoliageEditorSubsystem.h — grep count 0). Foliage remains covered by the existing add_foliage_instances.
6. **No "list active editor modes" enumeration for legacy FEdModes in 5.3.2**: FEditorModeTools offers `GetActiveMode(FEditorModeID)` (EditorModeManager.h:147) and `ForEachEdMode` (:547 — UEdMode-based modes only). Enumerating legacy actives requires probing well-known IDs; there is no GetActiveModes() in this engine version. get_editor_modes above documents the workaround.
7. **FEditorFileUtils class itself is unexported** — only its static members carry UNREALED_API (FileHelpers.h:383-437 verified per line); irrelevant for static calls but do not try to construct/derive.
8. **Editor-preferences classes are NOT all UDeveloperSettings**: ULevelEditorViewportSettings derives straight from UObject (LevelEditorViewportSettings.h:266-267), so a naive UDeveloperSettings-only enumeration under-reports the settings surface — list_developer_settings must ship the config-objects scope or it will mislead agents.
9. **UEditorUtilitySubsystem::TryRun on cooked EUBs is a dead end**: the UBlueprint cast path (Editor/Blutility/Private/EditorUtilitySubsystem.cpp:142-145) requires the uncooked UBlueprint asset; pak-mounted content ships only the generated class. run_editor_utility is loose-assets-only.
10. **UEditorLoadingAndSavingUtils::SaveDirtyPackages is NOT checkout-free**: FileHelpers.cpp:5551-5555 routes through InternalCheckoutAndSavePackages even in the no-dialog variant. The checkout-free save is FEditorFileUtils::SaveDirtyPackages(..., bFastSave=true) (FileHelpers.h:383).

## UNVERIFIED

- **set_editor_mode (write twin of get_editor_modes)** — ActivateMode/DeactivateMode are exported (EditorModeManager.h:84, :91) so linkage is NOT in doubt; what is unverified is mid-frame safety: activating a mode spawns Slate toolkits from an HTTP handler. Needs the SetTimerForNextTick deferral pattern + a PIE guard before proposing. Phase-2 design task.
- **Viewport bookmarks** — found no bookmark API on FEditorViewportClient (grep "Bookmark" over EditorViewportClient.h: 0 hits) and no Editor/UnrealEd/Public/BookmarkTypeTools.h in this fork at the path tried. The 5.3 bookmark surface (IBookmarkTypeTools?) was not located before time-boxing; value is low anyway since set_viewport_camera/get_viewport_camera already provide save/restore via the agent's own memory. _Phase-2 note: the surface DOES exist in this fork at `Editor/UnrealEd/Public/Bookmarks/` (IBookmarkTypeTools.h, IBookmarkTypeActions.h, BookmarkScoped.h — directory listed 2026-07-26); the 0-hit grep on EditorViewportClient.h is confirmed but was the wrong header. Stays UNVERIFIED (export macros/signatures not audited), no longer "not located"._
- **USelection visibility of BSP surface selection** (SelectBSPSurf, EditorEngine.h:1456) — untouched; BSP is irrelevant to this game's content.
- **UEditorActorSubsystem::ConvertActors** (EditorActorSubsystem.h:237, UNREALED_API) — exported and interesting (replace actors preserving transforms) but behaviour on Blueprint-class actors with cooked parents was not verified; candidate for the asset/actor axis in Phase-2.
- **Undo-barrier endpoints** (SetUndoBarrier/RemoveUndoBarrier, Transactor.h:611/:616, both UNREALED_API) — linkage verified, but interaction with the bridge's own blanket transactions (would a barrier strand RunEndpoint's entries?) needs a design pass before exposure.
- **UEditorAssetSubsystem metadata tags** (GetMetadataTag/SetMetadataTag, EditorAssetSubsystem.h:352/:361, UNREALED_API) — verified exported; deliberately NOT proposed (low agent value vs. endpoint budget); Phase-2 may revisit if asset-tagging workflows appear.

## Coverage log

Covered per the mission list:
- UEditorEngine public API: transaction block, selection virtuals + getters read; AddActor/undo/redo cited. NOT walked exhaustively (3314 lines) — remaining UNREALED_API surface (Bsp/poly ops :1472-1479, map rebuild :956, PIE internals) either belongs to other axes or is legacy BSP.
- UEditorSubsystem enumeration: COMPLETE — 26 engine-source + 28 engine-plugin subclasses, all dispositioned in the Surface inventory (grep patterns recorded there; Runtime tree: 0 hits).
- FEditorFileUtils / UEditorLoadingAndSavingUtils: COMPLETE for save/dirty flows (list_dirty_packages, save_dirty_packages); checkout paths deliberately skipped (no source control in modkit); Import/Export scene not examined (asset axis).
- Selection: COMPLETE — actor selection confirmed covered by existing select_level_actors (handler read); component selection proposed; content-browser selection getters exist (EditorEngine.h:1865-1868) but were left out (agents drive assets by path, not CB focus).
- Outliner folders + grouping: COMPLETE (list_actor_folders, set_actor_folder, group_actors/ungroup_actors).
- Transaction introspection: COMPLETE with the export finding the mission flagged (negative result #1).
- UDeveloperSettings enumeration: COMPLETE (list_developer_settings + the UObject-config caveat).
- IConsoleManager structured query: COMPLETE (get_cvar, list_cvars).
- Viewport (FLevelEditorViewportClient/FEditorViewportClient): view mode / show flags / game view / realtime / dimensions covered (get_viewport_state, set_view_mode); bookmarks UNVERIFIED; the temporary-override variant (AddRealtimeOverride, referenced by the deprecation note at EditorViewportClient.h:347-348) was not separately verified — SetRealtime (:392, UNREALED_API) covers the agent case.
- Editor modes: read side proposed; write side parked in UNVERIFIED with reasons.

Remaining for Phase-2 pickup: exhaustive EditorEngine.h walk (Bsp/rebuild families), UAssetEditorSubsystem open-editor-for-arbitrary-asset (only close/list proposed here), ULevelEditorSubsystem's LoadLevel/BuildLightMaps (async design needed), UEditorActorSubsystem::ConvertActors.

Proposal count: 21 endpoint names across 20 catalogue entries (group_actors/ungroup_actors share one entry). Tier 0: 3, Tier 1: 10, Tier 2: 8.

_Phase-2 verification pass COMPLETE (2026-07-26): all 20 entries carry verdicts — 18 CONFIRMED, 2 CORRECTED (save_dirty_packages: modal-on-failed-save + silent-skip + mid-frame-GC hazards, effort E2→E3; pilot_actor: silent no-viewport no-op + cinematic-lock precedence). 0 DEMOTED. All 10 negatives spot-verified, 0 overturned. Repairs: restored the `### list_actor_folders` heading lost to an earlier verdict insertion; located the bookmark headers under Editor/UnrealEd/Public/Bookmarks/ (stays UNVERIFIED). list_dirty_packages overlap with axis B remains flagged for the final-catalogue dedupe._

