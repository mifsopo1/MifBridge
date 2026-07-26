# Axis K2 — MifKismetReconstructor: reconstruction/verification pipeline as endpoints + the coupling model
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork "CookedEditorModKit"). Agent: phase-1 breadth._

Scope: the RECONSTRUCTION pipeline (decompile cooked bytecode → editable K2 graphs) and the
VERIFICATION stack (fidelity verify / drift census / intentional-drift classification) as MifBridge
endpoints, plus the coupling-model decision (section B) and the all-kr_*-in-the-plugin end-state
(section C). The read/dump/analyze surface (`mif.kr.ListBP/FindBP/DumpBP/AnalyzeUbergraph`) is the
K1 axis and is not re-proposed here.

Path shorthand used below:
- `KR/` = `D:/DDS2SDK/Game/Plugins/MifKismetReconstructor/Source/MifKismetReconstructor/`
- `MB/` = `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/`
- Engine paths are relative to `D:/UE532/Engine/Source`.

## Surface inventory

Every file below was opened and read this sweep (not recalled):

**Reconstruction pipeline (plugin, all Private/ — nothing exported):**
- `KR/Private/MifReconstructPipeline.h` (full, 34 lines) — the two shared entry points + cache reset.
- `KR/Private/MifReconstructCommand.cpp` (full, 212 lines) — `MifReconstructFunctionIntoGraph`
  definition (:33–116), the `MIF_KR_DEBUG`-gated `mif.kr.Reconstruct` console command (:118–212).
- `KR/Private/MifReconstructorDebug.h` (full) — `#define MIF_KR_DEBUG 1` (:10).
- `KR/Private/AssetGeneration/KismetBytecodeTransformer.h` (full, 65 lines);
  `KismetGraphDecompiler.h` (full, 318 lines); `KismetIntermediateFormat.h` (:1–80,
  `ECompiledStatementType` :15, `FKismetCompiledStatement` :70);
  `KismetGraphDecompiler_Reconstruct.cpp` :150–260 (the `Run` driver + flow-stack pre-pass) and the
  full `NodesCreated` accounting grep (23 increment sites).
- `KR/Private/MifReconstructEvent.cpp` :40–85 — `mif.kr.Events` CVar (:54–58, default **1**),
  `mif.kr.LatentResume` CVar (:64–68, default **1**), single-slot ubergraph cache rationale.
- `KR/Private/MifKismetReconstructorModule.cpp` (full) — delegate binding at startup (:18–42).
- `KR/Public/Toolkit/KismetBytecodeDisassemblerJson.h` (full, 60 lines) — the ONE exported class.

**Verification stack (plugin, Private/):**
- `KR/Private/Verify/MifFidelityVerifier.cpp` (full, 617 lines) — strict/lossy normalisation rules
  R1–R13, `Normalize` (:321), `VerifyBlueprint` (:432), delegate bind (:609–617), `mif.kr.DumpFull`
  CVar (:48–52).
- `KR/Private/Verify/MifDriftClassifier.h` (full, 57 lines) + `MifDriftClassifier.cpp` :1–140 —
  `FVerdict` (:28–46), `Classify` (:55–56), `mif.kr.ClassifyIntentional` CVar (default 1, .cpp:57–62),
  `mif.kr.DriftCensus` CVar (default 0, .cpp:66–71), caps (:81–83).

**Engine fork (Kismet module) — where half the "plugin" capability actually lives:**
- `Editor/Kismet/Public/CompiledBlueprintReconstructor.h` (full, 114 lines) — the three
  KISMET_API delegates + `CreateEditableBlueprintCopy` + `FBlueprintFidelityReport`.
- `Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp` :96–140, :525–570, :840–1140, :1140–1480,
  :1540–1753 — `IsCompiledBlueprintAsset` gate, `FUncookedCopyStats`, `CopyFunctionStubs`,
  `PopulateUncookedCopy`, `RunReconstructOnce`, `mif.kr.ReconstructAll`, `mif.kr.VerifyFidelity`
  (both registered HERE, not in the plugin), `CreateAndSaveEditableCopy`,
  `EnsureEditableParentChain`, `CreateEditableBlueprintCopy` definition.

**MifBridge (registry/coupling side):**
- `MB/Private/MifBridgeHandlers.h` (full, 386 lines) — `MIF_DECL` (:141), handler contract.
- `MB/Private/MifBridgeCommon.cpp` :1–30, :228–447 — `Handlers()`/`MIF_BIND` (:29–34),
  `IsReadOnlyEndpoint` (:239–270), `IsSelfManagedEndpoint` (:277–301), `H_self_audit` (:308–353),
  `IsCompileHeavyEndpoint` (:355–365), `RunEndpoint` (:367–393).
- `MB/Private/MifBridgeServer.cpp` :70–213 — per-endpoint route binding at `Start()` (:88–108),
  game-thread hop (:199–210).
- `MB/Private/MifBridge.cpp` :26–160 — `StartServer()` called from `StartupModule` (:58–61).
- `MB/Private/MifBridgeReconstruct.cpp` (full, 78 lines) — `create_editable_child` and the delegate
  comment (:73–74).
- `MB/Public/MifBridge.h` (full) — the Public/ dir EXISTS (module interface only, zero exported symbols).
- `MB/MifBridge.Build.cs` (full), `MifBridge.uplugin` (full, LoadingPhase **PostEngineInit**),
  `KR/MifKismetReconstructor.Build.cs` (full), `MifKismetReconstructor.uplugin` (full,
  LoadingPhase **Default**).

**Stock engine confirmations:**
- `Editor/UnrealEd/Public/Kismet2/KismetEditorUtilities.h` :110, :124, :169, :172, :178.
- `Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h` :305, :329, :390.
- `Runtime/Online/HTTPServer/Public/HttpServerModule.h` :25, :60 — the HTTP listener is a
  game-thread ticker (this fact shapes the whole job model, see below).

**Live probes (read-only):** `self_audit` → `endpointCount: 160`, healthy, no policy contradictions
(live DLL, 2026-07-26).

### Pipeline shape and cost (what the async design must respect)

The per-function pipeline is `disassemble → transform → decompile`, single-threaded, game-thread only:

- Entry: `MifReconstructFunctionIntoGraph` (`KR/Private/MifReconstructPipeline.h:15`). It holds an
  `FGCScopeGuard` for the WHOLE per-function pipeline because the IR holds raw un-rooted
  `UFunction*/UClass*` pointers (`KR/Private/MifReconstructCommand.cpp:44`, diagnosed on BP_BaseNPC:
  "crash after ~40 functions + an 8s GC stall", :43). **It cannot run off the game thread** and GC
  must be free to run BETWEEN functions — so any async model slices at function/Blueprint
  granularity, never mid-function, never on a worker thread.
- Per function: `FKismetBytecodeDisassemblerJson::SerializeFunction` (linear in `Script.Num()`),
  `FKismetBytecodeTransformer::SetSourceStatements`/`FinishGeneration` (linear),
  `FKismetGraphDecompiler::Run` (`KismetGraphDecompiler.h:46`) — flow-stack CFG simulation with a
  200 000-iteration guard (`KismetGraphDecompiler_Reconstruct.cpp:212`), then pass-1 node spawning
  (one `NewObject` + `AllocateDefaultPins` per node — the dominant per-function cost) and pass-2 pin
  wiring. Counters exposed per run: `GetNodesCreated()`, `GetStatementsProcessed()`, `WasDegraded()`
  (`KismetGraphDecompiler.h:76–78`).
- Per Blueprint: the loop over functions lives in the ENGINE (`CopyFunctionStubs`,
  `CompiledBlueprintCopyAction.cpp:551`, iterating `TFieldIterator<UFunction>(SourceBPGC,
  EFieldIteratorFlags::ExcludeSuper)` :567), followed by ONE full
  `FKismetEditorUtilities::CompileBlueprint` — the single most expensive step. Events are sliced out
  of the ubergraph separately (second delegate), gated by `mif.kr.Events` (default 1).
- Corpus scale, from in-source measurements (comments citing real runs): whole-game batch =
  **1256 BPs, PASS 1228** (`KR/Private/MifReconstructEvent.cpp:52`), fidelity **54.65%**
  (`CompiledBlueprintCopyAction.cpp:543`), events **85.9% reconstructed, FAIL 10/481**
  (`MifReconstructEvent.cpp:56`). No wall-clock numbers exist in source (see UNVERIFIED); the 8s GC
  stall note and the batch harness's per-BP crash-forensics flushing (:1222–1224) both testify that a
  single big BP is a multi-second, editor-stalling operation.

