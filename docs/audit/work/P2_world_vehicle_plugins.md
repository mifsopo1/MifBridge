# Axis P2 — world/vehicle content plugins (Oceanology, Riverology, FGear)
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth (extension sweep on the completed 223-entry audit)._

All plugin paths below are relative to `D:/DDS2SDK/Game/Plugins/`. Engine citations are relative
to `D:/UE532/Engine/Source`. Live probes ran against `http://127.0.0.1:8791/api/` (bridge went
down mid-sweep — connection refused at ~first attempt — and came back; all probes below are from
the post-recovery window, `self_audit` reporting 160 endpoints ok).

## The axis-defining fact: all three plugins are stub reconstructions, not vendor source

The brief calls these "third-party with source". The source present is an **SDK-style reflection
reconstruction**: real UCLASS/UPROPERTY/UFUNCTION declarations (matching the cooked game's
reflection layout) with **empty native function bodies**. Evidence, per the anti-invention rule:

- `Oceanology_Plugin/Source/Oceanology_Plugin/Private/OceanologyWaterParent.cpp:70-72`:
  ```cpp
  FVector AOceanologyWaterParent::GetWaveHeightAtLocation(const FVector& Location) {
      return FVector{};
  }
  ```
  Every non-constructor body in the file is `{}` / `return NULL;` / `return 0.0;` (read in full).
- `FGearPlugin/Source/FGearPlugin/Private/FGearVehicle.cpp:121-122` `void AFGearVehicle::setSteering(float steerInput) {\n}` and `:408-410` `float AFGearVehicle::getKMHSpeed() const { return 0.0f; }`.
- `Riverology_Plugin/Source/Riverology_Plugin/Private/Riverology_PluginBPLibrary.cpp` — all three
  statics empty (`GetEditorPaintLayer` returns `NULL`).
- **The editor loads DLLs built from these stubs, not vendor binaries**:
  `Oceanology_Plugin/Binaries/Win64/UnrealEditor-Oceanology_Plugin.dll` (838 KB, 2026-07-25 15:42)
  with matching locally-generated UHT artifacts in
  `FGearPlugin/Intermediate/Build/Win64/UnrealEditor/Inc/FGearPlugin/UHT/FGearVehicle.gen.cpp`
  (same for the other two), and `Binaries/Win64/UnrealEditor.modules` sharing one local
  `BuildId: e6efe3a6-080c-4bb5-8710-936512e1195a` across the plugins.
- Reconstructed `Build.cs` files are minimal (Oceanology: Core/CoreUObject/DeveloperSettings/
  Engine/Niagara only — no Renderer/RHI deps a real ocean renderer needs).

