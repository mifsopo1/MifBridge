# 03 — Gaps and risks (negative results, dead ends, hazards)

_Assembled 2026-07-26 from the 12 Phase-1/Phase-2 axis files in [work/](work/), the live probes in
[work/LIVE_PROBES.md](work/LIVE_PROBES.md), and the registry baseline (00_BASELINE.md / PROGRESS.md).
Engine: D:/UE532 (5.3.2 fork "CookedEditorModKit"). Audience: implementation sessions._

**This file exists so nobody re-treads a dead end.** Every entry below was verified against engine
source (file:line relative to `D:/UE532/Engine/Source` unless otherwise rooted) or against the live
bridge, and survived a Phase-2 adversarial re-verification. If an idea you're about to pursue appears
in §1–§5, read the cited axis entry before writing any code. §6 lists the three negatives that were
**overturned** — do not re-kill those.

---

## 1. Not viable — unexported / private / UI-locked engine APIs

These symbols cannot be linked, called, or driven headlessly from MifBridge in 5.3.2. Where an
exported alternative was found, it is named — use that instead.

### Editor core ([work/A_editor_core.md](work/A_editor_core.md))

| Dead end | Evidence | Exported alternative |
|---|---|---|
| `UEditorEngine::Map_Check` is a **private** member — `GEditor->Map_Check(...)` does not compile despite its UNREALED_API | private section opens EditorEngine.h:2544, Map_Check at :2569 | Public exec dispatcher: `GEditor->Exec(World, TEXT("MAP CHECK DONTDISPLAYDIALOG"))` (EditorServer.cpp:6264–6280) + read the "MapCheck" message log (FMessageLog at EditorServer.cpp:3957) |
| `ULightEditorSubsystem` — declared in a **Private** header, no export macro | Editor/LevelEditor/Private/LightEditorSubsystem.h:41 | Light workflows stay on `set_property` |
| `UFoliageEditorSubsystem` — no export macro, zero UFUNCTION/exported members | Editor/FoliageEdit/Public/FoliageEditorSubsystem.h (grep 0) | Exported `FFoliageInfo`/`AInstancedFoliageActor` FOLIAGE_API route (axis F paint_foliage) |
| `UTransactor::Undo/Redo` — no export macro; base-class `SelectActor/SelectNone/SelectComponent` are empty inline stubs | Transactor.h:635/:642; EditorEngine.h:1443–1464 | `UEditorEngine::UndoTransaction/RedoTransaction` (EditorEngine.h:934–935, UNREALED_API); selection via virtual dispatch through `GEditor` (real overrides on UUnrealEdEngine, UnrealEdEngine.h:174–179) |
| `FEditorFileUtils` class itself unexported (statics are method-exported); `UEditorLoadingAndSavingUtils::SaveDirtyPackages` is **not checkout-free** | FileHelpers.h:183; FileHelpers.cpp:5551–5555 → InternalCheckoutAndSavePackages | `FEditorFileUtils::SaveDirtyPackages(..., bFastSave=true)` (FileHelpers.h:383) — but see §2 for its modal trap |
| No `GetActiveModes()` enumeration for legacy FEdModes in 5.3.2 | EditorModeManager.h has only GetActiveMode(:147) + ForEachEdMode(:547) | Probe well-known mode IDs + ForEachEdMode for UEdMode-based modes |

### Blueprints / graphs ([work/C_blueprints_graphs.md](work/C_blueprints_graphs.md))

| Dead end | Evidence | Exported alternative |
|---|---|---|
| **`USubobjectDataSubsystem::ReparentSubobjects` is UI-locked in Blueprint contexts** — hard-requires an SCS-editor preview actor a headless bridge doesn't have | `if (Params.BlueprintContext) { if (!Params.ActorPreviewContext) { ... return false; } }` — SubobjectDataSubsystem.cpp:1818–1824 | Direct ENGINE_API SCS calls (`USCS_Node::AddChildNode/RemoveChildNode/SetParent`, SCS_Node.h:126/:129/:186/:189; `USimpleConstructionScript::AddNode/RemoveNode/FindSCSNode`, SimpleConstructionScript.h:91/:98/:107) — the same calls the subsystem makes underneath (cpp:2046–2072) |
| **`FBlueprintEditor::CollapseNodes*` protected** — collapse-to-function/macro impossible (documented roadmap IMPOSSIBLE; also node copy/paste = deliberate non-goal) | docs/06_CAPABILITY_ROADMAP.md (carried in _BRIEF) | None — guard, don't support |
| `UK2Node_CreateDelegate::IsValid` unexported (MinimalAPI class, neighbours :62–77 exported) | K2Node_CreateDelegate.h:59 | Validate via exported `GetDelegateSignature() != nullptr` (:64) |
| `UK2Node_Select::SetEnum` unexported | K2Node_Select.h:139 | Reflection init (`init: {Enum: <path>}`) + `ReconstructNode()` |
| **No engine API moves a function/graph between Blueprints** — editor does it via copy/paste (non-goal) | only MoveGraphBeforeOtherGraph (intra-BP ordering), BlueprintEditorUtils.h:1215 | None — dead end, do not attempt |
| Function terminator spawning (`K2Node_FunctionEntry/Result/Terminator`) via generic node spawn — PM-004 crash class | docs/01_POSTMORTEMS.md PM-004; guard at MifBridgeCommon.cpp:1315–1327 | `create_function` / `AddFunctionGraph` engine paths only; hard denylist in add_node_by_class |
| `FKismetBytecodeDisassembler` is NOT in Editor/KismetCompiler | lives in Developer/ScriptDisassembler/Public/ScriptDisassembler.h (:37–:52 SCRIPTDISASSEMBLER_API) | Usable — but it is a NEW Build.cs module dep (`ScriptDisassembler`, Core+CoreUObject only) |

### AI / navigation ([work/G1_ai_navigation.md](work/G1_ai_navigation.md))

| Dead end | Evidence | Exported alternative |
|---|---|---|
| **BehaviorTree GRAPH authoring UI-locked** — `UBehaviorTreeGraphNode` has no export macro; `CreateBTFromGraph` takes the unexported node type; the flow lives in the FBehaviorTreeEditor Slate toolkit | BehaviorTreeGraphNode.h:23–24; BehaviorTreeGraph.h:52; BehaviorTreeEditor.h:40 | None for authoring. DDS2 impact low: all 6 shipped BTs are cooked (graphs stripped) anyway. Running BTs is fine |
| **EQS GRAPH authoring UI-locked** — `UEnvironmentQueryFactory` unexported; same AIGraph family as BT | EnvironmentQueryFactory.h:8–9; plugin module type "UncookedOnly" | RUNNING existing queries fully viable (run_eqs_query); authoring is not |
| `UBlackboardDataFactory` unexported | BlackboardDataFactory.h:17–18 | Direct `NewObject` — the factory body is literally one NewObject call (BlackboardDataFactory.cpp:28–32) |
| MassAI / MassCrowd — both EnabledByDefault:false, absent from .uproject, game predates Mass | MassCrowd.uplugin:13, MassAI.uplugin:13 | Classic AIModule stack (DetourCrowd confirmed live — LIVE_PROBES G1) |
| Nav SEGMENT links engine-dead ("@todo … Not really working now") | NavLinkProxy.h:42–45 | Point links only; reject segment params by name |

### Materials / rendering ([work/D_materials_rendering.md](work/D_materials_rendering.md))

