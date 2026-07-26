# Axis P3 — Sessions & misc project plugins + GameFeatures state
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._

This axis is judgement-heavy: most of it is firm rule-outs. The single high-value area is the
GameFeatures subsystem (the two DLC game-feature plugins are failing to register today — exact
error captured below from the live editor log).

## Surface inventory

**Engine plugin (read in full or in cited regions):**
- `D:/UE532/Engine/Plugins/Experimental/GameFeatures/` — located here (NOT under Runtime/).
  Read: `Source/GameFeatures/Public/GameFeaturesSubsystem.h` (all 656 lines),
  `Public/GameFeatureTypes.h` (all 73 lines), `Public/GameFeaturePluginOperationResult.h` (all 65 lines),
  `Public/GameFeatureData.h` (class decl), `Public/GameFeaturesSubsystemSettings.h` (grep),
  `Private/GameFeaturePluginStateMachine.h` (grep — private header), `Private/GameFeaturesSubsystem.cpp`
  (cited regions), `GameFeatures.uplugin`, `GameFeatures.Build.cs`.
  Plugin: `"EnabledByDefault": false` in its .uplugin, but **project-enabled** (uproject line 96 `"Name": "GameFeatures"`).
  Modules: `GameFeatures` (Runtime, PreDefault) + `GameFeaturesEditor` (Editor). Marked `"IsBetaVersion": true`.
- `D:/UE532/Engine/Plugins/BlueprintFileUtils/` (top-level engine plugin dir, not Marketplace):
  `BlueprintFileUtils.uplugin`, `Source/BlueprintFileUtils/Public/BlueprintFileUtilsBPLibrary.h`.
- `D:/UE532/Engine/Source/Runtime/Projects/Public/Interfaces/IPluginManager.h` (cited lines) and
  `Runtime/Projects/Private/PluginManager.cpp:409-423` (project-plugin enable rule).
- `D:/UE532/Engine/Source/Editor/UnrealEd/Public/ObjectTools.h:682-715` (ThumbnailTools) and
  `Runtime/Core/Public/Misc/ObjectThumbnail.h` (grep), `Runtime/Engine/Public/ImageUtils.h` (grep).

**Project plugins under D:/DDS2SDK/Game/Plugins/ (each .uplugin read; Source/Binaries/Content listed):**
- `GameFeatures/ChristmasDlc/` (uplugin UTF-16, `"NoCode": true`, `"ExplicitlyLoaded": true`, Content/ EMPTY on disk)
- `GameFeatures/DDS2Casino/` (one Runtime module `DDS2CasinoRuntime` — single stub subsystem class; Content/ EMPTY on disk)
- `AdvancedSessions/`, `AdvancedSteamSessions/` (full REAL source — bodies implemented; 5 BP libraries, ~53 BlueprintCallable statics counted via grep)
- `Plugins_RamaThumb/RamaSaveSystem/` + `Plugins_RamaThumb/ThumbnailGenerator/` (SDK-dump STUB source — see negatives)
- `GamepadVirtualCursor/GamepadVirtualCursor/` (STUB source)
- `Hermes-main/HermesCore/` (uplugin ONLY — no Modules array, no Source, Content empty)
- `RedTalaria-master/` (uplugin ONLY — no Modules, Content empty)
- `DLSS/`, `NIS/`, `Streamline/`, `StreamlineDeepDVC/`, `DLSSMoviePipelineSupport/`, `BugSplat/` (Public/ headers enumerated; stub check on DLSSLibrary.cpp)

**Live-bridge probes (read-only, port 8791):** describe_class GameFeaturesSubsystem / GameFeatureData /
AdvancedSessionsLibrary; find_assets class=GameFeatureData; get_property + list_object_properties on
`/Engine/Transient.UnrealEdEngine_0:GameFeaturesSubsystem_0` and its state machine objects;
list_mounted_containers; pie_status. Plus editor log `D:/DDS2SDK/Game/Saved/Logs/DrugDealerSimulator2.log`
(read from disk).

**Key live evidence:**
- Live subsystem instance addressable at objectPath `/Engine/Transient.UnrealEdEngine_0:GameFeaturesSubsystem_0`;
  `GameFeaturePluginStateMachines` TMap holds exactly the two DLC URLs
  (`.../GameFeatures/ChristmasDlc/ChristmasDlc.uplugin`, `.../DDS2Casino/DDS2Casino.uplugin`).
- `find_assets class=GameFeatureData` → 2 assets: `/ChristmasDlc/ChristmasDlc.ChristmasDlc` and
  `/DDS2Casino/DDS2Casino.DDS2Casino`, both `origin:"container"`, `loaded:false` — the GameFeatureData
  assets live ONLY in the mounted cooked containers; the loose plugin Content folders are empty.
- Editor log (2026-07-26 session, lines 2425-2434): both DLCs end at
  `ErrorRegistering` with
  `ErrorCode=GameFeaturePlugin.StateMachine.Registering.Plugin_Missing_GameFeatureData`, plus warning
  `has no BuiltInInitialFeatureState key, using legacy BuiltInAutoRegister(1)/BuiltInAutoLoad(1)/BuiltInAutoActivate(1)`.
- `describe_class GameFeaturesSubsystem` → functions:[], properties:[] — NO UFUNCTIONs at all, so the
  reflection/ProcessEvent route does NOT exist for this subsystem; a C++ link against the GameFeatures
  module is the only route.

## Proposed endpoints

