# K — Wave 3 engine-fork refactor plan (verify-family endpoints)

_Planned 2026-07-26. Engine fork: `D:/UE532`, branch `BrandoCookedEditor-UE5.3.2`. Plan only — no source
was edited and nothing was built while writing this. Every engine line number below was re-opened and
re-verified against the CURRENT file this session; drift from K1/K2 is called out where found (none)._

Path shorthand:
- `ENG/` = `D:/UE532/Engine/Source/`
- `KR/`  = `D:/DDS2SDK/Game/Plugins/MifKismetReconstructor/Source/MifKismetReconstructor/`
- `MB/`  = `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/`

---

## 0. Headline answer

| Question | Answer |
|---|---|
| New exported symbols required | **1** — one `KISMET_API` free function. (One `struct` also moves into the header; a plain struct with only inline members adds **zero** entries to the DLL export table — see §2.4.) |
| Does one export cover all four Wave-3 endpoints? | **Yes** — proven in §2.1. K2's "one export unblocks all three verify endpoints" is correct AND extends to the fourth (`kr_batch_reconstruct`), *provided the export is shaped as the raw mint→populate→compile primitive rather than as a composed "verify" operation*. K2's proposed `RunHeadlessFidelityVerify` shape (verify baked in) would have needed a second export for the batch sweep. K1's `RunThrowawayReconstruct` + `RunBlueprintFidelityVerify` pair (2 exports) is one more than necessary. |
| Engine files to touch | 2 — `ENG/Editor/Kismet/Public/CompiledBlueprintReconstructor.h`, `ENG/Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp` |
| Plugin files to touch | 3 (+1 optional) — see §7 |
| Build cost | **15–40 min wall clock, link-dominated**: 1 TU recompile + ~77 module relinks, **no cascade past direct Kismet dependents**. No UHT run. See §3. |
| ABI-safe against existing prebuilt DLLs? | **Yes — purely additive.** See §3.3. |
| Engine-free alternative? | **One exists but it is degraded, not equivalent.** It writes a real `.uasset` per verify. Usable for a one-off `kr_verify_fidelity`; **unusable for `kr_drift_census`** (1256 asset writes + registry churn). Full analysis in §1 — read it before deciding, because half of it is a genuinely free win that reduces what the export has to carry. |

---

## 1. THE ENGINE-FREE ANALYSIS (read this first)

The task says an engine-free outcome is the best possible one. It was pursued to the end. Result:
**half of the blocker dissolves without touching the engine; the other half does not.**

### 1.1 FREE WIN — `AttemptedFunctions` needs no export at all

K1 Negative #1 and K2's `kr_verify_fidelity` entry both state that the fidelity denominator
(`FUncookedCopyStats::AttemptedFunctions`) is unobtainable outside the engine TU, because the verifier
delegate contract (`CompiledBlueprintReconstructor.h:106-107`) demands *"exactly the cooked UFunctions
the decompiler delegate was invoked on"*.

**That is obtainable plugin-side today, exactly, with zero engine change.** Proof — there are exactly two
`Stats->AttemptedFunctions.Add(Func)` sites in the whole TU, and each is paired 1:1 with an invocation of
the delegate the plugin already binds:

| Site | Delegate call | Add call | Pairing |
|---|---|---|---|
| UserConstructionScript branch | `:639` `UCSReconstructor.Execute(Func, UCSGraph, NewBP)` | `:652` `Stats->AttemptedFunctions.Add(Func)` | exact — the branch `continue`s at `:629` when the delegate is **unbound**, so `Add` is unreachable without a preceding `Execute` |
| real-function branch | `:794` `Reconstructor.IsBound() && Reconstructor.Execute(Func, NewGraph, NewBP)` | `:799` `Stats->AttemptedFunctions.Add(Func)` | exact **when the delegate is bound** (our case, always — `KR/Private/Verify/MifFidelityVerifier.cpp:609-611` binds at module startup). Unbound, `Add` still runs and `Execute` does not — irrelevant, because an unbound reconstructor means there is nothing to verify. |

Events go through a *different* delegate (`:743-745`) and are deliberately absent from
`AttemptedFunctions` (`:539-545`), so there is no contamination.

Consequence: the set and ORDER of `UFunction*` seen by the plugin's own bound
`FOnReconstructBlueprintFunctionGraph` lambda during one `PopulateUncookedCopy` call is
**bit-identical** to `Stats.AttemptedFunctions`. The plugin already exploits exactly this mechanism for
progress counting (`KR/Private/MifKrJobManager.h:117-122`, `NotifyFunctionReconstructed`) — capturing the
`UFunction*` alongside the bool is a one-line widening of a lambda that already exists.

**This removes `AttemptedFunctions` from the list of things the export must exist for**, and it is why the
export below can be the raw primitive instead of a composed verify.

### 1.2 THE IRREDUCIBLE PART — the transient mint

What remains blocked is: *mint a Blueprint into the transient package, run the shared populate pipeline
on it, compile it, and never save it*.

- `PopulateUncookedCopy` (`:892`) is `static` inside `namespace CompiledBlueprintCopyAction` (`:100`).
- Its load-bearing half, `CopyFunctionStubs` (`:551-802`), is 250 lines of engine-private logic:
  the ubergraph/delegate filter (`:572`), the generated-name drop with reasons (`:576-587`), the
  events-OFF `BndEvt__` gate (`:591`), the UserConstructionScript special case (`:600-655`), event-node
  spawning across three shapes — component-bound (`:673-688`), override incl. ghost-node revival
  (`:689-723`), custom event (`:724-729`) — and the `GetOverrideFunctionClass` / `AddFunctionGraph<UClass>`
  override path with its seeded-parent-call strip (`:770-788`). Reimplementing it plugin-side is exactly
  the fork the file's own guarantee forbids (`:1080-1082`: *"ONE mint→populate→compile pipeline… This is
  the 'do not fork the pipeline' guarantee"*) and would silently break the invariant at `:890-891`
  ("a batch PASS means clicking F3 on that BP would compile clean too").
- The **only** exported whole-copy entry, `CreateEditableBlueprintCopy` (`CompiledBlueprintReconstructor.h:37`,
  defined `CompiledBlueprintCopyAction.cpp:1709`), delegates to `CreateAndSaveEditableCopy` (`:1551`),
  which unconditionally does `FAssetRegistryModule::AssetCreated(NewBP)` (`:1627`),
  `Package->MarkPackageDirty()` (`:1628`) and `UPackage::SavePackage(...)` (`:1635`). It is a persistent
  asset factory by construction.

### 1.3 The degraded engine-free fallback (documented, NOT recommended)

If the engine change is refused, `kr_verify_fidelity` (and only it) is still buildable:

1. `CreateEditableBlueprintCopy(SourceBPGC, "/Game/Mif/Verify/_kr_verify_<BP>_<n>", /*bAsChild*/ true, &Err)`
2. capture `AttemptedFunctions` via §1.1
3. `GetBlueprintFidelityVerifier().Execute(SourceBPGC, NewBP, CapturedFuncs, Fid)`
4. delete the asset + `ObjectTools::DeleteAssets` / registry removal

The fidelity NUMBERS would be correct — a persistent child still inherits its components, so the sibling
false-drift problem (`:1364-1377`) does not apply. What is wrong with it:

- **It writes a `.uasset` to `Content/` and fires `AssetCreated` on every single call.** Over a
  `kr_drift_census` of the 1256-BP corpus that is 1256 disk writes, 1256 registry adds, 1256 deletes,
  and 1256 opportunities for a half-deleted asset to survive a crash. This alone disqualifies the census.
- **Compile errors are not capturable.** `CreateAndSaveEditableCopy` calls
  `FKismetEditorUtilities::CompileBlueprint(NewBP)` at `:1606` with **no** `FCompilerResultsLog` and
  **no** `bSilentMode` — unlike `RunReconstructOnce:1120-1122` which sets both. So the honesty rule at
  `:1426-1431` ("a failed compile means the bytecode is absent or stale — report NO fidelity number")
  can only be approximated from `NewBP->Status == BS_Error`, `compileErrors` cannot be reported at all,
  and every compile error spams the Message Log.
- **`kr_batch_reconstruct` remains impossible** in any form: it is *defined* as the throwaway sweep.

There is a third "trick" — call `CreateEditableBlueprintCopy` with a package path whose mount root does
not exist, so `SavePackage` fails and the function returns the Blueprint anyway (`:1635`'s return value
is never checked). **Do not do this.** It relies on an unchecked engine failure path, still fires
`AssetCreated` at `:1627`, and leaves a permanently-dirty in-memory package.

**Verdict: the one-export refactor is the correct call.** §1.1's free win is worth taking regardless —
it is what makes the export *one* function instead of two.