**The polling reality (verified, decisive):** `FHttpServerModule` is an `FTSTickerObjectBase`
(`Runtime/Online/HTTPServer/Public/HttpServerModule.h:25`) with `bool Tick(float DeltaTime) override`
(:60) — the HTTP listener is pumped by the game-thread ticker. While a synchronous reconstruct runs,
**no HTTP request is even read off the socket**, so mid-Blueprint progress polling is physically
impossible no matter where the status handler executes. Consequences baked into the design below:
- Single-BP jobs (reconstruct / verify / classify): one deferred-tick execution. The status endpoint's
  value is (1) the requester never holds a connection open across a multi-second stall, (2) results
  survive a client reconnect, (3) the flushed BEGIN log line + jobId give crash forensics. It does
  NOT give mid-BP progress — the file says so rather than pretending.
- Multi-BP jobs (drift census): sliced ONE BLUEPRINT PER TICK, so between BPs the ticker pumps HTTP
  and `kr_reconstruct_status` returns real `bpDone/bpTotal` progress. GC every 25 BPs, mirroring the
  engine batch loop (`CompiledBlueprintCopyAction.cpp:1303`).

### Shared job model (applies to all four request endpoints)

- **One job slot, no queue.** A second request while `state ∈ {queued, running}` fails:
  `"a kr job is already running (jobId=<id>, kind=<kind>) — poll kr_reconstruct_status or wait"`.
  A queue invites silent ordering surprises; batching belongs to the caller (`batch` exists).
- States: `queued → running(<phase>) → done | failed`. Phases: `resolving, minting, reconstructing,
  compiling, verifying, saving`. The last completed job record is retained until the next request
  (poll-after-done always works).
- Execution: the request handler validates + enqueues + returns; the work runs via
  `GEditor->GetTimerManager()->SetTimerForNextTick(...)` — the same deferral precedent as
  `new_level`/`load_level` (`MB/Private/MifBridgeHandlers.h:305–307`). Census re-arms one tick per BP.
- Progress counters are fed by the plugin's OWN already-bound delegates: the lambdas bound at
  `KR/Private/MifKismetReconstructorModule.cpp:18–37` increment `functionsDone/eventsDone`, add
  `TargetGraph->Nodes.Num()` deltas, and record degrade flags **only while a job is active**. Zero
  engine change for progress accounting.
- `functionsTotal` is precomputed plugin-side (`TFieldIterator<UFunction>(SourceBPGC, ExcludeSuper)`,
  `Script.Num() > 0`, `!FUNC_UbergraphFunction`). Flagged in the payload as `functionsTotalEstimate`:
  the authoritative gate lives in the engine-static `CopyFunctionStubs` and cannot be linked, so the
  done-counts are authoritative and the total is advisory (skew ⇒ the status simply overshoots or
  undershoots the estimate; it never lies about work done).
- Cancellation is deliberately omitted from v1: single-BP jobs are atomic anyway, and a census is
  bounded up-front via `maxCount`. If needed later, a `kr_job_cancel` flag checked between BP slices
  is a 20-line addition.

---

## Proposed endpoints

### kr_reconstruct_request
**Purpose**: start an asynchronous reconstruction job that decompiles a cooked Blueprint's bytecode
into editable K2 graphs — either a whole persistent editable copy (child/sibling/full-parent-chain)
or a single function into a scratch Blueprint — with a truthful per-function reconstruction tally an
agent can gate on (today `create_editable_child` runs the same engine pipeline synchronously and
reports nothing about reconstruction quality; the decompile half is reachable only via a
debug-gated console command).
**Engine API**:
```cpp
// engine fork — whole-copy mode (mint + populate(reconstruct) + compile + SAVE):
KISMET_API UBlueprint* CreateEditableBlueprintCopy(UBlueprintGeneratedClass* SourceBPGC,
	const FString& TargetPackagePath, bool bAsChild, FText* OutError, bool bFullParent = false);
```
`Editor/Kismet/Public/CompiledBlueprintReconstructor.h:37–38`
```cpp
// plugin-internal — single-function mode (the shared pipeline the F3 hook and the console command both use):
bool MifReconstructFunctionIntoGraph(UFunction* SourceFunc, UEdGraph* TargetGraph, UBlueprint* OwnerBP);
```
`KR/Private/MifReconstructPipeline.h:15` (definition `KR/Private/MifReconstructCommand.cpp:33–116`)
```cpp
// single-function scaffolding (all stock UnrealEd, method-level exports):
static UNREALED_API UBlueprint* CreateBlueprint(UClass* ParentClass, UObject* Outer, const FName NewBPName, enum EBlueprintType BlueprintType, TSubclassOf<UBlueprint> BlueprintClassType, TSubclassOf<UBlueprintGeneratedClass> BlueprintGeneratedClassType, FName CallingContext = NAME_None);
static UNREALED_API void CompileBlueprint(UBlueprint* BlueprintObj, EBlueprintCompileOptions CompileFlags = EBlueprintCompileOptions::None, class FCompilerResultsLog* pResults = nullptr );
```
`Editor/UnrealEd/Public/Kismet2/KismetEditorUtilities.h:124, :169`
```cpp
static UNREALED_API class UEdGraph* CreateNewGraph(UObject* ParentScope, const FName& GraphName, TSubclassOf<class UEdGraph> GraphClass, TSubclassOf<class UEdGraphSchema> SchemaClass);
static void AddFunctionGraph(UBlueprint* Blueprint, class UEdGraph* Graph, bool bIsUserCreated, SignatureType* SignatureFromObject)
```
`Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h:329, :390` (AddFunctionGraph is a header
template — no export macro needed). Single-function mode mirrors the proven console-command sequence
`KR/Private/MifReconstructCommand.cpp:164–192` exactly (mint parented to the cooked class →
`CreateNewGraph` + `AddFunctionGraph<UFunction>` from the cooked signature → shared pipeline →
`CompileBlueprint`).
**Export**: `KISMET_API` on `CreateEditableBlueprintCopy` (verbatim, CompiledBlueprintReconstructor.h:37);
`UNREALED_API` method-level on the FKismetEditorUtilities/FBlueprintEditorUtils statics.
`MifReconstructFunctionIntoGraph` has NO export macro — which is exactly why this handler must live
INSIDE the reconstructor module under coupling model (b). **Export promotion required: none under
(b).** Under (a) it would require promoting `MifReconstructPipeline.h` to `Public/` +
`MIFKISMETRECONSTRUCTOR_API` on both pipeline functions (see section B).
**Module**: handler lives in MifKismetReconstructor (editor-only plugin module, project-enabled,
`EnabledByDefault: true`). Its Build.cs already links Kismet, UnrealEd, BlueprintGraph, AssetRegistry,
Json (`KR/MifKismetReconstructor.Build.cs:19–27`) — the only NEW dep is `MifBridge` (registration
API, section B). MifBridge itself: **no new module dependency**.
**Guards**: none beyond module type (both modules are `"Type": "Editor"`); the handler runs only in
the editor process by construction.
**Bucket**: self-managed — the deferred slice runs a full `FKismetEditorUtilities::CompileBlueprint`
and (copy mode) `UPackage::SavePackage` (`CompiledBlueprintCopyAction.cpp:1606, :1635`); a full
compile inside a blanket transaction = reinstancing + Ctrl-Z = dead CDO. Precedent:
`create_editable_child` is registered self-managed for the same call (`MB/Private/MifBridgeCommon.cpp:287`).
The request handler itself opens no transaction (it only enqueues).
**Async**: yes — this endpoint + `kr_reconstruct_status` (below). Request returns
`{jobId, state:"queued", kind:"reconstruct", bpName, mode, variant, targetPath}` immediately; the
job body runs on the next tick(s) per the shared job model above.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| sourceAsset | blueprint, bpName, path | string | — | yes (strict: empty ⇒ `"sourceAsset required (cooked BP: asset path, _C class path, or exact name)"`) |
| mode | — | string: `copy` \| `function` | `copy` | no |
| variant | copyVariant | string: `child` \| `sibling` \| `uncooked` \| `sibling_full` \| `full` | `child` | no; ERROR if supplied with `mode=function` |
| targetPath | childPath, outPath | string (long package path) | `/Game/Mif/<Base>_Child` or `_Editable` (mirrors `MB/Private/MifBridgeReconstruct.cpp:51–57`) | no |
| function | functionName, func | string | — | required iff `mode=function`, ERROR if supplied with `mode=copy` |
`sourceAsset` accepts a `/Game/...` asset path, a `.<Name>_C` class path (resolution identical to
`H_create_editable_child`, `MB/Private/MifBridgeReconstruct.cpp:31–39`), or a bare name resolved by
exact `_C`-stripped AssetRegistry match (the `VerifyFidelityCmd` precedent,
`CompiledBlueprintCopyAction.cpp:1388–1404`); an ambiguous bare name errors listing the matches.
Unrecognised parameter ⇒ error naming it. Events are reconstructed iff the `mif.kr.Events` CVar is 1
(default 1, `KR/Private/MifReconstructEvent.cpp:54–58`); the job result reports event tallies either way.
**Failure modes**:
- job slot busy → `"a kr job is already running (jobId=..., kind=...) — poll kr_reconstruct_status"`.
- source not resolvable → `"source blueprint class not found: '<x>' (try the .<Name>_C class path; find candidates with find_assets)"`.
- `CanCreateBlueprintOfClass` false (`KismetEditorUtilities.h:178`) → `"cannot create a Blueprint parented to <class> — variant '<v>' is not mintable for this source"`.
- `mode=function` and the function is missing or `Script.Num()==0` → `"function '<f>' not found on <class> (or has no bytecode) — list candidates with describe_class"` (mirrors `KR/Private/MifReconstructCommand.cpp:158–161`).
- compile finishes with errors → job `state:"done"` with `compile.errors > 0` and `compile.firstError`; the asset still exists for inspection. `ok:true` on the HTTP envelope never implies a clean compile — the caller must read the numbers (house rule).
- ubergraph requested directly in `mode=function` → refused: `"'<f>' is the ubergraph — events are reconstructed per-event by the copy pipeline (mode=copy), never as one function"` (the pipeline itself skips it, `MifReconstructCommand.cpp:50–56`).
**Cooked**: this endpoint's entire reason to exist — it works on `.pak`-mounted `UBlueprintGeneratedClass`
whose graphs are stripped. Loose/uncooked sources are also accepted (no `PKG_Cooked` gate in
`CreateEditableBlueprintCopy`; the VerifyFidelity command deliberately accepts authored testbed BPs,
`CompiledBlueprintCopyAction.cpp:1393–1395`) — pointless but harmless, noted in the response.
**Verify**: poll `kr_reconstruct_status` to `done`, then: (1) `functionsReconstructed / functionsAttempted`
and `eventsReconstructed / eventsAttempted` are the truth tallies (delegate-return-fed, same
mechanism as the engine's `FUncookedCopyStats`, `CompiledBlueprintCopyAction.cpp:531–548`);
(2) `list_functions` / `list_graphs` on `result.blueprintId` shows the reconstructed overrides;
(3) `list_nodes` on one reconstructed graph — node count must be > 2 (a stub graph has only
entry/result) and should track `status.nodesCreated`; (4) `compile.errors == 0`; (5) for a
gold-standard check, follow with `kr_verify_fidelity` on the same source. Record the asset path used
in the implementation session's live proof.
**Score**: U5 E2 R3 → tier 0 — closes the mission's #1 stated gap ("cooked Blueprint graphs are
unreadable"; the decompile half is currently reachable only through a `MIF_KR_DEBUG`-gated console
command that opens UI and prints to the log).
**Phase-2 verdict**: CONFIRMED — `CreateEditableBlueprintCopy` signature verbatim (CompiledBlueprintReconstructor.h:37-38, KISMET_API); pipeline fn decl exact (MifReconstructPipeline.h:15, no macro, Private/); all four UnrealEd statics verbatim at cited lines; default-path mirror re-read (MifBridgeReconstruct.cpp:51-57); create_editable_child IS in the SelfManaged set with the exact "compiles + saves" comment (line anchor drifted, see Phase-2 log); Events CVar default 1 confirmed (MifReconstructEvent.cpp:54-58). No dialog in the headless copy path (hazard grep clean). NOTE for merge: `mode=function` duplicates K1's kr_reconstruct_function — ship one shape.