### get_game_feature_state
**Purpose**: Enumerate every known game-feature plugin with its exact state-machine state (including
error states), so an agent can diagnose why DLC content (ChristmasDlc/DDS2Casino) is not available —
today this fact is only visible by parsing the editor log.
**Engine API**:
```cpp
UCLASS()
class GAMEFEATURES_API UGameFeaturesSubsystem : public UEngineSubsystem
// Engine/Plugins/Experimental/GameFeatures/Source/GameFeatures/Public/GameFeaturesSubsystem.h:326-327

static UGameFeaturesSubsystem& Get() { return *GEngine->GetEngineSubsystem<UGameFeaturesSubsystem>(); }
// GameFeaturesSubsystem.h:337

EGameFeaturePluginState GetPluginState(const FString& PluginURL) const;
// GameFeaturesSubsystem.h:509  (unknown URL => returns EGameFeaturePluginState::UnknownStatus, never fails
//                               — impl at Private/GameFeaturesSubsystem.cpp:1450-1466)

bool GetPluginURLByName(const FString& PluginName, FString& OutPluginURL) const;
// GameFeaturesSubsystem.h:471  (returns false if the plugin is unknown to the GF subsystem)

bool GetGameFeaturePluginInstallPercent(const FString& PluginURL, float& Install_Percent) const;
// GameFeaturesSubsystem.h:435

bool IsGameFeaturePluginActive(const FString& PluginURL, bool bCheckForActivating = false) const;
// GameFeaturesSubsystem.h:438

void GetGameFeatureDataForActivePlugins(TArray<const UGameFeatureData*>& OutActivePluginFeatureDatas);
// GameFeaturesSubsystem.h:404
```
Enumeration of candidate plugins (subsystem has no public "list all" — see Negative results):
```cpp
static PROJECTS_API IPluginManager& Get();
// Runtime/Projects/Public/Interfaces/IPluginManager.h:494
virtual TArray<TSharedRef<IPlugin>> GetDiscoveredPlugins() = 0;
// IPluginManager.h:355
virtual const FString& GetName() const = 0;        // IPluginManager.h:83
virtual bool IsEnabled() const = 0;                // IPluginManager.h:137
virtual FString GetBaseDir() const = 0;            // IPluginManager.h:102
```
Iterate `GetDiscoveredPlugins()`, keep only plugins where `UGameFeaturesSubsystem::GetPluginURLByName(Name, Url)`
returns true — that is exactly the set the GF subsystem tracks. State→string: the natural
`GameFeaturePluginStatePrivate::LexToString` is **unexported** (see Negative results); instead reuse the
public X-macro `GAME_FEATURE_PLUGIN_STATE_LIST(XSTATE)` from
`Engine/Plugins/Experimental/GameFeatures/Source/GameFeatures/Public/GameFeatureTypes.h:9-38` to generate
an identical name table inside MifBridge (compile-time, no link dependency, cannot drift — same macro).
**Export**: `GAMEFEATURES_API` on the class (GameFeaturesSubsystem.h:327) — all methods exported. `PROJECTS_API` on IPluginManager::Get (IPluginManager.h:494). | **Module**: ADD `"GameFeatures"` to MifBridge.Build.cs (Runtime module, plugin project-enabled via uproject line 96). `Projects` already linked. | **Guards**: none (runtime module, no WITH_EDITOR needed).
**Bucket**: read-only — pure query of state machines; no object mutation, no transaction.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| name | plugin, pluginName | string | "" (= list ALL known GF plugins) | no |
| url | pluginUrl | string | "" | no |
Unrecognised parameter ⇒ error naming the parameter. If both `name` and `url` given ⇒ error
`"pass either name or url, not both"`. `name` given but GetPluginURLByName returns false ⇒ error
`"game feature plugin 'X' unknown to GameFeaturesSubsystem; known: [ChristmasDlc, DDS2Casino, ...]"`.
**Returns**: `{count, plugins:[{name, url, state, isErrorState, isActive, installPercent|null,
gameFeatureDataPath|null, baseDir}]}` — `state` is one of the 28 names from
GAME_FEATURE_PLUGIN_STATE_LIST (Uninitialized…Active, incl. ErrorRegistering etc.);
`gameFeatureDataPath` filled from GetGameFeatureDataForActivePlugins when active.
**Failure modes**:
- GameFeatures plugin module not loaded (cannot happen while project-enabled, but guard):
  `"GameFeatures module unavailable — plugin disabled in uproject?"`.
