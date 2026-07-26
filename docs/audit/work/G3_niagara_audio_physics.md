# Axis G3 — Niagara, audio, physics/Chaos, GAS
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._
_Phase-2 adversarial verification: 2026-07-26. Every cited file:line re-opened; .cpp implementations greped for modal/blocking hazards; verdicts appended per entry. Result: 15 entries — 11 CONFIRMED, 4 CORRECTED (cooked-crash mechanisms in create_niagara_system/add_niagara_emitter, private-access route in get_niagara_particle_counts, slow-task dialog + render-resource check in create_physics_asset), 0 DEMOTED; all 8 negatives re-checked, 0 overturned. Composition spot-checks: USoundFactory UCLASS(MinimalAPI) + AUDIOEDITOR_API SuppressImportDialogs (SoundFactory.h:25,:62) confirmed; UPhysicalMaterial MinimalAPI (PhysicalMaterial.h:58) confirmed; coverage-log claim about find_assets reading `class` via JStr confirmed at MifBridgeCooked.cpp:193._

## Surface inventory

**Niagara plugin state**: `D:/UE532/Engine/Plugins/FX/Niagara/Niagara.uplugin` → `"EnabledByDefault" : true`,
not disabled by the uproject → ACTIVE. Modules: NiagaraCore (Runtime, PreDefault), Niagara (Runtime),
NiagaraEditor, NiagaraEditorWidgets, NiagaraShader, NiagaraVertexFactories, NiagaraAnimNotifies.
Live-bridge confirmation: `describe_class NiagaraComponent` → ok, `/Script/Niagara.NiagaraComponent`
(module loaded in the running editor).

**DDS2 content mix (live bridge `find_assets` by class, 2026-07-26)**:
| Class | Count | Origin |
|---|---|---|
| `/Script/Niagara.NiagaraSystem` | **38** | container (cooked) — e.g. `/Game/SpecialEffects/ParticleSystems/BoatFoamTrail`, `/Game/UltraDynamicSky/Particles/Lightning_Strike` |
| `/Script/Engine.ParticleSystem` (Cascade) | **19** | container — e.g. `/Game/ParticleSystems/CarSmoke`, `TearGasSmoke` |
| `/Script/Engine.SoundCue` | **354** | container — voice-overs, shopkeepers etc. |
| `/Script/Engine.SoundWave` | **3753** | mixed |
| `/Script/MetasoundEngine.MetaSoundSource` | **185** | container — the game USES MetaSounds |
| `/Script/Engine.PhysicalMaterial` | **0** | — (game uses engine defaults; phys-mat endpoints deprioritized) |
| `/Script/GeometryCollectionEngine.GeometryCollection` | **0** | — (no Chaos destruction in game; GC endpoints tier 3) |
| `/Script/Engine.PhysicsAsset` | **163** | container — clothes/skeletal meshes |

Tier tailoring conclusion: Niagara is the game's primary FX system (2:1 over Cascade); SoundCue is the
dominant audio asset with a substantial MetaSound presence; there is no Chaos destruction content.

**Headers read (verbatim citations below come only from these)**:
- `Plugins/FX/Niagara/Source/Niagara/Public/`: NiagaraFunctionLibrary.h, NiagaraComponent.h,
  NiagaraSystemInstance.h, NiagaraSystemInstanceController.h, NiagaraTypes.h, NiagaraParameterStore.h
  (66 public headers enumerated in the directory listing)
- `Plugins/FX/Niagara/Source/Niagara/Classes/`: NiagaraSystem.h, NiagaraEmitter.h, NiagaraEmitterInstance.h
- `Plugins/FX/Niagara/Source/NiagaraEditor/Public/`: NiagaraSystemFactoryNew.h, NiagaraEmitterFactoryNew.h,
  NiagaraGraph.h, NiagaraNode.h, NiagaraEditorUtilities.h, ViewModels/Stack/NiagaraStackGraphUtilities.h
  (~60 public headers enumerated; factories counted: System/Emitter/Script/EffectType/ParameterCollection/
  ParameterDefinitions/DataChannel/VolumeCache)
- `Runtime/Engine/Classes/Sound/`: SoundCue.h, SoundNode.h, SoundNodeWavePlayer.h — **24 USoundNode*
  headers counted** in the directory
- `Editor/AudioEditor/Classes/Factories/`: 15 factory headers enumerated (SoundCueFactoryNew, SoundFactory,
  SoundAttenuationFactory, SoundClassFactory, SoundSubmixFactory, SoundConcurrencyFactory, ReverbEffect,
  AudioBus, DialogueVoice/Wave, SourceEffect, SubmixEffect, SourceBus, ReimportSound)
- `Editor/UnrealEd/Classes/Editor/EditorEngine.h` (preview-sound region 1168–1186)
- `Plugins/Runtime/Metasound/`: Metasound.uplugin (`"EnabledByDefault": true`, line 13), 7 source modules,
  MetasoundEngine/Public/MetasoundBuilderSubsystem.h (full builder surface read)
- `Runtime/PhysicsCore/Public/PhysicalMaterials/PhysicalMaterial.h`
- `Runtime/Engine/Classes/PhysicsEngine/PhysicsConstraintComponent.h`
- `Developer/PhysicsUtilities/Public/PhysicsAssetUtils.h` (located by glob after Editor/ paths came up empty)
- `Runtime/Experimental/GeometryCollectionEngine/Public/GeometryCollection/`: 26 headers enumerated;
  GeometryCollectionEngineConversion.h, GeometryCollectionObject.h read
- `Plugins/Experimental/ChaosEditor/Source/FractureEditor/Public/` (8 files), ChaosEditor.uplugin,
  `Plugins/Experimental/PlanarCutPlugin/` (PlanarCut.uplugin + Source/PlanarCut/Public/PlanarCut.h),
  `Plugins/Experimental/Fracture/Fracture.uplugin`
- `Plugins/Runtime/GameplayAbilities/GameplayAbilities.uplugin`; `D:/DDS2SDK/Game/Source/DrugDealerSimulator2/DrugDealerSimulator2.Build.cs`

**GAS verdict (one paragraph, as tasked)**: GameplayAbilities is a four-way negative for DDS2.
(1) `GameplayAbilities.uplugin:13` → `"EnabledByDefault" : false`; (2) the plugin is absent from
`DrugDealerSimulator2.uproject`'s plugin list; (3) the game module links only
`Core, CoreUObject, Engine, EnhancedInput, OnlineSubsystem, OnlineSubsystemUtils, SlateCore, UMG`
(`DrugDealerSimulator2.Build.cs`, read 2026-07-26) — no GameplayAbilities; (4) live bridge
`describe_class AbilitySystemComponent` → `{"ok":false,"error":"class not found: 'AbilitySystemComponent'"}`.
No game class can carry an AbilitySystemComponent, so GAS endpoints would exercise machinery the game
never runs. **Recommendation: no GAS endpoints. Do not enable the plugin for MifBridge's sake** — that
would be a new runtime dependency for zero content. Revisit only if a mod explicitly enables GAS.

## Proposed endpoints

