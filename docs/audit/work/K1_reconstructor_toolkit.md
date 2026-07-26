# Axis K1 — MifKismetReconstructor READ/ANALYSIS capability as MifBridge endpoints
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork "CookedEditorModKit"). Agent: phase-1 breadth._

Scope: everything MifKismetReconstructor (D:/DDS2SDK/Game/Plugins/MifKismetReconstructor, ~10.7k lines)
can already do from the console, re-derived as structured-JSON HTTP endpoints that call the underlying
code directly (never `run_console`). Mission source: docs/10_FULL_SCOPE_EXPANSION_PROMPT.md Phase 1.

Path shorthand used below:
- `KR/` = `D:/DDS2SDK/Game/Plugins/MifKismetReconstructor/Source/MifKismetReconstructor/`
- `MB/` = `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/`
- Engine paths are relative to `D:/UE532/Engine/Source` per the brief.

## Surface inventory

### Complete console-object census (7 commands + 5 CVars — nothing else registers)

Sweep method: `grep FAutoConsoleCommand|TAutoConsoleVariable|mif\.kr\.` over the whole plugin source
tree, plus the engine-fork file the module log points at. Every hit read.

**Plugin-side commands — ALL inside `#if MIF_KR_DEBUG` (gate defaults ON: `KR/Private/MifReconstructorDebug.h:9-11`,
comment says "Ship OFF (set to 0) before any release"):**