### kr_reconstruct_status
**Purpose**: poll the single kr job slot — phase, per-function/per-event done-counts, node counts,
compile numbers, and the final result payload for whichever kind of job (reconstruct / verify /
census / classify) is or was running; the other half of the request/poll contract that keeps a
multi-second, editor-stalling decompile from blocking an HTTP connection.
**Engine API**: none beyond reading the plugin's own job record (a plain struct owned by the new
`MifKrJobManager`); counters are written by the already-bound delegate lambdas
(`KR/Private/MifKismetReconstructorModule.cpp:18–37`) and by `FKismetGraphDecompiler` accessors:
```cpp
int32 GetNodesCreated() const { return NodesCreated; }
int32 GetStatementsProcessed() const { return StatementsProcessed; }
bool WasDegraded() const { return bDegraded; }
```
`KR/Private/AssetGeneration/KismetGraphDecompiler.h:76–78`
**Export**: n/a (all plugin-internal state under model (b)). **Export promotion required: none.**
**Module**: MifKismetReconstructor (as above). MifBridge: none.
**Guards**: none.
**Bucket**: read-only — pure query of a POD job record; must be listed read-only or every poll
pushes an empty undo entry (`MB/Private/MifBridgeCommon.cpp:237–239` rationale). Under model (b) the
bucket is declared in the registration descriptor (section B).
**Async**: no (this IS the poll half).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| jobId | id | string | latest job | no |
Unrecognised parameter ⇒ error. Unknown jobId ⇒ `found:false` plus the latest job's id in the
message (only the last record is retained).
**Payload** (fields fixed here so the implementer and server.py agree):
`{found, jobId, kind: reconstruct|verify|census|classify, state: queued|running|done|failed, phase,
bpName, sourceClassPath, targetPath, startedAt, elapsedMs, functionsTotalEstimate, functionsDone,
functionsReconstructed, functionsDegraded, eventsDone, eventsReconstructed, nodesCreated,
compile:{errors,warnings,firstError}, result:{...kind-specific, see each endpoint}, error}`.
For `kind:"census"`, additionally `{bpDone, bpTotal, pass, fail, skip, runningTotals:{ident, equiv,
intentional, drift, missing, uncomparable}}` — live per-BP because the census slices one BP per tick.
**Failure modes**: none destructive. Honesty requirements: (1) during a single-BP job the HTTP pump
is stalled (HttpServerModule.h:25/:60 — game-thread ticker), so polls answer between ticks only; the
payload carries `note:"single-BP jobs are atomic; progress counters advance per census slice or on
completion"`; (2) after editor restart the record is gone → `found:false` (job records are
in-memory only, stated in the field docs).
**Cooked**: n/a (reads no assets).
**Verify**: numbers only by construction — start a job, poll: `state` must transition
queued→running→done within the job's lifetime; `functionsDone == functionsAttempted` at `done`;
`elapsedMs > 0`; a second concurrent request must have failed while `state=="running"`.
**Score**: U4 E4 R5 → tier 0 — mandatory pair of kr_reconstruct_request; also the poll endpoint for
the three verification endpoints below.
**Phase-2 verdict**: CONFIRMED — decompiler counter accessors verbatim (KismetGraphDecompiler.h:76-78); the decisive HTTP-pump claim re-verified (`FHttpServerModule : ... FTSTickerObjectBase` and `bool Tick(float) override` at HttpServerModule.h:25/:60); per-event decompiler-instance claim confirmed (:265-266 comment: one instance per event). NOTE for merge: K1 proposes a separate kr_batch_reconstruct_status — unify on this shared-slot poll design.