### create_niagara_system
**Purpose**: create a new, immediately-usable NiagaraSystem asset (with default system scripts, optionally
seeded from an existing emitter asset) without touching the template-picker UI.
**Engine API**:
```cpp
// NiagaraSystemFactoryNew.h:29 (class UNiagaraSystemFactoryNew is NOT exported; this static IS)
NIAGARAEDITOR_API static void InitializeSystem(UNiagaraSystem* System, bool bCreateDefaultNodes);
// NiagaraEditorUtilities.h:266 — namespace FNiagaraEditorUtilities
NIAGARAEDITOR_API const FGuid AddEmitterToSystem(UNiagaraSystem& InSystem, UNiagaraEmitter& InEmitterToAdd, FGuid EmitterVersion, bool bCreateCopy = true);
// NiagaraSystem.h:403
NIAGARA_API bool RequestCompile(bool bForce, FNiagaraSystemUpdateContext* OptionalUpdateContext = nullptr);
```
`Plugins/FX/Niagara/Source/NiagaraEditor/Public/NiagaraSystemFactoryNew.h:29`,
`Plugins/FX/Niagara/Source/NiagaraEditor/Public/NiagaraEditorUtilities.h:266`,
`Plugins/FX/Niagara/Source/Niagara/Classes/NiagaraSystem.h:403`.
Route: `CreatePackage` → `NewObject<UNiagaraSystem>` (UCLASS is `MinimalAPI` — NiagaraSystem.h:210 — so
`StaticClass`/`NewObject` link fine) → `InitializeSystem(Sys, true)` → optional `AddEmitterToSystem` →
`RequestCompile(false)` → `FAssetRegistryModule::AssetCreated` + mark dirty. Avoids
`UNiagaraSystemFactoryNew::ConfigureProperties` (which opens the template dialog) entirely.
**Export**: `NIAGARAEDITOR_API` (statics), `NIAGARA_API` (RequestCompile) | **Module**: NEW deps `Niagara`
(runtime, plugin enabled-by-default) + `NiagaraEditor` (editor-only, same plugin) | **Guards**:
`AddEmitterHandle`/editor data are `#if WITH_EDITORONLY_DATA` (NiagaraSystem.h:296–300) — MifBridge is
editor-only, no extra guard needed beyond what the module already implies.
**Bucket**: self-managed — creates + registers a new UObject/package and kicks an async script compile;
must not sit inside a blanket transaction (undo of a half-compiled system = dangling compile request).
**Async**: creation itself `no`; compile completion is polled via `niagara_compile_status` (below).
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| packagePath | path | string | — | yes (strict: empty ⇒ error naming `packagePath`) |
| name | assetName | string | — | yes |
| emitterAsset | emitter | string (object path) | "" | no — if set, resolved strictly; added via AddEmitterToSystem(bCreateCopy=true) |
| createDefaultNodes | — | bool | true | no |
| compile | requestCompile | bool | true | no |
Unrecognised parameter ⇒ `error: unknown parameter '<p>' (accepted: packagePath, name, emitterAsset, createDefaultNodes, compile)`.
**Failure modes**:
- package already exists ⇒ `asset already exists at <path> — use a new name or delete_asset first`
- `emitterAsset` resolves to non-UNiagaraEmitter ⇒ `emitterAsset '<p>' is a <Class>, expected NiagaraEmitter`
- emitter has no exposed version GUID ⇒ pass `FGuid()`? NO — use `Emitter->GetExposedVersion().VersionGuid`; if unavailable ⇒ `emitterAsset '<p>' has no exposed version — asset may be cooked`
**Cooked**: creating NEW systems in `/Game/MODS/...` works (loose package). Seeding `emitterAsset` from a
cooked/container emitter is expected to FAIL (editor-only script source stripped) — error, not crash; state this in the reply.
**Verify**: `find_assets class=/Script/Niagara.NiagaraSystem path=<pkg>` returns count 1;
`get_property objectPath=<asset> path=EmitterHandles` array Num == expected;
`niagara_compile_status` reaches `outstanding=0`.
**Score**: U4 E3 R2 → tier 1 (38 Niagara systems in game; the modding story for VFX starts here)
**Phase-2 verdict**: CORRECTED — all signatures re-verified verbatim (NiagaraSystemFactoryNew.h:29, NiagaraEditorUtilities.h:266 in `namespace FNiagaraEditorUtilities` :61, NiagaraSystem.h:403, MinimalAPI :210) and modules confirmed (Niagara Runtime / NiagaraEditor Editor per Niagara.uplugin). TWO fixes: (1) the cooked-emitter claim "error, not crash" is WRONG — `FNiagaraEditorUtilities::AddEmitterToSystem` null-derefs `Cast<UNiagaraEmitterEditorData>(EmitterHandle.GetEmitterData()->GetEditorData())->SetShowSummaryView(...)` at NiagaraEditorUtilities.cpp:2130 when the emitter has no editor data, and `CastChecked<UNiagaraSystemEditorData>(InSystem.GetEditorData(), NullChecked)` at :2109 asserts when the system has none; the ENDPOINT must pre-check `GetEditorData() != nullptr` on both and refuse — the engine path crashes. (New systems are safe: `UNiagaraSystem::PostInitProperties` creates editor data, NiagaraSystem.cpp:410-418.) (2) Hidden blocking hazard: `UNiagaraSystem::PreSave` calls `WaitForCompilationComplete()` (NiagaraSystem.cpp:255) — saving the package while a compile is outstanding blocks the game thread behind an FScopedSlowTask; save_package must be gated on `niagara_compile_status` reaching `outstanding=0`.

### spawn_niagara_component
**Purpose**: spawn a one-shot/pooled Niagara FX component at a location in the editor world or the PIE
world — the runtime spawn path that `spawn_actor_in_level` (persistent NiagaraActor) does not cover.
**Engine API**:
```cpp
// NiagaraFunctionLibrary.h:42 — UFUNCTION(BlueprintCallable, ...)
static NIAGARA_API UNiagaraComponent* SpawnSystemAtLocation(const UObject* WorldContextObject, class UNiagaraSystem* SystemTemplate, FVector Location, FRotator Rotation = FRotator::ZeroRotator, FVector Scale = FVector(1.f), bool bAutoDestroy = true, bool bAutoActivate = true, ENCPoolMethod PoolingMethod = ENCPoolMethod::None, bool bPreCullCheck = true);
```
`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraFunctionLibrary.h:42`.
**Export**: `NIAGARA_API` (method-level; class is `UCLASS(MinimalAPI)` UBlueprintFunctionLibrary, NiagaraFunctionLibrary.h:24-25)
| **Module**: NEW dep `Niagara` (runtime, plugin enabled-by-default) | **Guards**: none.
**Bucket**: self-managed — spawns a transient, potentially pooled runtime component; a transaction would
put pooled-component lifecycle into the undo stack (Ctrl-Z on a pooled FX component = pool corruption).
**Async**: no (component returned same frame; particle activity is observed via `get_niagara_particle_counts`).
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| system | systemPath, asset | string | — | yes (strict resolve to UNiagaraSystem) |
| location | pos | [x,y,z] | — | yes |
| rotation | rot | [pitch,yaw,roll] | [0,0,0] | no |
| scale | — | [x,y,z] | [1,1,1] | no |
| world | target | string enum: `editor`\|`pie` | `pie` if PIE running else `editor` | no |
| autoDestroy | — | bool | true | no |
| autoActivate | — | bool | true | no |
| poolingMethod | — | string enum: `none`\|`auto_release`\|`manual_release` | `none` | no |
Returns the component path (`<World>...:<NiagaraComponent_N>`) for use as objectPath in follow-ups.
Unrecognised parameter ⇒ error naming it.
**Failure modes**:
- `world=pie` with no PIE session ⇒ `no PIE world — start_pie first or pass world=editor`
- system asset fails to load ⇒ `system '<p>' could not be loaded`
- component returns null (pre-cull, scalability cull) ⇒ `spawn was culled (bPreCullCheck) — retry with autoActivate=false or check scalability settings`; expose `bPreCullCheck=false` only if this bites.
**Cooked**: WORKS on cooked/container NiagaraSystems (runtime data is what ships) — this is the endpoint
that lets an agent exercise the game's own 38 systems.
**Verify**: returned component path + `get_niagara_particle_counts` on it > 0 within N frames during PIE;
`list_level_actors`/`list_pie_actors` shows the owning actor when `autoDestroy=false`.
**Score**: U4 E2 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — signature verbatim at NiagaraFunctionLibrary.h:42 (UCLASS line corrected 25→24, MinimalAPI; conclusion unchanged, method-level NIAGARA_API is what links). Implementation greped (NiagaraFunctionLibrary.cpp:91-160): no modal/blocking calls; returns null on missing world or pre-cull exactly as the failure modes state; component outer is the world's WorldSettings and it is registered, so no unrooted-UObject GC hazard. One residual: `FNiagaraWorldManager::Get(World)` is dereferenced without a null check on the pre-cull path (cpp:125-126) — fine for editor/PIE worlds (managers exist), just do not pass exotic preview worlds.

### set_niagara_user_parameter
**Purpose**: set a `User.*` parameter on any UNiagaraComponent (placed NiagaraActor, SCS template, or PIE
component). **This closes a real set_property gap**: user parameters live in the
`FNiagaraUserRedirectionParameterStore` (a serialized parameter blob), NOT in reflected UPROPERTYs, so the
existing dot-path property walker cannot reach them.
**Engine API** (typed dispatch, all method-level exports on MinimalAPI class UNiagaraComponent — NiagaraComponent.h:58):
```cpp
NIAGARA_API void SetVariableLinearColor(FName InVariableName, const FLinearColor& InValue); // :452
NIAGARA_API void SetVariableVec4(FName InVariableName, const FVector4& InValue);            // :461
NIAGARA_API void SetVariableQuat(FName InVariableName, const FQuat& InValue);               // :470
NIAGARA_API void SetVariableVec3(FName InVariableName, FVector InValue);                    // :488
NIAGARA_API void SetVariableVec2(FName InVariableName, FVector2D InValue);                  // :506
NIAGARA_API void SetVariableFloat(FName InVariableName, float InValue);                     // :515
NIAGARA_API void SetVariableInt(FName InVariableName, int32 InValue);                       // :524
NIAGARA_API void SetVariableBool(FName InVariableName, bool InValue);                       // :533
NIAGARA_API void SetVariableActor(FName InVariableName, AActor* Actor);                     // :540
NIAGARA_API void SetVariableObject(FName InVariableName, UObject* Object);                  // :547
NIAGARA_API void SetVariableMaterial(FName InVariableName, UMaterialInterface* Object);     // :550
NIAGARA_API void SetVariableStaticMesh(FName InVariableName, UStaticMesh* InValue);         // :553
NIAGARA_API void SetVariableTexture(FName InVariableName, class UTexture* Texture);         // :556
```
`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraComponent.h:452–556`.
**Export**: `NIAGARA_API` per method | **Module**: `Niagara` (already required by the endpoints above) |
**Guards**: none.
**Bucket**: transacted — for editor-world components the override-parameter store is serialized component
state (undo should restore the old value); PIE targets simply skip Modify (transient world).
**Async**: no.
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| objectPath | componentPath | string | — | yes (must resolve to a UNiagaraComponent; error names actual class otherwise) |
| parameter | name, variable | string | — | yes — accepts `User.Foo` or bare `Foo` (User. prefixed automatically by the redirection store) |
| type | — | string enum: `float`\|`int`\|`bool`\|`vec2`\|`vec3`\|`vec4`\|`color`\|`quat`\|`actor`\|`object`\|`material`\|`static_mesh`\|`texture` | — | yes (strict — no silent guessing) |
| value | — | number / bool / [..] / object path per type | — | yes |
Unrecognised parameter ⇒ error naming it; wrong value shape for `type` ⇒ error stating expected shape.
**Failure modes**:
- parameter name not present in the system's exposed user params ⇒ setter silently creates/ignores —
  endpoint must PRE-CHECK against `GetOverrideParameters().ReadParameterVariables` and error:
  `parameter 'User.<n>' not found on system '<sys>' — existing: [list]`
