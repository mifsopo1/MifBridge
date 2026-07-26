# Axis B — Assets and the registry
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._

## Surface inventory

- `Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h` read (lines 40–460): dependency/referencer
  API (GetDependencies L353/354/364, K2_GetDependencies L373–374, GetReferencers L384/385/395,
  K2_GetReferencers L404–405), tag queries (GetAssetsByTags L223, GetAssetsByTagValues L231),
  class-hierarchy queries (GetAncestorClassNames L425–426, GetDerivedClassNames L432–433),
  redirector chase (GetRedirectedObjectPath L419), package data (TryGetAssetPackageData L307,
  GetAssetPackageDataCopy L408, EnumerateAllPackages L414, DoesPackageExistOnDisk L416),
  enumeration (EnumerateAssets L254–257, EnumerateAllAssets L324, GetAssetsByPath L192,
  GetAssetsByPaths L202, GetAssetsByClass L212, GetAllCachedPaths L440).
  `FAssetRegistryDependencyOptions` (L72–100) is the BlueprintType options struct;
  `FAssetDependency` (L103–142) carries Category + Properties per edge.
- `Source/MifBridge/Private/MifBridgeCooked.cpp` read in full for the find_assets baseline:
  H_find_assets (L191–272) returns { path, name, class, package, origin(container|loose), loaded }
  filtered by class / pathPrefix / nameContains / origin / recursiveClasses / limit. It does NOT
  return: asset tags, dependencies, referencers, package size/disk data, redirector resolution.
  H_describe_package (L278+) covers per-package origin/exports. H_list_mounted_containers covers
  container mount status.
- `Source/MifBridge/Private/MifBridgeNodes2.cpp` H_create_blueprint (L1063+): allowlist =
  Normal | FunctionLibrary | Interface | MacroLibrary | WidgetBlueprint (L1089–1091). NOT covered:
  AnimBlueprint, EditorUtilityBlueprint/Widget, and all non-Blueprint asset types.
- `Developer/AssetTools/Public/IAssetTools.h` read: CreateAsset L305, CreateAssetWithDialog L333/L336
  (UI — rejected), DuplicateAsset L351, RenameAssets L367, RenameAssetsWithDialog L371 (UI),
  ImportAssets L419, ImportAssetsAutomated L428, ImportAssetTasks L436, ExportAssets L445/L453,
  ExportAssetsWithDialog L462/L470 (UI), MigratePackages L516/L520, BeginAdvancedCopyPackages
  L529/L532, FixupReferencers L538, IsFixupReferencersInProgress L541, ERedirectFixupMode L66–73,
  `ASSETTOOLS_API static IAssetTools& Get();` L247.
- **UFactory subclass enumeration** (`grep ': public UFactory'` over *.h):
  **Engine/Source: 108 declarations in 103 files**; **Engine/Plugins: 188 declarations in 174 files**
  (≈296 total). Direct subclasses in Engine/Source (unique class names, 103):
  UAnimBlueprintFactory UAnimBoneCompressionSettingsFactory UAnimCompositeFactory
  UAnimCurveCompressionSettingsFactory UAnimMontageFactory UAnimSequenceFactory UAnimStreamableFactory
  UAudioBusFactory UBehaviorTreeFactory UBlackboardDataFactory UBlendSpaceFactory1D UBlendSpaceFactoryNew
  UBlueprintFactory UCSVImportFactory UCanvasRenderTarget2DFactoryNew UCompositeCurveTableFactory
  UCurveFactory UCurveImportFactory UCurveLinearColorAtlasFactory UCurveTableFactory UDataAssetFactory
  UDataLayerFactory UDataTableFactory UDialogueVoiceFactory UDialogueWaveFactory
  UEditorUtilityBlueprintFactory UEditorUtilityWidgetBlueprintFactory UEndpointSubmixFactory UEnumFactory
  UFbxFactory UFoliageType_ActorFactory UFoliageType_InstancedStaticMeshFactory UFontFactory
  UFontFileImportFactory UForceFeedbackAttenuationFactory UForceFeedbackEffectFactory UHLODLayerFactory
  UHapticFeedbackEffectBufferFactory UHapticFeedbackEffectCurveFactory UHapticFeedbackEffectSoundWaveFactory
  ULandscapeGrassTypeFactory ULandscapeLayerInfoObjectFactory ULevelFactory ULightWeightInstanceFactory
  UMaterialFactoryNew UMaterialFunctionFactoryNew UMaterialFunctionInstanceFactory
  UMaterialFunctionMaterialLayerBlendFactory UMaterialFunctionMaterialLayerFactory
  UMaterialInstanceConstantFactoryNew UMaterialParameterCollectionFactoryNew UMirrorDataTableFactory
  UModelFactory UObjectLibraryFactory UPackFactory UPackageFactory UParticleSystemFactoryNew
  UPhysicalMaterialFactoryNew UPhysicalMaterialMaskFactory UPhysicsAssetFactory UPolysFactory
  UPoseAssetFactory UPreviewMeshCollectionFactory UProceduralFoliageSpawnerFactory UReverbEffectFactory
  URuntimeVirtualTextureFactory USceneImportFactory USkeletonFactory USlateBrushAssetFactory
  USlateVectorArtDataFactory USlateWidgetStyleAssetFactory USoundAttenuationFactory USoundClassFactory
  USoundConcurrencyFactory USoundCueFactoryNew USoundFactory USoundMixFactory USoundSourceBusFactory
  USoundSourceEffectChainFactory USoundSourceEffectFactory USoundSubmixEffectFactory USoundSubmixFactory
  USoundfieldEndpointSubmixFactory USoundfieldSubmixFactory USparseVolumeTextureFactory
  USpecularProfileFactory UStringTableFactory UStructureFactory USubUVAnimationFactory
  USubsurfaceProfileFactory UTexture2DArrayFactory UTexture2DFactoryNew UTextureCubeArrayFactory
  UTextureFactory UTextureRenderTarget2DArrayFactoryNew UTextureRenderTargetCubeFactoryNew
  UTextureRenderTargetFactoryNew UTextureRenderTargetVolumeFactoryNew UTouchInterfaceFactory
  UVariableFrameStrippingSettingsFactory UVectorFieldStaticFactory UVirtualTextureBuilderFactory
  UVolumeTextureFactory UWidgetBlueprintFactory UWorldFactory
  (plus second-level subclasses not matching the direct-parent grep: UCurveFloatFactory /
  UCurveLinearColorFactory / UCurveVectorFactory — CurveFactory.h:36/51/66 — and 3 OverlayEditor
  factories with line-wrapped declarations). Plugin factories (174 files) notable for this project:
  ControlRigBlueprintFactory, GameplayAbilitiesBlueprintFactory, Niagara* (8), LevelSequenceFactoryNew,
  MetasoundFactory, PCGGraphFactory (x2), StateTreeFactory, EnhancedInput factories
  (InputEditorModule.h x3), Paper2D (7), audio-plugin factories (~20) — none needed for the tier-1 set.
- `Editor/UnrealEd/Public/AssetImportTask.h` read in full (UAssetImportTask, all 10 option fields +
  3 result members, lines 24–102).
- `Runtime/Engine/Classes/Exporters/Exporter.h` swept: FindExporter L186, ExportToFile L201,
  ExportToArchive L213, RunAssetExportTask L267, RunAssetExportTasks L288 (all ENGINE_API);
  `Runtime/Engine/Public/AssetExportTask.h` read in full (all 11 fields, L13–65).
  **UExporter subclasses: 28 declarations in 27 files**, all under Editor/UnrealEd/Classes/Exporters/
  (TextureExporterPNG, TextureExporterPCX, TextureExporterGeneric, TextureCubeExporterHDR,
  StaticMeshExporterOBJ/FBX, SkeletalMeshExporterFBX, AnimSequenceExporterFBX, ExporterFbx,
  SoundExporterWAV/OGG, SoundSurroundExporterWAV, ObjectExporterT3D, ModelExporterT3D,
  LevelExporterT3D/STL/OBJ/LOD/FBX, PolysExporterT3D/OBJ, SequenceExporterT3D,
  RenderTargetExporterHDR(x2), VectorFieldExporter, TextBufferExporterTXT, plus 2
  Elements copy/paste exporters in UnrealEd/Public/Elements/).
- `Engine/Plugins/Editor/DataValidation/` swept: uplugin `"EnabledByDefault" : true` (line 13),
  NOT disabled in DrugDealerSimulator2.uproject (grepped). Public headers: EditorValidatorSubsystem.h
  (read L22–176), EditorValidatorBase.h, DataValidationModule.h (EDataValidationUsecase L18–37),
  DataValidationChangelist.h, DataValidationCommandlet.h.
- `Editor/UnrealEd/Classes/Editor/EditorEngine.h` Map_Check region read (L2540–2575): Map_Check
  UNREALED_API L2569, EMapCheckNotification L2556–2564, Game_Map_Check hooks L2047/L2056.
- `Developer/MessageLog/Public/` swept: MessageLogModule.h (GetLogListing L57, IsRegisteredLogListing
  L49), IMessageLogListing.h (GetFilteredMessages L47, GetSelectedMessagesAsString L76).
- `Editor/UnrealEd/Public/FileHelpers.h`: FEditorFileUtils class L183, GetDirtyContentPackages
  L144/L409, GetDirtyWorldPackages L402.
- `Runtime/Engine/Public/AssetCompilingManager.h`: FAssetCompilingManager L105, Get L108 ENGINE_API,
  GetNumRemainingAssets L128, GetRegisteredManagers L123, FinishAllCompilation L133 (blocking —
  rejected for handlers), IAssetCompilingManager::GetNumRemainingAssets L66.