### kr_verify_fidelity
**Purpose**: answer "is this cooked BP safe to edit via reconstruction?" with numbers — reconstruct
+ compile a THROWAWAY transient child of the cooked BP, then diff every reconstructed function's
recompiled bytecode against the cooked original (strict/lossy normalisation), returning the full
fidelity report (identical / equivalent / intentional / REAL drift / missing / uncomparable + raw
and adjusted scores + root-cause first-drift) instead of log lines a bridge agent cannot read.
**Engine API**:
```cpp
DECLARE_DELEGATE_RetVal_FourParams(bool, FOnVerifyBlueprintFidelity,
	UBlueprintGeneratedClass* /*SourceBPGC*/, UBlueprint* /*ReconBP*/,
	const TArray<UFunction*>& /*AttemptedFuncs*/, FBlueprintFidelityReport& /*OutReport*/);

KISMET_API FOnVerifyBlueprintFidelity& GetBlueprintFidelityVerifier();
```
`Editor/Kismet/Public/CompiledBlueprintReconstructor.h:109–113` (the report struct is
`FBlueprintFidelityReport`, :71–104, with `Scored()/HasScore()/Score()/AdjustedScore()` :91–103 and
the `NoScore = -1.0f` sentinel :97 — the endpoint MUST reproduce the "n/a, never 1.000" rule).
The verifier implementation is plugin-side:
```cpp
static bool VerifyBlueprint(UBlueprintGeneratedClass* SourceBPGC, UBlueprint* ReconBP,
                            const TArray<UFunction*>& AttemptedFuncs, FBlueprintFidelityReport& Out)
```
`KR/Private/Verify/MifFidelityVerifier.cpp:432–433`, bound via `MifKr_BindFidelityVerifier()`
(:609–612) from module startup (`MifKismetReconstructorModule.cpp:42`).
The mint-transient-child + populate + compile pipeline it needs is **file-static in the engine TU**:
```cpp
static UBlueprint* RunReconstructOnce(UBlueprintGeneratedClass* SourceBPGC, UClass* ParentClass, bool bAsChild,
                                      FUncookedCopyStats& OutStats, FCompilerResultsLog& OutResults)
```
`Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp:1089–1090` (inside
`namespace CompiledBlueprintCopyAction`, :100) — NOT linkable from any other module. **This endpoint
therefore requires ONE new engine-fork export** (the fork is ours; CompiledBlueprintReconstructor.h
is the established modkit extension header):
```cpp
// PROPOSED addition to Editor/Kismet/Public/CompiledBlueprintReconstructor.h — refactor of
// VerifyFidelityCmd's body (CompiledBlueprintCopyAction.cpp:1356–1470) into a callable:
KISMET_API bool RunHeadlessFidelityVerify(UBlueprintGeneratedClass* SourceBPGC,
	FBlueprintFidelityReport& OutReport, FString& OutError,
	int32* OutFunctionsAttempted = nullptr, int32* OutFunctionsReconstructed = nullptr,
	int32* OutCompileErrors = nullptr, int32* OutCompileWarnings = nullptr);
```
It mints the transient child (`MakeUniqueObjectName(GetTransientPackage(), ...)` pattern, :1101–1105),
roots it across the compile (:1113 — GC guard), runs `PopulateUncookedCopy` + `CompileBlueprint`
(:1117–1122), refuses to score a failed compile (:1426–1431 precedent), executes the bound verifier
with `Stats.AttemptedFunctions` as the denominator (:1435 — the denominator MUST be the attempted
set, `FUncookedCopyStats` comment :535–537), then `RemoveFromRoot()` + `RF_Transient` (:1468–1469).
Nothing is saved or registered.
**Export**: `KISMET_API` (verbatim macro used by every existing symbol in that header: :24, :37, :61,
:113). **Export promotion required: the ONE engine-fork function above** (Kismet module rebuild).
Plugin-side: none — the handler lives in the reconstructor module.
**Module**: engine `Kismet` module — already linked by BOTH plugins (`MB/MifBridge.Build.cs:26`,
`KR/MifKismetReconstructor.Build.cs:26`). No new module dependency on either side.
**Guards**: none (editor modules).
**Cost (asked explicitly)**: YES it compiles and YES it compares. Full child reconstruction
(disassemble+transform+decompile per function) + one full `CompileBlueprint` (:1122) + per function
BOTH sides are disassembled again (`Normalize` calls `Dis.SerializeFunction(F)`,
`MifFidelityVerifier.cpp:329–330`) and emitted TWICE each (strict + lossy, :420–421); a drifting
function additionally pays the classifier's LCS alignment, capped at 2000 statements / 4.2M cells /
200k sim iterations (`MifDriftClassifier.cpp:81–83`). Multi-second on any real BP ⇒ **needs async**.
**Bucket**: self-managed — runs a full compile (the transient child), the canonical self-managed
trigger (`MB/Private/MifBridgeCommon.cpp:272–276`).
**Async**: request+poll. This endpoint starts a `kind:"verify"` job in the shared slot; poll
`kr_reconstruct_status`. Result payload (in `status.result`): every `FBlueprintFidelityReport` field
verbatim — `{compared, identical, equivalent, intentional, drift, missing, uncomparable, firstDrift,
intentTally, firstIntentional, scored, score, adjustedScore}` — with `score/adjustedScore` emitted as
`null` when `HasScore()` is false (NEVER 1.0 and never the -1 sentinel: :96–97 says callers must
branch), plus `{functionsAttempted, functionsReconstructed, compile:{errors,warnings}}`.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| sourceAsset | blueprint, bpName, path | string | — | yes (strict, resolution as kr_reconstruct_request) |
| classifyIntentional | classify | bool | true | no — scoped override of the `mif.kr.ClassifyIntentional` CVar (default 1, `MifDriftClassifier.cpp:57–62`) for this job only, restored after; `false` reproduces the pre-classifier audit baseline (containment invariant: Drift_off == Intentional_on + Drift_on) |
No `variant` parameter exists ON PURPOSE: fidelity is CHILD-ONLY — a sibling copy mints into the
transient package and copies components, so every `EX_ObjectConst`/`EX_Context` component reference
differs by object path and reports systematic FALSE drift (engine refusal text
`CompiledBlueprintCopyAction.cpp:1369–1377`; verifier header `MifFidelityVerifier.cpp:20–21`).
Passing `variant` ⇒ unrecognised-parameter error with that explanation.
**Failure modes**:
- job slot busy / source unresolvable → as kr_reconstruct_request.
- verifier delegate unbound (reconstructor plugin not loaded) → cannot happen under model (b) — the
  endpoint only exists when the plugin is loaded; the engine-side check (:1408–1411) stays as
  belt-and-braces.
- child copy fails to compile → job `done`, `result.score = null`,
  `error:"<BP> failed to compile (<n> errors) — fidelity not measured"` (mirrors :1426–1431; a
  fidelity number over stale bytecode would be a lie).
- BP with zero comparable functions (e.g. all-event BP — 735/1250 of the corpus per
  `CompiledBlueprintCopyAction.cpp:1314`) → `scored:0, score:null` and a note; never 1.000.