- type mismatch with the declared user param type ⇒ same pre-check, error naming both types.
**Cooked**: WORKS on cooked systems (user parameter store is runtime data).
**Verify**: `get_niagara_user_parameters` round-trip equality (numbers compared exactly for int/bool,
epsilon for float vectors).
**Score**: U5 E2 R2 → tier 1 — closes a documented capability hole in the set_property route
**Phase-2 verdict**: CONFIRMED — all 13 setter signatures re-read and verbatim at exactly the cited lines (NiagaraComponent.h:452,461,470,488,506,515,524,533,540,547,550,553,556), each method-level NIAGARA_API on the MinimalAPI class (:58). Pre-check route confirmed link-viable: `GetOverrideParameters()` inline (:671) and `ReadParameterVariables()` inline virtual (NiagaraParameterStore.h:186). Bonus for the implementer: `SetVariablePosition` (:497) and `SetVariableMatrix` (:479) also exist if the type enum ever needs `position`/`matrix`.

### get_niagara_user_parameters
**Purpose**: enumerate a component's (or a system CDO's) user parameters with types and current values —
the read half / verification pair of `set_niagara_user_parameter`.
**Engine API**:
```cpp
// NiagaraComponent.h:671 (inline — no link dependency)
FNiagaraUserRedirectionParameterStore& GetOverrideParameters() { return OverrideParameters; }
// NiagaraParameterStore.h:388,399 (FORCEINLINE_DEBUGGABLE templates — header-only)
FORCEINLINE_DEBUGGABLE void GetParameterValue(T& OutValue, const FNiagaraVariableBase& Parameter)const
FORCEINLINE_DEBUGGABLE T GetParameterValue(const FNiagaraVariableBase& Parameter)const
// NiagaraTypes.h:1209 (inline) backed by exported static data member NiagaraTypes.h:1296
static const FNiagaraTypeDefinition& GetFloatDef() { return FloatDef; }
static NIAGARA_API FNiagaraTypeDefinition FloatDef;
```
`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraComponent.h:671`,
`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraParameterStore.h:388–424`,
`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraTypes.h:1209,1296`.
**Export**: inline accessors + `NIAGARA_API` static type-def data members (verified: the inline getters
link because `FloatDef`/`Vec3Def`/… are individually exported) | **Module**: `Niagara` | **Guards**: none.
**Bucket**: read-only — pure query.
**Params**: | objectPath | componentPath/systemPath | string | — | yes | (component OR NiagaraSystem asset —
for assets, reads `GetExposedParameters()` equivalent store).
**Failure modes**: objectPath resolves to neither UNiagaraComponent nor UNiagaraSystem ⇒ error naming class.
**Cooked**: works — parameter stores ship.
**Verify**: self-verifying (it IS the verifier); values match what `set_niagara_user_parameter` wrote.
**Score**: U4 E2 R1 → tier 1
**Phase-2 verdict**: CONFIRMED — every link header-inline or exported as claimed: GetOverrideParameters inline (NiagaraComponent.h:671), GetParameterValue templates FORCEINLINE_DEBUGGABLE (NiagaraParameterStore.h:388,399) whose bodies call only inline `IndexOf`/`GetParameterData` (:431 FORCEINLINE) and exported `FindParameterOffset` (:473 NIAGARA_API); type-def getters inline (NiagaraTypes.h:1209 ff) backed by private-but-NIAGARA_API static members (:1296 ff — private access is fine, reached only through the public inline getters). The asset-side store is `UNiagaraSystem::GetExposedParameters()` — inline at NiagaraSystem.h:336-337, link-viable (citation added).

### set_niagara_component_active
**Purpose**: activate / deactivate / reset a Niagara component so an agent can drive FX state during PIE
verification without console hacks.
**Engine API**:
```cpp
NIAGARA_API virtual void Activate(bool bReset = false) override;      // NiagaraComponent.h:221
NIAGARA_API virtual void Deactivate() override;                        // NiagaraComponent.h:222
NIAGARA_API virtual void DeactivateImmediate() override;               // NiagaraComponent.h:223
NIAGARA_API void ReinitializeSystem();                                 // NiagaraComponent.h:579
```
`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraComponent.h:221–223,579`.
**Export**: `NIAGARA_API` per method | **Module**: `Niagara` | **Guards**: none.
**Bucket**: self-managed (no transaction) — activation state is transient runtime state; undo entries for
it are noise and pooled components must never enter the undo stack.
**Async**: no.
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| objectPath | componentPath | string | — | yes |
| action | — | string enum: `activate`\|`activate_reset`\|`deactivate`\|`deactivate_immediate`\|`reinitialize` | — | yes |
**Failure modes**: not a UNiagaraComponent ⇒ error naming class; unknown action ⇒ error listing the enum.
**Cooked**: works.
**Verify**: `get_niagara_particle_counts` — active-particle total transitions >0 / →0; component
`IsActive` readable via `get_property objectPath=<comp> path=bIsActive`.
**Score**: U3 E1 R1 → tier 1
**Phase-2 verdict**: CONFIRMED — Activate/Deactivate/DeactivateImmediate verbatim at NiagaraComponent.h:221-223, ReinitializeSystem at :579, all method-level NIAGARA_API. Bucket (self-managed, no transaction on transient activation state) consistent with the brief's invariants.

### get_niagara_particle_counts
**Purpose**: numeric SFX/VFX verification primitive — per-emitter live particle counts + execution state
for any active Niagara component. "Numbers for correctness": this is how every FX mutation above proves
it did something.
**Engine API** (inline chain on unexported classes + one exported fallback — verified link-viable):
```cpp
// NiagaraComponent.h:390 (inline)
FNiagaraSystemInstanceControllerPtr GetSystemInstanceController() { return SystemInstanceController; }
// NiagaraSystemInstanceController.h:71 (inline; class FNiagaraSystemInstanceController itself UNEXPORTED — see Negative results)
FNiagaraSystemInstance* GetSystemInstance_Unsafe() const { return SystemInstance.Get(); }
// NiagaraSystemInstance.h:241 (FORCEINLINE)
FORCEINLINE TArray<TSharedRef<FNiagaraEmitterInstance, ESPMode::ThreadSafe> > &GetEmitters() { return Emitters; }
// NiagaraEmitterInstance.h:98 (FORCEINLINE body reads ParticleDataSet; GPU path calls the exported :96)
FORCEINLINE int32 GetNumParticles() const
NIAGARA_API int32 GetNumParticlesGPUInternal() const;                  // NiagaraEmitterInstance.h:96
NIAGARA_API const FNiagaraEmitterHandle& GetEmitterHandle() const;     // NiagaraEmitterInstance.h:118
FORCEINLINE bool IsActive()const  { return ExecutionState == ENiagaraExecutionState::Active; }   // NiagaraEmitterInstance.h:90
FORCEINLINE int32 GetTotalSpawnedParticles()const { return TotalSpawnedParticles; }              // NiagaraEmitterInstance.h:115
```
`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraComponent.h:390`,
`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraSystemInstanceController.h:71`,
`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraSystemInstance.h:241`,
`Plugins/FX/Niagara/Source/Niagara/Classes/NiagaraEmitterInstance.h:90–118`.
**Export**: chain is inline end-to-end; the only out-of-line calls (`GetNumParticlesGPUInternal`,
`GetEmitterHandle`) are `NIAGARA_API`. Implementer note: GPU emitters report latent counts (comment at
NiagaraEmitterInstance.h:99–101) — return a `gpuLatent: true` flag per GPU emitter.
**Module**: `Niagara` | **Guards**: none.
**Bucket**: read-only — pure query.
**Async**: no.
**Params**: | objectPath | componentPath | string | — | yes |
Returns per emitter: `{ name (from GetEmitterHandle), numParticles, totalSpawned, state: Active|Inactive|Complete|Disabled, gpuLatent }` + totals.
**Failure modes**: component has no system instance (never activated) ⇒ `ok:true, emitters:[], note:"component has no active system instance — activate first"` (not an error; that IS the answer).
**Cooked**: works (pure runtime state).
**Verify**: self-verifying; pairs with spawn/activate endpoints. PIE smoke test: spawn cooked
`/Game/ParticleSystems/...` system, poll counts > 0 within 60 frames.
**Score**: U5 E2 R1 → tier 1 — the axis's verification backbone
**Phase-2 verdict**: CORRECTED — the chain was re-walked link by link and IS link-viable end-to-end, with one access-control fix: `GetNumParticlesGPUInternal()` is **private** (NiagaraEmitterInstance.h:95-96) — MifBridge cannot call it directly; it is reached only through the public FORCEINLINE `GetNumParticles()` (:98), whose inlined body legally calls the private-but-NIAGARA_API symbol. Deeper links the proposer did not cite were also verified header-inline: `FNiagaraDataSet::GetCurrentData()` FORCEINLINE (NiagaraDataSet.h:312 — note file lives in `Classes/`, not `Public/`), `FNiagaraDataBuffer::GetNumInstances()` FORCEINLINE (:135), and the emitter-name read `FNiagaraEmitterHandle::GetName()` NIAGARA_API (NiagaraEmitterHandle.h:48). Remaining links confirmed at cited lines: NiagaraComponent.h:390 inline, NiagaraSystemInstanceController.h:71 inline, NiagaraSystemInstance.h:241 FORCEINLINE, IsActive/GetTotalSpawnedParticles/GetEmitterHandle at NiagaraEmitterInstance.h:90/:115/:118. Lifetime note: holding the returned TSharedPtr is safe to destroy from MifBridge (deleter is type-erased at creation inside the Niagara module).

