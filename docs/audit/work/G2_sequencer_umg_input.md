# Axis G2 — Sequencer, UMG extras, Enhanced Input
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._
_Phase-2 adversarial verification: 2026-07-26. Every proposed entry re-opened against engine source; verdicts appended per entry. Negative #1 (SequencerScripting dead end) OVERTURNED on its enablement premise — see Negative results. Live bridge was reachable during Phase 2; asset census captured (see negative #8 update)._

## Surface inventory

**Sequencer object model (engine runtime modules — read, not recalled):**
- `Runtime/LevelSequence/Public/LevelSequence.h` — `ULevelSequence` is `UCLASS(BlueprintType, MinimalAPI)` (:23) with method-level `LEVELSEQUENCE_API` on everything needed: `Initialize()` :38, `BindPossessableObject` :46, `GetMovieScene` :51, `MakeSpawnableTemplateFromInstance` :58.
- `Runtime/LevelSequence/Public/LevelSequencePlayer.h` — `CreateLevelSequencePlayer` :119 (`static LEVELSEQUENCE_API`).
- `Runtime/MovieScene/Public/MovieScene.h` — `UMovieScene` is `UCLASS(DefaultToInstanced, MinimalAPI)` (:339), method-level `MOVIESCENE_API`: `AddSpawnable` :374, `RemoveSpawnable` :390, `AddPossessable` :432, `RemovePossessable` :447, `AddTrack(TrackClass, Guid)` :497, `FindTrack` :534, root `AddTrack(TrackClass)` :610, `SetPlaybackRange` :973; inline `GetTickResolution` :784, `SetDisplayRate` :808.
- `Runtime/MovieScene/Public/MovieSceneTrack.h` — `UCLASS(abstract, DefaultToInstanced, MinimalAPI, BlueprintType)` :164; `AddSection` :378, `CreateNewSection` :385, `GetAllSections` :392 — all `PURE_VIRTUAL`, dispatched via vtable (no export needed to call through a pointer).
- `Runtime/MovieScene/Public/MovieSceneSection.h` — `UCLASS(abstract, DefaultToInstanced, MinimalAPI, BlueprintType)` :248; `MOVIESCENE_API` `SetStartFrame` :385, `SetEndFrame` :391, `GetChannelProxy` :642.
- Channels: `Runtime/MovieScene/Public/Channels/MovieSceneFloatChannel.h` `AddConstantKey/AddLinearKey/AddCubicKey` :264/:266/:268 (all `MOVIESCENE_API`); `MovieSceneDoubleChannel.h` same trio :267/:269/:271; `MovieSceneBoolChannel.h` struct :30, inline `GetData()` :48; `MovieSceneChannelData.h` inline `TMovieSceneChannelData::AddKey` :303 backed by `MOVIESCENE_API AddKeyInternal` :216; `MovieSceneChannelProxy.h` template `GetChannels<ChannelType>()` :259.
- `Runtime/MovieSceneTracks/Public/Sections/MovieScene3DTransformSection.h` — transform section channel layout: `FMovieSceneDoubleChannel Translation[3]` :300, `Rotation[3]` :304, `Scale[3]` :308, `FMovieSceneFloatChannel ManualWeight` :312.
- `Runtime/MovieSceneTracks/Public/Tracks/MovieScenePropertyTrack.h` — `UCLASS(abstract, MinimalAPI)` :22, `MOVIESCENETRACKS_API SetPropertyNameAndPath` :57.
- `Runtime/MovieScene/Public/MovieSceneNameableTrack.h` — `MOVIESCENE_API SetDisplayName` :35.