- `Editor/UnrealEd/Public/Subsystems/EditorAssetSubsystem.h` swept (UEditorAssetSubsystem lives in
  UnrealEd in 5.3.2 — NOT in the EditorScriptingUtilities plugin): LoadAsset L40, GetPathNameForLoadedAsset
  L57, DuplicateAsset L171, SaveLoadedAsset L254, SaveLoadedAssets L263, DoesDirectoryExist L292,
  ListAssets L318, ListAssetsByTagValue L327, GetTagValues L335 — all UNREALED_API, all overlapping
  existing endpoints or this file's registry proposals (see Compositions). No checksum method exists
  (grepped case-insensitively — absent in 5.3.2).
- `Developer/SourceControl/Public/ISourceControlModule.h`: class L80, GetProviderNames L87,
  IsEnabled L122, GetProvider L127. Project Config (DefaultEditor/Engine/Game/Input.ini) contains no
  source-control provider setup.
- `Editor/UnrealEd/Public/ObjectTools.h`: ConsolidateObjects L222–224 UNREALED_API,
  FConsolidationResults L176–196.
- `Runtime/CoreUObject/Public/Misc/AssetRegistryInterface.h`: EDependencyCategory L71,
  EDependencyProperty L88 (Hard/Game/Build L96–98), EDependencyQuery flags L122–131, FDependencyQuery L166.
- `Runtime/CoreUObject/Public/AssetRegistry/AssetData.h`: FAssetPackageData L893 (DiskSize L914,
  ImportedClasses L911, FileVersionUE L917, CookedHash L901, Extension L928).

## Proposed endpoints

### get_asset_dependencies
**Purpose**: Return the on-disk dependency list of a package (what it references), with hard/soft/game/build edge classification — the known big delta over find_assets, which returns no graph data at all.
**Engine API**:
```cpp
virtual bool GetDependencies(FName PackageName, TArray<FName>& OutDependencies, UE::AssetRegistry::EDependencyCategory Category = UE::AssetRegistry::EDependencyCategory::Package, const UE::AssetRegistry::FDependencyQuery& Flags = UE::AssetRegistry::FDependencyQuery()) const = 0;
virtual bool GetDependencies(const FAssetIdentifier& AssetIdentifier, TArray<FAssetDependency>& OutDependencies, UE::AssetRegistry::EDependencyCategory Category = UE::AssetRegistry::EDependencyCategory::All, const UE::AssetRegistry::FDependencyQuery& Flags = UE::AssetRegistry::FDependencyQuery()) const = 0;
```
`Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:364` and `:354`. `FAssetDependency { FAssetIdentifier AssetId; EDependencyCategory Category; EDependencyProperty Properties; }` at `IAssetRegistry.h:103–107`. Query-flag enums (`EDependencyCategory` L71, `EDependencyProperty` L88 with `Hard=0x1/Game=0x2/Build=0x4` L96–98, `EDependencyQuery` `Hard/NotHard/Soft/Game/NotGame/Build/NotBuild` L122–131, `FDependencyQuery` L166) in `Runtime/CoreUObject/Public/Misc/AssetRegistryInterface.h`.
**Export**: pure-virtual interface method — called through the vtable of the instance returned by `FAssetRegistryModule::Get()` (same pattern as H_find_assets, MifBridgeCooked.cpp:200); no export macro needed. | **Module**: none — AssetRegistry already linked. | **Guards**: none.
**Bucket**: read-only — pure registry query.
**Async**: no (in-memory map lookup).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| package | path, asset | string (package or object path; object path reduced to package like H_describe_package) | — | yes |
| filter | edgeFilter | string enum: `all`, `hard`, `soft`, `game`, `build`, `notgame` | all | no |
| includeProperties | — | bool: emit per-edge {hard, game, build} booleans from FAssetDependency.Properties | true | no |
Unrecognised parameter ⇒ error naming it.
**Failure modes**:
- Package not in registry ⇒ `"package '/Game/X' not found in asset registry — check spelling with find_assets"`.
- Container-only package (dependency data stripped by cook) ⇒ succeed but set `dependencyDataAvailable:false` with note `"cooked container package: dependency graph was stripped at cook time; results are empty by construction, not because the asset has no dependencies"` — never return a silently-empty list for these.
- Unknown `filter` value ⇒ error listing accepted values.
**Cooked**: degraded by design — the brief's documented impossible is dependency queries over cooked base-game content; this endpoint works fully on loose project packages and must self-diagnose the container case via the same `IsContainerOnlyPackage` test H_find_assets already uses (MifBridgeCooked.cpp:44–49).
**Verify**: create a test BP referencing 2 known assets (add_component with a mesh + a material), save, then `get_asset_dependencies` must list both packages; count of `hard` edges ≥ 2. Cross-check with `get_asset_referencers` from one of the targets (symmetry: A depends-on B ⇔ B referenced-by A).
**Score**: U5 E2 R5 → tier 1 (impact/refactor analysis is a standard agent need; nothing today answers "what does this asset use")
**Phase-2 verdict**: CONFIRMED — all signatures re-read verbatim (IAssetRegistry.h:364/:354; FAssetDependency struct :103 fields :105–107; AssetRegistryInterface.h enums: EDependencyCategory :71, EDependencyProperty :88 Hard/Game/Build :96–98, EDependencyQuery Hard :122–NotBuild :131, FDependencyQuery :166). Vtable-dispatch export claim correct; module already linked; bucket/async/cooked claims hold.