| Dead end | Evidence | Exported alternative |
|---|---|---|
| `UMaterialParameterCollection::SetScalarParameterDefaultValue / SetVectorParameterDefaultValue` — unexported, not UFUNCTIONs | MaterialParameterCollection.h:95–107 (`#if WITH_EDITOR`, no macro) | Write the UPROPERTY arrays (:89–93) directly + `PreEditChange/PostEditChange` virtuals (:140–144); propagation verified at ParameterCollection.cpp:227–305 |
| `URuntimeVirtualTextureFactory` — completely unexported (not even MinimalAPI) | RuntimeVirtualTextureFactory.h:16–17 | Replicate its one-line body: `NewObject<URuntimeVirtualTexture>` (factory cpp:23–28) |
| `GetMaterialSelectedNodes` genuinely requires an open material-editor window | MaterialEditingLibrary.cpp:781–795 | `GetMaterialPropertyInputNode` does NOT (cpp:797–806) — header comments are misleading on both |
| Material factories lack method-level UNREALED_API (unlike the MIC factory) | MaterialFactoryNew.h:24, MaterialFunctionFactoryNew.h:20, MaterialParameterCollectionFactoryNew.h:20 | Virtual dispatch through the UFactory vtable (Factory.h:109–112) — never qualified `::FactoryCreateNew` calls |
| Base-material layer authoring (MaterialAttributeLayers on root UMaterial) | MaterialLayersFunctions.h:55 editor-only tree state | Instance-level `SetMaterialLayers` (MaterialInstance.h:785) is the viable 5.3 route |

### Geometry / meshes ([work/E_geometry_meshes.md](work/E_geometry_meshes.md))

| Dead end | Evidence | Exported alternative |
|---|---|---|
| **Modeling Tools Editor Mode interactive tools UI-locked** — UInteractiveTool subclasses need a live ToolManager + EdMode toolkit + viewport input; no headless entry | axis E negative #3 | `UEditorModelingObjectsCreationAPI` (EditorModelingObjectsCreationAPI.h:29/:35/:93) or GeometryScriptingEditor `CreateNewStaticMeshAssetFromMesh` |
| GeometryScripting plugin **disabled** in this project (no EnabledByDefault key; ModelingToolsEditorMode does NOT chain-enable it) | GeometryScripting.uplugin; dep chains of 4 .uplugins read | One-time cost: plugin reference in MifBridge.uplugin — flagged, acceptable |
| PCG — EnabledByDefault:false, experimental 0.1, not project-enabled | PCG.uplugin:13/:4 | Not this cycle |
| `UStaticMeshEditorSubsystem` has NO socket API (old EditorStaticMeshLibrary docs suggest otherwise) | grep 0 in StaticMeshEditorSubsystem.h | `UStaticMesh::AddSocket/FindSocket/RemoveSocket` ENGINE_API (StaticMesh.h:1888/:1895/:1901) |
| `USkeletalMesh` has NO exported RemoveSocket; `Sockets` array is **private** | SkeletalMesh.h:2230 (`private:`), :2235–2236 | FProperty reflection on `Sockets` + `RebuildSocketMap()` (:2463, ENGINE_API) |
| EditorScriptingUtilities plugin EnabledByDefault:false in this fork — do not route through UEditorStaticMeshLibrary/EditorLevelLibrary | EditorScriptingUtilities.uplugin:13 | Everything needed lives in engine-source StaticMeshEditor/SkeletalMeshEditor modules, zero plugin cost |

### World / level ([work/F_world_level.md](work/F_world_level.md))

| Dead end | Evidence | Exported alternative |
|---|---|---|
| Landscape spline authoring has **no fully-exported path** — spline classes MinimalAPI, rebuild entry points (`UpdateSplinePoints`, `AutoCalcRotation`, `AutoFlipTangents`) carry no export macro; editor tool is a `friend class` | LandscapeSplinesComponent.h:101, LandscapeSplineControlPoint.h:49/:255–:265/:287, LandscapeSplineSegment.h:187/:342–:347 | Public mutable inline accessors (:161/:164) + public-virtual **vtable dispatch** (valid: both modules editor-built, classes non-final); fallback = fully-exported `ALandscapeProxy::EditorApplySpline` (LandscapeProxy.h:868–869) |
| Landscape edit layers — exports exist but a **contract** blocks them: create_landscape deliberately sets bCanHaveLayersContent=false; shipping CreateLayer alone would silently divorce sculpt/paint from visible terrain | Landscape.h:281/:285/:345; MifBridgeLandscape.cpp:246–250; live proof: EditorApplySpline no-ops on layered landscapes (LandscapeBlueprintSupport.cpp:26–31) | Keep layers off; a future enable_landscape_layers must own the sculpt/paint rewrite in the same change |
| **Landmass is a dead end for scripting** — ALandmassActor unexported, editor-module-only, brush behaviour is BP content driving edit layers (blocked above) | LandmassActor.h:9–10/:16–31 | Nothing to bridge |
| `AInstancedFoliageActor::AddInstances/RemoveAllInstances` statics unexported (UFUNCTION, no FOLIAGE_API) | InstancedFoliageActor.h:284–288 | Exported `FFoliageInfo::AddInstances` (InstancedFoliage.h:335) — strictly better |
| `UEditorLevelUtils::GetLevels` unexported | EditorLevelUtils.h:329–330 | `UWorld::GetLevels()` |
| WP conversion is commandlet-only; data-layer MUTATION excluded on value (only WP map is cooked/unsaveable) | WorldPartitionConvertCommandlet.h; DataLayerEditorSubsystem.h:67–68 (exported but pointless here) | Read-only list_data_layers only |

### Sequencer / UMG / input ([work/G2_sequencer_umg_input.md](work/G2_sequencer_umg_input.md))