**Consequences for every idea on this axis** (this is the contract the entries below follow):
1. Reflection data is REAL — `set_property`/`get_property`/`list_object_properties`/
   `describe_class`/`spawn_actor_in_level` work fully on these classes and on the cooked BP
   children that ship in pakchunk0, and values authored this way cook into mods that behave
   correctly in the SHIPPED game (which runs the vendor's real implementations).
2. Any endpoint whose value depends on EXECUTING a native plugin function in the modkit editor
   returns zeros / does nothing (stub body executes fine, produces nothing). This kills the two
   headline candidates (`get_ocean_height`, `pie_drive_vehicle`/`get_vehicle_telemetry`) —
   documented as negatives with full citations, not as endpoints.
3. Cooked **Blueprint bytecode still executes** in-editor (construction scripts, BP functions) —
   the one live execution lane, exploited by the single endpoint proposed below.

## Surface inventory

### Oceanology_Plugin
- `.uplugin`: "OCEANOLOGY 5" VersionName 5.6.0, EngineVersion 5.3.0, by Galidar; one Runtime
  module `Oceanology_Plugin` (PostConfigInit, Win64/Mac); depends on plugin `Niagara`;
  `CanContainContent: true`. Project-enabled in DrugDealerSimulator2.uproject (line 43).
- Header census: 101 headers in `Source/Oceanology_Plugin/Public/` (82 .cpp in Private/). Read in
  full: OceanologyWaterParent.h, OceanologyWaveSolverComponent.h,
  OceanologyGerstnerWaveSolverComponent.h, OceanologyInfiniteOcean.h, OceanBuoyancyComponent.h,
  OceanologyManager.h, OceanologyWaterVolume.h (head), Oceanology_PluginBPLibrary.h,
  OceanologyWave_1.h, OceanologyGlobalDisplacement.h; class-decl grep over OceanologyLake.h,
  OceanologySwimVolume.h, OceanologyWaterMeshComponent.h, OceanologyInfiniteComponent.h,
  QuadTree.h.
- Export census: actor/component classes are class-level `OCEANOLOGY_PLUGIN_API`
  (`class OCEANOLOGY_PLUGIN_API AOceanologyWaterParent : public AActor` OceanologyWaterParent.h:34;
  same pattern OceanologyInfiniteOcean.h:17, OceanologyLake.h:20, OceanologyWaterVolume.h:12,
  OceanologySwimVolume.h:7, OceanologyWaterMeshComponent.h:11, OceanologyManager.h:12,
  OceanBuoyancyComponent.h:18, OceanologyWaveSolverComponent.h:13,
  OceanologyGerstnerWaveSolverComponent.h:18, QuadTree.h:10). Structs export only their ctors
  (`OCEANOLOGY_PLUGIN_API FOceanologyWave_1();` OceanologyWave_1.h:24). Exception:
  `class UOceanology_PluginBPLibrary : public UBlueprintFunctionLibrary`
  (Oceanology_PluginBPLibrary.h:12) has **no export macro** — reflection-only; it contains a
  single `Oceanology_PluginSampleFunction` anyway (template junk, nothing to expose).
- Key reflection surface on AOceanologyWaterParent (all UPROPERTY BlueprintReadWrite EditAnywhere):
  `WaveSolverClass: TSubclassOf<UOceanologyWaveSolverComponent>` (:59), `WaveSolver` instanced
  (:62), material config structs `SurfaceScattering/Caustics/Refraction/HorizonCorrection/
  flipbook/Foam/Folding/Procedural/RVT/Mask/ActorHeight/GGX` (:68-101), `Material`/`MaterialFar`
  (:107-110). Wave params live on the SOLVER component:
  `GlobalDisplacement/BaseOffset/Wave_1..Wave_4/Summarize`
  (OceanologyGerstnerWaveSolverComponent.h:21-40).
- DDS2-usage evidence:
  - Cooked registry (`D:/DDS2SDK/Game/AssetRegistry.bin`, binary-grepped + base64 blob decoded):
    `/Game/Blueprints/Enviro/BP_OceanologyInfiniteOcean_ChildBTR` with NativeParentClass tag
    `/Script/CoreUObject.Class'/Script/Oceanology_Plugin.OceanologyInfiniteOcean'` (verbatim at
    offset ~3658573); plugin content cooked into pakchunk0
    (`/Oceanology_Plugin/Design/Ocean/Blueprints/Ocean/BP_OceanologyInfiniteOcean` et al.).
  - Live `find_assets nameContains=Oceanology` → 11 hits incl. the game-side
    `BP_OceanologyInfiniteOcean_ChildBTR_C`, `OceanologyBTR_C`, `Oceanology_Infinity_ChildBTR_C`,
    `M_Oceanology_InstBTR` (MaterialInstanceConstant), plus plugin-content BPs (all origin
    container).
  - Live `describe_class` on the child: parent chain
    `BP_OceanologyInfiniteOcean_ChildBTR_C → BP_OceanologyInfiniteOcean_C (plugin content BP)`;
    native `/Script/Oceanology_Plugin.OceanologyWaterParent` reflects live (139 fns / 54 props).
  - Live `get_property WaveSolverClass` on BOTH the game child CDO and the plugin BP CDO →
    `"None"` (relevant to the get_ocean_height negative below).

### Riverology_Plugin
- `.uplugin`: "RIVEROLOGY 2" VersionName 2.2.0, EngineVersion 5.3.0, by Galidar; one Runtime
  module `Riverology_Plugin` (PreDefault, Win64); `CanContainContent: true`. Project-enabled
  (uproject line 48).
- Header census: THREE headers total (read in full): `Riverology.h`, `RiverologyLandscapeStruct.h`,
  `Riverology_PluginBPLibrary.h`; 4 .cpp. DLL 107 KB (2026-07-25 09:35), locally built.
- Export census: `class RIVEROLOGY_PLUGIN_API ARiverology : public AActor` (Riverology.h:11) with
  exactly three UPROPERTYs and ZERO UFUNCTIONs: `Root` (:15),
  **`SplineComponent: USplineComponent*` (Instanced, :18)**, `RiverologyLandscape:
  FRiverologyLandscapeStruct` (:21). The struct (RiverologyLandscapeStruct.h:8-38, ctor exported
  :38) carries landscape-deform config: `ApplyLandscapeSpline`, `Landscape: ALandscape*`,
  `RaiseHeights/LowerHeights`, `DeformWidth/DeformFalloff`, `PaintLayerName`,
  `EditLayerToPaintOn`, `RefreshGrassFoliage`. BP library class UNEXPORTED
  (Riverology_PluginBPLibrary.h:12) with 3 static BlueprintCallables (all stubs).
- **Everything that makes a river a river lives in the cooked plugin-content Blueprint**
  `/Riverology_Plugin/Advanced/Blueprints/BP_Riverology.BP_Riverology_C` — live `describe_class`:
  parent `/Script/Riverology_Plugin.Riverology`, own BP functions include
  **`SetupSplineMeshComponents` (params: [])**, `GetSplineMeshComponents`,
  **`Editor Apply Spline` (params: [])**, `CalculateFlow`, `Calculate Buoyancy`,
  `UserConstructionScript`, `Waves`, `God Rays`, `Post Process`, `Entered/Exited Water`.
- DDS2-usage evidence: cooked registry shows `/Game/Blueprints/Enviro/BP_RiverologyBTR`
  (name blob decoded at ~1600863); live `find_assets nameContains=Riverology` → 6 hits;
  live `describe_class BP_RiverologyBTR_C` → parent `BP_Riverology_C`; live
  `list_object_properties` on `Default__BP_RiverologyBTR_C` → 157 props readable, incl. BP struct
  props with exotic names (`Σ1: FWave_1`, `River ` with trailing space, `Post-Process DYN`),
  `UnscaledSplineWidth: double = 75.0` — the whole BP config surface is already addressable by
  the existing property lane.

### FGearPlugin
- `.uplugin`: "FGear Vehicle Physics" VersionName 1.8.1 (Version 14), EngineVersion 5.3.0, by
  lazybitgames; one Runtime module `FGearPlugin` (Default, Win64/Android); depends on plugins
  `ProceduralMeshComponent`, `EnhancedInput`; `CanContainContent: true`. Project-enabled
  (uproject line 100).
- Header census: 53 headers in `Source/FGearPlugin/Public/` (36 .cpp). Read in full:
  FGearVehicle.h (609 lines), FGearEngine.h, FGearStandardInput.h (582 lines), FGearSettings.h;
  head/grep census: FGearAutoDrive.h, FGearSpline.h, FGearWheel.h.
- Export census: everything class-level `FGEARPLUGIN_API`:
  `class FGEARPLUGIN_API AFGearVehicle : public APawn` (FGearVehicle.h:35) — ~40 Instanced
  drivetrain sub-object UPROPERTYs (`mEngine`/`mTransmission`/`mStandardInput`/`mAeroDynamics`/
  `mAxle0`/`mWheelLeft0`/… :48-97) + ~50 tuning scalars + ~140 BlueprintCallable UFUNCTIONs
  (telemetry getters :405-598, config setters :245-375, `setSteering` :278-279, `setBraking`
  :365-366, `Reset` :391-392); `class FGEARPLUGIN_API UFGearEngine` (FGearEngine.h:9, `getRPM`
  :94-95); `class FGEARPLUGIN_API UFGearStandardInput` (FGearStandardInput.h:15,
  `setInputs(Throttle,Brake,steer,Clutch,gear,hb)` :316-317, `overrideFinalInputs` :382-383);
  `class FGEARPLUGIN_API AFGearAutoDrive : public AAIController` (FGearAutoDrive.h:11, spline-
  following AI driver over `AFGearSpline` FGearSpline.h:11); `UCLASS(Blueprintable,
  DefaultConfig, Config=Game) class FGEARPLUGIN_API UFGearSettings : public UDeveloperSettings`
  (FGearSettings.h:6-7).
- DDS2-usage evidence (tiering input):
  - Cooked registry: NativeParentClass `/Script/CoreUObject.Class'/Script/FGearPlugin.FGearVehicle'`
    present (offset ~3956488); decoded per-asset blobs prove EXACTLY THREE FGear vehicles:
    `RoadVeh_DriveableBicycle`, `RoadVeh_DriveableTest3`, `RoadVeh_DriveableTest4` (all under
    `/Game/Blueprints/QuickTravel/RoadVehicles/`), while `RoadVeh_Hatchback/Sedan/SUV/SmallBike`
    and the whole boat family (`/Game/Blueprints/QuickTravel/Boats/BP_VehicleBoat_*`,
    `BP_IntroMotorboatAI`) decode to native parent `/Script/Engine` + `Character` — NOT FGear.
  - Live confirmation: `describe_class RoadVeh_DriveableTest3_C` → parent
    `/Script/FGearPlugin.FGearVehicle`, 325 functions (player-interaction BP layer on top);
    `BP_VehicleBoat_Catamaran_C` → parent `OwnedVehicle_BoatLarge_C` (Character lane);
    `BP_ChristmasRoadVehicle_C` → parent `OwnedVehicle_Car_C` (Character lane).
  - Verdict: FGear is REAL but MARGINAL in DDS2 — an experimental "driveable" lane (one bicycle,
    two Test assets), not the shipped traffic/boat systems. Tier anything FGear-specific low.
  - Live `get_property` on `Default__RoadVeh_DriveableTest3_C` with propertyPath
    `mEngine.mIdleRpm` → `1000.000000` — the Instanced sub-object dot-path lane works TODAY on
    the cooked vehicle CDOs (composition proof).

### Engine/bridge files read for this axis
`Runtime/CoreUObject/Public/UObject/Object.h` (:1197, :1391, :1419),
`Runtime/CoreUObject/Public/UObject/UnrealType.h` (:465-499, :599),
`Runtime/CoreUObject/Public/UObject/Script.h` (:141, :157, :178),
`Runtime/Engine/Classes/GameFramework/Volume.h` (:52), `Runtime/Engine/Public/EngineUtils.h`
(:517), `MifBridge/Private/MifBridgeLevel.cpp` (:131 list_level_actors filter contract),
plus `work/F_world_level.md` (set_water_body_profile cross-reference, §577-610) and
`work/LIVE_PROBES.md` (bridge health precedent).

## Proposed endpoints

### call_object_function
**Purpose**: Invoke a named UFUNCTION on any objectPath via reflection — the ONLY way to trigger
the cooked-Blueprint rebuild logic these plugins keep in bytecode (Riverology's
`Editor Apply Spline` / `SetupSplineMeshComponents` after a set_spline_points edit), and the
general "poke the object after property edits" lane (`ForceFollow`, `LoadPreset`,
`CreateOrUpdateWaterMID`, …) that no existing endpoint covers (the 160-endpoint list has
graph-authoring `add_function_call` but no runtime invoke; `run_console 'ke …'` cannot address a
single actor instance by path and cannot tokenise BP function names containing spaces, which
`Editor Apply Spline` has).
**Engine API**:
```cpp
COREUOBJECT_API UFunction* FindFunction( FName InName ) const;
COREUOBJECT_API virtual void ProcessEvent( UFunction* Function, void* Parms );
COREUOBJECT_API bool CallFunctionByNameWithArguments( const TCHAR* Cmd, FOutputDevice& Ar, UObject* Executor, bool bForceCallWithNonExec = false );
```
Runtime/CoreUObject/Public/UObject/Object.h:1197, :1391, :1419 (all methods of UObject;
method-level export macros as shown). Argument/result marshalling per parameter property:
```cpp
const TCHAR* ImportText_Direct(const TCHAR* Buffer, void* PropertyPtr, UObject* OwnerObject, int32 PortFlags, FOutputDevice* ErrorText = (FOutputDevice*)GWarn) const
COREUOBJECT_API bool ExportText_Direct(FString& ValueStr, const void* Data, const void* Delta, UObject* Parent, int32 PortFlags, UObject* ExportRootScope = nullptr) const;
```
Runtime/CoreUObject/Public/UObject/UnrealType.h:499 (inline), :599. Safety flags:
`FUNC_Native` Script.h:141, `FUNC_BlueprintCallable` :157, `FUNC_NetFuncFlags` macro :178.
Implementation shape: resolve object (existing objectPath resolver), `FindFunction(FName)`,
allocate `Function->ParmsSize` buffer (UFunction member, Runtime/CoreUObject/Public/UObject/Class.h:1795),
`InitializeValue`+`ImportText_Direct` each in-param, `ProcessEvent`, `ExportText_Direct` each
out/return param into the JSON response, destroy values.
**Export**: `COREUOBJECT_API` (method-level, verbatim above) | **Module**: none — CoreUObject
already linked | **Guards**: none (reflection call machinery is runtime code; MifBridge is
editor-only regardless)
**Bucket**: transacted — the intended callees mutate actor state / (re)build components
(`Editor Apply Spline` rebuilds spline meshes) and a single blanket FScopedTransaction matches
the set_spline_points precedent; the handler itself registers no new assets. Callees that
compile Blueprints or tear down worlds are OUT OF CONTRACT (documented, see failure modes).
**Async**: no (synchronous ProcessEvent; latent-flagged functions are refused, see below)
**Params**: | name | aliases | type | default | required |
| objectPath | object, target | string (any objectPath: placed actor, CDO, component, template) | — | yes (strict) |
| function | functionName, name | string (FName — spaces preserved, e.g. `"Editor Apply Spline"`) | — | yes (strict) |
| args | arguments | object: paramName → value (stringified per-property via ImportText_Direct; object-reference params accept object paths) | `{}` | no |
Unrecognised body parameter ⇒ error naming it. Unrecognised key inside `args` (no matching param
property on the UFunction) ⇒ error naming the key AND listing the function's real parameter names.
**Failure modes**:
- object not found → `"objectPath '<p>' not found — pass a full /Game/... or /Script/... path"`.
- function not found → `"function '<f>' not found on <Class> — describe_class lists its functions"`.
- net function (`FUNC_NetFuncFlags`) → `"'<f>' is a replicated (Server/Client/Multicast) function — refusing to call it locally"`.
- latent function (`FBlueprintMetadata`/`meta=Latent`) → `"'<f>' is latent — cannot run inside a single bridge frame"` (refuse; brief invariant 3).
- ImportText failure on an arg → `"arg '<name>': could not parse '<v>' as <PropertyType>"`.
- missing required arg: params without defaults left uninitialised are zero-filled — response
  echoes `argsDefaulted:[...]` so silent zeros are visible.
- STUB-NATIVE caveat (this axis's discovery): calling a native function of a reconstructed plugin
  executes the empty stub — succeeds, does nothing. Response therefore always echoes
  `isNative:<bool>` (FUNC_Native) so an agent can tell "ran bytecode" from "ran a maybe-stub".
**Cooked**: WORKS — this endpoint exists BECAUSE cooked BP bytecode executes; it is the only lane
that reaches it. Native stubs caveat above; cooked-map save restrictions unchanged.
**Verify**: spawn `/Riverology_Plugin/Advanced/Blueprints/BP_Riverology.BP_Riverology_C` via
spawn_actor_in_level → set_spline_points (component `SplineComponent`, Riverology.h:18) →
call_object_function `SetupSplineMeshComponents` → `list_components` counts USplineMeshComponents
before/after (number must rise from 0); pure-getter path: call `GetSplineMeshComponents` and
compare returned array length to the list_components count. (If the count stays 0 the stub-library
degradation flagged in UNVERIFIED #2 is confirmed — the endpoint still verifiably executed BP
bytecode via the `isNative:false` echo + transaction dirty-state.)
**Score**: U5 E3 R3 → tier 1 (unlocks the cooked-BP execution category for every axis: river
rebuild here, but equally `ForceFollow`, `LoadPreset(UOceanologyOceanPreset*)`, RamaSave library
statics, DLC quest debug hooks like `DebugFinishPreChristmasTask` found by the J probes)
**Phase-2 verdict**: CONFIRMED — all engine cites re-opened and exact: `COREUOBJECT_API UFunction*
FindFunction(FName) const` Object.h:1197, `COREUOBJECT_API virtual void ProcessEvent(UFunction*, void*)`
:1391, CallFunctionByNameWithArguments :1419; ImportText_Direct inline UnrealType.h:493-499 verbatim
(incl. GWarn default); `COREUOBJECT_API bool ExportText_Direct(...)` :599; FUNC_Native Script.h:141,
FUNC_BlueprintCallable :157, FUNC_NetFuncFlags macro :178; `uint16 ParmsSize` Class.h:1795. Riverology
verify-path re-proven: ARiverology declares SplineComponent (Riverology.h:18) and zero UFUNCTIONs (file
re-read in full); spline finder PostEditChange confirmed (MifBridgeWorld.cpp:291 — note: only ONE
PostEditChange hit in that file; the ":232" half of the composition #3 cite is wrong, :291 is the real
one). No hidden modal/blocking hazard: ProcessEvent path is synchronous bytecode; the latent/net
refusals + isNative echo are the right guards. Bucket `transacted` consistent with set_spline_points
precedent; callees that compile/tear down remain out-of-contract as documented. No name collision:
`call_object_function` absent from 01_CATALOGUE.md, the brief's 160 list, and the live 165-endpoint
surface (re-probed this pass). Live composition proof reproduced this pass: get_property
`mEngine.mIdleRpm` on Default__RoadVeh_DriveableTest3_C → `1000.000000` (float), and WaveSolverClass
resolves on the plugin BP CDO.

## Compositions (no new endpoint needed) — with concrete objectPaths

1. **FGear vehicle config authoring** (works today, proven live): the whole drivetrain is
   Instanced sub-objects on the CDO. `get_property`/`set_property` with
   `objectPath: /Game/Blueprints/QuickTravel/RoadVehicles/RoadVeh_DriveableTest3.Default__RoadVeh_DriveableTest3_C`
   and `propertyPath: mEngine.mIdleRpm` returned `1000.000000` live. Same lane reaches
   `mTransmission.*`, `mAxle0/1.*`, `mWheelLeft0.*`, `mStandardInput.mDefaultMappingContext`,
   `mMass`, `mABS`, etc. (FGearVehicle.h:48-235). Mods tuned this way run REAL physics in the
   shipped game. No endpoint proposed — the brief's set_property rule covers it.
2. **Spawn + configure an Oceanology ocean/lake**: `spawn_actor_in_level` with class
   `/Script/Oceanology_Plugin.OceanologyInfiniteOcean` (native, spawnable — UCLASS Blueprintable,
   OceanologyInfiniteOcean.h:16-17) or the cooked BP
   `/Oceanology_Plugin/Design/Ocean/Blueprints/Ocean/BP_OceanologyInfiniteOcean.BP_OceanologyInfiniteOcean_C`;
   sea level = actor Z via existing `set_actor_transform`; visual tuning via `set_property` on the
   `SurfaceScattering`/`Caustics`/`Foam`/… structs (OceanologyWaterParent.h:68-101); ocean
   material params via existing `set_material_parameter` on
   `/Game/Blueprints/Enviro/M_Oceanology_InstBTR` (MaterialInstanceConstant, found live). Caveat:
   in the modkit editor the surface will not render/simulate (stub mesh component, negative #6) —
   authoring is data-true, verification is numeric (get_property round-trip), pixels only in the
   shipped game.
3. **Reshape a DDS2 river**: `get_spline_points`/`set_spline_points` already bind — ARiverology's
   spline is a plain `USplineComponent` UPROPERTY named `SplineComponent` (Riverology.h:18), and
   the F-axis confirmed the spline finder walks any USplineComponent and ends with
   `PostEditChange()` (MifBridgeWorld.cpp:232/:291), which re-runs the cooked construction
   script. Landscape-deform config rides `set_property` on `RiverologyLandscape.*`
   (RiverologyLandscapeStruct.h:11-36). Explicit rebuild/regenerate = `call_object_function`
   `"Editor Apply Spline"` / `SetupSplineMeshComponents` (proposed above) — there is NO native
   rebuild function to bind (negative #4).
4. **Water census / "what water is at (x,y)"**: existing `list_level_actors` takes `classFilter`
   (MifBridgeLevel.cpp:131) — filter on `OceanologyWaterParent`, `Riverology`,
   `OceanologyWaterVolume`, engine `WaterBody` in turn; then `get_actor_bounds` (point-in-AABB
   client-side) and surface Z = actor Z (ocean/lake) or nearest spline point Z
   (`get_spline_points` on rivers). A dedicated brush-accurate endpoint was considered and
   REJECTED (negative #9).
5. **FGear global settings**: `UFGearSettings` is `Config=Game` UDeveloperSettings
   (FGearSettings.h:6-7) — readable/writable in-memory via set_property on
   `/Script/FGearPlugin.Default__FGearSettings`; note the write does NOT persist to
   DefaultGame.ini (SaveConfig is not a UFUNCTION, unreachable even via call_object_function) —
   in-memory only, flag in docs.

### Cross-reference: F-axis `set_water_body_profile` (no collision)
That endpoint targets the ENGINE Water plugin tree (`AWaterBody`/`UWaterSplineMetadata`,
`WATER_API`, F_world_level.md:577-610) — a disjoint class hierarchy from Oceanology/Riverology
(both derive straight from AActor). DDS2's shipped sea is **Oceanology**
(BP_OceanologyInfiniteOcean_ChildBTR), not AWaterBody; the /Water content found live
(`/Water/Waves/GerstnerWaves_Ocean` WaterWavesAsset, loose+loaded) is the Water plugin's own
mounted content, not proof of in-map use. Both entries stand; merge should note: "engine Water
lane = set_water_body_profile; Oceanology/Riverology lane = set_property + call_object_function
compositions (P2)".

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

_Phase-2 spot-verification of ALL negatives (2026-07-26, this pass): stub bodies re-read at the exact
cited lines — `GetWaveHeightAtLocation { return FVector{}; }` OceanologyWaterParent.cpp:70-72; parent
UFUNCTION is `(BlueprintCallable, BlueprintPure)` with NO BlueprintNativeEvent (OceanologyWaterParent.h:151-152)
while the solver's IS BlueprintNativeEvent (OceanologyWaveSolverComponent.h:62-63) — exactly as #1 states;
GerstnerWave stub cpp:22-24, and the file's only two `_Implementation` defs are
UpdateOceanWavesByPresetResult/UpdateLakeWavesByPresetResult (:8,:11) — no GetWaveHeightAtLocation
override, #1 stands. FGear stubs re-read: setSteering cpp:121-122 `{}`, getKMHSpeed cpp:408-410
`return 0.0f;` — #2 stands (and the reflection surface really is all FGEARPLUGIN_API + BlueprintCallable,
so the block is the stubs, not linkability — correct per the false-negative watch-item). #4/#5:
Riverology.h zero UFUNCTIONs re-confirmed; BP library unexported (Riverology_PluginBPLibrary.h:12) with
3 BlueprintCallable statics all stubbed (GetEditorPaintLayer `return NULL;` cpp:9-10) — reflection could
CALL them but they DO nothing, negative correctly stub-based. #9: `ENGINE_API bool EncompassesPoint`
Volume.h:51-52 + TActorIterator EngineUtils.h:517 both exact — revival path intact. Inventory nit: the
unexported Oceanology BP library class decl is at Oceanology_PluginBPLibrary.h:7, not :12 (verdict
unchanged). uproject lines 43/48/100 re-confirmed._

1. **get_ocean_height (wave height at (x,y,t)) — NOT VIABLE in the modkit editor.** The API the
   task hoped for EXISTS and is both exported and BlueprintCallable:
   `UFUNCTION(BlueprintCallable, BlueprintPure) FVector GetWaveHeightAtLocation(const FVector& Location);`
   (OceanologyWaterParent.h:151-152, class-level OCEANOLOGY_PLUGIN_API :34) — but its only
   implementation in the loaded DLL is the stub `return FVector{};`
   (OceanologyWaterParent.cpp:70-72). It is NOT BlueprintNativeEvent, so no cooked BP can
   override it. The solver-level variant IS overridable
   (`UFUNCTION(BlueprintCallable, BlueprintNativeEvent, BlueprintPure) FVector GetWaveHeightAtLocation(const FVector& Location);`
   OceanologyWaveSolverComponent.h:62-63), but: the native Gerstner solver's math is also stubbed
   (`GerstnerWave` returns `FVector{}`, OceanologyGerstnerWaveSolverComponent.cpp:22-24; no
   `GetWaveHeightAtLocation_Implementation` override exists in that file — grep verified), live
   probes found ZERO Blueprint wave-solver assets (`find_assets nameContains=WaveSolver` → 0),
   and `WaveSolverClass` is `None` on both the game ocean CDO and the plugin BP CDO (live
   get_property, both verbatim `"value":"None"`). An endpoint would return (0,0,0) always.
   IF the SDK ever swaps in vendor binaries, this becomes a trivial tier-1 read endpoint — the
   citation above is implementation-ready. Until then: sea level = ocean actor Z (composition #2).
2. **pie_drive_vehicle / get_vehicle_telemetry — NOT VIABLE.** The reflection surface is ideal on
   paper (`setSteering` FGearVehicle.h:278-279, `setInputs`/`overrideFinalInputs`
   FGearStandardInput.h:316-317/:382-383, `getKMHSpeed` :528-529, `getRPM` FGearEngine.h:94-95 —
   all BlueprintCallable, all FGEARPLUGIN_API), but every body in the loaded DLL is a stub
   (FGearVehicle.cpp:121-122, :408-410) and the physics tick itself does not exist — an FGear
   vehicle in modkit PIE is an inert pawn. Telemetry would read hardwired zeros: worse than no
   endpoint (silent-wrong numbers). DDS2 usage is anyway 3 experimental BPs (see inventory).
   Config authoring (composition #1) is the real, working FGear value.
3. **Bridge-side Gerstner reimplementation — REJECTED.** Recomputing wave height from the
   readable `Wave_1..4`/`GlobalDisplacement` UPROPERTYs would duplicate vendor math we cannot
   read (stubs), diverge silently from the GPU displacement in the cooked materials, and violate
   the no-parallel-systems rule. If numbers are ever needed, they must come from real binaries
   (negative #1).
4. **Riverology has NO native rebuild function to bind.** `ARiverology` declares zero UFUNCTIONs
   (Riverology.h:10-25, read in full); rebuild logic is cooked BP bytecode
   (`Editor Apply Spline`, `SetupSplineMeshComponents` — live describe_class, params `[]`).
   Hence call_object_function above, not a `rebuild_river` wrapper hardcoding one class's
   function names.
5. **Riverology's native BP library is stubbed AND unexported** — `URiverology_PluginBPLibrary`
   (no export macro, Riverology_PluginBPLibrary.h:12); `CalculateSplineLength` returns zeros
   (cpp read). The cooked BP calls it during construction, so in-editor spline-mesh rebuild may
   produce zero/degenerate segments (UNVERIFIED #2 tracks the behavioral test).
6. **Oceanology water is INVISIBLE in the modkit editor** — `UOceanologyWaterMeshComponent`'s
   stub (cpp read in full) overrides nothing render-related (no CreateSceneProxy; `IsEnabled()`
   returns false), so no scene proxy is ever built. Consequence for ALL water work on this axis:
   "numbers for correctness" is the ONLY verification lane in-editor; capture_camera proofs are
   impossible until the shipped game runs the mod.
7. **Buoyancy/swimming/audio surfaces are stubs** — `UOceanBuoyancyComponent::GetCurrentWaveHeight`
   / `GetVelocityAtLocation` (OceanBuoyancyComponent.h:120-133) and the swim/drown delegate
   plumbing execute empty bodies in-editor. Also note: DDS2's shipped boats do NOT use FGear or
   (apparently) OceanBuoyancyComponent-on-FGear — they are `Character`-lane BPs
   (`OwnedVehicle_BoatLarge_C` chain, registry blobs + live describe_class).
8. **AFGearAutoDrive (AI spline driving) — stub**, same reason as #2; additionally its DDS2 usage
   is zero (no cooked asset references found beyond the class registration itself).
9. **Dedicated `get_water_surface_info` endpoint — REJECTED as redundant.** Composition #4 covers
   census + point queries with existing endpoints. The only capability lost is brush-accurate
   point-in-volume (`ENGINE_API bool EncompassesPoint(FVector Point, float SphereRadius=0.f, float* OutDistanceToPoint = 0) const;`
   Volume.h:52, viable via TActorIterator EngineUtils.h:517, zero new deps) — cited here so
   phase-2 can revive it if AABB precision proves insufficient on the swim volumes.

## UNVERIFIED

- **Instance-level wave solver on the shipped map**: the CDO-level `WaveSolverClass=None` finding
  does not rule out a per-instance `WaveSolver` component on the IslaSombra ocean actor — the
  editor world was `Untitled` during both probe windows (pie_status verbatim), so no placed-actor
  probe was possible. Re-probe after a real map load: `list_level_actors classFilter=Oceanology`
  → `get_property WaveSolver` on the instance. Does not change negative #1 (the solver math is
  stubbed regardless).
- **Whether BP_Riverology's construction rebuild produces spline meshes in the modkit editor**
  despite the stubbed native library (negative #5) — needs the spawn→rebuild→count experiment in
  the call_object_function Verify block; blocked on nothing but a mutation-allowed session.
- **Shipped-game binaries are the vendor's real implementations** — assumed (the game
  demonstrably has working ocean/rivers/driveables for players); not inspectable from this
  environment. All "mods behave correctly in the shipped game" claims inherit this assumption.
- **`OceanologyBTR_C` and `Oceanology_Infinity_ChildBTR_C`** (two further game-side ocean BPs
  found by find_assets) were not described — unknown whether they are legacy or in current use;
  queue a describe_class pass.
- **FGearSkeletalMeshComponent / FGearAnimInstance / FGearReplication / RewindReplay headers**
  censused by filename only — no endpoint ideas depended on them.
- **Christmas DLC vehicle lane** confirmed non-FGear only for `BP_ChristmasRoadVehicle_C`
  (parent OwnedVehicle_Car_C, live); the remaining `/ChristmasDlc/Blueprints/Vehicles/*` variants
  are assumed same-lane, not individually probed.

## Coverage log

- DONE: three .uplugin files read; uproject plugin block verified (lines 43/48/100); stub-vs-real
  question SETTLED with build-artifact evidence (UHT Intermediate, DLL timestamps, shared local
  BuildId); Oceanology 101-header census with 10 headers read in full + export grep; Riverology
  read COMPLETE (all 3 headers + all stub bodies); FGear 53-header census with 4 full reads +
  wheel/autodrive/spline greps; cooked AssetRegistry.bin mined for usage (offsets recorded,
  base64 name blobs decoded for RoadVeh/boat/ocean/river families); 14 live probes across
  self_audit / find_assets ×5 / describe_class ×8 / get_property ×4 / list_object_properties /
  pie_status (bridge was DOWN at sweep start — connection refused — recovered mid-sweep; all
  results above post-recovery); engine citations verified in Object.h / UnrealType.h / Script.h /
  Class.h / Volume.h / EngineUtils.h; F-axis water entry cross-referenced (no collision).
- REMAINS: the four UNVERIFIED probes above (two need a real map open, one needs a
  mutation-allowed session); OceanologyRuntimeSettings.h / preset asset classes
  (UOceanologyOceanPreset et al.) unopened — relevant only if a preset-authoring mission appears;
  per-wheel telemetry headers (FGearTire*.h) unopened — moot while negative #2 stands.