### niagara_compile_request / niagara_compile_status
**Purpose**: request (re)compilation of a NiagaraSystem's scripts after authoring mutations, and poll
completion — required because Niagara script compilation is multi-frame (invariant 3).
**Engine API**:
```cpp
NIAGARA_API bool RequestCompile(bool bForce, FNiagaraSystemUpdateContext* OptionalUpdateContext = nullptr); // NiagaraSystem.h:403
NIAGARA_API bool HasOutstandingCompilationRequests(bool bIncludingGPUShaders = false) const;                 // NiagaraSystem.h:391
NIAGARA_API bool PollForCompilationComplete(bool bFlushRequestCompile = true);                               // NiagaraSystem.h:406
```
`Plugins/FX/Niagara/Source/Niagara/Classes/NiagaraSystem.h:391–406`. (Do NOT use
`WaitForCompilationComplete` (:409) — it blocks the game thread, violating invariant 3.)
**Export**: `NIAGARA_API` | **Module**: `Niagara` | **Guards**: compile requests only meaningful with
editor data — `#if WITH_EDITORONLY_DATA` regions; fine in editor-only MifBridge.
**Bucket**: self-managed (request), read-only (status) — async compile must not live inside a transaction.
**Async**: THIS IS the request+poll pair. Status payload: `{ outstanding: bool, includingGpuShaders: bool, valid: bool (UNiagaraSystem::IsValid), emitterCount }`.
**Params (request)**: | system | systemPath | string | — | yes |; | force | — | bool | false | no |
**Params (status)**: | system | systemPath | string | — | yes |; | includeGpuShaders | — | bool | false | no |
**Failure modes**: cooked/container system ⇒ `RequestCompile` on stripped editor data — refuse up front:
`system '<p>' is cooked (origin=container) — cannot recompile cooked Niagara systems`.
**Cooked**: refuses (by design, with that message).
**Verify**: status flips `outstanding true→false`; system `IsValid` true after; spawn + particle counts as end-to-end proof.
**Score**: U4 E2 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — signatures verbatim (NiagaraSystem.h:391,403,406; WaitForCompilationComplete at :409 correctly banned). Implementations read: `RequestCompile` is non-blocking (queues an FNiagaraActiveCompilation; NiagaraSystem.cpp:3016 ff) and `PollForCompilationComplete` resolves to `QueryCompileComplete(false)` — a true non-blocking poll (cpp:2513-2521). Two evidence upgrades: (1) the cooked refusal can be belt-and-braces — the engine itself already returns false for cooked packages (`bIsCookedForEditor` / `PKG_FilterEditorOnly` early-outs, cpp:3030-3044), so the endpoint's up-front refusal message is UX, not crash-avoidance; (2) the PreSave hazard from create_niagara_system applies here too: never `save_package` a system while `outstanding=true` (UNiagaraSystem::PreSave blocks via WaitForCompilationComplete, cpp:255).

### add_niagara_emitter
**Purpose**: add an emitter (copied from an emitter asset) to an existing loose NiagaraSystem — the one
structural authoring operation with a clean exported entry point.
**Engine API**:
```cpp
// NiagaraEditorUtilities.h:266 — namespace FNiagaraEditorUtilities
NIAGARAEDITOR_API const FGuid AddEmitterToSystem(UNiagaraSystem& InSystem, UNiagaraEmitter& InEmitterToAdd, FGuid EmitterVersion, bool bCreateCopy = true);
// underlying: NiagaraSystem.h:300  #if WITH_EDITORONLY_DATA
NIAGARA_API FNiagaraEmitterHandle AddEmitterHandle(UNiagaraEmitter& SourceEmitter, FName EmitterName, FGuid EmitterVersion);
// removal: NiagaraSystem.h:310
NIAGARA_API void RemoveEmitterHandle(const FNiagaraEmitterHandle& EmitterHandleToDelete);
// emitter creation (if source emitter must be made first): NiagaraEmitter.h:574
NIAGARA_API static UNiagaraEmitter* CreateWithParentAndOwner(FVersionedNiagaraEmitter InParentEmitter, UObject* InOwner, FName InName, EObjectFlags FlagMask);
// or: NiagaraEmitterFactoryNew.h:26
NIAGARAEDITOR_API static void InitializeEmitter(UNiagaraEmitter* NewEmitter, bool bAddDefaultModulesAndRenderers);
```
`Plugins/FX/Niagara/Source/NiagaraEditor/Public/NiagaraEditorUtilities.h:266`,
`Plugins/FX/Niagara/Source/Niagara/Classes/NiagaraSystem.h:300,310`,
`Plugins/FX/Niagara/Source/Niagara/Classes/NiagaraEmitter.h:574`,
`Plugins/FX/Niagara/Source/NiagaraEditor/Public/NiagaraEmitterFactoryNew.h:26`.
**Export**: all shown | **Module**: `NiagaraEditor` + `Niagara` | **Guards**: WITH_EDITORONLY_DATA
(satisfied — editor module).
**Bucket**: self-managed — duplicates a UNiagaraEmitter object graph into the system and dirties compile
state; follow with `niagara_compile_request`.
**Async**: no (compile polled separately).
**Params**: | system | systemPath | string | — | yes |; | emitter | emitterAsset | string | — | yes |;
| removeEmitter | — | string (handle name, mutually exclusive with emitter) | — | no |. Version GUID taken
from the emitter's exposed version; error if absent.
**Failure modes**: cooked system or cooked emitter ⇒ refuse with origin-aware message; duplicate handle
name ⇒ auto-uniquified (report final name).
**Cooked**: refuses on container-origin system/emitter (editor data stripped).
**Verify**: `get_property objectPath=<system> path=EmitterHandles` count +1; after compile+spawn,
`get_niagara_particle_counts` shows the new emitter by name.
**Score**: U3 E3 R3 → tier 2
**Phase-2 verdict**: CORRECTED — signatures verbatim (RemoveEmitterHandle citation fixed 309→310; CreateWithParentAndOwner confirmed NIAGARA_API at NiagaraEmitter.h:574 inside `#if WITH_EDITOR`; InitializeEmitter at NiagaraEmitterFactoryNew.h:26). The load-bearing fix is the cooked-refusal MECHANISM: "refuses on container-origin" is only true if the endpoint enforces it — `AddEmitterToSystem` itself CRASHES on cooked input, it does not error. Evidence: `CastChecked<UNiagaraSystemEditorData>(InSystem.GetEditorData(), ECastCheckedType::NullChecked)` asserts on a system without editor data (NiagaraEditorUtilities.cpp:2109) and `Cast<UNiagaraEmitterEditorData>(...GetEditorData())->SetShowSummaryView(...)` null-derefs on an emitter without editor data (:2130). Endpoint MUST pre-check `GetEditorData() != nullptr` on both system and emitter and refuse with the origin-aware message. Also verified benign: AddEmitterToSystem internally calls KillSystemInstances (:2101) before mutating the handle list — safe, and another reason for the self-managed bucket.