**Cooked**: designed for cooked sources; also accepts authored/loose testbed BPs (deliberate,
:1393–1395 — that is the ground-truth loop: author a BP via MifBridge → reconstruct → diff).
**Verify**: (1) on a known-good corpus BP, `identical+equivalent+intentional+drift+missing ==
scored` and `compared == functionsAttempted - missing - uncomparable`… all internal sums must
reconcile (they are ints from one struct — assert them); (2) `classifyIntentional:false` re-run must
satisfy `drift_off == intentional_on + drift_on` exactly (the classifier containment invariant,
`MifDriftClassifier.cpp:53–62`); (3) authored-testbed ground truth: build a trivial BP with
create_blueprint/add_* endpoints, cook-free verify of its own child must score 100% identical.
**Score**: U5 E2 R3 → tier 1 — turns "it compiled" into "it provably behaves like the original" for
the edit-cooked-content workflow; effort includes the one engine-fork export + job plumbing.
**Phase-2 verdict**: CORRECTED — NAME COLLISION: K1_reconstructor_toolkit.md also proposes `kr_verify_fidelity` with a CONFLICTING design (K1: synchronous, no job slot; here: request+poll) and a differently-shaped proposed export (`RunHeadlessFidelityVerify` here vs K1's `RunBlueprintFidelityVerify` — different names, different out-params). All citations in THIS entry re-verified exact: delegate + report struct verbatim (CompiledBlueprintReconstructor.h:109-113, :71-104 incl. NoScore=-1.0f :97), `VerifyBlueprint` static decl exact (MifFidelityVerifier.cpp:432-433), `RunReconstructOnce` confirmed file-static in namespace (:100/:1089), honesty rule :1426-1431, sibling refusal :1364-1377, classifier caps 2000/4.2M/200k (MifDriftClassifier.cpp:81-83), double-emission cost chain (Normalize disassembles + EmitAll strict+lossy) confirmed. The merge must pick ONE name+design; the async form here is better supported by the verified cost evidence.

### kr_drift_census
**Purpose**: run the fidelity verify across a path-filtered SET of cooked Blueprints with the
classifier's census instrument on, producing (a) live per-BP progress + running corpus totals over
HTTP and (b) the on-disk `DriftCensus_<ts>.csv` of every UNCLAIMED drift edit — the data a rule
author needs to decide which drift classes dominate (spec 4: "rules must be written FROM DATA, not
guessed") — without a human babysitting a console for an hour.
**Engine API**: per-BP work = the same `RunHeadlessFidelityVerify` export as kr_verify_fidelity
(shared; no additional engine change). Enumeration mirrors the batch harness exactly:
`FARFilter` over `UBlueprintGeneratedClass` + `UBlueprint` class paths with `bRecursiveClasses`,
dedup by package, `PKG_Cooked` + widget-class gate (`IsCompiledBlueprintAsset`,
`CompiledBlueprintCopyAction.cpp:102–119`; enumeration loop :1167–1186) — reimplemented plugin-side
with `IAssetRegistry` (already a plugin dep, Build.cs:22). The census instrument is the existing CVar:
```cpp
static TAutoConsoleVariable<int32> CVarDriftCensus(
	TEXT("mif.kr.DriftCensus"),
	0,
	TEXT("1 = append every UNCLAIMED drift edit to <ProjectSaved>/MifKr/DriftCensus_<ts>.csv (rule discovery).\n")
	TEXT("Diagnostic only — it never changes a verdict."),
	ECVF_Default);
```
`KR/Private/Verify/MifDriftClassifier.cpp:66–71` — forced to 1 for the job's duration and restored
after (in-process `IConsoleVariable::Set`, NOT a run_console wrap: the endpoint's substance is the
sliced batch loop + structured totals, the CVar is scoped state around it).
**Export**: shares kr_verify_fidelity's single engine export. Plugin-side: **none** (the classifier
and its CVar are same-module). The gate predicate `IsCompiledBlueprintAsset` is engine-static and is
REIMPLEMENTED (4 class-path comparisons + PKG_Cooked, cited above) — flagged as a deliberate,
documented duplication with the citation as the sync contract.
**Module**: none new beyond section B's coupling.
**Guards**: none.
**Bucket**: self-managed — compiles one throwaway Blueprint per slice.
**Async**: request+poll, `kind:"census"`, sliced ONE BP PER TICK (the only kr job with real mid-job
progress — see the HTTP-pump analysis in the inventory). GC every 25 BPs
(`CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS)`, mirroring :1303). Status reports `bpDone/bpTotal`,
pass/fail/skip, running `ident/equiv/intentional/drift/missing/uncomparable`, and on completion
`result:{censusCsvPath, corpusFidelity, corpusAdjusted, intentTally, skipTaxonomy:{resolve,parent,mint}}`
(the three-way skip taxonomy is load-bearing — the engine keeps SKIP_RESOLVE/SKIP_PARENT/SKIP_MINT
distinct on purpose, :1084–1087).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| pathFilter | filter, pathSubstr | string ("*" = all cooked BPs) | "/Game/" | no |
| startIndex | start | int ≥ 0 (crash-resume cursor, matches `mif.kr.ReconstructAll`'s :1144) | 0 | no |
| maxCount | limit | int (0 = unbounded) | 50 | no — the default bounds an accidental whole-corpus run; "run everything" is `maxCount:0` explicitly |
| classifyIntentional | classify | bool | true | no (as kr_verify_fidelity) |
Unrecognised parameter ⇒ error.
**Failure modes**:
- job slot busy → standard message.
- zero matches → immediate `done` with `bpTotal:0` and the filter echoed back (`traced`-style echo:
  the caller must see what was actually enumerated).
- a BP that crashes the editor mid-census: the flushed per-BP BEGIN log marker (mirroring
  :1222–1224) + `startIndex` resume make the culprit findable and the run resumable — status
  payload documents `resumeHint: startIndex = bpDoneAbsolute`.
- census CSV unopenable (disk) → job continues, `result.censusCsvPath: null`,
  warning `"census file could not be opened — verdicts unaffected (census is diagnostic only)"`.
**Cooked**: cooked-only BY DESIGN (the `PKG_Cooked` gate) — loose BPs are exactly what the census
must not dilute. Stated in the field docs.
**Verify**: run with `maxCount:5, pathFilter:"/Game/"`: `bpDone` must advance across polls (proves
slicing), final `pass+fail+skip == bpTotal == 5`; totals must equal the sum of five individual
kr_verify_fidelity runs on the same BPs with the same classify flag (bit-identical counts — the
containment/summation invariants make this checkable); the CSV at `result.censusCsvPath` must exist
and its row count equal the reported unclaimed-edit count.
**Score**: U3 E2 R3 → tier 2 — valuable (rule discovery + corpus regression gate over HTTP), design
done here, but it is a long-running batch tool rather than a daily-driver.
**Phase-2 verdict**: CONFIRMED — CVarDriftCensus registration verbatim incl. help text (MifDriftClassifier.cpp:66-71, default 0); batch enumeration mirror exact (:1167-1186, IsCompiledBlueprintAsset :102); GC-every-25 confirmed (`% 25 == 24` at :1303); skip-taxonomy rationale verbatim (:1084-1087); one-BP-per-tick slicing is the only design compatible with the verified game-thread HTTP ticker. Purpose is distinct from K1's kr_batch_reconstruct_request (drift census vs pass/fail sweep) — no collision, but both must share ONE job slot at merge.

### kr_classify_drift
**Purpose**: per-FUNCTION drift verdicts for one Blueprint — for each reconstructed function:
`identical | equivalent | intentional | drift | missing | uncomparable`, the deduped intentional
reasons, and for REAL drift the ROOT-CAUSE edit (first UNCLAIMED edit, not the first raw diff, which
is usually a jump-reordinal cascade artefact) — the drill-down that `kr_verify_fidelity`'s aggregate
counts and single `firstDrift` string cannot provide.
**Engine API**: the classifier verdict struct and entry point (plugin-side):
```cpp
struct FVerdict
{
	bool  bIntentional = false;
	TArray<FString> Reasons;
	bool  bHasRoot = false;
	int32 RootCookedOrdinal = INDEX_NONE;
	int32 RootReconOrdinal  = INDEX_NONE;
	FString RootCooked;
	FString RootRecon;
};
...
FVerdict Classify(const FString& BPName, const FString& FuncName,
                  const TArray<FString>& CookedLossy, const TArray<FString>& ReconLossy);
```
`KR/Private/Verify/MifDriftClassifier.h:28–46, :55–56`. `Classify` is invoked exclusively on the
drift path inside `VerifyBlueprint` (`MifFidelityVerifier.cpp:540–541`); the lossy streams it needs
exist only inside that loop, so the endpoint runs the SAME headless verify
(`RunHeadlessFidelityVerify`, shared engine export) with a **plugin-internal per-function capture
buffer**: a module-static `TArray<FPerFunctionVerdict>*` sink that `VerifyBlueprint` appends to when
armed (function name, verdict class, reasons, root ordinals/texts, cooked/recon statement counts,
optionally the ±2 lossy window that the `MIF_KR_DEBUG` drift log already formats, :583–592).
Game-thread-only by the existing threading contract, so a static sink is race-free. The engine
delegate signature is NOT widened — the codebase explicitly refuses delegate-arity changes as a
lockstep hazard (`CompiledBlueprintReconstructor.h:54–56`), which is why the sink lives plugin-side.
**Export**: shares kr_verify_fidelity's engine export; plugin-side refactor only
(`MifFidelityVerifier.cpp` gains the sink; `MifDriftClassifier.h` types stay Private).
**Export promotion required: none.**
**Module**: none new beyond section B's coupling.
**Guards**: none.
**Bucket**: self-managed (compiles the throwaway child, same as verify).
**Async**: request+poll, `kind:"classify"`, single-BP job in the shared slot. Result (in
`status.result`): `functions:[{name, verdict, reasons[], rootCookedOrdinal, rootReconOrdinal,
rootCooked, rootRecon, cookedStmts, reconStmts, window?[]}]` + the aggregate report (same fields as
kr_verify_fidelity, so the two can be cross-checked).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| sourceAsset | blueprint, bpName, path | string | — | yes (strict) |
| function | functionName, func | string (filter: only report this function; the whole-BP verify still runs — the pipeline is per-BP) | — | no |
| includeWindow | window | bool (attach the ±2-statement cooked/recon lossy window per drifting function) | false | no |
| classifyIntentional | classify | bool | true | no — `false` makes every drifting function report `verdict:"drift"` with `bHasRoot:false` fallback semantics (`MifDriftClassifier.h:48–50`: Classify returns default-declined when off) |
Unrecognised parameter ⇒ error. `function` naming a function that was never attempted ⇒
`found:false` for it plus the attempted-function list (never a silent empty row).
**Failure modes**: as kr_verify_fidelity (busy / unresolvable / compile-fail ⇒ no verdicts), plus:
- classifier declined on caps (>2000 statements, LCS cell cap, sim cap — `MifDriftClassifier.cpp:81–83`)
  → that function reports `verdict:"drift", reasons:["classifier-declined:cap"]`, root absent, exactly
  mirroring the conservative decline-to-REAL contract (`MifDriftClassifier.h:20`).
**Cooked**: as kr_verify_fidelity (cooked sources + authored testbeds).
**Verify**: per-function rows must SUM to the aggregate counts returned alongside
(`count(verdict==identical) == identical`, etc. — an internal consistency assert the implementation
should also carry); on a corpus BP with known `intentTally` (e.g. "flowstack=N;outparam=M" from a
prior verify), the per-function reasons must reproduce the tally exactly; `classifyIntentional:false`
must yield zero `intentional` rows and unchanged identical/equivalent counts (containment).
**Score**: U3 E2 R3 → tier 2 — the debugging microscope for decompiler work; shares nearly all its
plumbing with kr_verify_fidelity.
**Phase-2 verdict**: CONFIRMED — `FVerdict` struct + `Classify` signature verbatim (MifDriftClassifier.h:28-46, :55-56, namespace MifKr::DriftClassify); classifier invoked only on the drift path inside VerifyBlueprint (call site re-read); ±2 lossy window formatting exists under MIF_KR_DEBUG (:583-592); decline-to-REAL cap contract confirmed (header :20, caps .cpp:81-83); delegate-arity refusal verbatim (CompiledBlueprintReconstructor.h:54-56) — the plugin-side sink is the correct placement.

---

## B. THE COUPLING MODEL (decision)

### Current state (verified, all four coupling surfaces)

1. **No direct dependency in either direction.** `MB/MifBridge.Build.cs:12–46` lists no
   `MifKismetReconstructor`; `KR/MifKismetReconstructor.Build.cs:12–27` lists no `MifBridge`;
   NEITHER `.uplugin` has a `"Plugins"` section at all (both files read in full).
2. **All existing coupling is via the ENGINE FORK's Kismet module** — three KISMET_API delegate
   accessors + one KISMET_API function in `Editor/Kismet/Public/CompiledBlueprintReconstructor.h`
   (:24, :37, :61, :113). The reconstructor BINDS at module load
   (`KR/Private/MifKismetReconstructorModule.cpp:18–42`); MifBridge CONSUMES
   `CreateEditableBlueprintCopy` in `H_create_editable_child`
   (`MB/Private/MifBridgeReconstruct.cpp:60`) and documents the soft behaviour: *"Graphs are filled
   with decompiled nodes iff the MifKismetReconstructor delegate is bound; otherwise function/event
   graphs are signature-only stubs"* (:73–74). MifBridge loads and works without the reconstructor —
   the property to preserve.
3. **Loading phases**: MifKismetReconstructor = `"LoadingPhase": "Default"`
   (`MifKismetReconstructor.uplugin:17`); MifBridge = `"LoadingPhase": "PostEngineInit"`
   (`MifBridge.uplugin:17`). Default runs BEFORE PostEngineInit, so the reconstructor's
   StartupModule always precedes MifBridge's.
4. **MifBridge's registry is entirely private and static**: `Handlers()` is a function-local
   `static TMap<FString, FHandlerFn>` populated once by `MIF_BIND` (`MB/Private/MifBridgeCommon.cpp:29–34`);
   the buckets are two file-static `TSet<FString>` literal lists (:239–270, :277–301);
   `IsCompileHeavyEndpoint` derives from self-managed (:355–365); HTTP routes are bound
   **once, per endpoint name, at server start** from `GetEndpointNames()`
   (`MB/Private/MifBridgeServer.cpp:88–108`), and the server starts inside
   `FMifBridgeModule::StartupModule` at PostEngineInit (`MB/Private/MifBridge.cpp:58–61`).
5. **Public/ dir check (the prompt's premise was wrong)**: MifBridge DOES have
   `MB/Public/MifBridge.h` (module interface, 31 lines). What it does NOT have is any exported
   symbol — `FMifBridgeModule` carries no `MIFBRIDGE_API`. So nothing must be *created* directory-wise;
   the new registration header is the module's **first** exported surface (UBT already defines the
   `MIFBRIDGE_API` macro for every module; it is simply unused today).

### Option (a) — hard link (REJECTED)

Mechanics: add `"MifKismetReconstructor"` to `MB/MifBridge.Build.cs` PrivateDependencyModuleNames +
a `"Plugins": [{"Name":"MifKismetReconstructor","Enabled":true}]` ref in `MifBridge.uplugin`; write
`H_kr_*` handlers inside MifBridge.

- Compile-time checked — the one genuine advantage.
- **MifBridge fails to load (and the whole editor-automation surface dies) whenever the
  reconstructor is absent or disabled** — inverting the deliberate soft design documented at
  `MifBridgeReconstruct.cpp:73–74` and `CompiledBlueprintReconstructor.h:2–4` ("If nothing is bound,
  the action behaves exactly as before — no regression").
- **It does not even avoid export work.** Every capability this axis needs is `Private/` and
  unexported: `MifReconstructFunctionIntoGraph` / `MifReconstructEventIntoGraph`
  (`KR/Private/MifReconstructPipeline.h:15, :29` — plain free functions, no macro),
  `MifKr::Fidelity::VerifyBlueprint` (`MifFidelityVerifier.cpp:432`, file-static namespace),
  `MifKr::DriftClassify::Classify` (`MifDriftClassifier.h:55`, Private header). A hard link would
  force promoting the pipeline, the verifier sink, the job counters, and the classifier types into
  `KR/Public/` with `MIFKISMETRECONSTRUCTOR_API` — a large, permanent export surface whose only
  consumer is one plugin. Under (b) that entire surface stays private because the handlers live
  next to it.
- Every future provider (the mission anticipates more `Mif*` plugins) would add another hard dep to
  MifBridge — N×fragility in the one plugin that must always load.

### Option (b) — registration interface (RECOMMENDED — no blocker found)

The generalisation of the delegate pattern that already works: providers register named handlers
into MifBridge's registry at module load; endpoints exist only when the provider is installed;
`self_audit` names the provider per endpoint.

**B.1 The interface — new file `MB/Public/MifBridgeEndpointRegistry.h`** (MifBridge's first
exported symbols; the Public/ dir already exists):

```cpp
#pragma once
#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

namespace MifBridge
{
	// Same shape as the internal FHandlerFn (MifBridgeHandlers.h:24) — (In, Out), game thread,
	// unrecognised-parameter-is-an-error contract applies to external handlers identically.
	using FExternalHandler = TFunction<void(const TSharedRef<FJsonObject>& /*In*/, const TSharedRef<FJsonObject>& /*Out*/)>;

	// ONE bucket per endpoint, by construction — the twin-set contradiction class that
	// self_audit polices for built-ins (policyContradictions) cannot exist for externals.
	enum class EEndpointBucket : uint8 { ReadOnly, SelfManaged, Transacted };

	struct FExternalEndpointDesc
	{
		FString Name;                                   // lowercase snake_case, verb_noun
		EEndpointBucket Bucket = EEndpointBucket::Transacted;
		FString Provider;                               // e.g. "MifKismetReconstructor" — surfaced by self_audit
		FString Summary;                                // one-liner for self_audit / docs
		FExternalHandler Handler;
	};

	// Register at module startup (any time before the HTTP route table is built at PostEngineInit).
	// Returns false + OutError on: name collision (built-in or external), empty/invalid name,
	// empty handler, or registration after the route table is live.
	MIFBRIDGE_API bool RegisterExternalEndpoint(FExternalEndpointDesc Desc, FString* OutError = nullptr);

	// Module shutdown symmetry (the reconstructor already unbinds all three engine delegates on
	// shutdown — MifKismetReconstructorModule.cpp:51-54). Returns the number removed.
	MIFBRIDGE_API int32 UnregisterExternalEndpoints(const FString& Provider);
}
```

**B.2 MifBridgeCommon.cpp changes (all mechanical, cited against current lines):**
- Add a function-local `static TMap<FString, FExternalEndpointDesc>& ExternalRegistry()` beside
  `Handlers()` (:29–34). Function-local static ⇒ initialised on first call ⇒ safe to populate before
  `FMifBridgeModule::StartupModule` runs (see B.4).
- `GetEndpointNames()` (:230–235) merges both maps — this alone makes the route binding loop
  (`MifBridgeServer.cpp:88–108`) and `self_audit`'s endpoint list pick externals up with no server change.
- `RunEndpoint` (:367–393): if `Handlers().Find` misses (:371), look up `ExternalRegistry()` before
  failing "unknown endpoint"; dispatch honouring the descriptor's bucket exactly as :383–392 does for
  the static sets (ReadOnly/SelfManaged ⇒ no blanket transaction; Transacted ⇒ `FScopedTransaction`).
- `IsReadOnlyEndpoint` (:239) / `IsSelfManagedEndpoint` (:277): after the static `TSet` miss, consult
  `ExternalRegistry()`'s bucket. **`IsCompileHeavyEndpoint` (:355–365) then needs zero changes** — it
  already derives from `IsSelfManagedEndpoint`, so an external SelfManaged endpoint is automatically
  excluded from `batch`'s open transaction (the exact guard that protects kr_* jobs, which compile).
- `H_self_audit` (:308–353): add `provider` per endpoint (`"MifBridge"` for built-ins, the
  descriptor's Provider for externals — the field the mission demands), an `externalProviders`
  array with per-provider counts, and keep `policyContradictions` semantics unchanged (externals are
  single-bucket by type and cannot contradict).
- Threading: registration happens in provider StartupModule (main thread, pre-server) and dispatch
  is game-thread only (`MifBridgeServer.cpp:199`) — no locking needed; assert
  `IsInGameThread() || !GIsRunning`-style guard in `RegisterExternalEndpoint` and document it.

**B.3 MIF_DECL/MIF_BIND coexistence + the three-way registry:** built-ins are untouched — the
`grep -c 'MIF_DECL(' == grep -c 'MIF_BIND('` invariant still holds because external endpoints never
appear in either file. The registry contract becomes: *built-ins = MIF_DECL + MIF_BIND + @mcp.tool;
externals = one `RegisterExternalEndpoint` call (in the provider) + @mcp.tool (in server.py)* — and
`self_audit.endpoints` (which is built from the LIVE merged map) remains the single source of truth
that the README's server.py diff is checked against, now with `provider` making drift attributable.

**B.4 Load order (the concern, resolved with citations):**
- Provider registration must precede route binding. Route binding happens in
  `FMifBridgeServer::Start()` (`MifBridgeServer.cpp:88`), called from MifBridge's StartupModule at
  **PostEngineInit** (`MifBridge.cpp:58–61`; `MifBridge.uplugin:17`). The reconstructor's
  StartupModule runs at **Default** phase (`MifKismetReconstructor.uplugin:17`), which is strictly
  earlier. ✔
- "Can the reconstructor call an exported MifBridge function before MifBridge's StartupModule has
  run?" Yes: the new Build.cs link makes the reconstructor DLL import the MifBridge DLL, so the OS
  loader maps MifBridge when the reconstructor loads; the registry is a function-local static
  (initialise-on-first-use), and `RegisterExternalEndpoint` touches nothing initialised by
  `StartupModule` (server, menus, token). This must be stated as a hard rule in the header comment:
  *the registration API must never touch module-startup state*.
- Late registration (after server start) fails loudly (`RegisterExternalEndpoint` returns false:
  "route table already live — register from your module's StartupModule"). Dynamic route rebinding
  is a v2 option (the Stop/Start toggle already exists, `MifBridge.cpp:150–158`), not v1 scope.
- LoadingPhase changes required: **none** (current values already order correctly).

**B.5 Reconstructor-side changes:**
- `KR/MifKismetReconstructor.Build.cs`: add `"MifBridge"` to PrivateDependencyModuleNames, wrapped in
  a directory-existence check that also sets `PrivateDefinitions.Add("WITH_MIFBRIDGE=1")`, so the
  reconstructor still builds if the MifBridge plugin folder is deleted (zero hard deps in the
  shipping sense; see UNVERIFIED for the enabled-but-folder-present edge).
- `MifKismetReconstructor.uplugin`: add `"Plugins": [{ "Name": "MifBridge", "Enabled": true,
  "Optional": true }]`.
- New file `KR/Private/MifKrBridgeEndpoints.cpp` (`#if WITH_MIFBRIDGE`): all `H_kr_*` handlers + a
  `MifKr_RegisterBridgeEndpoints()` called from `StartupModule` (after the delegate binds) +
  `MifKr_UnregisterBridgeEndpoints()` from `ShutdownModule`.
- New file `KR/Private/MifKrJobManager.{h,cpp}`: the one-slot job state machine + delegate-wrapper
  counters (shared by all four request endpoints).

**Decision: (b).** No hard blocker was found; every mechanism it needs was verified against source
(static-map merge, bucket fallback, route-bind timing, phase ordering, DLL-load ordering). The one
place (b) cannot reach — the engine-TU-static transient-verify pipeline — is a problem for (a)
exactly equally, and is solved by one KISMET_API export in the fork's existing modkit header
(kr_verify_fidelity entry).

**Phase-2 verdict on section B**: CONFIRMED with stale line anchors. Re-verified against CURRENT
source: `Handlers()` is a function-local static TMap populated via a local `#define MIF_BIND`/`#undef`
(:29 — anchor still exact); route binding iterates `GetEndpointNames()` once per name at `Start()`;
dispatch miss-path + bucket branching + blanket `FScopedTransaction` all as described (RunEndpoint now
at :396-422); `IsCompileHeavyEndpoint` derives from `IsSelfManagedEndpoint` (zero-change claim holds);
`grep MIFBRIDGE_API` still 0 hits; `MB/Public/` contains only MifBridge.h; LoadingPhases
PostEngineInit/Default at both .uplugin:17; neither .uplugin has a Plugins section; KR Build.cs deps
as cited. ANCHOR DRIFT: MifBridgeCommon.cpp gained four endpoints since the sweep (redo_transactions,
list_transactions, list_dirty_packages, save_dirty_packages — none in the brief's 160 list), shifting
cited lines by ~+6 to +29 (GetEndpointNames :230→:236, IsReadOnlyEndpoint :239→:245,
IsSelfManagedEndpoint :277→:294, H_self_audit :308→:337, RunEndpoint :367→:396); MifBridgeHandlers.h
`MIF_DECL` macro is :153 not :141. One anchor was imprecise beyond drift: the
`SetTimerForNextTick` deferral CODE lives in MifBridgeWorld.cpp:144/:204 (the handlers header carries
only the new_level modal-deadlock comment, now ~:316-318) — the precedent claim itself is true.
B.2's edits must be re-anchored against live line numbers at implementation time.

## C. Can K1's read endpoints ship the same way? (end-state)

**Yes — and they are the easiest case.** The K1 surface (`mif.kr.ListBP` / `FindBP` / `DumpBP` /
`AnalyzeUbergraph` internals in `KR/Private/MifBlueprintDumper.cpp` and
`KR/Private/Analysis/MifUbergraphAnalyzer.cpp`, plus the exported
`FKismetBytecodeDisassemblerJson` — `KR/Public/Toolkit/KismetBytecodeDisassemblerJson.h:9`,
`class MIFKISMETRECONSTRUCTOR_API`) is read-only and entirely plugin-internal: handlers in
`MifKrBridgeEndpoints.cpp` call those internals directly, registered with
`EEndpointBucket::ReadOnly`. Zero export promotion, zero MifBridge changes beyond the registry API,
zero engine changes. The recommended end-state holds: **every kr_* endpoint (K1 reads + K2
pipeline/verify) lives in the reconstructor plugin; MifBridge gains only the registration API; the
only remaining cross-plugin artefact is the soft, optional reconstructor→MifBridge link.**

**File-touch list for the implementation session** (complete; nothing else moves):

| # | File | Change |
|---|---|---|
| 1 | `MB/Public/MifBridgeEndpointRegistry.h` | NEW — the interface in B.1 |
| 2 | `MB/Private/MifBridgeCommon.cpp` | ExternalRegistry + merge in GetEndpointNames/RunEndpoint + bucket fallbacks + self_audit `provider` (B.2) |
| 3 | `KR/MifKismetReconstructor.Build.cs` | conditional `MifBridge` dep + `WITH_MIFBRIDGE` (B.5) |
| 4 | `MifKismetReconstructor.uplugin` | optional plugin ref to MifBridge (B.5) |
| 5 | `KR/Private/MifKrBridgeEndpoints.cpp` | NEW — all kr_* handlers + register/unregister |
| 6 | `KR/Private/MifKrJobManager.h/.cpp` | NEW — one-slot job machine + delegate-wrapper counters |
| 7 | `KR/Private/MifKismetReconstructorModule.cpp` | call register in StartupModule / unregister in ShutdownModule |
| 8 | `KR/Private/Verify/MifFidelityVerifier.cpp` | per-function verdict capture sink (kr_classify_drift) |
| 9 | `D:/UE532/Engine/Source/Editor/Kismet/Public/CompiledBlueprintReconstructor.h` + `Private/CompiledBlueprintCopyAction.cpp` | ONE new `KISMET_API RunHeadlessFidelityVerify` (refactor of VerifyFidelityCmd :1356–1470) — needed only by the three verify endpoints; the reconstruct pair ships without any engine change |
| 10 | `tools/ue5-mcp-bridge/server.py` | `@mcp.tool()` per kr_* endpoint |
| 11 | `MB/Private/MifBridgeServer.cpp` | UNCHANGED in v1 (routes pick externals up via GetEndpointNames) — listed only to record that it was checked |

Suggested batch order: (1) files 1–2 + a trivial `kr_ping` external endpoint to prove the registry
end-to-end via self_audit's provider field; (2) files 3–7 + kr_reconstruct_request/status; (3) file 9
+ kr_verify_fidelity; (4) file 8 + kr_classify_drift + kr_drift_census.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

1. **The transient reconstruct+verify pipeline is unlinkable.** `RunReconstructOnce`
   (`CompiledBlueprintCopyAction.cpp:1089`), `PopulateUncookedCopy` (:892), `CopyFunctionStubs`
   (:551), `FUncookedCopyStats` (:531) and `IsCompiledBlueprintAsset` (:102) are all `static` inside
   `namespace CompiledBlueprintCopyAction` in a Private engine TU — no export macro, not reachable
   from ANY module. The only exported whole-copy entry is `CreateEditableBlueprintCopy`
   (KISMET_API), which **saves an asset** (:1631–1635) — unusable for a throwaway verify. Hence the
   one proposed fork export; without it, kr_verify_fidelity/kr_drift_census/kr_classify_drift cannot
   be built honestly (a save-then-delete workaround would churn the AssetRegistry and content dir on
   every verify).
2. **Five of the mission's "eleven console commands" are not commands.** `mif.kr.DumpFull`
   (`MifFidelityVerifier.cpp:48`), `mif.kr.Events` (`MifReconstructEvent.cpp:54`),
   `mif.kr.LatentResume` (:64), `mif.kr.ClassifyIntentional` (`MifDriftClassifier.cpp:57`),
   `mif.kr.DriftCensus` (:66) are `TAutoConsoleVariable`s. There is no "resume latent actions"
   operation to wrap — LatentResume is a reconstruction-behaviour toggle (default now 1). CVar
   get/set is already covered by `run_console`; no kr_ endpoint should wrap them (only the scoped
   in-process overrides inside verify/census jobs, which are state management, not wrapping).
3. **`mif.kr.Reconstruct` (console) is not an endpoint substrate.** It is compiled only under
   `MIF_KR_DEBUG` (`MifReconstructCommand.cpp:118, :212`; the flag is 1 today but documented
   "Ship OFF", `MifReconstructorDebug.h:5–10`), prints to the log, and opens an asset editor UI
   (:198–204). The endpoint calls `MifReconstructFunctionIntoGraph` (compiled in all configs, :31–32)
   and never the command.
4. **Fidelity of a SIBLING copy is impossible by design, permanently.** Sibling copies mint into the
   transient package and COPY components ⇒ systematic false drift on every component reference; the
   engine refuses (`CompiledBlueprintCopyAction.cpp:1158–1165, :1369–1377`) and the verifier is
   CHILD-MODE-ONLY (`MifFidelityVerifier.cpp:20–21`). kr_verify_fidelity therefore has no variant
   parameter at all.
5. **Per-function verdicts cannot flow through the engine delegate.** `FOnVerifyBlueprintFidelity`
   returns one aggregate `FBlueprintFidelityReport`; widening its arity is exactly the lockstep
   engine+plugin hazard the codebase refuses on principle (`CompiledBlueprintReconstructor.h:54–56`).
   Solved plugin-side (capture sink), not engine-side.
6. **No mid-Blueprint progress is observable over HTTP, ever.** `FHttpServerModule` is a game-thread
   ticker (`HttpServerModule.h:25, :60`); a synchronous reconstruct starves the socket pump itself.
   Any status design promising live per-function progress for a single-BP job would be lying;
   only per-BP-sliced batch jobs (census) have real progress.
7. **Reconstruction cannot be threaded.** The pipeline holds `FGCScopeGuard` across each function
   because the IR carries raw un-rooted UObject pointers (`MifReconstructCommand.cpp:37–44`), spawns
   nodes via NewObject, and resolves against the skeleton class that a recompile replaces
   (`MifReconstructEvent.cpp:79–84`). Worker-thread "async" is structurally impossible; the job model
   is deferred-tick game-thread slices.
8. **Late (post-server-start) endpoint registration is invisible** until a server restart — routes
   are bound once per name at `Start()` (`MifBridgeServer.cpp:88–108`). Constraint is enforced
   loudly by the registration API rather than discovered silently.
9. **Events must never enter the fidelity denominator.** Event bodies are ubergraph slices with no
   own-class UFunction to diff; the recon ubergraph is regenerated with different layout/offsets and
   shared regions duplicated BY DESIGN (`CompiledBlueprintCopyAction.cpp:539–547`). kr_* payloads
   keep `events*` fields separate from `functions*` fields, matching `FUncookedCopyStats`.

## UNVERIFIED

- **Wall-clock cost of a big-BP reconstruct/verify**: no measured timings exist anywhere in source.
  Verified proxies only: the 8s GC stall + ~40-function crash note (`MifReconstructCommand.cpp:43`),
  corpus counts (1256 BPs / PASS 1228 / fidelity 54.65% / events 85.9%), and the per-BP crash-flush
  design of the batch harness. The implementation session must measure one small and one large BP
  (e.g. BP_BaseNPC) and record numbers in the live proof.
- **UBT behaviour when the MifBridge plugin folder exists but the plugin is disabled in the
  .uproject**: the proposed `Directory.Exists`-based conditional dep would still link and then fail.
  5.3 ModuleRules has no clean "is plugin enabled" query I could verify this sweep. Fallback if it
  bites: unconditional dep (both plugins ship together in this modkit) — flagged for the implementer.
- **`FHttpServerModule` listener-thread internals**: I verified the module is an
  `FTSTickerObjectBase` (header), not the full request read path in `HttpListener.cpp`; the
  "no socket pump during a stall" conclusion follows from the ticker contract but the .cpp was not
  read line-by-line.
- **K1's exact endpoint names** (kr_list_bp / kr_dump_bp / …): the K1 axis file did not exist in
  `work/` at sweep time; section C references the surface, not final names. Merge step must
  reconcile naming.
- **`FKismetGraphDecompiler` counters during EVENT reconstruction**: `GetNodesCreated()` exists per
  decompiler instance (one per event — `KismetGraphDecompiler.h:265–266`), but I did not verify how
  `MifReconstructEventIntoGraph` aggregates/reports them; the job-manager node tally should use
  `Graph->Nodes.Num()` deltas instead (stated in the design), which needs no such plumbing.

## Coverage log

Covered this sweep: the full reconstruction pipeline (entry points, phases, GC/threading
constraints, cost drivers), both console-command flows (`mif.kr.Reconstruct` plugin-side;
`mif.kr.ReconstructAll`/`mif.kr.VerifyFidelity` engine-side), the complete verification stack
(verifier rules R1–R13 at header level, report struct, classifier contract, both verify CVars), the
delegate precedent end-to-end (declaration → binding → consumption), MifBridge's registry/bucket/
dispatch/route/self_audit internals, both Build.cs + both .uplugins, the stock-engine signatures the
endpoints rest on, and one live self_audit probe (160 endpoints, healthy). Five endpoints proposed
(1 read-only poll, 4 self-managed async requests sharing one job slot); coupling decision written as
an executable spec with an 11-item file-touch list.

Not covered (left to other axes / future passes): K1's read/dump/analyze endpoint specs
(`MifBlueprintDumper.cpp` and `MifUbergraphAnalyzer.cpp` internals were only skimmed for
registration facts); `MifUbergraphSlicer` internals; `PropertyTypeHelper`; dynamic route rebinding
(v2); a `kr_job_cancel` endpoint (design note only); server.py tool-stub text.

## Phase-2 verification log (adversarial re-check, 2026-07-26)

- Every cited signature re-opened and matched verbatim: CompiledBlueprintReconstructor.h (all 114
  lines), CompiledBlueprintCopyAction.cpp (:96-140, :525-570, :1080-1348, :1356-1478, :1596-1639),
  MifReconstructPipeline.h, MifReconstructCommand.cpp, MifReconstructEvent.cpp (CVar defaults BOTH 1),
  MifFidelityVerifier.cpp, MifDriftClassifier.{h,cpp}, MifKismetReconstructorModule.cpp (delegate
  binds :18-42, unbinds :51-54), KismetGraphDecompiler.h/_Reconstruct.cpp (200k guard :212),
  KismetIntermediateFormat.h, both Build.cs + both .uplugins, HttpServerModule.h:25/:60.
- Hazard grep: FMessageDialog only in the interactive Execute path (:971/:980/:1019) — untouched by
  every proposed endpoint; no FScopedSlowTask anywhere in either TU; CollectGarbage only in the batch
  loop (:1303/:1306) exactly as the census design mirrors; plugin .cpp files clean.
- Cost/async claims audited against the loops they cite: RunReconstructOnce is one mint + populate +
  ONE CompileBlueprint (:1101-1123); Normalize re-disassembles BOTH sides and emits strict+lossy
  (double emission confirmed in source); the game-thread-ticker HTTP pump conclusion stands — the
  "no mid-BP progress, per-BP slicing only" job model is correctly derived, and the one-BP-per-tick
  census is the only entry with real progress.
- Collision check: no `kr_` name in the brief's 160 list or 01_CATALOGUE.md (grep = 0). ONE intra-audit
  collision: `kr_verify_fidelity` also proposed by K1 with a conflicting sync design and a
  differently-shaped proposed engine export (see CORRECTED stamp). Functional overlaps flagged for
  merge: kr_reconstruct_request(mode=function) vs K1 kr_reconstruct_function; this file's shared job
  slot vs K1's kr_batch_reconstruct_request/status pair.
- Negative results #1-#9 spot-verified against source — all hold (incl. :1631-1635 SavePackage in the
  only exported whole-copy path, and the namespace-static status of all five engine-TU symbols).
- Section B coupling spec verified against the LIVE registry code; line anchors drifted (four new
  endpoints landed in MifBridgeCommon.cpp after the sweep) — see the verdict block under the decision.
- Verdict tally: 4 CONFIRMED, 1 CORRECTED (kr_verify_fidelity — cross-axis name/design collision),
  0 DEMOTED.
