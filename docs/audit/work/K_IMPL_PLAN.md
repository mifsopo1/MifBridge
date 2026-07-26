# K — Implementation plan: wiring MifKismetReconstructor capability into MifBridge over HTTP

_Written 2026-07-26. Every line number in this file was read from live source during this session,
after the K1/K2 sweeps and after their Phase-2 re-checks. Where the specs and the source disagree,
**the source wins** and the disagreement is called out explicitly in §0.3._

Path shorthand:
- `MB/` = `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/`
- `KR/` = `D:/DDS2SDK/Game/Plugins/MifKismetReconstructor/Source/MifKismetReconstructor/`
- Engine paths relative to `D:/UE532/Engine/Source`.

---

## 0. Anchor re-verification (the reason this file exists)

### 0.1 The drift K2 warned about happened TWICE

K2's section-B Phase-2 verdict says *"B.2's edits must be re-anchored against live line numbers at
implementation time"* because `MifBridgeCommon.cpp` had gained four endpoints after the sweep. It has
since gained **ten more** (the Batch-D material-graph set: `create_material`,
`create_material_function`, `add_material_expression`, `connect_material_expressions`,
`connect_material_property`, `delete_material_expression`, `list_material_expressions`,
`layout_material_expressions`, `recompile_material`, `shader_compile_status` — live at
`MB/Private/MifBridgeCommon.cpp:231-241`). Every anchor in K2 §B.2 is stale again.

| Symbol | K2 sweep | K2 Phase-2 | **LIVE (read this session)** |
|---|---|---|---|
| `Handlers()` | :29 | :29 | **:29** (body :29-245) |
| `#define MIF_BIND` | :34 | — | **:34** (`#undef` at :242) |
| `GetEndpointNames()` | :230 | :236 | **:247** (body :247-252) |
| `IsReadOnlyEndpoint()` | :239 | :245 | **:256** (body :256-302) |
| `IsSelfManagedEndpoint()` | :277 | :294 | **:309** (body :309-355) |
| `H_self_audit()` | :308 | :337 | **:362** (body :362-407) |
| `IsCompileHeavyEndpoint()` | :355 | — | **:409** (body :409-419) |
| `RunEndpoint()` | :367 | :396 | **:421** (body :421-447) |
| `MIF_DECL` macro (Handlers.h) | :141 | :153 | **:157** |
| Live endpoint count | 160 | 160 | **176** (`grep -c 'MIF_BIND('` = 176, `grep -c 'MIF_DECL('` = 176 — invariant holds) |

`MB/Private/MifBridgeCommon.cpp` is 1645 lines; `MB/Private/MifBridgeHandlers.h` is 431 lines.

**Rule for the implementer: re-run the two greps below immediately before editing. If they do not
return 29 / 247 / 256 / 309 / 362 / 409 / 421, re-anchor again — this file will have drifted too.**

```bash
grep -n "Handlers()\|GetEndpointNames\|IsReadOnlyEndpoint\|IsSelfManagedEndpoint\|IsCompileHeavyEndpoint\|RunEndpoint\|H_self_audit" \
  MB/Private/MifBridgeCommon.cpp
grep -c "MIF_BIND(" MB/Private/MifBridgeCommon.cpp ; grep -c "MIF_DECL(" MB/Private/MifBridgeHandlers.h
```

### 0.2 Coupling model: (b) is ratified and it changes the promotion arithmetic

K2 §B decides **option (b) — provider registration**. Handlers live in
`KR/Private/MifKrBridgeEndpoints.cpp`, i.e. **inside the MifKismetReconstructor module**, next to the
code they call. This is not a detail: it is what makes Wave 2 empty (§A.3).

K1 was written before (b) was ratified and prices every entry under "Model A (hard link)", which
requires moving `MifUbergraphSlicer.h` and `MifReconstructPipeline.h` into `KR/Public/` and stamping
`MIFKISMETRECONSTRUCTOR_API` on 10 free functions + 2 externs. **None of that applies.** Verified
this session:

- `KR/Private/MifReconstructPipeline.h:15,:29,:33` — three free functions, **no `static`**, external
  linkage, ungated (`MifReconstructCommand.cpp:31-32` comment: *"Compiled in all configs"*; the
  definition at `:33` sits **above** the `#if MIF_KR_DEBUG` that opens at `:118`).