### create_sound_cue
**Purpose**: create a SoundCue asset with an optional ready-wired wave-player chain (wave player →
[looping] → [attenuation] → root) — the 80% case for mod audio, matching the game's 354 existing cues.
**Engine API**:
```cpp
// SoundCue.h:189–199 — template, header-only; instantiable from MifBridge
template<class T>
T* ConstructSoundNode(TSubclassOf<USoundNode> SoundNodeClass = T::StaticClass(), bool bSelectNewNode = true)
// the template body calls (both exported):
ENGINE_API void SetupSoundNode(USoundNode* InSoundNode, bool bSelectNewNode = true);   // SoundCue.h:309
ENGINE_API void CreateGraph();                                                          // SoundCue.h:303
ENGINE_API void LinkGraphNodesFromSoundNodes();                                         // SoundCue.h:312
ENGINE_API void CompileSoundNodesFromGraphNodes();                                      // SoundCue.h:315
// SoundNodeWavePlayer.h:53
ENGINE_API void SetSoundWave(USoundWave* SoundWave);
// SoundNode.h:166-174
ENGINE_API virtual void CreateStartingConnectors( void );
ENGINE_API virtual void InsertChildNode( int32 Index );
ENGINE_API virtual void SetChildNodes(TArray<USoundNode*>& InChildNodes);
```
`Runtime/Engine/Classes/Sound/SoundCue.h:189-199,303,309,312,315` (all inside `#if WITH_EDITOR`,
SoundCue.h:301), `Runtime/Engine/Classes/Sound/SoundNodeWavePlayer.h:53`,
`Runtime/Engine/Classes/Sound/SoundNode.h:166-174`. Root pointer: `TObjectPtr<USoundNode> FirstNode;`
(SoundCue.h:95, public UPROPERTY — assignable directly, then `LinkGraphNodesFromSoundNodes()`).
Route: `NewObject<USoundCue>` (UCLASS MinimalAPI, SoundCue.h:89 — StaticClass/NewObject link) in a new
package → per wave: `ConstructSoundNode<USoundNodeWavePlayer>()` + `SetSoundWave` → optional wrapper
nodes → set `FirstNode` → `LinkGraphNodesFromSoundNodes()` → AssetCreated + dirty.
**Export**: ENGINE_API methods on MinimalAPI class — all verified above | **Module**: none — Engine
already linked | **Guards**: call sites inside MifBridge need no guard (editor-only module), the engine
API itself is `#if WITH_EDITOR`.
**Bucket**: self-managed — creates a package + object web; consistent with create_blueprint precedent.
**Async**: no.
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| packagePath | path | string | — | yes |
| name | assetName | string | — | yes |
| waves | wave, soundWaves | array of SoundWave object paths | [] | no |
| container | mode | string enum: `single`/`random`/`mixer` | `single` (must be `random`/`mixer` when waves>1) | no |
| looping | loop | bool | false | no |
| volume | volumeMultiplier | float | 1.0 | no |
| pitch | pitchMultiplier | float | 1.0 | no |
`random` uses `ConstructSoundNode<USoundNodeRandom>` (SoundNodeRandom.h — one of the 24 enumerated node
types), `mixer` uses USoundNodeMixer. Unrecognised parameter ⇒ error naming it.
**Failure modes**:
- wave path resolves to non-USoundWave ⇒ `waves[2] '<p>' is a <Class>, expected SoundWave`
- waves>1 with container=single ⇒ `container=single supports exactly one wave — pass container=random or mixer`
**Cooked**: creating new cues referencing cooked/container SoundWaves WORKS (waves are runtime assets;
the cue is a new loose package). This is the axis's highest-value cooked interop: 3753 shipped waves
become reusable.
**Verify**: `get_property objectPath=<cue> path=FirstNode` non-null; `list_object_properties` on the
wave player shows the wave; `play_sound_preview` + `stop` round-trip; duration
`get_property path=Duration` > 0.
**Score**: U4 E2 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — every signature verbatim at the cited lines (ConstructSoundNode template SoundCue.h:189-199; CreateGraph :303, SetupSoundNode :309, LinkGraphNodesFromSoundNodes :312, CompileSoundNodesFromGraphNodes :315, all ENGINE_API inside `#if WITH_EDITOR` :301; FirstNode :95; MinimalAPI :89; SetSoundWave SoundNodeWavePlayer.h:53; SoundNode.h:166-174). Implementation notes from SoundCue.cpp: (1) `NewObject<USoundCue>` already calls CreateGraph via PostInitProperties (SoundCue.cpp:47-52) — the explicit CreateGraph in the route is redundant but harmless (null-guarded, :754); (2) the graph methods deref the static `ISoundCueAudioEditor` TSharedPtr unchecked (:756, :781, :786, :792) — set by the AudioEditor module at startup; loaded in any full editor, but a defensive `GetSoundCueAudioEditor().IsValid()`-equivalent check (via ensuring the AudioEditor module is loaded) costs one line; (3) `SetupSoundNode` has `check(InSoundNode->GraphNode == NULL)` (:779) — never call it twice on one node (ConstructSoundNode already calls it; do not re-call).

### add_sound_cue_node
**Purpose**: extend an existing loose SoundCue's node tree (add attenuation/random/mixer/looping/wave
player under a named parent slot) — composable authoring beyond the create-time chain.
**Engine API**: same verified set as `create_sound_cue` (ConstructSoundNode template SoundCue.h:189;
SetChildNodes / InsertChildNode SoundNode.h:167-174; CompileSoundNodesFromGraphNodes SoundCue.h:315;
LinkGraphNodesFromSoundNodes SoundCue.h:312). Node classes resolved by name against the 24 `USoundNode*`
classes enumerated in `Runtime/Engine/Classes/Sound/` (SoundNodeWavePlayer, SoundNodeRandom,
SoundNodeMixer, SoundNodeLooping, SoundNodeAttenuation, SoundNodeConcatenator, SoundNodeDelay,
SoundNodeModulator, ...).
**Export**: as above | **Module**: none | **Guards**: as above.
**Bucket**: transacted — small object-graph edit on an existing asset, undo-friendly.
**Async**: no.
**Params**: | cue | cuePath | string | — | yes |; | nodeClass | class | string | — | yes (bare or full
path; strict resolve to USoundNode subclass) |; | parentNode | parent | string ("root" or node name/index
from list) | `root` | no |; | childIndex | index | int | append | no |; | wave | — | string | — | only for
WavePlayer |. Node addressing uses the index order of `USoundCue::AllNodes` (readable via
`list_object_properties`).
**Failure modes**: cooked cue ⇒ refuse (`cue '<p>' is cooked — duplicate_asset into /Game/MODS first`);
parent node index out of range ⇒ error with valid range; nodeClass not a USoundNode ⇒ error naming class.
**Cooked**: refuses on container cues (graph editor data stripped); the duplicate-first workflow is the
documented alternative.
**Verify**: `get_property path=AllNodes` count +1; parent's ChildNodes count +1; preview still plays.
**Score**: U3 E3 R2 → tier 2
**Phase-2 verdict**: CONFIRMED — relies on the same API set re-verified under create_sound_cue (all citations verbatim). The SetupSoundNode double-call check() noted there applies here too. Transacted bucket is right for an existing-asset small-graph edit.

### play_sound_preview / stop_sound_preview
**Purpose**: audition any USoundBase (cue, wave, MetaSound source) through the editor's preview audio
component — transient, no viewport, no PIE required; the audio analogue of capture_camera.
**Engine API**:
```cpp
// EditorEngine.h:1182
UNREALED_API UAudioComponent* PlayPreviewSound(USoundBase* Sound, USoundNode* SoundNode = nullptr);
// EditorEngine.h:1174 — with no args this resets (stops+clears) the preview component
UNREALED_API UAudioComponent* ResetPreviewAudioComponent(USoundBase* Sound = nullptr, USoundNode* SoundNode = nullptr);
```
`Editor/UnrealEd/Classes/Editor/EditorEngine.h:1174,1182` (UEditorEngine is `UCLASS(config=Engine,
transient, MinimalAPI)` :290 with per-method UNREALED_API — both methods verified exported). Call via
`GEditor->`.
**Export**: `UNREALED_API` | **Module**: none — UnrealEd already linked | **Guards**: none.
**Bucket**: self-managed (no transaction) — transient preview state, nothing to undo.
**Async**: no — play returns immediately; completion is polled via the status action, never by blocking.
**Params (play)**: | sound | soundPath, asset | string | — | yes (strict resolve to USoundBase — covers
SoundCue, SoundWave, MetaSoundSource) |. Returns `{ playing: true, sound, durationSeconds }`
(duration via `USoundBase::GetDuration`).
**Params (stop)**: none (unrecognised ⇒ error).
Recommend a third read-only name `sound_preview_status`: `IsPlaying()` on the preview component
(UAudioComponent is ENGINE_API) → `{ playing: bool }` so agents poll completion numerically.
**Failure modes**: asset not a USoundBase ⇒ error naming class; editor audio disabled (`-nosound`) ⇒
`preview component unavailable — editor started without audio device`.
**Cooked**: WORKS on cooked sounds — highest-leverage audition path for the 354 shipped cues +
185 MetaSounds.
**Verify**: play → status `playing:true` → stop → status `playing:false`; `durationSeconds` matches
`get_property path=Duration` within epsilon.
**Score**: U4 E1 R1 → tier 1
**Phase-2 verdict**: CONFIRMED — EditorEngine.h:1174 and :1182 verbatim, UNREALED_API per-method on the MinimalAPI UCLASS (:290). Implementation read (EditorEngine.cpp:2602-2653): no modal, no blocking; returns nullptr when `GetMainAudioDeviceRaw()` is null — exactly the `-nosound` failure mode already specified (endpoint should map a nullptr return to that error). The recommended `sound_preview_status` third name has a clean exported read: `UNREALED_API UAudioComponent* GetPreviewAudioComponent()` (EditorEngine.h:1166) + ENGINE_API UAudioComponent::IsPlaying — citation added.