**Sequencer scripting/editor plugins:**
- `D:/UE532/Engine/Plugins/MovieScene/SequencerScripting` — enumerated all 11 extension-library headers in `Source/SequencerScripting/Public/ExtensionLibraries/` (MovieSceneSequence/Binding/Section/Track/Folder/EventTrack/MaterialTrack/PrimitiveMaterialTrack/PropertyTrack/VectorTrack/SequencerScriptingRange Extensions; 69+21+23+13 UFUNCTIONs in the four core ones). **Plugin is NOT enabled**: no `EnabledByDefault` in its .uplugin (defaults false), not referenced by the .uproject; extension classes are `UCLASS()` with **no export macro**; the typed key/channel classes (`UMovieSceneScriptingFloatChannel` etc.) live in `Source/SequencerScripting/Private/KeysAndChannels/*.h` — private headers. See Negative results; all proposals below use the exported MOVIESCENE_API route instead. **[Phase-2 correction: the "plugin is NOT enabled" claim is WRONG — SequencerScripting is transitively enabled via LevelSequenceEditor.uplugin:25-30 (`"SequencerScripting": Enabled true`), and LevelSequenceEditor is EnabledByDefault (its .uplugin:13) and not disabled by the .uproject; ControlRig.uplugin:38-39 and MovieRenderPipeline.uplugin do the same. Compiled binaries exist at Engine/Plugins/MovieScene/SequencerScripting/Binaries/Win64/UnrealEditor-SequencerScripting.dll. The no-export/Private-header findings stand, so the MOVIESCENE_API route below remains the right call for linked C++ — but the reflection route through the extension UFUNCTIONs is available today at zero enabling cost. See overturned negative #1.]**
- `D:/UE532/Engine/Plugins/MovieScene/LevelSequenceEditor` — `"EnabledByDefault": true` (.uplugin:13), module `LevelSequenceEditor` Type Editor. `Public/LevelSequenceEditorBlueprintLibrary.h`: class-level `LEVELSEQUENCEEDITOR_API` :42; `OpenLevelSequence` :52, `GetCurrentLevelSequence` :58, `CloseLevelSequence` :88, `Play` :94, `Pause` :100, `SetCurrentTime` :108, `GetCurrentTime` :114, `SetPlaybackSpeed` :132, `IsPlaying` :150. Factory `ULevelSequenceFactoryNew` is in `Source/LevelSequenceEditor/Private/Factories/` — unexported (see Negative results); its 3-line recipe read from the .cpp :29-41.
- `D:/UE532/Engine/Plugins/MovieScene/MovieRenderPipeline` — `"EnabledByDefault": false` (.uplugin:16) **but enabled transitively**: .uproject enables `DLSSMoviePipelineSupport` (uproject :67-68) whose .uplugin (D:/DDS2SDK/Game/Plugins/DLSSMoviePipelineSupport/DLSSMoviePipelineSupport.uplugin:29-32) declares `MovieRenderPipeline: Enabled true`. Modules: MovieRenderPipelineCore (Runtime), MovieRenderPipelineEditor (Editor) (.uplugin:51-74). `MoviePipelineQueueSubsystem.h`: class-level `MOVIERENDERPIPELINEEDITOR_API` :12; `MoviePipelineQueue.h`: `MOVIERENDERPIPELINECORE_API` `UMoviePipelineExecutorJob` :275 / `UMoviePipelineQueue` :608, `AllocateNewJob` :623; `MoviePipelinePIEExecutor.h`: `MOVIERENDERPIPELINEEDITOR_API UMoviePipelinePIEExecutor` :27.

**UMG (modules UMG + UMGEditor — both ALREADY in MifBridge.Build.cs):**
- `Runtime/UMG/Public/Blueprint/WidgetTree.h` — `UCLASS(MinimalAPI)` :18; method-level `UMG_API`: `FindWidget` :30, `RemoveWidget` :43, static `FindWidgetParent(Widget, OutChildIndex)` :46, static `FindWidgetChild` :52, `GetAllWidgets` :61, static `GetChildWidgets` :64, static `TryMoveWidgetToNewTree` :67, `ForEachWidget` :74.
- `Runtime/UMG/Public/Components/PanelWidget.h` — `UCLASS(Abstract, MinimalAPI)` :13; `UMG_API`: `GetChildrenCount` :28, `GetChildIndex` :44, `RemoveChildAt` :52, `AddChild` :59; inside `#if WITH_EDITOR` (:61-93): `ReplaceChildAt` :70, `InsertChildAt` :84, `ShiftChild` :89. Insertion/shift are index-based and work on any multi-child panel (`CanHaveMultipleChildren` :114 gates single-slot panels like Border/Button).
- `Editor/UMGEditor/Public/WidgetBlueprint.h` — class-level `UMGEDITOR_API UWidgetBlueprint` :241, `TArray<TObjectPtr<UWidgetAnimation>> Animations` :256.
- `Editor/UMGEditor/Public/WidgetBlueprintEditorUtils.h` — class-level `UMGEDITOR_API` :22; `RenameWidget` :34 **takes `TSharedRef<FWidgetBlueprintEditor>`** (UI-locked; core replicated — see rename_widget + Negative results; reference implementation read at `Editor/UMGEditor/Private/WidgetBlueprintEditorUtils.cpp:277-433`).
- `Runtime/UMG/Public/Animation/WidgetAnimation.h` — `UCLASS(BlueprintType, MinimalAPI)` :20, `UWidgetAnimation : UMovieSceneSequence` :21 (⇒ the sequencer endpoints below are double-duty); public UPROPERTYs `MovieScene` :131 and `AnimationBindings` :135; `UMG_API RemoveBinding` :115-116.
- `Runtime/UMG/Public/Animation/WidgetAnimationBinding.h` — fields `WidgetName` :22, `SlotWidgetName` :25, `AnimationGuid` :28, `bIsRootWidget` :31.
- UMG-specific track classes exist in `Runtime/UMG/Public/Animation/`: `MovieScene2DTransformTrack.h`, `MovieSceneMarginTrack.h`, `MovieSceneWidgetMaterialTrack.h` (dir listing verified).
- Widget-animation creation recipe read from `Editor/UMGEditor/Private/TabFactory/AnimationTabSummoner.cpp:589-613` and `Blueprint->Animations.Add` at :260/:277.
- `Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h` — `static UNREALED_API void ReplaceVariableReferences(UBlueprint*, FName, FName)` :1053. `Kismet2NameValidators.h` — `FKismetNameValidator` unexported class but method-level `UNREALED_API` ctor :86 and `IsValid` :93-94.

**Enhanced Input (plugin `D:/UE532/Engine/Plugins/EnhancedInput`):**
- `"EnabledByDefault": true` (.uplugin:13); modules: EnhancedInput (Runtime, :19-21), InputBlueprintNodes (UncookedOnly, :24-26), InputEditor (Editor, :29-31). Not disabled by the .uproject.
- **DDS2 uses Enhanced Input**: `D:/DDS2SDK/Game/Config/DefaultInput.ini:81-82` — `DefaultPlayerInputClass=/Script/EnhancedInput.EnhancedPlayerInput`, `DefaultInputComponentClass=/Script/EnhancedInput.EnhancedInputComponent`. Loose SDK content includes 7 `IA_*.uasset` + `IMC_Default.uasset` under `Content/CityScooter/Blueprints/Input/`.
- `Source/EnhancedInput/Public/InputMappingContext.h` — class-level `ENHANCEDINPUT_API UInputMappingContext : UDataAsset` :22; `GetMappings` :53, `GetMapping(Index)` :54, `MapKey` :62, `UnmapKey` :68, `UnmapAllKeysFromAction` :78.
- `Source/EnhancedInput/Public/InputAction.h` — class-level `ENHANCEDINPUT_API UInputAction : UDataAsset` :53, `ValueType` :114.
- `Source/EnhancedInput/Public/EnhancedActionKeyMapping.h` — `struct ENHANCEDINPUT_API FEnhancedActionKeyMapping` :82; `Triggers` :134, `Modifiers` :144, `Action` :148, `Key` :152.
- `Source/InputEditor/Public/InputEditorModule.h` — `INPUTEDITOR_API UInputMappingContext_Factory` :59, `UInputAction_Factory` :80 (both exported, but `ConfigureProperties` opens a modal — see Negative results; factories are unnecessary since both assets are plain UDataAssets).

**DDS2 UMG weight (tier evidence):** live bridge was unreachable during this sweep (connection refused on 127.0.0.1:8791 across the whole window; retried 4×). Static evidence used instead: `Content/GUI/` holds 101 loose .uassets, the MODS folder contains widget-blueprint mod content (`Content/MODS/DriveableScooter/WBP_MifFuelGauge.uasset`), and the shipped game UI is UMG (cooked WidgetBlueprintGeneratedClasses in paks). UI-mod authoring is a first-class modkit scenario, which justifies Tier-0/1 scores on the UMG entries. **[Phase-2 update: bridge WAS reachable on 2026-07-26 (Phase-2 window). Census via `POST /api/find_assets` (param is `class`, NOT `className` — see negative #8 update): WidgetBlueprint 54, InputAction 62, InputMappingContext 5, LevelSequence 4 (origin=any, cooked+loose). UMG/input weight confirmed; LevelSequence barely used by the base game (4 assets) — creation demand is mod-side, which is exactly what create_level_sequence serves.]**

**New module dependencies this axis would add to MifBridge.Build.cs** (all editor-safe; MifBridge is editor-only):
| Module | Kind | Where |
|---|---|---|
| `MovieScene`, `MovieSceneTracks` | Runtime | engine Source/Runtime (no plugin) — already anticipated by roadmap §"New module dependencies" |
| `LevelSequence` | Runtime | engine Source/Runtime |
| `LevelSequenceEditor` | Editor | plugin LevelSequenceEditor, EnabledByDefault true |
| `MovieRenderPipelineCore` | Runtime | plugin MovieRenderPipeline, enabled transitively via DLSSMoviePipelineSupport |
| `MovieRenderPipelineEditor` | Editor | same plugin |
| `EnhancedInput` | Runtime | plugin EnhancedInput, EnabledByDefault true |

## Proposed endpoints

### create_level_sequence
**Purpose**: Mint a new, saveable ULevelSequence asset at a given package path — currently no cinematic can be authored at all.
**Engine API**:
```cpp
LEVELSEQUENCE_API virtual void Initialize();                                              // Runtime/LevelSequence/Public/LevelSequence.h:38
MOVIESCENE_API void SetPlaybackRange(FFrameNumber Start, int32 Duration, bool bAlwaysMarkDirty = true);  // Runtime/MovieScene/Public/MovieScene.h:973
void SetDisplayRate(FFrameRate InDisplayRate)                                             // Runtime/MovieScene/Public/MovieScene.h:808 (inline)
```
Recipe (verbatim engine precedent, `Engine/Plugins/MovieScene/LevelSequenceEditor/Source/LevelSequenceEditor/Private/Factories/LevelSequenceFactoryNew.cpp:29-41`): `NewObject<ULevelSequence>(InParent, Name, Flags|RF_Transactional)` → `Initialize()` → `GetMovieScene()->SetPlaybackRange(...)`. MifBridge does the same into a freshly created `UPackage` + `FAssetRegistryModule::AssetCreated` + optional save (same pattern as existing `create_blueprint`).
**Export**: `LEVELSEQUENCE_API` / `MOVIESCENE_API` (method-level; both classes MinimalAPI) | **Module**: `LevelSequence`, `MovieScene` — NEW deps, runtime engine modules | **Guards**: none
**Bucket**: self-managed — creates + registers a new package/asset; must not sit inside the blanket undo entry (delete_asset is the inverse, matching create_blueprint precedent).
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| packagePath | path, assetPath | string (`/Game/...`) | — | yes (strict: empty ⇒ error naming param) |
| name | assetName | string | — | yes |
| displayRate | fps | number (fps) | 30 | no |
| durationFrames | duration | int (display-rate frames) | 150 | no |
| save | — | bool | false | no |
Unrecognised parameter ⇒ error naming it.
**Failure modes**: package already exists ⇒ `"packagePath '/Game/X' already contains an asset — pass a different name or delete_asset first"`; invalid long package name ⇒ `"packagePath must start with /Game/"`; durationFrames <= 0 ⇒ `"durationFrames must be >= 1"`.
**Cooked**: unaffected — always creates a NEW loose asset; cannot create inside a pak mount point (refuse with `"packagePath is under a mounted container — choose a loose content path"`).
**Verify**: `describe_sequence` on the result reports `tickResolution`, `displayRate == 30/1`, `playbackRange == [0,150)`, 0 bindings, 0 tracks; `find_assets class=LevelSequence` count +1 (Phase-2: live endpoint's param is `class`, not `className` — MifBridgeCooked.cpp:193).
**Score**: U5 E2 R2 → tier 1 (opens the whole cinematic category)
**Phase-2 verdict**: CONFIRMED — LevelSequence.h:23/:38, MovieScene.h:973/:808 re-read verbatim; factory recipe re-read at LevelSequenceFactoryNew.cpp:28-40 (NewObject → Initialize → SetPlaybackRange, exactly as claimed). Verify line's `className` corrected to `class`.

### describe_sequence
**Purpose**: One-call structured dump of any UMovieSceneSequence (ULevelSequence *or* UWidgetAnimation): ranges, rates, bindings with GUIDs, tracks with class + sections + channel inventory — the numeric ground truth every mutation below verifies against.
**Engine API**:
```cpp
MOVIESCENE_API UMovieSceneTrack* FindTrack(TSubclassOf<UMovieSceneTrack> TrackClass, const FGuid& ObjectGuid, const FName& TrackName = NAME_None) const; // MovieScene.h:534
virtual const TArray<UMovieSceneSection*>& GetAllSections() const PURE_VIRTUAL(...)       // Runtime/MovieScene/Public/MovieSceneTrack.h:392
MOVIESCENE_API FMovieSceneChannelProxy& GetChannelProxy() const;                          // Runtime/MovieScene/Public/MovieSceneSection.h:642
```
Plus inline accessors: `UMovieScene::GetTickResolution` (MovieScene.h:784), `GetPossessableCount/GetPossessable/GetSpawnableCount/GetSpawnable/GetTracks` (all inline or MOVIESCENE_API in MovieScene.h), `FMovieSceneChannelEntry::GetChannels()` (Channels/MovieSceneChannelProxy.h:41 — Phase-2: this line is on the ENTRY class, not the proxy; the proxy's typed template `GetChannels<ChannelType>()` is :259) for per-channel key counts via `FMovieSceneChannel::GetNumKeys` (virtual with default impl, Channels/MovieSceneChannel.h:157 — vtable call, no export needed).
**Export**: `MOVIESCENE_API` method-level | **Module**: `MovieScene` (NEW) | **Guards**: none
**Bucket**: read-only — pure query.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| objectPath | sequencePath, path | string (asset path of ULevelSequence, or `WidgetBP:AnimName` widget-animation address) | — | yes |
| includeKeys | keys | bool | false | no (true ⇒ per-channel key times/values for float/double/bool channels) |
**Failure modes**: path resolves to non-UMovieSceneSequence ⇒ `"objectPath '<x>' is a <Class>, expected ULevelSequence or UWidgetAnimation"`; unloadable ⇒ `"could not load objectPath — check the path with find_assets"`.
**Cooked**: WORKS — MovieScene data is runtime data and survives cooking intact (unlike BP graphs), so pak-mounted sequences and cooked widget animations are fully readable.
**Verify**: self-verifying (it IS the verifier). Numbers: binding count, track count per binding, section ranges in frames, channel key counts.
**Score**: U5 E3 R5 → tier 1 (pairs with every mutation on this axis)
**Phase-2 verdict**: CORRECTED — one class misattribution fixed in place: ChannelProxy.h:41 `GetChannels()` belongs to `FMovieSceneChannelEntry`, not `FMovieSceneChannelProxy` (proxy's typed template is :259; both re-read). Added the `GetNumKeys` citation (MovieSceneChannel.h:157, virtual w/ default impl). Primary citations (FindTrack :534, GetAllSections :392, GetChannelProxy :642) verbatim-correct.

### sequence_bind_actor
**Purpose**: Bind a placed level actor into a sequence as a possessable (or clone it in as a spawnable), returning the binding GUID that all track endpoints key off.
**Engine API**:
```cpp
MOVIESCENE_API FGuid AddPossessable(const FString& Name, UClass* Class);                  // Runtime/MovieScene/Public/MovieScene.h:432
LEVELSEQUENCE_API virtual void BindPossessableObject(const FGuid& ObjectId, UObject& PossessedObject, UObject* Context) override; // Runtime/LevelSequence/Public/LevelSequence.h:46
MOVIESCENE_API FGuid AddSpawnable(const FString& Name, UObject& ObjectTemplate);          // Runtime/MovieScene/Public/MovieScene.h:374
LEVELSEQUENCE_API virtual UObject* MakeSpawnableTemplateFromInstance(UObject& InSourceObject, FName ObjectName) override; // LevelSequence.h:58
```
Context for BindPossessableObject = the actor's UWorld (editor world). `spawnable=true` path: `MakeSpawnableTemplateFromInstance` → `AddSpawnable`.
**Export**: method-level `MOVIESCENE_API` / `LEVELSEQUENCE_API` | **Module**: `MovieScene`, `LevelSequence` (NEW) | **Guards**: none
**Bucket**: transacted — small object-graph mutation on a loaded asset; clean single undo.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| sequencePath | objectPath | string | — | yes |
| actor | actorLabel, actorName | string (label or path-name, same resolution as set_actor_transform) | — | yes |
| spawnable | asSpawnable | bool | false | no |
| bindingName | name | string | actor label | no |
**Failure modes**: actor not found ⇒ `"actor 'X' not found in the editor world — list_level_actors to see labels"`; already bound ⇒ return existing GUID with `"alreadyBound": true` (idempotent, not an error); spawnable on a non-duplicatable actor class ⇒ error naming the class.
**Cooked**: sequence must be loose to SAVE; binding a cooked-map actor into a loose sequence works in-editor (possessables resolve by GUID at runtime) — degraded note in response when the sequence is pak-mounted: refuse with `"sequence is cooked/pak-mounted — duplicate_asset it into a loose path first"`.
**Verify**: `describe_sequence` bindings count +1, returned GUID present with the given name and class.
**Score**: U4 E3 R3 → tier 1
**Phase-2 verdict**: CONFIRMED — MovieScene.h:432/:374 and LevelSequence.h:46/:58 verbatim. Headless-safety additionally verified: `ULevelSequence::BindPossessableObject` implementation (Runtime/LevelSequence/Private/LevelSequence.cpp:424-430) just calls `BindingReferences.AddBinding` and no-ops on null Context — no UI/preview dependency; pass the editor UWorld as Context as specified.

### sequence_add_track
**Purpose**: Add a typed track (transform, float property, bool property, visibility, …) to a binding or to sequence root — the step between binding and keys.
**Engine API**:
```cpp
MOVIESCENE_API UMovieSceneTrack* AddTrack(TSubclassOf<UMovieSceneTrack> TrackClass, const FGuid& ObjectGuid); // Runtime/MovieScene/Public/MovieScene.h:497
MOVIESCENE_API UMovieSceneTrack* AddTrack(TSubclassOf<UMovieSceneTrack> TrackClass);      // Runtime/MovieScene/Public/MovieScene.h:610  (root tracks)
MOVIESCENETRACKS_API void SetPropertyNameAndPath(FName InPropertyName, const FString& InPropertyPath); // Runtime/MovieSceneTracks/Public/Tracks/MovieScenePropertyTrack.h:57
MOVIESCENE_API virtual void SetDisplayName(const FText& NewDisplayName);                  // Runtime/MovieScene/Public/MovieSceneNameableTrack.h:35
```
Track class resolved by NAME via reflection (`FindObject<UClass>` on e.g. `/Script/MovieSceneTracks.MovieScene3DTransformTrack`) — sidesteps every MinimalAPI track class; `Cast<UMovieScenePropertyTrack>` links because MinimalAPI still exports StaticClass. Property tracks (float/bool/color/…) additionally get `SetPropertyNameAndPath` from the `propertyName`/`propertyPath` params. UMG-only track classes (`/Script/UMG.MovieScene2DTransformTrack`, `MovieSceneMarginTrack`, `MovieSceneWidgetMaterialTrack` — headers verified in `Runtime/UMG/Public/Animation/`) resolve through the same reflection route, making this endpoint double-duty for widget animations.
**Export**: `MOVIESCENE_API` / `MOVIESCENETRACKS_API` method-level | **Module**: `MovieScene`, `MovieSceneTracks` (NEW) | **Guards**: `SetDisplayName` is declared inside `#if WITH_EDITORONLY_DATA` (MovieSceneNameableTrack.h:27) — satisfied in MifBridge's editor-only compile, but the displayName code path must sit under that guard (Phase-2 correction; rest of the entry needs none)
**Bucket**: transacted.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| sequencePath | objectPath | string (LevelSequence or `WidgetBP:AnimName`) | — | yes |
| trackClass | type | string — short name (`3DTransform`, `Float`, `Bool`, `Visibility`, `2DTransform`, `Margin`) or full `/Script/...` path | — | yes |
| bindingGuid | guid, binding | string GUID | root track if omitted | no |
| propertyName | — | string | — | required iff trackClass is a property track |
| propertyPath | — | string | propertyName | no |
| displayName | — | string | — | no |
**Failure modes**: unknown trackClass ⇒ `"trackClass 'X' did not resolve to a UMovieSceneTrack subclass — try a /Script/ path"`; bindingGuid not in sequence ⇒ error listing valid GUIDs; property track without propertyName ⇒ `"trackClass Float requires propertyName"`; duplicate single-instance track (transform on same binding) ⇒ return existing track index with `"alreadyExists": true`.
**Cooked**: refuses on pak-mounted sequence (unsaveable) with the duplicate_asset hint; fine on loose.
**Verify**: `describe_sequence` — track count under the binding +1, class name echoed, `trackIndex` returned for addressing.
**Score**: U4 E3 R3 → tier 1
**Phase-2 verdict**: CORRECTED — Guards field was "none" but `SetDisplayName` (MovieSceneNameableTrack.h:35) sits inside `#if WITH_EDITORONLY_DATA` (:27); field fixed in place. All four signatures re-read verbatim (MovieScene.h:497/:610, MovieScenePropertyTrack.h:57 — which is OUTSIDE the editor-only block, verified — and NameableTrack :35). Reflection-resolution + MinimalAPI-Cast<> claims sound.

### sequence_add_section
**Purpose**: Give a track an actual section with a frame range — tracks evaluate nothing without one.
**Engine API**:
```cpp
virtual class UMovieSceneSection* CreateNewSection() PURE_VIRTUAL(...)                    // Runtime/MovieScene/Public/MovieSceneTrack.h:385
virtual void AddSection(UMovieSceneSection& Section) PURE_VIRTUAL(...)                    // Runtime/MovieScene/Public/MovieSceneTrack.h:378
MOVIESCENE_API virtual void SetStartFrame(TRangeBound<FFrameNumber> NewStartFrame);       // Runtime/MovieScene/Public/MovieSceneSection.h:385
MOVIESCENE_API virtual void SetEndFrame(TRangeBound<FFrameNumber> NewEndFrame);           // Runtime/MovieScene/Public/MovieSceneSection.h:391
```
CreateNewSection/AddSection are vtable calls on the concrete track (no export needed). Frame params are display-rate frames, converted to tick-resolution internally (`Frame * TickResolution / DisplayRate`, FFrameRate arithmetic is inline).
**Export**: `MOVIESCENE_API` on the setters; vtable for the rest | **Module**: `MovieScene` (NEW) | **Guards**: none
**Bucket**: transacted.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| sequencePath | objectPath | string | — | yes |
| bindingGuid | guid | string | root | no |
| trackIndex | track | int | 0 | no |
| startFrame | start | int (display-rate) | 0 | no |
| endFrame | end | int (display-rate, exclusive) | sequence playback end | no |
**Failure modes**: trackIndex out of range ⇒ `"trackIndex 3 out of range — track has N sections, binding has M tracks (describe_sequence)"`; endFrame <= startFrame ⇒ error naming both.
**Cooked**: refuses on pak-mounted sequence (unsaveable).
**Verify**: `describe_sequence` — section count on the track +1, reported range == requested (in both frame spaces).
**Score**: U4 E3 R3 → tier 1
**Phase-2 verdict**: CONFIRMED — MovieSceneTrack.h:378/:385 (PURE_VIRTUAL, vtable-dispatched as claimed) and MovieSceneSection.h:385/:391 (MOVIESCENE_API) verbatim.

### sequence_set_keys
**Purpose**: Batch-write keys into a section's channels — transform XYZ, any float/double property, bools — the endpoint that turns the object model into actual animation.
**Engine API**:
```cpp
MOVIESCENE_API FMovieSceneChannelProxy& GetChannelProxy() const;                          // Runtime/MovieScene/Public/MovieSceneSection.h:642
TArrayView<ChannelType*> GetChannels() const;                                             // Runtime/MovieScene/Public/Channels/MovieSceneChannelProxy.h:259 (template, inline)
MOVIESCENE_API int32 AddCubicKey(FFrameNumber InTime, float InValue, ERichCurveTangentMode TangentMode = RCTM_Auto, const FMovieSceneTangentData& Tangent = FMovieSceneTangentData()); // Channels/MovieSceneFloatChannel.h:268 (AddConstantKey :264, AddLinearKey :266)
MOVIESCENE_API int32 AddCubicKey(FFrameNumber InTime, double InValue, ...);               // Channels/MovieSceneDoubleChannel.h:271 (AddConstantKey :267, AddLinearKey :269)
int32 AddKey(FFrameNumber InTime, ParamType InValue)                                      // Channels/MovieSceneChannelData.h:303 (inline; bool channels via FMovieSceneBoolChannel::GetData(), MovieSceneBoolChannel.h:48)
```
Channel addressing is positional: transform sections expose `Translation[3]/Rotation[3]/Scale[3]` as double channels 0-8 plus float ManualWeight (`Runtime/MovieSceneTracks/Public/Sections/MovieScene3DTransformSection.h:300-312`); float property sections expose one float channel. The proxy is type-generic, so the SAME endpoint keys level-sequence transform tracks and UMG 2D-transform/margin channels. This is exactly what SequencerScripting's private `UMovieSceneScriptingFloatChannel` wraps — we call the exported engine layer directly.
**Export**: `MOVIESCENE_API` method-level; templates inline | **Module**: `MovieScene` (NEW; `MovieSceneTracks` only for the layout doc, no link need here) | **Guards**: none
**Bucket**: transacted — pure data write into a loaded section.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| sequencePath | objectPath | string | — | yes |
| bindingGuid | guid | string | root | no |
| trackIndex | track | int | 0 | no |
| sectionIndex | section | int | 0 | no |
| channel | channelIndex | int OR string (`loc.x`..`scale.z` sugar for transform sections) | — | yes |
| keys | — | array of `{frame:int, value:number|bool}` | — | yes (strict: empty ⇒ error) |
| interp | interpolation | string enum `cubic\|linear\|constant` | cubic | no |
Also auto-expands the section range to cover the outermost keys (SetStartFrame/SetEndFrame) unless `expandSection=false`.
**Failure modes**: channel index out of range ⇒ `"channel 9 out of range — section has 9 double + 1 float channels (describe_sequence includeKeys=true)"`; bool value into float channel ⇒ error naming the channel type; string channel name on a non-transform section ⇒ `"named channels only apply to 3DTransform/2DTransform sections — use a numeric channel index"`.
**Cooked**: refuses on pak-mounted sequence.
**Verify**: `describe_sequence includeKeys=true` — key count per channel == submitted count, spot-check `keys[i].value` echo; playback range covers key extents.
**Score**: U5 E2 R3 → tier 1 (with the three above, completes create→bind→track→section→keys end-to-end)
**Phase-2 verdict**: CONFIRMED — all key-API citations verbatim (FloatChannel :264/:266/:268, DoubleChannel :267/:269/:271, ChannelData AddKey :303 backed by MOVIESCENE_API AddKeyInternal :216, BoolChannel GetData :48, proxy template :259). Note verified in passing: the 3DTransformSection channel members (:300-312) are PRIVATE UPROPERTYs — irrelevant here since all access goes through GetChannelProxy, but do not attempt direct member access.

### open_sequence_editor
**Purpose**: Open (or close) a level sequence in the Sequencer editor so a human — or capture_camera — can see what the agent authored.
**Engine API**:
```cpp
static bool OpenLevelSequence(ULevelSequence* LevelSequence);                             // Plugins/MovieScene/LevelSequenceEditor/Source/LevelSequenceEditor/Public/LevelSequenceEditorBlueprintLibrary.h:52
static ULevelSequence* GetCurrentLevelSequence();                                         // :58
static void CloseLevelSequence();                                                         // :88
```
**Export**: class-level `LEVELSEQUENCEEDITOR_API` (:42) | **Module**: `LevelSequenceEditor` (NEW; plugin EnabledByDefault true) | **Guards**: none (editor module)
**Bucket**: self-managed (none) — UI action, no data mutation, transaction would be an empty undo entry.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| sequencePath | objectPath, path | string | — | yes unless close=true |
| close | — | bool | false | no |
**Failure modes**: asset is not a ULevelSequence ⇒ error with actual class; OpenLevelSequence returns false ⇒ `"Sequencer failed to open the asset — is the editor in PIE?"`.
**Cooked**: cooked sequences OPEN read-only fine (data intact) — allowed, response flags `"cooked": true`.
**Verify**: response echoes `GetCurrentLevelSequence()->GetPathName()` == requested path.
**Score**: U3 E4 R4 → tier 2
**Phase-2 verdict**: CONFIRMED — library citations verbatim (:42/:52/:58/:88). Modal-hazard check done: `OpenLevelSequence` implementation (LevelSequenceEditorBlueprintLibrary.cpp:28-36) is a plain `UAssetEditorSubsystem::OpenEditorForAsset` — no dialog, no blocking wait.

### sequence_editor_play
**Purpose**: Drive the open Sequencer: play/pause/scrub/speed — lets an agent step a cinematic to a frame and capture_camera it (numbers + pixels workflow).
**Engine API**:
```cpp
static void Play();                                                                        // LevelSequenceEditorBlueprintLibrary.h:94
static void Pause();                                                                       // :100
static void SetCurrentTime(int32 NewFrame);                                                // :108
static int32 GetCurrentTime();                                                             // :114
static void SetPlaybackSpeed(float NewPlaybackSpeed);                                      // :132
static bool IsPlaying();                                                                   // :150
```
**Export**: class-level `LEVELSEQUENCEEDITOR_API` | **Module**: `LevelSequenceEditor` (NEW) | **Guards**: none
**Bucket**: self-managed (none) — transient playback state, not undoable data.
**Async**: no — commands return immediately; playback advances on subsequent editor frames, which is fine because the handler never waits.
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| action | — | string enum `play\|pause\|scrub\|status` | status | no |
| frame | time | int (display-rate frames; SetCurrentTime takes frames) | — | required iff action=scrub |
| speed | playbackSpeed | float | — | no |
Response always includes `{currentFrame, isPlaying, sequence}` from GetCurrentTime/IsPlaying/GetCurrentLevelSequence.
**Failure modes**: no sequence open ⇒ `"no level sequence is open in Sequencer — call open_sequence_editor first"` (GetCurrentLevelSequence()==nullptr check); action=scrub without frame ⇒ error naming `frame`.
**Cooked**: same as open_sequence_editor.
**Verify**: `action=scrub frame=42` then `action=status` ⇒ `currentFrame == 42`; play → two status polls show increasing currentFrame.
**Score**: U3 E4 R3 → tier 2
**Phase-2 verdict**: CONFIRMED — all six signatures verbatim at the cited lines (:94/:100/:108/:114/:132/:150); `SetCurrentTime(int32)`/`GetCurrentTime` int-frame typing confirmed against the header comments ("playback position ... in frames").

### render_movie_request / render_movie_status
**Purpose**: Queue a Movie Render Pipeline job for a sequence+map and poll it — turns authored cinematics into actual video/image output, entirely headless.
**Engine API**:
```cpp
UMoviePipelineQueue* GetQueue() const                                                      // Plugins/MovieScene/MovieRenderPipeline/Source/MovieRenderPipelineEditor/Public/MoviePipelineQueueSubsystem.h:25-28 (inline)
UMoviePipelineExecutorBase* RenderQueueWithExecutor(TSubclassOf<UMoviePipelineExecutorBase> InExecutorType); // MoviePipelineQueueSubsystem.h:65
bool IsRendering() const                                                                   // MoviePipelineQueueSubsystem.h:83-86 (inline)
UMoviePipelineExecutorJob* AllocateNewJob(TSubclassOf<UMoviePipelineExecutorJob> InJobType); // .../MovieRenderPipelineCore/Public/MoviePipelineQueue.h:623
void SetConfiguration(UMoviePipelinePrimaryConfig* InPreset);                              // MoviePipelineQueue.h:444
FString JobName; FSoftObjectPath Sequence; FSoftObjectPath Map;                            // MoviePipelineQueue.h:536/540/544 (public UPROPERTYs)
```
Executor: `UMoviePipelinePIEExecutor` (`MOVIERENDERPIPELINEEDITOR_API`, MoviePipelinePIEExecutor.h:27) — renders in a PIE session inside the running editor. Subsystem obtained via `GEditor->GetEditorSubsystem<UMoviePipelineQueueSubsystem>()`.
**Export**: class-level `MOVIERENDERPIPELINEEDITOR_API` (subsystem :12) + `MOVIERENDERPIPELINECORE_API` (queue :608, job :275) | **Module**: `MovieRenderPipelineCore` + `MovieRenderPipelineEditor` — NEW deps; plugin enabled transitively via project-enabled DLSSMoviePipelineSupport (verified above) | **Guards**: none
**Bucket**: self-managed — spawns a PIE session and multi-frame async work; MUST NOT be inside any transaction (same class of hazard as start_pie).
**Async**: REQUEST + POLL. `render_movie_request` allocates job, sets Sequence/Map/output config (resolution, output dir, .jpg/.png/EXR via `UMoviePipelinePrimaryConfig` settings), calls RenderQueueWithExecutor, subscribes `OnExecutorFinished`, returns a jobId. `render_movie_status` reports `{isRendering, finished, success, outputDirectory, filesWritten}` (files enumerated from the output dir on finish).
**Params (request)**: | name | aliases | type | default | required |
|---|---|---|---|---|
| sequencePath | sequence | string | — | yes |
| mapPath | map, level | string | current editor map | no |
| outputDir | output | string | Saved/MovieRenders | no |
| resX/resY | width/height | int | 1920/1080 | no |
| format | — | string enum `png\|jpg\|exr` | png | no |
**Failure modes**: already rendering ⇒ `"a render is in progress — poll render_movie_status or wait"` (IsRendering()==true); sequence/map soft path unresolvable ⇒ error naming which; PIE already active ⇒ `"stop_pie first — MRQ needs to own the PIE session"`.
**Cooked**: cooked sequence + cooked map RENDER fine (evaluation-only) — allowed.
**Verify**: status flips isRendering true→false with finished:true, `filesWritten > 0`, and the file count == frame count of the playback range (numeric check).
**Score**: U4 E2 R2 → tier 2 (heavier design: config object graph; but the API surface is fully exported and already enabled)
**Phase-2 verdict**: CORRECTED — Phase 1 missed a MODAL hazard: `UMoviePipelinePIEExecutor::Start` (MovieRenderPipelineEditor/Private/MoviePipelinePIEExecutor.cpp:82-114) calls `FMessageDialog::Open(EAppMsgType::Ok, ...)` at :93 (job sequence fails to load) and :109 (any queue job's target map unsaved, via `UMoviePipelineEditorBlueprintLibrary::IsMapValidForRemoteRender` :103), suppressed only when rendering offscreen — and Start runs SYNCHRONOUSLY inside `RenderQueueWithExecutor`, i.e. inside the HTTP handler mid-frame ⇒ modal hangs the pump. `render_movie_request` MUST pre-validate before calling: (1) sequencePath loads as ULevelSequence, (2) mapPath (and every job in the queue) points to a saved map — reuse `IsMapValidForRemoteRender` and error `"mapPath '/Game/X' is unsaved — save_level_as first"`. Also clear stale jobs from the shared editor queue (GetQueue is the singleton editor queue) before allocating. All queue/subsystem/executor citations otherwise verbatim (QueueSubsystem :12/:25-28/:65/:83-86, Queue/Job classes + AllocateNewJob/SetConfiguration/JobName/Sequence/Map, PIEExecutor :27) and the enablement chain re-verified (MovieRenderPipeline.uplugin:16 false + DLSSMoviePipelineSupport.uplugin:29-32 → enabled; compiled DLLs present in the plugin's Binaries/Win64).

### list_widget_tree
**Purpose**: One-call enumeration of a Widget Blueprint's whole widget hierarchy (name, class, parent, child index, slot class, isVariable) — closes the documented roadmap gap "UMG: no one-call tree enumeration" (docs/06_CAPABILITY_ROADMAP.md:74).
**Engine API**:
```cpp
UMG_API void ForEachWidget(TFunctionRef<void(UWidget*)> Predicate) const;                 // Runtime/UMG/Public/Blueprint/WidgetTree.h:74
UMG_API void GetAllWidgets(TArray<UWidget*>& Widgets) const;                              // WidgetTree.h:61
static UMG_API class UPanelWidget* FindWidgetParent(UWidget* Widget, int32& OutChildIndex); // WidgetTree.h:46
UMG_API int32 GetChildIndex(const UWidget* Content) const;                                // Runtime/UMG/Public/Components/PanelWidget.h:44
```
**Export**: `UMG_API` method-level (UWidgetTree and UPanelWidget are MinimalAPI) | **Module**: none — UMG already linked | **Guards**: none
**Bucket**: read-only — pure query.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| blueprint | blueprintPath, objectPath | string (WidgetBlueprint path) | — | yes |
| includeSlots | slots | bool | true | no (adds slot class + key slot properties per child) |
Unrecognised parameter ⇒ error.
**Failure modes**: asset is not a UWidgetBlueprint ⇒ `"'/Game/X' is a <Class> — list_widget_tree needs a WidgetBlueprint"`; cooked-only class given ⇒ degraded read (see Cooked).
**Cooked**: DEGRADED but useful — for pak-mounted UI only `UWidgetBlueprintGeneratedClass` exists; its template `WidgetTree` is still walkable (same UMG_API calls), but bindings/animations metadata that lives on the UWidgetBlueprint asset is absent. Response sets `"cooked": true` and omits editor-only fields.
**Verify**: widget count == count visible in the designer outline; for a known test WBP, parent/childIndex pairs are numerically checkable after every reparent_widget call.
**Score**: U5 E4 R5 → tier 0 (closes roadmap gap; the read half of the reparent/rename pairs)
**Phase-2 verdict**: CONFIRMED — WidgetTree.h:18/:30/:46/:61/:64/:67/:74 and PanelWidget.h:44 all verbatim, UMG_API method-level on MinimalAPI classes exactly as claimed. Live census: 54 WidgetBlueprints visible to the bridge (tier-0 demand confirmed). The cooked-degraded branch still depends on the UNVERIFIED UWidgetBlueprintGeneratedClass accessor — implement the loose path first.

### reparent_widget
**Purpose**: Move an existing widget to a new parent panel and/or new child index WITHOUT destroying it — today remove+add loses identity, bindings, and every property edit (roadmap :74-75).
**Engine API**:
```cpp
UMG_API UPanelSlot* InsertChildAt(int32 Index, UWidget* Content);                          // Runtime/UMG/Public/Components/PanelWidget.h:84  (#if WITH_EDITOR, :61)
UMG_API void ShiftChild(int32 Index, UWidget* Child);                                      // PanelWidget.h:89  (#if WITH_EDITOR)
UMG_API UPanelSlot* AddChild(UWidget* Content);                                            // PanelWidget.h:59
UMG_API bool RemoveChildAt(int32 Index);                                                   // PanelWidget.h:52
static UMG_API bool TryMoveWidgetToNewTree(UWidget* Widget, UWidgetTree* DestinationTree); // Runtime/UMG/Public/Blueprint/WidgetTree.h:67
static UMG_API class UPanelWidget* FindWidgetParent(UWidget* Widget, int32& OutChildIndex);// WidgetTree.h:46
```
Same-tree move: RemoveChildAt(old) then InsertChildAt/AddChild on the new parent. Reorder-only: ShiftChild. Single-child panels (Border, Button, SizeBox…) gated by `CanHaveMultipleChildren()` (PanelWidget.h:114, inline): insertion index forced to 0 and occupied ⇒ error. Slot properties reset to the new panel's slot defaults — response lists the new slot class so the agent knows to re-apply `Slot.*` via set_property (documented behaviour, not silent).
**Export**: `UMG_API` method-level | **Module**: none — UMG already linked | **Guards**: call sites for InsertChildAt/ShiftChild/ReplaceChildAt inside `#if WITH_EDITOR` (MifBridge is editor-only — satisfied, but the guard must be written).
**Bucket**: transacted — single coherent user-undoable structural edit. Marks the BP structurally modified WITHOUT a full compile (avoids the roadmap-documented compile-per-write cost, :76-77).
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| blueprint | blueprintPath | string | — | yes |
| widget | widgetName, name | string (FName in tree) | — | yes |
| newParent | parent | string | current parent (reorder-only mode) | no |
| index | childIndex | int | append | no |
**Failure modes**: widget not found ⇒ `"widget 'X' not in tree — list_widget_tree to enumerate"`; newParent not a UPanelWidget ⇒ `"'Y' is a <Class>, not a panel — it cannot take children"`; single-child panel occupied ⇒ `"Border 'Y' already has a child — panels of this class hold exactly one"`; widget == newParent or newParent is a descendant of widget ⇒ `"cannot reparent 'X' into its own subtree"`; index > child count ⇒ clamped, echoed in response.
**Cooked**: refuses on pak-mounted UI (no UWidgetBlueprint to edit or save) ⇒ `"cooked WidgetBlueprint — only loose widget blueprints are editable"`.
**Verify**: `list_widget_tree` — the widget's parent/childIndex equal the request; total widget count unchanged (identity preserved, nothing destroyed); bindings count unchanged.
**Score**: U5 E3 R3 → tier 0 (closes roadmap gap "no reparent/reorder")
**Phase-2 verdict**: CONFIRMED — PanelWidget.h:52/:59/:70/:84/:89 verbatim inside the `#if WITH_EDITOR` block (:61-93) exactly as flagged; `CanHaveMultipleChildren` :114 inline; WidgetTree.h:46/:67 verbatim. Guard requirement correctly stated by Phase 1. Slot-reset behaviour is documented, not silent — passes the parameter-ignore rule.

### rename_widget
**Purpose**: Rename a widget preserving identity — variable references in graphs, delegate bindings, animation bindings — where today the only route (remove+add) silently breaks all three.
**Engine API** (the UI-locked original and the exported pieces its core uses):
```cpp
static bool RenameWidget(TSharedRef<class FWidgetBlueprintEditor> BlueprintEditor, const FName& OldObjectName, const FString& NewDisplayName); // Editor/UMGEditor/Public/WidgetBlueprintEditorUtils.h:34 — UI-locked, NOT called
static UNREALED_API void ReplaceVariableReferences(UBlueprint* Blueprint, const FName OldName, const FName NewName); // Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h:1053
UNREALED_API FKismetNameValidator(const class UBlueprint* Blueprint, FName InExistingName = NAME_None, const UStruct* InScope = nullptr); // Editor/UnrealEd/Public/Kismet2/Kismet2NameValidators.h:86
UNREALED_API virtual EValidatorResult IsValid( const FName& Name, bool bOriginal = false) override;                  // Kismet2NameValidators.h:94
```
Core replicated from the reference implementation READ at `Editor/UMGEditor/Private/WidgetBlueprintEditorUtils.cpp:277-433`: validate via FKismetNameValidator, then `Widget->SetDisplayLabel` + `Widget->Rename`, then `FBlueprintEditorUtils::ReplaceVariableReferences`, then patch `Blueprint->Bindings[].ObjectName` (`FDelegateEditorBinding`, public) and every `UWidgetAnimation::AnimationBindings[].WidgetName` (public UPROPERTY, WidgetAnimation.h:135). The editor-preview steps (GetReferenceFromTemplate/ReplaceDesiredFocus) are skipped — no editor is open; if one IS open, refuse (see failure modes) rather than desync it.
**Export**: all called symbols `UNREALED_API`/`UMG_API` (UnrealEd + UMG already linked); `UWidgetBlueprint` is class-level `UMGEDITOR_API` (WidgetBlueprint.h:241, UMGEditor already linked) | **Module**: none new | **Guards**: none beyond editor-only module context
**Bucket**: transacted — multi-object but single logical edit; matches the engine's own FScopedTransaction in the reference (cpp:305).
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| blueprint | blueprintPath | string | — | yes |
| widget | oldName, widgetName | string | — | yes |
| newName | name | string | — | yes (strict) |
**Failure modes**: name collision ⇒ `"'NewName' is already used in this Blueprint (FKismetNameValidator: <result>)"`; widget BP currently open in an editor tab ⇒ `"close the Widget Blueprint editor for this asset first — renaming under an open designer desyncs the preview"` (check via UAssetEditorSubsystem::FindEditorForAsset); widget not found ⇒ list_widget_tree hint.
**Cooked**: refuses (no UWidgetBlueprint on pak-mounted UI).
**Verify**: `list_widget_tree` shows newName at same parent/childIndex; `find_nodes` for variable-get nodes of the widget returns the new name; `compile` reports 0 errors; animation binding WidgetNames (get_property on the animation) match newName.
**Score**: U4 E2 R2 → tier 1 (prevents documented identity-loss failure, roadmap :74-75)
**Phase-2 verdict**: CORRECTED — the reference implementation actually spans WidgetBlueprintEditorUtils.cpp:277-433 (Phase 1 cited :277-395, cutting the tail off mid-loop); citation fixed. A faithful headless replica must ALSO include the tail steps: rename the animation's `FMovieScenePossessable` via `MovieScene->FindPossessable(guid)->SetName(...)` when patching AnimationBindings (cpp:400-404, else Sequencer UI shows the stale name), rename widget Navigation bindings via `Navigation->TryToRenameBinding(Old, New)` (cpp:415-422), `FBlueprintEditorUtils::ValidateBlueprintChildVariables` (cpp:425), and `MarkBlueprintAsStructurallyModified` (cpp:428). The engine also routes the new name through `SanitizeWidgetName` before validation (cpp:292). Exported-symbol claims re-verified: WidgetBlueprintEditorUtils.h:34 UI-locked signature confirmed; ReplaceVariableReferences UNREALED_API at BlueprintEditorUtils.h:1053; FKismetNameValidator ctor :86 / IsValid(FName) :94 method-level UNREALED_API.

### create_widget_animation
**Purpose**: Add a UWidgetAnimation to a Widget Blueprint — the container that makes UMG animation authoring possible at all (roadmap module table row "MovieScene + MovieSceneTracks | UMG widget animations", :107).
**Engine API** (recipe verbatim from the engine's own creation path, READ at `Editor/UMGEditor/Private/TabFactory/AnimationTabSummoner.cpp:589-613`):
```cpp
UWidgetAnimation* NewAnimation = NewObject<UWidgetAnimation>(WidgetBlueprint, FName(), RF_Transactional); // AnimationTabSummoner.cpp:589
NewAnimation->MovieScene = NewObject<UMovieScene>(NewAnimation, NewFName, RF_Transactional);              // :604
NewAnimation->MovieScene->SetDisplayRate(FFrameRate(20, 1));                                              // :607
NewAnimation->MovieScene->SetPlaybackRange(TRange<FFrameNumber>(InFrame.FrameNumber, OutFrame.FrameNumber+1)); // :611
Blueprint->Animations.Add(WidgetAnimation);                                                               // AnimationTabSummoner.cpp:260 / WidgetBlueprint.h:256
```
`UWidgetAnimation::MovieScene` and `AnimationBindings` are public UPROPERTYs (Runtime/UMG/Public/Animation/WidgetAnimation.h:131/:135) — direct member writes, no unexported calls. Name validation via FKismetNameValidator (exported, cited above) replaces the UI's VerifyAnimationRename. A compile afterwards materialises the animation property on the generated class — the endpoint reports `requiresCompile: true` and the agent runs the existing `compile` endpoint (composition, not duplication).
**Export**: `UMG_API`/`MOVIESCENE_API` method-level; public data members | **Module**: `MovieScene` (NEW — SetDisplayRate/SetPlaybackRange); UMG/UMGEditor already linked | **Guards**: `UWidgetBlueprint::Animations` is declared inside `#if WITH_EDITORONLY_DATA` (WidgetBlueprint.h:247) — always defined for MifBridge's editor target, but the member write belongs under that guard (Phase-2 correction)
**Bucket**: transacted — contained object creation inside an existing asset, mirrors the engine's own transaction.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| blueprint | blueprintPath | string | — | yes |
| name | animationName | string | — | yes (strict) |
| lengthSeconds | duration | float | 5.0 | no (engine default OutTime=5.0, :578) |
| displayRate | fps | number | 20 (engine default, :607) | no |
**Failure modes**: duplicate name ⇒ FKismetNameValidator error text; not a WidgetBlueprint ⇒ class-named error; lengthSeconds <= 0 ⇒ error.
**Cooked**: refuses (UWidgetBlueprint required).
**Verify**: `get_property` objectPath=`<BP>` path=`Animations` length +1; `describe_sequence` on `WidgetBP:AnimName` reports displayRate 20/1 and playbackRange [0, lengthSeconds*20).
**Score**: U4 E3 R3 → tier 1
**Phase-2 verdict**: CORRECTED — Guards field was "none"; `Blueprint->Animations` sits inside `#if WITH_EDITORONLY_DATA` (WidgetBlueprint.h:247-256), field fixed in place. Whole recipe re-read verbatim: AnimationTabSummoner.cpp :589 (NewObject), :604 (MovieScene NewObject), :607 (SetDisplayRate 20/1), :611 (SetPlaybackRange), :578 (OutTime=5.0 default), Animations.Add at :260 AND :277 both confirmed. Note the engine also calls `SetDisplayLabel(UniqueName)` + `Rename(*UniqueName)` on the animation (:601-602) — include both so the display name matches the object name.

### widget_animation_bind
**Purpose**: Bind a named widget (or widget slot) into a widget animation and return the possessable GUID — after this, sequence_add_track / sequence_add_section / sequence_set_keys work on the animation unchanged (UWidgetAnimation IS a UMovieSceneSequence, WidgetAnimation.h:21 — double-duty verified by class hierarchy, not assumed).
**Engine API**:
```cpp
MOVIESCENE_API FGuid AddPossessable(const FString& Name, UClass* Class);                   // Runtime/MovieScene/Public/MovieScene.h:432
UPROPERTY() TArray<FWidgetAnimationBinding> AnimationBindings;                             // Runtime/UMG/Public/Animation/WidgetAnimation.h:135 (public — direct append)
FName WidgetName; FName SlotWidgetName; FGuid AnimationGuid; bool bIsRootWidget = false;   // Runtime/UMG/Public/Animation/WidgetAnimationBinding.h:22/25/28/31
```
NOTE: `UWidgetAnimation::BindPossessableObject` (the virtual the Sequencer UI uses) CastCheckeds its Context to a live preview UUserWidget (Runtime/UMG/Private/Animation/WidgetAnimation.cpp:155-157) — the headless bridge has none, so the binding struct is appended directly, exactly mirroring what that function writes (cpp:162-198, read): widget case `{AnimationGuid, WidgetName}`, slot case `SlotWidgetName` = the UPanelSlot OBJECT's FName (`PossessedSlot->GetFName()`, cpp:182 — NOT the parent panel's name; Phase-2 correction) and `WidgetName` = the slot's Content widget FName (cpp:183). At runtime only `SlotWidgetName != NAME_None` matters — resolution finds the widget by WidgetName then takes its `Slot` (WidgetAnimationBinding.cpp:21-31) — but the editor mirror should still write the real slot object name so the Sequencer UI displays it correctly.
**Export**: `MOVIESCENE_API` + public data members | **Module**: `MovieScene` (NEW) | **Guards**: none
**Bucket**: transacted.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| blueprint | blueprintPath | string | — | yes |
| animation | animationName | string | — | yes |
| widget | widgetName | string (must exist in WidgetTree — validated via FindWidget, WidgetTree.h:30) | — | yes |
| bindSlot | slot | bool | false | no (true ⇒ binds the widget's PARENT slot for Canvas/Overlay slot animation) |
**Failure modes**: widget not in tree ⇒ list_widget_tree hint; already bound ⇒ return existing GUID `"alreadyBound": true`; animation name not in `Animations` ⇒ error listing existing names.
**Cooked**: refuses.
**Verify**: `describe_sequence` on the animation — possessable count +1 and the binding row present (WidgetName, GUID) in its output.
**Score**: U4 E3 R3 → tier 1
**Phase-2 verdict**: CORRECTED — the slot-binding detail was wrong: the engine writes `SlotWidgetName = PossessedSlot->GetFName()` (the UPanelSlot object's name, WidgetAnimation.cpp:182), not the parent panel's name; NOTE fixed in place with the runtime-resolution evidence (WidgetAnimationBinding.cpp:21-31). Double-duty premise re-verified: `UWidgetAnimation : UMovieSceneSequence` at WidgetAnimation.h:21, MovieScene/AnimationBindings public UPROPERTYs at :131/:135, AddPossessable MOVIESCENE_API at MovieScene.h:432 — all verbatim.

### create_input_action / create_input_mapping_context
**Purpose**: Mint UInputAction / UInputMappingContext assets — closes the roadmap coverage gap "no non-Blueprint asset creation (… no InputAction)" (docs/06_CAPABILITY_ROADMAP.md:69-70) for the input family DDS2 actually uses (DefaultInput.ini:81-82 sets EnhancedPlayerInput/EnhancedInputComponent).
**Engine API**:
```cpp
class ENHANCEDINPUT_API UInputAction : public UDataAsset                                    // Plugins/EnhancedInput/Source/EnhancedInput/Public/InputAction.h:53
EInputActionValueType ValueType = EInputActionValueType::Boolean;                           // InputAction.h:114
class ENHANCEDINPUT_API UInputMappingContext : public UDataAsset                            // Plugins/EnhancedInput/Source/EnhancedInput/Public/InputMappingContext.h:22
```
Both are plain UDataAssets: `NewObject<UInputAction>(Package, Name, RF_Public|RF_Standalone|RF_Transactional)` + `FAssetRegistryModule::AssetCreated` — the same minting pattern as create_level_sequence. `valueType` param maps to the `ValueType` UPROPERTY. The exported editor factories exist (`INPUTEDITOR_API UInputAction_Factory`, InputEditorModule.h:80) but are NOT used — their ConfigureProperties opens a modal (see Negative results).
**Export**: class-level `ENHANCEDINPUT_API` | **Module**: `EnhancedInput` (Runtime module, plugin EnabledByDefault true — verified .uplugin:13; NOT disabled by uproject) | **Guards**: none
**Bucket**: self-managed — new package/asset registration, same as create_blueprint precedent.
**Async**: no
**Params** (both endpoints): | name | aliases | type | default | required |
|---|---|---|---|---|
| packagePath | path | string | — | yes (strict) |
| name | assetName | string | — | yes |
| valueType | type | string enum `Boolean\|Axis1D\|Axis2D\|Axis3D` | Boolean | no (create_input_action only; unknown value ⇒ error listing the accepted set; see UNVERIFIED on enum spellings) |
| save | — | bool | false | no |
**Failure modes**: existing asset at path ⇒ same message as create_level_sequence; valueType passed to create_input_mapping_context ⇒ `"unrecognised parameter 'valueType' — this endpoint has none"` (parameter-ignore rule).
**Cooked**: creates new loose assets only; refuses pak-mounted target paths.
**Verify**: `find_assets class=InputAction` +1 (Phase-2: live param is `class`, not `className`); `get_property path=ValueType` echoes the enum; `list_object_properties` on the new asset shows the full UDataAsset shape.
**Score**: U3 E4 R3 → tier 1 (two endpoints, one shared implementation)
**Phase-2 verdict**: CONFIRMED — class-level ENHANCEDINPUT_API on both UDataAsset subclasses verbatim (InputAction.h:53, InputMappingContext.h:22); plugin EnabledByDefault re-verified (.uplugin:13, not disabled by uproject). The UNVERIFIED enum caveat is now RESOLVED: `EInputActionValueType` literals are exactly `Boolean, Axis1D, Axis2D, Axis3D` (EnhancedInput/Public/InputActionValue.h:10-19) — the valueType whitelist in the params table is safe to hardcode as written. Live census: 62 InputActions / 5 IMCs visible to the bridge.

### input_map_key
**Purpose**: Append a key-to-action mapping to an InputMappingContext — the one structural edit set_property cannot express safely (Mappings is an array of structs holding instanced Modifier/Trigger objects; whole-array rewrite via set_property would drop them).
**Engine API**:
```cpp
FEnhancedActionKeyMapping& MapKey(const UInputAction* Action, FKey ToKey);                  // Plugins/EnhancedInput/Source/EnhancedInput/Public/InputMappingContext.h:62
void UnmapKey(const UInputAction* Action, FKey Key);                                        // InputMappingContext.h:68
void UnmapAllKeysFromAction(const UInputAction* Action);                                    // InputMappingContext.h:78
struct ENHANCEDINPUT_API FEnhancedActionKeyMapping                                          // EnhancedActionKeyMapping.h:82 — Triggers :134, Modifiers :144, Action :148, Key :152
```
Key resolved via `FKey(FName)` + EKeys validity check (InputCore, transitive). `remove=true` routes to UnmapKey / UnmapAllKeysFromAction. Modifiers/Triggers: the returned `FEnhancedActionKeyMapping&` allows immediate instancing from `modifierClasses[]`/`triggerClasses[]` string params (classes resolved by reflection, NewObject outered to the context asset, appended to the row's arrays).
**Export**: class-level `ENHANCEDINPUT_API` | **Module**: `EnhancedInput` (NEW) | **Guards**: none
**Bucket**: transacted — bounded data edit on a loaded asset.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| context | contextPath, imc | string (UInputMappingContext path) | — | yes |
| action | actionPath, ia | string (UInputAction path) | — | yes |
| key | — | string (FKey name, e.g. `SpaceBar`, `Gamepad_FaceButton_Bottom`) | — | yes unless remove+all |
| remove | unmap | bool | false | no |
| all | allKeys | bool | false | no (with remove: UnmapAllKeysFromAction) |
| modifierClasses | modifiers | string[] (UInputModifier subclasses, e.g. `InputModifierNegate`) | [] | no |
| triggerClasses | triggers | string[] (UInputTrigger subclasses) | [] | no |
**Failure modes**: invalid key name ⇒ `"key 'SpaceBarr' is not a valid FKey — see EKeys (did you mean SpaceBar?)"`; action asset wrong class ⇒ named error; modifier class not a UInputModifier subclass ⇒ error naming it; remove of a mapping that does not exist ⇒ `"no mapping of <action> to <key> in <context>"`.
**Cooked**: in-memory edit of a pak-mounted IMC would be unsaveable ⇒ refuse with duplicate_asset hint.
**Verify**: `list_input_mappings` — mapping count +1 and the (action, key) pair present with modifier/trigger class names; after remove, count -1.
**Score**: U4 E4 R3 → tier 1
**Phase-2 verdict**: CONFIRMED — MapKey :62 / UnmapKey :68 / UnmapAllKeysFromAction :78 verbatim (UFUNCTION BlueprintCallable on a class-level ENHANCEDINPUT_API class); FEnhancedActionKeyMapping struct ENHANCEDINPUT_API :82 with Triggers :134 / Modifiers :144 / Action :148 / Key :152 all verbatim, Instanced UPROPERTYs as the whole-array-rewrite argument requires. Header note at InputMappingContext.h:56 says Map/Unmap are "intended for use in the config/binding screen only" — an editor-time caveat that matches this exact use case, no hazard.

### list_input_mappings
**Purpose**: Read back an InputMappingContext: every (action, key, modifiers[], triggers[]) row — verification twin of input_map_key and the discovery tool for the game's existing cooked IMCs.
**Engine API**:
```cpp
const TArray<FEnhancedActionKeyMapping>& GetMappings() const { return Mappings; }           // InputMappingContext.h:53 (inline)
```
Row fields read from `FEnhancedActionKeyMapping` (Action :148, Key :152, Triggers :134, Modifiers :144 — EnhancedActionKeyMapping.h).
**Export**: inline accessor on `ENHANCEDINPUT_API` class | **Module**: `EnhancedInput` (NEW) | **Guards**: none
**Bucket**: read-only.
**Async**: no
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| context | contextPath, imc | string | — | yes |
**Failure modes**: wrong class / unloadable ⇒ named errors as above.
**Cooked**: WORKS — UDataAssets cook intact; reading the base game's pak-mounted IMCs is a primary use (discover what the game binds before adding mod inputs).
**Verify**: self-verifying; count matches the asset's Mappings array length shown by list_object_properties.
**Score**: U3 E5 R5 → tier 1
**Phase-2 verdict**: CONFIRMED — GetMappings inline at InputMappingContext.h:53 verbatim; row-field citations verified. Live census confirms the discovery use-case: 5 InputMappingContexts currently visible to the bridge (cooked+loose).

## Compositions (no new endpoint needed)

- **Sequence playback-range / display-rate edits after creation** — `set_property` on `<Sequence>.MovieScene` sub-properties, or recreate; if demand appears, a `sequence_set_range` is a 10-line endpoint over `SetPlaybackRange` (MovieScene.h:973). Not proposed now.
- **Widget slot layout after reparent** — already covered: `set_property` with the documented `Slot.*` object paths (roadmap :121 documents this as an existing-but-undocumented capability). reparent_widget intentionally returns the new slot class to feed this.
- **InputAction tuning (ValueType, bConsumeInput, Triggers/Modifiers on the ACTION asset)** — plain `set_property` on the asset; arrays of instanced objects hit the known element-addressing gap (roadmap :67-68) — flagged there, not solvable on this axis.
- **Widget animation playback preview** — `sequence_editor_play` does NOT apply (the Sequencer library is level-sequence-only: `GetCurrentLevelSequence` returns ULevelSequence, LevelSequenceEditorBlueprintLibrary.h:58); previewing a widget animation headlessly = run PIE + capture_camera (existing endpoints). No endpoint proposed.
- **Deleting sequencer objects** — RemovePossessable/RemoveSpawnable (MovieScene.h:447/:390) and RemoveWidget (WidgetTree.h:43) are exported; a `sequence_remove_binding` / widget delete can be added later as trivially as the adds; delete_asset covers whole-asset removal today.
- **PIE-time sequence playback** — `ULevelSequencePlayer::CreateLevelSequencePlayer` (LevelSequencePlayer.h:119, `static LEVELSEQUENCE_API`) is verified and exported, but a dedicated endpoint needs PIE-world plumbing + lifetime management; deferred to the PIE axis. Compose today: spawn a LevelSequenceActor via spawn_actor_in_level, set bAutoPlay via set_property, then start_pie.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

1. **SequencerScripting plugin is a dead end for MifBridge** (checked thoroughly since it is nominally THE scripting surface): the plugin is NOT enabled (no `EnabledByDefault` key in `D:/UE532/Engine/Plugins/MovieScene/SequencerScripting/SequencerScripting.uplugin` ⇒ defaults false; absent from the .uproject), its .uplugin declares a `PythonScriptPlugin` dependency, every `UMovieScene*Extensions` class is `UCLASS()` with **no export macro** (e.g. `MovieSceneSequenceExtensions.h` UCLASS at the class decl, `MovieSceneBindingExtensions.h:17-18`) so nothing links directly, and the typed channel wrappers (`UMovieSceneScriptingFloatChannel` and 9 siblings) sit in **Private/** headers (`Source/SequencerScripting/Private/KeysAndChannels/*.h`). Reflection-calling them would require enabling the plugin project-wide. Verdict: bypass — every capability it wraps is reachable through exported `MOVIESCENE_API` symbols (see proposals), at zero plugin cost. The extensions enumeration (69 UFUNCTIONs in MovieSceneSequenceExtensions, 21 in Binding, 23 in Section, 13 in Track) remains useful as a FEATURE CHECKLIST for phase-2.
   **Phase-2: OVERTURNED — the enablement premise is factually wrong.** SequencerScripting IS enabled in this project's editor, transitively: `LevelSequenceEditor.uplugin` (EnabledByDefault true, :13 — verified) declares `"Plugins": [{"Name": "SequencerScripting", "Enabled": true}]` (:25-30, read); `ControlRig.uplugin` (:38-39) and `MovieRenderPipeline.uplugin` declare the same, and the .uproject disables none of them (grepped — zero `"Enabled": false` entries). Compiled binaries exist and load: `Engine/Plugins/MovieScene/SequencerScripting/Binaries/Win64/UnrealEditor-SequencerScripting.dll` (+Editor module DLL). Consequences: (a) "would require enabling the plugin project-wide" is wrong — it already is; (b) the ~126 extension UFUNCTIONs (all `UFUNCTION(BlueprintCallable)` on registered UCLASSes) are callable TODAY via reflection (`FindObject<UClass>("/Script/SequencerScripting.MovieSceneSequenceExtensions")` → FindFunction/ProcessEvent), including the typed key/channel wrappers in Private/KeysAndChannels (their UCLASSes are registered even though the headers aren't includable). What STANDS from the original finding (re-verified): no export macros on any extension class (`UCLASS()` at MovieSceneSequenceExtensions.h:25-26, MovieSceneBindingExtensions.h:17-18) ⇒ no direct C++ linking, and Private headers ⇒ no typed compile-time access. So the exported-MOVIESCENE_API design below is still the better engineering route for MifBridge (typed, compile-checked, no reflection marshalling), but SequencerScripting is a fallback/cross-check surface, NOT a dead end — and 03_GAPS_AND_RISKS.md must not record it as one.
2. **ULevelSequenceFactoryNew is unexported and private** — `Plugins/MovieScene/LevelSequenceEditor/Source/LevelSequenceEditor/Private/Factories/LevelSequenceFactoryNew.h:11-23`, plain `UCLASS(BlueprintType, hidecategories=Object)` with no module export macro. Non-blocking: its entire FactoryCreateNew is 3 exported calls (read at .cpp:29-41) which create_level_sequence replicates. **Phase-2: CONFIRMED** — header re-read in full (:11-23, no export macro) and .cpp:28-40 recipe matches verbatim.
3. **FWidgetBlueprintEditorUtils::RenameWidget is UI-locked** — signature takes `TSharedRef<FWidgetBlueprintEditor>` (`Editor/UMGEditor/Public/WidgetBlueprintEditorUtils.h:34`); the editor instance is used for preview-widget rename and desired-focus fixup (cpp:312-317, :375). Core is replicable from exported pieces (see rename_widget). Risk carried: future engine versions may add steps to the reference implementation — pinned to 5.3.2 here. **Phase-2: CONFIRMED** — :34 signature verbatim (TSharedRef<FWidgetBlueprintEditor> first param); note the full reference implementation runs :277-433, see the corrected rename_widget entry for the tail steps a replica must include.
4. **UWidgetAnimation::BindPossessableObject requires a live preview widget** — `CastChecked<UUserWidget>(Context)` (`Runtime/UMG/Private/Animation/WidgetAnimation.cpp:157`) asserts on a null/wrong context; headless callers must write `AnimationBindings` directly (public UPROPERTY — safe, but a struct-shape dependency on FWidgetAnimationBinding, WidgetAnimationBinding.h:17-31). **Phase-2: CONFIRMED** — `CastChecked<UUserWidget>(Context)` at cpp:157 re-read; struct fields verbatim at :22/:25/:28/:31. One detail in the mirror recipe corrected (SlotWidgetName = slot object's FName, see widget_animation_bind verdict).
5. **UInputMappingContext_Factory / UInputAction_Factory ConfigureProperties opens a modal class-picker** (`Plugins/EnhancedInput/Source/InputEditor/Public/InputEditorModule.h:59-90`, `virtual bool ConfigureProperties() override`) — calling it from a mid-frame handler would hang the HTTP pump behind a modal. FactoryCreateNew itself is callable, but the factories add nothing over NewObject for UDataAssets; the InputEditor module dependency is avoided entirely. **Phase-2: CONFIRMED with hard evidence** — the modal is real: `SClassPickerDialog::PickClass` inside both `UInputMappingContext_Factory::ConfigureProperties` (InputEditorModule.cpp:94-118) and `UInputAction_Factory::ConfigureProperties` (:171-195).
6. **FKismetNameValidator class is unexported** (`class FKismetNameValidator`, Kismet2NameValidators.h:83) — but its ctor and IsValid are method-level `UNREALED_API` (:86, :93-94), so stack construction links fine. Trap for implementers: only the marked methods link; nothing else on the class does. **Phase-2: CONFIRMED** — class decl :83 bare, ctor :86 / IsValid(FString) :93 / IsValid(FName) :94 method-level UNREALED_API (also GetMaximumNameLength :90); rename_widget only calls marked methods.
7. **Sequencer editor scrub/play library is root-sequence-only and frame-int-typed** — `SetCurrentTime(int32)` (LevelSequenceEditorBlueprintLibrary.h:108) operates in display-rate frames of the ROOT sequence; sub-sequence scrubbing needs FocusLevelSequence (:70) with a UMovieSceneSubSection pointer — deferred, not exposed in sequence_editor_play v1. **Phase-2: CONFIRMED** — :108 `SetCurrentTime(int32)` and :70 `FocusLevelSequence(UMovieSceneSubSection*)` verbatim; note the library also has `SetCurrentLocalTime/GetCurrentLocalTime` (:120/:126) if local-frame scrubbing is ever wanted.
8. **Live bridge was unreachable for this sweep** — every curl to 127.0.0.1:8791 (find_assets, pie_status) got connection-refused across the session despite TIME_WAIT evidence of recent bridge activity; the cooked-content asset census (WidgetBlueprint/InputAction/LevelSequence counts) could not be captured and tier evidence fell back to static config + loose-content census (documented in Surface inventory). Phase-2 should re-run: `find_assets className=WidgetBlueprint / InputAction / InputMappingContext / LevelSequence`. **Phase-2: RESOLVED — bridge was reachable and the census ran** (POST /api/find_assets, X-Mif-Token): WidgetBlueprint **54**, InputAction **62**, InputMappingContext **5**, LevelSequence **4** (origin=any). Two live findings while doing it: (a) the endpoint's parameter is **`class`**, not `className` (MifBridgeCooked.cpp:193) — the Verify lines in this file have been corrected; (b) `find_assets` SILENTLY IGNORES unrecognised parameters — `{"className": "..."}` returned all 37131 assets with no error, a live instance of the brief's #1 bug class in an already-shipped endpoint (worth a fix outside this axis).

## UNVERIFIED

- `UMoviePipelinePrimaryConfig` setting-class layout (output-directory / resolution / image-format setting classes under `MovieRenderPipelineCore/Public/Settings/`) — not opened; render_movie_request's config-building step needs a phase-2 read of `MoviePipelineOutputSetting.h` (+ export macro) before implementation. The queue/executor/job spine IS verified.
- ~~`EInputActionValueType` enum literal spellings inferred from the `InputAction.h:114` default only; the enum body lives in `InputActionValue.h`, not opened — verify the four names before hardcoding the valueType whitelist.~~ **Phase-2: RESOLVED** — enum body read at `Plugins/EnhancedInput/Source/EnhancedInput/Public/InputActionValue.h:10-19`: literals are exactly `Boolean`, `Axis1D`, `Axis2D`, `Axis3D`. The whitelist in create_input_action is correct as written.
- Event tracks: whether `UMovieSceneEventTrack` + event-section payloads (idea: `sequence_add_event_key` calling a director-BP function) are practical without SequencerScripting's `UMovieSceneEventTrackExtensions` — the raw payload object model (FMovieSceneEvent + director BP endpoint binding) was not walked; parked for phase-2.
- `UWidgetBlueprintGeneratedClass` widget-tree-archetype accessor name/export for the cooked-degraded path of list_widget_tree — the template-walk claim is sound in principle (the generated class carries a WidgetTree archetype for CreateWidget) but the precise 5.3.2 accessor was not opened; verify before implementing the cooked branch.

## Coverage log

**Phase-2 verification log (2026-07-26)**: all 17 proposed-endpoint sections re-verified against D:/UE532 source — 11 CONFIRMED, 6 CORRECTED (describe_sequence: channel-entry class misattribution; sequence_add_track + create_widget_animation: missing WITH_EDITORONLY_DATA guard facts; render_movie_request: missed FMessageDialog modal in UMoviePipelinePIEExecutor::Start cpp:93/:109 — pre-validation now mandatory; rename_widget: reference-impl range :277-433 + four missing tail steps; widget_animation_bind: SlotWidgetName detail), 0 DEMOTED. Negatives: #2-#7 confirmed (with new hard evidence for #5: SClassPickerDialog at InputEditorModule.cpp:118/:195), **#1 OVERTURNED** (SequencerScripting transitively enabled via LevelSequenceEditor/ControlRig/MovieRenderPipeline plugin references; DLLs present; reflection route live — no-export/Private-header findings stand), #8 resolved with live census (WBP 54 / IA 62 / IMC 5 / LS 4). UNVERIFIED list shrunk by one (EInputActionValueType resolved). Endpoint names checked against the 160-endpoint covered list — no collisions; all snake_case verb_noun. Bucket and async assignments all consistent with the brief's invariants (only multi-frame op is MRQ, which has request+poll).

Covered: LevelSequence module (asset class + factory recipe + player), MovieScene module (UMovieScene bindings/tracks, UMovieSceneTrack sections, UMovieSceneSection channels+ranges, float/double/bool channel key APIs, channel proxy), MovieSceneTracks (3D transform section channel layout, property-track binding), SequencerScripting plugin (all 11 extension headers enumerated, enabled-state + export audit ⇒ negative result #1), LevelSequenceEditor plugin (BlueprintLibrary full walk :41-170, private factory), MovieRenderPipeline plugin (queue subsystem, queue/job classes, PIE executor, enablement chain via DLSSMoviePipelineSupport), UMG (WidgetTree full public API, UPanelWidget child ops incl. the WITH_EDITOR block, UWidgetAnimation + bindings + engine creation recipe, UMG-specific track headers), UMGEditor (UWidgetBlueprint, WidgetBlueprintEditorUtils::RenameWidget + full reference implementation cpp:277-395), UnrealEd (ReplaceVariableReferences, FKismetNameValidator), EnhancedInput plugin (uplugin module map, UInputAction, UInputMappingContext + MapKey family, FEnhancedActionKeyMapping, InputEditor factories), DDS2 config (DefaultInput.ini input classes :81-82, uproject plugin list, loose-content census: 7 IA_* + IMC_Default in CityScooter, 101 GUI uassets, WBP mod content).

Not covered / remaining for phase-2: MRQ settings classes (UNVERIFIED); event tracks & director-blueprint payloads; sub-sequences and shots (UMovieSceneSubSection); camera-cut track authoring (UMovieSceneCameraCutTrack — natural follow-up to sequence_add_track, same object model, needs a CineCameraActor binding sweep); TemplateSequence/ActorSequence/CustomizableSequencerTracks plugins (present in Engine/Plugins/MovieScene, unexamined); Enhanced Input user settings (UEnhancedInputUserSettings — note 5.3 deprecation churn around UPlayerMappableInputConfig, InputEditorModule.h:92); the live-bridge asset census (bridge was down for the whole session).