| # | Command | Registration | Handler → call chain | Prints |
|---|---|---|---|---|
| 1 | `mif.kr.FindBP <substr>` | `KR/Private/MifBlueprintDumper.cpp:337-340` (`GMifKrFindBP`) | `MifKr_FindBP` (:323) → `MifKr_RegistryMatches` (:150) → `IAssetRegistry::GetAssets` with FARFilter {UBlueprintGeneratedClass, UBlueprint, bRecursiveClasses} then substring match on AssetName (strips `_C`) | one line per match `[<class>] <objectPath>` + count. Registry-wide: cooked + loose, loaded or not |
| 2 | `mif.kr.ListBP <substr>` | `KR/Private/MifBlueprintDumper.cpp:342-345` (`GMifKrListBP`) | `MifKr_ListBP` (:206) → `TObjectIterator<UBlueprintGeneratedClass>` name-contains filter | one line per LOADED BPGC path + count (loaded-state census, disjoint info from #1) |
| 3 | `mif.kr.DumpBP <substr\|/Path>` | `KR/Private/MifBlueprintDumper.cpp:347-350` (`GMifKrDumpBP`) | `MifKr_DumpBP` (:223) → `MifKr_ResolveBPGC` (:174; explicit path via `LoadObject`, else registry match + `GetAsset()`, else loaded-class fallback) → per own-`UFunction` with `Script.Num()>0`: `FKismetBytecodeDisassemblerJson::SerializeFunction` → writes one JSON per function + `_histogram.json` to `Saved/MifKismetReconstructor/<Class>/` | summary line `%d functions (%d with bytecode), %d statements, %d FAILED. Out: <dir>` + per-abort lines. Output goes to FILES, not the console |
| 4 | `mif.kr.Reconstruct <BP> <Func>` | `KR/Private/MifReconstructCommand.cpp:207-210` (`GMifKrReconstruct`) | `MifKr_Reconstruct` (:141) → find BPGC via registry (:120-139) → `FindFunctionByName` → `CreatePackage("/Game/Reconstructed/Recon_<BP>_<Fn>_<n>")` → `FKismetEditorUtilities::CreateBlueprint` (parented to the cooked class) → `FBlueprintEditorUtils::CreateNewGraph` + `AddFunctionGraph<UFunction>` (:182-183) → **`MifReconstructFunctionIntoGraph`** (:33, the shared pipeline: disassemble → `FKismetBytecodeTransformer` → `FKismetGraphDecompiler::Run`, under one `FGCScopeGuard` :44) → `CompileBlueprint` → `MarkPackageDirty` + `AssetCreated` + `OpenEditorForAsset` | `ok=%d \| graphNodes=%d \| asset: <pkg>` |
| 5 | `mif.kr.AnalyzeUbergraph [pathSubstr] [startIndex]` | `KR/Private/Analysis/MifUbergraphAnalyzer.cpp:391-395` (`GMifKrAnalyzeUbergraph`) | `MifKr_AnalyzeUbergraph` (:76) → registry enumeration with **`PKG_Cooked` filter** (:96) + package dedup → per BP under `FGCScopeGuard`: `BPGC->UberGraphFunction`, disassemble ONCE, `MifUber::BuildStatements` / `DetectPrologue` / `RecoverEvent` (per thunk) / `WalkEvent` (per event, own stack) → 2 CSVs in `Saved/MifKr/` (29-col per-BP + per-event), flushed per BP | GO/NO-GO aggregate block (events recovered, shared/unreached statements, SHARED-LATENT count). READ-ONLY by design (file header :1-5) |

**Engine-fork commands — NOT debug-gated, live in `Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp`:**

| # | Command | Registration | Handler → call chain |
|---|---|---|---|
| 6 | `mif.kr.ReconstructAll [pathSubstr] [startIndex] [child] [verify]` | `CompiledBlueprintCopyAction.cpp:1345-1348` (`GMifKrReconstructAll`) | `ReconstructAll` (:1140) → registry enum via `IsCompiledBlueprintAsset` (:102, `PKG_Cooked` + BP/BPGC/WidgetBP/WidgetBPGC) → per BP: `ResolveBlueprintClass` (:121) → `RunReconstructOnce` (:1089: `CreateBlueprint` into `GetTransientPackage()`, `AddToRoot`, `PopulateUncookedCopy` (:892 — the F3 pipeline, invokes the plugin delegates), `CompileBlueprint` with silent `FCompilerResultsLog`) → tally + CSV per BP → `RemoveFromRoot` | 
| 7 | `mif.kr.VerifyFidelity <BPName> [child]` | `CompiledBlueprintCopyAction.cpp:1472-1475` (`GMifKrVerifyFidelity`) | `VerifyFidelityCmd` (:1356) → exact-name registry match, **no PKG_Cooked gate** (deliberate — authored-testbed support, comment :1393-1395) → `CanCreateBlueprintOfClass` → `RunReconstructOnce` (child mode; sibling refused with reasoned error :1369-1377) → `GetBlueprintFidelityVerifier().Execute(SourceBPGC, NewBP, Stats.AttemptedFunctions, Fid)` (:1435) → prints `FBlueprintFidelityReport` fields → `RemoveFromRoot` + `RF_Transient` (:1468-1469, throwaway — nothing saved) |

**CVars (behaviour gates, not operations):**

| CVar | Registration | Default | Meaning |
|---|---|---|---|
| `mif.kr.Events` | `KR/Private/MifReconstructEvent.cpp:54-58` | 1 | reconstruct event bodies by slicing the ubergraph (0 = bare stubs) |
| `mif.kr.LatentResume` | `KR/Private/MifReconstructEvent.cpp:64-68` | 1 | reconstruct latent (Delay/timeline) resume wiring (0 = refuse latent-reaching events) |
| `mif.kr.ClassifyIntentional` | `KR/Private/Verify/MifDriftClassifier.cpp:57-62` | 1 | split DRIFT into INTENTIONAL + REAL (0 = audit baseline) |
| `mif.kr.DriftCensus` | `KR/Private/Verify/MifDriftClassifier.cpp:66-71` | 0 | append unclaimed drift edits to `Saved/MifKr/DriftCensus_<ts>.csv` |
| `mif.kr.DumpFull` | `KR/Private/Verify/MifFidelityVerifier.cpp:48-52` | 0 | log the full cooked-vs-recon statement stream per compared function |

(The mission doc's list of "eleven console commands" conflates these: the real census is 7 commands +
5 CVars, and one command — `mif.kr.ReconstructAll` — is missing from the doc's list.)

### Export status of everything a handler could call

Exported today (verbatim):
```cpp
class MIFKISMETRECONSTRUCTOR_API FKismetBytecodeDisassemblerJson {   // KR/Public/Toolkit/KismetBytecodeDisassemblerJson.h:9
	TSharedPtr<FJsonObject> SerializeExpression(int32& ScriptIndex);                                  // :12
	TArray<TSharedPtr<FJsonValue>> SerializeFunction(UStruct* Function);                              // :15
	bool GetStatementLength(UStruct* Function, int32 StatementIndex, int32& OutStatementLength);      // :18
	bool FindFirstStatementOfType(UStruct* Function, int32 StartIndex, uint8 StatementOpcode, int32& OutStatementIndex); // :21
	bool bDisassemblyFailed = false;    // :25
	uint8 FailedOpcode = 0;             // :27
	int32 FailedAtIndex = -1;           // :29
	TMap<uint8, int32> OpcodeHistogram; // :31
};
class MIFKISMETRECONSTRUCTOR_API FPropertyTypeHelper {               // KR/Public/Toolkit/PropertyTypeHelper.h:7
    static FEdGraphPinType DeserializeGraphPinType(const TSharedRef<FJsonObject>& PinJson, UClass* SelfScope);   // :9
    static TSharedRef<FJsonObject> SerializeGraphPinType(const FEdGraphPinType& GraphPinType, UClass* SelfScope);// :10
    static bool ConvertPropertyToPinType(const FProperty* Property, FEdGraphPinType& OutType);                   // :11
};
```
Engine fork, `Editor/Kismet/Public/CompiledBlueprintReconstructor.h` (MifBridge already links `Kismet` —
`MB/MifBridge.Build.cs:26` — and already calls one of these from `MB/Private/MifBridgeReconstruct.cpp:60`):
```cpp
KISMET_API FOnReconstructBlueprintFunctionGraph& GetBlueprintFunctionGraphReconstructor();   // :24
KISMET_API UBlueprint* CreateEditableBlueprintCopy(UBlueprintGeneratedClass* SourceBPGC,
	const FString& TargetPackagePath, bool bAsChild, FText* OutError, bool bFullParent = false); // :37-38
KISMET_API FOnReconstructBlueprintEventGraph& GetBlueprintEventGraphReconstructor();         // :61
struct FBlueprintFidelityReport { int32 Compared; int32 Identical; int32 Equivalent; int32 Intentional;
	int32 Drift; int32 Missing; int32 Uncomparable; FString FirstDrift; FString IntentTally;
	FString FirstIntentional; /* Scored()/HasScore()/Score()/AdjustedScore() header-inline */ }; // :71-104
KISMET_API FOnVerifyBlueprintFidelity& GetBlueprintFidelityVerifier();                       // :113
```

NOT exported (each is a promotion target named in the entries below):
- `bool MifReconstructFunctionIntoGraph(UFunction*, UEdGraph*, UBlueprint*)` — `KR/Private/MifReconstructPipeline.h:15` (impl `KR/Private/MifReconstructCommand.cpp:33`, compiled in ALL configs — comment :31-32)
- `bool MifReconstructEventIntoGraph(UFunction*, UEdGraph*, UK2Node*, UBlueprint*)` — `KR/Private/MifReconstructPipeline.h:29`
- `void MifKr_ResetUbergraphCache()` — `KR/Private/MifReconstructPipeline.h:33`
- namespace `MifUber` free functions — `KR/Private/Analysis/MifUbergraphSlicer.h`: `BuildStatements` (:91-92), `DetectPrologue` (:95), `WalkEvent` (:104-105), `ClassifyEvent` (:111), `KindName` (:112), `RecoverEvent` (:139), `CountRawPointerHits` (:144); structs `FUberStmt` (:50), `FPrologue` (:73), `FWalkResult` (:81), `FEventEntry` (:114); `extern const int32 GMaxWalkIterations/GMaxJsonDepth` (:44-45)
- `FKismetBytecodeTransformer` — `KR/Private/AssetGeneration/KismetBytecodeTransformer.h` (class, no macro)
- `FKismetGraphDecompiler` — `KR/Private/AssetGeneration/KismetGraphDecompiler.h` (class, no macro)
- Engine fork statics (file-local, unlinkable): `RunReconstructOnce` (`CompiledBlueprintCopyAction.cpp:1089`), `PopulateUncookedCopy` (:892), `FUncookedCopyStats` (:531), `ResolveBlueprintClass` (:121), `IsCompiledBlueprintAsset` (:102)

### Integration model (applies to every entry's "Module" field)

MifBridge does not link MifKismetReconstructor today (`MB/MifBridge.Build.cs:19-46` — not listed), and
MifBridge exports nothing (`grep MIFBRIDGE_API` over MB/Source → zero hits), so the mission's
recommended "provider registers its endpoints into MifBridge" pattern (10_FULL_SCOPE_EXPANSION_PROMPT.md
option (b)) requires MifBridge to first grow an exported registrar. Two viable models; each entry states
its cost under the one it needs:

- **Model A (hard link)**: add `"MifKismetReconstructor"` to `MB/MifBridge.Build.cs` PrivateDependencyModuleNames
  + a `"Plugins": [{"Name":"MifKismetReconstructor","Enabled":true}]` clause in `MifBridge.uplugin`.
  Both plugins are `"Type":"Editor"`, `"EnabledByDefault": true`, Win64 (both .uplugin files read) — no
  runtime leak. Under Model A only the two `MIFKISMETRECONSTRUCTOR_API` classes are reachable; every
  Private symbol needs a Public/ header move + API macro (exact decls listed per entry).
- **Model B (provider registration)**: MifBridge exports a `MIFBRIDGE_API` registrar
  (RegisterEndpoint(name, handler, bucket, provider)); MifKismetReconstructor links MifBridge and binds
  its handlers at StartupModule. Handlers then live INSIDE the reconstructor module, so NO reconstructor
  symbol needs exporting — the export cost moves to MifBridge (one registrar) plus `self_audit` growing a
  `provider` field. LoadingPhase check: MifBridge is `PostEngineInit`, reconstructor is `Default` —
  reconstructor loads first, so its StartupModule cannot call into a not-yet-loaded MifBridge; the
  registrar must be pull-based (MifBridge drains a static registry at ITS startup) or the reconstructor
  must defer via `FModuleManager::Get().OnModulesChanged()`. That design cost is real and is why the
  per-entry "Module" lines below price Model A explicitly; Model B is a one-time platform investment that
  amortises across all 9 endpoints AND every future `Mif*` provider.

Handlers must NOT be placed inside `#if MIF_KR_DEBUG` and must NOT call the `MifKr_*` command statics
(all file-`static`, debug-gated, and they print instead of returning). The building blocks named per
entry are the correct layer.

### Verification assets (cooked, container-origin — confirmed by same-day live probes in LIVE_PROBES.md)

My own live probe this session failed (see Negative results #10), so candidates are taken from the
2026-07-26 LIVE_PROBES.md session (same audit, bridge build "Jul 26 2026"):

| Asset (objectPath) | Size class | Known numbers to verify against |
|---|---|---|
| `/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C` | large (281 fns incl. inherited, 113 own; the documented crash-hardening target) | own-function count 113 (LIVE_PROBES.md G1); ubergraph present |
| `/Game/Blueprints/Pawns/NPC/Oponents/Behaviour/BP_OponentPatrolRoute.BP_OponentPatrolRoute_C` | medium (7 own fns) | own fns: OponentDestroyed, CheckPathAllowed, GetNextPatrolPoint, RegisterPatrol, GetClosestPoint, GetPatrolLocation, SetupEnds |
| `/Game/Blueprints/Enviro/Markers/BP_SegmentedPathTaskMarker.BP_SegmentedPathTaskMarker_C` | small (4 own fns) | own fns: OnRep_PathActive, SegmentOverlapp, AddPathBox, TaskUpdate |

Corpus-level ground truth already measured by the plugin itself (usable as end-to-end checks):
1277 cooked BPs analysed, 5871/5871 events recovered, 0/122,638 ubergraph statements unreached
(`KR/Private/Analysis/MifUbergraphSlicer.h:4-5`); batch PASS 1228/1256, fidelity 54.65%
(`KR/Private/MifReconstructEvent.cpp:53`); event reconstruction 85.9%, FAIL 10/481
(`KR/Private/MifReconstructEvent.cpp:56`).

---

## Proposed endpoints

### kr_list_cooked_blueprints
**Purpose**: registry census of cooked Blueprints (the analyzer/batch enumeration) over HTTP — package-deduped, `PKG_Cooked`-filtered, with loaded-state — so an agent can size and iterate the reconstructable corpus without guessing from `find_assets`.
**Engine API**:
```cpp
virtual bool GetAssets(const FARFilter& Filter, TArray<FAssetData>& OutAssetData, bool bSkipARFilteredAssets=true) const = 0;
```
`Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:243`. Filter shape and `PKG_Cooked` dedup logic mirror `KR/Private/Analysis/MifUbergraphAnalyzer.cpp:84-102` (FARFilter with `UBlueprintGeneratedClass`+`UBlueprint` ClassPaths, `bRecursiveClasses=true`, then `(A.PackageFlags & PKG_Cooked)` + `SeenPackages` dedup). Loaded flag via `FindObject<UBlueprintGeneratedClass>(nullptr, *ObjectPath)` (no load).
**Export**: pure-virtual interface method (no macro needed; obtained via `FAssetRegistryModule`) | **Module**: none — AssetRegistry already in `MB/MifBridge.Build.cs:36`; NO reconstructor dependency at all (this endpoint replicates 19 lines of enumeration, acceptable because the filter is data, not logic) | **Guards**: none (MifBridge is editor-only)
**Bucket**: read-only — pure registry query, no object mutation.
**Async**: no (registry query over the in-memory index; existing `find_assets` precedent).
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| pathContains | pathFilter, path | string | "/Game/" | no ("*" = all mounted roots incl. /ChristmasDlc, /DDS2Casino) |
| cookedOnly | — | bool | true | no (false additionally lists loose BPs, flagged `cooked:false`) |
| includeWidgets | — | bool | true | no (WidgetBPGC arrives via bRecursiveClasses; false filters class name) |
| offset | — | int | 0 | no |
| limit | — | int | 200 | no (max 2000) |
Unrecognised parameter → `error: unrecognised parameter '<name>' (accepted: pathContains, cookedOnly, includeWidgets, offset, limit)`.
**Failure modes**:
- registry not yet populated at editor boot → `error: asset registry still scanning; retry after initial scan completes`
- `limit` > 2000 → `error: limit 5000 exceeds maximum 2000; page with offset`
**Cooked**: this endpoint EXISTS for cooked content — container-only BPGCs appear because the modkit premounts them into the registry; loose assets appear when `cookedOnly:false`. Returns per entry: `{objectPath, packageName, class, cooked, loaded}` + `{total, matched, truncated}`.
**Verify**: `total` for `pathContains:"*"` vs the analyzer's measured 1277 cooked BP packages (allow drift for DLC mounts); `BP_BaseNPC` row present with `cooked:true`; `offset:0,limit:100` then `offset:100,limit:100` → no overlap, counts sum.
**Score**: U3 E5 R5 → tier 1
**Phase-2 verdict**: CONFIRMED — GetAssets signature verbatim (IAssetRegistry.h:243), analyzer filter/dedup mirror re-read (MifUbergraphAnalyzer.cpp:84-102), AssetRegistry dep confirmed (MB Build.cs:36), bucket correct, no name collision (brief 160-list and 01_CATALOGUE.md both grep-clean for kr_).

### kr_dump_blueprint
**Purpose**: `mif.kr.DumpBP` as structured JSON returned inline (no `Saved/` files, no log scraping): per-function bytecode inventory + opcode histogram for a cooked BPGC — the first HTTP-visible read of cooked Blueprint logic.
**Engine API**:
```cpp
TArray<TSharedPtr<FJsonValue>> SerializeFunction(UStruct* Function);          // resets bDisassemblyFailed/FailedOpcode/FailedAtIndex/OpcodeHistogram per call
```
`KR/Public/Toolkit/KismetBytecodeDisassemblerJson.h:15` (impl `KR/Private/Toolkit/KismetBytecodeDisassemblerJson.cpp:948-965`; SelfScope = `Function->GetTypedOuter<UClass>()` :950). Iteration: `TFieldIterator<UFunction> It(BPGC, EFieldIteratorFlags::ExcludeSuper)` + `Func->Script` (public field, `Runtime/CoreUObject/Public/UObject/Class.h:409`) — exactly `KR/Private/MifBlueprintDumper.cpp:253-289`. Resolution: `LoadObject<UBlueprintGeneratedClass>` with `_C` fallback then `UBlueprint->GeneratedClass`, matching `MB/Private/MifBridgeReconstruct.cpp:31-39` precedent.
**Export**: `MIFKISMETRECONSTRUCTOR_API` on the class (h:9) — all methods exported | **Module**: Model A: add `MifKismetReconstructor` to MifBridge.Build.cs + uplugin Plugins clause (plugin: project-local, Editor, EnabledByDefault:true). No promotions needed — this is the exported class working as designed | **Guards**: none
**Bucket**: read-only — disassembly writes nothing; `UStruct::Script` is only read.
**Async**: no. Single-asset `LoadObject` + disassembly is in-frame (the analyzer disassembled all 1277 BPs' ubergraphs in one console command; one BP is the cheap case).
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| asset | sourceAsset, path | string | — | YES (strict: empty ⇒ `error: asset required (objectPath of the cooked BP, e.g. /Game/.../BP_Foo.BP_Foo_C)`) |
| functionFilter | function | string | "" | no (substring over function names) |
| includeStatements | — | bool | false | no (false ⇒ per-function metadata only: name, scriptBytes, numStatements, flags, disassemblyFailed) |
| maxStatementsPerFunction | — | int | 500 | no (per-function truncation with `truncated:true` + `totalStatements`) |
| includeHistogram | — | bool | true | no (aggregate opcode histogram, names via the disassembler's Inst strings) |
| offset / limit | — | int | 0 / 100 | no (over the function list, sorted by name) |
**Output size control** (the DumpFull lesson — full statement dumps are megabytes on BP_BaseNPC): with
`includeStatements:true` and no `functionFilter`, `limit` is force-capped at 10 and the response says so
(`note:"includeStatements without functionFilter caps limit at 10"`); per-function statement arrays
truncate at `maxStatementsPerFunction`.
**Failure modes**:
- asset not found → `error: asset '<x>' not found — pass the objectPath (try the .<Name>_C class path); use kr_list_cooked_blueprints to search`
- asset resolves to non-BPGC → `error: '<x>' is a <Class>, not a BlueprintGeneratedClass`
- unknown opcode mid-function → NOT an endpoint error: that function's entry carries `disassemblyFailed:true, failedOpcode, failedAtIndex` (mirrors dumper :274-282); response-level `functionsFailed` count
**Cooked**: primary target. A pak-mounted BPGC loads via LoadObject (proven at 1277-BP scale) and its `Script` carries live pointers in-process (`KR/Public/Toolkit/KismetBytecodeDisassemblerJson.h:8`). Loose/uncooked BP asset: also works — its compiled GeneratedClass has Script; if never compiled this session the load compiles it. WidgetBPGC: expected to work (subclass; same UFunction storage) — see UNVERIFIED #1.
**Verify**: against `/Game/Blueprints/Enviro/Markers/BP_SegmentedPathTaskMarker.BP_SegmentedPathTaskMarker_C`: function list ⊇ {OnRep_PathActive, SegmentOverlapp, AddPathBox, TaskUpdate} + `ExecuteUbergraph_BP_SegmentedPathTaskMarker`; every listed function has `scriptBytes == Func->Script.Num()` > 0 or is flagged; histogram totals equal the sum of per-function statement counts; on BP_BaseNPC_C expect own functions ≈ 113 + thunks (LIVE_PROBES.md number).
**Score**: U5 E4 R5 → tier 0 — closes the documented #1 gap ("cooked Blueprint graphs are unreadable", 02_GOTCHAS.md §3 currently routes agents to run_console)
**Phase-2 verdict**: CONFIRMED — MIFKISMETRECONSTRUCTOR_API class export re-read (h:9), SerializeFunction impl at cited lines (.cpp:948-965, SelfScope :950), Script field at Class.h:409, dumper iteration mirror :253-289 verbatim, per-function failure fields :274-281, read-only bucket sound.

### kr_disassemble_function
**Purpose**: full JSON statement stream for ONE function of a cooked BPGC (the per-function half of DumpBP), paginated — what an agent reads before deciding to reconstruct or patch.
**Engine API**: same `SerializeFunction` as above (h:15); function lookup:
```cpp
UFunction* FindFunctionByName(FName InName, EIncludeSuperFlag::Type IncludeSuper = EIncludeSuperFlag::IncludeSuper) const;
```
(UClass; called with `EIncludeSuperFlag::ExcludeSuper` exactly as `KR/Private/MifReconstructCommand.cpp:157`).
**Export**: `MIFKISMETRECONSTRUCTOR_API` class export (h:9); FindFunctionByName is CoreUObject | **Module**: Model A hard link, no promotions | **Guards**: none
**Bucket**: read-only.
**Async**: no.
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| asset | sourceAsset, path | string | — | YES (strict) |
| function | functionName | string | — | YES (strict; exact FName match, ExcludeSuper; error lists near-miss names on failure) |
| statementOffset | offset | int | 0 | no (index into the statement ARRAY, not a byte offset — say so in the field name docs) |
| statementLimit | limit | int | 2000 | no (max 5000; `truncated` + `totalStatements` always returned) |
| includeRaw | — | bool | true | no (false strips per-statement fields down to {Inst, StatementIndex} for cheap CFG views) |
**Failure modes**:
- function not on class → `error: function 'Foo' not found on BP_Bar_C (own functions only; inherited functions live on the parent class — call kr_disassemble_function on that class)`
- function has no bytecode → `error: 'Foo' has no bytecode (Script.Num()==0) — it is a stub/interface/BlueprintImplementableEvent shell`
- disassembly abort → `disassemblyFailed:true` + `failedOpcode` (byte), `failedOpcodeName`, `failedAtIndex`, plus every statement decoded before the abort (the disassembler's degrade contract, h:11)
**Cooked**: primary target; identical story to kr_dump_blueprint. Note for callers: `StatementIndex` fields are BYTE offsets into Script (`KismetBytecodeDisassemblerJson.cpp:959-961`), pagination is by array index.
**Verify**: `SegmentOverlapp` on BP_SegmentedPathTaskMarker_C returns N>0 statements with `totalStatements == N` when unpaginated; paginating with statementLimit=5 walks the same stream (concatenation equals the unpaginated array); `ExecuteUbergraph_BP_BaseNPC` on BP_BaseNPC_C returns `truncated:true` with default limit and a stable totalStatements across calls.
**Score**: U5 E4 R5 → tier 0
**Phase-2 verdict**: CONFIRMED — byte-offset StatementIndex claim proven against the impl (KismetBytecodeDisassemblerJson.cpp:959-961). One strengthening note: the quoted FindFunctionByName decl omits its `COREUOBJECT_API` prefix (Class.h:3028) — the macro is present in source, so linkability is even more solid than stated.

### kr_list_events
**Purpose**: the event census of a cooked BP — every event thunk with kind (Event/BndEvt/InpActEvt/SequenceEvent), recovered ubergraph entry offset, param count, and the authoritative frame→param map — the data an agent needs to reason about event wiring without reconstructing anything.
**Engine API**:
```cpp
bool RecoverEvent(UFunction* Thunk, UFunction* UberFunc, FEventEntry& Out);   // KR/Private/Analysis/MifUbergraphSlicer.h:139
EEventKind ClassifyEvent(const FString& Name);                                // :111
```
plus `FEventEntry` fields Name/Kind/EntryOffset/NumParams/FrameLets/FastCall/Status/bRecovered/FrameParamMap (:114-134) and the thunk-enumeration filter (skip `FUNC_Delegate|FUNC_UbergraphFunction`, `Script.Num()==0`) from `KR/Private/Analysis/MifUbergraphAnalyzer.cpp:197-226`. Ubergraph handle: `TObjectPtr<UFunction> UberGraphFunction` — `Runtime/Engine/Classes/Engine/BlueprintGeneratedClass.h:697` (field read; `UBlueprintGeneratedClass` is `MinimalAPI`, h:630 — StaticClass/Cast work cross-module, field access needs no export).
**Export**: **NOT exported** — `MifUber` free functions carry no API macro and the header is `Private/`. Model A promotion required: move `Analysis/MifUbergraphSlicer.h` → `Public/Analysis/` and add `MIFKISMETRECONSTRUCTOR_API` to each free function (`BuildStatements` :91, `DetectPrologue` :95, `WalkEvent` :104, `ClassifyEvent` :111, `KindName` :112, `RecoverEvent` :139, `CountRawPointerHits` :144) and to the two `extern const int32` (:44-45); structs are POD-in-header, no macro needed. Under Model B: zero promotions (handler lives in-module). | **Module**: Model A hard link + the above promotion; or Model B | **Guards**: none. Wrap the per-BP work in `FGCScopeGuard` exactly as the analyzer does (:163) — raw UFunction* handling.
**Bucket**: read-only — thunk disassembly only.
**Async**: no (per-thunk disassembly of one BP; the analyzer did this for all 1277 BPs in one command).
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| asset | sourceAsset, path | string | — | YES (strict) |
| kind | — | string enum: `all\|event\|bndEvt\|inpActEvt\|sequenceEvent` | "all" | no (unknown value → error listing the enum) |
**Failure modes**:
- no ubergraph → `ok:true, events:[], note:"no UberGraphFunction — this BP has no event graph bodies (functions only)"` (not an error; NO_UBERGRAPH is a normal analyzer status, MifUbergraphAnalyzer.cpp:168-172)
- thunk that calls no ubergraph → excluded, counted in `realFunctions` (the analyzer's "not a failed event" rule :207-221)
- entry recovery failure → event listed with `recovered:false, status:"<reason>"` (degrade-per-event, never abort)
**Cooked**: primary target — events only EXIST as thunk+ubergraph on cooked BPGCs. Loose BPs: their compiled class has the same shape; works.
**Verify**: BP_SegmentedPathTaskMarker_C must list `SegmentOverlapp`-shaped bound events / `OnRep_PathActive` etc. with `recovered:true, entryOffset >= 0`, and `sum(numParams)` matching the thunks' CPF_Parm counts; corpus check: run over the three candidates — every event `recovered:true` (measured corpus rate is 5871/5871).
**Score**: U4 E3 R4 → tier 1
**Phase-2 verdict**: CONFIRMED — full promotion list re-verified against current decls: all 7 MifUber free functions + 2 externs carry NO macro in Private/ (RecoverEvent :139, ClassifyEvent :111, etc. exact); slicer impl is ungated (MifUbergraphSlicer.cpp:1 comment confirms the move out of MIF_KR_DEBUG); BPGC MinimalAPI :630 + UberGraphFunction :697 exact. Two off-by-one struct anchors (FPrologue is :72 not :73, FWalkResult :80 not :81) — immaterial.

### kr_analyze_ubergraph
**Purpose**: per-BP ubergraph slice statistics as JSON — prologue shape, per-event reachability, shared/unreached statement counts, SHARED-LATENT offsets (the split-feasibility verdict) — `mif.kr.AnalyzeUbergraph` for ONE Blueprint, returned instead of CSV-on-disk.
**Engine API**:
```cpp
void BuildStatements(const TArray<TSharedPtr<FJsonValue>>& RawStmts, const FString& LatentStructPath,
	const FString& UberFuncName, TArray<FUberStmt>& OutStmts, TMap<int32, int32>& OutIndexByOffset);  // MifUbergraphSlicer.h:91-92
FPrologue DetectPrologue(const TArray<FUberStmt>& Stmts);                                             // :95
FWalkResult WalkEvent(const TArray<FUberStmt>& Stmts, const TMap<int32, int32>& IndexByOffset,
	const FPrologue& Prologue, int32 EntryOffset, TSet<int32>& OutReached);                           // :104-105
```
plus `RecoverEvent` (:139) and the exact orchestration of `KR/Private/Analysis/MifUbergraphAnalyzer.cpp:183-283` (disassemble ubergraph ONCE, walk per event with its own stack — invariants [A][B][C] at MifUbergraphSlicer.h:13-31). LatentStructPath = `FLatentActionInfo::StaticStruct()->GetPathName()` (`Runtime/Engine/Classes/Engine/LatentActionManager.h`, UHT-generated StaticStruct).
**Export**: NOT exported — same promotion set as kr_list_events (one promotion serves both). | **Module**: Model A + slicer promotion, or Model B | **Guards**: none; `FGCScopeGuard` around the per-BP body (analyzer :163).
**Bucket**: read-only — "builds NO graphs, mints NO Blueprints and compiles NOTHING" (analyzer header :3-5).
**Async**: no for one BP. The all-corpus sweep is NOT this endpoint (see kr_batch_reconstruct for the async pattern; a corpus analyze sweep is a tier-3 variant, not proposed).
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| asset | sourceAsset, path | string | — | YES (strict) |
| includePerEvent | — | bool | true | no (per-event rows: name, kind, entryOffset, numParams, frameParamMap, reached, status) |
| includeOffsets | — | bool | false | no (true adds sharedLatentOffsets[] and unreachedOffsets[] arrays — bounded: shared/unreached are ~0 on the measured corpus) |
**Failure modes**:
- no ubergraph / no bytecode → `ok:true` with `status:"NO_UBERGRAPH"|"NO_BYTECODE"` (analyzer statuses, not errors)
- ubergraph disassembly abort → partial stats + `disasmAborted:true, failedOpcode, failedAtIndex` and `note:"counts are partial"` (analyzer Notes convention :313-317)
- walk cap hit → `walkCapHit:true` + `note:"counts are a LOWER BOUND"` (:251-256)
**Cooked**: primary target (the analyzer gates its SWEEP on PKG_Cooked :96; the per-BP endpoint accepts any BPGC with an ubergraph so authored testbeds work — same reasoning as VerifyFidelityCmd's deliberate non-gate, CompiledBlueprintCopyAction.cpp:1393-1395).
**Verify**: numbers must reconcile internally: `analysedStmts == reached1 + shared + unreached`; on the three candidate BPs expect `unreached == 0` and `eventsRecovered == events` (corpus measured 0 unreached / 100% recovery); `sharedLatent <= latentStmts`.
**Score**: U3 E3 R4 → tier 1
**Phase-2 verdict**: CONFIRMED — BuildStatements/DetectPrologue/WalkEvent signatures verbatim (MifUbergraphSlicer.h:91-92, :95, :104-105), analyzer statuses NO_UBERGRAPH/NO_BYTECODE (:168-178), Notes convention (:311-320), walk-cap warning (:251-256), READ-ONLY header (:1-5) all re-read exact.

### kr_reconstruct_function
**Purpose**: decompile ONE cooked function into a real, compilable Blueprint graph and report structured results (ok/degraded, node count, compile errors) — `mif.kr.Reconstruct` minus the log-scrape and the forced editor-tab open; the targeted micro-tool where `create_editable_child` is the whole-asset tool.
**Engine API**:
```cpp
bool MifReconstructFunctionIntoGraph(UFunction* SourceFunc, UEdGraph* TargetGraph, UBlueprint* OwnerBP);  // KR/Private/MifReconstructPipeline.h:15
static UNREALED_API UBlueprint* CreateBlueprint(UClass* ParentClass, UObject* Outer, const FName NewBPName, enum EBlueprintType BlueprintType, TSubclassOf<UBlueprint> BlueprintClassType, TSubclassOf<UBlueprintGeneratedClass> BlueprintGeneratedClassType, FName CallingContext = NAME_None); // Editor/UnrealEd/Public/Kismet2/KismetEditorUtilities.h:124
static UNREALED_API class UEdGraph* CreateNewGraph(UObject* ParentScope, const FName& GraphName, TSubclassOf<class UEdGraph> GraphClass, TSubclassOf<class UEdGraphSchema> SchemaClass); // Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h:329
static void AddFunctionGraph(UBlueprint* Blueprint, class UEdGraph* Graph, bool bIsUserCreated, SignatureType* SignatureFromObject) // template, header-defined :390-400
static UNREALED_API void CompileBlueprint(UBlueprint* BlueprintObj, EBlueprintCompileOptions CompileFlags = EBlueprintCompileOptions::None, class FCompilerResultsLog* pResults = nullptr ); // KismetEditorUtilities.h:169
```
Orchestration = `KR/Private/MifReconstructCommand.cpp:141-205` verbatim, minus `OpenEditorForAsset` (add `open:false` default) and minus the log-only result. `FBlueprintEditorUtils` the CLASS is unexported (BlueprintEditorUtils.h:140 — bare `class FBlueprintEditorUtils`); the methods used are individually `UNREALED_API` and `AddFunctionGraph` is an in-header template — MifBridge already links UnrealEd and instantiates such templates today.
**Export**: `MifReconstructFunctionIntoGraph` NOT exported. Model A promotion: move `MifReconstructPipeline.h` → `Public/` and prefix the three functions (:15, :29, :33) with `MIFKISMETRECONSTRUCTOR_API`. The function is compiled in all configs (NOT debug-gated — `KR/Private/MifReconstructCommand.cpp:31-32`), so the promotion is a declaration change only. Under Model B: zero promotions. | **Module**: Model A hard link + pipeline promotion, or Model B | **Guards**: none (both modules editor-only); the pipeline takes its own `FGCScopeGuard` internally (:44)
**Bucket**: self-managed — runs a full `FKismetEditorUtilities::CompileBlueprint` (brief invariant 2: full compile inside an outer transaction ⇒ reinstancing + Ctrl-Z ⇒ dead CDO ⇒ crash). Precedent: `create_editable_child` is self-managed for the same reason.
**Async**: no. Single function decompile+compile is in-frame; the F3 host loops the same call per function synchronously today. (The BP_BaseNPC pathology was per-BP volume + GC, both addressed inside the pipeline: GC lock :37-44, ubergraph skip :50-56.)
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| asset | sourceAsset, path | string | — | YES (strict; cooked BPGC or loose BP) |
| function | functionName | string | — | YES (strict; ExcludeSuper; ubergraph functions REFUSED — see failure modes) |
| targetPath | packagePath | string | `/Game/Reconstructed/Recon_<BP>_<Fn>_<n>` | no (collision → `_<n+1>` uniquify, matching :167-169) |
| compile | — | bool | true | no (false = graph only, caller compiles via existing `compile` endpoint) |
| open | — | bool | false | no (true = OpenEditorForAsset after) |
| save | — | bool | false | no (true = save_package semantics; default leaves the asset dirty in memory) |
**Failure modes**:
- function is the ubergraph → `error: '<fn>' is the ubergraph (FUNC_UbergraphFunction) — per-function reconstruction of event bodies is not supported; use create_editable_child (events are sliced per-event by the bound delegate) ` (pipeline refuses it by design, MifReconstructCommand.cpp:50-56)
- no bytecode → `error: function '<fn>' has no bytecode (Script.Num()==0); nothing to reconstruct`
- `CreateBlueprint` fails → `error: CreateBlueprint failed for '<name>' (is targetPath a valid /Game package path?)`
- decompiler degrades → `ok:true, clean:false` + `graphNodes`, plus compiler messages array when `compile:true` (degraded is a RESULT, not an error — F3 tally contract, CompiledBlueprintReconstructor.h:19-20)
**Cooked**: primary target — this is the "read cooked logic as a real graph" tool. Loose BP source: works (bytecode from its compiled class). The OUTPUT is always a new loose editable asset under /Game.
**Verify**: response `graphNodes` > 2 (entry+result minimum) for `GetNextPatrolPoint` on BP_OponentPatrolRoute_C; `clean:true` expected (function is in the 1228/1256 passing corpus); then `list_nodes {blueprintId:<returned>, graph:"<Fn>_Recon"}` count equals `graphNodes`; `validate` on the returned blueprintId reports 0 errors.
**Score**: U4 E3 R3 → tier 1
**Phase-2 verdict**: CONFIRMED — all four engine signatures verbatim at cited lines (CreateBlueprint KismetEditorUtilities.h:124, CompileBlueprint :169, CreateNewGraph BlueprintEditorUtils.h:329, AddFunctionGraph header-template :390-400); `class FBlueprintEditorUtils` bare at :140 confirmed; pipeline fn ungated + compiled all configs (MifReconstructCommand.cpp:31-33); ubergraph refusal :50-56 and FGCScopeGuard :44 exact; self-managed bucket matches invariant 2. NOTE for merge: functionally overlaps K2's kr_reconstruct_request `mode=function` — one of the two shapes should ship, not both.

### kr_verify_fidelity
**Purpose**: the release-gate metric over HTTP — reconstruct a BP as a throwaway CHILD, recompile, and diff recompiled vs cooked bytecode; returns the full `FBlueprintFidelityReport` as JSON (Compared/Identical/Equivalent/Intentional/Drift/Missing/Uncomparable/Score/AdjustedScore/FirstDrift/IntentTally) so an agent can prove a reconstruction instead of trusting "it compiled".
**Engine API** (existing, verbatim):
```cpp
KISMET_API FOnVerifyBlueprintFidelity& GetBlueprintFidelityVerifier();                    // Editor/Kismet/Public/CompiledBlueprintReconstructor.h:113
DECLARE_DELEGATE_RetVal_FourParams(bool, FOnVerifyBlueprintFidelity,
	UBlueprintGeneratedClass* /*SourceBPGC*/, UBlueprint* /*ReconBP*/,
	const TArray<UFunction*>& /*AttemptedFuncs*/, FBlueprintFidelityReport& /*OutReport*/); // :109-111
static UNREALED_API bool CanCreateBlueprintOfClass(const UClass* Class);                   // Editor/UnrealEd/Public/Kismet2/KismetEditorUtilities.h:178
```
**BLOCKER + exact fix**: the throwaway mint+populate+compile that produces `ReconBP` and `AttemptedFuncs`
is `RunReconstructOnce` + `PopulateUncookedCopy` + `FUncookedCopyStats` — all file-local statics in
`Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp` (:1089, :892, :531). Not linkable. Reimplementing
them in MifBridge is a forbidden parallel pipeline (the file's own "do not fork the pipeline" guarantee,
:1080-1082), and building `AttemptedFuncs` by guesswork skews the fidelity denominator against the
delegate contract (":106-107 AttemptedFuncs = exactly the cooked UFunctions the decompiler delegate was
invoked on"). Required engine-fork promotion (precedent: `CreateEditableBlueprintCopy` was exported for
exactly this kind of MifBridge unification, header comment :26-27): refactor `VerifyFidelityCmd`
(:1356-1470) into
```cpp
// PROPOSED addition to Editor/Kismet/Public/CompiledBlueprintReconstructor.h
KISMET_API bool RunBlueprintFidelityVerify(UBlueprintGeneratedClass* SourceBPGC,
	FBlueprintFidelityReport& OutReport, int32& OutAttempted, int32& OutCompileErrors, FString* OutError);
```
with the console command becoming a thin caller. Until that lands this entry is implementable only in
the degraded persistent-asset form (see Negative results #1 for why that form is NOT proposed).
**Export**: KISMET_API (existing) + the ONE proposed KISMET_API addition above | **Module**: none — Kismet already linked (`MB/MifBridge.Build.cs:26`); no MifKismetReconstructor link needed AT ALL (the verifier arrives via the delegate the plugin binds at startup, `KR/Private/MifKismetReconstructorModule.cpp:42` → `MifFidelityVerifier.cpp:609-612`) | **Guards**: none
**Bucket**: self-managed — full CompileBlueprint of the throwaway child (invariant 2).
**Async**: no for one BP, matching today's synchronous console command and the synchronous `create_editable_child` precedent (both run this identical pipeline in-frame). Document expected latency: seconds on large BPs. (The CORPUS sweep is the async endpoint below.)
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| asset | sourceAsset, path | string | — | YES (strict; cooked or loose — VerifyFidelityCmd deliberately accepts both, :1393-1395) |
| classifyIntentional | — | bool | true | no (set/restore `mif.kr.ClassifyIntentional` via `IConsoleVariable::Set` around the call — game thread, atomic w.r.t. the handler; false gives the audit-baseline split where Intentional stays 0) |
| driftCensus | — | bool | false | no (same set/restore for `mif.kr.DriftCensus`; response echoes the census CSV path when on) |
**Failure modes**:
- verifier delegate unbound (plugin not loaded) → `error: no fidelity verifier bound — is the MifKismetReconstructor plugin loaded? (check self_audit providers)` (mirrors :1408-1412)
- `CanCreateBlueprintOfClass` false → `error: cannot create a Blueprint parented to <Class> (deprecated/abstract-blocked parent)`
- child fails to compile → `ok:false, compiled:false, compileErrors:<n>` and NO fidelity numbers (the command's honesty rule :1426-1431: "a failed compile means the bytecode is absent or stale — report NO fidelity number")
- sibling mode requested → parameter does not exist; document in the endpoint description that sibling fidelity is unmeasurable BY CONSTRUCTION (transient-package component paths ⇒ systematic false drift, :1364-1377) so nobody asks for it
**Cooked**: primary target. Report semantics on cooked: covers real function graphs only — events are excluded from the score by construction (:1437-1438); the response must carry `compared` next to every percentage (FBlueprintFidelityReport comment :73) and emit `"n/a"` when `HasScore()` is false, never the -1 sentinel (:94-97).
**Verify**: on BP_OponentPatrolRoute_C expect `scored > 0` and `score` in a plausible band vs the corpus 54.65% raw fidelity; invariants machine-checkable from the response: `scored == identical+equivalent+intentional+drift+missing`, `adjustedScore >= score`, and with `classifyIntentional:false` re-run: `intentional == 0` and `drift_off == intentional_on + drift_on` (the containment invariant, MifDriftClassifier.cpp:57-62 comment).
**Score**: U4 E2 R3 → tier 2 (blocked on one engine-fork export; everything else is plumbing)
**Phase-2 verdict**: CORRECTED — NAME COLLISION: K2_reconstructor_pipeline.md proposes `kr_verify_fidelity` for the same capability with a CONFLICTING design (K2: request+poll async via the shared job slot; this entry: synchronous) and a differently-shaped proposed engine export (`RunBlueprintFidelityVerify` here vs K2's `RunHeadlessFidelityVerify`). All citations in THIS entry re-verified exact (delegate :109-113, blocker statics :1089/:892/:531 confirmed file-local, honesty rule :1426-1431, AttemptedFuncs contract :106-107, verifier bind MifFidelityVerifier.cpp:609-612). The merge must pick ONE name+design; K2's async form is the safer default given the verified multi-second cost chain (child reconstruct + full compile + double disassembly per function).

### kr_batch_reconstruct_request
**Purpose**: the `mif.kr.ReconstructAll` regression sweep (reconstruct every cooked BP into a throwaway copy, compile, tally pass/fail, optional per-BP fidelity) as a background job — the whole-corpus health number an agent runs after changing the decompiler or before trusting it on a new content drop.
**Engine API**: same blocked statics as kr_verify_fidelity — `ReconstructAll` (:1140-1348) is built on `RunReconstructOnce`/`PopulateUncookedCopy`/`IsCompiledBlueprintAsset`/`ResolveBlueprintClass`, all file-local. Same promotion path: export the proposed `RunBlueprintFidelityVerify` (verify path) plus
```cpp
// PROPOSED addition to Editor/Kismet/Public/CompiledBlueprintReconstructor.h
KISMET_API UBlueprint* RunThrowawayReconstruct(UBlueprintGeneratedClass* SourceBPGC, bool bAsChild,
	int32& OutFunctionsAttempted, int32& OutFunctionsReconstructed, int32& OutNumErrors, FString* OutFirstError);
```
(refactor of `RunReconstructOnce` + the stats/results unpacking at :1236-1290; returns rooted BP, caller `RemoveFromRoot`s). Enumeration (`PKG_Cooked` + the 4-class predicate :102-119) is replicable bridge-side from public API (same FARFilter pattern as kr_list_cooked_blueprints + `UWidgetBlueprint(GeneratedClass)::StaticClass()` — UMG/UMGEditor already linked).
**Export**: blocked on the proposed KISMET_API additions above | **Module**: Kismet (linked); UMG/UMGEditor (linked) | **Guards**: none
**Bucket**: self-managed — hundreds of CompileBlueprint calls; also each processed BP is its own unit so a blanket transaction would be meaningless and lethal (invariant 2).
**Async**: **request + poll, mandatory** (invariant 3: the sweep is minutes-long; today's console command blocks the editor for the whole run — that is exactly what an HTTP handler must never do). Design: handler validates params, snapshots the target list, stores a job singleton `{jobId, targets[], cursor, tallies, csvPath, state}`, and schedules ONE BP per editor tick via `GEditor->GetTimerManager()->SetTimerForNextTick` re-arming itself until done (each per-BP unit is in-frame; the multi-frame whole is what polls). Returns `{jobId, total, csvPath}` immediately. One job at a time: a second request while running → `error: batch job <id> still running (<cursor>/<total>) — poll kr_batch_reconstruct_status or wait`.
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| pathContains | pathFilter | string | "/Game/" | no ("*" = all) |
| mode | variant | string enum `sibling\|child` | "sibling" | no |
| verify | — | bool | false | no (**requires mode:"child"** — sibling verify refused with the :1158-1165 reasoning in the error text) |
| startIndex | — | int | 0 | no (crash-resume semantics, matching the console arg) |
| maxBlueprints | limit | int | 0 (=all) | no (cap for smoke runs) |
**Failure modes**:
- `verify:true, mode:"sibling"` → `error: verify requires mode:'child' — a sibling copies components into the transient package, so drift would measure the mode, not the decompiler`
- zero targets match → `error: no cooked Blueprints match pathContains '<x>' — check kr_list_cooked_blueprints`
- editor shutdown mid-job → job state carries `state:"aborted"`; CSV on disk is flushed per-BP (the crash-preserving contract :1188-1197) so partial results survive
**Cooked**: the target set IS the cooked corpus (PKG_Cooked-gated enumeration). Loose BPs excluded by the same predicate that gates F3.
**Verify**: end-state tallies vs the measured baseline: `pass+fail+skip == total`, expected pass ratio ≈ 1228/1256 on the unchanged decompiler; the per-BP CSV row count equals `total - startIndex`; spot-check one row's RealFuncs against kr_dump_blueprint's own-function-with-bytecode count for the same BP.
**Score**: U3 E2 R3 → tier 2
**Phase-2 verdict**: CONFIRMED — blocked statics re-verified (`RunReconstructOnce` :1089, `PopulateUncookedCopy` :892, `IsCompiledBlueprintAsset` :102, `ResolveBlueprintClass` :121 — all `static` inside `namespace CompiledBlueprintCopyAction` :100); sibling-verify refusal text :1158-1165 exact; per-BP CSV flush :1188-1197 exact; async request+poll design is mandatory per invariant 3 and matches the verified synchronous console loop (:1217). UMG/UMGEditor deps for the widget-class predicate confirmed linked (MB Build.cs:24-25). NOTE for merge: this job model duplicates K2's shared one-slot job model (kr_reconstruct_status polls ALL kr jobs there) — unify on one slot/status design.

### kr_batch_reconstruct_status
**Purpose**: poll the batch job — progress, live tallies, aggregate fidelity so far, current BP (the crash-culprit signal), CSV path.
**Engine API**: none beyond reading the job singleton written by the request endpoint (all bridge-side state).
**Export**: n/a | **Module**: none | **Guards**: none
**Bucket**: read-only — pure state read.
**Async**: this IS the poll half. Payload: `{jobId, state: queued|running|done|aborted, cursor, total, currentBlueprint, pass, fail, skip, ident, equiv, intentional, drift, missing, uncomparable, aggregateFidelity, aggregateAdjusted, csvPath, startedAt, elapsedSec}`.
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| jobId | — | string | last job | no (unknown id → `error: unknown jobId '<x>' — jobs do not survive editor restarts`) |
**Failure modes**: no job ever started → `error: no batch job has run this session — call kr_batch_reconstruct_request first`.
**Cooked**: n/a (state read).
**Verify**: monotonicity across polls (`cursor` non-decreasing, tallies only grow); terminal poll's tallies equal the CSV's row aggregation.
**Score**: U3 E5 R5 → tier 2 (only meaningful with its request twin)
**Phase-2 verdict**: CONFIRMED — pure bridge-side state read, no engine claims to falsify; read-only bucket correct. Same merge note as its request twin (K2's kr_reconstruct_status covers the same poll role for all kr job kinds).

### kr_pin_type_from_property
**Purpose**: expose `FPropertyTypeHelper` (the second exported class — currently used by nothing outside the plugin) as a tiny read-only converter: property path or (class, property) → the exact `FEdGraphPinType` JSON the reconstructor/bridge agree on — lets an agent pre-compute the pin type strings that `add_variable`/`set_pin_type` need from any cooked class's property, instead of guessing category/subcategory spellings.
**Engine API**:
```cpp
static bool ConvertPropertyToPinType(const FProperty* Property, FEdGraphPinType& OutType);                    // KR/Public/Toolkit/PropertyTypeHelper.h:11
static TSharedRef<FJsonObject> SerializeGraphPinType(const FEdGraphPinType& GraphPinType, UClass* SelfScope); // :10
```
Property lookup via `UStruct::FindPropertyByName` (CoreUObject; used the same way at `KR/Private/Verify/MifFidelityVerifier.cpp:87`).
**Export**: `MIFKISMETRECONSTRUCTOR_API` on the class (h:7) | **Module**: Model A hard link, no promotions (or Model B) | **Guards**: none
**Bucket**: read-only.
**Async**: no.
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| class | className, asset | string | — | YES (strict; class path or `_C` objectPath) |
| property | propertyName | string | — | YES (strict) |
| selfScope | — | string | the resolved class | no (controls when the serializer emits `"<SELF>"` — document that token's meaning: object == SelfScope, per MifFidelityVerifier.cpp:66-69) |
**Failure modes**: class not found / property not found → errors naming the parameter and suggesting `list_object_properties` / `describe_class`.
**Cooked**: works — FProperty reflection survives cooking (it is how describe_class works today).
**Verify**: for BP_SegmentedPathTaskMarker_C.`PathActive` expect `{PinCategory:"bool"}`; for `PathSpline` expect object category with SplineComponent subobject; round-trip check: feed the JSON to `DeserializeGraphPinType` in a unit path and compare `FEdGraphPinType::operator==`.
**Score**: U2 E5 R5 → tier 2 (cheap, but partially shadowed by describe_class/list_object_properties — justified only by exact-pin-type parity with the reconstructor)
**Phase-2 verdict**: CONFIRMED — `class MIFKISMETRECONSTRUCTOR_API FPropertyTypeHelper` re-read (h:7), all three static signatures verbatim (:9-11); "<SELF>" token semantics confirmed against MifFidelityVerifier.cpp R1 comment.

---

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

1. **Headless throwaway reconstruct/verify is unlinkable today.** `RunReconstructOnce`
   (`Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp:1089`), `PopulateUncookedCopy` (:892),
   `FUncookedCopyStats` (:531), `ResolveBlueprintClass` (:121), `IsCompiledBlueprintAsset` (:102) are all
   file-local `static`. The only exported mint — `CreateEditableBlueprintCopy`
   (`CompiledBlueprintReconstructor.h:37`) — SAVES a persistent asset (header comment :27-30), so a
   "verify" built on it would litter /Game with children and still could not supply the
   `AttemptedFuncs` array the verifier delegate contract requires (:106-107). kr_verify_fidelity and
   kr_batch_reconstruct are therefore gated on the two proposed KISMET_API refactors named in their
   entries; no bridge-side workaround is honest.
2. **The whole plugin-side console surface is `#if MIF_KR_DEBUG` throwaway.** All five plugin commands
   (FindBP/ListBP/DumpBP/Reconstruct/AnalyzeUbergraph) sit inside the gate whose header says "Ship OFF
   before any release" (`KR/Private/MifReconstructorDebug.h:6-8`). Endpoints must bind the underlying
   building blocks (which are ungated: the disassembler, the slicer, `MifReconstructFunctionIntoGraph`),
   never the `MifKr_*` statics — otherwise flipping the debug gate silently deletes the HTTP surface.
3. **`MifUber` slicer and the reconstruction pipeline are Private/unexported.** Under a hard-link model,
   kr_list_events / kr_analyze_ubergraph / kr_reconstruct_function require the header moves + macro
   additions enumerated in their entries (7 free functions + 2 externs in MifUbergraphSlicer.h; 3
   functions in MifReconstructPipeline.h). Under provider-registration (Model B) none are needed — but
   Model B first requires MifBridge to export a registrar (today `grep MIFBRIDGE_API` → 0 hits) and to
   resolve the load-order inversion (MifBridge=PostEngineInit loads AFTER the reconstructor=Default).
4. **No endpoint for the five `mif.kr.*` CVars** (Events, LatentResume, ClassifyIntentional, DriftCensus,
   DumpFull). Deliberate: they are console variables, fully covered by `run_console` (brief invariant 5
   forbids wrapper endpoints), and the two that affect a measurement (ClassifyIntentional, DriftCensus)
   are better expressed as per-call set/restore params on kr_verify_fidelity. The task hint's
   `kr_events` / `kr_latent_resume` endpoint names do not survive contact with the source — they are
   toggles, not operations.
5. **Raw IR (compiled-statement) endpoint refused by design.** `FKismetBytecodeTransformer` /
   `FKismetGraphDecompiler` are Private+unexported, and the IR they exchange holds RAW un-rooted
   `UFunction*`/`UClass*` pointers whose validity depends on a GC lock held across the pipeline
   (`KR/Private/MifReconstructCommand.cpp:37-44`) and on skeleton identity
   (`KR/Private/MifReconstructEvent.cpp:79-98`). Any endpoint that returned IR handles across HTTP calls
   would dangle by construction. The JSON statement stream (kr_disassemble_function) is the correct
   serialisable layer.
6. **Standalone single-EVENT reconstruction (kr_reconstruct_event) not viable.**
   `MifReconstructEventIntoGraph` requires the HOST to have already spawned the event node into the
   event graph (`KR/Private/MifReconstructPipeline.h:17-29`); node spawning for all event shapes
   (BndEvt__/InpActEvt_/...) lives engine-side in `PopulateUncookedCopy` (unexported, :892). Event
   bodies are reachable today via `create_editable_child` with `mif.kr.Events=1` (default ON since
   2026-07-18). A per-event micro-endpoint would need either the engine spawn branch exported or a
   parallel node-spawn implementation — both worse than the existing whole-asset route.
7. **DumpBP's file-output contract does not port.** The console command writes per-function JSON files +
   `_histogram.json` under `Saved/MifKismetReconstructor/<Class>/` (`KR/Private/MifBlueprintDumper.cpp:246-312`).
   An endpoint that reproduced that would just move the log-scrape to a file-scrape; kr_dump_blueprint
   returns the same data inline with pagination instead. No files.
8. **`mif.kr.ReconstructAll` cannot be exposed synchronously.** It compiles the whole corpus in one
   console invocation (blocks the editor for the entire sweep, `CompiledBlueprintCopyAction.cpp:1217`
   loop). Invariant 3 forces the request+poll split (kr_batch_reconstruct_request/status); a synchronous
   form would deadlock the HTTP pump for minutes.
9. **`FBlueprintEditorUtils` class is unexported** (bare `class FBlueprintEditorUtils`,
   `Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h:140`) — only individually-marked methods are
   callable. Everything kr_reconstruct_function needs is individually `UNREALED_API`
   (CreateNewGraph :329, MarkBlueprintAsStructurallyModified :305) or header-template
   (AddFunctionGraph :390); no gap in practice, recorded so nobody assumes class-wide export.
10. **Live-bridge probe failed this session.** `POST /api/self_audit` → connection refused (curl exit,
    HTTP 000) at sweep time — editor/bridge not running. Verification asset paths were therefore taken
    from the same-day LIVE_PROBES.md session (bridge build "Jul 26 2026", 160 endpoints healthy) rather
    than re-probed. Every claim depending on live state is marked accordingly.
11. **`UBlueprintGeneratedClass` is MinimalAPI** (`Runtime/Engine/Classes/Engine/BlueprintGeneratedClass.h:630`,
    `UCLASS(NeedsDeferredDependencyLoading, MinimalAPI)`) — StaticClass/Cast/LoadObject and field reads
    (`UberGraphFunction` :697) work cross-module, but calling its non-inline member FUNCTIONS from
    MifBridge would not link. None of the proposed endpoints need one; recorded as a boundary.

## UNVERIFIED

- **WidgetBlueprintGeneratedClass through kr_dump_blueprint / kr_disassemble_function** — expected to
  work (UFunction::Script storage is identical; the analyzer's `bRecursiveClasses` sweep covered widget
  BPs per its comment `KR/Private/Analysis/MifUbergraphAnalyzer.cpp:82-83`), but I did not open a widget
  BPGC's functions to confirm; prove against one of the 279 cooked WBPGCs (e.g.
  `/Game/GUI/Inventory/SimpleTooltipWidget.SimpleTooltipWidget_C`) at implementation time.
- **Single-BP verify latency on the largest BPs** — no ms numbers exist for BP_BaseNPC-class assets;
  the sync-is-acceptable call for kr_verify_fidelity rests on today's synchronous console command and
  the synchronous create_editable_child precedent, not on a measurement. If implementation finds
  multi-second stalls unacceptable, the request/poll pattern from kr_batch applies to a single-BP job
  trivially (list of one).
- **`FLatentActionInfo::StaticStruct()->GetPathName()` exact citation** — usage copied from
  `KR/Private/Analysis/MifUbergraphAnalyzer.cpp:188`; I did not open LatentActionManager.h to quote the
  UHT-generated declaration (it is generated code; the call pattern is proven by the shipping analyzer).
- **Behaviour of `IConsoleVariable::Set` + restore inside an HTTP handler** (kr_verify_fidelity's
  classifyIntentional param) — game-thread set/read of `ECVF_Default` CVars around a synchronous call
  should be race-free since handlers run on the game thread, but I did not audit
  `CVarClassifyIntentional.GetValueOnAnyThread()` (`KR/Private/Verify/MifDriftClassifier.cpp:73`) for
  render-thread readers. Low risk (the classifier runs inside the same game-thread call), unproven.
- **Registry `GetAssets` cost at 37k assets inside a handler** — existing find_assets does the same
  class-filtered query (precedent suggests fine); not measured.

## Coverage log

Read in full or in cited part:
- `KR/Private/MifReconstructCommand.cpp` (all 213 lines), `KR/Private/MifBlueprintDumper.cpp` (all 353),
  `KR/Private/MifKismetReconstructorModule.cpp` (all 59), `KR/Private/MifReconstructorDebug.h`,
  `KR/Private/MifReconstructPipeline.h` (all 34), `KR/Private/MifReconstructEvent.cpp` (:1-120 — CVars,
  cache design; rest of the file is the event pipeline internals, characterised via MifReconstructPipeline.h),
  `KR/Private/Analysis/MifUbergraphAnalyzer.cpp` (all 398), `KR/Private/Analysis/MifUbergraphSlicer.h`
  (all 146), `KR/Public/Toolkit/KismetBytecodeDisassemblerJson.h` (all 61) + impl :935-980,
  `KR/Public/Toolkit/PropertyTypeHelper.h` (all 13), `KR/Private/Verify/MifFidelityVerifier.cpp`
  (:1-140, :560-618), `KR/Private/Verify/MifDriftClassifier.cpp` (:1-100),
  `KR/Private/AssetGeneration/KismetBytecodeTransformer.h` (:1-30), `KismetGraphDecompiler.h` (:1-30),
  `MifKismetReconstructor.Build.cs`, `MifKismetReconstructor.uplugin`.
- Engine fork: `Editor/Kismet/Public/CompiledBlueprintReconstructor.h` (all 114),
  `Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp` (:40-129, :1080-1220, :1354-1478 + grep census),
  `Editor/UnrealEd/Public/Kismet2/KismetEditorUtilities.h` (grep: CreateBlueprint :110/:124,
  CompileBlueprint :169, CanCreateBlueprintOfClass :178), `Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h`
  (grep: :140 class decl, :305, :329, :345, :390-400), `Runtime/Engine/Classes/Engine/BlueprintGeneratedClass.h`
  (:630, :697), `Runtime/CoreUObject/Public/UObject/Class.h` (:409),
  `Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h` (:243).
- MifBridge side: `MB/MifBridge.Build.cs` (all), `MifBridge.uplugin`, `MB/Private/MifBridgeReconstruct.cpp`
  (all 79), `MB/Private/MifBridgeCooked.cpp` (:1-80), MIFBRIDGE_API grep (0 hits).
- Docs: audit `_BRIEF.md`, `10_FULL_SCOPE_EXPANSION_PROMPT.md` (Phase 0-3 sections),
  `03_RECONSTRUCTOR_PROMPTS.md` (all), `02_GOTCHAS.md` §3, `audit/work/LIVE_PROBES.md` (all).
- Live probes: attempted self_audit + find_assets — bridge DOWN (recorded, Negative #10).

Not covered / remaining for phase 2: the event-pipeline internals of `MifReconstructEvent.cpp` beyond
:120 (per-event decompiler orchestration — irrelevant until Negative #6's blockers move);
`KismetGraphDecompiler_Reconstruct.cpp` / `KismetIntermediateFormat.h` details (IR layer, refused as an
endpoint by Negative #5); `MifDriftClassifier.h` rule inventory (verdict semantics summarised via the
report struct instead); anim-BP reconstruction (explicitly a MifKismetReconstructor-side future work
item, 03_RECONSTRUCTOR_PROMPTS.md Prompt 1 — no endpoint proposable until it exists).

## Phase-2 verification log (adversarial re-check, 2026-07-26)

- Every Engine API citation re-opened: plugin headers (disassembler/pin-helper/pipeline/slicer),
  MifBlueprintDumper.cpp, MifUbergraphAnalyzer.cpp, MifReconstructCommand.cpp, MifReconstructEvent.cpp,
  MifFidelityVerifier.cpp, MifDriftClassifier.{h,cpp}, CompiledBlueprintReconstructor.h (all 114),
  CompiledBlueprintCopyAction.cpp (all cited ranges), KismetEditorUtilities.h, BlueprintEditorUtils.h,
  IAssetRegistry.h:243, Class.h:409/:3028, BlueprintGeneratedClass.h:630/:697, HttpServerModule.h:25/:60.
  All signatures verbatim; export/promotion lists match CURRENT declarations exactly.
- Hazard grep over the reconstructor plugin (.cpp): zero FMessageDialog/FScopedSlowTask/CollectGarbage
  hits. In the engine TU, FMessageDialog appears ONLY in the interactive Execute path (:971/:980/:1019),
  which no proposed endpoint calls; the headless path (CreateAndSaveEditableCopy :1551-1639) is
  dialog-free; CollectGarbage sits in the batch loop (:1303/:1306) as the entries state.
- Collision check: no `kr_` name in the brief's 160-endpoint list nor in 01_CATALOGUE.md (grep = 0).
  The ONE collision found is intra-audit: `kr_verify_fidelity` proposed by BOTH K1 and K2 (see the
  CORRECTED stamp on that entry).
- Negative results #1-#9, #11 spot-verified against source — all hold (#10 is a live-state claim,
  unverifiable retroactively, plausible).
- Verdict tally: 9 CONFIRMED, 1 CORRECTED (kr_verify_fidelity — cross-axis name/design collision),
  0 DEMOTED.