### create_metasound_source
**Purpose**: create a playable MetaSoundSource asset (mono/stereo, one-shot or looping, optionally
wave-backed) via the 5.3 document-builder API — no UI. The game ships 185 MetaSoundSources, so mods that
want to match its audio pipeline need this.
**Engine API** (all on `METASOUNDENGINE_API` classes — class-level export, MetasoundBuilderSubsystem.h:129,473,486,535):
```cpp
// UMetaSoundBuilderSubsystem (UEngineSubsystem) — MetasoundBuilderSubsystem.h:568
UPARAM(DisplayName = "Source Builder") UMetaSoundSourceBuilder* CreateSourceBuilder(
    FName BuilderName,
    FMetaSoundBuilderNodeOutputHandle& OnPlayNodeOutput,
    FMetaSoundBuilderNodeInputHandle& OnFinishedNodeInput,
    TArray<FMetaSoundBuilderNodeInputHandle>& AudioOutNodeInputs,
    EMetaSoundBuilderResult& OutResult,
    EMetaSoundOutputAudioFormat OutputFormat = EMetaSoundOutputAudioFormat::Mono,
    bool bIsOneShot = true);
// UMetaSoundBuilderBase — MetasoundBuilderSubsystem.h:137,142,156,162
FMetaSoundBuilderNodeOutputHandle AddGraphInputNode(FName Name, FName DataType, FMetasoundFrontendLiteral DefaultValue, EMetaSoundBuilderResult& OutResult, bool bIsConstructorInput = false);
FMetaSoundBuilderNodeInputHandle AddGraphOutputNode(FName Name, FName DataType, FMetasoundFrontendLiteral DefaultValue, EMetaSoundBuilderResult& OutResult, bool bIsConstructorOutput = false);
FMetaSoundNodeHandle AddNodeByClassName(const FMetasoundFrontendClassName& ClassName, int32 MajorVersion, EMetaSoundBuilderResult& OutResult);
void ConnectNodes(const FMetaSoundBuilderNodeOutputHandle& NodeOutputHandle, const FMetaSoundBuilderNodeInputHandle& NodeInputHandle, EMetaSoundBuilderResult& OutResult);
// build to object — MetasoundBuilderSubsystem.h:361 (base, pure virtual) / :495 (source override)
virtual TScriptInterface<IMetaSoundDocumentInterface> Build(UObject* Parent, const FMetaSoundBuilderOptions& Options) const override;
```
`Plugins/Runtime/Metasound/Source/MetasoundEngine/Public/MetasoundBuilderSubsystem.h:129-568`.
Plugin `Metasound.uplugin:13` → `"EnabledByDefault": true` — ACTIVE (and 185 game assets prove the
runtime is exercised).
**Export**: `METASOUNDENGINE_API` class-level | **Module**: NEW dep `MetasoundEngine` (runtime module,
enabled-by-default plugin); likely `MetasoundFrontend` too for `FMetasoundFrontendClassName`/literal types |
**Guards**: none.
**Bucket**: self-managed — builder subsystem owns transient builder objects; final `Build()` creates the
asset object; no blanket transaction.
**Async**: no (document build is synchronous; audition via play_sound_preview).
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| packagePath | path | string | — | yes |
| name | assetName | string | — | yes |
| format | outputFormat | string enum: `mono`/`stereo` | `mono` | no |
| oneShot | — | bool | true | no |
| wave | soundWave | string (SoundWave path) | — | no — if set, adds the engine Wave Player node (AddNodeByClassName with the UE-namespace wave-player class) wired OnPlay→Play, Out Mono→graph audio out, wave asset set as node input literal |
**Failure modes**: every builder call reports `EMetaSoundBuilderResult` — endpoint maps `Failed` to an
error naming the failed step (`AddNodeByClassName('Wave Player') failed — node class not registered`);
duplicate package ⇒ standard exists-error.
**Cooked**: new asset creation works; referencing cooked waves works (same rationale as create_sound_cue).
**Verify**: asset exists in registry with class MetaSoundSource; `play_sound_preview` plays it
(`playing:true`); `get_property path=Duration` sanity; node count via document inspection if exposed.
**Score**: U4 E4 R3 → tier 2 — the builder surface is large and handle-based; this endpoint sticks to the
one-shot wave-source recipe and leaves general MetaSound graph authoring to a phase-2 design (see
Negative results / UNVERIFIED for the asset-save flow caveat).
**Phase-2 verdict**: CONFIRMED — all citations verbatim: CreateSourceBuilder signature exact at MetasoundBuilderSubsystem.h:568-575; AddGraphInputNode :137, AddGraphOutputNode :142, AddNodeByClassName :156, ConnectNodes :162; Build pure-virtual base :361 and source override :495; class-level METASOUNDENGINE_API at :129/:473/:486/:535. Plugin state re-verified: Metasound.uplugin:13 `"EnabledByDefault": true`; module types from the uplugin: MetasoundEngine and MetasoundFrontend are both Runtime/PreDefault (module claim correct). The UNVERIFIED asset-save-flow caveat is properly scoped and stays.

### set_physics_constraint
**Purpose**: wire a physics constraint component to its two bodies with correct initialization order —
the one constraint operation where raw `set_property` on `ComponentName1/2` is NOT equivalent, because
`SetConstrainedComponents` re-initializes the constraint instance against live bodies.
**Engine API**:
```cpp
// PhysicsConstraintComponent.h:112
ENGINE_API void SetConstrainedComponents(UPrimitiveComponent* Component1, FName BoneName1, UPrimitiveComponent* Component2, FName BoneName2);
```
`Runtime/Engine/Classes/PhysicsEngine/PhysicsConstraintComponent.h:112` (UPhysicsConstraintComponent is
`UCLASS(... MinimalAPI)` :18 with per-method ENGINE_API — verified).
**Export**: `ENGINE_API` | **Module**: none — Engine linked | **Guards**: none.
**Bucket**: transacted — component state on a placed actor; undo restores prior constraint targets.
**Async**: no.
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| constraint | objectPath, actorPath | string | — | yes (APhysicsConstraintActor label/path or component objectPath; if actor given, uses its constraint component) |
| component1 | actor1 | string | — | yes (actor label/path or component objectPath; actor ⇒ root primitive component) |
| bone1 | — | string | "" | no |
| component2 | actor2 | string | — | yes |
| bone2 | — | string | "" | no |
**Failure modes**: target not primitive component ⇒ `component1 '<p>' resolves to <Class>, expected a
PrimitiveComponent (pass a component objectPath or an actor with a primitive root)`; bone name not found
on skeletal body ⇒ error listing nearest bone names; constraint component missing ⇒ error naming actor.
**Cooked**: works — constraints operate on live components regardless of asset origin.
**Verify**: `get_property objectPath=<constraint> path=ConstraintInstance.ConstraintBone1` etc. reflect
inputs; during PIE, positions of the constrained pair over N frames show coupled motion (numeric).
**Score**: U3 E2 R2 → tier 2 (constraint-follow-up property tuning is already covered by set_property —
see Compositions)
**Phase-2 verdict**: CONFIRMED — SetConstrainedComponents verbatim at PhysicsConstraintComponent.h:112, ENGINE_API method-level on the MinimalAPI UCLASS (:18-19); also BlueprintCallable (reflection route available as fallback). The read-back pair `GetConstrainedComponents` (:116, ENGINE_API) exists for the verification step — citation added.

### create_physics_asset
**Purpose**: generate a PhysicsAsset (bodies + constraints) from a skeletal mesh — the from-scratch
ragdoll/collision setup step; 163 PhysicsAssets in the game prove the asset class matters here.
**Engine API**:
```cpp
// PhysicsAssetUtils.h:136 — namespace FPhysicsAssetUtils
PHYSICSUTILITIES_API bool CreateFromSkeletalMesh(UPhysicsAsset* PhysicsAsset, USkeletalMesh* SkelMesh, const FPhysAssetCreateParams& Params, FText& OutErrorMessage, bool bSetToMesh = true);
// companions (same namespace/file):
PHYSICSUTILITIES_API int32 CreateNewBody(UPhysicsAsset* PhysAsset, FName InBodyName, const FPhysAssetCreateParams& Params);        // :209
PHYSICSUTILITIES_API int32 CreateNewConstraint(UPhysicsAsset* PhysAsset, FName InConstraintName, UPhysicsConstraintTemplate* InConstraintSetup = NULL); // :192
```
`Developer/PhysicsUtilities/Public/PhysicsAssetUtils.h:136,192,209`. Defaults from
`FPhysAssetCreateParams` (:38, ctor :42-58): `MinBoneSize=20.0`, `GeomType=EFG_Sphyl`,
`VertWeight=EVW_DominantWeight`, `bAutoOrientToBone=true`, `bCreateConstraints=true`,
`bWalkPastSmall=true`, `bBodyForAll=false`, `bDisableCollisionsByDefault=true`,
`AngularConstraintMode=ACM_Limited`, `HullCount=4`, `MaxHullVerts=16`.
**Export**: `PHYSICSUTILITIES_API` (namespace free functions) | **Module**: NEW dep `PhysicsUtilities`
(Developer module — editor-safe, never a runtime leak) | **Guards**: none at call site.
**Bucket**: self-managed — creates a package + a body/constraint object web; body generation can be
non-trivially expensive on dense meshes (keep out of blanket transaction).
**Async**: no for typical meshes; if generation on the game's densest meshes proves slow, split into
request/status — flagged for the implementer to measure, not assumed.
**Params**: | name | aliases | type | default | required |
|---|---|---|---|---|
| skeletalMesh | mesh | string | — | yes |
| packagePath | path | string | — | yes |
| name | assetName | string | — | yes |
| minBoneSize | — | float | 20.0 | no |
| geomType | primitiveType | string enum: `sphyl`/`sphere`/`box`/`single_convex`/`multi_convex`/`level_set` | `sphyl` | no |
| createConstraints | — | bool | true | no |
| bodyForAll | — | bool | false | no |
| setToMesh | assign | bool | true | no (writes PhysicsAsset ref back to the mesh) |
**Failure modes**: `CreateFromSkeletalMesh` returns false ⇒ surface `OutErrorMessage` verbatim plus the
mesh path; skeletal mesh unresolved ⇒ strict-resolution error; existing asset ⇒ exists-error.
**Cooked**: UNVERIFIED behavior on container skeletal meshes — body fitting reads vertex/skin data which
cooked meshes retain in render data, but the utility may want editor-only mesh description. The endpoint
must report a clean error rather than crash; test on `/Game/SkeletalMeshes/Clothes/...` first (see
UNVERIFIED).
**Verify**: returned `{ bodies: PhysicsAsset->SkeletalBodySetups.Num(), constraints:
ConstraintSetup.Num() }` both > 0; `get_property` on the mesh's PhysicsAsset ref equals new asset when
`setToMesh`. (Axis E owns per-body editing; this entry deliberately stops at whole-asset generation +
counts.)
**Score**: U4 E3 R2 → tier 2
**Phase-2 verdict**: CORRECTED — signatures and every FPhysAssetCreateParams default re-verified verbatim (PhysicsAssetUtils.h:136/:192/:209, ctor :42-58, `namespace FPhysicsAssetUtils` :123, PHYSICSUTILITIES_API; Developer/PhysicsUtilities/PhysicsUtilities.Build.cs confirms a Developer module). Three findings Phase 1 missed, from PhysicsAssetUtils.cpp: (1) HIDDEN UI HAZARD — body generation opens an `FScopedSlowTask` and calls `SlowTask.MakeDialog()` when on the game thread (cpp:343-346 and :903-906): a progress dialog that pumps Slate mid-HTTP-request. Not modal-input-blocking, but Slate re-entrancy inside a bridge handler; implementer should suppress (unattended-script guard) or accept the dialog flash and document it. (2) CRASH GUARD — `check(SkelMesh->GetResourceForRendering())` at cpp:500-501: endpoint must pre-verify the mesh has render resources or the check() fires. (3) COOKED INTEL — the utility reads RENDER data via FSkinnedBoneTriangleCache (cpp:503), NOT FMeshDescription, so the UNVERIFIED cooked-mesh question hinges on CPU-accessible vertex buffers, not editor mesh data; the one live test is still required before promising cooked behavior. CROSS-AXIS: also proposed by axis E — dedup at merge; this entry's whole-asset-generation scope is the cleaner split.