- Unknown `name`: error listing known names (above) — matches ResolveClassStrict precedent.
- Empty result with no error when the project has zero GFPs — return `count:0`, not an error.
**Cooked**: unaffected by cooked content per se — it reads state machines, not assets. NOTE (live-verified
2026-07-26): both DLC GFPs sit in `ErrorRegistering` /
`GameFeaturePlugin.StateMachine.Registering.Plugin_Missing_GameFeatureData` because their
GameFeatureData assets exist only inside the mounted cooked containers (find_assets shows
`origin:"container"`) while the loose plugin Content/ folders are empty. This endpoint is the
diagnostic for exactly that condition.
**Verify**: call with `{}` → `count == 2`, both entries `state == "ErrorRegistering"`,
`isErrorState == true` (current known-broken baseline). Cross-check numerically against
`run_console_captured "ListGameFeaturePlugins -CSV"` (console command registered at
Private/GameFeaturesSubsystem.cpp:291; handler :1805) — row count and state column must match.
`find_assets class=GameFeatureData` count == 2.
**Score**: U4 E4 R5 → tier 1. Closes the "why doesn't my DLC content register" blind spot with zero risk.
**Phase-2 verdict**: CONFIRMED — every citation re-opened against the engine plugin source and exact:
`UCLASS()` + `class GAMEFEATURES_API UGameFeaturesSubsystem : public UEngineSubsystem` h:326-327, inline
Get() :337, GetPluginState :509 with UnknownStatus fall-through impl re-read (GameFeaturesSubsystem.cpp:1450-1466),
GetPluginURLByName :471, GetGameFeaturePluginInstallPercent :435, IsGameFeaturePluginActive :438,
GetGameFeatureDataForActivePlugins :404. X-macro `GAME_FEATURE_PLUGIN_STATE_LIST` at GameFeatureTypes.h:9;
unexported `FString LexToString(EGameFeaturePluginState)` decl :50 / def cpp:99 — the regenerate-from-macro
workaround is sound. IPluginManager cites all exact (Get :494 PROJECTS_API, GetDiscoveredPlugins :355,
GetName :83, IsEnabled :137, GetBaseDir :102). Console cross-check valid: ListGameFeaturePlugins
registered cpp:291, handler cpp:1805. GameFeatures.uplugin `EnabledByDefault:false` + `IsBetaVersion` and
uproject line 96 enablement re-confirmed. Editor log lines 2425-2434 re-read from disk: both DLCs
ErrorRegistering with the legacy BuiltInInitialFeatureState warning, verbatim. Live re-probe this pass:
describe_class GameFeaturesSubsystem → `functions:[], properties:[]` reproduced (bridge now at 165
endpoints; no name collision with any get_game_feature_state/change_game_feature_state_*).

### change_game_feature_state_request (+ change_game_feature_state_status)
**Purpose**: Drive a game-feature plugin to a target state (Installed/Registered/Loaded/Active) and read
back the completion result — e.g. re-attempt Registering after fixing content layout, or Deactivate/Unload
a feature; the completion error code (which today only appears in the log) becomes machine-readable.
**Engine API**:
```cpp
void ChangeGameFeatureTargetState(const FString& PluginURL, EGameFeatureTargetState TargetState, const FGameFeaturePluginChangeStateComplete& CompleteDelegate);
// GameFeaturesSubsystem.h:428

void LoadAndActivateGameFeaturePlugin(const FString& PluginURL, const FGameFeaturePluginLoadComplete& CompleteDelegate);
// GameFeaturesSubsystem.h:425  (equivalent to ChangeGameFeatureTargetState(..., Active, ...); not separately needed)

DECLARE_DELEGATE_OneParam(FGameFeaturePluginChangeStateComplete, const UE::GameFeatures::FResult& /*Result*/);
// GameFeaturesSubsystem.h:159

UENUM(BlueprintType)
enum class EGameFeatureTargetState : uint8
{
	Installed,
	Registered,
	Loaded,
	Active,
	Count	UMETA(Hidden)
};
const FString GAMEFEATURES_API LexToString(const EGameFeatureTargetState GameFeatureTargetState);
void GAMEFEATURES_API LexFromString(EGameFeatureTargetState& Value, const TCHAR* StringIn);
// GameFeaturesSubsystem.h:180-190  (LexFromString is case-insensitive — impl GameFeaturesSubsystem.cpp:150)

struct GAMEFEATURES_API FResult { ... bool HasError() const; FString GetError() const; FText OptionalErrorText; ... };
GAMEFEATURES_API FString ToString(const FResult& Result);
// Engine/Plugins/Experimental/GameFeatures/Source/GameFeatures/Public/GameFeaturePluginOperationResult.h:15-59

void CancelGameFeatureStateChange(const FString& PluginURL);
// GameFeaturesSubsystem.h:464  (available for a future cancel param; not exposed in v1)
```
**Export**: `GAMEFEATURES_API` (class-level, GameFeaturesSubsystem.h:327; struct FResult at GameFeaturePluginOperationResult.h:15; both Lex functions at :189-190). | **Module**: ADD `"GameFeatures"` (same single new dep as get_game_feature_state). | **Guards**: none.
**Bucket**: self-managed — the transition mounts/unmounts pak files, scans or unregisters asset-registry
state, adds/removes primary asset types, and (for Active) runs arbitrary GameFeatureAction objects;
none of that is undoable, and it completes over multiple frames. Wrapping it in the blanket
FScopedTransaction would put non-transactable global changes in the undo stack. Handler opens NO
transaction.
**Async**: request+poll. `change_game_feature_state_request` resolves the URL (via GetPluginURLByName,
or accepts an explicit `url`), rejects if a request for that URL is already pending, calls
ChangeGameFeatureTargetState with a delegate that writes `{done, ok, errorCode, errorText}` into a
bridge-side `static TMap<FString /*url*/, FMifGfsResult>` latch (game-thread only — delegate fires on
the game thread, same thread as handlers, no locking needed), and returns immediately with
`{requested:true, url, targetState}`. `change_game_feature_state_status` (read-only bucket) returns
`{pending, done, ok, errorCode|null, errorText|null, currentState, targetState}` where `currentState`
is a fresh GetPluginState call. Latch entry cleared on next request for the same URL.
**Params** (request):
| name | aliases | type | default | required |
|---|---|---|---|---|
| name | plugin, pluginName | string | — | yes, unless url given |
| url | pluginUrl | string | — | yes, unless name given |
| targetState | state, target | string enum: Installed \| Registered \| Loaded \| Active (case-insensitive, via exported LexFromString) | — | yes |
Params (status): `name`/`url` as above (one required). Unrecognised parameter ⇒ error naming it.
Empty `targetState` ⇒ error `"targetState required: one of Installed|Registered|Loaded|Active"`.
**Failure modes**:
- Unknown plugin name ⇒ error listing known GFP names (strict resolution).
- Invalid targetState string ⇒ error naming the four accepted values (LexFromString leaves value
  unchanged on no match — pre-validate against the four strings, do not silently default).