---

## 2. THE MINIMAL EXPORT SURFACE

### 2.1 Why ONE export covers all four Wave-3 endpoints

Every one of the four needs the identical primitive and nothing more:

| Endpoint | Needs | Extra engine surface |
|---|---|---|
| `kr_verify_fidelity` | mint+populate+compile ×1, then the plugin's own bound verifier | none — `GetBlueprintFidelityVerifier()` is already `KISMET_API` (`h:113`) |
| `kr_classify_drift` | identical, + a plugin-side per-function capture sink in `MifFidelityVerifier.cpp` | none (K2 Negative #5: the delegate arity must NOT be widened — `h:54-56`) |
| `kr_drift_census` | the same ×N, + `mif.kr.DriftCensus` forced on via `IConsoleVariable::Set` | none — the CVar is plugin-side (`KR/Private/Verify/MifDriftClassifier.cpp:66-71`) |
| `kr_batch_reconstruct` | the same ×N, verifier optional, tally + CSV | none — CSV is `IFileManager` (public); `CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS)` is `COREUOBJECT_API` |

Enumeration (`IsCompiledBlueprintAsset`, `:102-119`) and resolution (`ResolveBlueprintClass`, `:121-137`)
do **not** need exporting: the plugin already reimplements the exact same `FARFilter` +
`bRecursiveClasses` + `PKG_Cooked` + package-dedup shape in `H_kr_list_cooked_blueprints`
(`KR/Private/MifKrBridgeEndpoints.cpp`, the filter block near its `Filter.ClassPaths.Add` /
`bRecursiveClasses` / `PKG_Cooked` lines, whose own comment cites `MifUbergraphAnalyzer.cpp:85-90` as the
sync contract). Wave 3 reuses that helper — it is not new duplication. `ResolveBlueprintClass` is 16 lines
of `AssetData.GetAsset()` + two `Cast<>`s, all public API.

`FKismetEditorUtilities::CanCreateBlueprintOfClass` is `UNREALED_API`
(`ENG/Editor/UnrealEd/Public/Kismet2/KismetEditorUtilities.h:178`) and the reconstructor already links
`UnrealEd` (`KR/MifKismetReconstructor.Build.cs:25`).

### 2.2 The exact signature

Add to **`ENG/Editor/Kismet/Public/CompiledBlueprintReconstructor.h`**, appended after the existing
`GetBlueprintFidelityVerifier()` declaration at `:113`:

```cpp
// modkit: MifBridge unification — the THROWAWAY half of the pipeline, the mirror of
// CreateEditableBlueprintCopy (:37) which is the PERSISTENT half. Mint a copy of SourceBPGC into
// GetTransientPackage(), run the shared PopulateUncookedCopy pipeline on it, compile it silently, hand
// the live Blueprint to OnCompiled, then unroot it and mark it RF_Transient. NOTHING is saved,
// registered with the AssetRegistry, opened in an editor, or left rooted — the caller cannot leak it
// even by throwing out of the callback (see the definition's comment).
//
// This is CompiledBlueprintCopyAction::VerifyFidelityCmd's :1421-1424 + :1468-1469 lifted verbatim,
// with the caller's verify/tally work moved into OnCompiled. It exists so a headless caller (an HTTP
// handler) gets the IDENTICAL pipeline the console command and F3 run — the "do not fork the pipeline"
// guarantee (Private/CompiledBlueprintCopyAction.cpp:1080-1082) extended across the module boundary.
//
// ParentClass is resolved and validated BY THE CALLER, deliberately: folding ResolveBlueprintClass and
// CanCreateBlueprintOfClass in here would collapse the SKIP_RESOLVE / SKIP_PARENT / SKIP_MINT taxonomy
// the batch harness keeps distinct (:1084-1087). bAsChild=true ⇒ ParentClass IS SourceBPGC.
//
// OnCompiled runs ONLY when the copy minted AND compiled clean (OutResults.NumErrors == 0 and
// Status != BS_Error). A failed compile leaves no trustworthy GeneratedClass bytecode, so scoring it
// would be a lie — the honesty rule at :1426-1431, enforced HERE so no caller can forget it.
// OutStats/OutResults are filled either way, so a caller can still report the failure truthfully.
// Pass a no-op lambda when you only want the tally (the batch sweep).
//
// FIDELITY IS CHILD-ONLY. bAsChild=false is legal (the batch sweep's default) but the resulting copy
// must never be handed to the fidelity verifier: a sibling mints its components into the transient
// package, so every component reference differs by object path and reports systematic FALSE drift
// (the refusals at :1158-1165 and :1369-1377). Callers enforce this; this function does not.
//
// Returns true iff OnCompiled ran.
KISMET_API bool RunTransientBlueprintReconstruct(
	UBlueprintGeneratedClass* SourceBPGC,
	UClass*                   ParentClass,
	bool                      bAsChild,
	FUncookedCopyStats&       OutStats,
	FCompilerResultsLog&      OutResults,
	TFunctionRef<void(UBlueprint* /*ReconBP*/)> OnCompiled);
```

Required header housekeeping, all verified against the current file:

| Change | Why |
|---|---|
| add `#include "Templates/Function.h"` beside `#include "Delegates/Delegate.h"` (`:9`) | `TFunctionRef` lives at `ENG/Runtime/Core/Public/Templates/Function.h:816`; `CoreMinimal.h` does not pull it in |
| add `class UClass;` and `class FCompilerResultsLog;` to the forward-decl block at `:11-15` | `UClass` is used for the first time in this header. **`FCompilerResultsLog` MUST be forward-declared, never included**: it lives in `ENG/Editor/UnrealEd/Public/Kismet2/CompilerResultsLog.h`, and `UnrealEd` is a **PrivateDependencyModuleName** of Kismet (`ENG/Editor/Kismet/Kismet.Build.cs`, `PrivateDependencyModuleNames` list) — private deps do not propagate include paths to dependents, so including it from a Kismet *public* header would break every consumer's build. Same trap, same fix, as `class FJsonObject;` in `MB/Public/MifBridgeEndpointRegistry.h:36-41`. Callers include it themselves; `KR/Private/MifKrBridgeEndpoints.cpp` already does. |

### 2.3 Which existing header — and why not a new one

**`CompiledBlueprintReconstructor.h`, not a new header.** Justification, not preference:

1. It is *the* modkit extension header for this exact purpose — its own line `:26` reads
   `"modkit: MifBridge unification"` on `CreateEditableBlueprintCopy`, the previous export made for
   precisely this reason. The new function is that export's throwaway twin; splitting twins across
   headers is how a source of truth starts drifting.
2. Every consumer already includes it: `KR/Private/MifKismetReconstructorModule.cpp:4`,
   `KR/Private/Verify/MifFidelityVerifier.cpp:32`, `KR/Private/MifKrBridgeEndpoints.cpp:52`,
   `MB/Private/MifBridgeReconstruct.cpp:9`. Zero new `#include` lines anywhere.
3. It is the header that already owns `FBlueprintFidelityReport` (`:71-104`) — the type the new
   function's callers immediately populate. Co-location is correct.
4. A new public header in `ENG/Editor/Kismet/Public/` is a second engine file to create, track and roll
   back, for zero benefit. The rollback story (§6) is strictly better with 2 files than 3.

### 2.4 `FUncookedCopyStats` — move it, verbatim

Current definition, `ENG/Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp:529-548` (**verified
exact this session**, anchor unchanged from K1/K2):

```cpp
	// Tally threaded out of CopyFunctionStubs so the batch harness can report a TRUTHFUL reconstruction count
	// (over the real-function set the decompiler is actually invoked on — not raw graph-node presence).
	struct FUncookedCopyStats
	{
		int32 FunctionsAttempted = 0;      // real BP function graphs the decompiler delegate was called on
		int32 FunctionsReconstructed = 0;  // of those, how many it reported reconstructed cleanly
		// The exact cooked UFunctions the decompiler ran on. The fidelity verifier's denominator MUST be this set,
		// not a re-derived one — re-deriving risks scoring functions nobody ever reconstructed (free "passes").
		TArray<UFunction*> AttemptedFunctions;

		// EVENTS ARE COUNTED SEPARATELY AND MUST STAY THAT WAY. Their bodies come from slicing the ubergraph, so
		// they have no own-class UFunction to diff against: the recon class's ubergraph is REGENERATED from N event
		// graphs (different name, different layout, different entry offsets, and shared regions duplicated BY
		// DESIGN), which makes a whole-ubergraph bytecode comparison meaningless rather than merely lossy. Folding
		// events into FunctionsAttempted would silently corrupt the existing, honest 54.65% fidelity metric — that
		// number's denominator is AttemptedFunctions, and an event has no place in it. Events are therefore
		// deliberately absent from AttemptedFunctions too.
		int32 EventsAttempted = 0;         // event nodes the event-body delegate was called on
		int32 EventsReconstructed = 0;     // of those, how many it reported reconstructed cleanly
	};
```

**Proposed public form: byte-identical.** Cut it out of the `namespace CompiledBlueprintCopyAction`
block and paste it into `CompiledBlueprintReconstructor.h` immediately above the new function
declaration, at global scope. No rename, no field change, no API macro, no comment change.

- **Do not rename it.** The name appears at 12 sites in the TU (`:551`, `:892`, `:1236`, `:1421`, the
  `Stats->` dereferences at `:648-653` / `:746-750` / `:795-800`, and the `Stats.` reads across
  `:1273-1291` and `:1443`). A rename is 12 edit sites of pure churn on the one engine file this work
  must keep trivially revertable. The name is honest — it IS the uncooked-copy stat block.
- **Do not add an API macro.** It has no virtuals and no out-of-line member functions, so it emits
  nothing into the export table — same as its neighbour `FBlueprintFidelityReport` (`:71`), which is
  likewise macro-free and has been crossing this DLL boundary since the fidelity delegate landed. This
  is why the honest count of new **exported symbols is 1, not 2**.
- **Cross-DLL safety**: the only non-trivial member is `TArray<UFunction*>`, header-only, on UE's global
  allocator (`FMemory::Malloc/Free`, exported by Core). Standard UE practice; `FBlueprintFidelityReport`
  already carries three `FString`s across the same boundary.
- **The comments are the point.** `:535-537` (the denominator contract) and `:539-545` (events must
  never enter the fidelity denominator) are the two rules a bridge implementer is most likely to break.
  Moving them into the public header puts them in front of the person who needs them.

In the `.cpp`, `#include "CompiledBlueprintReconstructor.h"` is already present — **twice**, at `:34` and
`:50` (harmless, `#pragma once`; worth collapsing to one while you are in the file, but that is cosmetic
and optional). No new include is needed there either.

### 2.5 The exact code motion in `CompiledBlueprintCopyAction.cpp`

| Symbol | Current | After | Note |
|---|---|---|---|
| `IsCompiledBlueprintAsset` | `static`, `:102` | **stays `static`** | plugin reimplements the filter (§2.1) |
| `ResolveBlueprintClass` | `static`, `:121` | **stays `static`** | 16 lines of public API; caller-side by design (`:1084-1087`) |
| `CopySCSNodeRecursive` … `RemapSelfClassReferences` (`:139`–`:886`) | `static` | **all stay `static`** | none is reachable or wanted from outside |
| `FUncookedCopyStats` | `struct`, `:531`, in-namespace | **moves to the public header**, global scope | §2.4 |
| `CopyFunctionStubs` | `static`, `:551` | **stays `static`** | the irreducible core; exporting it would invite a caller to skip the skeleton refresh at `:934` |
| `PopulateUncookedCopy` | `static`, `:892` | **stays `static`** | reachable only through the new export |
| `Execute` (F3) | `static`, `:958` | **unchanged** | this is the only path with modal dialogs (`:971`, `:980`, `:1019`) and `SDlgPickAssetPath` (`:993`); it must stay untouched and untouchable |
| `RunReconstructOnce` | `static`, `:1089-1124` | **stays `static`** — becomes the private body the new export wraps | see below |
| `ReconstructAll` | `static`, `:1140` | **unchanged** | keeps calling `RunReconstructOnce` directly |
| `VerifyFidelityCmd` | `static`, `:1356-1470` | **`:1421-1424` and `:1468-1469` are replaced by one call to the new export**; everything else stays | see below |
| **NEW** `RunTransientBlueprintReconstruct` | — | global scope, `KISMET_API`, defined **after** the `namespace CompiledBlueprintCopyAction` closing brace at `:1541`, beside `CreateAndSaveEditableCopy` (`:1551`) | that region is already the "global scope + KISMET_API, reaches the file-static helpers by qualified name" zone — its own comment says so at `:1543-1546` |

**The new definition** (place it at global scope after `:1541`, next to its persistent twin):

```cpp
bool RunTransientBlueprintReconstruct(UBlueprintGeneratedClass* SourceBPGC, UClass* ParentClass, bool bAsChild,
	FUncookedCopyStats& OutStats, FCompilerResultsLog& OutResults,
	TFunctionRef<void(UBlueprint*)> OnCompiled)
{
	UBlueprint* NewBP = CompiledBlueprintCopyAction::RunReconstructOnce(SourceBPGC, ParentClass, bAsChild, OutStats, OutResults);
	if (!NewBP)
	{
		return false;   // exactly one meaning: CreateBlueprint failed (see RunReconstructOnce's comment)
	}

	// Honesty rule (VerifyFidelityCmd:1426-1431): a failed compile leaves absent or stale bytecode.
	const bool bCompiled = (OutResults.NumErrors == 0) && (NewBP->Status != BS_Error);
	if (bCompiled)
	{
		OnCompiled(NewBP);
	}

	// Lifetime is owned HERE, never by the caller: RunReconstructOnce roots the copy across the compile
	// (:1113) and its contract hands the RemoveFromRoot to the caller (:1088). Doing it across a module
	// boundary would mean one forgotten line permanently roots a transient Blueprint per verify — a leak
	// a census would multiply by 1256. Verbatim from VerifyFidelityCmd:1468-1469.
	NewBP->RemoveFromRoot();
	NewBP->SetFlags(RF_Transient);
	return bCompiled;
}
```

**The console command becomes a thin caller.** Replace `VerifyFidelityCmd:1421-1424` and `:1468-1469`;
keep `:1358-1362` (usage), `:1364-1378` (sibling refusal), `:1380-1405` (registry name match),
`:1407-1412` (verifier-bound check), `:1414-1419` (parent + `CanCreateBlueprintOfClass`), and the whole
`:1434-1465` reporting block moved inside the lambda:

```cpp
	FUncookedCopyStats Stats;
	FCompilerResultsLog Results;
	const bool bScored = RunTransientBlueprintReconstruct(SourceBPGC, ParentClass, bAsChild, Stats, Results,
		[&](UBlueprint* NewBP)
		{
			FBlueprintFidelityReport Fid;
			Verifier.Execute(SourceBPGC, NewBP, Stats.AttemptedFunctions, Fid);
			/* ... the existing :1437-1465 logging block, unchanged ... */
		});
	if (!bScored)
	{
		UE_LOG(LogCompiledBlueprintCopy, Error, TEXT("[verify] %s FAILED TO COMPILE (%d errors) — fidelity not measured."),
			*SourceBPGC->GetName(), Results.NumErrors);   // :1429-1430 text preserved verbatim
	}
```

Note this silently fixes a latent bug in the current command: at `:1424`, when `RunReconstructOnce`
returns null the command `return`s **without** the `RemoveFromRoot`/`RF_Transient` at `:1468-1469` — a
null return means nothing was minted, so today it happens to be harmless, but the new shape makes the
invariant structural instead of accidental.

### 2.6 What the public function must NOT do (implementer checklist)

| Must not | Enforced by |
|---|---|
| **Save an asset** | never call `UPackage::SavePackage` (the only call site is `:1635`, in `CreateAndSaveEditableCopy` — a different function) |
| **Register with the AssetRegistry** | never call `FAssetRegistryModule::AssetCreated` (`:1071`, `:1627` — both other functions) |
| **Open an editor tab** | never call `OpenEditorForAsset` (`:1076` only, in `Execute`) |
| **Open a modal dialog** | never call `FMessageDialog::Open` (`:971`, `:980`, `:1019` — all in `Execute`) or `SNew(SDlgPickAssetPath)` (`:993`) |
| **Leak the transient package** | `RemoveFromRoot()` + `SetFlags(RF_Transient)` unconditionally after the callback — in the *engine*, not the caller |
| **Collect garbage** | never call `CollectGarbage` (`:1303`, `:1306` — the batch loop owns GC cadence; a GC inside the primitive would run while the caller holds raw `UFunction*`) |
| **Run the fidelity verifier itself** | the verifier is the CALLER's business; baking it in is what would force a second export for `kr_batch_reconstruct` (§2.1) |
| **Resolve or validate the parent class** | destroys the SKIP_RESOLVE / SKIP_PARENT / SKIP_MINT taxonomy (`:1084-1087`) |
| **Refuse siblings** | `bAsChild=false` is the batch sweep's default and must keep working; the *verify* refusal belongs to callers (`:1158-1165`, `:1369-1377`) |
| **Open a transaction** | `RunReconstructOnce` opens none today; a full `CompileBlueprint` inside an outer transaction is the reinstancing/dead-CDO hazard (§4.3) |

### 2.7 Rejected alternative shapes (so nobody re-derives them)

| Shape | Why rejected |
|---|---|
| K2's `RunHeadlessFidelityVerify(SourceBPGC, OutReport, OutError, …)` | bakes the verifier in ⇒ `kr_batch_reconstruct` (verify **off** by default, `:1146`/`:1150`) needs a second export. Also folds resolution+parent validation in, destroying the skip taxonomy (`:1084-1087`). |
| K1's `RunThrowawayReconstruct(...) → UBlueprint*` + `RunBlueprintFidelityVerify(...)` | 2 exports where 1 does; and the raw-pointer return hands a **rooted** object across the DLL boundary — one missed `RemoveFromRoot()` in a 1256-BP census permanently roots 1256 transient Blueprints. |
| Widening `FOnVerifyBlueprintFidelity` to carry per-function verdicts | explicitly refused by the codebase (`CompiledBlueprintReconstructor.h:54-56`: a delegate arity change forces a lockstep engine+plugin update). `kr_classify_drift` uses a plugin-side sink instead (§5.3). |
| Exporting `PopulateUncookedCopy` / `CopyFunctionStubs` directly | a caller could then skip `GenerateBlueprintSkeleton` (`:934`) or the mint's GC rooting (`:1113`) — both silent-corruption bugs. The primitive must be the whole unit. |

---

## 3. BLAST RADIUS

### 3.1 Who includes the header

Verified by grep over the **entire** `ENG/` tree and the **entire** `D:/DDS2SDK/Game/` tree:

| Include site | File |
|---|---|
| Engine (**1 TU**) | `ENG/Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp:34` and `:50` (same TU, duplicate include) |
| Project | `MB/Private/MifBridgeReconstruct.cpp:9` · `KR/Private/MifKismetReconstructorModule.cpp:4` · `KR/Private/Verify/MifFidelityVerifier.cpp:32` · `KR/Private/MifKrBridgeEndpoints.cpp:52` |

That is **the complete list**. It is in no shared PCH (grep over `SharedPCH*.h` → 0 hits) and no other
engine module includes it. Kismet has 79 `.cpp` / 81 `.h` files; **exactly one of them recompiles.**

The header contains **no** `UCLASS`/`USTRUCT`/`UENUM`/`UFUNCTION` and no `.generated.h` — verified by
reading all 113 lines. **UHT does not run.**

### 3.2 What relinks — and where the cascade stops

`Kismet` is depended on by **26 engine modules** (`grep -rl '"Kismet"' ENG --include=*.Build.cs`),
including `UnrealEd` (`ENG/Editor/UnrealEd/UnrealEd.Build.cs:139` and `:265`) and `Engine`
(editor-only block), plus **49 engine-plugin modules**, plus both project plugins
(`MB/MifBridge.Build.cs:26`, `KR/MifKismetReconstructor.Build.cs:26`).

That sounds catastrophic. It is not, and here is the mechanism, **verified on disk in this repo**:

```
Engine/Intermediate/Build/Win64/x64/UnrealEditor/Development/Kismet/
  UnrealEditor-Kismet.lib      985786 bytes   Jul 25 09:12
  UnrealEditor-Kismet.sup.lib  985786 bytes   Jul 25 09:29
```

UBT links the DLL producing `.sup.lib`, then copies it over `.lib` **only if the export table changed**.
The 17-minute timestamp gap with identical sizes proves the optimization is live here: the Jul 25 build
relinked Kismet and did **not** touch the import lib, so no dependent relinked.

Adding one export **does** change the table, so `.lib` gets updated and every module that links
`UnrealEditor-Kismet.lib` relinks — ~77 link steps. **But their own export tables are unchanged**, so
`UnrealEd.lib`, `Engine.lib` etc. are not touched and **the cascade stops one level deep.**

| Step | Count | Cost |
|---|---|---|
| TU recompile (`CompiledBlueprintCopyAction.cpp`, 1752 lines, heavy editor headers) | 1 | ~10–30 s |
| `UnrealEditor-Kismet.dll` link (14.3 MB DLL / 174 MB PDB) | 1 | ~30–60 s |
| direct-dependent relinks (26 engine modules + 49 plugin modules + 2 project plugin modules) | ~77 | dominated by `UnrealEditor-UnrealEd.dll`; parallelised by UBT but IO/RAM-bound |
| second-level relinks | **0** | cascade stops (above) |
| UHT | **0** | no reflection macros in the header |

**Honest estimate: 15–40 minutes wall clock on this machine, link-dominated.** Not a full engine
rebuild (that would be hours), but not a 2-minute iteration either — budget it as a once-per-session
cost and do the whole Wave-3 engine edit in ONE build.

### 3.3 ABI safety against the existing prebuilt DLLs

**Safe — the change is purely additive.**

- `Engine/Binaries/Win64/UnrealEditor-Kismet.dll` (14.3 MB, Jul 25 09:29) plus 459 sibling editor DLLs
  are a from-source build of this fork; the project's own
  `Game/Binaries/Win64/UnrealEditor-DrugDealerSimulator2.dll` is Jul 25 15:42.
- Adding an export **appends** to Kismet's export table. Every existing symbol keeps its name and
  signature. An old dependent DLL that never references `RunTransientBlueprintReconstruct` loads and
  resolves exactly as before.
- Moving `FUncookedCopyStats` from a `.cpp` to a `.h` changes **no layout for anyone**: it was
  file-local with internal linkage, so no other TU in the process has ever had a definition of it to
  disagree with.
- No existing signature, struct layout, vtable, or enum is modified. There is no `UCLASS` reflection
  data to version-mismatch.
- Practical caveat, not an ABI one: after the build, `UnrealEditor-Kismet.dll` and the ~77 relinked DLLs
  are newer than the rest. That is a normal incremental build — but **do not hand-copy a single new
  Kismet.dll onto an otherwise-stale binary set** and expect the plugins to load; the plugin DLLs must
  be rebuilt against the new `.lib` in the same pass.

### 3.4 Does anything in the game/plugins include the header today?

**Yes — four files, all listed in §3.1.** All four already link `Kismet`. `MB/Private/MifBridgeReconstruct.cpp:9`
consumes `CreateEditableBlueprintCopy` today; `KR/Private/MifKrBridgeEndpoints.cpp:52` already includes it
for Wave 1/2. **Zero new `#include` lines and zero new module dependencies are needed on either side** —
`MifKismetReconstructor` already lists `Kismet` (`Build.cs:26`) and `UnrealEd` (`:25`).

---

## 4. RISK + HAZARDS

### 4.1 Modal dialogs — clean, with a caveat

Hazard grep over the whole TU (`FMessageDialog|MakeDialog|SDlgPickAssetPath|FScopedSlowTask|GWarn`):

| Hit | Line | In | Reachable from the new export? |
|---|---|---|---|
| `FMessageDialog::Open` "NotABlueprint" | `:971` | `Execute` (F3) | **no** |
| `FMessageDialog::Open` "BadParent" | `:980` | `Execute` (F3) | **no** |
| `SNew(SDlgPickAssetPath)` | `:993` | `Execute` (F3) | **no** |
| `FMessageDialog::Open` chain error | `:1019` | `Execute` (F3) | **no** |

**Zero `FScopedSlowTask`, zero `GWarn`, zero `FPlatformProcess::Sleep`, zero blocking waits, zero
`FlushAsyncLoading`, zero `FlushRenderingCommands` in the entire TU.** The plugin `.cpp` tree is likewise
clean (K1 Phase-2 hazard grep, re-confirmed).

**Caveat the implementer must own**: `RunReconstructOnce:1122` calls
`FKismetEditorUtilities::CompileBlueprint`, which is silenced only for the *compiler results log*
(`bSilentMode = true`, `:1120`). Deep inside a compile, the engine can still surface a Message Log tab or
a `Notification`. That is true of `create_editable_child` today and has not bitten — but it is the reason
§4.3 defers to a tick rather than running inline: a dialog inside an HTTP handler that is itself blocking
the game-thread ticker would deadlock the bridge, exactly the `new_level` modal-deadlock case already
documented in `MB/Private/MifBridgeHandlers.h`.

### 4.2 GC and transient-package lifetime

- **Mint**: `MakeUniqueObjectName(GetTransientPackage(), BlueprintClassType, "MifKrBatch_<Name>")`
  (`:1101-1102`) → `CreateBlueprint(..., GetTransientPackage(), ...)` (`:1103-1105`).
- **Rooting**: `NewBP->AddToRoot()` then `ClearFlags(RF_Standalone | RF_Public)` (`:1113-1114`). The
  comment at `:1111-1112` states the reason exactly: `CompileBlueprint` can trigger a GC and a raw local
  is not a root.
- **Unrooting**: `RemoveFromRoot()` + `SetFlags(RF_Transient)` (`:1468-1469` in the command,
  `:1301-1302` in the batch loop). **In the new design this moves into the engine function** (§2.5) so
  it cannot be skipped by a plugin caller.
- **No `CollectGarbage` inside the primitive.** The only two calls are `:1303` (`% 25 == 24`) and
  `:1306`, both in `ReconstructAll`. Wave 3's census must own the same cadence **plugin-side**,
  between BP slices, never inside one.
- **Raw-pointer window**: the plugin's decompiler pipeline holds `FGCScopeGuard` per function
  (`KR/Private/MifReconstructCommand.cpp:44`) because its IR carries un-rooted `UFunction*`/`UClass*`.
  GC must remain free to run BETWEEN functions and BETWEEN Blueprints — which is exactly why the async
  model slices at Blueprint granularity and never mid-Blueprint (K2 Negative #7).
- **`AttemptedFunctions` lifetime**: it holds raw `UFunction*` into the **cooked source** class, which is
  rooted by the loaded package — safe for the duration of the callback. **Do not retain it past the
  callback**, and do not stash it in the job record.

### 4.3 Transaction bucket, and whether it can run mid-frame

**Bucket: `SelfManaged`** (`MifBridge::EEndpointBucket::SelfManaged`) for all four endpoints. This is not
a judgement call — the pipeline runs a full `FKismetEditorUtilities::CompileBlueprint` (`:1122`), and a
full compile inside a blanket `FScopedTransaction` means reinstancing captured by an undo step, i.e. a
dead CDO on Ctrl-Z. `create_editable_child` is already registered `SelfManaged` for this exact call.
`SelfManaged` also makes `IsCompileHeavyEndpoint` true, which keeps the endpoint out of `batch`'s single
open transaction (`MB/Public/MifBridgeEndpointRegistry.h:56-59` documents both consequences).

**Can it run inside the mid-frame HTTP handler? No — defer a tick, exactly like `kr_reconstruct_request`.**

- `FHttpServerModule` is an `FTSTickerObjectBase` (`ENG/Runtime/Online/HTTPServer/Public/HttpServerModule.h:25`)
  with `bool Tick(float DeltaTime) override` (`:60`) — the listener is pumped by the **game-thread ticker**.
- Therefore, while a synchronous reconstruct runs, **no HTTP request is even read off the socket**.
  Mid-job polling is not unimplemented, it is physically impossible. `KR/Private/MifKrJobManager.h:3-12`
  already states this as a load-bearing fact of the design; Wave 3 inherits it unchanged.
- Running the compile inline in the handler would hold the HTTP connection open across a multi-second
  editor stall, which the bridge's read timeout turns into a reported failure that did not happen.
- **Shape**: the request handler validates + takes the one job slot (`MifKr::Jobs::TryBegin`) + returns
  `{jobId, state:"queued"}` immediately; the work runs from
  `GEditor->GetTimerManager()->SetTimerForNextTick(...)`, the deferral precedent already used by Wave 2
  and by `new_level`/`load_level`.
- **Progress honesty**: single-BP jobs (`verify`, `classify`) are ATOMIC — the counters jump at
  completion and the payload must say so. Only `census` and `batch`, which re-arm the timer **one
  Blueprint per tick**, have real mid-job progress, because between BPs the ticker pumps HTTP.

### 4.4 Ranked risk register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Engine rebuild breaks an unrelated editor module (a stale `.lib`, a mismatched DLL set) | **high impact, low probability** | §3.3 is additive-only; build **everything** in one pass, never hand-copy DLLs; §6 rollback is a 2-file revert |
| R2 | A dialog/notification surfaces inside `CompileBlueprint` during a deferred slice and blocks the game thread | medium | mid-frame execution is already forbidden (§4.3); if it ever bites, the job's `state` stays `running` and the log names the BP (flushed BEGIN marker) |
| R3 | A caller retains the transient `UBlueprint*` past the callback and dereferences a GC'd object | medium | the engine unroots + `RF_Transient`s it at `:1468-1469`-equivalent; document "the pointer is valid ONLY inside `OnCompiled`" in the header (done, §2.2) |
| R4 | Census over 1256 BPs runs out of memory | medium | mirror `ReconstructAll`'s GC cadence exactly — `CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS)` every 25 BPs (`:1303`) plus once at the end (`:1306`), **plugin-side, between slices** |
| R5 | A BP hard-asserts mid-census and kills the editor | medium | mirror `:1222-1224`: flushed `BEGIN <pkg>` log marker per BP (`GLog->Flush()`), per-row CSV flush (`:1196`), and a `startIndex` resume cursor |
| R6 | Scoping a CVar (`ClassifyIntentional`, `DriftCensus`) leaks if the job fails | low | RAII set/restore around the slice; both are read via `GetValueOnAnyThread()` (`MifDriftClassifier.cpp:73`, `:879`) but only ever from inside the same game-thread call |
| R7 | Anim Blueprints silently mis-reconstruct | **medium — see §5.5** | `IsCompiledBlueprintAsset:115-118` uses **exact** class-path equality and excludes `UAnimBlueprintGeneratedClass`; `GetBlueprintClassTypesForSource:216-225` only special-cases Widget. An anim source mints a plain `UBlueprint` and loses its AnimGraph. Census/batch exclude them; single-BP verify does not — must be flagged in the response |
| R8 | Concurrent plugin edits collide with this plan's plugin-side line anchors | low | this plan cites **engine** line numbers (stable, re-verified) and names plugin integration points by **symbol**, not line — `KR/Private/MifKrBridgeEndpoints.cpp` is being edited by another agent right now |

---

## 5. THE FOUR ENDPOINTS

All four: **provider** = `MifKismetReconstructor`, registered via
`MifBridge::RegisterExternalEndpoint` in `MifKr_RegisterBridgeEndpoints()`
(`KR/Private/MifKrBridgeEndpoints.cpp`, the `Reg(...)` helper); **bucket** = `SelfManaged`;
**shape** = request + poll through the existing one-slot job model
(`KR/Private/MifKrJobManager.h`), polled by the existing `kr_reconstruct_status`. Wave 3 adds no new
status endpoint — it adds `Kind` values `"verify"`, `"classify"`, `"census"`, `"batch"` to the record
whose header already reserves them (`MifKrJobManager.h:44`).

Unrecognised parameter ⇒ error naming it and listing the accepted set (`KrRejectUnknownParams`).
Job slot busy ⇒ `"a kr job is already running (jobId=<id>, kind=<kind>) — poll kr_reconstruct_status or wait"`.

### 5.1 `kr_verify_fidelity`

- **Params**: `sourceAsset` (aliases `blueprint`, `bpName`, `path`) — **required**, strict; resolution
  identical to `kr_reconstruct_request`. `classifyIntentional` (alias `classify`) — bool, default `true`,
  scoped set/restore of `mif.kr.ClassifyIntentional` (`MifDriftClassifier.cpp:57-62`).
  **No `variant` parameter exists, on purpose** — fidelity is CHILD-ONLY; passing it errors with the
  sibling explanation from `:1369-1377`.
- **Bucket / shape**: SelfManaged; async request+poll, `kind:"verify"`, ONE deferred tick, atomic.
- **Engine call**: resolve → `ParentClass = static_cast<UClass*>(SourceBPGC)` →
  `CanCreateBlueprintOfClass` → `RunTransientBlueprintReconstruct(..., bAsChild=true, Stats, Results,
  [&](UBlueprint* BP){ GetBlueprintFidelityVerifier().Execute(SourceBPGC, BP, Stats.AttemptedFunctions, Fid); })`.
- **Structured return** (in `status.result`): every `FBlueprintFidelityReport` field verbatim —
  `{compared, identical, equivalent, intentional, drift, missing, uncomparable, firstDrift, intentTally,
  firstIntentional, scored, score, adjustedScore}` plus
  `{functionsAttempted, functionsReconstructed, eventsAttempted, eventsReconstructed,
  compile:{errors, warnings, firstError}}`.
  **`score`/`adjustedScore` are `null` when `HasScore()` is false — never `1.0`, never the `-1` sentinel**
  (`h:94-97` is explicit that callers must branch). `compared` is emitted next to every percentage
  (`h:73`). `events*` stay in separate fields from `functions*` (`:539-545`).
- **Failure modes**:
  - source unresolvable → `"source blueprint class not found: '<x>' (try the .<Name>_C class path; find candidates with kr_list_cooked_blueprints)"`
  - `CanCreateBlueprintOfClass` false → `"cannot create a Blueprint parented to <Class>"` (`:1417` text)
  - mint failed (export returned false with `Results.NumErrors == 0`) → `state:"failed"`, `"could not mint a copy of <BP>"` (`:1424`)
  - **compile failed** → `state:"done"`, `result.score = null`,
    `error:"<BP> failed to compile (<n> errors) — fidelity not measured"` (`:1426-1431` verbatim rule).
    The export refuses to invoke the verifier, so no number can leak.
  - verifier delegate unbound → cannot occur under coupling model (b) (the endpoint only exists when the
    plugin is loaded); keep the engine's belt-and-braces check at `:1407-1412`.
  - zero comparable functions (all-event BP — **735 of 1250** per `:1314`) → `scored:0, score:null` +
    a note. Never `1.000`.
- **Cooked / loose**: cooked is the target; loose/authored BPs are deliberately accepted (`:1393-1395` —
  the authored-testbed ground-truth loop). No `PKG_Cooked` gate on this endpoint.
- **Live proof** (see §5.5 for asset selection):
  1. `/Game/Blueprints/Pawns/NPC/Oponents/Behaviour/BP_OponentPatrolRoute.BP_OponentPatrolRoute_C`
     — 7 own functions (K1-verified). Expect `scored > 0`; `score` in a plausible band around the
     corpus 54.65%. Machine-checkable invariants from the response alone:
     `scored == identical + equivalent + intentional + drift + missing` (`h:91`) and
     `adjustedScore >= score` (`h:102-103`).
  2. Re-run with `classifyIntentional:false` → `intentional == 0` and
     `drift_off == intentional_on + drift_on` (the classifier containment invariant).
  3. `/Game/Audio/Music/ChaseAndFight/RaidAreaSphere.RaidAreaSphere_C` — a small trigger actor; the
     expected outcome is `scored:0, score:null` (event-only). **That is the proof that matters for the
     "never 1.000" rule** — record it as such, not as a failure.
  4. Stress: `/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C` (113 own functions). **Record the
     wall-clock ms** — no measured timing for a big-BP verify exists anywhere in source (K2 UNVERIFIED),
     and this plan's async design rests on that gap.

### 5.2 `kr_drift_census`

- **Params**: `pathFilter` (aliases `filter`, `pathSubstr`) — string, default `"/Game/"`, `"*"` = all.
  `startIndex` (alias `start`) — int ≥ 0, default 0 (crash-resume cursor, matching `ReconstructAll`'s
  `:1144`). `maxCount` (alias `limit`) — int, default **50**, `0` = unbounded (an accidental whole-corpus
  run must be opt-in). `classifyIntentional` — bool, default true.
- **Bucket / shape**: SelfManaged; async, `kind:"census"`, **sliced ONE BLUEPRINT PER TICK** — the only
  Wave-3 endpoint with real mid-job progress (§4.3). `CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS)` every
  25 BPs, mirroring `:1303`, plus once at the end (`:1306`).
- **Enumeration**: reuse the plugin's existing `kr_list_cooked_blueprints` filter helper
  (`FARFilter` over `UBlueprintGeneratedClass` + `UBlueprint`, `bRecursiveClasses=true`, then
  `PKG_Cooked` + `SeenPackages` dedup, then `PackageName.Contains(pathFilter)`, then sort by package
  name) — the mirror of `:1167-1186`. Cite `IsCompiledBlueprintAsset:102-119` in a comment as the sync
  contract, exactly as Wave 1 already does for its own filter.
- **Census instrument**: force `mif.kr.DriftCensus` to 1 for the job's duration via in-process
  `IConsoleVariable::Set`, restore after (`MifDriftClassifier.cpp:66-71`). The CSV is written by the
  classifier itself to `<ProjectSaved>/MifKr/DriftCensus_<ts>.csv`, one flushed row per unclaimed edit
  (`MifDriftClassifier.cpp:352-370`).
- **Structured return — must be COMPARABLE to the corpus baseline.** The completion payload must carry
  the same aggregate the engine batch prints at `:1328-1331`, or the number is not comparable to the
  published 54.65%:
  `{bpTotal, bpDone, pass, fail, skip,
    skipTaxonomy:{resolve, parent, mint},
    totals:{identical, equivalent, intentional, drift, missing, uncomparable, compared},
    corpusFidelity, corpusAdjusted, intentTally, censusCsvPath}`.
  - `corpusFidelity = (identical + equivalent) / compared`; `corpusAdjusted = (identical + equivalent +
    intentional) / compared`; both `null` when `compared == 0`. **Print raw and adjusted together,
    always** (`:1311-1314` / `h:99-101`).
  - `skipTaxonomy` is load-bearing: the engine keeps SKIP_RESOLVE / SKIP_PARENT / SKIP_MINT distinct on
    purpose (`:1084-1087`, rows at `:1227`/`:1233`/`:1239`). Collapsing them destroys the diagnosis.
  - `intentTally` merged across BPs the same way `:1262-1269` merges it (`"flowstack=812;outparam=61"`).
  - **Corpus reference numbers to compare against**: 1256 BPs / PASS 1228 (`KR/Private/MifReconstructEvent.cpp:52`);
    raw fidelity 54.65% (`CompiledBlueprintCopyAction.cpp:543`); events 85.9% reconstructed, FAIL 10/481
    (`MifReconstructEvent.cpp:56`); 1277 cooked BP packages analysed, 5871/5871 events recovered,
    0/122638 ubergraph statements unreached (`KR/Private/Analysis/MifUbergraphSlicer.h:4-5`);
    735/1250 all-event BPs score nothing (`:1314`).
- **Failure modes**: zero matches → immediate `done` with `bpTotal:0` and the filter echoed back;
  a mid-census crash → flushed BEGIN marker + `resumeHint: startIndex = bpDoneAbsolute`;
  census CSV unopenable → job continues, `censusCsvPath: null`, warning
  `"census file could not be opened — verdicts unaffected (census is diagnostic only)"`.
- **Cooked**: cooked-**only** by design — the `PKG_Cooked` gate. Loose BPs are exactly what the corpus
  number must not dilute. State this in the field docs.
- **Live proof**: `maxCount:5, pathFilter:"/Game/Blueprints/"`. `bpDone` must **advance across polls**
  (proves the per-tick slicing); final `pass + fail + skip == bpTotal == 5`; the five per-BP totals must
  sum **bit-identically** to five individual `kr_verify_fidelity` runs on the same BPs with the same
  `classifyIntentional`; the CSV at `censusCsvPath` exists and its row count equals the reported
  unclaimed-edit count.

### 5.3 `kr_classify_drift`

- **Params**: `sourceAsset` — required, strict. `function` (aliases `functionName`, `func`) — optional
  report filter (**the whole-BP verify still runs** — the pipeline is per-BP). `includeWindow` (alias
  `window`) — bool, default false (attaches the ±2-statement cooked/recon lossy window). 
  `classifyIntentional` — bool, default true.
- **Bucket / shape**: SelfManaged; async, `kind:"classify"`, single-BP, one deferred tick.
- **Engine call**: identical to `kr_verify_fidelity`. **Zero additional engine surface.**
- **Plugin-side change** (the only one): a module-static per-function verdict sink in
  `KR/Private/Verify/MifFidelityVerifier.cpp`, appended to on the drift path where
  `MifKr::DriftClassify::Classify(...)` is already called (`MifFidelityVerifier.cpp:541`). Armed before
  the callback, disarmed after. Game-thread-only by the existing threading contract, so a static sink is
  race-free. **The engine delegate is NOT widened** — `h:54-56` refuses arity changes on principle
  (K2 Negative #5).
- **Structured return**: `functions:[{name, verdict: identical|equivalent|intentional|drift|missing|uncomparable,
  reasons[], rootCookedOrdinal, rootReconOrdinal, rootCooked, rootRecon, cookedStmts, reconStmts, window?[]}]`
  plus the same aggregate report as `kr_verify_fidelity`, so the two cross-check.
  Verdict/root fields map 1:1 onto `FVerdict` (`KR/Private/Verify/MifDriftClassifier.h:28-46`).
- **Failure modes**: as `kr_verify_fidelity`, plus — classifier declined on a cap (>2000 statements /
  LCS cell cap / sim cap, `MifDriftClassifier.cpp:81-83`) ⇒ that function reports
  `verdict:"drift", reasons:["classifier-declined:cap"]`, root absent — mirroring the
  conservative decline-to-REAL contract (`MifDriftClassifier.h:20`). `function` naming a
  never-attempted function ⇒ `found:false` for it **plus the attempted-function list**, never a silent
  empty row.
- **Cooked / loose**: as `kr_verify_fidelity`.
- **Live proof**: on `BP_OponentPatrolRoute_C`, `count(verdict == "identical") == identical` (and the
  same for every other class) — an internal-consistency assert the implementation should also carry;
  the per-function `reasons` must reproduce the aggregate `intentTally` exactly;
  `classifyIntentional:false` yields zero `intentional` rows with `identical`/`equivalent` unchanged.

### 5.4 `kr_batch_reconstruct`

- **Params**: `pathFilter` (aliases `filter`, `pathContains`) — default `"/Game/"`, `"*"` = all.
  `mode` (alias `variant`) — enum `sibling|child`, default `"sibling"` (matches `ReconstructAll`'s
  `bAsChild = false` at `:1145`). `verify` — bool, default false, **requires `mode:"child"`**.
  `startIndex` — int, default 0. `maxBlueprints` (alias `limit`) — int, default 0 (= all).
- **Bucket / shape**: SelfManaged; async, `kind:"batch"`, ONE BP per tick (same slicing as census — a
  whole-corpus sweep is minutes long and today's console command blocks the editor for the entire run,
  `:1217`). GC every 25 (`:1303`) + once at the end (`:1306`).
- **Engine call**: `RunTransientBlueprintReconstruct(SourceBPGC, ParentClass, bAsChild, Stats, Results,
  bVerify ? verifyLambda : [](UBlueprint*){})`. **Same single export.**
- **Structured return**: `{bpTotal, bpDone, pass, fail, skip, skipTaxonomy:{resolve, parent, mint},
  csvPath}` and, when `verify:true`, the same totals block as §5.2. Per-BP CSV written to
  `<ProjectSaved>/MifKr/` with the engine's exact column set (`:1199-1200`: 17 columns with verify,
  9 without) and **flushed per row** (`:1196`) so a hard assert preserves partial results.
- **Failure modes**:
  - `verify:true, mode:"sibling"` → `"verify requires mode:'child' — a sibling copies its components into
    the transient package, so the drift would be an artefact of the mode, not of the decompiler"`
    (`:1158-1165` reasoning, verbatim).
  - zero targets → immediate `done`, `bpTotal:0`, filter echoed.
  - editor shutdown mid-job → record is in-memory only; the CSV on disk survives (flushed per BP).
- **Cooked**: the target set **is** the cooked corpus (`PKG_Cooked` via `IsCompiledBlueprintAsset`);
  loose BPs excluded by the same predicate that gates F3.
- **Live proof**: `pathFilter:"/Game/Blueprints/Enviro/", mode:"child", maxBlueprints:10`.
  `pass + fail + skip == bpTotal`; CSV row count `== bpTotal - startIndex`; spot-check one row's
  `RealFuncs` against `kr_dump_blueprint`'s own-function-with-bytecode count for the same BP. Full-corpus
  regression: expect PASS ≈ 1228/1256 on an unchanged decompiler.

### 5.5 Live-proof asset selection — a warning about the named candidates

The task named two assets. Both were checked against the code; **one of them is a trap**:

| Asset | Verdict |
|---|---|
| `/Game/Animations/AnimClasses/NPC/PrisonerAnimBP.PrisonerAnimBP_C` | **Do NOT use as a fidelity proof.** It is a `UAnimBlueprintGeneratedClass`. (a) `IsCompiledBlueprintAsset:115-118` matches class paths with **exact equality** against `UBlueprint`/`UBlueprintGeneratedClass`/`UWidgetBlueprint`/`UWidgetBlueprintGeneratedClass` — **anim BPGCs are rejected**, so `kr_drift_census` and `kr_batch_reconstruct` will never enumerate it. (b) `GetBlueprintClassTypesForSource:216-225` only special-cases Widget, so an anim source would mint a **plain `UBlueprint`** and lose its AnimGraph entirely. (c) K1 records anim-BP reconstruction as explicitly unimplemented future work. Use it as a **negative-path proof**: `kr_verify_fidelity` (which has no class gate) must either refuse it with a clear message or return honestly degraded numbers — **and the endpoint should detect `Cast<UAnimBlueprintGeneratedClass>` and say so**, rather than silently producing a meaningless score. Add that check. |
| `/Game/Audio/Music/ChaseAndFight/RaidAreaSphere.RaidAreaSphere_C` | Usable, but expect `scored:0, score:null` (a small trigger actor is almost certainly event-only). Perfect as the **"never 1.000"** proof; useless as a fidelity proof. |

**Primary positive fidelity proof** should therefore be K1's verified candidates, whose own-function
counts are already ground-truthed: `BP_OponentPatrolRoute_C` (7 own fns — the daily driver),
`BP_SegmentedPathTaskMarker_C` (4 own fns — the small case), `BP_BaseNPC_C` (113 own fns — the timing
stress case). All three are cooked, container-origin, and confirmed present in the registry by the
2026-07-26 `LIVE_PROBES.md` session. Note there are no loose `.uasset` files for these under
`Game/Content/` — they arrive premounted from the shipped containers, which is the whole point.

---

## 6. ROLLBACK

The fork is git, branch `BrandoCookedEditor-UE5.3.2`, HEAD `5d6943d1df2f`. **Current working tree
(verified this session):**

```
 M Engine/Build/BatchFiles/BuildUAT.bat
 M Engine/Build/BatchFiles/BuildUBT.bat
?? .github/
?? Engine/Config/DefaultEngine.ini
```

Four pre-existing, unrelated modifications — two tracked `.bat` edits and two untracked paths. **A Wave-3
change would be the FIRST engine change from this audit work, so it must stay cleanly separable from all
four.** Neither Wave-3 file overlaps any of them.

### 6.1 Files that change (exhaustive)

| # | File | Change |
|---|---|---|
| 1 | `ENG/Editor/Kismet/Public/CompiledBlueprintReconstructor.h` | +2 forward decls, +1 include, + the moved `FUncookedCopyStats` struct, + one `KISMET_API` declaration |
| 2 | `ENG/Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp` | − the struct at `:529-548`; `VerifyFidelityCmd:1421-1424` + `:1468-1469` replaced by one call; + the new definition after `:1541` |

**Nothing else in `D:/UE532` is touched.** No `.Build.cs`, no `.uplugin`, no config, no third file.

### 6.2 Rollback procedure

```bash
cd /d/UE532
git status --porcelain                                  # must show ONLY the 4 pre-existing entries + these 2
git diff --stat Engine/Source/Editor/Kismet/            # must be exactly 2 files
git checkout -- Engine/Source/Editor/Kismet/Public/CompiledBlueprintReconstructor.h \
                Engine/Source/Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp
```

Then rebuild Kismet + dependents (same ~15–40 min as §3.2 — a revert costs the same as the change).

### 6.3 Separability rules for the implementer

1. **Commit the engine change on its own**, touching only those two files, before any plugin work.
   Suggested message: `Kismet: export RunTransientBlueprintReconstruct for headless verify (MifBridge Wave 3)`.
   Do **not** let the two pre-existing `.bat` modifications or the untracked `.github/` and
   `DefaultEngine.ini` ride along — `git add` the two paths explicitly, never `git add -A`.
2. **Take a `git stash`-free baseline first**: `git diff > /path/scratch/pre_wave3_engine.patch` so the
   four pre-existing modifications are recoverable independently of anything Wave 3 does.
3. **The plugin side is independently revertable and independently useful.** §1.1's `AttemptedFunctions`
   capture, the census enumeration reuse, and the classify sink are all plugin-only and can land, and be
   reverted, without touching the engine at all.
4. **If the engine change must be backed out after the plugin ships**: the four Wave-3 endpoints stop
   compiling (they reference the export). Guard them behind the plugin's existing `#if WITH_MIFBRIDGE`
   pattern — no: use a *separate* `WITH_KR_TRANSIENT_RECONSTRUCT` define set in
   `KR/MifKismetReconstructor.Build.cs`, so backing out the engine change degrades to "the four verify
   endpoints do not register" rather than "the plugin does not build". Wave 1/2 endpoints are unaffected
   either way. This costs ~6 lines and is the difference between a 40-minute rollback and a broken tree.

---

## 7. FILE-TOUCH LIST (complete)

| # | File | Change | Depends on the engine change? |
|---|---|---|---|
| 1 | `ENG/Editor/Kismet/Public/CompiledBlueprintReconstructor.h` | §2.2 + §2.4 | — |
| 2 | `ENG/Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp` | §2.5 | — |
| 3 | `KR/Private/MifKrBridgeEndpoints.cpp` | 4 new handlers + 4 `Reg(...)` lines; reuse the existing cooked-BP filter helper | **yes** |
| 4 | `KR/Private/MifKrJobManager.h` (+ its `.cpp`, wherever the impl currently lives) | add `Kind` values `verify`/`classify`/`census`/`batch`; add the census/batch progress fields (`BpDone`, `BpTotal`, `Pass`, `Fail`, `Skip`, `SkipResolve`, `SkipParent`, `SkipMint`, running totals); widen the function-graph delegate lambda to capture `UFunction*` (§1.1) | no (§1.1 is engine-free) |
| 5 | `KR/Private/Verify/MifFidelityVerifier.cpp` | per-function verdict sink for `kr_classify_drift`, at the existing `Classify(...)` call site (`:541`) | no |
| 6 | `KR/MifKismetReconstructor.Build.cs` | `WITH_KR_TRANSIENT_RECONSTRUCT` define (§6.3 item 4) | — |
| 7 | `tools/ue5-mcp-bridge/server.py` | one `@mcp.tool()` per new endpoint | no |
| — | `MB/**` | **UNCHANGED.** The registry (`MB/Public/MifBridgeEndpointRegistry.h`) and `MifBridgeCommon.cpp`'s external-registry merge already landed in Wave 1. Listed only to record that it was checked. | — |

**Suggested batch order** (each step independently buildable and revertable):
(1) file 4's §1.1 capture + a plugin-only unit check that the captured set matches
`Stats.AttemptedFunctions` on a known BP — this is free and it de-risks everything downstream;
(2) files 1–2, ONE engine build, verify `mif.kr.VerifyFidelity BP_OponentPatrolRoute child` still prints
identical output to before the refactor (the regression gate: the console command must be behaviourally
unchanged); (3) files 3+6 + `kr_verify_fidelity`; (4) `kr_classify_drift` (file 5) and `kr_drift_census`;
(5) `kr_batch_reconstruct`; (6) file 7.

---

## 8. Verification log for this plan

Every anchor below was re-opened and matched against the CURRENT file this session. **Zero drift from
K1/K2 was found in the engine TU** — all cited line numbers are still exact.

- `ENG/Editor/Kismet/Public/CompiledBlueprintReconstructor.h` — read in full (113 lines).
  `KISMET_API` at `:24`, `:37`, `:61`, `:113` — **all four exact**. `"modkit: MifBridge unification"`
  comment at `:26` — exact. `FBlueprintFidelityReport` `:71-104` with `NoScore = -1.0f` at `:97`,
  `Scored()` `:91`, `Score()`/`AdjustedScore()` `:102-103`. `AttemptedFuncs` contract `:106-107`.
  Delegate-arity refusal `:54-56`. **No UHT macros, no `.generated.h`.**
- `ENG/Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp` (1752 lines) — read `:96-140`, `:214-233`,
  `:500-660`, `:657-812`, `:880-960`, `:1060-1150`, `:1149-1348`, `:1350-1480`, `:1541-1650`; full
  symbol-census grep of the rest. **Verified exact**: `IsCompiledBlueprintAsset` `:102`,
  `ResolveBlueprintClass` `:121`, `FUncookedCopyStats` `:531` (struct body `:531-548`),
  `CopyFunctionStubs` `:551`, `PopulateUncookedCopy` `:892`, `Execute` `:958`, `RunReconstructOnce`
  `:1089-1124`, `ReconstructAll` `:1140`, `VerifyFidelityCmd` `:1356-1470`, its registration `:1472-1475`,
  `CreateAndSaveEditableCopy` `:1551`, `EnsureEditableParentChain` `:1650`,
  `CreateEditableBlueprintCopy` definition `:1709-1750`. All are `static` inside
  `namespace CompiledBlueprintCopyAction` (`:100`) except the last three, which are global + `KISMET_API`.
- **New this session, not in K1/K2**: the 1:1 `Execute`↔`AttemptedFunctions.Add` pairing at
  `:639`/`:652` and `:794`/`:799` (§1.1); `GetBlueprintClassTypesForSource:216-225` handles only Widget
  (§5.5); `IsCompiledBlueprintAsset:115-118` uses exact class-path equality and excludes anim BPGCs
  (§5.5); `CreateAndSaveEditableCopy:1606` compiles with **no** `FCompilerResultsLog` and no
  `bSilentMode` (§1.3); `UnrealEd` is a **Private** dependency of Kismet, forcing the
  `FCompilerResultsLog` forward declaration (§2.2).
- **Hazard grep** over the whole TU: `FMessageDialog` at `:971`/`:980`/`:1019` and `SDlgPickAssetPath`
  at `:993` — **all four inside `Execute` (F3) only**. `CollectGarbage` at `:1303`/`:1306` — batch loop
  only. `SavePackage` at `:1635`, `AssetCreated` at `:1071`/`:1627`, `OpenEditorForAsset` at `:1076` —
  all outside the throwaway path. **Zero** `FScopedSlowTask`, `GWarn`, `FlushAsyncLoading`,
  `FlushRenderingCommands`, `FScopedTransaction`, `FPlatformProcess::Sleep`.
- **Include census**: `CompiledBlueprintReconstructor.h` is included by 1 engine TU (`:34` and `:50`,
  duplicate) and 4 project files. In no shared PCH.
- **Dependency census**: 26 engine `.Build.cs` list `"Kismet"` (incl. `UnrealEd.Build.cs:139`/`:265`),
  49 engine-plugin `.Build.cs`, both project plugins. Import-lib optimization proven live by the
  `UnrealEditor-Kismet.lib` (Jul 25 09:12) vs `.sup.lib` (Jul 25 09:29) timestamp/size pair.
- `ENG/Editor/UnrealEd/Public/Kismet2/KismetEditorUtilities.h` — `CompileBlueprint` `:169`,
  `CanCreateBlueprintOfClass` `:178`, `GenerateBlueprintSkeleton` `:172` — all `static UNREALED_API`,
  verbatim.
- Plugin side (read-only; **another agent is editing this tree concurrently**, so these are cited by
  symbol, not line): `MB/Public/MifBridgeEndpointRegistry.h` (full, 85 lines — the registrar shipped);
  `KR/Private/MifKrJobManager.h` (full, 126 lines — reserves verify/census/classify for Wave 3 at `:44`);
  `KR/Private/MifKrBridgeEndpoints.cpp` (header block + the `MifKr_RegisterBridgeEndpoints` /
  `Reg(...)` pattern — Wave 1 `kr_list_cooked_blueprints` registered; Wave 2 in flight);
  `KR/MifKismetReconstructor.Build.cs` (full — `Kismet` `:26`, `UnrealEd` `:25`, `WITH_MIFBRIDGE`
  `:36-45`); `KR/Private/Verify/MifFidelityVerifier.cpp` (`VerifyBlueprint` `:432`, `Classify` call site
  `:541`, bind `:609-611`); `KR/Private/Verify/MifDriftClassifier.cpp` (`CVarClassifyIntentional` `:57`,
  `CVarDriftCensus` `:66`, `CensusWrite` `:352-370`, `Classify` `:401`).
- **Git**: branch `BrandoCookedEditor-UE5.3.2`, HEAD `5d6943d1df2f`, 4 pre-existing modifications
  (2 tracked `.bat`, 2 untracked). Neither Wave-3 file is among them.

### UNVERIFIED (honest gaps)

- **No wall-clock measurement exists** for a single-BP reconstruct+verify, anywhere in source. The
  async design rests on the verified proxies only (the 8 s GC stall / ~40-function crash note at
  `KR/Private/MifReconstructCommand.cpp:43`; the batch harness's per-BP crash-flush design). §5.1's live
  proof must record real ms for one small and one large BP.
- **The 15–40 min build estimate is a derivation, not a measurement** — it follows from the verified
  include census, the verified `.lib`/`.sup.lib` mechanism and the verified dependent counts, but no
  Wave-3 build has been run (deliberately: the task forbids building).
- **`FLatentActionInfo` / anim-BP behaviour under `CanCreateBlueprintOfClass`** was not exercised;
  §5.5's anim-BP conclusion rests on reading `IsCompiledBlueprintAsset:115-118` and
  `GetBlueprintClassTypesForSource:216-225`, not on running the command against `PrisonerAnimBP_C`.
- **`IConsoleVariable::Set` + restore inside a deferred slice**: both CVars are read via
  `GetValueOnAnyThread()` (`MifDriftClassifier.cpp:73`, `:879`); no render-thread reader was audited.
  Low risk (the classifier runs inside the same game-thread call), unproven.
- **Plugin line anchors are volatile** — `KR/Private/MifKrBridgeEndpoints.cpp` grew between two reads
  during this very session (Wave 2 landing concurrently). Re-anchor plugin-side citations at
  implementation time; engine anchors are stable.