### create_geometry_collection
**Purpose**: build a GeometryCollection asset from one or more static meshes — the entry asset for Chaos
destruction. Proposed honestly at tier 3: the game ships ZERO GeometryCollections, and without fracture
(see Negative results) the collection holds only unfractured root pieces.
**Engine API**:
```cpp
// GeometryCollectionObject.h:365 — UCLASS(BlueprintType, customconstructor, MinimalAPI)
GEOMETRYCOLLECTIONENGINE_API UGeometryCollection(const FObjectInitializer& ObjectInitializer = FObjectInitializer::Get());
// GeometryCollectionObject.h:427
GEOMETRYCOLLECTIONENGINE_API void InvalidateCollection();
// GeometryCollectionEngineConversion.h:74 (overloads :91,:100) — class FGeometryCollectionEngineConversion
static GEOMETRYCOLLECTIONENGINE_API bool AppendStaticMesh(const UStaticMesh* StaticMesh, const TArray<UMaterialInterface*>& Materials, const FTransform& StaticMeshTransform, ...);
// GeometryCollectionEngineConversion.h:35
static GEOMETRYCOLLECTIONENGINE_API int32 AppendMaterials(const TArray<UMaterialInterface*>& Materials, UGeometryCollection* GeometryCollectionObject, bool bAddInteriorCopy);
```
`Runtime/Experimental/GeometryCollectionEngine/Public/GeometryCollection/GeometryCollectionObject.h:359-365,427`,
`Runtime/Experimental/GeometryCollectionEngine/Public/GeometryCollection/GeometryCollectionEngineConversion.h:35,74,91,100`.
**Export**: `GEOMETRYCOLLECTIONENGINE_API` (exported ctor on customconstructor MinimalAPI class — NewObject
works) | **Module**: NEW dep `GeometryCollectionEngine` (base Runtime/Experimental engine module — always
compiled, no plugin gate) | **Guards**: none for creation; mesh-description reads want loose meshes.
**Bucket**: self-managed — package + geometry copy.
**Async**: no.
**Params**: | staticMeshes | meshes | array of paths | — | yes |; | packagePath | — | string | — | yes |;
| name | — | string | — | yes |; | transforms | — | array of [loc,rot,scale] | identity per mesh | no |.
**Failure modes**: mesh without MeshDescription (cooked) ⇒
`GetMaxResMeshDescriptionWithNormalsAndTangents` (GeometryCollectionEngineConversion.h:63) returns null ⇒
error `staticMeshes[0] '<p>' has no editor mesh data (cooked) — use a loose mesh`; empty mesh array ⇒
strict param error.
**Cooked**: refuses on container meshes (needs FMeshDescription — editor-only geometry).
**Verify**: `get_property objectPath=<gc> path=GeometrySource` count == meshes passed; spawn via
`spawn_actor_in_level` (GeometryCollectionActor) + `get_actor_bounds` non-zero.
**Score**: U2 E3 R3 → tier 3 — unlockable later by enabling PlanarCut/Fracture plugins (flagged cost)
**Phase-2 verdict**: CONFIRMED — all citations verbatim: UCLASS(BlueprintType, customconstructor, MinimalAPI) at GeometryCollectionObject.h:359 with GEOMETRYCOLLECTIONENGINE_API ctor :365, InvalidateCollection :427; conversion statics AppendMaterials :35, GetMaxResMeshDescriptionWithNormalsAndTangents :63, AppendStaticMesh overloads :74/:91/:100, all GEOMETRYCOLLECTIONENGINE_API. GeometryCollectionEngine.Build.cs exists under Runtime/Experimental (base engine module, no plugin gate) as claimed. Tier-3 honesty (zero game content, no fracture) is the right call.

## Compositions (no new endpoint needed)

- **Physical material creation/editing**: `UPhysicalMaterial` is `UCLASS(... MinimalAPI)`
  (`Runtime/PhysicsCore/Public/PhysicalMaterials/PhysicalMaterial.h:58`) — NewObject links; friction/
  restitution/density are plain UPROPERTYs ⇒ axis-B generic asset creation + existing `set_property`.
  Live check found **0** PhysicalMaterial assets in DDS2 — no dedicated endpoint is warranted.
- **Persistent editor-world Niagara placement**: `spawn_actor_in_level` with class `NiagaraActor`
  (`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraActor.h`) + `set_property` on the component's `Asset`
  property (UNiagaraComponent::PostEditChangeProperty handles the reset; the dedicated
  `NIAGARA_API void SetAsset(UNiagaraSystem*, bool)` — NiagaraComponent.h:287 — is the belt-and-braces
  route if set_property proves lossy; fold it into spawn_niagara_component as an `asset`-on-existing-
  component action rather than a new endpoint).
- **Field system actors** (Chaos fields): `spawn_actor_in_level` with the FieldSystemActor class +
  `set_property` — generic actor machinery suffices; no Chaos-specific init order.
- **WAV import**: axis B owns file import. The audio-specific detail axis B needs:
  `AUDIOEDITOR_API void SuppressImportDialogs();` on USoundFactory
  (`Editor/AudioEditor/Classes/Factories/SoundFactory.h:62`; class itself `UCLASS(MinimalAPI ...)` :25 —
  instantiate via NewObject, then call the exported suppressor before import) — otherwise WAV import can
  modal-block the bridge thread.
- **Attenuation / SoundClass / SoundSubmix / SoundConcurrency / ReverbEffect assets**: simple data
  assets; 15 factories enumerated in `Editor/AudioEditor/Classes/Factories/` are MinimalAPI but these
  asset classes need no factory — axis-B generic create + `set_property` covers them. Assigning
  attenuation to a cue = `set_property path=AttenuationSettings`.
- **Constraint tuning** (limits, motors, breakable thresholds): after `set_physics_constraint`, all of
  `ConstraintInstance.*` (ProfileInstance sub-struct) is reflected ⇒ existing `set_property` dot-path.
- **Sound-cue node parameter edits** (volume/pitch on a wave player, delay min/max, ...): reflected
  UPROPERTYs on node subobjects ⇒ `set_property` with the node objectPath from `AllNodes`.
- **Cascade (legacy) spawn for the 19 shipped ParticleSystems**: `spawn_actor_in_level` with class
  `Emitter` + `set_property` on its ParticleSystemComponent `Template` — no authoring endpoints for a
  deprecated system.
- **Niagara scalability / quality experiments**: `fx.*` CVars via existing `run_console_captured`.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

1. **GAS: four-way negative** (full evidence in Surface inventory). `GameplayAbilities.uplugin:13`
   `"EnabledByDefault" : false`; absent from uproject; absent from
   `DrugDealerSimulator2.Build.cs` dependency list; live `describe_class AbilitySystemComponent` →
   `class not found`. Zero endpoints proposed; enabling the plugin for the bridge would add a runtime
   dependency the shipped game never loads.
   **Phase-2: STANDS** — three static legs independently re-read 2026-07-26: uplugin:13 `false`; no
   GameplayAbilities string anywhere in DrugDealerSimulator2.uproject; Build.cs public deps are exactly
   the listed eight. Negative is safe.
2. **FNiagaraSystemInstanceController is a fully unexported class**
   (`Plugins/FX/Niagara/Source/Niagara/Public/NiagaraSystemInstanceController.h:44` — bare `class`, no
   NIAGARA_API). None of its out-of-line methods are callable from MifBridge. Worked around in
   `get_niagara_particle_counts` via the all-inline accessor chain (documented there); any future endpoint
   needing controller methods that are NOT inline (e.g. `Initialize`, `Release`) is blocked.
   **Phase-2: STANDS** — re-read :44-59: bare `class FNiagaraSystemInstanceController`, Initialize/Release
   out-of-line and unexported as claimed. (The inline `~FNiagaraSystemInstanceController() { Release(); }`
   :53 is NOT a link trap for MifBridge: TSharedPtr type-erases the deleter at construction inside the
   Niagara module.)