| Dead end | Evidence | Exported alternative |
|---|---|---|
| SequencerScripting extension classes: **no export macros** on any `UMovieScene*Extensions` UCLASS; typed channel wrappers live in **Private/** headers | MovieSceneSequenceExtensions.h:25–26, MovieSceneBindingExtensions.h:17–18; Private/KeysAndChannels/*.h | Exported MOVIESCENE_API object-model route (axis G2 proposals). NOTE: the plugin **is** enabled and reflection-callable — see §6 overturn |
| `ULevelSequenceFactoryNew` unexported AND private | LevelSequenceEditor/Private/Factories/LevelSequenceFactoryNew.h:11–23 | Replicate its 3-call FactoryCreateNew (.cpp:29–41) |
| `FWidgetBlueprintEditorUtils::RenameWidget` UI-locked (takes `TSharedRef<FWidgetBlueprintEditor>`) | WidgetBlueprintEditorUtils.h:34; reference impl cpp:277–433 | Replicate the core from exported pieces (axis G2 rename_widget, incl. the four tail steps) |
| `UWidgetAnimation::BindPossessableObject` asserts without a live preview widget (`CastChecked<UUserWidget>(Context)`) | WidgetAnimation.cpp:157 | Write `AnimationBindings` UPROPERTY directly (WidgetAnimationBinding.h:17–31) |
| **`SClassPickerDialog`-gated factories**: `UInputMappingContext_Factory` / `UInputAction_Factory` ConfigureProperties opens a modal class picker | InputEditorModule.cpp:94–118 / :171–195 (`SClassPickerDialog::PickClass`) | Both assets are plain UDataAssets — bare `NewObject` + AssetCreated; skip the InputEditor module entirely |
| `FKismetNameValidator` class unexported — only ctor/IsValid/GetMaximumNameLength carry UNREALED_API | Kismet2NameValidators.h:83/:86/:90/:93–94 | Fine for stack use; call only the marked methods |

### Niagara / audio / physics ([work/G3_niagara_audio_physics.md](work/G3_niagara_audio_physics.md))

| Dead end | Evidence | Exported alternative |
|---|---|---|
| GAS: **four-way negative** — plugin EnabledByDefault:false, absent from .uproject and game Build.cs, `describe_class AbilitySystemComponent` → not found live | GameplayAbilities.uplugin:13 + 3 more legs | Zero endpoints; enabling would add a runtime dep the shipped game never loads |
| `FNiagaraSystemInstanceController` and `FNiagaraSystemInstance` fully unexported; aggregate GetNumParticles commented out | NiagaraSystemInstanceController.h:44, NiagaraSystemInstance.h:69/:225 | All-inline accessor chain + per-emitter summation via FNiagaraEmitterInstance |
| Niagara GRAPH authoring UI-locked at raw-node level; module-stack route is a **one-way street** (AddScriptModuleToStack: only the UNiagaraScript* overload exported; ALL RemoveModuleFromStack overloads unexported) | NiagaraGraph.h:178, NiagaraNode.h:27, NiagaraStackGraphUtilities.h:229–235/:276/:278/:280 | Asset-create + parameter-set + spawn + particle counts (all exported); stack authoring deferred until a removal story exists |
| **Chaos fracture plugin-gated AND UI-locked** — PlanarCut + Fracture plugins EnabledByDefault:false; `UFractureTool*` in FractureEditor Private, only 3 FRACTUREEDITOR_API classes in Public (mode/toolkit/settings) | PlanarCut.uplugin:13, Fracture.uplugin:16; FractureEditorMode.h:140, FractureEditorModeToolkit.h:100, FractureModeSettings.h:33 | PLANARCUT_API fns (PlanarCut.h:207/:261/:284) would work IF the plugin were enabled — future unlock, game has zero destruction content |
| MetaSound 5.3 lacks `BuildToAsset` (only `Build(UObject* Parent, ...)`) | MetasoundBuilderSubsystem.h:361/:495 (grep: no BuildToAsset) | Builder API is exported end-to-end; deep design, one recipe endpoint only |

### Data ([work/H_data.md](work/H_data.md))

| Dead end | Evidence | Exported alternative |
|---|---|---|
| `UDataAssetFactory` / `UCurveFactory` methods unexported on MinimalAPI classes | DataAssetFactory.h:22–23, CurveFactory.h:26–27 | NewObject the factory, set public UPROPERTYs, hand to `IAssetTools::CreateAsset` (virtual dispatch inside AssetTools) |
| `UCompositeDataTable::ParentTables` protected; composite tables reject row mutation by design | CompositeDataTable.h:43/:58/:76–77 | `AppendParentTables` (:58) for append; FProperty reflection + PostEditChangeProperty for replace/remove; row-op endpoints must detect + refuse |
| String-table edits are **not undoable** — data in private non-UPROPERTY `FStringTablePtr` | StringTable.h:38–39 | Report `undoable:false`; restore = re-set previous value |
| **RamaSaveSystem is stub-source + binary-only** — SDK cpp bodies empty, no import .lib ships; MifBridge can never link RAMASAVESYSTEM_API | RamaSaveLibrary.cpp:6–8/:39–41; Binaries/Win64 glob | Reflection-only route; live probe shows the module reflects fully (LIVE_PROBES H1) but behavioral proof (PIE save/load) outstanding |
| Localization gather is commandlet-only ⇒ blocking ⇒ not a sync endpoint | 7 GatherText* commandlet headers | Future: out-of-process `-run=GatherText` behind request+poll (trigger_cook precedent), tier 3 |

### Diagnostics / project ([work/I_diagnostics.md](work/I_diagnostics.md), [work/J_dds2_project.md](work/J_dds2_project.md))

| Dead end | Evidence | Exported alternative |
|---|---|---|
| `GAverageFPS/GAverageMS` have no public-header declaration — engine's own consumers re-declare `extern ENGINE_API` locally | UnrealEngine.cpp:634–635; EngineAnalyticsSessionSummary.cpp:23 | Copy the local-extern pattern (links; fork-fragility wart) |
| `IMessageLogListing::GetFilteredMessageCount` does not exist in 5.3.2; no API enumerates log categories or registered Message Log listings | IMessageLogListing.h:47 (only GetFilteredMessages); LogSuppressionInterface.h (27 lines); MessageLogModule.h:97 private view-model | Count = `GetFilteredMessages().Num()`; categories = "seen" set from ring buffer; listings = required logName + documented canonical names |
| No UnrealPak.exe on this machine (nor any IoStore tool exe) — the "ModKit UnrealPak lane" is impossible | D:/UE532/Engine/Binaries/Win64 (and D:/DDS2SDK/Engine does not exist) | retoc is the ONLY pack lane |
| `IAssetRegistry` / `IFileManager` carry no export macros — never link against their concrete impls | IAssetRegistry.h:150, FileManager.h:57 | Module-singleton virtual dispatch only |
| No editor→live-game control channel — UE4SS Lua mods run in the shipping game process; bridge can only tail their log | axis J negative #4 | Out of scope; docs note so agents stop looking |
| No population-manager class; DDS2 dialogue is a plain UObject, not a graph asset | live probes (axis J negatives #6/#7) | Population = TownStatusManager+GameMode emergent; dialogue via get_property |

---

## 2. Modal-dialog traps on otherwise-viable paths

**The recurring Phase-1 blind spot.** A modal dialog opened from a mid-frame HTTP handler deadlocks
the editor (the HTTP pump never runs again). Every entry below is a *viable* endpoint whose engine
path hides a modal; the documented mitigation is mandatory, not advisory.

| # | Trap | Where it fires | Mitigation (documented in the axis entry) |
|---|---|---|---|
| 1 | `UDataTableFactory::ConfigureProperties` is modal (`GEditor->EditorAddModalWindow`) | DataTableFactory.cpp:176 | Never call ConfigureProperties on ANY factory; `IAssetTools::CreateAsset` never calls it (verified, AssetTools.cpp:1627–1682) — [work/H_data.md](work/H_data.md) |
| 2 | `UCurveTableFactory::ConfigureProperties` modal window | CurveTableFactory.cpp:55 | Same rule — [work/H_data.md](work/H_data.md) |
| 3 | `UCurveFactory::ConfigureProperties` modal SClassPickerDialog | EditorFactories.cpp:7231–7259 (PickClass :7251) | Use the typed subclass factories (UCurveFloat/LinearColor/VectorFactory) whose overrides are non-modal `return true;` (:7283–7286/:7299–7302) — [work/H_data.md](work/H_data.md) |
| 4 | `UDataAssetFactory::ConfigureProperties` modal SClassPickerDialog | EditorFactories.cpp:7470 | Same rule — [work/H_data.md](work/H_data.md) |
| 5 | `UAssetToolsImpl::CanCreateAsset` (called from CreateAsset :1647) raises FMessageDialog on invalid name (:4294), map-name collision (:4301), and a YesNo **overwrite prompt** (:4331–4337) | AssetTools.cpp:4287–4337 | Pre-validate: `FName::IsValidObjectName` + `FPackageName::IsValidLongPackageName` + map-asset check + `DoesPackageExist`; never implement overwrite by letting the engine prompt — [work/B_assets_registry.md](work/B_assets_registry.md), [work/H_data.md](work/H_data.md) |
| 6 | `UAnimBlueprintFactory::FactoryCreateNew` FMessageDialog on null/invalid ParentClass | AnimBlueprintFactory.cpp:454–459 | Pre-validate parentClass strictly (must be UAnimInstance child) — [work/B_assets_registry.md](work/B_assets_registry.md) create_asset |
| 7 | `ObjectTools::ConsolidateObjects`: **unsuppressable** end-of-run modals — "Failed to Consolidate Assets" (:1888) and "Critical Failure" (:1922), gated only by `!IsRunningCommandlet()` (:1440); plus `bWarnAboutRootSet` default TRUE ⇒ modal YesNo on rooted objects (ForceReplaceReferences :1093–1110) | ObjectTools.cpp | Use the 6-arg overload with bWarnAboutRootSet=false; mandatory pre-validation ladder (same-class, target-not-dependent-on-sources, no rooted sources) keeps failure sets empty; residual critical-failure risk must be documented — [work/B_assets_registry.md](work/B_assets_registry.md) consolidate_assets |
| 8 | `IAssetTools::FixupReferencers`: blocking `SDiscoveringAssetsDialog` while the registry is scanning (:59–66); final delete via `ObjectTools::DeleteObjects` retains modal failure paths (ObjectTools.cpp:2833/:3127) | AssetFixUpRedirectors.cpp | Gate on `IAssetRegistry::IsLoadingAssets()` → "registry scan in progress — retry"; residual modal risk when a redirector is still referenced in-memory (undo buffer) — [work/B_assets_registry.md](work/B_assets_registry.md) fixup_redirectors |
| 9 | `FEditorFileUtils::SaveDirtyPackages` fast path: **modal on any failed save** — fast branch hardcodes `bUseDialog=true` (FileHelpers.cpp:3822–3828) → InternalWarnUserAboutFailedSave → `FMessageDialog::Open` (:3620–3640) | FileHelpers.cpp | Enumerate dirty packages yourself and save per-package non-dialog, or wrap in `TGuardValue<bool>(GIsRunningUnattendedScript, true)` (the engine's own trick, cf. :5476) — [work/A_editor_core.md](work/A_editor_core.md) save_dirty_packages |
| 10 | `EditorLevelUtils::AddLevelToWorld`: modal `FSuppressableWarningDialog::ShowModal()` when the package is **already present** or is the persistent level | EditorLevelUtils.cpp:441–451 | Pre-check `FLevelUtils::FindStreamingLevel` (LevelUtils.h:35/:44) + persistent-name compare; return `alreadyPresent:true` without calling — [work/F_world_level.md](work/F_world_level.md) add_sublevel |
| 11 | `EditorLevelUtils::MakeLevelCurrent`: modal FMessageDialog on a **locked level** (bEvenIfLocked=false) | EditorLevelUtils.cpp:555–589 | Pre-check `FLevelUtils::IsLevelLocked` (LevelUtils.h:91) — [work/F_world_level.md](work/F_world_level.md) set_current_sublevel |
| 12 | `EditorLevelUtils::RemoveLevelFromWorld`: modal on locked level (cpp:830–834) and on failed package unload (cpp:894–897); also resets the transaction buffer (:886–889) + forced GC (`GEditor->Cleanse`, :909) | EditorLevelUtils.cpp | Pre-check IsLevelLocked + Package->IsDirty(); self-managed bucket mandatory — [work/F_world_level.md](work/F_world_level.md) remove_sublevel |
| 13 | `EditorLevelUtils::MoveActorsToLevel` with default flags pops modal reference/rename prompts | bWarnAboutReferences → clipboard prompt (cpp:182), bWarnAboutRenaming gate (:250) | MUST pass both `false` — [work/F_world_level.md](work/F_world_level.md) move_actors_to_sublevel |
| 14 | `CreateNewStreamingLevelForWorld(bInUseSaveAs=true)` → modal SaveAs (`FEditorFileUtils::SaveLevelAs`) | EditorLevelUtils.cpp:760–767 | Hard-code `bInUseSaveAs=false` + real filename — [work/F_world_level.md](work/F_world_level.md) create_sublevel |
| 15 | **`ULevelInstanceSubsystem::CreateLevelInstanceFrom` — DEMOTED, no dialog-free path in 5.3.2**: internally calls CreateNewStreamingLevelForWorld with bUseSaveAs **hard-coded true** → modal Save-As (SaveAsImplementation, FileHelpers.cpp:1469–1486); FNewLevelInstanceParams fields don't reach the SaveAs branch | LevelInstanceSubsystem.cpp:898/:999–1000 | None found — create_level_instance is parked in F's UNVERIFIED with the full write-up; a 5.4+ change or bridge-side reimplementation of move+save could resurrect it |
| 16 | `UMoviePipelinePIEExecutor::Start`: `FMessageDialog::Open` at :93 (sequence fails to load) and :109 (any queue job's map unsaved) — and Start runs SYNCHRONOUSLY inside `RenderQueueWithExecutor`, i.e. inside the handler | MoviePipelinePIEExecutor.cpp:82–114 | Pre-validate: sequence loads as ULevelSequence; every job's map saved (reuse `IsMapValidForRemoteRender`); clear stale jobs from the shared editor queue first — [work/G2_sequencer_umg_input.md](work/G2_sequencer_umg_input.md) render_movie_request |
| 17 | `FPhysicsAssetUtils::CreateFromSkeletalMesh` body generation: `FScopedSlowTask` + `SlowTask.MakeDialog()` on the game thread — Slate re-entrancy mid-handler (progress dialog, not input-modal) | PhysicsAssetUtils.cpp:343–346, :903–906 | Suppress via unattended-script guard or accept+document the dialog flash; also pre-check `GetResourceForRendering()` (check() at :500–501) — [work/G3_niagara_audio_physics.md](work/G3_niagara_audio_physics.md) create_physics_asset |
| 18 | `RecompileMaterial` tail (`BuildTextureStreamingData`): cancellable `FScopedSlowTask ... MakeDialog(true)` pumping UI mid-handler (plus GC×2 and a shader busy-wait — see §3) | MaterialEditorUtilities.cpp:791–792 | Don't call RecompileMaterial for the UMaterial branch; replicate its non-blocking core (FMaterialUpdateContext + Pre/PostEditChange, MaterialEditingLibrary.cpp:697–728) — [work/D_materials_rendering.md](work/D_materials_rendering.md) recompile_material |
| 19 | `SetConvexDecompositionCollisionsWithNotification`: blocks the game thread in a `WaitFor(33ms)` loop while pumping an FScopedSlowTask progress dialog until V-HACD completes; also closes+reopens any open static-mesh editor tab | StaticMeshEditorSubsystem.cpp:1383–1391, :1409–1412 | Mandatory input-size cap (refuse >500k tris); document the dialog flash + editor-tab churn — [work/E_geometry_meshes.md](work/E_geometry_meshes.md) set_convex_collision |
| 20 | `ValidateAssetsWithSettings`: FScopedSlowTask with `ESlowTaskVisibility::ForceVisible` + `MakeDialogDelayed(.1f)` (non-modal but pumps Slate); default `bShowIfNoFailures=true` spawns toasts | EditorValidatorSubsystem.cpp:218–220; .h:95 | Keep asset lists small; set bShowIfNoFailures=false — [work/B_assets_registry.md](work/B_assets_registry.md) validate_assets |
| 21 | `UAssetExportTask` prompts gated only on `bPrompt` | UnrealExporter.cpp:337/:385 | Always bPrompt=false, bAutomated=true — [work/B_assets_registry.md](work/B_assets_registry.md) export_asset |

**Rule distilled**: for every engine editor-utility call, grep its .cpp for `FMessageDialog`,
`EditorAddModalWindow`, `ShowModal`, `FScopedSlowTask`/`MakeDialog`, and `OpenMsgDlgInt` BEFORE
wiring it into a handler. Phase-2 found modals on "benign" paths in 6 of 12 axes.

---

## 3. Blocking / synchronous hazards (game-thread waits an endpoint must not trigger)

Handlers run ON the game thread **synchronously and inline, post-world-tick** — not "mid-frame", and
not via `AsyncTask`; brief invariant 3 has been corrected (`MifBridgeServer.cpp:229-265`). The
hazard is *larger* under the real model, not smaller: a blocking handler occupies the `FTSTicker`
that would have to advance whatever it is waiting on, so these calls stall or deadlock the whole
bridge, not just their own request:

| # | Hazard | Citation | Mitigation |
|---|---|---|---|
| 1 | **`UMaterialEditingLibrary::GetStatistics` synchronously blocks until that material's shaders compile** — submits jobs at High priority then `Resource->FinishCompilation()`; unbounded on cold DDC (see §6 overturn — it is NOT "stale numbers") | MaterialEditingLibrary.cpp:1355–1362 | Check `IsGameThreadShaderMapComplete()` first; return `{pending:true}` steering to shader_compile_status — [work/D_materials_rendering.md](work/D_materials_rendering.md) get_material_stats |
| 2 | **`UEditorEngine::BuildReflectionCaptures` calls `FAssetCompilingManager::FinishAllCompilation()`** — unbounded wait for ALL editor-wide asset compilation; plus `check(FeatureLevel >= SM5)` = hard CRASH below SM5, and a GWarn slow task | EditorEngine.cpp:3978–3982, :3989 | MANDATORY pre-checks: shader_compile_status idle AND GetNumRemainingAssets()==0 AND feature level ≥ SM5 — [work/D_materials_rendering.md](work/D_materials_rendering.md) + [work/F_world_level.md](work/F_world_level.md) (dedupe to ONE endpoint at merge; F's spec carries the pre-checks) |
| 3 | **`UNiagaraSystem::WaitForCompilationComplete`** — exported but a synchronous multi-frame FScopedSlowTask spin; ALSO reachable **implicitly**: `UNiagaraSystem::PreSave` calls it, so saving a system mid-compile blocks | NiagaraSystem.h:409; NiagaraSystem.cpp:2429–2461, PreSave :255 | Request/poll pair only; gate save_package on the niagara compile-status poll — [work/G3_niagara_audio_physics.md](work/G3_niagara_audio_physics.md) |
| 4 | **StaticMesh convex decomposition WaitFor loop** — bulk path blocks in `WaitFor(33ms)` pumping Slate until V-HACD finishes (§2 #19) | StaticMeshEditorSubsystem.cpp:1386–1391 | Input-size cap; synchronous-by-design with documented stall — [work/E_geometry_meshes.md](work/E_geometry_meshes.md) |
| 5 | `FAssetCompilingManager::FinishAllCompilation` (:133) and `FShaderCompilingManager` flushes — frame-blocking drains, blacklisted for handlers | AssetCompilingManager.h:133 | Poll `GetNumRemainingAssets()` / `GetNumRemainingMeshes()` (get_asset_compilation_status / asset_compile_status — dedupe B vs E naming at merge) |
| 6 | `UStaticMesh::Build(bInSilent, OutErrors)` — passing non-null OutErrors **forces synchronous build** ("This will prevent async static mesh compilation") | StaticMesh.h:1661–1662, :1672–1675 | Never pass OutErrors; call `Build(true)`, poll the compile manager — [work/E_geometry_meshes.md](work/E_geometry_meshes.md) build_static_mesh |
| 7 | **Save-time / mid-handler GC**: `FEditorFileUtils` dirty-package enumeration runs `CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS)` whenever content packages are included; `RecompileMaterial` tail runs CollectGarbage **twice**; `RemoveLevelsFromWorld` ends with `GEditor->Cleanse` (forced GC) | FileHelpers.cpp:3642–3647; MaterialEditorUtilities.cpp:789/:814; EditorLevelUtils.cpp:909 | Any unrooted UObject the bridge holds across these calls dies — root everything (TStrongObjectPtr / AddToRoot), esp. the render-target and dynamic-mesh handle maps |
| 8 | `RecompileMaterial` → `CompileDebugViewModeShaders` busy-waits (`while (PendingMaterials.Num()>0) { Sleep(0.1); ... }`) | DebugViewModeHelpers.cpp:322–356 | Same as §2 #18: reimplement the non-blocking core |
| 9 | `IAssetRegistry::EnumerateAllPackages` runs the callback **inside the registry lock** — re-entry deadlocks | IAssetRegistry.h:411–414 (header states it) | Copy data out; never call registry/asset functions inside the callback — [work/B_assets_registry.md](work/B_assets_registry.md) |
| 10 | `USkeletalMesh::GetMorphTargets()` accessor synchronously WAITS if the mesh is mid-async-compilation (linkage is fine — see §6 overturn) | SkeletalMesh.h:1795–1801, WaitUntilAsyncPropertyReleased :2708 | Check the compile-status poll first when in doubt — [work/E_geometry_meshes.md](work/E_geometry_meshes.md) |
| 11 | `RegenerateLOD` runs skeletal reduction synchronously on the game thread (seconds on dense meshes); `MergeComponentsToStaticMesh` and material baking are synchronous by design | SkeletalMeshEditorSubsystem.cpp:39–55; IMeshMergeUtilities.h:67 | Input caps + documented stall; pass bSilent=true — [work/E_geometry_meshes.md](work/E_geometry_meshes.md) |
| 12 | `EditorBuild(BuildLighting)`: synchronous `MAP REBUILD ALLVISIBLE` first if the level has non-volume BSP brushes; build-progress window always opens | EditorBuildUtils.cpp:411–429, :364 | Lightmass kick itself is async (request+poll stands); document the BSP pre-pass — [work/D_materials_rendering.md](work/D_materials_rendering.md) build_lighting |
| 13 | Localization gather commandlets in-process = full-gather block | axis H negative #2 | Out-of-process request+poll or nothing |

---

## 4. Cooked-content limitations (what .pak-mounted base-game content can and cannot do)

DDS2 mounts ~25,285 container-only packages (LIVE_PROBES: 37,131 total / 11,846 loose / 6,348 loaded).
Cooked packages are saved editor-filtered (`PKG_FilterEditorOnly`, `isCookedForEditor` — verified at
object level via describe_package, [work/J_dds2_project.md](work/J_dds2_project.md) live #3).

### Stripped — endpoints must REFUSE or degrade honestly (never silent-empty)

| What's gone | Evidence | Consequence |
|---|---|---|
| **Material expression graphs** — `UMaterialExpression` is `UCLASS(abstract, Optional, ...)`; Optional classes are stripped from cooked packages; UMaterial's expression collection lives in `UMaterialEditorOnlyData` (also Optional) | MaterialExpression.h:183–184; Material.h:309–310 | Every material-graph endpoint refuses on PKG_Cooked; only create-NEW + instance-derivation work against base-game materials — [work/D_materials_rendering.md](work/D_materials_rendering.md) negative #3 |
| **Cooked textures have no FTextureSource** — engine's own header warning: "Always check Source.IsValid" | Texture.h:1092–1096 | Compression/mip regeneration impossible; refresh_texture reports `sourceValid:false` per texture — D negative #4 |
| **Foliage `FFoliageInfo::Instances` is editor-only data** — serialized only when `!Ar.ArIsFilterEditorOnly`; cooked IFAs load with EMPTY instance arrays (the FoliageInfos type map survives) | InstancedFoliage.h:275–296; InstancedFoliage.cpp:503–514, :4386 | list_foliage on cooked content = types + HISM component counts only (`FFoliageInfo::GetComponent()` → `GetInstanceCount()`); remove_foliage_instances **cannot see or remove base-game foliage at all** — [work/F_world_level.md](work/F_world_level.md) verdicts |
| **Source-model-less meshes**: LOD generation, convex decomposition, UV writes, Nanite builds, build settings all need FStaticMeshSourceModel/MeshDescription — stripped. **Plus a crash guard**: stock `CopyMeshFromStaticMesh(render_data)` unconditionally reads `GetSourceModel(LOD).BuildSettings` in WITH_EDITOR builds and range-check-asserts on cooked meshes | MeshAssetFunctions.cpp:141, :155–158 | Read route for base-game geometry: pre-check `GetNumSourceModels() > LOD`; on failure bypass the wrapper and call `FStaticMeshLODResourcesToDynamicMesh::Convert` directly (MESHCONVERSIONENGINETYPES_API, StaticMeshLODResourcesToDynamicMesh.h:39–42). Pipeline: render_data read → derive → create NEW asset — [work/E_geometry_meshes.md](work/E_geometry_meshes.md) negative #10 + copy_from_static_mesh verdict |
| **Dependency/referencer graph stripped at cook** — GetDependencies on container packages returns empty by construction. Loose→container edges DO survive (stored in the loose package) | [work/B_assets_registry.md](work/B_assets_registry.md) negative #1; self-diagnose via IsContainerOnlyPackage (MifBridgeCooked.cpp:44–49) | Endpoints must return `dependencyDataAvailable:false` + explanation, never a silent empty list |
| **Premade asset registry is blind to NEW mounts** — zen containers expose chunks not files; ScanPathsSynchronous can't see them; a mount_pak'd mod trio is loadable by exact path but invisible to find_assets. **Refinement (live)**: boot-time-mounted DLC containers (/DDS2Casino, /ChristmasDlc) ARE visible — the blindness applies to paks mounted after boot | AssetRegistry.cpp:186–192, :61–70; LIVE_PROBES refined claim #9 | Only an AppendState of a carried registry blob (IAssetRegistry.h:729 + FAssetRegistryState::Load, AssetRegistryState.h:541) fixes discovery; retoc does not emit such blobs today — [work/J_dds2_project.md](work/J_dds2_project.md) negative #2 |
| **Cooked BP graphs stripped** (existing, foundational): container BP packages export only `<Name>_C` + `Default__<Name>_C` + PackageMetaData — no UBlueprint object | describe_package live probe, J live #3 | All graph endpoints refuse (existing ResolveBlueprint grading); EUB TryRun dead on cooked (EditorUtilitySubsystem.cpp:142–145, [work/A_editor_core.md](work/A_editor_core.md) negative #9); Niagara/SoundCue graph editing refuses — but note emitter-list edits **crash rather than refuse** without endpoint pre-checks (G3 negative #7) |
| Cooked WP maps: unsaveable (documented IMPOSSIBLE); reflection-capture/lighting results land in MapBuildData and can't persist; cooked WP streaming cells are not editable sublevels | EditorEngine.cpp:3971; F negative #8 | Endpoints flag `cookedMap:true` / `transient:true` instead of failing silently |

### Survives — verified live ([work/LIVE_PROBES.md](work/LIVE_PROBES.md), [work/J_dds2_project.md](work/J_dds2_project.md))

- **Curves with full key data**: FRichCurve keys (times, values, tangents, interp modes, extrapolation)
  read back via get_property on a container-origin CurveFloat (LIVE_PROBES H2). 46 CurveFloats in
  project; CurveTable count is **0** — curve-TABLE endpoints have zero project demand.
- **DataTable rows**: read_datatable / get_datatable_row fully serialise cooked *Database tables with
  native row structs and field names (J live #1–2; LIVE_PROBES H3 — 379 tables live, 187 "Database"-named).
- **CDO / MIC properties**: get_property on cooked-BPGC CDOs works (`Default__BP_BaseNPC_C.JumpMaxCount` → 1);
  cooked material instances expose all 37 props incl. parameter arrays — cooked MI parameter READS are
  compositions; only writes need set_material_parameter (J live #4–5).
- **Reflection of native AND BPGC classes**: describe_class returns identical-quality surfaces for
  `/Script/...` natives and cooked `_C` classes (J live #6; the whole LIVE_PROBES G1 NPC-class analysis
  ran on cooked content).
- Also: Kismet **bytecode** (`UStruct::Script`) survives — disassemble_function's whole point (C);
  AnimBP `BakedStateMachines`/`AnimNodeProperties` are cooked runtime data (C describe_anim_class);
  StaticMesh **sockets** and UBodySetup collision counts survive (E); render data (LOD/vert/UV-channel
  counts) readable; WP **data layers** live-readable on the cooked map (F list_data_layers);
  UFoliageType assets load fine from paks (F).
- **Census correction for tiering**: the real UMG surface is **279 cooked WidgetBlueprintGeneratedClass**
  assets — plain WidgetBlueprint (54) badly under-counts it (LIVE_PROBES G2). Sequencer surface is
  3 game LevelSequences → lowest tier.

---

## 5. Engine bugs / sharp edges in this 5.3.2 fork

Not policy — actual traps in the engine code an implementer must engineer around.

1. **UDynamicMeshPool MaxPoolSize failsafe is a leak, not a backstop** — on trip (default 1000,
   CVar `geometry.DynamicMesh.MaxPoolSize`), `RequestMesh` does `AllCreatedMeshes.Reset()` +
   `ForceGarbageCollection`; afterwards `ReturnMesh` fails its `Contains` ensure for every pre-trip
   mesh — handles held by a rooted map survive GC but can never be returned (permanent UObject leak +
   ensure spam). UDynamicMesh.cpp:559, :563–568, :578. Bridge must enforce its own live-handle cap
   (~256) and never rely on the CVar. ([work/E_geometry_meshes.md](work/E_geometry_meshes.md))
2. **The `editcondition` silent-ignore class** — writing a property whose meta editcondition flag is
   false is silently ignored by the engine. Concrete instances: `UStaticMeshComponent::MinLOD` needs
   `bOverrideMinLOD=true` (StaticMeshComponent.h:115, flag :226 — [work/D_materials_rendering.md](work/D_materials_rendering.md)
   set_actor_render_overrides); `ALevelStreamingVolume::StreamingLevelNames` is VisibleAnywhere/
   BlueprintReadOnly (CPF_EditConst — LevelStreamingVolume.h:34; drive the inverse
   `ULevelStreaming::EditorStreamingVolumes` instead, [work/F_world_level.md](work/F_world_level.md)).
   Audit every set_property-adjacent write for editconditions.
3. **EditorScriptingHelpers PIE silent-zero returns** — every UStaticMeshEditorSubsystem /
   USkeletalMeshEditorSubsystem call runs `CheckIfInEditorAndPIE`, which returns false during PIE
   (EditorScriptingHelpers.cpp:170–187), so even READ endpoints silently report 0/defaults while PIE
   runs. All subsystem-backed endpoints must detect PIE and error "stop_pie first".
   ([work/E_geometry_meshes.md](work/E_geometry_meshes.md) systemic finding)
4. **`FAIMoveRequest` holds exactly ONE goal** (`SetGoalLocation`, AITypes.h:547) — no engine-side
   route chaining exists. Multi-waypoint NPC walking requires a plugin-side leg queue (FTSTicker,
   one MoveToLocation per tick, gate leg-advance on `DidMoveReachGoal()` not Status==Idle).
   ([work/G1_ai_navigation.md](work/G1_ai_navigation.md) pie_move_pawn)
5. **RVT enum differs in this branch** — `ERuntimeVirtualTextureMaterialType` has NO `Mask4` or
   `Displacement` values in 5.3.2 (they are 5.4+); real list at
   Runtime/Engine/Public/VT/RuntimeVirtualTextureEnum.h:35–45 (note Public/VT/, not Classes/VT/).
   Generalize: **do not trust 5.4-era enum/API knowledge against this fork** — verify per-branch.
   ([work/D_materials_rendering.md](work/D_materials_rendering.md) create_rvt_asset)
6. **The engine's own `InCalllback` typo** (`FEditorModeTools::ForEachEdMode(TFunctionRef<...> InCalllback)`,
   EditorModeManager.h:547) — a standing caution: grep for what's ACTUALLY in the header, including
   typos; a "correctly"-spelled grep can return 0 hits on real API. ([work/A_editor_core.md](work/A_editor_core.md))
7. **`AddRichCurve` hard-check() crash + factory pre-seeding** — `UCurveTable::AddRichCurve` asserts
   (`check(CurveTableMode != SimpleCurves)`, CurveTable.cpp:554–557) = editor CRASH on a simple-mode
   table; and `UCurveTableFactory::MakeNewCurveTable` pre-seeds a stray "Curve" row whose mode is
   SimpleCurves under the CreateAsset path (zero-init InterpMode = RCIM_Linear, CurveTableFactory.cpp:63–77).
   Mandatory: `EmptyTable()` (CurveTable.h:217) immediately after creation; read `GetCurveTableMode()`
   before any row op. ([work/H_data.md](work/H_data.md) create_curve_table / set_curve_keys)
8. **Hard-assert (crash-not-error) class** — engine utilities that `check()` instead of failing:
   `SetStreamingClassForLevel` checks `GetLoadedLevel()` non-null (EditorLevelUtils.cpp:524–525);
   BuildReflectionCaptures checks SM5 (§3 #2); PhysicsAssetUtils checks `GetResourceForRendering()`
   (§2 #17); CopyMeshFromStaticMesh range-check-asserts on cooked meshes (§4);
   `UWidgetAnimation::BindPossessableObject` CastChecked's its context (§1 G2). Pre-check all of these.
9. **Silent no-op class** — engine calls that succeed while doing nothing:
   `ALandscapeProxy::EditorApplySpline` with an unknown PaintLayer silently does nothing (header says
   so, LandscapeProxy.h:865) and RETURNS WITHOUT DEFORMING on a layers-enabled landscape with an
   unresolved edit-layer name (LandscapeBlueprintSupport.cpp:26–31), plus an UNGUARDED
   `LandscapeActor.Get()` deref (crash) when no ULandscapeInfo is registered;
   `PilotLevelActor` silently no-ops with no viewport (LevelEditorSubsystem.cpp:160–173);
   `FMessageLogModule::GetLogListing` silently CREATES empty listings for typo'd names
   (MessageLogModule.h:52 — gate on IsRegisteredLogListing :49). Pre-check + structured errors.
10. **`FGrassVariety` has a non-inline unexported constructor** (LandscapeGrassType.h:33, defined in
    LandscapeGrass.cpp:1948) — `GrassVarieties.AddDefaulted()` from MifBridge will not link; add
    elements via FScriptArrayHelper reflection. ([work/F_world_level.md](work/F_world_level.md) create_grass_type)
11. Minor live oddity: `scene_report` and `list_level_actors` disagree by one on the empty world
    (bounds walk sees an actor GetAllLevelActors filters out) — harmless, remember it when
    cross-checking counts (LIVE_PROBES I).
12. **Two editor instances race on the project** — a second UnrealEditor on the same .uproject killed
    BOTH instances without crash records (AssetSearch FileInfo.db SQLite lock). Auto-relaunch logic
    must check for an existing instance first; the log-name suffix `_2` is the first symptom, not the
    port-8791 bind failure. ([work/J_dds2_project.md](work/J_dds2_project.md) negative #10)

---

## 6. Overturned negatives — actually viable, with caveats (do NOT re-kill these)

Three Phase-1 negative results were overturned by Phase-2 evidence. Anyone re-deriving them from the
Phase-1 text alone will reach the wrong conclusion.

1. **SequencerScripting IS enabled — transitively.** Phase-1 recorded it as a dead end partly because
   "not enabled". Wrong: `LevelSequenceEditor.uplugin` (EnabledByDefault true) declares
   `"Plugins": [{"Name": "SequencerScripting", "Enabled": true}]` (:25–30), as do ControlRig and
   MovieRenderPipeline; the .uproject disables none; compiled DLLs exist and load. The ~126 extension
   UFUNCTIONs are callable TODAY via reflection (FindFunction/ProcessEvent). **Caveats that stand**:
   no export macros on any extension class (no direct C++ linking) and typed channel wrappers live in
   Private/ headers (no compile-time access) — so the exported-MOVIESCENE_API route remains the better
   engineering choice, with SequencerScripting as a live fallback/cross-check surface.
   ([work/G2_sequencer_umg_input.md](work/G2_sequencer_umg_input.md) negative #1)
2. **`GetStatistics` is not stale — it BLOCKS.** Phase-1 claimed FMaterialStatistics lags compilation
   and returns stale/zero numbers. The mechanism is the opposite: it force-submits the material's
   compile jobs and synchronously `FinishCompilation()`s them (MaterialEditingLibrary.cpp:1355–1362).
   Viable as an endpoint, **caveat**: bounded-to-one-material synchronous wait — guard with
   `IsGameThreadShaderMapComplete()` or document the block (§3 #1).
   ([work/D_materials_rendering.md](work/D_materials_rendering.md) negative #7)
3. **`USkeletalMesh::WaitUntilAsyncPropertyReleased` IS exported** (`ENGINE_API`, SkeletalMesh.h:2708;
   base helper SkinnedAsset.h:301) — so the inline `GetMorphTargets()` accessor links fine from
   external modules; Phase-1's "link trap" claim was wrong. **Caveat**: the accessor synchronously
   waits if the mesh is mid-async-compile (behavioral, not linkage — §3 #10). The FProperty-reflection
   detour remains valid but is not required. ([work/E_geometry_meshes.md](work/E_geometry_meshes.md) negative #6)

_Near-miss in the same spirit (premise refinement, not a full overturn): "premade registry leaves
mounts invisible" applies only to paks mounted AFTER boot — boot-mounted DLC containers are fully
visible to find_assets (LIVE_PROBES refined claim #9). And Phase-1's "122 *Database tables" figure
matches no live scope — authoritative census is list_datatables: 379 total / 214 under /Game/DataTables /
95 under /Game/DataTables/Databases (LIVE_PROBES refined claim #8)._

---

## 7. Bridge-side defects found during the audit (repairs to EXISTING endpoints, not new endpoints)

> **RE-CHECKED 2026-08-29.** #1, #3, #6, #7, #8 confirmed FIXED against current source (see
> `docs/audit/07_SELF_AUDIT_FINDINGS.md`'s own staleness header and `FEATURE_PARITY_SPEC.md` for the
> #3/connect_pins account specifically). #2 (add_foliage_instances) is addressed, though not exactly
> either proposed way: it now has a genuine dual mode - `foliageType` places real
> `AInstancedFoliageActor` instances via `GetInstancedFoliageActorForCurrentLevel`, `mesh` stays the
> HISM-holder shape but is honestly documented as that rather than claiming to be foliage-system
> content - both live under the one endpoint rather than a separate `paint_foliage`. #4
> (trigger_cook plan-only) and #5 (get_datatable_row's whole-table read per row) are both still
> exactly as described - #4 is a documented, understood boundary (see the GHOST_OK triage in
> tools/audit_report.py: "NOT A BUG... plan-only... Honest"), #5 is a real but unconfirmed-feasible
> perf question nobody has picked up.

| # | Defect | Evidence | Repair |
|---|---|---|---|
| 1 | **`find_assets` silently ignores unknown parameters** — live-proven twice: `{"className": ...}` returned ALL 37,131 assets with no error; `{"recursive": false}` accepted silently. The correct parameter is **`class`**, not `className` (handler reads only pathPrefix/class/nameContains/origin/recursiveClasses/limit). This is a live instance of the brief's #1 bug class in a shipped READ endpoint | MifBridgeCooked.cpp:193–198; [work/G2_sequencer_umg_input.md](work/G2_sequencer_umg_input.md) neg #8, [work/G3_niagara_audio_physics.md](work/G3_niagara_audio_physics.md) coverage log, [work/J_dds2_project.md](work/J_dds2_project.md) neg #9 | Unknown-param rejection in H_find_assets; then sweep EVERY handler for the same hole (get_property already errors correctly — the pattern exists in-house) |
| 2 | **`add_foliage_instances` is a detached-HISM impostor** — it builds a HISM holder actor, NOT AInstancedFoliageActor foliage; invisible to foliage tools, foliage stat counts, and procedural systems | MifBridgeAuthoring.cpp:428–478 (read first, axis F surface inventory) | Either repoint it at the real FFoliageInfo route or ship paint_foliage and document add_foliage_instances as "decorative HISM only" |
| 3 | **`connect_pins` hardcodes the K2 schema CDO** — `ConnectPinsChecked` uses `GetDefault<UEdGraphSchema_K2>()` for CanCreateConnection/TryCreateConnection AND BreakPinLinks, so any graph whose schema overrides these (anim graphs, state machines, widget graphs) silently gets K2 semantics: state-to-state connections never spawn transitions | MifBridgeCommon.cpp:475–478 (K2()), :1494, :1505–1506, :1509, :1515 | One-line-class fix: `Graph->GetSchema()` (EdGraph.h:115) for all three calls — [work/C_blueprints_graphs.md](work/C_blueprints_graphs.md) behaviour change, tier 0 |
| 4 | **`trigger_cook` is plan-only** — returns `executed:false` + a 6-step retoc plan; cooks NOTHING; no status poll exists because there is no process. Both live-install constants are hardcoded (`GameRoot`, `RetocExe`) — path drift on another machine silently breaks read_modloader_log's default too | MifBridgePipeline.cpp:96–142 (:108 executed:false), :16–17 | Real execution lane = mod_package_request/_status (axis J proposal); at minimum make the constants configurable and the plan-only nature LOUD in server.py docs |
| 5 | **`get_datatable_row` is O(whole table) per row** — serialises the entire table via GetTableAsJSON then linear-scans for the row; quadratic for agents looping rows on large tables | MifBridgeDataTables.cpp:135–153 | Serialise just the requested row (FDataTableExporterJSON has per-row paths — signature verification still open, J UNVERIFIED) |
| 6 | **`describe_class` and `list_enum_values` sit in the transacted bucket** — read-shaped endpoints not in IsReadOnlyEndpoint, so every call pushes an undo entry (undo-stack pollution; not a policy contradiction, but exactly what the read-only bucket exists to prevent) | 00_BASELINE.md registry-health legend (\* entries) + PROGRESS.md 2026-07-26 baseline notes | Move both to IsReadOnlyEndpoint in MifBridgeCommon.cpp |
| 7 | **server.py is missing `diagnose_landscape_draws`** — 159 MCP tools vs 160 source endpoints; the three-way registry (MIF_DECL/MIF_BIND/@mcp.tool) has one drift | 00_BASELINE.md:26; PROGRESS.md:12 | Add the @mcp.tool wrapper |
| 8 | **Live DLL ran 4 endpoints behind source** — `set_viewport_camera`, `get_viewport_camera`, `focus_viewport`, `spawn_actor_in_pie` were in source (160) but not the running DLL (156) pending rebuild. **Status**: the 2026-07-26 rebuild landed — self_audit now reports endpointCount 160, healthy, 0 policy contradictions (LIVE_PROBES Probe 0). Verify with one self_audit at implementation-session start before trusting endpoint availability | 00_BASELINE.md:23–25; [work/LIVE_PROBES.md](work/LIVE_PROBES.md) Probe 0 | Ritual: `self_audit` first, every session |

Related quality notes for the same repair pass: `landscape_info` iterates `TActorIterator<ALandscape>`
only — widen to ALandscapeProxy to see streaming proxies (one-line, [work/F_world_level.md](work/F_world_level.md)
compositions); get_property/list_object_properties/get_datatable_row demand exact param names
(`objectPath`/`propertyPath`/`rowName`) and self-document via error strings — the good pattern to
propagate (LIVE_PROBES bridge-quality notes); describe_class returns ALL inherited members — grow an
`ownOnly` flag or Phase-3 tooling must diff against the parent class (LIVE_PROBES).

---

## How to use this file

Before implementing any endpoint from 01_CATALOGUE / 02_RANKED, check it against this file in order:
§1 tells you if the "obvious" engine API is a dead end and what the verified alternative is; §2 and §3
list the modal and blocking traps whose mitigations are load-bearing parts of the endpoint specs (an
implementation that skips a pre-check listed here is wrong even if it appears to work); §4 defines
the cooked-content behaviour your endpoint must declare (refuse / degrade-with-flag / works — never
silent-empty); §5 is the sharp-edge checklist to re-read whenever you touch dynamic meshes, LOD
properties, editor subsystems during PIE, curves/curve tables, landscape splines, or anything
enum-shaped that might differ from 5.4 knowledge; §6 protects three capabilities from being wrongly
re-killed by stale Phase-1 text; and §7 is the standing repair list for endpoints that already ship —
fix those in the same passes that add neighbours, and start every session with `self_audit`. Every
claim here carries a file:line — if your reading of the engine disagrees with a citation, re-open the
cited line in D:/UE532 before trusting either; the axis files under [work/](work/) hold the full
per-endpoint context these one-liners compress.
