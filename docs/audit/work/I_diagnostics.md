# Axis I — Diagnostics and observation
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._

House rule applied throughout: numbers for correctness, pixels for taste. Nothing below wraps
`run_console` — every endpoint calls the C++ API that the equivalent console command itself sits on,
and returns structured, numerically checkable data that the console path cannot.

## Surface inventory

Engine headers read end-to-end or in the cited regions (paths relative to D:/UE532/Engine/Source):

| Area | Files read | What was verified |
|---|---|---|
| Frame timing globals | `Runtime/RenderCore/Public/RenderTimer.h:104-127`; `Runtime/Engine/Private/UnrealEngine.cpp:628-641`; `Runtime/Engine/Public/EngineGlobals.h:13` | GGameThreadTime/GRenderThreadTime/GRHIThreadTime/GSwapBufferTime/GGameThreadWaitTime/GRenderThreadWaitTime all `extern RENDERCORE_API uint32`; GAverageFPS/GAverageMS defined `ENGINE_API` in UnrealEngine.cpp with **no public-header declaration** (engine's own consumers re-extern locally — `Runtime/Engine/Private/Analytics/EngineAnalyticsSessionSummary.cpp:23`); GGPUFrameTime `extern ENGINE_API uint32` in EngineGlobals.h |
| Draw-call counters | `Runtime/RHI/Public/RHIStats.h:64-107` | `GNumDrawCallsRHI` / `GNumPrimitivesDrawnRHI` extern RHI_API arrays; texture-memory stats struct same file |
| UObject/GC counters | `Runtime/CoreUObject/Public/UObject/UObjectArray.h:889-917,1269`; `Runtime/Core/Public/CoreGlobals.h:486-500` | GUObjectArray COREUOBJECT_API + GetObjectArrayNum family (FORCEINLINE); GFrameCounter/GLastGCFrame/GFrameNumber CORE_API |
| Memory stats | `Runtime/Core/Public/GenericPlatform/GenericPlatformMemory.h:110-179,330` | FPlatformMemoryStats fields verbatim; `static CORE_API FPlatformMemoryStats GetStats();` |
| Log redirector | `Runtime/Core/Public/Misc/OutputDeviceRedirector.h:30-150`; `Runtime/Core/Public/Misc/OutputDevice.h:190-213` | AddOutputDevice/RemoveOutputDevice/IsRedirectingTo CORE_API; CanBeUsedOnAnyThread/CanBeUsedOnMultipleThreads contract verbatim |
| Log categories | `Runtime/Core/Public/Logging/LogSuppressionInterface.h` (whole file, 27 lines) | No enumeration API — negative result below |
| Message log | `Developer/MessageLog/Public/MessageLogModule.h` (whole file); `Developer/MessageLog/Public/IMessageLogListing.h:21-134`; `Runtime/Core/Public/Logging/MessageLog.h:69`; `Runtime/Core/Public/Logging/TokenizedMessage.h:17-28,109-192`; listing-name registrations `Editor/UnrealEd/Private/UnrealEdMisc.cpp:460-511`, `Editor/Kismet/Private/BlueprintEditorModule.cpp:234` | Module + listing interfaces; EMessageSeverity enum verbatim; 9 canonical listing names with registration sites |
| Screenshots | `Runtime/Engine/Public/UnrealClient.h:140-234,640-662`; `Runtime/Engine/Public/HighResScreenshot.h` (whole file, 77 lines); `Runtime/Core/Public/CoreGlobals.h:386`; `Editor/UnrealEd/Classes/Editor/EditorEngine.h:1200,2459` | FScreenshotRequest statics ENGINE_API; FHighResScreenshotConfig + GetHighResScreenshotConfig() ENGINE_API; GIsHighResScreenshot CORE_API; RedrawAllViewports / GetActiveViewport UNREALED_API |
| Automation | `Runtime/Core/Public/Misc/AutomationTest.h:55-140,180-310,813-970,1181` (framework class, flags enum, exec-info, test-info); `Developer/AutomationController/Public/IAutomationControllerModule.h` (whole file); `Developer/AutomationController/Public/IAutomationControllerManager.h:159-330` (grep-verified members) | FAutomationTestFramework per-method CORE_API; controller module interface pure-virtual |
| Trace | `Runtime/Core/Public/ProfilingDebugging/TraceAuxiliary.h` (whole file, 225 lines) | Every FTraceAuxiliary static CORE_API; EConnectionType; FOptions |
| PIE path mapping | `Runtime/Engine/Classes/Engine/World.h:953,3266-3271,4050-4089`; `Runtime/Engine/Classes/Engine/Engine.h:406,3323` | `class ENGINE_API UWorld final`; ConvertToPIEPackageName/RemovePIEPrefix/BuildPIEPackagePrefix; FWorldContext::PIEInstance |
| Hashing | `Runtime/Core/Public/Misc/Crc.h:30-41` | MemCrc32 FORCEINLINE over CORE_API function pointer — links |

Plugin source read (paths relative to D:/DDS2SDK/Game/Plugins/MifBridge):

- `Source/MifBridge/MifBridge.Build.cs` (whole file) — confirmed **no RHI, MessageLog, or AutomationController dependency today**.
- `Source/MifBridge/Private/MifBridgePIE.cpp:1-235,300-460` — GetPIEWorld()=GEditor->PlayWorld (:47), CollectPIEWorlds via GEngine->GetWorldContexts (:73), WritePieStateInto (:104), FScopedLogCapture ring device precedent (:152-205), H_list_pie_actors returns live GetPathName (:411).
- `Source/MifBridge/Private/MifBridgeLevel.cpp:130-199` — list_level_actors goes through UEditorActorSubsystem::GetAllLevelActors (:155), not ULevel::Actors directly.
- `Source/MifBridge/Private/MifBridgeSpatial.cpp:224-375` — H_capture_camera is an offscreen ASceneCapture2D + ExportRenderTarget (deliberately NOT the user viewport, no UI, no PIE screen); H_scene_report shape.
- `Source/MifBridge/Private/MifBridgeNodes.cpp:1230-1340` — H_batch wraps ALL ops in one FScopedTransaction (:1269) and adds per-op envelopes; compile-heavy ops refused inside.
- `Source/MifBridge/Private/MifBridgeNodes6.cpp:1-210` — ResolveGenericTarget (:23) and ResolveReadPropertyPath (:58) — the exact helpers get_properties_bulk reuses.
- `Source/MifBridge/Private/MifBridgePipeline.cpp:25-89` — read_modloader_log is a file tail of the out-of-editor UE4SS.log; it does not observe the editor process at all.

Live-bridge probe attempted (`pie_status`, `list_level_actors` limit 1, curl -m 20): **connection refused** at sweep time — the editor bridge was not up. All verification below is header/source-based; the coverage log marks the probes as not run.

## Proposed endpoints

### get_perf_stats
**Purpose**: one read-only call returning the numeric frame-health of the editor process — FPS, per-thread ms, draw calls, primitives, object counts, GC recency, and process memory — so an agent can bracket any mutation with before/after numbers instead of guessing from pixels.
**Engine API**:
```cpp
// Runtime/RenderCore/Public/RenderTimer.h:107-126
extern RENDERCORE_API uint32 GRenderThreadTime;
extern RENDERCORE_API uint32 GRenderThreadWaitTime;
extern RENDERCORE_API uint32 GRHIThreadTime;
extern RENDERCORE_API uint32 GGameThreadTime;
extern RENDERCORE_API uint32 GGameThreadWaitTime;
extern RENDERCORE_API uint32 GSwapBufferTime;

// Runtime/Engine/Private/UnrealEngine.cpp:633-635 (definition; NO public-header declaration — see note)
// We expose these variables to everyone as we need to access them in other files via an extern
ENGINE_API float GAverageFPS = 0.0f;
ENGINE_API float GAverageMS = 0.0f;

// Runtime/Engine/Public/EngineGlobals.h:13
extern ENGINE_API uint32					GGPUFrameTime;

// Runtime/RHI/Public/RHIStats.h:66-67
extern RHI_API int32 GNumDrawCallsRHI[MAX_NUM_GPUS];
extern RHI_API int32 GNumPrimitivesDrawnRHI[MAX_NUM_GPUS];

// Runtime/CoreUObject/Public/UObject/UObjectArray.h:894 (FORCEINLINE members on exported global)
FORCEINLINE int32 GetObjectArrayNum() const
// UObjectArray.h:1269
extern COREUOBJECT_API FUObjectArray GUObjectArray;

// Runtime/Core/Public/GenericPlatform/GenericPlatformMemory.h:330
static CORE_API FPlatformMemoryStats GetStats();
// FGenericPlatformMemoryStats fields (GenericPlatformMemory.h:127-142, verbatim names):
// AvailablePhysical, AvailableVirtual, UsedPhysical, PeakUsedPhysical, UsedVirtual, PeakUsedVirtual

// Runtime/Core/Public/CoreGlobals.h:486,491
extern CORE_API uint64 GFrameCounter;
extern CORE_API uint64 GLastGCFrame;
```
**Export**: RENDERCORE_API / ENGINE_API / RHI_API / COREUOBJECT_API / CORE_API — all verbatim above. GAverageFPS/GAverageMS carry ENGINE_API **on the definition only**; the call site declares `extern ENGINE_API float GAverageFPS;` locally, which is the engine's own pattern (`Runtime/Engine/Private/Analytics/EngineAnalyticsSessionSummary.cpp:23`, `Runtime/Engine/Private/UnrealEngine.cpp:11454`).
**Module**: `RHI` is a NEW dependency (runtime module, always available) — needed only for the two RHIStats externs. Everything else: none — already linked (Core, CoreUObject, Engine, RenderCore).
**Guards**: none. All symbols exist in editor builds unconditionally.
**Bucket**: read-only — pure query of globals; a transaction would push an empty undo entry per poll.
**Async**: no. Reads complete in microseconds on the game thread.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| includePlatformSpecific | — | bool | false | no |
Unrecognised parameter → error naming it.
**Returns** (all numeric): `fpsAverage`, `frameMsAverage`, `gameThreadMs`, `gameThreadWaitMs`, `renderThreadMs`, `renderThreadWaitMs`, `rhiThreadMs`, `gpuFrameMs`, `swapMs` (cycles converted via FPlatformTime::ToMilliseconds), `drawCalls`, `primitivesDrawn` (index 0 + `perGpu` arrays), `uobjectCount`, `uobjectCountMinusPermanent`, `frameCounter`, `framesSinceGC` (GFrameCounter - GLastGCFrame), `memory:{usedPhysical, peakUsedPhysical, availablePhysical, usedVirtual, peakUsedVirtual, availableVirtual}` in bytes.
**Failure modes**:
- Called before engine tick loop has run a frame → thread times are 0; return them as 0 with `"warmedUp": false` rather than erroring ("thread timings are zero until the first frame completes").
- `GNumDrawCallsRHI` is written by the RHI/render thread; game-thread read is last-completed-frame data. Field is documented as `drawCallsFrameLag: 1` in the payload so callers never treat it as this-frame truth.
**Cooked**: works — none of these globals depend on asset form; .pak-mounted content is irrelevant.
**Verify**: call twice 2 s apart while idle: `frameCounter` strictly increases; `fpsAverage` within [1, 1000]; `memory.usedPhysical` > 100 MB. Then `spawn_many` 500 cubes → `drawCalls` rises; `delete_level_actor` them → returns toward baseline. Numbers, no pixels.
**Score**: U4 E4 R5 → tier 1. The agent's frame-health probe; pairs with every heavy mutation on other axes.
**Phase-2 verdict**: CONFIRMED — every citation re-opened and matched verbatim (RenderTimer.h:108-123; UnrealEngine.cpp:633-635; EngineGlobals.h:13; RHIStats.h:66-67; UObjectArray.h:894,1269; GenericPlatformMemory.h:127-142,330; CoreGlobals.h:486,491). GAverageFPS/GAverageMS local-extern pattern verified at both cited engine call sites (EngineAnalyticsSessionSummary.cpp:23, UnrealEngine.cpp:11454); grep of Runtime/Engine/Public and Runtime/Engine/Classes confirms NO public-header declaration exists. Two upgrades from inference to fact: (a) the one-frame-lag claim for GNumDrawCallsRHI is now VERIFIED — it is written once per frame in `FRHICommandListImmediate::ProcessStats()`, comment "Called from RHIBeginFrame" (RHI.cpp:1321-1322, writes at :1368-1369), so a game-thread read is last-completed-frame data as documented; (b) note that GGameThreadTime/GGameThreadWaitTime are computed inside FViewport::Draw (UnrealClient.cpp:1821-1845) — all thread timings go STALE (not zero) when no viewport draws (editor minimized); worth a `staleness` caveat next to `warmedUp` in the payload doc.

### log_tail
**Purpose**: incremental, structured tail of the EDITOR process log (every UE_LOG from any thread) with monotonic sequence ids — an agent polls `sinceSeq` and never re-reads or misses lines; closes the gap left by read_modloader_log, which tails the out-of-editor game's UE4SS.log file only.
**Engine API**:
```cpp
// Runtime/Core/Public/Misc/OutputDeviceRedirector.h:65,72,80
CORE_API void AddOutputDevice(FOutputDevice* OutputDevice);
CORE_API void RemoveOutputDevice(FOutputDevice* OutputDevice);
CORE_API bool IsRedirectingTo(FOutputDevice* OutputDevice);
// class FOutputDeviceRedirector final : public FOutputDevice  (OutputDeviceRedirector.h:51)
// static CORE_API FOutputDeviceRedirector* Get();             (OutputDeviceRedirector.h:58) — this is GLog

// Runtime/Core/Public/Misc/OutputDevice.h:193-204 (the thread contract, verbatim comments)
/** @return whether this output device can be used on any thread. */
virtual bool CanBeUsedOnAnyThread() const
/** @return whether this output device can be used from multiple threads simultaneously without any locking */
virtual bool CanBeUsedOnMultipleThreads() const
```
**Export**: CORE_API on every FOutputDeviceRedirector method (class itself unexported; method-level macros verbatim above). FOutputDevice is subclassed, not linked against beyond virtuals.
**Module**: none — already linked (Core).
**Guards**: none.
**Bucket**: read-only (the endpoint). The ring-buffer device itself is module infrastructure, not an endpoint.
**Async**: no — reading the ring buffer is instant.
**Device lifetime design** (the part that must not be improvised):
- One static `FMifLogRing : public FOutputDevice` owned by the module. `GLog->AddOutputDevice(&Ring)` in `StartupModule()` (GLog exists before any module loads); `GLog->RemoveOutputDevice(&Ring)` in `ShutdownModule()` — removal MUST precede module unload or GLog serializes into freed code. `IsRedirectingTo()` guards double-registration across hot-reload.
- GLog serializes from ANY thread — the contract is the two virtuals above. The device overrides both to return true and takes an `FScopeLock` in `Serialize`, exactly the pattern already proven in-plugin by `FScopedLogCapture` (MifBridgePIE.cpp:152-205, including the same two overrides at :178-179).
- Fixed ring of 8192 entries `{uint64 seq, double time, FName category, uint8 verbosity, FString text}`; `seq` monotonic from a module-lifetime counter; overwrite oldest; count overwritten as `droppedTotal`.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| sinceSeq | since | int (uint64) | 0 | no |
| category | — | string (exact FName match, e.g. "LogBlueprint") | "" = all | no |
| verbosityAtLeast | minVerbosity | string enum: Fatal, Error, Warning, Display, Log, Verbose, VeryVerbose | "VeryVerbose" (= all) | no |
| contains | filter | string substring | "" | no |
| limit | — | int 1..5000 | 200 | no |
Unrecognised parameter → error naming it. Bad verbosity string → error listing accepted values.
**Returns**: `entries:[{seq, time, category, verbosity, text}]`, `lastSeq` (newest seq in buffer), `oldestSeq` (so a caller can detect it missed lines: sinceSeq < oldestSeq ⇒ `gap:true`), `matched`, `truncated`, `categoriesSeen:[..]` (distinct categories currently in the buffer — the honest substitute for global category enumeration, which 5.3.2 does not expose; see negative results).
**Failure modes**:
- `sinceSeq` older than `oldestSeq` → still succeeds but sets `gap:true` and `gapCount` ("lines were overwritten before you polled; increase poll frequency or limit").
- Unknown category → succeeds with 0 matches plus `categoriesSeen` so the caller can self-correct (a typo'd category must not look like silence).
**Cooked**: works — log lines are log lines regardless of asset form.
**Verify**: `run_console` `MifBridge.TestLog` (or any UE_LOG-producing call) → poll `log_tail {sinceSeq: lastSeqBefore}` → exactly the new lines appear, `seq` strictly increasing, count matches. Numeric: `lastSeq - lastSeqBefore >= 1`.
**Score**: U5 E3 R4 → tier 1. Prevents the documented failure class where run_console_captured misses ASYNC output (its own comment, MifBridgePIE.cpp:457-458: "A command that kicks off async work reports nothing here; tail the log instead" — today there is nothing to tail).
**Phase-2 verdict**: CONFIRMED — OutputDeviceRedirector.h re-read: class :51, Get() :58, AddOutputDevice :65, RemoveOutputDevice :72, IsRedirectingTo :80, all method-level CORE_API exactly as claimed. Thread-contract virtuals at OutputDevice.h:196,204 with the quoted comments verbatim at :193-195,:201-203. In-plugin precedent FScopedLogCapture re-read at MifBridgePIE.cpp:152-205 including the two overrides at exactly :178-179 and the FScopeLock-in-Serialize pattern (:166). run_console_captured's async disclaimer comment confirmed verbatim at MifBridgePIE.cpp:457-458. Module claim (Core, already linked) and read-only bucket correct.

### message_log_read
**Purpose**: structured read of editor Message Log listings (MapCheck, PIE, BlueprintLog, LightingResults, …) — the channel where map_check results, PIE warnings, and load errors actually land; today invisible to the agent because they are UI-only.
**Engine API**:
```cpp
// Developer/MessageLog/Public/MessageLogModule.h:49,57 (class FMessageLogModule : public IModuleInterface — no export macro; see Export)
virtual bool IsRegisteredLogListing(const FName& LogName) const;
virtual TSharedRef<class IMessageLogListing> GetLogListing(const FName& LogName);

// Developer/MessageLog/Public/IMessageLogListing.h:47,90 (pure virtual interface)
virtual const TArray< TSharedRef<class FTokenizedMessage> >& GetFilteredMessages() const = 0;
virtual const FName& GetName() const = 0;

// Runtime/Core/Public/Logging/TokenizedMessage.h:130,153
CORE_API FText ToText() const;
CORE_API EMessageSeverity::Type GetSeverity() const;

// Runtime/Core/Public/Logging/TokenizedMessage.h:20-27 (verbatim)
enum Type : int
{
	CriticalError UE_DEPRECATED(5.1, ...) = 0,
	Error = 1,
	PerformanceWarning = 2,
	Warning = 3,
	Info = 4,	// Should be last
};
```
**Export**: FMessageLogModule and IMessageLogListing carry NO export macro — both are reached purely through virtual dispatch after `FModuleManager::LoadModuleChecked<FMessageLogModule>("MessageLog")`, which is the supported pattern for module-interface classes (no symbols to link). FTokenizedMessage methods are method-level CORE_API (verbatim above).
**Module**: `MessageLog` is a NEW dependency — Developer module, editor builds only, always present in-editor (loaded by UnrealEd at startup: it registers the canonical listings in `Editor/UnrealEd/Private/UnrealEdMisc.cpp:460-511`). MifBridge is editor-only, so a Developer-module dep is legal; it must never appear in a runtime module.
**Guards**: none beyond being an editor module.
**Bucket**: read-only — pure query.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| logName | log | string FName, e.g. "MapCheck" | — | YES (strict: empty ⇒ error naming it) |
| severityAtLeast | minSeverity | string enum: Error, PerformanceWarning, Warning, Info | "Info" (= all) | no |
| limit | — | int 1..5000 | 200 | no |
Unrecognised parameter → error. Known-good logName values (registration sites read): `MapCheck`, `PIE`, `LoadErrors`, `LightingResults`, `PackagingResults`, `AssetCheck`, `EditorErrors`, `HLODResults`, `SlateStyleLog` (UnrealEdMisc.cpp:460-511), `BlueprintLog` (BlueprintEditorModule.cpp:234), `AnimBlueprintLog` (AnimationBlueprintEditorModule.cpp:59).
**Failure modes**:
- Unregistered logName → error `"log listing 'X' is not registered — known listings: MapCheck, PIE, ..."`. This check matters: `GetLogListing` silently CREATES a listing if absent (its own doc comment, MessageLogModule.h:52: "If it does not exist it will created"), so calling it blind would fabricate empty logs and report 0 messages for a typo — the endpoint must probe `IsRegisteredLogListing` first.
- Listing exists but empty → `count: 0`, not an error.
**Cooked**: works — messages are runtime editor state, independent of asset form. (MapCheck content on cooked maps is whatever the checker could produce; the read itself is unaffected.)
**Verify**: run axis-B's map_check (or menu Build → Map Check) on a level with a known defect (e.g. an actor with null static mesh) → `message_log_read {logName:"MapCheck"}` returns `count >= 1` and per-severity counts match the Message Log window totals. Pairs 1:1 with the axis-B map_check runner.
**Score**: U4 E4 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — every citation re-opened and matched: IsRegisteredLogListing (MessageLogModule.h:49), GetLogListing (:57) with the silent-create doc comment verbatim at :52 ("If it does not exist it will created"), private MessageLogViewModel at :97; IMessageLogListing::GetFilteredMessages at IMessageLogListing.h:47, GetName at :90; FTokenizedMessage ToText/GetSeverity method-level CORE_API at TokenizedMessage.h:130,153; EMessageSeverity enum verbatim at :20-27. All 11 claimed listing registrations re-read at their exact sites: 9 in UnrealEdMisc.cpp:460-511 (EditorErrors :460, LoadErrors :466, LightingResults :472, PackagingResults :479, MapCheck :485, AssetCheck :491, SlateStyleLog :497, HLODResults :503, PIE :511), BlueprintLog at BlueprintEditorModule.cpp:234, AnimBlueprintLog at AnimationBlueprintEditorModule.cpp:59 — and the LoadModuleChecked("MessageLog") load-at-editor-startup claim is the very line UnrealEdMisc.cpp:456. Grep of the whole MessageLog module confirms GetFilteredMessageCount does not exist (negative #2 stands). One precision note, not a defect: MessageLog.Build.cs (Developer/MessageLog) compiles for any non-Shipping engine-linked target, not strictly "editor builds only" — irrelevant for editor-only MifBridge; the dep is legal exactly as claimed. Virtual-dispatch-only access pattern (no export macro needed) is correctly described.

### screenshot_request / screenshot_status
**Purpose**: capture what is ACTUALLY on the editor screen — active viewport including PIE gameplay and Slate UI — as a file the agent can hand to a human ("pixels for taste"); distinct from capture_camera, which renders an offscreen ASceneCapture2D from an arbitrary viewpoint and can never show PIE's real frame or any UI (read from its handler, MifBridgeSpatial.cpp:224-306).
**Engine API**:
```cpp
// Runtime/Engine/Public/UnrealClient.h:174,165,179,184,189 (struct FScreenshotRequest)
static ENGINE_API void RequestScreenshot(const FString& InFilename, bool bInShowUI, bool bAddFilenameSuffix, bool bHdrScreenshot=false);
static ENGINE_API void RequestScreenshot(bool bInShowUI);
static ENGINE_API void Reset();
static const FString& GetFilename() { return Filename; }                  // inline; backing static is ENGINE_API (:226)
static bool IsScreenshotRequested() { return bIsScreenshotRequested; }    // inline; backing static is ENGINE_API (:224)

// Runtime/Engine/Public/HighResScreenshot.h:67,70,76
ENGINE_API bool SetResolution(uint32 ResolutionX, uint32 ResolutionY, float ResolutionScale = 1.0f);
ENGINE_API void SetFilename(FString Filename);
ENGINE_API FHighResScreenshotConfig& GetHighResScreenshotConfig();

// Runtime/Engine/Public/UnrealClient.h:654 (class FViewport)
ENGINE_API bool TakeHighResScreenShot();

// Editor/UnrealEd/Classes/Editor/EditorEngine.h:2459,1200
UNREALED_API FViewport* GetActiveViewport();
UNREALED_API void RedrawAllViewports(bool bInvalidateHitProxies=true);

// Runtime/Core/Public/CoreGlobals.h:386
extern CORE_API bool GIsHighResScreenshot;
```
**Export**: ENGINE_API / UNREALED_API verbatim above. The two inline statics (GetFilename/IsScreenshotRequested) compile into MifBridge and link because their backing members are ENGINE_API (UnrealClient.h:224,226).
**Module**: none — already linked (Engine, UnrealEd).
**Guards**: none (UnrealEd module is editor-only by definition).
**Bucket**: read-only for screenshot_status; **self-managed (no transaction)** for screenshot_request — it mutates transient screenshot-request state only, never a UObject; a transaction would be an empty undo entry.
**Async**: YES — request + poll. The screenshot is taken when a viewport next draws (end-of-frame processing), never inside the handler's stack frame. `screenshot_request` records the expected absolute path in module state, calls `RequestScreenshot(path, showUI, /*bAddFilenameSuffix=*/false)` (suffix OFF so the returned path is deterministic), then `GEditor->RedrawAllViewports(false)` so a non-realtime editor viewport is guaranteed to draw and consume the request. `screenshot_status` reports `{pending: FScreenshotRequest::IsScreenshotRequested(), path, exists, fileBytes}` — done when pending==false AND exists && fileBytes > 0.
**Params** (screenshot_request):
| name | aliases | type | default | required |
|---|---|---|---|---|
| name | filename | string (file-name stem, sanitized via FPaths::MakeValidFileName like capture_camera) | "MifScreen_<utcstamp>" | no |
| showUI | — | bool | true | no |
| highRes | — | bool | false | no |
| resolutionX | width | int 64..16384 | — | only if highRes |
| resolutionY | height | int 64..16384 | — | only if highRes |
Output dir fixed to `<ProjectSaved>/MifBridge/Screenshots/`. When `highRes:true`: `GetHighResScreenshotConfig().SetResolution(X,Y); SetFilename(path);` then `GEditor->GetActiveViewport()->TakeHighResScreenShot()`; its bool return is surfaced (`accepted`) — false means the GPU refused the size (comment at UnrealClient.h:651-653 verbatim: "can fail if the requested multiplier makes the screen too big for the GPU to cope with"). screenshot_status takes no params. Unrecognised parameter → error.
**Failure modes**:
- A screenshot is already pending → error `"a screenshot is already pending — poll screenshot_status until pending==false"` (single-flight; FScreenshotRequest is global static state, two concurrent requests would race on Filename).
- highRes without resolutionX/resolutionY → error naming the missing parameter.
- `TakeHighResScreenShot()` returns false → `accepted:false` with the GPU-size explanation.
- File never appears (viewport minimized / no draw) → status keeps returning `pending:false, exists:false`; status includes `adviceIfStuck: "editor window may be minimized — a viewport must draw one frame"`.
**Cooked**: works — captures the framebuffer; asset form irrelevant.
**Verify**: request → poll status until done → `exists:true` and `fileBytes > 10000`; for highRes 1920x1080, decode the PNG header (bytes 16-23) and check width==1920, height==1080. Numbers first, then the human looks at the pixels.
**Score**: U3 E3 R4 → tier 2. Closes the "show me what PIE actually looks like" gap that capture_camera structurally cannot.
**Phase-2 verdict**: CONFIRMED — all citations verbatim: RequestScreenshot overloads at UnrealClient.h:165,174, Reset :179, inline GetFilename :184 / IsScreenshotRequested :189 with ENGINE_API backing statics at :224,226; TakeHighResScreenShot at :654 with the GPU-size comment verbatim at :651-653; SetResolution/SetFilename/GetHighResScreenshotConfig at HighResScreenshot.h:67,70,76; GetActiveViewport/RedrawAllViewports at EditorEngine.h:2459,1200; GIsHighResScreenshot at CoreGlobals.h:386. Phase-1's UNVERIFIED consumption-chain question is now RESOLVED by reading the implementations: (a) UEditorEngine::RedrawAllViewports is Invalidate-only (EditorServer.cpp:292-302) — nothing draws in the handler's stack frame, so the request is non-blocking and consumed on the next editor tick inside FViewport::Draw, where GIsHighResScreenshot|=bTakeHighResScreenShot (UnrealClient.cpp:1782), HighResScreenshot() runs (:1800) and ProcessScreenShots(this) consumes normal requests (:1858); (b) TakeHighResScreenShot itself only sets a flag + Invalidate() and its false path raises a non-modal Slate toast, never a dialog (UnrealClient.cpp:1448-1485); (c) the deterministic-path design is safe because CreateViewportScreenShotFilename keeps any filename containing a path separator as-is (UnrealClient.cpp:380-385) — pass the absolute path. One implementation note: FHighResScreenshotConfig::SetResolution can ALSO return false on oversize and is what actually sets GScreenshotResolutionX/Y + GIsHighResScreenshot (HighResScreenshot.cpp:217-235) — surface ITS bool as `accepted` too, not only TakeHighResScreenShot's.

### list_automation_tests
**Purpose**: enumerate every automation test registered in this editor (engine smoke tests, functional tests, project tests) with flags and source locations — the prerequisite for running any of them, and a capability inventory an agent can diff across builds.
**Engine API**:
```cpp
// Runtime/Core/Public/Misc/AutomationTest.h:849,967,959
static CORE_API FAutomationTestFramework& Get();
CORE_API void GetValidTestNames( TArray<FAutomationTestInfo>& TestInfo ) const;
CORE_API void LoadTestModules();

// FAutomationTestInfo getters live in the same header (class at :271; fields set via ctor at :290:
// DisplayName, FullTestPath, TestName, TestParameter, SourceFile, SourceFileLine, TestFlags, NumParticipantsRequired)

// EAutomationTestFlags::Type (AutomationTest.h:76-132, key values verbatim):
// EditorContext = 0x00000001, ClientContext = 0x00000002, NonNullRHI = 0x00000100,
// RequiresUser = 0x00000200, Disabled = 0x00010000, SmokeFilter = 0x01000000,
// EngineFilter = 0x02000000, ProductFilter = 0x04000000, PerfFilter = 0x08000000,
// StressFilter = 0x10000000, NegativeFilter = 0x20000000
static CORE_API const TMap<FString, Type>& GetTestFlagsMap();   // AutomationTest.h:134
```
**Export**: CORE_API method-level (class FAutomationTestFramework itself unexported — every used method individually exported, verified above).
**Module**: none — already linked (Core).
**Guards**: none (framework exists in all configurations; test registration density differs).
**Bucket**: read-only.
**Async**: no — GetValidTestNames walks an in-memory map.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| filter | nameContains | string substring vs DisplayName/FullTestPath | "" | no |
| flagsAny | flags | array of strings from GetTestFlagsMap keys (e.g. ["SmokeFilter","EditorContext"]) | [] = all | no |
| loadTestModules | — | bool (call LoadTestModules() first) | false | no |
| limit | — | int 1..5000 | 500 | no |
Unrecognised parameter → error. Unknown flag string → error listing GetTestFlagsMap keys.
**Returns**: `{count, matched, truncated, tests:[{displayName, fullTestPath, testName, parameter, sourceFile, sourceFileLine, flags:[names], numParticipants}]}`.
**Failure modes**: none hard; `loadTestModules:true` can stall the game thread while modules load — documented in the response as `loadedModules:true` and capped by being an explicit opt-in.
**Cooked**: works — tests are code, not assets. Functional tests that reference cooked maps will enumerate fine (running them is the other endpoint's problem).
**Verify**: `count > 0` in any editor build (engine registers hundreds); `list_automation_tests {flagsAny:["SmokeFilter"]}` returns a strict subset; every returned test has non-empty fullTestPath.
**Score**: U4 E4 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — Get() at AutomationTest.h:849, GetValidTestNames :967, LoadTestModules :959, GetTestFlagsMap :134, all method-level CORE_API verbatim; EAutomationTestFlags values re-read at :76-132 and every quoted hex value matches (EditorContext 0x1, ClientContext 0x2, NonNullRHI 0x100, RequiresUser 0x200, Disabled 0x10000, SmokeFilter 0x01000000 ... NegativeFilter 0x20000000); FAutomationTestInfo class at :271 with the ctor carrying exactly the claimed fields at :290. Module claim (Core, already linked) and read-only bucket correct; LoadTestModules stall correctly disclosed as opt-in.

### run_automation_test / automation_status
**Purpose**: run one automation test in-process and get pass/fail + error entries + duration as numbers — turning the engine's own test corpus (and future project functional tests) into an agent-checkable oracle.
**Engine API**:
```cpp
// Runtime/Core/Public/Misc/AutomationTest.h — all verbatim, method-level CORE_API
CORE_API bool ContainsTest( const FString& InTestName ) const;                    // :900
CORE_API void StartTestByName( const FString& InTestToRun, const int32 InRoleIndex ); // :920
CORE_API bool StopTest( FAutomationTestExecutionInfo& OutExecutionInfo );         // :927
CORE_API bool ExecuteLatentCommands();                                            // :934
//   "@return - true if the latent command queue is now empty and the test is complete" (:932)

// FAutomationTestExecutionInfo (AutomationTest.h:189-268):
//   bool bSuccessful (:246), double Duration (:258),
//   const TArray<FAutomationExecutionEntry>& GetEntries() const (:214),
//   int32 GetErrorTotal() const (:222), int32 GetWarningTotal() const (:221)
```
**Export**: CORE_API per method, verbatim above.
**Module**: none — already linked (Core). (The IAutomationControllerManager route was evaluated and NOT chosen — see negative results.)
**Guards**: none.
**Bucket**: **self-managed (no transaction)** — tests may start PIE, swap worlds, and create objects at scale; capturing any of that in a blanket FScopedTransaction is exactly the dead-CDO/undo hazard the contract forbids.
**Async**: YES — request + poll, mandatory. `StartTestByName` runs the synchronous part of the test in the handler's frame; latent commands then need one `ExecuteLatentCommands()` call per editor tick until it returns true. Design: module holds a single run-state struct; on request, `StartTestByName(name, 0)` then register a ticker (`GEditor->GetTimerManager()` next-tick chain or FTSTicker on the game thread) that calls `ExecuteLatentCommands()` each frame; when it returns true → `StopTest(ExecInfo)` → stash results → unregister. `automation_status` reads the stash. Timeout enforced in the ticker (DequeueAllCommands + StopTest on expiry, AutomationTest.h:946).
**Params** (run_automation_test):
| name | aliases | type | default | required |
|---|---|---|---|---|
| testName | fullTestPath | string — FullTestPath as returned by list_automation_tests | — | YES (strict) |
| timeoutSeconds | timeout | number 1..3600 | 300 | no |
automation_status: no params. Unrecognised parameter → error.
**Returns** (automation_status): `{state: "idle"|"running"|"done", testName, elapsedSeconds}` plus when done: `{success, durationSeconds, errorCount, warningCount, entries:[{type, message}]}` — from bSuccessful/Duration/GetErrorTotal/GetWarningTotal/GetEntries.
**Failure modes**:
- Unknown test → `ContainsTest` false → error `"test 'X' not registered — use list_automation_tests (did you pass DisplayName instead of FullTestPath?)"` before any state is touched.
- A run already active → error `"a test is already running — poll automation_status"` (framework is a singleton; concurrent StartTestByName is undefined).
- PIE already running when the test itself needs to drive PIE (functional tests) → refuse with `"stop_pie first: functional tests manage their own PIE session"`. Interaction with existing start_pie/stop_pie is exclusive by design.
- Timeout → `state:"done", success:false, entries:[{type:"error", message:"timed out after N s — latent queue drained"}]`.
**Cooked**: works for code tests. Functional tests referencing cooked maps load them read-only (fine); tests that try to SAVE cooked content will fail inside the test — reported as that test's failure, not the endpoint's.
**Verify**: run a known-green engine smoke test (e.g. from `list_automation_tests {flagsAny:["SmokeFilter"]}`) → `success:true, errorCount:0`; run a known-missing name → immediate structured error. Duration > 0. Pure numbers.
**Score**: U4 E2 R3 → tier 2. The ticker pump and PIE-exclusivity need real design care, but this unlocks a category (self-testing loops).
**Phase-2 verdict**: CORRECTED — all header citations verbatim (ContainsTest AutomationTest.h:900, StartTestByName :920, StopTest :927, ExecuteLatentCommands :934 with the :932 return comment, DequeueAllCommands :946; exec-info bSuccessful :246, Duration :258, GetEntries :214, GetErrorTotal :222, GetWarningTotal :221 — all exact), but reading the implementation (AutomationTest.cpp) surfaced two hazards the entry MUST absorb: (1) **StartTestByName silently refuses to start** when `GIsSlowTask || GIsPlayInEditorWorld` — it only UE_LOGs "Test %s is too slow and could not be run." and returns (AutomationTest.cpp:474-492). Refusing when PIE is up (already designed) is necessary but NOT sufficient: the handler must confirm the test actually started by checking `GIsAutomationTesting` (extern CORE_API bool, CoreGlobals.h:564) after the call, else report a structured "test did not start (slow task in progress?)" error. (2) **StopTest asserts** `check(GIsAutomationTesting)` (AutomationTest.cpp:497) — calling it after a refused start, or twice, is an editor CRASH, so the ticker/timeout path may only ever call StopTest while GIsAutomationTesting is true. Also two upgrades from unknown to fact: PrepForAutomationTests/ConcludeAutomationTests need NO external bracketing — StartTestByName calls Prep internally (AutomationTest.cpp:480) and StopTest calls Conclude (:502), resolving the Phase-1 UNVERIFIED item; and StartTestByName, if a test is already in flight, dequeues all latent/network commands and StopTest()s it first (:448-462) — the single-flight refusal designed above is what prevents this silent kill. Effort stays E2; contract unchanged, implementation notes mandatory.

### trace_start / trace_stop / trace_status
**Purpose**: record an Unreal Insights .utrace of an agent-triggered workload (channel-selectable) and get back the exact file path plus connection GUIDs — deep profiling beyond get_perf_stats' frame counters.
**Engine API**:
```cpp
// Runtime/Core/Public/ProfilingDebugging/TraceAuxiliary.h — all verbatim
static CORE_API bool Start(EConnectionType Type, const TCHAR* Target, const TCHAR* Channels = TEXT("default"), FOptions* Options = nullptr, const FLogCategoryAlias& LogCategory = LogCore);  // :84
static CORE_API bool Stop();                                              // :90
static CORE_API bool IsConnected(FGuid& OutSessionGuid, FGuid& OutTraceGuid); // :174
static CORE_API FString GetTraceDestinationString();                      // :160
static CORE_API void	GetActiveChannelsString(FStringBuilderBase& String); // :184
// EConnectionType { Network, File, None } (:26-41); FOptions { bNoWorkerThread, bTruncateFile, bExcludeTail } (:64-72)
```
**Export**: CORE_API per method (class FTraceAuxiliary unexported; every used member exported, verified above).
**Module**: none — already linked (Core).
**Guards**: none. (Trace can be compiled out via UE_TRACE_ENABLED in some configs; `Start` then returns false — surfaced, not hidden.)
**Bucket**: self-managed (no transaction) — mutates global trace state, never UObjects; undo is meaningless here.
**Async**: no request/poll pair needed — `Start` returns immediately and recording proceeds on trace's own worker; `trace_status` IS the observation endpoint. `Stop` is synchronous.
**Console-wrapper honesty check (required by the sweep brief)**: PASSES, decided honestly. `trace.start` the console command is a thin parser over this same FTraceAuxiliary API; the endpoint (a) returns the resolved absolute .utrace path (console returns nothing machine-readable), (b) exposes `IsConnected(FGuid&,FGuid&)` session/trace GUIDs and the active channel list as structured fields via trace_status, and (c) validates the channel string and target path with named-parameter errors. That is structured value the console route cannot deliver — not a wrapper.
**Params** (trace_start):
| name | aliases | type | default | required |
|---|---|---|---|---|
| channels | — | string, comma-separated (e.g. "cpu,gpu,frame,bookmark,log") | "default" | no |
| file | path | string; absolute or project-relative | `<ProjectSaved>/MifBridge/Traces/<utcstamp>.utrace` | no |
| truncate | — | bool (FOptions::bTruncateFile) | false | no |
| excludeTail | — | bool (FOptions::bExcludeTail) | false | no |
trace_stop / trace_status: no params. Unrecognised parameter → error.
**Returns**: trace_start `{started, destination}` (started=false ⇒ error text "a trace connection is already active — trace_stop first, or trace was compiled out"); trace_status `{connected, destination, sessionGuid, traceGuid, activeChannels}`; trace_stop `{stopped, destination, fileBytes}` (file size read after Stop — the numeric proof the trace captured something).
**Failure modes**:
- Already tracing → Start returns false (header: "If a connection is already active this call does nothing" :75-76) → error naming trace_stop.
- Unwritable target dir → started:false with the path in the message.
- trace_stop with no active connection → `stopped:false` (Stop's documented false case :88), not an exception.
**Cooked**: works — tracing instruments code, not assets.
**Verify**: trace_start {channels:"cpu,frame"} → trace_status shows connected:true + both GUIDs non-zero → run 5 s of PIE → trace_stop → `fileBytes > 100000` and the .utrace opens in Insights. File size is the number; Insights is the taste.
**Score**: U3 E4 R4 → tier 2.
**Phase-2 verdict**: CONFIRMED — watch-item satisfied: every used FTraceAuxiliary member re-read and ALL are CORE_API exported on an unexported class (`class FTraceAuxiliary` bare at TraceAuxiliary.h:15): Start at :84 signature verbatim including the FLogCategoryAlias default, Stop :90 with the "false if there was no data connection" doc at :88, IsConnected(FGuid&,FGuid&) :174, GetTraceDestinationString :160, GetActiveChannelsString :184; EConnectionType {Network, File, None} at :26-41 and FOptions {bNoWorkerThread, bTruncateFile, bExcludeTail} at :64-72 exact; the already-active no-op comment verbatim at :75-76. Module (Core, already linked), self-managed bucket, and the not-a-console-wrapper argument all hold.

### get_properties_bulk
**Purpose**: read N object/property pairs in ONE call — the watch-list primitive for PIE observation loops (poll player position + health + door state at 5 Hz without 3 round-trips and 3 undo-history perturbations).
**Engine API**: none new — pure composition of the plugin's existing reflection walkers: `ResolveGenericTarget` (MifBridgeNodes6.cpp:23) and `ResolveReadPropertyPath` (MifBridgeNodes6.cpp:58) plus `FProperty::ExportText_Direct` exactly as H_get_property uses them (MifBridgeNodes6.cpp:119-142).
**Export**: n/a (already-compiling plugin code).
**Module**: none.
**Guards**: none.
**Bucket**: read-only — and this is the justification for existing at all versus `batch`: **H_batch wraps every op in one FScopedTransaction (MifBridgeNodes.cpp:1269)**, so a read-only watch-list polled through batch pushes an empty undo entry into the editor per poll — precisely the anti-pattern invariant #2 names. batch also spends response weight on per-op envelopes and blueprint compile tracking (:1303-1319) that reads never need. A dedicated read-only bulk endpoint is the correct shape; it was diffed against all 159 endpoints — nothing else provides multi-target reads.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| targets | reads | array (1..200) of `{objectPath, propertyPath}` (objectPath rules identical to get_property, incl. PIE paths) | — | YES |
| failFast | — | bool: stop at first failure | false | no |
Unrecognised parameter (top-level or per-target) → error naming it and the array index.
**Returns**: `{count, okCount, results:[{ok, target, propertyPath, type, value} | {ok:false, error, index}]}` — order preserved, index echoed so a partial failure is attributable.
**Failure modes**: per-entry `object not found: <path>` / `property 'X' not found on 'Y'` (same texts as get_property, MifBridgeNodes6.cpp:38,76); entry failures do not poison siblings unless failFast. targets empty or >200 → error naming the limit.
**Cooked**: same as get_property — reads work on cooked objects' reflected properties; stripped editor-only data simply isn't there to read.
**Verify**: bulk-read 3 known properties (e.g. one CDO float, one placed-actor RelativeLocation, one PIE pawn property mid-session) and compare byte-for-byte with three individual get_property calls: values identical, okCount==3.
**Score**: U4 E4 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — the composed plugin helpers re-read at their exact sites: ResolveGenericTarget at MifBridgeNodes6.cpp:23 (StaticLoadObject live-object resolution at :31, error text "object not found: %s" verbatim at :38), ResolveReadPropertyPath at :58 ("property '%s' not found on '%s'" verbatim at :76), and H_get_property's ExportText_Direct usage spanning :119-142 exactly as cited. The existence justification also re-verified: H_batch opens one blanket FScopedTransaction at MifBridgeNodes.cpp:1269 with per-op envelopes and touched-blueprint compile tracking at :1303-1319, so reads routed through batch do pollute undo — the read-only bulk shape is correctly argued and collides with none of the 160 covered endpoints.

### pie_resolve_path
**Purpose**: convert an editor object path to its live PIE counterpart (and back) so an agent can go from "the actor I placed" to "the actor that is running" in one deterministic step, instead of re-discovering it by scanning list_pie_actors output for a name match.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Engine/World.h — class ENGINE_API UWorld final : public UObject, public FNetworkNotify (:953)
static FString ConvertToPIEPackageName(const FString& PackageName, int32 PIEInstanceID);       // :4065
static FString StripPIEPrefixFromPackageName(const FString& PackageName, const FString& Prefix); // :4068
static FString BuildPIEPackagePrefix(int32 PIEInstanceID);                                     // :4071
static FString RemovePIEPrefix(const FString &Source, int32* OutPIEInstanceID = nullptr);      // :4080

// Runtime/Engine/Classes/Engine/Engine.h:406 — FWorldContext member
int32	PIEInstance;
```
**Export**: class-level ENGINE_API on UWorld (World.h:953) exports all four statics; no method-level macro needed. FWorldContext is an exported Engine type read by value.
**Module**: none — already linked (Engine).
**Guards**: none (PIE machinery is editor-runtime).
**Bucket**: read-only.
**Async**: no.
**Precedent read first, as instructed**: list_pie_actors resolves the PIE world via `GEditor->PlayWorld` (MifBridgePIE.cpp:47-50) and multi-instance worlds via `GEngine->GetWorldContexts()` filtering `EWorldType::PIE` (CollectPIEWorlds, MifBridgePIE.cpp:73-86); the actorPath it returns is `Actor->GetPathName()` (:411) — i.e. the `/Game/Maps/UEDPIE_0_Map.Map:PersistentLevel.Foo` form this endpoint produces without iteration. Handler: split the object path at the first `.` into package + subobject path, run ConvertToPIEPackageName / RemovePIEPrefix on the package half, reassemble, then (default) `StaticFindObject` to confirm the object is live — the same in-memory resolution get_property relies on (StaticLoadObject finds live objects first, MifBridgeNodes6.cpp:31).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| path | objectPath | string object or package path | — | YES (strict) |
| direction | — | string enum: "toPie", "toEditor", "auto" (auto = toEditor if RemovePIEPrefix changes the string, else toPie) | "auto" | no |
| pieInstance | — | int | the PIEInstance of GEditor->PlayWorld's context; with multiple clients, must be given explicitly | no |
| verifyExists | — | bool | true | no |
Unrecognised parameter → error. Bad direction string → error listing the three values.
**Returns**: `{inputPath, resolvedPath, direction, pieInstance, exists, class}` (class only when exists).
**Failure modes**:
- toPie with no PIE session → error `"no PIE world — start_pie, then poll pie_status until state=='running'"` (same text as list_pie_actors, MifBridgePIE.cpp:376).
- pieInstance given but no matching FWorldContext → error listing the PIEInstance values that DO exist (from GetWorldContexts).
- verifyExists and object not found → `exists:false` with `resolvedPath` still returned (the mapping is still the correct string; the object may simply not be spawned) — explicitly NOT an error, so agents can use it as an existence probe.
**Cooked**: works — the transform is pure string mapping plus an in-memory find; cooked maps that can run PIE resolve identically.
**Verify**: start_pie → take any actorPath from list_level_actors, resolve toPie → returned path must equal the corresponding entry in list_pie_actors byte-for-byte, `exists:true`; resolve that back toEditor → round-trips to the input. String equality is the number.
**Score**: U4 E4 R5 → tier 1. Closes a real friction point in every PIE assertion loop today.
**Phase-2 verdict**: CONFIRMED — `class ENGINE_API UWorld final` verbatim at World.h:953 (class-level export claim correct, statics need no method macros); ConvertToPIEPackageName :4065, StripPIEPrefixFromPackageName :4068, BuildPIEPackagePrefix :4071, RemovePIEPrefix :4080 — all four signatures verbatim; FWorldContext::PIEInstance at Engine.h:406. Plugin precedents re-read: GetPIEWorld()=GEditor->PlayWorld at MifBridgePIE.cpp:47-50, CollectPIEWorlds via GEngine->GetWorldContexts filtering EWorldType::PIE at :73-86, Actor->GetPathName() at :411, and the no-PIE error text verbatim at :376. Read-only bucket, no new module, cooked claim all correct.

### world_state_hash
**Purpose**: one deterministic number summarizing world state (actor set + transforms + loaded levels) so an agent can prove "this operation changed nothing" or "these two runs produced identical worlds" without diffing full actor dumps — the reproducibility primitive.
**Engine API**:
```cpp
// Runtime/Core/Public/Misc/Crc.h:30-34 — FORCEINLINE wrapper over exported function pointer
static CORE_API MemCrc32Functor MemCrc32Func;
static FORCEINLINE uint32 MemCrc32(const void* Data, int32 Length, uint32 CRC = 0)

// Runtime/Engine/Classes/Engine/World.h (class ENGINE_API UWorld, :953)
int32 GetNumLevels() const;                       // :3266
const TArray<class ULevel*>& GetLevels() const;   // :3271

// Runtime/Core/Public/CoreGlobals.h:486
extern CORE_API uint64 GFrameCounter;
```
Actor access route: the same two the plugin already ships — `UEditorActorSubsystem::GetAllLevelActors()` for the editor world (read first: list_level_actors handler, MifBridgeLevel.cpp:155) and `TActorIterator<AActor>` for a PIE world (list_pie_actors, MifBridgePIE.cpp:387). No direct ULevel::Actors poke needed, so no new export question.
**Export**: CORE_API / ENGINE_API verbatim above; subsystem route already links today.
**Module**: none — already linked.
**Guards**: none.
**Bucket**: read-only.
**Async**: no — hashing 5k actors' transforms is sub-millisecond.
**Design (lean, per the sweep brief)**: collect `{GetPathName(), ActorTransform}` for every valid actor (optional classFilter identical in semantics to list_level_actors'), sort by path (determinism — iteration order is not stable across runs), quantize transforms (location to 0.01 uu, rotation quat to 1e-5, scale to 1e-4) to kill float noise, then fold path + quantized values through FCrc::MemCrc32 into one uint32; separately hash the sorted level package names. Quantization constants are part of the endpoint contract and echoed in the response.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| world | — | string enum: "editor", "pie" | "editor" | no |
| classFilter | — | string (ancestry name match, same rule as list_level_actors) | "" | no |
| includeTransforms | — | bool (false = membership-only hash) | true | no |
Unrecognised parameter → error.
**Returns**: `{world, actorCount, levelCount, levels:[names], stateHash (hex8), levelSetHash (hex8), frameCounter, quantization:{loc,rotQuat,scale}}`.
**Failure modes**: world:"pie" with no PIE session → same error text as list_pie_actors. Editor world absent → "no editor world is open" (MifBridgeLevel.cpp:143 text reused).
**Cooked**: works — actor transforms and level names exist regardless of asset form. (Hash covers dynamic state only; it deliberately says nothing about asset contents.)
**Verify**: call twice with no edits between → identical stateHash both times (same frameCounter NOT required — hash must be frame-independent when nothing moved). `move_actor_to` one actor by 10 uu → hash changes; move it back exactly → hash restores. Three numeric assertions; pairs with scene_report for the "what changed" follow-up.
**Score**: U3 E4 R5 → tier 2.
**Phase-2 verdict**: CONFIRMED — FCrc::MemCrc32 FORCEINLINE over `static CORE_API MemCrc32Functor MemCrc32Func` verbatim at Crc.h:30-35 (links exactly as argued); GetNumLevels at World.h:3266 and GetLevels at :3271 on the ENGINE_API-exported UWorld (:953); GFrameCounter CORE_API at CoreGlobals.h:486. Actor-access routes re-read: UEditorActorSubsystem::GetAllLevelActors at MifBridgeLevel.cpp:155 and the "no editor world is open" error text verbatim at :143; TActorIterator PIE route at MifBridgePIE.cpp:387. No new export question, read-only bucket correct, quantization contract sensibly pinned in the response.

## Compositions (no new endpoint needed)

- **"Editor screenshot from an arbitrary viewpoint"** — already covered by `capture_camera` (offscreen SceneCapture2D, MifBridgeSpatial.cpp:224-306). screenshot_request above is only for the real viewport/UI/PIE frame; anything viewpoint-parameterized should keep using capture_camera.
- **"Tail the running GAME's log"** — covered by `read_modloader_log` (UE4SS.log file tail, MifBridgePipeline.cpp:29-89). log_tail is the EDITOR-process counterpart; the two are complementary, not overlapping.
- **"Watch one property across frames"** — a single target is just repeated `get_property`; get_properties_bulk earns its slot only at N>1 (and by staying out of the undo history, unlike reads routed through `batch` — MifBridgeNodes.cpp:1269).
- **"Did the console command print X?"** — covered by `run_console_captured` for synchronous output; log_tail covers the async remainder its own doc comment disclaims (MifBridgePIE.cpp:457-458).
- **"Frame-rate before/after a level edit"** — get_perf_stats twice + arithmetic in the caller; no dedicated diff endpoint proposed.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

_Phase-2 spot-verification (2026-07-26): all 8 negatives re-checked against their citations; NONE overturned. Per-item evidence: (1) grep of all of Runtime/Engine for GAverageFPS finds definitions/re-externs only under Private (UnrealEngine.cpp:634-635, EngineAnalyticsSessionSummary.cpp:23, UnrealEngine.cpp:11454) — no Public/Classes declaration exists; (2) grep of the whole Developer/MessageLog module for GetFilteredMessageCount: zero hits, and the full IMessageLogListing interface region re-read confirms only GetFilteredMessages (IMessageLogListing.h:47); (3) LogSuppressionInterface.h re-read whole (27 lines) — only AssociateSuppress/DisassociateSuppress/ProcessConfigAndCommandLine, no enumeration; (4) MessageLogModule.h re-read whole — private MessageLogViewModel at :97, per-name IsRegisteredLogListing at :49, no listing enumeration; (5) silent-create doc comment verbatim at MessageLogModule.h:52; (6) all six IAutomationControllerManager member line numbers exact (RequestAvailableWorkers :168, RequestTests :177, RunTests :184, Tick :200, GetReports :245, GetTestState :329) and IAutomationControllerModule.h:23,28-29,31-34 verbatim — deferral reasoning sound; (7) verdict stands, FTraceAuxiliary surface re-verified under trace_start; (8) see the item's own Phase-2 note below._

1. **GAverageFPS / GAverageMS have no public-header declaration in 5.3.2.** Definitions carry ENGINE_API (`Runtime/Engine/Private/UnrealEngine.cpp:634-635`) but no header under Runtime/Engine/Public declares them; the engine's own consumers re-declare `extern ENGINE_API float GAverageFPS;` locally (`Runtime/Engine/Private/Analytics/EngineAnalyticsSessionSummary.cpp:23`, `UnrealEngine.cpp:11454`). get_perf_stats must copy that pattern — it links, but it is a fork-fragility wart worth recording.
2. **`IMessageLogListing::GetFilteredMessageCount` does not exist in 5.3.2.** The sweep hypothesis named it; the interface has only `GetFilteredMessages()` (Developer/MessageLog/Public/IMessageLogListing.h:47 — full interface region :21-134 read). Count = `GetFilteredMessages().Num()`.
3. **No public API enumerates registered log categories.** `FLogSuppressionInterface` (Runtime/Core/Public/Logging/LogSuppressionInterface.h, whole 27-line file) exposes only AssociateSuppress/DisassociateSuppress/ProcessConfigAndCommandLine; the category table lives in the private implementation. The Output Log UI builds its category list from messages it has seen — log_tail adopts the same honest approach (`categoriesSeen` from its ring buffer).
4. **No public API enumerates registered Message Log listings.** FMessageLogModule holds them in a private `TSharedPtr<FMessageLogViewModel> MessageLogViewModel` (MessageLogModule.h:97); the public surface offers only per-name `IsRegisteredLogListing` (:49). message_log_read therefore takes a required logName and documents the canonical names with their registration sites (UnrealEdMisc.cpp:460-511 et al.).
5. **`FMessageLogModule::GetLogListing` silently creates missing listings** ("If it does not exist it will created", MessageLogModule.h:52) — a foot-gun, not a feature: a typo'd name yields a plausible empty log. Endpoint must gate on IsRegisteredLogListing (designed in above; recorded here because any OTHER future caller of this module has the same trap).
6. **IAutomationControllerManager route evaluated and deferred.** The interface is pure-virtual and reachable (`Developer/AutomationController/Public/IAutomationControllerModule.h:23,31-34` — `virtual IAutomationControllerManagerRef GetAutomationController() = 0;` via `FModuleManager::GetModuleChecked<IAutomationControllerModule>("AutomationController")`; manager members RequestAvailableWorkers :168, RequestTests :177, RunTests :184, Tick :200, GetReports :245, GetTestState :329 in IAutomationControllerManager.h), but it drags in the MessageBus worker/session machinery and something must pump BOTH `IAutomationControllerModule::Tick()` (doc comment :28-29: "Tick function that will execute enabled tests") and the worker. For single-test in-editor runs, FAutomationTestFramework direct (chosen above) is strictly simpler. Controller route remains the right answer if multi-device/multi-participant tests are ever needed — record as future work, new deps `AutomationController` (+transitively AutomationMessages/MessageBus).
7. **`trace_start` console-wrapper verdict: NOT a wrapper** — argued inline in the proposal; recorded here since the sweep brief demanded an explicit decision either way.
8. **Live editor bridge was DOWN during this sweep** (curl to 127.0.0.1:8791 → connection refused, both probes). No live confirmations of pie_status/list_level_actors payload shapes were possible; all citations are source-based. Phase-2 should re-run the two probes.
   **Phase-2: probes re-run as requested — bridge is UP and both PASS.** Routes are `POST /api/<name>` with an `X-Mif-Token` header (MifBridgeServer.cpp:91,99; token from env `MIF_BRIDGE_TOKEN`, default "dev" — MifBridge.cpp:33-36). `pie_status` → `{ok:true, running:false, startPending:false, sessionActive:false, worldHasBegunPlay:false, stopPending:false, simulating:false, state:"stopped", editorWorld:"Untitled"}`; `list_level_actors {limit:1}` → `{ok:true, world:"Untitled", count:0, matched:0, truncated:false, actors:[]}`. Payload shapes match the source-derived claims; the negative was a true statement about sweep time, not an API finding — nothing to overturn.

## UNVERIFIED

- **Whether a non-realtime editor viewport consumes FScreenshotRequest without an explicit redraw** — mitigated by calling `RedrawAllViewports` in the design, but the FViewport::Draw → ProcessScreenShots call chain itself was not read (only the request-side statics were). Risk: low; the mitigation is unconditional.
  **Phase-2: RESOLVED** — chain read: RedrawAllViewports is Invalidate-only (EditorServer.cpp:292-302); the next FViewport::Draw consumes the request (GIsHighResScreenshot|=bTakeHighResScreenShot at UnrealClient.cpp:1782, HighResScreenshot() :1800, ProcessScreenShots(this) :1858). Design works as written; see screenshot_request verdict.
- **GNumDrawCallsRHI reset site and thread visibility** — the extern is verified (RHIStats.h:66) but the per-frame reset/accumulate code was not read; the one-frame-lag claim in get_perf_stats is inference from the "GPU stats" section context, hence surfaced as an explicit `drawCallsFrameLag` field rather than hidden.
  **Phase-2: RESOLVED** — written once per frame in FRHICommandListImmediate::ProcessStats(), "Called from RHIBeginFrame" (RHI.cpp:1321-1322, writes at :1368-1369); the one-frame-lag claim is now fact, see get_perf_stats verdict.
- **Whether `PrepForAutomationTests`/`ConcludeAutomationTests` (AutomationTest.h:1181,1184) are required bracketing for StartTestByName** — no caller site was read. run_automation_test's implementer must check FAutomationTestFramework's own use before shipping; if required, they slot into the request/finish steps without changing the endpoint contract.
  **Phase-2: RESOLVED** — no external bracketing: StartTestByName calls PrepForAutomationTests internally (AutomationTest.cpp:480) and StopTest calls ConcludeAutomationTests (:502). See the run_automation_test CORRECTED verdict for the two hazards found at the same call sites.
- **`FApp::GetSessionId` for the controller route** — not needed by the chosen design; left unverified deliberately.

## Coverage log

Covered (headers opened, signatures pasted, exports checked): frame/thread timing globals; RHI draw-call stats; GC/UObject counters; platform memory stats; GLog redirector + output-device thread contract; log-category enumeration (negative); Message Log module + listing + tokenized message + canonical listing names; viewport & high-res screenshot request paths incl. editor redraw/active-viewport accessors; automation framework (enumeration, run, stop, latent pump, flags, exec-info) + controller module (assessed, deferred); FTraceAuxiliary full surface; PIE package-name mapping statics + FWorldContext::PIEInstance; FCrc/GetLevels/GFrameCounter for state hashing. Plugin precedents read: PIE world resolution, log-capture device, batch transaction shape, get_property target/path resolution, list_level_actors subsystem route, capture_camera, read_modloader_log, Build.cs deps.

Not covered / left for other axes or phase-2: `stats`-system readback (FStatUnitData / stat-group enumeration — larger design, get_perf_stats covers the high-value numbers); CSV profiler (FCsvProfiler) endpoints; memory report commands (memreport is console-covered); IAutomationControllerManager full design (deferred, see negative #6); live-bridge payload confirmation (bridge was down — negative #8); screenshot pixel-content assertions (deliberately out of scope — pixels are for taste).

Proposed endpoint names (14): get_perf_stats, log_tail, message_log_read, screenshot_request, screenshot_status, list_automation_tests, run_automation_test, automation_status, trace_start, trace_stop, trace_status, get_properties_bulk, pie_resolve_path, world_state_hash. All diffed against the 159 covered endpoints; no overlaps found (nearest neighbours: read_modloader_log, run_console_captured, capture_camera, batch, get_property — distinctions documented inline).