3. **FNiagaraSystemInstance likewise unexported** (`NiagaraSystemInstance.h:69`), and its aggregate
   `GetNumParticles(EmitterIndex)` is commented out (`NiagaraSystemInstance.h:225`) — per-emitter
   summation via FNiagaraEmitterInstance is the only path.
   **Phase-2: STANDS** — both re-read verbatim: bare `class FNiagaraSystemInstance` :69, commented-out
   aggregate at :225.
4. **Niagara GRAPH authoring is UI-locked at the raw-node level**: `UNiagaraGraph` is `UCLASS(MinimalAPI)`
   (`NiagaraEditor/Public/NiagaraGraph.h:178`) and `UNiagaraNode` likewise (`NiagaraNode.h:27`) — only
   selected methods carry NIAGARAEDITOR_API; there is no exported AddNode path. The REAL authoring model
   (module stack) is partially exported: `FNiagaraStackGraphUtilities::AddScriptModuleToStack` — ONLY the
   `UNiagaraScript*` overload (`ViewModels/Stack/NiagaraStackGraphUtilities.h:280` NIAGARAEDITOR_API); the
   FAssetData overloads (:277-278) and ALL `RemoveModuleFromStack` overloads (:229-235) are UNEXPORTED.
   Module addition without removal is a one-way street ⇒ module-stack authoring deferred to phase 2 as a
   designed feature (needs: output-node lookup via `UNiagaraGraph::FindEquivalentOutputNode`
   (NiagaraGraph.h:211, exported), input-setting via `SetDynamicInputForFunctionInput`
   (NiagaraStackGraphUtilities.h:225, exported), and a removal story). Verdict: tier 3 / not proposed this
   phase; the value endpoints are asset-create + parameter-set + spawn + counts, all above.
   **Phase-2: STANDS** — export map re-verified line by line: UNiagaraGraph UCLASS(MinimalAPI) :178,
   UNiagaraNode UCLASS(MinimalAPI) :27, FindEquivalentOutputNode NIAGARAEDITOR_API :211,
   SetDynamicInputForFunctionInput NIAGARAEDITOR_API :225, all four RemoveModuleFromStack overloads
   unexported :229-235, AddScriptModuleToStack unexported at :276 (args-struct) and :278 (FAssetData)
   [proposer cited 277-278 — struct overload is :276], NIAGARAEDITOR_API UNiagaraScript* overload :280.
5. **Chaos fracturing is plugin-gated AND UI-locked**: fracture algorithms live in
   `Plugins/Experimental/PlanarCutPlugin` — `PlanarCut.uplugin:13` `"EnabledByDefault" : false` — and
   `Plugins/Experimental/Fracture/Fracture.uplugin:16` `"EnabledByDefault": false` (both WOULD REQUIRE
   ENABLING); the editor-side tools (`UFractureTool*`) are in FractureEditor's Private folder with only
   settings/toolkit classes exported (`FractureEditorMode.h:140`, `FractureEditorModeToolkit.h:100`,
   `FractureModeSettings.h:33` — the only three `class FRACTUREEDITOR_API` hits in Public). The exported
   PlanarCut functions themselves (`CutWithPlanarCells` PlanarCut.h:207, `CutMultipleWithPlanarCells`
   :261, `SplitIslands` :284) would be callable IF the plugin were enabled — recorded as a future unlock,
   not proposed now (game has zero destruction content).
   **Phase-2: STANDS** — re-verified: PlanarCut.uplugin:13 false, Fracture.uplugin:16 false;
   PLANARCUT_API present on :207/:261/:284 exactly; grep of FractureEditor/Public confirms precisely the
   three cited FRACTUREEDITOR_API classes (FractureEditorMode.h:140, FractureEditorModeToolkit.h:100,
   FractureModeSettings.h:33) and nothing else.
6. **`UNiagaraSystem::WaitForCompilationComplete`** (NiagaraSystem.h:409) is exported but MUST NOT be
   used — synchronous multi-frame wait on the game thread (invariant 3). The request/poll pair above is
   the only compliant shape.
   **Phase-2: STANDS** — :409 verbatim; implementation confirms an FScopedSlowTask spin
   (NiagaraSystem.cpp:2429-2461). ADDITION: this blocker is also reachable IMPLICITLY —
   `UNiagaraSystem::PreSave` calls it (cpp:255), so saving a system mid-compile blocks; gate saves on the
   poll endpoint.
7. **Cooked Niagara/SoundCue graph editing**: container-origin NiagaraSystems (38) and SoundCues (354)
   have editor-only source stripped; `RequestCompile`/node edits must refuse. Spawn, user-parameter set,
   preview, and particle-count reads all still work on cooked content — endpoints above state per-entry
   behavior.
   **Phase-2: STANDS, with one refinement** — `RequestCompile` self-refuses on cooked packages (returns
   false: `bIsCookedForEditor` / `PKG_FilterEditorOnly` early-outs, NiagaraSystem.cpp:3030-3044), but
   emitter-list edits do NOT self-refuse — they crash (CastChecked/null-deref, see add_niagara_emitter
   verdict). "Must refuse" therefore means endpoint-enforced pre-checks, not engine grace.
8. **MetaSound full graph authoring** — viable (builder API is exported + BlueprintCallable end to end)
   but deep: handle-based (FMetaSoundNodeHandle etc.), node-class discovery needs the frontend registry,
   and 5.3 lacks the later `BuildToAsset` convenience (only `Build(UObject* Parent, ...)` —
   MetasoundBuilderSubsystem.h:361/:495). One recipe endpoint proposed; a general metasound_* endpoint
   family is a phase-2 design item, not an engine-surface gap.
   **Phase-2: STANDS** — grep confirms no `BuildToAsset` symbol anywhere in the 5.3.2
   MetasoundBuilderSubsystem.h; :361/:495 verbatim.

## UNVERIFIED

- `FPhysicsAssetUtils::CreateFromSkeletalMesh` on container-origin skeletal meshes — does body fitting
  fall back to render data or fail? Needs one live test against a cooked mesh before the endpoint ships
  a cooked-behavior promise. (Signature itself verified.)
- MetaSound `Build()` package/save flow: whether the built UMetaSoundSource needs registration beyond
  AssetCreated + SavePackage (document version stamp, frontend registry) — prototype required; builder
  signatures verified but MetasoundEditor's factory save path was not traced.
- Exact FMetasoundFrontendClassName for the engine Wave Player node (namespace/name/variant strings) —
  must be read from MetasoundStandardNodes/MetasoundEngine sources during implementation; not cited here
  because I did not open those files.
- Whether `USoundFactory::SuppressImportDialogs` covers OGG/FLAC paths as well as WAV (only the header
  region was read, not the cpp).
- `UNiagaraComponent::SetVariable*` on SCS component templates (`_GEN_VARIABLE` objects) — the setters
  target the override store which exists on templates, but template-to-instance propagation was not
  traced; the endpoint's editor-world story is verified only for placed actors and PIE components.

## Coverage log

- DONE: Niagara plugin state + module list; runtime spawn/param/activate/particle-read surface
  (NiagaraFunctionLibrary.h, NiagaraComponent.h, NiagaraSystem.h, NiagaraEmitter.h,
  NiagaraEmitterInstance.h, NiagaraSystemInstance(Controller).h, NiagaraTypes.h, NiagaraParameterStore.h);
  editor factories + InitializeSystem/InitializeEmitter; AddEmitterToSystem; stack-utilities export map;
  graph-authoring export audit (negative); compile request/poll API.
- DONE: SoundCue authoring surface (SoundCue.h WITH_EDITOR block fully read; SoundNode.h; WavePlayer);
  24 sound-node headers counted; AudioEditor factory inventory (15); editor preview sound
  (EditorEngine.h:1168-1186); Metasound plugin state + builder subsystem surface.
- DONE: physical material export check; constraint SetConstrainedComponents; PhysicsAssetUtils (located
  in Developer/PhysicsUtilities, NOT Editor/UnrealEd — glob confirmed); FPhysAssetCreateParams defaults;
  GeometryCollection creation path + conversion exports; fracture plugin gating (negative).
- DONE: GAS four-way negative (uplugin, uproject, Build.cs, live describe_class).
- DONE: live-bridge content census (NiagaraSystem 38 / ParticleSystem 19 / SoundCue 354 / SoundWave 3753 /
  MetaSoundSource 185 / PhysicalMaterial 0 / GeometryCollection 0 / PhysicsAsset 163). NOTE for other
  agents: the bridge dropped connections intermittently mid-session (curl exit 7 / HTTP 000 for ~2
  minutes, then recovered); also `find_assets` SILENTLY IGNORES an unknown `className`/`query` parameter
  and returns the full 37k-asset registry — the correct parameter is `class`
  (MifBridgeCooked.cpp:193 `JStr(In, TEXT("class"))`). That silent-ignore is itself an instance of the
  #1 bug class the brief warns about, on a shipped read endpoint.
- NOT COVERED (left for other axes / phase 2): submix/audio-bus routing endpoints (no game content
  signal); Niagara data channels + SimCache; Cascade authoring (deliberate skip, legacy); ChaosVehicles
  (FGearPlugin is the game's vehicle sim, third-party — separate audit if modding needs it);
  AnimNotify-driven FX (axis E territory); MetaSound patch (non-source) assets.