### get_asset_referencers
**Purpose**: Reverse edge of get_asset_dependencies — "what references this asset" — including the high-value cooked case: which LOOSE project assets reference a base-game container asset (those edges live in the loose assets' saved data, so they survive).
**Engine API**:
```cpp
virtual bool GetReferencers(FName PackageName, TArray<FName>& OutReferencers, UE::AssetRegistry::EDependencyCategory Category = UE::AssetRegistry::EDependencyCategory::Package, const UE::AssetRegistry::FDependencyQuery& Flags = UE::AssetRegistry::FDependencyQuery()) const = 0;
virtual bool GetReferencers(const FAssetIdentifier& AssetIdentifier, TArray<FAssetDependency>& OutReferencers, UE::AssetRegistry::EDependencyCategory Category = UE::AssetRegistry::EDependencyCategory::All, const UE::AssetRegistry::FDependencyQuery& Flags = UE::AssetRegistry::FDependencyQuery()) const = 0;
```
`Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:395` and `:385`.
**Export**: pure-virtual interface method via module vtable — no export needed. | **Module**: none — AssetRegistry linked. | **Guards**: none.
**Bucket**: read-only.
**Async**: no.
**Params**: same table as get_asset_dependencies (package | filter | includeProperties); plus `origin` filter (`any`/`loose`/`container`, default any) applied to the RESULTS so an agent can ask "which of MY assets reference this" directly.
**Failure modes**: same as get_asset_dependencies, with the inverse cooked caveat: referencers FROM container packages are absent (their edge data was stripped), so a container asset's referencer list only shows loose referencers — response carries `note:"container-side referencers invisible (stripped at cook)"` whenever the queried package or any result is container-adjacent.
**Cooked**: partially works and that partial is the point — loose→container edges are recorded in the loose packages and ARE returned when querying the container package as target. Container→anything edges are gone.
**Verify**: after the get_asset_dependencies test asset exists: `get_asset_referencers` on the referenced mesh package must include the test BP package (count ≥ 1). Delete the test BP (delete_asset) → count drops by exactly 1.
**Score**: U5 E2 R5 → tier 1 (pairs with delete_asset: "is anything still using this?" before destructive ops)
**Phase-2 verdict**: CONFIRMED — both signatures verbatim at IAssetRegistry.h:395 and :385; export/module/bucket/cooked reasoning verified.

### find_assets_by_tag
**Purpose**: Query the registry by asset-registry tag/value pairs — e.g. all DataTables with `RowStructure=<X>`, all Blueprint assets with `NativeParentClass=<Y>`, all assets with `ParentClass` set — a search axis find_assets (class/path/name only) cannot express.
**Engine API**:
```cpp
virtual bool GetAssetsByTags(const TArray<FName>& AssetTags, TArray<FAssetData>& OutAssetData) const = 0;
virtual bool GetAssetsByTagValues(const TMultiMap<FName, FString>& AssetTagsAndValues, TArray<FAssetData>& OutAssetData) const = 0;
```
`Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:223` and `:231`. (Alternative for combined class+path+tag filters: `FARFilter.TagsAndValues` — same header, GetAssets L243, as already used by H_find_assets.)
**Export**: pure-virtual via module vtable. | **Module**: none. | **Guards**: none.
**Bucket**: read-only.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| tag | tagName | string | — | yes |
| value | tagValue | string; omitted ⇒ any value (GetAssetsByTags path) | — | no |
| class | — | string, optional additional class filter (FARFilter route) | — | no |
| pathPrefix | — | string | — | no |
| limit | — | int 1–5000 | 100 | no |
Output rows: same shape as find_assets plus the matched tag's value.
**Failure modes**:
- No assets carry the tag ⇒ `count:0` plus `knownTags` sample (from a bounded EnumerateAssets pass) so the agent can discover real tag names instead of guessing.
- Tag name with spaces/invalid FName ⇒ error naming the parameter.
**Cooked**: works to the extent the mounted cooked registry serialized tags — cooked registries do carry tags (that is how the content browser displays cooked asset metadata), but editor-only tags may be stripped; the endpoint reports counts so absence is measurable, and get_asset_tags (below) lets the agent inspect what a specific cooked asset actually carries.
**Verify**: `find_assets_by_tag {tag:"RowStructure"}` count must equal the number of DataTables reported by find_assets class=DataTable that have a row struct; create one DataTable with a known RowStruct → count increments by 1 and the new row's value matches the struct path.
**Score**: U4 E2 R5 → tier 1 (the only way to find "all DataTables of struct X" / "all BPs whose native parent is Y" without loading everything)
**Phase-2 verdict**: CONFIRMED — GetAssetsByTags at IAssetRegistry.h:223 and GetAssetsByTagValues at :231 verbatim; the FARFilter alternative (GetAssets :243) also re-verified. No hidden hazards (pure in-memory query).

### get_asset_tags
**Purpose**: Dump the full asset-registry tag/value map for one asset (RowStructure, ParentClass, NativeParentClass, GeneratedClass, BlueprintType, thumbnails dims, etc.) without loading it — the metadata find_assets throws away.
**Engine API**:
```cpp
virtual FAssetData GetAssetByObjectPath(const FSoftObjectPath& ObjectPath, bool bIncludeOnlyOnDiskAssets = false, bool bSkipARFilteredAssets = true) const = 0;
```
`Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:289`. Tag iteration via `FAssetData::TagsAndValues` (see AssetData.h — enumerate with `for (const auto& Pair : Asset.TagsAndValues)` exactly as engine callers do; FAssetData is ASSETREGISTRY-owned value type, fully usable).
**Export**: pure-virtual via module vtable; FAssetData is a public value struct. | **Module**: none. | **Guards**: none.
**Bucket**: read-only.
**Async**: no.
**Params**: | asset | path, objectPath | string object path | — | yes |. Unrecognised ⇒ error.
**Failure modes**: invalid/unknown object path ⇒ `"asset '<path>' not found in registry — object path form is /Game/Pkg/Name.Name"`.
**Cooked**: works — returns whatever tags the mounted registry has for container assets; the honest source of truth for "what metadata survived the cook".
**Verify**: on a known project DataTable, response must contain key `RowStructure` whose value equals the struct path shown by read_datatable; tag count > 0 for any Blueprint asset (ParentClass at minimum).
**Score**: U4 E1 R5 → tier 1 (also the debug view for find_assets_by_tag)
**Phase-2 verdict**: CONFIRMED — GetAssetByObjectPath(FSoftObjectPath) verbatim at IAssetRegistry.h:289 (the pure-virtual, not the K2 wrapper at :279); TagsAndValues iteration is the standard engine pattern. Consider TryGetAssetByObjectPath (:298, returns EExists) for a cleaner not-found-vs-scanning error split.

### get_class_hierarchy
**Purpose**: Registry-level class inheritance queries — all classes derived from X (including unloaded cooked BlueprintGeneratedClasses) and the ancestor chain of X — describe_class only works on loaded classes and cannot enumerate downward.
**Engine API**:
```cpp
virtual void GetDerivedClassNames(const TArray<FTopLevelAssetPath>& ClassNames, const TSet<FTopLevelAssetPath>& ExcludedClassNames, TSet<FTopLevelAssetPath>& OutDerivedClassNames) const = 0;
virtual bool GetAncestorClassNames(FTopLevelAssetPath ClassPathName, TArray<FTopLevelAssetPath>& OutAncestorClassNames) const = 0;
```
`Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:433` and `:426` (header comment L424/L431: "can be slow if temporary caching mode is not on" — acceptable, this is a single registry pass, still same-frame).
**Export**: pure-virtual via module vtable. | **Module**: none. | **Guards**: none.
**Bucket**: read-only.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| class | className | string (bare name resolved via ResolveClass, or /Script/... path, or /Game/....X_C) | — | yes |
| direction | — | `derived` \| `ancestors` \| `both` | derived | no |
| excludeClasses | — | array of class paths to prune subtrees | [] | no |
| limit | — | int | 500 | no |
**Failure modes**: unresolvable class ⇒ strict error naming param (ResolveClassStrict precedent); direction typo ⇒ error listing the three values.
**Cooked**: works — this is the headline cooked win: the class-inheritance map includes container BPGCs that were never loaded, so "list every subclass of the game's BaseNPC class" works over pure cooked content.
**Verify**: `get_class_hierarchy {class:"Actor", direction:"derived"}` count must be ≥ the number of blueprints returned by find_assets class=Blueprint; for a leaf class, `ancestors` must end at /Script/CoreUObject.Object and its length must equal the chain shown by describe_class on the loaded class.
**Score**: U5 E2 R5 → tier 1 (unlocks "find all NPC subclasses in base game" — a real DDS2 modding workflow)
**Phase-2 verdict**: CONFIRMED — GetDerivedClassNames verbatim at IAssetRegistry.h:433, GetAncestorClassNames at :426; "can be slow if temporary caching mode is not on" comments re-read at :431/:424. Deprecated FName overloads (:429/:436) correctly avoided.

### resolve_redirector
**Purpose**: Chase ObjectRedirectors to the live object path (registry-level, no load) — after rename_asset the old path silently points at a redirector and every endpoint that takes an objectPath can be fed a stale path.
**Engine API**:
```cpp
virtual FSoftObjectPath GetRedirectedObjectPath(const FSoftObjectPath& ObjectPath) const = 0;
```
`Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:419` (comment: "will follow the chain of redirectors. It will return the original path if no redirectors are found").
**Export**: pure-virtual via module vtable. | **Module**: none. | **Guards**: none.
**Bucket**: read-only.
**Async**: no.
**Params**: | path | asset, objectPath | string | — | yes |. Output: { input, resolved, wasRedirected(bool) }.
**Failure modes**: nonexistent path returns input unchanged — endpoint distinguishes `wasRedirected:false, existsInRegistry:false` so a typo isn't mistaken for "no redirector".
**Cooked**: works; cooked content rarely contains redirectors (cook resolves them) so mostly relevant to loose project assets.
**Verify**: rename_asset a test asset → resolve_redirector on the OLD path must return the NEW path with wasRedirected:true; on the new path returns itself with false.
**Score**: U3 E1 R5 → tier 1 (tiny, removes a whole confusion class after renames)
**Phase-2 verdict**: CONFIRMED — GetRedirectedObjectPath verbatim at IAssetRegistry.h:419, header comment quoted accurately; deprecated FName overload at :422 correctly avoided.

### list_content_paths
**Purpose**: Enumerate the content folder tree (registry-cached paths) — find_assets needs a known pathPrefix; nothing today answers "what folders exist under /Game".
**Engine API**:
```cpp
virtual void GetSubPaths(const FString& InBasePath, TArray<FString>& OutPathList, bool bInRecurse) const = 0;
```
`Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:450`.
**Export**: pure-virtual via module vtable. | **Module**: none. | **Guards**: none.
**Bucket**: read-only. **Async**: no.
**Params**: | base | basePath, path | string | `/Game` | no | ; | recursive | — | bool | false | no | ; | limit | — | int | 1000 | no |.
**Failure modes**: base path not cached ⇒ empty list + `note:"path not in registry cache — check spelling; valid roots: /Game, /Engine, /<PluginName>"`.
**Cooked**: works — mounted container content contributes registry paths.
**Verify**: count of non-recursive sub-paths of /Game must equal the number of distinct first path segments over find_assets pathPrefix=/Game results.
**Score**: U3 E1 R5 → tier 1
**Phase-2 verdict**: CONFIRMED — GetSubPaths(FString) verbatim at IAssetRegistry.h:450 (FName overload at :453, EnumerateSubPaths :456/:459 available for large trees).

### get_package_disk_data
**Purpose**: Per-package physical data from the registry — disk size, file version, imported classes, extension — "how big is this asset" without touching the filesystem.
**Engine API**:
```cpp
virtual UE::AssetRegistry::EExists TryGetAssetPackageData(FName PackageName, FAssetPackageData& OutAssetPackageData) const = 0;
```
`Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:307`. `class FAssetPackageData` at `Runtime/CoreUObject/Public/AssetRegistry/AssetData.h:893` — `int64 DiskSize` :914, `TArray<FName> ImportedClasses` :911, `FPackageFileVersion FileVersionUE` :917, `FMD5Hash CookedHash` :901, `EPackageExtension Extension` :928.
**Export**: pure-virtual via module vtable; FAssetPackageData is a public CoreUObject value type. | **Module**: none. | **Guards**: none.
**Bucket**: read-only. **Async**: no.
**Params**: | package | path, asset | string | — | yes |.
**Failure modes**: EExists::DoesNotExist ⇒ error naming package; EExists::Unknown (registry still scanning) ⇒ distinct error `"registry scan in progress — retry"`.
**Cooked**: degraded — package data is "only updated on save" (header comment at IAssetRegistry.h:407); mounted cooked registries may omit it ⇒ report `available:false` rather than zeros.
**Verify**: for a loose asset, DiskSize must equal the .uasset file size on disk (±0; exact match expected for single-file packages).
**Score**: U2 E1 R5 → tier 2 (nice-to-have; ImportedClasses is a free bonus signal for reconstructing stripped BPs)
**Phase-2 verdict**: CONFIRMED — TryGetAssetPackageData verbatim at IAssetRegistry.h:307; FAssetPackageData class at AssetData.h:893 with CookedHash :901, ImportedClasses :911, DiskSize :914, FileVersionUE :917, Extension :928 all re-read; "only updated on save" comment at IAssetRegistry.h:407 accurate.

### create_asset
**Purpose**: Mint non-Blueprint assets (DataTable-with-RowStruct, curves, DataAssets, StringTable, MaterialParameterCollection, PhysicalMaterial, AnimBlueprint) — create_blueprint's allowlist is Normal/FunctionLibrary/Interface/MacroLibrary/WidgetBlueprint only (MifBridgeNodes2.cpp:1089–1091), and nothing else in the 159 creates a plain asset.
**Engine API**:
```cpp
virtual UObject* CreateAsset(const FString& AssetName, const FString& PackagePath, UClass* AssetClass, UFactory* Factory, FName CallingContext = NAME_None) = 0;
```
`Developer/AssetTools/Public/IAssetTools.h:305`; `ASSETTOOLS_API static IAssetTools& Get();` at :247 (class `IAssetTools` :242, UINTERFACE MinimalApi :236). CreateAsset does NOT call ConfigureProperties (that is the WithDialog variant at :333) — factory config properties are set directly on the NewObject'd factory instance before the call.
**Factory allowlist** (every class + config property read from its header):
| assetType | Factory class | file:line (Editor/) | config property (verbatim) |
|---|---|---|---|
| DataTable | UDataTableFactory | UnrealEd/Classes/Factories/DataTableFactory.h:13 | `TObjectPtr<const class UScriptStruct> Struct;` :18 — REQUIRED rowStruct param |
| CurveFloat | UCurveFloatFactory | UnrealEd/Classes/Factories/CurveFactory.h:36 | — (base UCurveFactory `TSubclassOf<UCurveBase> CurveClass;` :23 preset by subclass ctor) |
| CurveVector | UCurveVectorFactory | CurveFactory.h:66 | — |
| CurveLinearColor | UCurveLinearColorFactory | CurveFactory.h:51 | — |
| DataAsset | UDataAssetFactory | UnrealEd/Classes/Factories/DataAssetFactory.h:14 | `TSubclassOf<UDataAsset> DataAssetClass;` :19 — REQUIRED class param |
| StringTable | UStringTableFactory | UnrealEd/Classes/Factories/StringTableFactory.h:11 | — |
| MaterialParameterCollection | UMaterialParameterCollectionFactoryNew | UnrealEd/Classes/Factories/MaterialParameterCollectionFactoryNew.h:15 | — |
| PhysicalMaterial | UPhysicalMaterialFactoryNew | UnrealEd/Classes/Factories/PhysicalMaterialFactoryNew.h:18 | `TSubclassOf<UPhysicalMaterial> PhysicalMaterialClass;` :23 (default UPhysicalMaterial) |
| AnimBlueprint | UAnimBlueprintFactory | UnrealEd/Classes/Factories/AnimBlueprintFactory.h:17 | `TSubclassOf<class UAnimInstance> ParentClass;` :27, `TObjectPtr<class USkeleton> TargetSkeleton;` :31, `bool bTemplate;` :39 — REQUIRED skeleton param (or template:true) |
All are `UCLASS(MinimalAPI)` in UnrealEd: `NewObject<T>()` links via the MinimalAPI-exported StaticClass, UPROPERTY writes are direct member access, and FactoryCreateNew is invoked virtually inside CreateAsset — no unexported symbol is ever called (precedent: ContentBrowser constructs these same factories cross-module). UNREALED_API appears on UDataTableFactory::FactoryCreateNew (DataTableFactory.h:22) and UStringTableFactory::FactoryCreateNew (StringTableFactory.h:16) anyway.
**Export**: ASSETTOOLS_API (Get) + vtable. | **Module**: none — AssetTools + UnrealEd already linked. | **Guards**: none (MifBridge is editor-only).
**Bucket**: self-managed — creates+registers a new UObject/package; must not sit in the blanket transaction (delete-on-undo of a freshly created package is the documented crash shape). AnimBlueprint additionally compiles on first open; treat like create_blueprint (registered self-managed).
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| path | — | string /Game/... (package path + asset name, create_blueprint style) | — | yes |
| assetType | type | string, one of the allowlist column 1 (exact error listing valid values otherwise) | — | yes |
| rowStruct | struct | string struct path — DataTable only | — | when DataTable |
| class | assetClass | string — DataAsset/PhysicalMaterial subclass | — | when DataAsset |
| skeleton | targetSkeleton | string object path — AnimBlueprint only | — | when AnimBlueprint unless template:true |
| parentClass | — | string — AnimBlueprint only (default AnimInstance) | AnimInstance | no |
| overwrite | — | bool | false | no |
Per-type params passed for the wrong type ⇒ error naming the parameter and the type that accepts it (silent-ignore ban).
**Failure modes**:
- rowStruct unresolvable ⇒ `"rowStruct 'X' not found — pass a full path like /Game/Data/FMyRow.FMyRow or /Script/Module.Struct"` (resolve_struct precedent).
- Package already exists && !overwrite ⇒ error suggesting overwrite:true or a new path.
- CreateAsset returns nullptr ⇒ surface the factory name + asset class in the error.
- skeleton omitted for AnimBlueprint ⇒ error: `"skeleton is required (or pass template:true) — find one with find_assets class=Skeleton"`.
**Cooked**: creates loose project assets only (by definition); a cooked skeleton/struct CAN be referenced (rowStruct from a mounted container resolves — UDataTableFactory only needs the UScriptStruct pointer).
**Verify**: DataTable: create → write_datatable_rows 2 rows → read_datatable returns 2 rows of the right struct. Curves: list_object_properties on the new asset shows FloatCurve. AnimBlueprint: compile endpoint returns 0 errors; describe_class shows parent.
**Score**: U5 E3 R3 → tier 1 (whole categories of authoring — tables/curves/configs — currently impossible)
**Phase-2 verdict**: CORRECTED — all factory citations re-verified verbatim (DataTableFactory.h:13/:18, CurveFactory.h:23/:36/:51/:66 with CurveClass preset in subclass ctors confirmed at EditorFactories.cpp:7276–7281, DataAssetFactory.h:14/:19, StringTableFactory.h:11, MaterialParameterCollectionFactoryNew.h:15, PhysicalMaterialFactoryNew.h:18/:23, AnimBlueprintFactory.h:17/:27/:31/:39; IAssetTools.h:305/:247) — but two HIDDEN MODAL hazards were missed and are now part of the spec:
(1) `IAssetTools::CreateAsset` → `UAssetToolsImpl::CanCreateAsset` (Developer/AssetTools/Private/AssetTools.cpp:4287) opens `FMessageDialog` Ok on invalid asset/package name (:4294, :4321) and on map-name collision (:4301), and a **YesNo "replace existing object?" modal (:4332) whenever the target object already exists**. The handler MUST pre-validate (FName::IsValidObjectName + FPackageName::IsValidLongPackageName + registry existence check) BEFORE calling; `overwrite:true` cannot be implemented by letting the engine prompt — delete the existing asset first (delete_asset route) or error.
(2) `UAnimBlueprintFactory::FactoryCreateNew` opens `FMessageDialog` (AnimBlueprintFactory.cpp:454–459) when ParentClass is null / fails CanCreateBlueprintOfClass / is not a UAnimInstance child — pre-validate parentClass strictly. It also runs FKismetEditorUtilities::CreateBlueprint and may CompileBlueprint (:464/:470), reinforcing the self-managed bucket.
With pre-validation both modals are unreachable; without it Risk reads R2, not R3. Overlap note: axis H proposes validating creators (create_datatable, create_curve, create_curve_table, create_string_table) and axis D proposes create_material etc. — same factory surface, not resolved here.

### import_asset
**Purpose**: Import a disk file (PNG/TGA→Texture2D, FBX→StaticMesh/SkeletalMesh, WAV→SoundWave) as a project asset — the bridge currently has no way to get external content in.
**Engine API**:
```cpp
virtual void ImportAssetTasks(const TArray<UAssetImportTask*>& ImportTasks) = 0;
```
`Developer/AssetTools/Public/IAssetTools.h:436`. Task object `UCLASS(Transient, BlueprintType, MinimalAPI) class UAssetImportTask` — `Editor/UnrealEd/Public/AssetImportTask.h:24–25`; fields verbatim: `FString Filename;` :36, `FString DestinationPath;` :40, `FString DestinationName;` :44, `bool bReplaceExisting;` :48, `bool bReplaceExistingSettings;` :52, `bool bAutomated;` :56, `bool bSave;` :60, `bool bAsync;` :64, `TObjectPtr<UFactory> Factory;` :68, `TObjectPtr<UObject> Options;` :72; results `UNREALED_API const TArray<UObject*>& GetObjects() const;` :82 (doc :78: "if the import was asynchronous, this will block until the results are ready" — NEVER call before IsAsyncImportComplete), `UNREALED_API bool IsAsyncImportComplete() const;` :89, `TArray<FString> ImportedObjectPaths;` :93.
**Per-format factory allowlist**:
| format | Factory | file:line | notes |
|---|---|---|---|
| png/tga/bmp/jpg/exr/hdr | UTextureFactory | Editor/UnrealEd/Classes/Factories/TextureFactory.h:49–50 (MinimalAPI) | synchronous |
| fbx (static) | UFbxFactory | Editor/UnrealEd/Classes/Factories/FbxFactory.h:16–17 (MinimalAPI); options `TObjectPtr<class UFbxImportUI> ImportUI;` :22 | UFbxImportUI (FbxImportUI.h:97–98): `TEnumAsByte<enum EFBXImportType> MeshTypeToImport;` :113, `bool bImportAsSkeletal;` :121, `TObjectPtr<class UFbxStaticMeshImportData> StaticMeshImportData;` :202, `TObjectPtr<class UFbxSkeletalMeshImportData> SkeletalMeshImportData;` :206 |
| fbx (skeletal) | UFbxFactory + ImportUI->bImportAsSkeletal=true | same | skeleton reuse via SkeletalMeshImportData |
| wav | USoundFactory | Editor/AudioEditor/Classes/Factories/SoundFactory.h:25–26 (MinimalAPI) | NEW module dep: AudioEditor (editor-only engine module) |
**Export**: vtable via IAssetTools::Get(); UNREALED_API on the two task result methods. | **Module**: AudioEditor NEW (only if wav support wanted; texture+FBX need nothing new). | **Guards**: none.
**Bucket**: self-managed — creates packages, triggers texture/mesh compilation; must not be inside the blanket transaction.
**Async**: default sync (`bAutomated=true, bAsync=false` — ImportAssetTasks returns with GetObjects() ready; a large FBX makes one long frame, which is legal — no cross-frame waiting). Optional `async:true` design: keep the rooted UAssetImportTask in a handle map → `import_asset_status {handle}` polls `IsAsyncImportComplete()` and reports `ImportedObjectPaths` when done, plus FAssetCompilingManager remaining count (below) since texture/mesh compile continues after import returns.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| file | filename, sourcePath | absolute disk path | — | yes |
| destination | destinationPath, path | /Game/... folder | — | yes |
| name | destinationName | string | file stem | no |
| format | type | auto-detected from extension; explicit value overrides | auto | no |
| skeletal | — | bool (fbx only) | false | no |
| replaceExisting | overwrite | bool | false | no |
| save | — | bool (task bSave) | false | no |
| async | — | bool | false | no |
**Failure modes**: file missing ⇒ error with the path checked; unknown extension ⇒ error listing supported formats; import produced 0 objects ⇒ error including task->Errors-equivalent (log capture) rather than a silent empty success; destination not /Game ⇒ error.
**Cooked**: N/A — always creates loose assets; can overwrite-shadow a container path only if the mount rules allow it (do not promise; refuse destinations that collide with container-only packages and say why).
**Verify**: import a known 64×64 PNG → get_property on the new texture: SizeX==64, SizeY==64. FBX cube → get_actor_bounds after spawn_actor_in_level matches known extents; find_assets shows the new asset with origin:"loose".
**Score**: U5 E3 R3 → tier 1 (unlocks external-content round-trip; pairs with get_asset_compilation_status)
**Phase-2 verdict**: CONFIRMED — UAssetImportTask fields all verbatim (AssetImportTask.h:36–:93, GetObjects :82 with the blocking doc at :78, IsAsyncImportComplete :89); ImportAssetTasks at IAssetTools.h:436; factory headers verified (TextureFactory.h:49–50, FbxFactory.h:16–17/:22/:59, FbxImportUI.h:97–98/:113/:121/:202/:206, SoundFactory.h:25–26 in AudioEditor). Implementation checks strengthen the design: `ImportAssetsInternal` sets `TGuardValue<bool>(GIsRunningUnattendedScript, …|| Params.bAutomated)` (AssetTools.cpp:3045), so bAutomated=true genuinely suppresses interactive dialogs; Interchange is bypassed only when Task->Factory is explicitly set (AssetTools.cpp:3068–3071) — the entry always sets Factory, keep it that way or PNG/FBX may route to async Interchange unexpectedly. `ImportAssetTasks` shows a non-modal FScopedSlowTask progress dialog (AssetTools.cpp:2485–2486) — acceptable, one long frame.

### export_asset
**Purpose**: Write an asset to a disk file (texture→PNG, static mesh→OBJ/FBX, sound→WAV, object/level→T3D) — the read-side of round-tripping; lets an agent diff content outside the editor.
**Engine API**:
```cpp
static ENGINE_API bool RunAssetExportTask( class UAssetExportTask* Task );
ENGINE_API static UExporter* FindExporter( UObject* Object, const TCHAR* Filetype );
```
`Runtime/Engine/Classes/Exporters/Exporter.h:267` and `:186` (class UExporter :22, UCLASS(abstract, MinimalAPI) with per-method ENGINE_API). Task `UCLASS(Transient, BlueprintType, MinimalAPI) class UAssetExportTask` — `Runtime/Engine/Public/AssetExportTask.h:13–14`; fields verbatim: `TObjectPtr<UObject> Object;` :21, `TObjectPtr<class UExporter> Exporter;` :25 ("otherwise it will be determined automatically" :23), `FString Filename;` :29, `bool bSelected;` :33, `bool bReplaceIdentical;` :37, `bool bPrompt;` :41, `bool bAutomated;` :45, `TArray<FString> Errors;` :65.
Concrete exporters enumerated: 28 `: public UExporter` declarations in Engine/Source (27 files, all in Editor/UnrealEd/Classes/Exporters/) incl. TextureExporterPNG.h, StaticMeshExporterOBJ.h, StaticMeshExporterFBX.h, SkeletalMeshExporterFBX.h, SoundExporterWAV.h, ObjectExporterT3D.h, LevelExporterT3D.h, AnimSequenceExporterFBX.h.
**Export**: ENGINE_API statics. | **Module**: none — Engine linked; exporter subclasses live in UnrealEd (linked). | **Guards**: none.
**Bucket**: read-only — writes to disk outside the transaction system, mutates no UObject.
**Async**: no (sync; FBX export of a big mesh = one long frame, acceptable).
**Params**: | asset | path, objectPath | string | — | yes | ; | file | filename, outPath | absolute disk path (extension selects exporter via FindExporter) | — | yes | ; | exporter | — | explicit exporter class name override | auto | no |.
**Failure modes**: FindExporter returns null ⇒ `"no exporter for <Class> to '.ext' — supported for this class: <list from UExporter::FormatExtension sweep>"`; RunAssetExportTask false ⇒ surface Task->Errors verbatim; asset unloaded ⇒ load first, error only if load fails.
**Cooked**: works for loaded container assets whose data survived cooking (textures: yes, platform-compressed — PNG export re-decompresses what's resident; meshes: rendered LODs exist so OBJ/FBX export generally works; T3D of cooked BPs: mostly useless since graphs are stripped). State per-format in docs; Errors array makes failures visible.
**Verify**: export a texture to PNG → file exists, byte size > 0, re-import it (import_asset) → SizeX/SizeY match the original's get_property values. OBJ export → line count of `v ` entries equals the mesh's vertex count from get_property.
**Score**: U4 E2 R4 → tier 1
**Phase-2 verdict**: CONFIRMED — RunAssetExportTask verbatim at Exporter.h:267, FindExporter :186 (class is UCLASS(abstract, **transient**, MinimalAPI) at :21–22, per-method ENGINE_API as claimed); UAssetExportTask fields verbatim (AssetExportTask.h:21–:65). Implementation grep (Runtime/Engine/Private/UnrealExporter.cpp): prompts are gated on Task->bPrompt only (:337/:385) — handler must set bPrompt=false, bAutomated=true and no dialog can appear. Exporter subclass sweep spot-checked (TextureExporterPNG.h etc. present under Editor/UnrealEd/Classes/Exporters/).

### validate_assets
**Purpose**: Run the engine's data-validation pass (UObject::IsDataValid + all registered validators) over chosen assets and return structured per-asset errors/warnings — the editor's right-click "Validate Assets" as an endpoint.
**Engine API**:
```cpp
virtual int32 ValidateAssetsWithSettings(const TArray<FAssetData>& AssetDataList, const FValidateAssetsSettings& InSettings, FValidateAssetsResults& OutResults) const;
```
`Engine/Plugins/Editor/DataValidation/Source/DataValidation/Public/EditorValidatorSubsystem.h:172` in `class DATAVALIDATION_API UEditorValidatorSubsystem : public UEditorSubsystem` :136. Settings struct :83–107 (`bool bCollectPerAssetDetails = false;` :99 — set TRUE; `EDataValidationUsecase ValidationUsecase` :103; `bool bLoadAssetsForValidation = true;` :106). Results struct :43–80 (`int NumChecked/NumValid/NumInvalid/NumSkipped/NumWarnings/NumUnableToValidate` :51–71, `TMap<FString, FValidateAssetsDetails> AssetsDetails;` :79). Details struct :22–40 (Result + `TArray<FText> ValidationErrors/ValidationWarnings` :36–39). `EDataValidationUsecase` enum: DataValidationModule.h:18–37 (None/Manual/Commandlet/Save/PreSubmit/Script) — use `Script`.
**Export**: DATAVALIDATION_API on subsystem + both structs (:22, :43, :83). Access via `GEditor->GetEditorSubsystem<UEditorValidatorSubsystem>()`. | **Module**: NEW dep `DataValidation` — plugin `Editor/DataValidation`, **EnabledByDefault:true** (DataValidation.uplugin:13) and NOT disabled in DrugDealerSimulator2.uproject (grepped — no mention). Editor-only plugin module. | **Guards**: none.
**Bucket**: read-only — validators must not mutate; loading assets is a side effect but not a transactional one.
**Async**: no for asset lists ≤ a few dozen (validators run inline). Endpoint caps list size (default 50, max 500) and reports duration so callers can batch.
**Params**: | assets | paths | array of object paths (or `pathPrefix`+`class` filter, same resolution as find_assets) | — | yes (one form) | ; | loadAssets | — | bool → bLoadAssetsForValidation | true | no |.
**Failure modes**: unknown asset path ⇒ error naming it before running anything; DataValidation module missing (defensive) ⇒ `"DataValidation plugin not loaded — enable Editor/DataValidation"`.
**Cooked**: degraded — container assets validate against their cooked form (stripped BP graphs validate vacuously); report per-asset origin so vacuous passes are visible.
**Verify**: NumChecked == number of inputs; break a DataTable row struct ref on a scratch asset → NumInvalid increments by exactly 1 and its AssetsDetails entry carries ≥1 ValidationErrors text.
**Score**: U4 E2 R4 → tier 1 (complements `validate` (BP-compile) with data-level checks)
**Phase-2 verdict**: CORRECTED — all citations verbatim (EditorValidatorSubsystem.h:172/:136, settings :83–107, results :43–80, details :22–40; DataValidationModule.h:18–37; uplugin EnabledByDefault:true line 13; uproject grep clean). Missed hazard now added: `ValidateAssetsWithSettings` opens an FScopedSlowTask dialog with `ESlowTaskVisibility::ForceVisible` + `MakeDialogDelayed(.1f)` (EditorValidatorSubsystem.cpp:218–220) — non-modal progress, but it pumps Slate mid-frame; keep asset lists small as specified. Also set `bShowIfNoFailures=false` (default is true — spawns toast notifications, EditorValidatorSubsystem.h:95). Bonus confirmation: results are mirrored to the "AssetCheck" message log (`FMessageLog DataValidationLog("AssetCheck")`, EditorValidatorSubsystem.cpp:229) — exactly the get_message_log tie-in the entry promises.

### map_check
**Purpose**: Run the editor's Map Check over the loaded level and return the message list structurally — today the only route is the UI (Build menu), invisible to an agent.
**Engine API**:
```cpp
UNREALED_API bool Map_Check(UWorld* InWorld, const TCHAR* Str, FOutputDevice& Ar, bool bCheckDeprecatedOnly, EMapCheckNotification::Type Notification = EMapCheckNotification::DisplayResults, bool bClearLog = true);
```
`Editor/UnrealEd/Classes/Editor/EditorEngine.h:2569` — **Phase-2 correction: this is a PRIVATE member** (the `private: // Map execs.` section opens at EditorEngine.h:2544 and covers :2549–:2571, including the nested `EMapCheckNotification` struct :2556–2564). UNREALED_API on a private method does not make it callable from another module — the direct call `GEditor->Map_Check(...)` DOES NOT COMPILE. The real reachable route is the public exec dispatcher: `GEditor->Exec(World, TEXT("MAP CHECK DONTDISPLAYDIALOG"))` — `HandleMapCommand` parses `CHECK` + optional `DONTDISPLAYDIALOG` / `NOTIFYRESULTS` / `NOCLEARLOG` and forwards to Map_Check (EditorServer.cpp:6264–6280; `CHECKDEP` variant :6282–6298; Map_Check impl at EditorServer.cpp:3950). Results read back via get_message_log (below) on log name "MapCheck" (`FMessageLog MapCheckLog("MapCheck")`, EditorServer.cpp:3957).
Phase-1's "this is NOT a run_console wrap" claim is therefore weakened: the C++ side is an exec string. The endpoint's remaining value is doing exec + structured MapCheck-log capture + count delta in ONE structured return (the raw exec emits nothing machine-readable to the output device); the catalogue editor should weigh this against brief rule 5.
**Export**: n/a — dispatched through public `UEditorEngine::Exec`. | **Module**: none — UnrealEd linked. | **Guards**: none.
**Bucket**: read-only — Map_Check inspects actors and emits messages; no object mutation, no transaction wanted.
**Async**: no — synchronous actor iteration; one long frame on huge maps (same as the menu item).
**Params**: | checkDeprecatedOnly | — | bool | false | no | ; | clearLog | — | bool | true | no |. Output: { ran:true, messageCount, messages[] } (messages inlined by internally doing the get_message_log read after the call).
**Failure modes**: no editor world ⇒ `"no editor world loaded — load_level first"`; PIE active ⇒ run against the editor world and say so in the response.
**Cooked**: works — checks the loaded level's actors regardless of asset origin; cooked base-game maps cannot be SAVED (documented impossible) but CAN be checked.
**Verify**: place an actor with a null StaticMesh (set_property mesh to None) → map_check messageCount increases by ≥1 and the message text names the actor; fix it → count returns to baseline.
**Score**: U4 E2 R4 → tier 1 (level-material assignment validation — a named Tier-0 gap — surfaces here as MapCheck warnings)
**Phase-2 verdict**: CORRECTED — Map_Check and EMapCheckNotification are private members of UEditorEngine (private section at EditorEngine.h:2544); the proposed direct call cannot link/compile. Replaced with the public `UEditorEngine::Exec` route `MAP CHECK DONTDISPLAYDIALOG [NOCLEARLOG]` (EditorServer.cpp:6264–6280) + MapCheck message-log readback — capability preserved, Effort unchanged (exec route is simpler). Hazard check done: with DONTDISPLAYDIALOG no modal appears; Map_Check runs a non-cancellable `GWarn->BeginSlowTask` (EditorServer.cpp:3954) and `bClearLog` starts a new MapCheck log page (:3959–3965) — the readback must take the CURRENT page's messages only.

### get_message_log
**Purpose**: Read any FMessageLog channel ("MapCheck", "AssetCheck", "LoadErrors", "BlueprintLog", "PIE", "AssetReimport", …) as structured JSON — an entire class of editor diagnostics currently invisible to agents.
**Engine API**:
```cpp
virtual TSharedRef<class IMessageLogListing> GetLogListing(const FName& LogName);
virtual const TArray< TSharedRef<class FTokenizedMessage> >& GetFilteredMessages() const = 0;
```
`Developer/MessageLog/Public/MessageLogModule.h:57` (`class FMessageLogModule : public IModuleInterface` :14 — obtained via `FModuleManager::LoadModuleChecked<FMessageLogModule>("MessageLog")`, virtual dispatch, no export macro needed) and `Developer/MessageLog/Public/IMessageLogListing.h:47`. Message content via FTokenizedMessage (Core, Logging/TokenizedMessage.h): severity + ToText().
**Export**: module-interface vtable (both). FTokenizedMessage is CORE_API territory (Core linked). | **Module**: NEW dep `MessageLog` (Developer module, editor-safe — loaded by the editor always for the Message Log window). | **Guards**: none.
**Bucket**: read-only. **Async**: no.
**Params**: | log | logName, name | string FName of the listing | — | yes | ; | severityFilter | — | `all`\|`errors`\|`warnings` | all | no | ; | limit | — | int | 200 | no |. Output rows: { severity, text }.
**Failure modes**: unknown log name still returns a (new, empty) listing — so the endpoint first checks `IsRegisteredLogListing` (same header) and errors with the registered-name list instead of silently returning empty.
**Cooked**: works — logs are session state, not assets.
**Verify**: run map_check with a known defect → get_message_log{"MapCheck"} count matches map_check's messageCount exactly.
**Score**: U4 E2 R5 → tier 1 (multiplies value of map_check, validate, compile flows)
**Phase-2 verdict**: CONFIRMED — GetLogListing verbatim at MessageLogModule.h:57, IsRegisteredLogListing :49, GetFilteredMessages at IMessageLogListing.h:47; the silent-creation trap is real (header doc at MessageLogModule.h:52: "If it does not exist it will created") and the IsRegisteredLogListing gate is the right fix. "AssetCheck" listing confirmed live (EditorValidatorSubsystem.cpp:229), "MapCheck" confirmed live (EditorServer.cpp:3957). Note GetFilteredMessages returns the current page's filtered view — pair with map_check's bClearLog/new-page semantics.

### list_dirty_packages
**Purpose**: Enumerate unsaved (dirty) content and world packages — "what have I not saved" is a basic agent need before save_package / trigger_cook / PIE.
**Engine API**:
```cpp
static UNREALED_API void GetDirtyContentPackages(TArray<UPackage*>& OutDirtyPackages);
UNREALED_API static void GetDirtyWorldPackages(TArray<UPackage*>& OutDirtyPackages, const FShouldIgnorePackageFunctionRef& ShouldIgnorePackageFunction = FShouldIgnorePackage::Default);
```
`Editor/UnrealEd/Public/FileHelpers.h:144` and `:402`. Phase-2 class-attribution fix: the `:144` overload belongs to `UEditorLoadingAndSavingUtils` (UCLASS at FileHelpers.h:38–39), NOT FEditorFileUtils; the FEditorFileUtils (class :183) statics are GetDirtyWorldPackages `:402`, GetDirtyContentPackages-with-filter `:409`, and combined GetDirtyPackages `:417`. All UNREALED_API static either way — use the FEditorFileUtils trio for the filter support.
**Export**: UNREALED_API (method-level). | **Module**: none. | **Guards**: none.
**Bucket**: read-only. **Async**: no.
**Params**: | kind | — | `content`\|`world`\|`all` | all | no |. Output: { count, packages[{ name, kind, origin(container|loose), assetClass? }] }.
**Failure modes**: none meaningful; empty list is a valid answer (and the point).
**Cooked**: works; a dirtied container-backed package is the interesting red flag (it can never be saved) — mark those rows `saveable:false`.
**Verify**: set_property on any asset → its package appears (count +1); save_package it → disappears (count −1).
**Score**: U4 E1 R5 → tier 1
**Phase-2 verdict**: CORRECTED — both signatures verbatim, but the :144 overload was attributed to the wrong class (it is UEditorLoadingAndSavingUtils, FileHelpers.h:38–39; fixed in place above). Prefer FEditorFileUtils::GetDirtyWorldPackages :402 / GetDirtyContentPackages :409 / GetDirtyPackages :417. OVERLAP: axis A also proposes list_dirty_packages — not resolved here, catalogue editor must dedupe.

### get_asset_compilation_status
**Purpose**: Poll outstanding async asset compilation (textures, static meshes, sounds) — the missing poll half for import_asset and any bulk mutation; today an agent cannot tell "editor still crunching" from "done".
**Engine API**:
```cpp
ENGINE_API static FAssetCompilingManager& Get();
ENGINE_API int32 GetNumRemainingAssets() const;
ENGINE_API TArrayView<IAssetCompilingManager* const> GetRegisteredManagers() const;
virtual int32 GetNumRemainingAssets() const = 0;   // per-manager, IAssetCompilingManager
```
`Runtime/Engine/Public/AssetCompilingManager.h:108, :128, :123, :66` (class FAssetCompilingManager :105). Do NOT call `FinishAllCompilation()` (:133) from a handler — it blocks the frame for the whole queue; polling is the contract.
**Export**: ENGINE_API. | **Module**: none — Engine linked. | **Guards**: none.
**Bucket**: read-only. **Async**: it IS the poll endpoint (pairs with import_asset, paint/sculpt landscape, material edits).
**Params**: none (unrecognised ⇒ error). Output: { remaining, perManager[{ name, remaining }] } (manager name via IAssetCompilingManager::GetAssetTypeName if present — verify at implementation; count alone is sufficient v1).
**Failure modes**: none; returns 0 when idle.
**Cooked**: works — compilation is session-level.
**Verify**: import a 4k PNG → remaining > 0 immediately after; poll until 0; then export/get_property give final values.
**Score**: U4 E1 R5 → tier 1
**Phase-2 verdict**: CONFIRMED — all four signatures verbatim (AssetCompilingManager.h:108/:128/:123/:66); FinishAllCompilation (:133) correctly blacklisted. The per-manager name accessor the proposer flagged as unverified EXISTS: `virtual FName GetAssetTypeName() const = 0;` at AssetCompilingManager.h:49 — the perManager breakdown is fully implementable. OVERLAP: axis E proposes asset_compile_status over the same API — not resolved here.

### fixup_redirectors
**Purpose**: Repoint all referencers of ObjectRedirectors at the live assets and delete the redirectors — after a few rename_asset calls a project accumulates redirectors that break naive path assumptions; the Content Browser right-click has this, the bridge does not.
**Engine API**:
```cpp
virtual void FixupReferencers(const TArray<UObjectRedirector*>& Objects, bool bCheckoutDialogPrompt = true, ERedirectFixupMode FixupMode = ERedirectFixupMode::DeleteFixedUpRedirectors) const = 0;
```
`Developer/AssetTools/Public/IAssetTools.h:538`; `enum class ERedirectFixupMode { DeleteFixedUpRedirectors, LeaveFixedUpRedirectors };` :66–73. Call with `bCheckoutDialogPrompt=false` (no UI). Redirectors discovered via registry: find_assets class=/Script/CoreUObject.ObjectRedirector, then LoadObject<UObjectRedirector>.
**Export**: vtable via ASSETTOOLS_API IAssetTools::Get(). | **Module**: none. | **Guards**: none.
**Bucket**: self-managed — resaves every referencing package and deletes redirector packages (package deletion is on the self-managed list); wrap nothing in the blanket transaction.
**Async**: no (bounded by referencer count; report per-redirector results).
**Params**: | paths | redirectors | array of object paths; empty + `all:true` sweeps every loose redirector under /Game | — | yes (one form) | ; | delete | deleteRedirectors | bool → FixupMode | true | no |.
**Failure modes**: path is not an ObjectRedirector ⇒ error naming it and its actual class; referencer resave fails (read-only file) ⇒ per-item failure entry, not a global throw.
**Cooked**: loose-only — container packages cannot be resaved; if a container asset references the redirector, report it as unfixable with origin listed.
**Verify**: rename_asset A→B (creates redirector), make a second asset reference B via old path is not possible — instead: count redirectors via find_assets class=ObjectRedirector before (n) and after fixup (n−k); referencing asset's get_asset_dependencies now lists B's package, not A's.
**Score**: U3 E2 R3 → tier 2 (mutation over many packages — valuable but needs the per-item result design)
**Phase-2 verdict**: CORRECTED — signature verbatim (IAssetTools.h:538, ERedirectFixupMode :66–73) but three implementation facts were missed (Developer/AssetTools/Private/AssetFixUpRedirectors.cpp):
(1) If the asset registry is still scanning, FixupReferencers opens the blocking `SDiscoveringAssetsDialog` (AssetFixUpRedirectors.cpp:59–66) — the handler MUST gate on `IAssetRegistry::IsLoadingAssets()` and return "registry scan in progress — retry" instead of calling.
(2) The final delete goes through `ObjectTools::DeleteObjects(ObjectsToDelete, false)` (AssetFixUpRedirectors.cpp:490); DeleteObjects retains modal "Cannot currently delete selected objects. See log for details." failure paths (ObjectTools.cpp:2833/:3127) — residual modal risk when a redirector is still referenced in-memory (e.g. undo buffer).
(3) Referencer packages are saved via `FEditorFileUtils::PromptForCheckoutAndSave(..., bCheckDirty=false, bPromptToSave=false, ...)` (AssetFixUpRedirectors.cpp:372–374) — no save prompt, confirming the dialog-free claim on the save leg; bCheckoutDialogPrompt=false is honored in CheckOutReferencingPackages (:117 caller, :268 impl).

### consolidate_assets
**Purpose**: Merge duplicate assets: repoint every referencer of N source assets at one target and (optionally) delete the sources — dedupe workflow that delete_asset+manual re-wiring cannot do safely.
**Engine API**:
```cpp
UNREALED_API FConsolidationResults ConsolidateObjects(UObject* ObjectToConsolidateTo, TArray<UObject*>& ObjectsToConsolidate, bool bShowDeleteConfirmation = true );
```
`Editor/UnrealEd/Public/ObjectTools.h:222` (namespace ObjectTools). Results `struct FConsolidationResults : public FGCObject` :176 — `TArray<TObjectPtr<UPackage>> DirtiedPackages;` :191, `TArray<TObjectPtr<UObject>> InvalidConsolidationObjs;` :194, FailedConsolidationObjs :196+. Call with `bShowDeleteConfirmation=false`.
**Export**: UNREALED_API (free function). | **Module**: none. | **Guards**: none.
**Bucket**: self-managed — reinstances references across many packages and deletes source objects/packages.
**Async**: no.
**Params**: | target | consolidateTo | object path | — | yes | ; | sources | consolidate, assets | array of object paths, same class as target | — | yes |. Output: { dirtiedPackages[], invalid[], failed[], deleted[] }.
**Failure modes**: class mismatch target/source ⇒ error naming both classes before touching anything; source in InvalidConsolidationObjs ⇒ per-item report; target inside sources ⇒ error.
**Cooked**: loose-only for sources (they get deleted/resaved); target may be a container asset (repointing loose referencers AT a base-game asset is a legit dedupe) — but referencers that are container-only cannot be rewritten: report them.
**Verify**: two duplicate materials M1 M2, a mesh using M2 → consolidate {target:M1, sources:[M2]} → mesh's get_asset_dependencies lists M1's package; find_assets shows M2 gone; DirtiedPackages count matches response.
**Score**: U3 E3 R2 → tier 2 (powerful, destructive — needs the per-item result design and a dry-run flag)
**Phase-2 verdict**: CORRECTED — signature verbatim (ObjectTools.h:222; results struct :176–197) but serious HIDDEN MODAL hazards were missed (Editor/UnrealEd/Private/ObjectTools.cpp):
(1) The proposed 3-arg overload forwards with `bWarnAboutRootSet` defaulting TRUE (:1947→:1964→:1933→:1432): any rooted object in the consolidation set triggers a modal YesNo (ForceReplaceReferences, ObjectTools.cpp:1093–1110). Use the 6-arg overload (ObjectTools.h:223) with `bWarnAboutRootSet=false`, or pre-reject rooted sources.
(2) UNSUPPRESSABLE end-of-run modals: the "Failed to Consolidate Assets" dialog (:1888, fires when any source is referenced by the target) and the "Critical Failure" dialog (:1922) are gated ONLY by `bShouldShowDialogs = !IsRunningCommandlet()` (:1440) — always true in a live editor; no parameter turns them off in 5.3.2. Mitigation is mandatory pre-validation so the failure sets stay empty: same-class check (the API "performs NO type checking, by design", header note :217), target-must-not-depend-on-any-source via get_asset_dependencies, no rooted sources, sources deletable. Even then the critical-failure modal remains a residual risk — the endpoint doc must say so, and the R2 risk score is justified only WITH that pre-validation ladder in place.
(3) bShowDeleteConfirmation=false correctly skips ShowDeleteConfirmationDialog (:1955–1957) — that part of the entry holds.

## Compositions (no new endpoint needed)

- **"Does asset X exist?"** — find_assets (nameContains + pathPrefix) or describe_package. No delta.
- **"List assets in a directory"** — find_assets pathPrefix already does UEditorAssetSubsystem::ListAssets
  (EditorAssetSubsystem.h:318); list_content_paths (proposed) adds only the FOLDER view.
- **"Save one loaded asset"** — save_package (exists, MifBridgeIntrospect.cpp:134) covers
  UEditorAssetSubsystem::SaveLoadedAsset (:254); list_dirty_packages (proposed) + save_package in a
  batch covers SaveLoadedAssets/"save all".
- **"Get tag values of an asset"** — UEditorAssetSubsystem::GetTagValues (:335) is an alternate
  implementation for the proposed get_asset_tags; not a separate endpoint.
- **CSV → DataTable import** — UCSVImportFactory exists (UnrealEd/Classes/Factories/CSVImportFactory.h:57)
  but create_asset{DataTable} + write_datatable_rows (exists) already covers the workflow with
  structured input; a CSV file route adds parsing failure modes for no capability. Rejected.
- **"List all redirectors"** — find_assets class=/Script/CoreUObject.ObjectRedirector. Feeds
  fixup_redirectors.
- **Duplicate / rename / delete asset** — exist (duplicate_asset, rename_asset, delete_asset).
- **"Find all Blueprints of parent X"** — get_class_hierarchy (derived) then find_assets per class,
  or find_assets_by_tag tag=ParentClass — two proposed endpoints compose; no third needed.
- **IAssetTools::ExportAssets(TArray<FString>&, const FString&)** (IAssetTools.h:445) — auto-names
  files in a folder; strictly less controllable than proposed export_asset (explicit filename per
  asset); use export_asset in a batch instead.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

_Phase-2: all 9 negatives below spot-verified against source on 2026-07-26; NONE overturned. Evidence re-read: EnumerateAllPackages lock comment (IAssetRegistry.h:411–414 verbatim); EditorAssetSubsystem.h greps clean for checksum/MD5/hash; DataTableFactory::ConfigureProperties override at DataTableFactory.h:21 and CreateAsset path never calls ConfigureProperties (AssetTools.cpp:1659 goes straight to FactoryCreateNew — and curve subfactories' ConfigureProperties are no-op `return true`, EditorFactories.cpp:7283–7286); FinishAllCompilation at AssetCompilingManager.h:133; ISourceControlModule.h:80/:87/:122/:127 verbatim + D:/DDS2SDK/Game/Config greps clean for any provider; EditorScriptingUtilities.uplugin line 13 `"EnabledByDefault" : false`; MessageLog silent-creation doc at MessageLogModule.h:52. Phase-2 additions from hazard sweeps: UAssetToolsImpl::CanCreateAsset modal prompts (AssetTools.cpp:4287–4337) and ObjectTools::ConsolidateObjects unsuppressable failure modals (ObjectTools.cpp:1440/:1888/:1922) — recorded in the create_asset / consolidate_assets entries above; both belong in 03_GAPS_AND_RISKS.md as "dialog-free automation requires handler-side pre-validation"._

- **Dependency/referencer data for container-only packages is gone at source** — the cook strips the
  dependency graph (brief-documented; confirmed structurally: the data returned by
  IAssetRegistry::GetDependencies comes from on-disk package summaries, and mounted IoStore packages
  carry cooked summaries). get_asset_dependencies/get_asset_referencers MUST self-diagnose via the
  IsContainerOnlyPackage test (MifBridgeCooked.cpp:44–49) and say "stripped", never return silent
  empties. Loose→container referencer edges DO survive (stored in the loose package).
- **UEditorAssetSubsystem has NO checksum API in 5.3.2** — grepped
  `Editor/UnrealEd/Public/Subsystems/EditorAssetSubsystem.h` case-insensitively for "checksum": absent.
  Asset-content hashing would have to come from FAssetPackageData::CookedHash (cooked-only,
  AssetData.h:901) or manual file MD5 — deferred, no proposal.
- **UFactory::ConfigureProperties is UI** — every factory's ConfigureProperties opens a Slate picker
  (e.g. UDataTableFactory::ConfigureProperties UNREALED_API, DataTableFactory.h:21). create_asset must
  set factory UPROPERTYs directly and call IAssetTools::CreateAsset (:305), never the
  CreateAssetWithDialog variants (IAssetTools.h:333/:336). Same for ImportAssetsWithDialog (:399),
  ExportAssetsWithDialog (:462/:470), RenameAssetsWithDialog (:371), DuplicateAssetWithDialog (:340) —
  all UI-locked, all rejected.
- **FAssetCompilingManager::FinishAllCompilation (AssetCompilingManager.h:133) is a frame-blocking
  drain** — must never be called from a handler; poll GetNumRemainingAssets instead (that is the whole
  design of get_asset_compilation_status).
- **IAssetRegistry::EnumerateAllPackages (IAssetRegistry.h:411–414) runs the callback INSIDE the
  registry lock** — header states re-entry deadlocks. Any implementer iterating package data must
  copy out, never call registry/asset functions in the callback.
- **Source control: project does not use it** — no provider configured in
  D:/DDS2SDK/Game/Config/*.ini; ISourceControlModule::Get().IsEnabled()
  (Developer/SourceControl/Public/ISourceControlModule.h:122) would report the null provider. A
  status endpoint is tier 3 at best; not proposed. FixupReferencers/consolidate must pass
  bCheckoutDialogPrompt=false / bShowDeleteConfirmation=false so the absent provider never raises UI.
- **EditorScriptingUtilities plugin is NOT enabled** (`"EnabledByDefault" : false` in
  EditorScriptingUtilities.uplugin:13; absent from DrugDealerSimulator2.uproject) — irrelevant for
  this axis because UEditorAssetSubsystem lives in UnrealEd in 5.3.2, but any axis reaching for
  UStaticMeshEditorSubsystem/UEditorAssetLibrary there inherits a WOULD-REQUIRE-ENABLING cost.
- **Cooked-editor caveat for import destination collisions** — importing to a path that is
  container-only shadow-mounts ambiguously; import_asset must refuse those destinations (check via
  IsContainerOnlyPackage) rather than let FPackageName resolution pick a winner.
- **MessageLog silent-creation trap** — FMessageLogModule::GetLogListing (MessageLogModule.h:57)
  CREATES an empty listing for unknown names; get_message_log must gate on IsRegisteredLogListing
  (:49) or a typo in `log` returns a plausible empty success.

## UNVERIFIED

- MigratePackages options: signature verified (IAssetTools.h:520 with `const struct FMigrationOptions&`)
  but FMigrationOptions fields (prompting behaviour?) not read — cannot yet promise a dialog-free
  migrate endpoint. Next step: read PackageMigrationContext.h / IAssetTools.h struct block.
- BeginAdvancedCopyPackages (IAssetTools.h:529/:532): async completion via FAdvancedCopyCompletedEvent
  not inspected; unclear whether it prompts. Tier-3 candidate only; duplicate_asset covers most needs.
- ~~IAssetCompilingManager::GetAssetTypeName existence assumed for per-manager breakdown in
  get_asset_compilation_status output — count-only v1 is fully verified; the name accessor needs one
  header check at implementation time.~~ **Phase-2: RESOLVED — `virtual FName GetAssetTypeName() const = 0;`
  exists at Runtime/Engine/Public/AssetCompilingManager.h:49; per-manager breakdown fully verified.**
- USoundFactory constructor defaults (auto-cue creation flags) not read — wav import verified only to
  the factory-class level (SoundFactory.h:25–26); flag audit needed before promising SoundWave-only import.
- UTextureFactory `customconstructor` (TextureFactory.h:49) — NewObject works, but its ctor contract
  (NoDefaultConstructorGuard) should be eyeballed once during implementation.
- FbxFactory `SetDetectImportTypeOnImport(false)` (FbxFactory.h:59, inline) interaction with
  ImportUI->MeshTypeToImport — the exact combination that forces static vs skeletal without a dialog
  needs a one-shot editor test; both symbols verified.

## Coverage log

- [x] Brief read; find_assets (MifBridgeCooked.cpp:191) + create_blueprint (MifBridgeNodes2.cpp:1063)
      baselines read before proposing.
- [x] IAssetRegistry.h swept (deps/referencers/tags/class-hierarchy/paths/package-data) → 8 proposals.
- [x] IAssetTools.h swept (create/import/export/migrate/advanced-copy/fixup) → 3 proposals + rejects.
- [x] UFactory enumeration: 108 decls/103 files (Source) + 188 decls/174 files (Plugins); allowlist
      factories read individually (DataTable/Curve×3/DataAsset/StringTable/MPC/PhysMat/AnimBP).
- [x] UAssetImportTask read in full; import_asset designed sync-default with async poll option.
- [x] UExporter + UAssetExportTask read; 28 exporter subclasses enumerated → export_asset.
- [x] DataValidation plugin (enabled-by-default, not disabled by project) → validate_assets.
- [x] Map_Check exported (UNREALED_API, EditorEngine.h:2569) → map_check + get_message_log pair.
- [x] Dirty packages (FileHelpers.h:144/:402/:409) → list_dirty_packages.
- [x] FAssetCompilingManager → get_asset_compilation_status.
- [x] Source control → negative result (project has no provider).
- [x] UEditorAssetSubsystem → compositions only; checksum absent (negative result).
- [x] ObjectTools::ConsolidateObjects → consolidate_assets.
- Remaining for Phase-2: FMigrationOptions fields; thumbnail capture (ThumbnailGenerator plugin is
  project-enabled — belongs to whichever axis owns imaging); collection APIs (ICollectionManager) —
  not swept here, flagging as an open corner of this axis.
- [x] **Phase-2 adversarial pass (2026-07-26)**: all 18 entries re-verified against source — 12 CONFIRMED,
      6 CORRECTED (create_asset: CanCreateAsset + AnimBP-factory modals; validate_assets: ForceVisible
      SlowTask + bShowIfNoFailures; map_check: Map_Check is PRIVATE — rerouted via UEditorEngine::Exec
      "MAP CHECK DONTDISPLAYDIALOG"; list_dirty_packages: :144 class attribution; fixup_redirectors:
      SDiscoveringAssetsDialog gate + DeleteObjects modal residue; consolidate_assets: unsuppressable
      !IsRunningCommandlet() failure modals + bWarnAboutRootSet default), 0 DEMOTED. All 9 negatives
      spot-verified, 0 overturned. UNVERIFIED item GetAssetTypeName resolved (exists,
      AssetCompilingManager.h:49). Still open from UNVERIFIED: FMigrationOptions fields,
      BeginAdvancedCopyPackages prompting, USoundFactory ctor defaults, UTextureFactory customconstructor
      contract, FbxFactory type-forcing combination.