- Transition already pending for this URL ⇒ error `"state change already pending for 'X'; poll change_game_feature_state_status"`.
- Completion failure ⇒ surfaced in status as e.g.
  `errorCode:"GameFeaturePlugin.StateMachine.Registering.Plugin_Missing_GameFeatureData"` (verbatim
  real error from the 2026-07-26 log for both DLCs).
- Editor-context hazard: targetState=Active runs the feature's GameFeatureActions (AddComponents/
  AddCheats/AddWPContent — headers in the same Public/ dir); with a PIE session running these mutate the
  game world. Document: refuse `Active` while `pie_status.running == true` unless `force:true`
  (param: `force` | bool | default false).
**Cooked**: transitions themselves are content-agnostic. For THESE two DLCs, Registering currently fails
(Plugin_Missing_GameFeatureData) because the loose Content/ folders are empty — the endpoint faithfully
reports that; it cannot fix content layout. If the GFD assets are ever placed loose (or the registering
step is pointed at mounted content), the same request succeeds — the endpoint is how you TEST that fix.
**Verify**: request `{name:"ChristmasDlc", targetState:"Registered"}` → poll status until `done:true` →
today expect `ok:false` with the exact errorCode above, and `get_game_feature_state` shows
`state:"ErrorRegistering"` (numbers: 2 plugins, both isErrorState). After a content fix: `ok:true`,
`currentState:"Registered"`, and `list_blueprints`/`find_assets path=/ChristmasDlc/` asset counts go
from 0 loose to >0 registered.
**Score**: U3 E3 R2 → tier 2. State transitions are high-leverage for DLC/mod workflows but Active in an
editor process executes feature actions designed for game runtime — needs the PIE guard and honest docs.
Editor-context honesty: Registered/Loaded are the states an EDITOR agent actually needs (content becomes
scannable/loadable); Active is mostly a PIE-testing convenience.
**Phase-2 verdict**: CORRECTED — two findings. (1) The LexFromString failure claim is wrong: the impl
(GameFeaturesSubsystem.cpp:136-158) does NOT "leave value unchanged on no match" — it defaults ValueOut
to `EGameFeatureTargetState::Count` and fires `ensureAlwaysMsgf(false, ...)` on empty AND on no-match
(the ":150" case-insensitivity cite is exact: `.Equals(StringIn, ESearchCase::IgnoreCase)`), and
ChangeGameFeatureTargetState then `check(TargetPluginState != EGameFeaturePluginState::MAX)` after
mapping (cpp:855-870) — an unvalidated string reaching the engine is an ensure at best and a CRASH via
check() at worst. Pre-validating against the four literal strings is MANDATORY; never call the engine
LexFromString on raw input (drop it from the param table's mechanism note). (2) Hidden synchronous
blocking the entry missed: the Registering transition loads the GameFeatureData via
`GameFeatureDataHandle->WaitUntilComplete(0.0f, false)` (GameFeaturePluginStateMachine.cpp:2226, engine
comment "@todo make this async. For now we just wait") and bundle loads likewise (:2384) — the state
machine can stall the game thread for the full asset-load inside ITS tick. Request+poll design stands
(the HTTP handler never waits) but the docstring must warn the editor may hitch during Registering/Loading.
Also note the completion delegate CAN fire synchronously inside the request call (policy-refusal paths
`CompleteDelegate.ExecuteIfBound(...)` inline, cpp:875-900) — the latch-then-return design already
tolerates this; keep it. All other cites exact: ChangeGameFeatureTargetState :428, delegate :159,
EGameFeatureTargetState UENUM + GAMEFEATURES_API Lex pair :180-190, FResult GAMEFEATURES_API
GameFeaturePluginOperationResult.h:15/:59, CancelGameFeatureStateChange :464(+overload :465),
`Plugin_Missing_GameFeatureData` literal at GameFeaturePluginStateMachine.cpp:2279. Self-managed bucket
justification verified against the mount/scan/action behaviour — correct.

### render_asset_thumbnail
**Purpose**: Render a per-asset thumbnail PNG (the same image the Content Browser shows) so an agent can
visually confirm an asset (mesh, material, texture, blueprint) without loading a level or moving the
viewport camera. (Hunted per mission item 3: the ThumbnailGenerator PLUGIN route is dead — stub DLL, see
Negative results — but the ENGINE route is exported and available.)
**Engine API**:
```cpp
namespace ThumbnailTools
{
	namespace EThumbnailTextureFlushMode { enum Type { NeverFlush = 0, AlwaysFlush, }; }

	UNREALED_API void RenderThumbnail( UObject* InObject, const uint32 InImageWidth, const uint32 InImageHeight, EThumbnailTextureFlushMode::Type InFlushMode, FTextureRenderTargetResource* InRenderTargetResource = NULL, FObjectThumbnail* OutThumbnail = NULL );
	// Editor/UnrealEd/Public/ObjectTools.h:709 (namespace at :682, flush enum at :685-695)

	UNREALED_API FObjectThumbnail* GetThumbnailForObject( UObject* InObject );      // ObjectTools.h:737
	UNREALED_API bool LoadThumbnailFromPackage(const FAssetData& AssetData, FObjectThumbnail& OutThumbnail); // ObjectTools.h:740
}
class FObjectThumbnail  // Runtime/Core/Public/Misc/ObjectThumbnail.h:59 — class unexported, methods CORE_API:
	int32 GetImageWidth() const   // ObjectThumbnail.h:88 (inline)
	int32 GetImageHeight() const  // ObjectThumbnail.h:94 (inline)
	CORE_API const TArray< uint8 >& GetUncompressedImageData() const;  // ObjectThumbnail.h:164
// PNG write (already-proven in-plugin pattern: capture_camera uses UKismetRenderingLibrary::ExportRenderTarget,
// MifBridgeSpatial.cpp:295; for raw bytes use):
ENGINE_API static bool SaveImageByExtension(const TCHAR * Filename, const FImageView & InImage, int32 Quality=0);
// Runtime/Engine/Public/ImageUtils.h:106 (class FImageUtils at :86)
```
**Export**: `UNREALED_API` (function-level, ObjectTools.h:709/737/740); `CORE_API` method-level on
FObjectThumbnail accessors; `ENGINE_API` on FImageUtils::SaveImageByExtension. | **Module**: none —
UnrealEd, Core, Engine all already linked. | **Guards**: none beyond MifBridge being editor-only
(ObjectTools.h is an editor-module header).
**Bucket**: read-only — renders to a transient FObjectThumbnail and writes a file; no UObject mutation,
no undo entry wanted. (Asset gets loaded as a side effect — same as every existing read endpoint that
takes an assetPath.)
**Async**: no. RenderThumbnail is synchronous (enqueues + flushes rendering commands in-frame — same
pattern as capture_camera, MifBridgeSpatial.cpp:231 comment). Default `flush:"never"` to avoid a
texture-streaming stall; `"always"` trades a hitch for non-blurry textures.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| assetPath | path, objectPath | string | — | yes (strict: empty ⇒ error naming param) |
| width | w | int (16..2048) | 256 | no |
| height | h | int (16..2048) | 256 | no |
| flush | flushMode | string enum: never \| always | never | no |
| outputPath | file, out | string | Saved/MifBridge/Thumbnails/<AssetName>.png | no |
Unrecognised parameter ⇒ error naming it. Out-of-range width/height ⇒ error naming param and range.
**Failure modes**:
- Asset not found / fails to load ⇒ `"assetPath 'X' could not be loaded — check find_assets"`.
- No thumbnail renderer registered for the class (e.g. arbitrary UDataAsset) ⇒ RenderThumbnail produces
  an empty/blank image; detect zero-variance pixel buffer and return
  `ok:true, blank:true, note:"no thumbnail renderer for class Y — try LoadThumbnailFromPackage fallback"`;
  implement the ObjectTools.h:740 package-thumbnail fallback for that case.
- Output dir not writable ⇒ error with resolved absolute path.
**Cooked**: degraded, honestly: container-mounted assets load and most (textures, static meshes,
materials with cooked shader permutations) render; cooked UBlueprintGeneratedClass-only Blueprints have
no editor thumbnail renderer input and typically fall back to class icon/blank — report `blank:true`
rather than erroring. `LoadThumbnailFromPackage` fallback does NOT work for container assets (cooked
packages ship without the thumbnail table).
**Verify**: render `/Game/<known static mesh>` at 256x256 → response `{file, width:256, height:256,
bytes>1000, blank:false}`; file exists on disk with those pixel dimensions (numbers). Render the same
asset at 64x64 → bytes strictly smaller. Blank-detection: render a plain UDataAsset → `blank:true`.
**Score**: U3 E3 R3 → tier 2. "Pixels for taste": lets the agent see individual assets; not on the
tier-0 gap list, but the engine route is fully exported and cheap.
**Phase-2 verdict**: CONFIRMED — header cites exact (namespace :682, flush enum :685-695, RenderThumbnail
UNREALED_API :709, GetThumbnailForObject :737, LoadThumbnailFromPackage :740; FObjectThumbnail class
unexported :59 with inline :88/:94 + `CORE_API ... GetUncompressedImageData` :164; FImageUtils is an
UNEXPORTED class :86 with method-level `ENGINE_API static bool SaveImageByExtension` :106 — entry quoted
it correctly at method level). Impl hazard-swept (ObjectTools.cpp:5031-5190): NeverFlush path is canvas
render + ENQUEUE_RENDER_COMMAND + ReadPixelsPtr — a GPU-readback stall only, same class as capture_camera;
`flush:"always"` executes `FlushAsyncLoading()` + waits for shader/asset compilation + 
`IStreamingManager::Get().StreamAllResources()` (cpp:5076-5089) — potentially a MULTI-MINUTE stall on
this project, so the `never` default is load-bearing; say "may block for the full compile queue" in the
param doc. Sharper warning found: `GenerateThumbnailForObjectToSaveToDisk` (the neighbouring API,
cpp:5194+) opens `FScopedSlowTask` + `MakeDialog()` ("Finishing Shader Compilation...", cpp:5218-5219)
— an implementer must call RenderThumbnail directly, NEVER the SaveToDisk variant. Read-only bucket and
sync design stand.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

_Phase-2 spot-verification of ALL negatives (2026-07-26, this pass): GameFeatures internals — zero
UFUNCTION lines in GameFeaturesSubsystem.h re-grepped (0 hits) AND live describe_class reproduced
`functions:[]` this pass, so "no reflection route" is genuine, not a missed BlueprintCallable;
LexToString(EGameFeaturePluginState) export-macro-free at GameFeatureTypes.h:50 ✓; state machine class
:420 / GetCurrentState :471 / CurrentStateInfo :511 all in the Private header ✓; GameFeaturePluginNameToPathMap
raw TMap h:632 ✓. Rule-outs re-checked at source: UAdvancedSessionsLibrary unexported (:29-30) with 38
BlueprintCallable/Pure statics (recounted: exactly 38) — NOTE the reflection route (P2
call_object_function) COULD invoke these; the negative correctly rests on runtime/PIE relevance, not on
linkability, and stands. UAdvancedSteamFriendsLibrary :323-324 unexported ✓; USteamNotificationsSubsystem
ADVANCEDSTEAMSESSIONS_API : UGameInstanceSubsystem :19 ✓. ThumbnailGeneration.h:15/:27 exact and cpp is
55 lines of stubs, DLL Jul 25 09:35 ✓. RamaSaveSystem DLL also Jul 25 09:35 — same local stub-build
batch, which additionally ANSWERS H-axis read_rama_savefile's open "stub-vs-real DLL" question: the
loaded DLL is the stub build, so that H entry's live probe will return FileIOSuccess=false until real
binaries appear. GamepadVirtualCursor stubs re-read (3 empty bodies) ✓. DLSSLibrary QueryDLSSSupport
returns constant `Supported` ✓ (note: a hardwired TRUE, not false — even more misleading than a zero,
reinforcing the negative). BlueprintFileUtilsBPLibrary UCLASS() unexported :10-11 at the engine-plugins
root, function lines :25-:96 spot-matched ✓ (also reflection-callable via call_object_function, but the
redundancy/attack-surface verdict stands). Hermes-main/HermesCore + RedTalaria-master re-listed:
uplugin + empty Content only, no Source/Modules ✓. ChristmasDlc.uplugin UTF-16 with NoCode+ExplicitlyLoaded
re-read ✓; both DLC loose Content/ dirs empty ✓._

**GameFeatures internals that are NOT viable (workarounds exist and are used above):**
- `GameFeaturePluginStatePrivate::LexToString(EGameFeaturePluginState)` — declared with NO export macro
  (`FString LexToString(EGameFeaturePluginState InEnum);`, GameFeatureTypes.h:50, defined in
  Private/GameFeaturesSubsystem.cpp:99) ⇒ cannot link. Workaround: regenerate the identical name table
  from the public `GAME_FEATURE_PLUGIN_STATE_LIST` X-macro (GameFeatureTypes.h:9-38).
- `UGameFeaturePluginStateMachine` — entire class lives in a PRIVATE header
  (`Source/GameFeatures/Private/GameFeaturePluginStateMachine.h:420`) with no export macro;
  `GetCurrentState()` (:471) and `CurrentStateInfo` (:511) unreachable from MifBridge. Route around via
  `UGameFeaturesSubsystem::GetPluginState` (public, exported).
- No public "enumerate all GFPs" API on the subsystem: `GameFeaturePluginNameToPathMap` is a private raw
  TMap (GameFeaturesSubsystem.h:632, not a UPROPERTY — live get_property probe confirms
  `property not found`), and `ListGameFeaturePlugins` is private (GameFeaturesSubsystem.h:603, console
  handler). Workaround used: IPluginManager::GetDiscoveredPlugins × GetPluginURLByName.
  (`GameFeaturePluginStateMachines` IS a UPROPERTY and IS readable today via
  `get_property objectPath=/Engine/Transient.UnrealEdEngine_0:GameFeaturesSubsystem_0` — live-verified —
  but its value is a string blob of URL→object pairs, and the state itself is not reflected:
  `StateProperties` reflects as an empty struct. Reflection route gives the plugin LIST but not STATES.)
- Retroactive per-plugin ERROR DETAIL retrieval: no public API returns the FResult of a transition that
  completed in the past (e.g. the startup auto-load). The error STATE (ErrorRegistering) is readable via
  GetPluginState; the error CODE string is only available (a) in the log, or (b) by capturing the
  completion delegate of a transition the bridge itself initiated. Documented limitation of
  change_game_feature_state_status.
- `EGameFeaturePluginState` is NOT a UENUM (raw enum in a private namespace, GameFeatureTypes.h:41-52) —
  no reflection route for state names.
- `describe_class GameFeaturesSubsystem` (live) ⇒ `functions:[], properties:[]` minus the four UPROPERTYs —
  zero UFUNCTIONs ⇒ NO FindFunction/ProcessEvent route to any subsystem method. C++ link is mandatory.

**Ruled-out plugins (one paragraph each; every future session can skip these):**

- **AdvancedSessions** (`D:/DDS2SDK/Game/Plugins/AdvancedSessions/`, real source, real DLL 648KB, module
  loaded live): `UCLASS() class UAdvancedSessionsLibrary : public UBlueprintFunctionLibrary`
  (Classes/AdvancedSessionsLibrary.h:29-30) — NO export macro, 38 BlueprintCallable/Pure statics
  (KickPlayer :38, GetExtraSettings :53, GetSessionState :57, ...). NOT in the uproject plugin list but
  auto-enabled as a project plugin (rule cited below). Verdict: **negative for endpoints**. Sessions are
  a RUNTIME/PIE concern (create/find/join Steam sessions); a single-editor HTTP bridge cannot host a
  meaningful multiplayer session test, the editor world has no game session (`GetSessionState` ⇒
  NoSession outside PIE), and the classes being unexported means only a reflection call route would work
  anyway. Composition note: `describe_class AdvancedSessionsLibrary` already enumerates the whole surface
  live (verified — full param schemas returned), and PIE-side behaviour is the game's own Blueprint
  logic, exercised by playing, not by bridge calls.
- **AdvancedSteamSessions** (real source; UAdvancedSteamFriendsLibrary unexported
  (AdvancedSteamFriendsLibrary.h:323-324), USteamNotificationsSubsystem IS exported
  `ADVANCEDSTEAMSESSIONS_API` (SteamNotificationsSubsystem.h:19) but is a GameInstance subsystem =
  PIE-lifetime only): same verdict as AdvancedSessions — **negative**; Steam friends/workshop/overlay
  are runtime services, nothing an editor agent needs that reflection doesn't already show.
- **ThumbnailGenerator** (`Plugins_RamaThumb/ThumbnailGenerator/`, uproject line 57 — CONFIRMED this
  folder is what the uproject's ThumbnailGenerator entry resolves to; Mans Isaksson marketplace plugin
  v3.0.9): the callable entry EXISTS —
  `UFUNCTION(BlueprintCallable) static void K2_GenerateThumbnailAsync(...)`,
  `THUMBNAILGENERATOR_API UThumbnailGeneration` (Public/ThumbnailGeneration.h:15,27) — **but the source
  is an SDK-dump STUB**: every function body in Private/ThumbnailGeneration.cpp is empty/`return NULL;`
  (55 lines total for 13 functions), and the editor DLL
  (Binaries/Win64/UnrealEditor-ThumbnailGenerator.dll, 216KB, built 2026-07-25 09:35 in the same batch as
  every other stub DLL) was compiled FROM those stubs. Calling it in-editor does nothing. Verdict:
  **negative — plugin route dead**; the agent-useful capability is delivered instead by the engine-route
  `render_asset_thumbnail` proposal above (ThumbnailTools::RenderThumbnail, UNREALED_API).
- **RamaSaveSystem** (`Plugins_RamaThumb/RamaSaveSystem/`, uproject line 26): same stub pattern —
  URamaSaveLibrary bodies all empty (Private/RamaSaveLibrary.cpp: RamaSave_SaveToFile {} etc.). The real
  save-system code exists only in the shipped game binary; in-editor the module is a reflection shell so
  cooked assets referencing RamaSaveComponent load. Verdict: **negative** — no in-editor behaviour to
  expose; save/load testing happens inside PIE where the game's own (also stubbed in editor!) module
  runs — i.e. Rama save/load CANNOT work in this modkit editor at all. Worth a docs/ note for mod
  authors, not an endpoint.
- **GamepadVirtualCursor** (`GamepadVirtualCursor/GamepadVirtualCursor/`, uproject line 86): stub source
  (Private/VirtualCursorFunctionLibrary.cpp — all bodies empty), 3 BlueprintCallable statics
  (EnableGamepadCursor/DisableGamepadCursor/IsCursorOverInteractableWidget,
  Public/VirtualCursorFunctionLibrary.h:14-21, GAMEPADVIRTUALCURSOR_API). Even if real, it drives the
  SYSTEM cursor from a gamepad at runtime — zero editor-agent relevance. Verdict: **negative**.
- **Hermes-main/HermesCore**: the folder contains ONLY `HermesCore.uplugin` (UTF-16; NO "Modules" array,
  Content/ empty, no Source/, no Binaries/). It is a dead shell — nothing can load from it. (Upstream
  Hermes is an editor URL-protocol plugin — irrelevant here since no module exists.) Not in uproject;
  as a project plugin it would auto-enable (PluginManager.cpp:409-423:
  `return GetLoadedFrom() == EPluginLoadedFrom::Project;` — project plugins are enabled by default even
  with EnabledByDefault unspecified) but enabling an empty descriptor is a no-op. Verdict: **negative — dead folder**.
- **RedTalaria-master**: ONLY `RedTalaria.uplugin` (no Modules, Content/ empty; declares dependency on
  HermesCore, itself dead). CD Projekt's Hermes endpoint collection, shipped here as an empty husk.
  Verdict: **negative — dead folder**.
- **DDS2Casino runtime module** (`GameFeatures/DDS2Casino/Source/DDS2CasinoRuntime/`): single class
  `DDS2CasinoSoundManagerSubsystemBase` (stub). Nothing to expose; the plugin matters only as a
  game-feature state-machine subject (covered by the two GameFeatures endpoints).
- **DLSS / NIS / Streamline / StreamlineDeepDVC / DLSSMoviePipelineSupport / BugSplat**: all six are
  stub reconstructions in this modkit (checked DLSSBlueprint/Private/DLSSLibrary.cpp — every body empty,
  `QueryDLSSSupport` returns a constant). Surfaces enumerated: DLSSLibrary.h, NISLibrary.h,
  StreamlineLibrary.h/StreamlineLibraryDLSSG.h/StreamlineLibraryReflex.h (8 BlueprintCallable statics
  for DLSS-G mode/FPS queries), StreamlineLibraryDeepDVC.h, MoviePipelineDLSSSetting.h,
  BugSplatUtils.h (BUGSPLATRUNTIME_API, 2 functions). Nothing surprising found: even in a real install
  these set upscaler/frame-gen/latency modes — runtime rendering config whose engine-side effect is
  CVars (`r.NGX.*`, `t.Streamline.*`), i.e. already fully reachable via run_console/run_console_captured;
  and here the editor DLLs are no-op stubs anyway. BugSplat is crash reporting config
  (BugSplatEditorSettings UPROPERTYs — reachable via get_property/set_property on the CDO if ever
  needed). Verdict: **all six negative**; one-line why: stub DLLs + CVar-equivalent + not an editor
  authoring concern.
- **BlueprintFileUtils**: located at `D:/UE532/Engine/Plugins/BlueprintFileUtils/` (top-level engine
  plugins dir — not Marketplace, not in D:/DDS2SDK/Game/Plugins), project-enabled via uproject line 53.
  `UCLASS() class UBlueprintFileUtilsBPLibrary : public UBlueprintFunctionLibrary`
  (Public/BlueprintFileUtilsBPLibrary.h:10-11 — NO export macro). 10 functions: FindFiles :25,
  FindRecursive :41, FileExists :50, DirectoryExists :59, MakeDirectory :69, DeleteDirectory :81,
  DeleteFile :85, CopyFile :89, MoveFile :92, GetUserDirectory :96. Verdict: **negative — fully
  redundant**: the MCP agent has native filesystem access on the same machine, and bridge-side file IO
  (delete/copy on arbitrary paths through an HTTP endpoint) is an attack surface, not a capability. The
  plugin exists so GAME Blueprints can do file IO at runtime; zero editor-agent value.

## UNVERIFIED

- Whether `ChangeGameFeatureTargetState(..., Active)` inside this 5.3.2 editor (outside PIE) applies
  GameFeatureActions to the editor world or defers to game-world contexts — the action classes take
  world-context filters (FGameFeatureStateChangeContext::ShouldApplyToWorldContext,
  GameFeaturesSubsystem.h:58) but I did not trace every stock action's editor behaviour. The PIE guard
  in the proposal is designed assuming the worst. Needs one supervised live test.
- `GameFeaturesEditor` module surface (Source/GameFeaturesEditor/ — all headers Private/, e.g.
  SGameFeatureStateWidget.h): assumed nothing linkable; not exhaustively read.
- Whether `LoadThumbnailFromPackage` (ObjectTools.h:740) works for LOOSE-but-unsaved assets — asserted
  behaviour for cooked containers (no thumbnail table) is from cooked-package format knowledge, not a
  live test with this endpoint (endpoint not built yet).
- ChristmasDlc.uplugin lacks `BuiltInInitialFeatureState`; DetermineBuiltInInitialFeatureState
  (GameFeaturesSubsystem.cpp:2003+) falls back to legacy BuiltInAutoRegister/Load/Activate keys (log
  line 2425 confirms) — but whether ADDING that key changes the registration failure was not tested
  (would require editing the DLC uplugin — a mutation, out of scope for this sweep).

## Coverage log

**Covered**: GameFeatures engine plugin (subsystem header 100%, types/result headers 100%, state machine
private header targeted grep, subsystem cpp cited regions, uplugin+Build.cs); ChristmasDlc + DDS2Casino
descriptors and layout; AdvancedSessions/AdvancedSteamSessions (uplugins, all header names, library
class decls + export check, stub-vs-real check ⇒ real, live describe_class); Plugins_RamaThumb both
subplugins (uplugins, headers, stub check ⇒ stub, DLL timestamps); GamepadVirtualCursor (full file list,
stub check); Hermes-main + RedTalaria-master (full file list ⇒ uplugin-only shells); DLSS/NIS/Streamline/
StreamlineDeepDVC/DLSSMoviePipelineSupport/BugSplat (Public/ header sweep + one stub confirmation);
BlueprintFileUtils (located in engine plugins root, header 100%); project-plugin auto-enable rule
(PluginManager.cpp:409-423); ThumbnailTools engine route (ObjectTools.h:682-740, ObjectThumbnail.h,
ImageUtils.h); live probes as listed in Surface inventory; editor log for the DLC registration errors.

**Incident**: mid-sweep (~14:1x local), the bridge stopped listening on 8791 for several minutes —
first failed call was `describe_class {"class":"AdvancedSessionsLibrary"}` (connection-level failure,
HTTP 000, no listener in netstat). The editor came back with a fresh world ("Untitled") and the same
describe_class then succeeded instantly, so the call itself is not implicated with the module already
loaded; cause unknown (editor restart or crash between probes). Phase-2 should not treat
AdvancedSessions reflection as risky without rechecking, but the coincidence is recorded.

**Remains for other axes**: nothing on this axis's list. Adjacent leads surfaced: (a) a docs/ note for
mod authors that RamaSaveSystem is non-functional in the modkit editor (stub DLL) — documentation, not
an endpoint; (b) BugSplatEditorSettings CDO is get/set_property-reachable if crash-report config ever
matters; (c) the GameFeatureAction_* headers (AddComponents/AddCheats/AddWPContent/DataRegistry) would
be the follow-up if per-feature ACTION introspection is ever wanted — reflection over a loaded
GameFeatureData's Actions array via list_object_properties should be tried first.