- `KR/Private/Analysis/MifUbergraphSlicer.h:40` `namespace MifUber` — the whole header and its .cpp
  are **ungated** (`MifUbergraphSlicer.cpp:1`: *"Moved VERBATIM out of MifUbergraphAnalyzer.cpp's
  `#if MIF_KR_DEBUG`"*), no `static`, external linkage.
- `KR/Private/Verify/MifDriftClassifier.h:26` `namespace MifKr::DriftClassify` — `IsEnabled()` :50,
  `Classify()` :55-56, ungated, external linkage.
- Include convention proves in-module reachability: `MifUbergraphAnalyzer.cpp:27` already does
  `#include "Analysis/MifUbergraphSlicer.h"`, `:28` `#include "Toolkit/KismetBytecodeDisassemblerJson.h"`,
  and `MifFidelityVerifier.cpp:31` does `#include "Verify/MifDriftClassifier.h"`. A new
  `KR/Private/MifKrBridgeEndpoints.cpp` gets all of them with plain `#include`, zero Build.cs change.

### 0.3 Conflicts found between spec and current source — SOURCE WINS

**C-1 (blocking, must be fixed in the header before it is written). K2 §B.1's registry header
includes `Dom/JsonObject.h`, but MifBridge declares `Json` as a PRIVATE dependency.**
`MB/MifBridge.Build.cs:12-17` lists only `Core`, `CoreUObject`, `Engine` as
`PublicDependencyModuleNames`; `"Json"` is at `:39`, inside `PrivateDependencyModuleNames`. A UBT
module's private dependencies do **not** propagate their include paths to dependents, so a *public*
MifBridge header that `#include`s `Dom/JsonObject.h` is not compilable by a consumer that happens not
to depend on Json. Resolution (chosen, minimal): the public header **forward-declares
`class FJsonObject;`** instead of including it — `TSharedRef<FJsonObject>` inside a `TFunction<>`
signature needs only a declaration. Consequence: **`MB/MifBridge.Build.cs` needs NO change** (§B.4),
which also preserves K2's "MifBridge gains no new module dependency" claim. Rejected alternative:
promoting `"Json"` to `PublicDependencyModuleNames` — larger blast radius, changes MifBridge's public
ABI surface for one typedef.

**C-2 (design-affecting). K1's kr_dump_blueprint / kr_list_cooked_blueprints cannot reuse the
dumper's resolution helpers — they are BOTH `static` AND debug-gated.**
`KR/Private/MifBlueprintDumper.cpp:29` opens `#if MIF_KR_DEBUG` and `:352` closes it; the whole file
body is inside. `MifKr_ResolveBPGC` (`:174`), `MifKr_RegistryMatches` (`:150`),
`MifKr_CoerceToBPGC` (`:140`), `MifKr_OpcodeName` (`:32`) are all `static` — internal linkage, not
reachable from a sibling TU even in the same module, and gone entirely when `MIF_KR_DEBUG` flips to 0
(`MifReconstructorDebug.h:9-11`, comment `:6` *"Ship OFF (set to 0) before any release"*). Same story
for `MifKr_AnalyzeUbergraph` (`MifUbergraphAnalyzer.cpp:76`, `static`, inside the `#if` at `:47` /
`#endif` at `:397`) and `MifKr_FindBPGCByName` (`MifReconstructCommand.cpp:120`, `static`, inside the
`#if` at `:118`). This matches K1 Negative-result #2 and is **not** a promotion candidate: the
handlers reimplement resolution from the `create_editable_child` precedent
(`MB/Private/MifBridgeReconstruct.cpp:31-39`) plus the registry filter
(`MifUbergraphAnalyzer.cpp:84-102`), which is data, not logic. Wave-1 handlers must call **only**
ungated, externally-linked symbols.

**C-3 (cosmetic, corrects K2 §B.1's comment).** K2's header draft says *"Same shape as the internal
`FHandlerFn` (MifBridgeHandlers.h:24)"*. `:24` is still correct live:
`using FHandlerFn = TFunction<void(const TSharedRef<FJsonObject>& /*In*/, const TSharedRef<FJsonObject>& /*Out*/)>;`
— keep the citation, it is one of the few that did not move.

**C-4 (records a check, no change).** K2 §B.2 claims `IsCompileHeavyEndpoint` needs zero changes
because it derives from `IsSelfManagedEndpoint`. Live source confirms:
`MifBridgeCommon.cpp:416-418` is `return IsSelfManagedEndpoint(Endpoint) || Endpoint == TEXT("compile") || Endpoint == TEXT("validate");`.
Adding the external-bucket fallback inside `IsSelfManagedEndpoint` therefore propagates for free, and
external `SelfManaged` endpoints are automatically fenced out of `batch`'s open transaction.

**C-5 (records a check, no change).** K2 §B.2 says the route table picks externals up with no server
change. Live: `MB/Private/MifBridgeServer.cpp:88` `const TArray<FString> Endpoints = MifBridge::GetEndpointNames();`
then the bind loop `:89-108`, called from `FMifBridgeServer::Start()` (`:74`), called from
`FMifBridgeModule::StartupModule` at `MB/Private/MifBridge.cpp:58-61`. Confirmed — `MifBridgeServer.cpp`
is UNCHANGED in v1.

**C-6 (note for the job manager).** K2 cites `MifBridgeHandlers.h:305-307` as the
`SetTimerForNextTick` deferral precedent; its own Phase-2 log already corrected this. Live grep over
`MB/Private/`: the only two `SetTimerForNextTick` call sites are `MifBridgeWorld.cpp:144`
(`H_new_level`, body :133-155) and `MifBridgeWorld.cpp:204` (`H_save_level_as`). Use `:144` as the
copy-from pattern — including its `note` field (`:151-154`) that tells the caller the call did not
block.

---

## A. Endpoint set, reconciled

### A.1 The merge arithmetic

K1 proposed 10, K2 proposed 5 = **15 proposals**. Four ratified merge decisions:

| # | Merge | Effect on the name count |
|---|---|---|
| 1 | `kr_verify_fidelity` (K1 sync) → **K2's async request+poll form** | −1 name (K1's dropped) |
| 2 | `kr_reconstruct_function` (K1) → **`kr_reconstruct_request{mode:"function"}`** | −1 name |
| 3 | `kr_batch_reconstruct_status` (K1) → **`kr_reconstruct_status`** (one poll for every job kind) | −1 name |
| 4 | `kr_batch_reconstruct_request` (K1) → **keeps its purpose, adopts the shared one-slot job model** as `kind:"batch"`; renamed `kr_batch_reconstruct` for symmetry with the other request endpoints | −0 names |

Merge 4 keeps the endpoint because K2's own Phase-2 note says the purposes are genuinely distinct
(*"drift census vs pass/fail sweep — no collision, but both must share ONE job slot at merge"*).
15 − 3 = **12 final endpoints**.

Also ratified from the merge: K2's export name `RunHeadlessFidelityVerify` beats K1's
`RunBlueprintFidelityVerify` (K2's shape carries the four optional out-params the job payload needs).

### A.2 Final list

`Bucket` = the `MifBridge::EEndpointBucket` value passed in the registration descriptor (§B.1).
All twelve register with `Provider = "MifKismetReconstructor"`.

| # | Endpoint | Bucket | Sync/Async | Underlying call (verified live) | Promotion? | Wave |
|---|---|---|---|---|---|---|
| 1 | `kr_list_cooked_blueprints` | `ReadOnly` | sync | `IAssetRegistry::GetAssets` + `PKG_Cooked`/package-dedup, mirroring `KR/Private/Analysis/MifUbergraphAnalyzer.cpp:84-102` | none | **1** |
| 2 | `kr_dump_blueprint` | `ReadOnly` | sync | `FKismetBytecodeDisassemblerJson::SerializeFunction` (`KR/Public/Toolkit/KismetBytecodeDisassemblerJson.h:15`, class `MIFKISMETRECONSTRUCTOR_API` at `:9`) over `TFieldIterator<UFunction>(BPGC, ExcludeSuper)` — loop shape from `MifBlueprintDumper.cpp:253-289` | none (already exported) | **1** |
| 3 | `kr_disassemble_function` | `ReadOnly` | sync | same `SerializeFunction` + `UClass::FindFunctionByName(..., EIncludeSuperFlag::ExcludeSuper)` (call shape `MifReconstructCommand.cpp:157`) | none (already exported) | **1** |
| 4 | `kr_list_events` | `ReadOnly` | sync | `MifUber::RecoverEvent` (`MifUbergraphSlicer.h:139`), `ClassifyEvent` (`:111`), `KindName` (`:112`); thunk filter from `MifUbergraphAnalyzer.cpp:197-226` | none under (b) — same module, ungated | **1** |
| 5 | `kr_analyze_ubergraph` | `ReadOnly` | sync | `MifUber::BuildStatements` (`:91-92`), `DetectPrologue` (`:95`), `WalkEvent` (`:104-105`), `RecoverEvent` (`:139`); orchestration from `MifUbergraphAnalyzer.cpp:163-267` | none under (b) | **1** |
| 6 | `kr_pin_type_from_property` | `ReadOnly` | sync | `FPropertyTypeHelper::ConvertPropertyToPinType` / `SerializeGraphPinType` (`KR/Public/Toolkit/PropertyTypeHelper.h:11,:10`, class `MIFKISMETRECONSTRUCTOR_API` at `:7`) | none (already exported) | **1** |
| 7 | `kr_reconstruct_request` | `SelfManaged` | **async** (`kind:"reconstruct"`) | `mode:"copy"` → `CreateEditableBlueprintCopy` (`CompiledBlueprintReconstructor.h:37-38`, `KISMET_API`, already consumed at `MB/Private/MifBridgeReconstruct.cpp:60`); `mode:"function"` → `MifReconstructFunctionIntoGraph` (`KR/Private/MifReconstructPipeline.h:15`) + the mint/graph/compile sequence of `MifReconstructCommand.cpp:165-192` minus `OpenEditorForAsset` (`:198-204`) | none under (b) | **1** |
| 8 | `kr_reconstruct_status` | `ReadOnly` | sync (this **is** the poll half, for all job kinds) | reads the `MifKrJobManager` POD record; counters fed by the delegate lambdas already bound at `MifKismetReconstructorModule.cpp:18-24` and `:30-37`, plus `Graph->Nodes.Num()` deltas | none | **1** |
| 9 | `kr_verify_fidelity` | `SelfManaged` | **async** (`kind:"verify"`) | **NEW** `KISMET_API RunHeadlessFidelityVerify(...)` → executes the verifier delegate bound at `MifKismetReconstructorModule.cpp:42` → `MifKr_BindFidelityVerifier` (`MifFidelityVerifier.cpp:609-612`) → `MifKr::Fidelity::VerifyBlueprint` (`:432-433`) | **engine fork** | **3** |
| 10 | `kr_classify_drift` | `SelfManaged` | **async** (`kind:"classify"`) | same engine export + a NEW per-function verdict capture sink inside `MifFidelityVerifier.cpp` (arm/disarm pair beside `MifKr_BindFidelityVerifier` at `:609`), consuming `MifKr::DriftClassify::FVerdict` (`MifDriftClassifier.h:28-46`) | **engine fork** (plugin side: none) | **3** |
| 11 | `kr_drift_census` | `SelfManaged` | **async** (`kind:"census"`, ONE BP per tick) | same engine export, sliced; `mif.kr.DriftCensus` CVar forced to 1 for the job and restored (`MifDriftClassifier.cpp:66-71`) | **engine fork** (shared with #9) | **3** |
| 12 | `kr_batch_reconstruct` | `SelfManaged` | **async** (`kind:"batch"`, ONE BP per tick) | **NEW** `KISMET_API RunThrowawayReconstruct(...)` (refactor of `RunReconstructOnce`, `CompiledBlueprintCopyAction.cpp:1089`) | **engine fork** | **3** |

### A.3 Wave 1 — ZERO export promotions needed (8 endpoints: #1-#8)

Two independent reasons, both verified this session:

1. **#2, #3, #6 ride already-exported classes.** `class MIFKISMETRECONSTRUCTOR_API FKismetBytecodeDisassemblerJson`
   at `KR/Public/Toolkit/KismetBytecodeDisassemblerJson.h:9`; `class MIFKISMETRECONSTRUCTOR_API FPropertyTypeHelper`
   at `KR/Public/Toolkit/PropertyTypeHelper.h:7`. (They would work even under the rejected model (a).)
2. **#1, #4, #5, #7, #8 are reachable because the handler TU is IN the reconstructor module.**
   Under model (b), `KR/Private/MifKrBridgeEndpoints.cpp` includes the Private headers directly. Every
   symbol it needs was checked for internal linkage and for the `MIF_KR_DEBUG` gate:

| Symbol | Declared at | `static`? | `#if MIF_KR_DEBUG`? | In-module callable |
|---|---|---|---|---|
| `MifReconstructFunctionIntoGraph` | `KR/Private/MifReconstructPipeline.h:15` (def `MifReconstructCommand.cpp:33`) | no | **no** (gate opens at `:118`) | ✅ |
| `MifReconstructEventIntoGraph` | `KR/Private/MifReconstructPipeline.h:29` | no | no | ✅ |
| `MifKr_ResetUbergraphCache` | `KR/Private/MifReconstructPipeline.h:33` | no | no | ✅ |
| `MifUber::BuildStatements` | `KR/Private/Analysis/MifUbergraphSlicer.h:91-92` | no | no | ✅ |
| `MifUber::DetectPrologue` | `:95` | no | no | ✅ |
| `MifUber::WalkEvent` | `:104-105` | no | no | ✅ |
| `MifUber::ClassifyEvent` / `KindName` | `:111` / `:112` | no | no | ✅ |
| `MifUber::RecoverEvent` | `:139` | no | no | ✅ |
| `MifUber::CountRawPointerHits` | `:144` | no | no | ✅ |
| `MifUber::GMaxWalkIterations` / `GMaxJsonDepth` | `:44` / `:45` (`extern const int32`) | n/a | no | ✅ |
| `MifKr::DriftClassify::IsEnabled` / `Classify` | `KR/Private/Verify/MifDriftClassifier.h:50` / `:55-56` | no | no | ✅ |

**Wave 2 = EMPTY. Zero declarations change.** This is the whole payoff of ratifying (b), and it is
worth stating in one sentence for the commit message: *the promotion list K1 enumerated (7 `MifUber`
free functions + 2 externs in `MifUbergraphSlicer.h`, 3 functions in `MifReconstructPipeline.h`)
exists only under Model A, which was rejected.*

The one **new** export in the whole plan is on the **MifBridge** side, and it is an addition rather
than a promotion: `MIFBRIDGE_API` on the two registrar functions in the new public header (§B.1).
`grep -rn "MIFBRIDGE_API" MB/Source/` = **0 hits** live, so this is MifBridge's first exported
symbol; UBT already defines the macro for every module, so nothing else changes.

Two things that look like Wave-2 items but are not:
- The `static` + debug-gated dumper/analyzer helpers (§0.3 C-2) are **reimplemented**, not promoted.
- `MifKr::Fidelity::VerifyBlueprint` (`MifFidelityVerifier.cpp:432`) IS `static` inside
  `namespace MifKr::Fidelity` (`:43`) and therefore genuinely unreachable from a sibling TU — but no
  handler calls it. It is reached through the engine delegate (bound at `:611`
  `GetBlueprintFidelityVerifier().BindStatic(&MifKr::Fidelity::VerifyBlueprint)`), so it stays
  `static`. The kr_classify_drift sink is added *inside that same file*, which is why K2's design put
  it plugin-side.

### A.4 Wave 3 — blocked on the engine-fork `KISMET_API` refactor (4 endpoints: #9-#12)

Blocked because the throwaway mint+populate+compile pipeline is file-static inside
`namespace CompiledBlueprintCopyAction` in a Private engine TU (`CompiledBlueprintCopyAction.cpp:100`):
`RunReconstructOnce` `:1089`, `PopulateUncookedCopy` `:892`, `CopyFunctionStubs` `:551`,
`FUncookedCopyStats` `:531`, `IsCompiledBlueprintAsset` `:102`, `ResolveBlueprintClass` `:121`. The
only exported whole-copy entry, `CreateEditableBlueprintCopy`, **saves an asset** (`:1631-1635`) and
so cannot back a throwaway verify.

Two additions to `D:/UE532/Engine/Source/Editor/Kismet/Public/CompiledBlueprintReconstructor.h`
(the established modkit extension header — every symbol in it already uses `KISMET_API`: `:24`, `:37`,
`:61`, `:113`):

```cpp
// Refactor of VerifyFidelityCmd's body (CompiledBlueprintCopyAction.cpp:1356-1470); the console
// command becomes a thin caller. Mints a TRANSIENT child, roots it, populates + compiles, refuses to
// score a failed compile, executes the bound verifier with Stats.AttemptedFunctions as the
// denominator, then RemoveFromRoot + RF_Transient. Nothing is saved or registered.
KISMET_API bool RunHeadlessFidelityVerify(UBlueprintGeneratedClass* SourceBPGC,
    FBlueprintFidelityReport& OutReport, FString& OutError,
    int32* OutFunctionsAttempted = nullptr, int32* OutFunctionsReconstructed = nullptr,
    int32* OutCompileErrors = nullptr, int32* OutCompileWarnings = nullptr);

// Refactor of RunReconstructOnce + the stats/results unpacking at :1236-1290. Returns a ROOTED BP;
// the caller must RemoveFromRoot.
KISMET_API UBlueprint* RunThrowawayReconstruct(UBlueprintGeneratedClass* SourceBPGC, bool bAsChild,
    int32& OutFunctionsAttempted, int32& OutFunctionsReconstructed,
    int32& OutNumErrors, FString* OutFirstError);
```

Rebuilding the engine `Kismet` module is a prerequisite for Wave 3 and for nothing else. Both plugins
already link `Kismet` (`MB/MifBridge.Build.cs:36`, `KR/MifKismetReconstructor.Build.cs:26`) — no
Build.cs change on either side.

---

## B. Exact edit list, live-anchored

Fourteen files touched (four new C++ files, one new header, eight modified, one — the engine fork —
counted as a single pair). One more (`MB/MifBridge.Build.cs`) was checked and is deliberately
untouched.

| # | File | New/Mod | Wave |
|---|---|---|---|
| 1 | `MB/Public/MifBridgeEndpointRegistry.h` | NEW | 0 |
| 2 | `MB/Private/MifBridgeCommon.cpp` | MOD (6 edits) | 0 |
| 3 | `MB/Private/MifBridgeHandlers.h` | MOD (1 decl — `MarkRouteTableLive`, §B.2) | 0 |
| 4 | `MB/Private/MifBridgeServer.cpp` | MOD (**1 line** — `MarkRouteTableLive()` after `:110`; the route-bind loop `:88-108` is untouched, §B.3) | 0 |
| 5 | `MB/MifBridge.Build.cs` | **UNCHANGED** — recorded, see §B.4 | — |
| 6 | `KR/MifKismetReconstructor.Build.cs` | MOD (1 edit) | 0 |
| 7 | `D:/DDS2SDK/Game/Plugins/MifKismetReconstructor/MifKismetReconstructor.uplugin` | MOD (1 edit) | 0 |
| 8 | `KR/Private/MifKrBridgeEndpoints.cpp` | NEW | 0→1 |
| 9 | `KR/Private/MifKrJobManager.h` | NEW | 1 |
| 10 | `KR/Private/MifKrJobManager.cpp` | NEW | 1 |
| 11 | `KR/Private/MifKismetReconstructorModule.cpp` | MOD (3 edits) | 0 |
| 12 | `KR/Private/Verify/MifFidelityVerifier.cpp` | MOD (capture sink) | 3 |
| 13 | `D:/UE532/.../Editor/Kismet/Public/CompiledBlueprintReconstructor.h` + `Private/CompiledBlueprintCopyAction.cpp` | MOD (one pair) | 3 |
| 14 | `MifBridge/tools/ue5-mcp-bridge/server.py` | MOD (`@mcp.tool()` × 12) | per wave |

**Total files to touch: 14.** Batch 0 alone touches 8 of them (#1, #2, #3, #4, #6, #7, #8, #11).

> If you would rather keep `MifBridgeServer.cpp` and `MifBridgeHandlers.h` byte-identical (rows 3-4),
> drop `GbRouteTableLive` and accept that post-`Start()` registration is silently invisible instead of
> loudly refused. K2 Negative-result #8 argues against that; this plan keeps the loud failure and pays
> two lines for it.

### B.1 NEW: `MB/Public/MifBridgeEndpointRegistry.h` — full final content, ready to paste

Differences from K2 §B.1's draft: forward-declares `FJsonObject` instead of including
`Dom/JsonObject.h` (§0.3 C-1), and all citations re-anchored.

```cpp
// MifBridge — external endpoint registration for provider plugins.
//
// MifBridge's built-in endpoints are MIF_DECL'd in MifBridgeHandlers.h and MIF_BIND'd into the
// function-local static map in MifBridgeCommon.cpp:29-245. Providers (MifKismetReconstructor and any
// future Mif* plugin) instead register named handlers HERE at their own module startup. The endpoint
// exists only while its provider is installed; self_audit names the provider per endpoint.
//
// This is the generalisation of the delegate pattern that already works between the two plugins
// (CompiledBlueprintReconstructor.h's KISMET_API delegate accessors). It keeps MifBridge free of any
// dependency on its providers: MifBridge loads and serves its 176 built-ins whether or not a
// provider is present — the soft-coupling property documented at MifBridgeReconstruct.cpp:73-74.
//
// HARD RULE — the registration API must never touch module-startup state.
// Providers load EARLIER than MifBridge (MifKismetReconstructor.uplugin:17 = "Default";
// MifBridge.uplugin:17 = "PostEngineInit"), so RegisterExternalEndpoint runs BEFORE
// FMifBridgeModule::StartupModule (MifBridge.cpp:29). The registry is a function-local static
// (initialise-on-first-use), and nothing here may read the server, the menus or the token.
//
// Registration must also precede route binding: routes are bound ONCE per name from
// GetEndpointNames() in FMifBridgeServer::Start() (MifBridgeServer.cpp:88-108). Late registration is
// refused loudly rather than silently invisible.
#pragma once

#include "CoreMinimal.h"

class FJsonObject;   // NOT an include: MifBridge lists "Json" as a PRIVATE dependency
                     // (MifBridge.Build.cs:39), so a public header must not pull Dom/JsonObject.h.

namespace MifBridge
{
	// Same shape as the internal FHandlerFn (MifBridgeHandlers.h:24). Game thread only (dispatch hops
	// there at MifBridgeServer.cpp:199). The unrecognised-parameter-is-an-error contract
	// (RejectUnknownParams, MifBridgeHandlers.h:65-67) applies to external handlers identically.
	using FExternalHandler = TFunction<void(const TSharedRef<FJsonObject>& /*In*/, const TSharedRef<FJsonObject>& /*Out*/)>;

	// ONE bucket per endpoint, by construction. The twin-set contradiction class that self_audit
	// polices for built-ins (policyContradictions, MifBridgeCommon.cpp:390-401) cannot exist here.
	//   ReadOnly    — no blanket transaction (else every call pushes an empty undo entry)
	//   SelfManaged — runs a full CompileBlueprint / asset save; opens its OWN tight transactions.
	//                 Also makes IsCompileHeavyEndpoint true (MifBridgeCommon.cpp:416), which keeps
	//                 the endpoint out of batch's single open transaction.
	//   Transacted  — RunEndpoint wraps the call in one FScopedTransaction (MifBridgeCommon.cpp:445)
	enum class EEndpointBucket : uint8 { ReadOnly, SelfManaged, Transacted };

	struct FExternalEndpointDesc
	{
		FString Name;                                    // lowercase snake_case, verb_noun
		EEndpointBucket Bucket = EEndpointBucket::Transacted;
		FString Provider;                                // e.g. "MifKismetReconstructor" — surfaced by self_audit
		FString Summary;                                 // one-liner for self_audit / docs
		FExternalHandler Handler;
	};

	/** Register from your module's StartupModule. Returns false + OutError on: name collision with a
	 *  built-in or another external, empty/invalid name, empty handler, non-game-thread call, or
	 *  registration after the HTTP route table is live. */
	MIFBRIDGE_API bool RegisterExternalEndpoint(FExternalEndpointDesc Desc, FString* OutError = nullptr);

	/** Module shutdown symmetry (the reconstructor already unbinds all three engine delegates at
	 *  MifKismetReconstructorModule.cpp:51-54). Returns the number removed. */
	MIFBRIDGE_API int32 UnregisterExternalEndpoints(const FString& Provider);
}
```

### B.2 MOD: `MB/Private/MifBridgeCommon.cpp` — six edits, live anchors

> All six edits are inside `namespace MifBridge { ... }` which opens at `:25`.

**Edit 1 — include (at `:2`).** Current `:2-3`:
```cpp
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"
```
Insert after `:2`:
```cpp
#include "MifBridgeEndpointRegistry.h"
#include "Dom/JsonObject.h"                 // FJsonObject is only fwd-declared in the registry header
```

**Edit 2 — `ExternalRegistry()` + the two registrar definitions, immediately AFTER `Handlers()`.**
`Handlers()` occupies `:29-245` exactly: `:31` `static TMap<FString, FHandlerFn> Map;`, `:34`
`#define MIF_BIND(Name) Map.Add(TEXT(#Name), &H_##Name)`, `:242` `#undef MIF_BIND`, `:243` `}`,
`:244` `return Map;`, `:245` `}`.
**Insert a new block between the current `:245` and the current `:247` (`TArray<FString> GetEndpointNames()`).**

```cpp
	// --- External (provider-registered) endpoints ----------------------------
	// Function-local static: initialised on first use, so a provider whose module loads at "Default"
	// can populate it before MifBridge's own StartupModule runs at "PostEngineInit".
	static TMap<FString, FExternalEndpointDesc>& ExternalRegistry()
	{
		static TMap<FString, FExternalEndpointDesc> Map;
		return Map;
	}

	// Flipped by FMifBridgeServer::Start() once the route table is built (MifBridgeServer.cpp:88-108).
	static bool GbRouteTableLive = false;
	void MarkRouteTableLive() { GbRouteTableLive = true; }

	bool RegisterExternalEndpoint(FExternalEndpointDesc Desc, FString* OutError)
	{
		auto Reject = [OutError](const FString& Why) { if (OutError) { *OutError = Why; } return false; };

		if (!IsInGameThread())          { return Reject(TEXT("RegisterExternalEndpoint must be called on the game thread (from your module's StartupModule)")); }
		if (Desc.Name.IsEmpty())        { return Reject(TEXT("endpoint name is empty")); }
		if (!Desc.Handler)              { return Reject(FString::Printf(TEXT("endpoint '%s' has no handler"), *Desc.Name)); }
		if (Desc.Provider.IsEmpty())    { return Reject(FString::Printf(TEXT("endpoint '%s' has no Provider (self_audit attributes every external endpoint to a provider)"), *Desc.Name)); }
		if (GbRouteTableLive)           { return Reject(FString::Printf(TEXT("endpoint '%s': route table already live — register from your module's StartupModule (routes bind once at server start)"), *Desc.Name)); }
		if (Handlers().Contains(Desc.Name)) { return Reject(FString::Printf(TEXT("endpoint '%s' collides with a MifBridge built-in"), *Desc.Name)); }
		if (const FExternalEndpointDesc* Existing = ExternalRegistry().Find(Desc.Name))
		{
			return Reject(FString::Printf(TEXT("endpoint '%s' already registered by provider '%s'"), *Desc.Name, *Existing->Provider));
		}

		const FString Name = Desc.Name;
		ExternalRegistry().Add(Name, MoveTemp(Desc));
		return true;
	}

	int32 UnregisterExternalEndpoints(const FString& Provider)
	{
		TArray<FString> Doomed;
		for (const TPair<FString, FExternalEndpointDesc>& KV : ExternalRegistry())
		{
			if (KV.Value.Provider == Provider) { Doomed.Add(KV.Key); }
		}
		for (const FString& Name : Doomed) { ExternalRegistry().Remove(Name); }
		return Doomed.Num();
	}
```

> `MarkRouteTableLive()` needs a declaration. Add it to `MB/Private/MifBridgeHandlers.h` immediately
> after the `IsCompileHeavyEndpoint` declaration at `:33`:
> `/** Called once by FMifBridgeServer::Start() — after this, RegisterExternalEndpoint refuses. */`
> `void MarkRouteTableLive();`
> and call it from `MB/Private/MifBridgeServer.cpp` on the line after `Http.StartAllListeners();`
> (currently `:110`) — see §B.3. §0.3 C-5's "unchanged" claim covers the ROUTE-BINDING LOOP
> (`:88-108`), which really is untouched.

**Edit 3 — `GetEndpointNames()` merges both maps.** Current, live at `:247-252`:
```cpp
	TArray<FString> GetEndpointNames()
	{
		TArray<FString> Names;
		Handlers().GetKeys(Names);
		return Names;
	}
```
Replace `:249-251` with:
```cpp
		TArray<FString> Names;
		Handlers().GetKeys(Names);
		// Externals are first-class from here down: this single merge is what makes the route-bind
		// loop (MifBridgeServer.cpp:88-108) and self_audit's endpoint list pick them up unchanged.
		for (const TPair<FString, FExternalEndpointDesc>& KV : ExternalRegistry())
		{
			Names.AddUnique(KV.Key);
		}
		return Names;
```

**Edit 4 — bucket fallbacks.** `IsReadOnlyEndpoint` body is `:256-302`; its literal set closes at
`:300` (`};`) and `:301` is `return ReadOnly.Contains(Endpoint);`. Replace `:301` with:
```cpp
		if (ReadOnly.Contains(Endpoint)) { return true; }
		// External endpoints declare exactly ONE bucket in their descriptor.
		if (const FExternalEndpointDesc* Ext = ExternalRegistry().Find(Endpoint))
		{
			return Ext->Bucket == EEndpointBucket::ReadOnly;
		}
		return false;
```
`IsSelfManagedEndpoint` body is `:309-355`; its literal set closes at `:353` (`};`) and `:354` is
`return SelfManaged.Contains(Endpoint);`. Replace `:354` with the mirror:
```cpp
		if (SelfManaged.Contains(Endpoint)) { return true; }
		if (const FExternalEndpointDesc* Ext = ExternalRegistry().Find(Endpoint))
		{
			return Ext->Bucket == EEndpointBucket::SelfManaged;
		}
		return false;
```
**`IsCompileHeavyEndpoint` (`:409-419`) needs ZERO changes** — `:416` already reads
`return IsSelfManagedEndpoint(Endpoint)`, so an external `SelfManaged` endpoint is automatically
fenced out of `batch`'s open transaction (§0.3 C-4).

**Edit 5 — `RunEndpoint` miss-path + bucket dispatch.** Body `:421-447`. Current `:425-430`:
```cpp
		const FHandlerFn* Fn = Handlers().Find(Endpoint);
		if (!Fn)
		{
			Fail(Out, FString::Printf(TEXT("unknown endpoint: %s"), *Endpoint));
			return;
		}
```
Replace with:
```cpp
		const FHandlerFn* Fn = Handlers().Find(Endpoint);
		const FExternalEndpointDesc* Ext = Fn ? nullptr : ExternalRegistry().Find(Endpoint);
		if (!Fn && !Ext)
		{
			Fail(Out, FString::Printf(TEXT("unknown endpoint: %s"), *Endpoint));
			return;
		}
```
Then, leaving `:433` (`FEditorScriptExecutionGuard ScriptGuard;`) untouched, replace the dispatch tail
(currently `:437-446`) with:
```cpp
		// Read-only endpoints and self-managed (compile-inside) endpoints run without the
		// blanket transaction — the latter open their own scoped transactions internally.
		// IsReadOnly/IsSelfManaged already consult the external registry, so one test covers both kinds.
		if (IsReadOnlyEndpoint(Endpoint) || IsSelfManagedEndpoint(Endpoint))
		{
			if (Fn) { (*Fn)(In, Out); } else { Ext->Handler(In, Out); }
			return;
		}

		// Every mutation the handler performs is captured in one transaction so the
		// user can Ctrl-Z the whole bridge action.
		FScopedTransaction Transaction(FText::Format(LOCTEXT("BridgeEditFmt", "Mif Bridge: {0}"), FText::FromString(Endpoint)));
		if (Fn) { (*Fn)(In, Out); } else { Ext->Handler(In, Out); }
```

**Edit 6 — `H_self_audit` gains `provider` and `externalProviders`.** Body `:362-407`. Today the
endpoint list at `:381` is a flat array of name strings (`Out->SetArrayField(TEXT("endpoints"), All);`).
The mission wants a `provider` per endpoint. **Do not replace the string array** — README's
MIF_BIND↔@mcp.tool diff and every existing consumer parse it. Add a parallel object array.

Inside the loop `:368-378`, after `:370` (`All.Add(MakeShared<FJsonValueString>(Name));`) add:
```cpp
			{
				const FExternalEndpointDesc* Ext = ExternalRegistry().Find(Name);
				TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
				Row->SetStringField(TEXT("name"), Name);
				Row->SetStringField(TEXT("provider"), Ext ? Ext->Provider : TEXT("MifBridge"));
				Row->SetStringField(TEXT("bucket"),
					IsReadOnlyEndpoint(Name)    ? TEXT("readOnly")
					: IsSelfManagedEndpoint(Name) ? TEXT("selfManaged")
					                              : TEXT("transacted"));
				if (Ext && !Ext->Summary.IsEmpty()) { Row->SetStringField(TEXT("summary"), Ext->Summary); }
				EndpointRows.Add(MakeShared<FJsonValueObject>(Row));
				if (Ext) { ProviderCounts.FindOrAdd(Ext->Provider)++; }
			}
```
Declare `EndpointRows` / `ProviderCounts` alongside the existing arrays at `:367`
(`TArray<TSharedPtr<FJsonValue>> All, ReadOnly, SelfManaged, CompileHeavy, Transacted;`):
```cpp
		TArray<TSharedPtr<FJsonValue>> EndpointRows;
		TMap<FString, int32> ProviderCounts;
```
After `:381` (`Out->SetArrayField(TEXT("endpoints"), All);`) add:
```cpp
		Out->SetArrayField(TEXT("endpointDetails"), EndpointRows);
		Out->SetNumberField(TEXT("externalEndpointCount"), ExternalRegistry().Num());

		TArray<TSharedPtr<FJsonValue>> Providers;
		for (const TPair<FString, int32>& KV : ProviderCounts)
		{
			TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
			P->SetStringField(TEXT("provider"), KV.Key);
			P->SetNumberField(TEXT("endpointCount"), KV.Value);
			Providers.Add(MakeShared<FJsonValueObject>(P));
		}
		Out->SetArrayField(TEXT("externalProviders"), Providers);
```
`policyContradictions` (`:390-400`) and `healthy` (`:401`) keep their exact current semantics —
externals are single-bucket by type and cannot contradict, so the loop over `Names` at `:393` is
correct as written and needs no change.

### B.3 `MB/Private/MifBridgeServer.cpp` — one line (see the note in Edit 2)

After the current `:110` `Http.StartAllListeners();`, add `MifBridge::MarkRouteTableLive();`. The
route-binding loop (`:88-108`) is untouched — externals arrive through `GetEndpointNames()` at `:88`.

### B.4 `MB/MifBridge.Build.cs` — **NO CHANGE**

Stated explicitly because K2's file-touch list omits it and the reader will ask.
- No new module dependency: the registry header needs only `Core` (`TFunction`, `FString`, `TMap`),
  which is already `PublicDependencyModuleNames` at `:14`.
- No `Json` promotion: §0.3 C-1's forward declaration removes the reason. `"Json"` stays private at
  `:39`.
- `PublicIncludePaths` needs nothing: `MB/Public/` is already a public include path by UBT convention
  (`MB/Public/MifBridge.h` is consumed today).
- `PrivateIncludePaths` at `:60` (UMGEditor's Private folder) is unrelated and untouched.

### B.5 `KR/MifKismetReconstructor.Build.cs` — one edit

The file is 29 lines: `PublicDependencyModuleNames` `:12-17`, `PrivateDependencyModuleNames`
`:19-27` (`"Kismet"` is the last entry at `:26`, `});` at `:27`, class close `:28`).
**Insert between the current `:27` and `:28`:**

```cs
			// MifBridge (OPTIONAL). All kr_* HTTP endpoints live in THIS module (coupling model (b),
			// docs/audit/work/K2_reconstructor_pipeline.md §B): the handlers sit next to the Private
			// code they call, so not one reconstructor symbol needs exporting. The dep is guarded so
			// this plugin still builds when MifBridge is not installed — the reconstructor's own
			// value (F3 hook, verifier, console tools) does not depend on the bridge.
			string MifBridgeModuleDir = System.IO.Path.Combine(PluginDirectory, "..", "MifBridge", "Source", "MifBridge");
			if (System.IO.Directory.Exists(MifBridgeModuleDir))
			{
				PrivateDependencyModuleNames.Add("MifBridge");
				PrivateDefinitions.Add("WITH_MIFBRIDGE=1");
			}
			else
			{
				PrivateDefinitions.Add("WITH_MIFBRIDGE=0");
			}
```

> **Known risk (K2 UNVERIFIED, unresolved).** If the MifBridge *folder* is present but the plugin is
> *disabled in the .uproject*, this still adds the dep and UBT fails at link. UE 5.3 `ModuleRules` has
> no clean "is plugin enabled" query. Fallback if it bites: make the dep unconditional (both plugins
> ship together in this modkit) and always define `WITH_MIFBRIDGE=1`. Decide this at first build; it
> is a two-line change either way.
>
> Always defining `WITH_MIFBRIDGE` (1 **or** 0) rather than leaving it undefined lets
> `MifKrBridgeEndpoints.cpp` use `#if WITH_MIFBRIDGE` without an `#ifndef` guard.

### B.6 `MifKismetReconstructor.uplugin` — one edit

File is 23 lines: `"EnabledByDefault": true` `:12`, `"Modules": [` `:13`, module object `:14-21`
(`"LoadingPhase": "Default"` at `:17`), `]` `:22`, `}` `:23`. There is **no `"Plugins"` section**
today (verified). Insert after the current `:22` (`]`), adding the comma:

```json
	],
	"Plugins": [
		{
			"Name": "MifBridge",
			"Enabled": true,
			"Optional": true
		}
	]
}
```

`"Optional": true` is what preserves "the reconstructor works without the bridge". Note the load
order is already correct and **no `LoadingPhase` changes**: `MifKismetReconstructor.uplugin:17` =
`"Default"`, `MifBridge.uplugin:17` = `"PostEngineInit"` — Default runs strictly earlier, so provider
registration always precedes `FMifBridgeServer::Start()`.

### B.7 NEW: `KR/Private/MifKrBridgeEndpoints.cpp`

Whole file guarded by `#if WITH_MIFBRIDGE` / `#endif`. Skeleton (Wave 1; #9-#12 land in Wave 3):

```cpp
// MifKismetReconstructor — all kr_* MifBridge HTTP endpoints.
//
// Coupling model (b): the handlers live HERE, in the provider module, so every Private symbol they
// call (MifReconstructPipeline.h:15, MifUbergraphSlicer.h's MifUber:: free functions,
// MifDriftClassifier.h:55) stays private and unexported. MifBridge gains only the registrar.
//
// NEVER call the MifKr_* console statics: MifBlueprintDumper.cpp:29-352,
// MifUbergraphAnalyzer.cpp:47-397 and MifReconstructCommand.cpp:118-212 are ALL inside
// `#if MIF_KR_DEBUG` (MifReconstructorDebug.h:9-11, "Ship OFF before any release") AND file-static.
// Binding them would make the HTTP surface vanish when the debug gate flips. Call the ungated
// building blocks below instead.
#include "MifReconstructorDebug.h"

#if WITH_MIFBRIDGE

#include "MifBridgeEndpointRegistry.h"          // MifBridge/Public — the ONLY MifBridge header used
#include "MifKrJobManager.h"
#include "MifReconstructPipeline.h"             // :15 MifReconstructFunctionIntoGraph (ungated)
#include "Analysis/MifUbergraphSlicer.h"        // :40 namespace MifUber (ungated)
#include "Toolkit/KismetBytecodeDisassemblerJson.h"   // :9 MIFKISMETRECONSTRUCTOR_API
#include "Toolkit/PropertyTypeHelper.h"               // :7 MIFKISMETRECONSTRUCTOR_API

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "Engine/LatentActionManager.h"         // FLatentActionInfo::StaticStruct() (analyzer :188 pattern)
#include "UObject/Class.h"
#include "UObject/GarbageCollection.h"          // FGCScopeGuard — analyzer :163 / pipeline :44
#include "UObject/ObjectMacros.h"               // PKG_Cooked
#include "UObject/UObjectIterator.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetRegistry/ARFilter.h"
#include "AssetRegistry/AssetData.h"

namespace MifKr::BridgeEndpoints
{
	static const TCHAR* GProvider = TEXT("MifKismetReconstructor");

	// Local mirrors — the dumper's resolver (MifBlueprintDumper.cpp:174) is static AND debug-gated,
	// so it cannot be reused. Resolution follows MifBridgeReconstruct.cpp:31-39 (the create_editable_child
	// precedent) with the _C fallback of MifBlueprintDumper.cpp:177-184.
	static UBlueprintGeneratedClass* ResolveBPGC(const FString& Arg, FString& OutError);
	// Registry enumeration mirrors MifUbergraphAnalyzer.cpp:84-102 (FARFilter + PKG_Cooked + package dedup).
	static void EnumerateCookedBlueprints(const FString& PathContains, bool bCookedOnly, TArray<FAssetData>& Out);

	// --- handlers (Wave 1) ---
	static void H_kr_ping(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);   // mechanism proof, §C.1
	static void H_kr_list_cooked_blueprints(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);
	static void H_kr_dump_blueprint(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);
	static void H_kr_disassemble_function(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);
	static void H_kr_list_events(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);
	static void H_kr_analyze_ubergraph(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);
	static void H_kr_pin_type_from_property(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);
	static void H_kr_reconstruct_request(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);
	static void H_kr_reconstruct_status(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);
}

void MifKr_RegisterBridgeEndpoints()
{
	using namespace MifBridge;
	using namespace MifKr::BridgeEndpoints;

	auto Reg = [](const TCHAR* Name, EEndpointBucket Bucket, const TCHAR* Summary, FExternalHandler H)
	{
		FExternalEndpointDesc D;
		D.Name = Name; D.Bucket = Bucket; D.Provider = GProvider; D.Summary = Summary; D.Handler = MoveTemp(H);
		FString Err;
		if (!RegisterExternalEndpoint(MoveTemp(D), &Err))
		{
			UE_LOG(LogMifKismetReconstructor, Error, TEXT("kr endpoint '%s' NOT registered: %s"), Name, *Err);
		}
	};

	Reg(TEXT("kr_ping"), EEndpointBucket::ReadOnly,
		TEXT("Registry mechanism proof: echoes provider identity and module state."), &H_kr_ping);
	// ... the other Wave-1 endpoints, same shape ...
}

void MifKr_UnregisterBridgeEndpoints()
{
	const int32 N = MifBridge::UnregisterExternalEndpoints(MifKr::BridgeEndpoints::GProvider);
	UE_LOG(LogMifKismetReconstructor, Log, TEXT("unregistered %d kr_* bridge endpoints."), N);
}

#endif // WITH_MIFBRIDGE
```

**Parameter discipline (non-negotiable, house rule):** every handler starts with the equivalent of
`MifBridge::RejectUnknownParams` (`MifBridgeHandlers.h:65-67`; call shape at
`MifBridgeCooked.cpp:92` and `:204`). That helper is **not exported** — it is declared in a Private
header. Two options: (a) copy a 20-line `KrRejectUnknownParams` into this file (the precedent is the
helper's own history: it was born file-local in `MifBridgeCooked.cpp` and promoted later), or (b) add
`MIFBRIDGE_API` re-exports to the registry header. **Choose (a)** — it keeps MifBridge's new public
surface to exactly two functions. Same for `JStr`/`JInt`/`JBool`/`JStrAny`/`JIntAny`
(`MifBridgeHandlers.h:40,:42,:43,:46,:52`): reimplement the six one-liners locally rather than widen
the export.

### B.8 NEW: `KR/Private/MifKrJobManager.{h,cpp}`

The one-slot job state machine shared by #7 and #9-#12. Contract (from K2's "Shared job model"):

- **One slot, no queue.** A second request while `state ∈ {queued, running}` fails with
  `"a kr job is already running (jobId=<id>, kind=<kind>) — poll kr_reconstruct_status or wait"`.
- States `queued → running(<phase>) → done | failed`; phases `resolving, minting, reconstructing,
  compiling, verifying, saving`. The last completed record is retained until the next request.
- Execution: the handler validates + enqueues + returns; work runs via
  `GEditor->GetTimerManager()->SetTimerForNextTick(...)` — copy the pattern at
  `MB/Private/MifBridgeWorld.cpp:144` (and its `note` field at `:151-154`). `kind:"census"` and
  `kind:"batch"` re-arm one tick per Blueprint; GC every 25 BPs
  (`CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS)`, mirroring `CompiledBlueprintCopyAction.cpp:1303`).
- Progress counters are fed by the module's **already-bound** delegate lambdas —
  `MifKismetReconstructorModule.cpp:18-24` (functions) and `:30-37` (events) — incrementing
  `functionsDone/eventsDone` and recording degrade flags **only while a job is active**. Node tally
  uses `TargetGraph->Nodes.Num()` deltas, not decompiler counters (K2 UNVERIFIED: per-event counter
  aggregation was never confirmed). Zero engine change for progress accounting.
- `functionsTotalEstimate` precomputed with `TFieldIterator<UFunction>(SourceBPGC, ExcludeSuper)` +
  `Script.Num() > 0` + `!FUNC_UbergraphFunction` — **named "Estimate" in the payload** because the
  authoritative gate is engine-static (`CopyFunctionStubs`, `CompiledBlueprintCopyAction.cpp:551`)
  and cannot be linked. Done-counts are authoritative; the total is advisory.
- No cancellation in v1.
- **Honesty field, required.** `kr_reconstruct_status` always carries
  `note: "single-BP jobs are atomic; progress counters advance per census/batch slice or on completion"`.
  `FHttpServerModule` is an `FTSTickerObjectBase` (`HttpServerModule.h:25`) with
  `bool Tick(float) override` (`:60`) — during a synchronous reconstruct the socket is not even read,
  so mid-Blueprint progress is physically impossible. Never promise it.
- After an editor restart the record is gone → `found:false`.

### B.9 MOD: `KR/Private/MifKismetReconstructorModule.cpp` — three edits (file is 58 lines)

Live structure: includes `:1-5`; `extern` verifier decls `:10-11`; `StartupModule` `:13-47`
(function delegate `:18-24`, event delegate `:30-37`, `MifKr_BindFidelityVerifier();` `:42`, log
`:44-46`); `ShutdownModule` `:49-56` (`:51` `GetBlueprintFunctionGraphReconstructor().Unbind();`,
`:52` event unbind, `:53` `MifKr_ResetUbergraphCache();`, `:54` `MifKr_UnbindFidelityVerifier();`,
log `:55`); `IMPLEMENT_MODULE` `:58`.

**Edit 1 — declarations, after the current `:11`:**
```cpp
#if WITH_MIFBRIDGE
// Private/MifKrBridgeEndpoints.cpp — the kr_* HTTP endpoints, registered into MifBridge's registry.
extern void MifKr_RegisterBridgeEndpoints();
extern void MifKr_UnregisterBridgeEndpoints();
#endif
```
(Mirrors the existing `extern` pair at `:10-11` — same file-local-declaration idiom, no new header.)

**Edit 2 — register in `StartupModule`, between the current `:42` and `:44`** (i.e. AFTER
`MifKr_BindFidelityVerifier();` and BEFORE the closing `UE_LOG`):
```cpp
#if WITH_MIFBRIDGE
	// Register the kr_* HTTP endpoints. Safe here: this module loads at "Default"
	// (MifKismetReconstructor.uplugin:17), MifBridge at "PostEngineInit" (MifBridge.uplugin:17), so the
	// registry (a function-local static) is populated before MifBridge's StartupModule and long before
	// FMifBridgeServer::Start() binds routes from GetEndpointNames() (MifBridgeServer.cpp:88).
	MifKr_RegisterBridgeEndpoints();
#endif
```
Optionally extend the `:44-46` log line to mention the endpoint count.

**Edit 3 — unregister in `ShutdownModule`, immediately after the current `:54`
(`MifKr_UnbindFidelityVerifier();`) and before the `UE_LOG` at `:55`:**
```cpp
#if WITH_MIFBRIDGE
	MifKr_UnregisterBridgeEndpoints();
#endif
```
This gives the registry the same shutdown symmetry the three engine delegates already have at
`:51-54`.

### B.10 MOD (Wave 3 only): `KR/Private/Verify/MifFidelityVerifier.cpp`

For `kr_classify_drift`. The file is 617 lines; `namespace MifKr::Fidelity` opens at `:43` and closes
at `:607`; `VerifyBlueprint` is `static` at `:432-433`; the bind/unbind pair sits **outside** the
namespace at `:609-612` / `:614-617`.

Add a module-static per-function verdict sink armed only for the duration of a `kind:"classify"` job.
`VerifyBlueprint` appends to it on the drift path (function name, verdict class, `FVerdict::Reasons`,
root ordinals/texts, cooked/recon statement counts, optionally the ±2 lossy window the
`MIF_KR_DEBUG` drift log already formats at `:583`+). Expose the arm/disarm pair with **external
linkage outside the namespace, next to `:609`** — the same idiom as `MifKr_BindFidelityVerifier`, so
`MifKrBridgeEndpoints.cpp` reaches it with an `extern` declaration and no header. Game-thread-only by
the existing threading contract, so a static sink is race-free. The engine delegate's arity is NOT
widened (`CompiledBlueprintReconstructor.h:54-56` refuses arity changes on principle) — that is
exactly why the sink is plugin-side.

### B.11 MOD (Wave 3 only): the engine fork

`D:/UE532/Engine/Source/Editor/Kismet/Public/CompiledBlueprintReconstructor.h` — add the two
`KISMET_API` declarations of §A.4.
`D:/UE532/Engine/Source/Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp` — refactor
`VerifyFidelityCmd` (`:1356-1470`) and `RunReconstructOnce` (`:1089`) into them; the two console
commands (`GMifKrVerifyFidelity` `:1472-1475`, `GMifKrReconstructAll` `:1345-1348`) become thin
callers. Preserve verbatim: the transient-child mint (`:1101-1105`), the GC root across compile
(`:1113`), populate+compile (`:1117-1122`), the refuse-to-score-a-failed-compile rule (`:1426-1431`),
`Stats.AttemptedFunctions` as the fidelity denominator (`:1435`), and the
`RemoveFromRoot()` + `RF_Transient` teardown (`:1468-1469`). Nothing saved, nothing registered.

---

## C. Risks and ordering

### C.1 Build order — prove the mechanism before writing eight handlers

**Batch 0 — the mechanism, with exactly ONE trivial external endpoint.**
Files: §B.1 (registry header), §B.2 (six edits), §B.3 (one line), §B.5, §B.6, §B.7 with **only
`kr_ping`**, §B.9. That is 7 files and roughly 180 lines. `kr_ping` does nothing but echo
`{provider, moduleLoaded, mifKrDebug, endpointsRegistered}`.

Do not write a second handler until Batch 0's live proof passes. Everything downstream — bucket
policy, route binding, `self_audit` attribution, load order, DLL load order, the optional-dependency
Build.cs guard — is exercised by `kr_ping` exactly as it will be by `kr_drift_census`. Nine handlers
written against an unproven registry is nine handlers to re-debug at once.

**The live proof for the mechanism** (this is the gate, not "it compiled"):

```bash
curl -s -X POST http://127.0.0.1:8791/api/self_audit \
     -H "X-Mif-Token: $MIF_BRIDGE_TOKEN" -H "Content-Type: application/json" -d '{}' \
| python -c "import sys,json; d=json.load(sys.stdin); \
  r=[e for e in d['endpointDetails'] if e['name']=='kr_ping']; \
  print('count', d['endpointCount'], 'external', d.get('externalEndpointCount')); \
  print('providers', d.get('externalProviders')); print('row', r); \
  assert d['healthy'] and not d['policyContradictions']; \
  assert r and r[0]['provider']=='MifKismetReconstructor' and r[0]['bucket']=='readOnly'"

curl -s -X POST http://127.0.0.1:8791/api/kr_ping \
     -H "X-Mif-Token: $MIF_BRIDGE_TOKEN" -H "Content-Type: application/json" -d '{}'
```

Pass conditions, all four:
1. `self_audit.endpointCount` is **177** (176 built-ins + `kr_ping`) — the merged map really is what
   `GetEndpointNames()` returns.
2. `endpointDetails` contains `{"name":"kr_ping","provider":"MifKismetReconstructor","bucket":"readOnly"}`
   — **provider attribution works**, the mission's stated requirement.
3. `externalProviders` is `[{"provider":"MifKismetReconstructor","endpointCount":1}]`.
4. `POST /api/kr_ping` returns `ok:true` — **the route was bound**, which proves registration beat
   `FMifBridgeServer::Start()`. A 404 here means the load order failed and everything else is moot.
5. `healthy:true`, `policyContradictions:[]` — the external did not corrupt built-in policy.

Then a negative proof: disable MifKismetReconstructor in the .uproject, restart, confirm
`endpointCount` is back to 176 and MifBridge still serves — the soft-coupling property
(`MifBridgeReconstruct.cpp:73-74`) is preserved.

**Batch 1 — the six read endpoints (#1-#6).** No job manager, no engine change. Each is independently
provable against a real cooked asset (§D). Ship them; they close the audit's #1 stated gap ("cooked
Blueprint graphs are unreadable") on their own.

**Batch 2 — the job manager + #7, #8.** §B.8 + the reconstruct pair. First async work; first time the
delegate lambdas at `MifKismetReconstructorModule.cpp:18-24`/`:30-37` feed a counter. Measure and
record wall-clock for one small BP (`BP_SegmentedPathTaskMarker`) and one large
(`BP_BaseNPC`) — K2 UNVERIFIED #1 says **no timing numbers exist anywhere in source**; this batch is
where they get created.

**Batch 3 — the engine fork + #9.** Rebuild the `Kismet` module. Verify the refactored
`mif.kr.VerifyFidelity` console command still produces byte-identical output before touching the
endpoint — the refactor is the risk, not the endpoint.

**Batch 4 — #10, #11, #12.** The capture sink (§B.10), then census and batch on the proven slicer.

### C.2 How `self_audit`'s output changes

Additive only. Unchanged: `endpointCount`, `endpoints` (flat string array — the README's
MIF_BIND↔@mcp.tool diff still works), `transactionBuckets`, `policyContradictions`, `healthy`,
`buildDate`, `buildTime`, `engineVersion`. New: `endpointDetails[]` (name/provider/bucket/summary),
`externalEndpointCount`, `externalProviders[]`.

`endpointCount` rises from 176 → 177 (Batch 0) → 183 (Batch 1) → 185 (Batch 2) → 189 (Batch 4).
**Update the README's endpoint-count claim and add the 12 `@mcp.tool()` stubs to
`tools/ue5-mcp-bridge/server.py` in the SAME commit as each batch** — `self_audit.endpoints` is the
single source of truth the README diff is checked against, and drift there is exactly what the
`provider` field now makes attributable.

### C.3 Risks, ordered by cost of getting them wrong

| # | Risk | Why it bites | Mitigation |
|---|---|---|---|
| R1 | Registration happens after `Start()` | Routes bind once per name at `MifBridgeServer.cpp:88-108`; the endpoint is invisible with no error anywhere | `GbRouteTableLive` + loud `false` return (§B.2 Edit 2) + `MarkRouteTableLive()` (§B.3). Batch-0 pass condition 4 tests it directly |
| R2 | `WITH_MIFBRIDGE` guard misfires when the folder exists but the plugin is disabled | UBT link failure, whole plugin fails to build | K2 UNVERIFIED, still unresolved. Fallback: unconditional dep (§B.5 note). Test at first build in BOTH configurations |
| R3 | A handler calls a `MIF_KR_DEBUG`-gated static | HTTP surface silently vanishes the day the gate flips to 0 for release (`MifReconstructorDebug.h:6` says it will) | §0.3 C-2 + the file-header warning in §B.7. Grep gate: `grep -n "MifKr_ResolveBPGC\|MifKr_RegistryMatches\|MifKr_FindBPGCByName\|MifKr_AnalyzeUbergraph\|MifKr_DumpBP\|MifKr_OpcodeName" MifKrBridgeEndpoints.cpp` must return **zero** |
| R4 | A kr job runs inside a transaction | Full `CompileBlueprint` + reinstancing captured by an undo step ⇒ dead CDO ⇒ crash | Bucket `SelfManaged` on #7, #9-#12. `IsCompileHeavyEndpoint` (`:416`) then excludes them from `batch` for free. Assert in `self_audit`: every kr_* request endpoint appears in `transactionBuckets.selfManaged` AND `transactionBuckets.compileHeavy` |
| R5 | A status handler transacted | Every poll pushes an empty undo entry (the exact pollution `IsReadOnlyEndpoint` exists to prevent, comment `:254-255`) | Bucket `ReadOnly` on #8 and all six reads |
| R6 | Promising mid-BP progress | `FHttpServerModule` is a game-thread ticker (`HttpServerModule.h:25,:60`); the socket is not read during a stall | Mandatory `note` field (§B.8); only census/batch report real per-BP progress |
| R7 | Anchors drift again mid-implementation | This file's line numbers go stale exactly as K2's did, twice | Re-run §0.1's greps before every editing session. Edit top-down (Edit 1 → 6) so earlier insertions do not invalidate later anchors — or re-grep between edits |
| R8 | Public header pulls a private dependency | Consumer fails to compile MifBridge's public header | §0.3 C-1: forward-declare `FJsonObject` |
| R9 | Fidelity measured on a sibling copy | Systematic FALSE drift from transient-package component paths | `kr_verify_fidelity` has **no** `variant` param at all; passing one is an unrecognised-parameter error with the explanation (engine refusal `CompiledBlueprintCopyAction.cpp:1369-1377`; verifier is child-mode-only, `MifFidelityVerifier.cpp:20-21`) |
| R10 | Reporting a fidelity score for a failed compile | A number over stale bytecode is a lie | `score:null` + explicit error, mirroring `:1426-1431`. Also emit `null` (never `1.0`, never the `-1` sentinel) when `HasScore()` is false |

---

## D. Live-proof scripts

Real cooked Blueprint paths, copied verbatim from K1 §"Verification assets" (sourced from the
2026-07-26 `LIVE_PROBES.md` session, bridge build "Jul 26 2026"):

| Alias | objectPath | Size | Known ground truth |
|---|---|---|---|
| `SMALL` | `/Game/Blueprints/Enviro/Markers/BP_SegmentedPathTaskMarker.BP_SegmentedPathTaskMarker_C` | 4 own fns | `OnRep_PathActive`, `SegmentOverlapp`, `AddPathBox`, `TaskUpdate` |
| `MED` | `/Game/Blueprints/Pawns/NPC/Oponents/Behaviour/BP_OponentPatrolRoute.BP_OponentPatrolRoute_C` | 7 own fns | `OponentDestroyed`, `CheckPathAllowed`, `GetNextPatrolPoint`, `RegisterPatrol`, `GetClosestPoint`, `GetPatrolLocation`, `SetupEnds` |
| `BIG` | `/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C` | 113 own fns (281 incl. inherited) | the documented crash-hardening target; ubergraph present |
| `WIDGET` | `/Game/GUI/Inventory/SimpleTooltipWidget.SimpleTooltipWidget_C` | — | K1 UNVERIFIED #1: WidgetBPGC support is expected but unproven — prove it here |

```bash
BR=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: ${MIF_BRIDGE_TOKEN:-dev}" -H "Content-Type: application/json")
SMALL=/Game/Blueprints/Enviro/Markers/BP_SegmentedPathTaskMarker.BP_SegmentedPathTaskMarker_C
MED=/Game/Blueprints/Pawns/NPC/Oponents/Behaviour/BP_OponentPatrolRoute.BP_OponentPatrolRoute_C
BIG=/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C
WIDGET=/Game/GUI/Inventory/SimpleTooltipWidget.SimpleTooltipWidget_C
```

**#1 `kr_list_cooked_blueprints`** — pass: `BP_BaseNPC` present with `cooked:true`; paged calls do not
overlap and their counts sum; `pathContains:"*"` total near the analyzer's measured 1277 cooked BP
packages (`MifUbergraphSlicer.h:4-5`), allowing for DLC mounts.
```bash
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"pathContains":"/Game/Blueprints/Pawns/NPC/","limit":50}'
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"pathContains":"*","limit":1}'      # read "total"
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"pathContains":"/Game/","offset":0,"limit":100}'
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"pathContains":"/Game/","offset":100,"limit":100}'
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"bogusParam":1}'                    # MUST error, naming the key
```

**#2 `kr_dump_blueprint`** — pass: `SMALL`'s function list ⊇ the four known names plus
`ExecuteUbergraph_BP_SegmentedPathTaskMarker`; every listed function has `scriptBytes > 0` or is
flagged; histogram totals equal the sum of per-function statement counts; `BIG` reports ≈113 own
functions + thunks; the `includeStatements`-without-`functionFilter` cap fires with its `note`.
```bash
curl -s -X POST $BR/kr_dump_blueprint "${H[@]}" -d "{\"asset\":\"$SMALL\"}"
curl -s -X POST $BR/kr_dump_blueprint "${H[@]}" -d "{\"asset\":\"$SMALL\",\"functionFilter\":\"SegmentOverlapp\",\"includeStatements\":true}"
curl -s -X POST $BR/kr_dump_blueprint "${H[@]}" -d "{\"asset\":\"$BIG\"}"
curl -s -X POST $BR/kr_dump_blueprint "${H[@]}" -d "{\"asset\":\"$BIG\",\"includeStatements\":true}"   # expect the limit-10 cap note
curl -s -X POST $BR/kr_dump_blueprint "${H[@]}" -d "{\"asset\":\"$WIDGET\"}"                            # closes K1 UNVERIFIED #1
```

**#3 `kr_disassemble_function`** — pass: unpaginated `totalStatements == N`; the concatenation of
`statementLimit:5` pages equals the unpaginated array; `ExecuteUbergraph_BP_BaseNPC` returns
`truncated:true` with a stable `totalStatements` across calls; an inherited-function request errors
with the "call it on the parent class" message.
```bash
curl -s -X POST $BR/kr_disassemble_function "${H[@]}" -d "{\"asset\":\"$SMALL\",\"function\":\"SegmentOverlapp\"}"
curl -s -X POST $BR/kr_disassemble_function "${H[@]}" -d "{\"asset\":\"$SMALL\",\"function\":\"SegmentOverlapp\",\"statementOffset\":0,\"statementLimit\":5}"
curl -s -X POST $BR/kr_disassemble_function "${H[@]}" -d "{\"asset\":\"$BIG\",\"function\":\"ExecuteUbergraph_BP_BaseNPC\"}"
curl -s -X POST $BR/kr_disassemble_function "${H[@]}" -d "{\"asset\":\"$SMALL\",\"function\":\"NoSuchFunction\"}"   # MUST error with near-misses
```

**#4 `kr_list_events`** — pass: every event `recovered:true, entryOffset >= 0` on all three BPs
(measured corpus rate is 5871/5871, `MifUbergraphSlicer.h:4-5`); `sum(numParams)` matches the thunks'
`CPF_Parm` counts; a BP with no ubergraph returns `ok:true, events:[], note:"no UberGraphFunction…"`
— **an empty list, never an error** (`MifUbergraphAnalyzer.cpp:168-173`).
```bash
curl -s -X POST $BR/kr_list_events "${H[@]}" -d "{\"asset\":\"$SMALL\"}"
curl -s -X POST $BR/kr_list_events "${H[@]}" -d "{\"asset\":\"$MED\"}"
curl -s -X POST $BR/kr_list_events "${H[@]}" -d "{\"asset\":\"$BIG\",\"kind\":\"bndEvt\"}"
```

**#5 `kr_analyze_ubergraph`** — pass: `analysedStmts == reached1 + shared + unreached` and
`unreached == 0` and `eventsRecovered == events` on all three (corpus measured 0 unreached / 100%
recovery); `sharedLatent <= latentStmts`; a walk-cap hit sets `walkCapHit:true` with the
LOWER-BOUND note (`MifUbergraphAnalyzer.cpp:251-256`).
```bash
curl -s -X POST $BR/kr_analyze_ubergraph "${H[@]}" -d "{\"asset\":\"$SMALL\"}"
curl -s -X POST $BR/kr_analyze_ubergraph "${H[@]}" -d "{\"asset\":\"$MED\",\"includeOffsets\":true}"
curl -s -X POST $BR/kr_analyze_ubergraph "${H[@]}" -d "{\"asset\":\"$BIG\"}"
```

**#6 `kr_pin_type_from_property`** — pass: `PathActive` → `{"PinCategory":"bool"}`; `PathSpline` →
object category with the SplineComponent subobject; the `"<SELF>"` token appears only when the object
equals `selfScope` (`MifFidelityVerifier.cpp:66-69`).
```bash
curl -s -X POST $BR/kr_pin_type_from_property "${H[@]}" -d "{\"class\":\"$SMALL\",\"property\":\"PathActive\"}"
curl -s -X POST $BR/kr_pin_type_from_property "${H[@]}" -d "{\"class\":\"$SMALL\",\"property\":\"PathSpline\"}"
```

**#7 + #8 `kr_reconstruct_request` / `kr_reconstruct_status`** — pass: `state` transitions
queued→running→done; a second request while `running` **fails** with the busy message;
`functionsDone == functionsAttempted` at `done`; `elapsedMs > 0`; `graphNodes > 2` (a stub graph has
only entry+result); `compile.errors == 0`; `list_nodes` on the reconstructed graph agrees with
`status.nodesCreated`.
```bash
# function mode (K1's kr_reconstruct_function, merged)
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" \
  -d "{\"sourceAsset\":\"$MED\",\"mode\":\"function\",\"function\":\"GetNextPatrolPoint\"}"
curl -s -X POST $BR/kr_reconstruct_status "${H[@]}" -d '{}'          # poll to done
# busy-slot proof: fire two requests back to back, the second MUST fail
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" -d "{\"sourceAsset\":\"$BIG\",\"mode\":\"copy\"}" &
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" -d "{\"sourceAsset\":\"$SMALL\",\"mode\":\"copy\"}"
# ubergraph refusal
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" \
  -d "{\"sourceAsset\":\"$BIG\",\"mode\":\"function\",\"function\":\"ExecuteUbergraph_BP_BaseNPC\"}"   # MUST refuse
# copy mode + timing (record BOTH numbers — K2 UNVERIFIED #1)
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" -d "{\"sourceAsset\":\"$SMALL\",\"mode\":\"copy\",\"variant\":\"child\"}"
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" -d "{\"sourceAsset\":\"$BIG\",\"mode\":\"copy\",\"variant\":\"child\"}"
```

**#9 `kr_verify_fidelity` (Wave 3)** — pass: `scored == identical+equivalent+intentional+drift+missing`
and `compared == functionsAttempted - missing - uncomparable`; `adjustedScore >= score`; a
`classifyIntentional:false` re-run gives `intentional == 0` and satisfies the containment invariant
`drift_off == intentional_on + drift_on` exactly (`MifDriftClassifier.cpp:57-62`); `score` is `null`
(never `1.000`, never `-1`) when nothing was scorable.
```bash
curl -s -X POST $BR/kr_verify_fidelity "${H[@]}" -d "{\"sourceAsset\":\"$MED\"}"
curl -s -X POST $BR/kr_reconstruct_status "${H[@]}" -d '{}'
curl -s -X POST $BR/kr_verify_fidelity "${H[@]}" -d "{\"sourceAsset\":\"$MED\",\"classifyIntentional\":false}"
curl -s -X POST $BR/kr_verify_fidelity "${H[@]}" -d "{\"sourceAsset\":\"$MED\",\"variant\":\"sibling\"}"   # MUST be an unrecognised-parameter error
```

**#10 `kr_classify_drift` (Wave 3)** — pass: per-function rows SUM to the aggregate returned
alongside (`count(verdict=="identical") == identical`, etc.); on a BP with a known `intentTally` from
a prior verify, the per-function reasons reproduce it exactly; `classifyIntentional:false` yields zero
`intentional` rows with unchanged identical/equivalent counts; a cap-declined function reports
`verdict:"drift", reasons:["classifier-declined:cap"]` with no root
(`MifDriftClassifier.h:20`, caps `.cpp:81-83`).
```bash
curl -s -X POST $BR/kr_classify_drift "${H[@]}" -d "{\"sourceAsset\":\"$MED\",\"includeWindow\":true}"
curl -s -X POST $BR/kr_classify_drift "${H[@]}" -d "{\"sourceAsset\":\"$MED\",\"function\":\"GetNextPatrolPoint\"}"
```

**#11 `kr_drift_census` (Wave 3)** — pass: `bpDone` **advances across polls** (this is the slicing
proof — the only kr job with real mid-job progress); `pass+fail+skip == bpTotal == 5`; the totals
equal the sum of five individual `kr_verify_fidelity` runs on the same BPs with the same flag
(bit-identical); the CSV at `result.censusCsvPath` exists and its row count equals the reported
unclaimed-edit count.
```bash
curl -s -X POST $BR/kr_drift_census "${H[@]}" -d '{"pathFilter":"/Game/Blueprints/Enviro/","maxCount":5}'
for i in 1 2 3 4 5 6; do curl -s -X POST $BR/kr_reconstruct_status "${H[@]}" -d '{}'; sleep 2; done
```

**#12 `kr_batch_reconstruct` (Wave 3)** — pass: `pass+fail+skip == total`; pass ratio ≈ 1228/1256 on
an unchanged decompiler (`MifReconstructEvent.cpp:52-53`); per-BP CSV row count equals
`total - startIndex`; `verify:true` with `mode:"sibling"` is **refused** with the
`CompiledBlueprintCopyAction.cpp:1158-1165` reasoning.
```bash
curl -s -X POST $BR/kr_batch_reconstruct "${H[@]}" -d '{"pathContains":"/Game/Blueprints/Enviro/","maxBlueprints":10}'
curl -s -X POST $BR/kr_reconstruct_status "${H[@]}" -d '{}'
curl -s -X POST $BR/kr_batch_reconstruct "${H[@]}" -d '{"mode":"sibling","verify":true}'   # MUST be refused
```

**Record every response in the implementation session's live-proof log**, including the two wall-clock
timings from Batch 2 — they are the first measured reconstruct/verify numbers that will exist
anywhere (K2 UNVERIFIED #1).
