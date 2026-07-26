# Axis B — Assets and the registry
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._

## Surface inventory

(being filled during sweep — see Coverage log at bottom for state)

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

## Proposed endpoints

## Compositions (no new endpoint needed)

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

## UNVERIFIED

## Coverage log

- [x] Brief read; find_assets + create_blueprint baselines read.
- [x] IAssetRegistry.h swept.
- [ ] IAssetTools
- [ ] UFactory enumeration
- [ ] UAssetImportTask
- [ ] UExporter
- [ ] DataValidation / MapCheck
- [ ] Dirty packages
- [ ] IAssetCompilingManager
- [ ] Source control
- [ ] UEditorAssetSubsystem
