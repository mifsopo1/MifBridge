# Axis H — Data assets, curves, localization, config, savegames
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._
_Phase-2 adversarial verify: 2026-07-26, COMPLETED pass — all 25 proposals + 10 negatives re-checked against engine/plugin source. (An earlier interrupted pass had stamped this header with a completion claim while only 2 entries carried verdicts; every check was re-run from scratch this pass.) Result: 21 CONFIRMED, 4 CORRECTED (create_datatable, move_datatable_row, set_curve_keys, create_curve_table — the last one hides an editor-crash hazard), 0 demoted, 0 negatives overturned. Live bridge re-probed this pass: POST /api/pie_status returns the token-gate JSON (see note below)._

> Live-bridge note: the brief allows read-only introspection against http://127.0.0.1:8791, but the
> port refused connections during this sweep (curl exit 7 on pie_status and list_datatables).
> Everything below is source-verified only; no live confirmations were possible.
> Phase-2 update (2026-07-26): the bridge now RESPONDS on 127.0.0.1:8791 — routes are `POST /api/<name>`
> (MifBridgeServer.cpp:91-99) and return `{"ok":false,"error":"invalid or missing X-Mif-Token header"}`
> without a token. Live probes are possible for follow-up sessions that hold the token.

## Surface inventory

Read in full or in cited regions (paths relative to D:/UE532/Engine/Source unless noted):

| Area | Files actually opened |
|---|---|
| Existing handlers | `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeDataTables.cpp` (all 287 lines — list/read/get-row/write-rows semantics), `MifBridgeAssetOps.cpp` (IAssetTools precedent, headless Rename/Duplicate), `MifBridgeUserTypes.cpp` (struct/enum authoring surface — see struct section) |
| Factories | `Editor/UnrealEd/Classes/Factories/DataTableFactory.h` (28 lines), `DataAssetFactory.h` (29), `CurveFactory.h` (75), `CurveTableFactory.h` (31), `StringTableFactory.h` (19), plus implementations `Editor/UnrealEd/Private/Factories/DataTableFactory.cpp:194-209`, `EditorFactories.cpp:7261-7292` (curve factories), `:7480-7492` (data asset), `:7917-7920` (string table). Factory dir listing showed 15 data-adjacent factories incl. CompositeDataTableFactory.h, MirrorDataTableFactory.h, ReimportDataTableFactory.h |
| DataTable core | `Runtime/Engine/Classes/Engine/DataTable.h:60-384` (UCLASS line, all import/export methods incl. WITH_EDITOR guards), `Runtime/Engine/Classes/Engine/CompositeDataTable.h` (all 96 lines) |
| DataTable editor utils | `Editor/UnrealEd/Public/DataTableEditorUtils.h:55-134` (full static surface: 20 exported statics enumerated) |
| Curves | `Runtime/Engine/Classes/Curves/RichCurve.h:79-321` (FRichCurve full public surface), `CurveBase.h:16-101`, `CurveFloat.h`, `CurveVector.h`, `CurveLinearColor.h` (class decls + exported accessors) |
| AssetTools | `Developer/AssetTools/Public/IAssetTools.h:242-357` (CreateAsset/CreateAssetWithDialog split), `Developer/AssetTools/Private/AssetTools.cpp:1627-1660` (CreateAsset is headless; dialog risk is in CanCreateAsset overwrite prompt — see failure modes) |
| CurveTable | `Runtime/Engine/Classes/Engine/CurveTable.h:14-315` (exported surface grep + FCurveTableRowHandle), `Editor/UnrealEd/Private/Factories/CurveTableFactory.cpp:24-57` (modal ConfigureProperties discovery) |
| AssetManager | `Runtime/Engine/Classes/Engine/AssetManager.h:41-221` (UCLASS + Get/IsInitialized + primary-asset list APIs), `Runtime/Engine/Classes/Engine/AssetManagerTypes.h` (FPrimaryAssetTypeInfo USTRUCT at :135) |
| StringTable | `Runtime/Engine/Public/Internationalization/StringTable.h` (all 43 lines), `Runtime/Core/Public/Internationalization/StringTableCore.h:26-137` (FStringTableEntry + FStringTable exported methods) |
| Config | `Runtime/Core/Public/Misc/ConfigCacheIni.h:467-802` (per-file GetString/SetString/GetSection/RemoveKey/Flush/GetSectionNames), `Runtime/Core/Public/CoreGlobals.h:96,391-407` (GConfig + G*Ini externs) |
| DeveloperSettings | `Runtime/DeveloperSettings/Public/Engine/DeveloperSettings.h:22-55`, `Runtime/CoreUObject/Public/UObject/Object.h:1279-1284` (GetDefaultConfigFilename) |
| SaveGames | `Runtime/Engine/Classes/Kismet/GameplayStatics.h:1098-1207` (save/load slot statics), `Runtime/Engine/Classes/GameFramework/SaveGame.h:22-81`, `Runtime/Engine/Public/SaveGameSystem.h:19-204`, `Runtime/Core/Public/HAL/FileManager.h:57-147` |
| RamaSaveSystem (project plugin) | `D:/DDS2SDK/Game/Plugins/Plugins_RamaThumb/RamaSaveSystem` — uplugin + Binaries + Source layout listed; all 15 Public headers listed; `RamaSaveLibrary.h` read in full (105 lines); `Private/RamaSaveLibrary.cpp` read (stub bodies); Private dir line counts taken (227 total) |
| Struct/enum authoring | `Editor/UnrealEd/Public/Kismet2/StructureEditorUtils.h:32-165` (full exported static surface), `Editor/UnrealEd/Public/Kismet2/EnumEditorUtils.h:26-98`, existing handlers in `MifBridgeUserTypes.cpp` (struct ops :133-390, enum ops :399+) |
| Internationalization | `Runtime/Core/Public/Internationalization/Internationalization.h:28,59-194`, gather commandlet dir listing `Editor/UnrealEd/Classes/Commandlets/Gather*` (7 files) |
| Curves (extra) | `Runtime/Engine/Classes/Curves/RealCurve.h:114-196` (FRealCurve + Eval PURE_VIRTUAL), `Runtime/Engine/Classes/Curves/IndexedCurve.h:17-62` |

Existing-coverage facts established by reading `MifBridgeDataTables.cpp` before proposing anything:
- `list_datatables` = asset-registry class sweep (includes UCompositeDataTable via bSearchSubClasses).
- `read_datatable` = JSON dump via `GetTableAsJSON` (maxRows clamp 1..10000). No CSV form.
- `get_datatable_row` = single row from the same JSON dump.
- `write_datatable_rows` = confirm-gated; merge mode via `FDataTableEditorUtils::AddRow` +
  `FJsonObjectConverter::JsonObjectToUStruct`, replace mode via `CreateTableFromJSONString`.
  It can add and update rows but CANNOT delete a single row (only whole-table replace),
  cannot rename, reorder, or duplicate a row, and there is no CSV path in or out.
- No endpoint creates a UDataTable, UCurve*, UCurveTable, UDataAsset, or UStringTable asset.
  (`create_blueprint`/`create_material_instance`/`create_struct`/`create_enum` are the only creators.)

## Proposed endpoints

### create_datatable
**Purpose**: Create a new UDataTable asset with a chosen RowStruct — today rows can be written but no table can be created, so agents must hand-author tables in-editor first.
**Engine API**:
```cpp
// Editor/UnrealEd/Classes/Factories/DataTableFactory.h:12-27
UCLASS(hidecategories=Object, MinimalAPI)
class UDataTableFactory : public UFactory
{
	UPROPERTY(BlueprintReadWrite, Category = "Data Table Factory")
	TObjectPtr<const class UScriptStruct> Struct;                                    // line 17-18
	UNREALED_API virtual UObject* FactoryCreateNew(UClass* Class, UObject* InParent, FName Name, EObjectFlags Flags, UObject* Context, FFeedbackContext* Warn) override;  // line 22
};
// Developer/AssetTools/Public/IAssetTools.h:305
virtual UObject* CreateAsset(const FString& AssetName, const FString& PackagePath, UClass* AssetClass, UFactory* Factory, FName CallingContext = NAME_None) = 0;
```
Implementation fact (read, not assumed): `UDataTableFactory::FactoryCreateNew` returns nullptr when `Struct` is null and assigns `DataTable->RowStruct = const_cast<UScriptStruct*>(ToRawPtr(Struct));` (`Editor/UnrealEd/Private/Factories/DataTableFactory.cpp:194-207`).
**Export**: class is `MinimalAPI` (StaticClass/NewObject fine) with method-level `UNREALED_API` on `FactoryCreateNew`; `IAssetTools::Get()` is `ASSETTOOLS_API` (IAssetTools.h:247) but MifBridge already loads the module via `FModuleManager` (MifBridgeAssetOps.cpp:126 precedent). | **Module**: none — UnrealEd + AssetTools already linked. | **Guards**: none (editor-only plugin).
**Bucket**: self-managed — creates + registers a new UObject/package; matches create_blueprint precedent, and IAssetTools::CreateAsset internally handles registration/notification (no outer blanket transaction wanted around package creation).
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| path | newPath | string `/Game/Dir/Name` | — | yes |
| rowStruct | row_struct, struct | string (struct path or short name, resolved via existing ResolveStruct/resolve_struct logic) | — | yes |
Unrecognised parameter ⇒ error naming it.
**Failure modes**:
- rowStruct unresolvable ⇒ `"rowStruct '<v>' not found — pass a full path (/Game/... or /Script/Module.Struct) or use resolve_struct"`.
- rowStruct not a FTableRowBase child ⇒ pre-validate with `FDataTableEditorUtils::IsValidTableStruct` (UNREALED_API, DataTableEditorUtils.h:113) ⇒ `"struct '<v>' does not derive from FTableRowBase"`. Without this check the factory silently produces a table the DT editor rejects.
- Package already exists ⇒ MUST pre-check (`FPackageName::DoesPackageExist`) and fail; `UAssetToolsImpl::CanCreateAsset` opens a modal overwrite dialog (AssetTools.cpp:1647) which would block the game thread mid-HTTP-request.
- Factory returned nullptr (Struct null / class mismatch) ⇒ `"factory refused to create — rowStruct did not survive resolution"`.
**Cooked**: works — creates a NEW loose asset; RowStruct may be a cooked /Game struct (UScriptStruct survives cooking) or a /Script struct.
**Verify**: `read_datatable path=<new>` returns rowCount=0 and `rowStruct=<name>`; then `write_datatable_rows` one row and re-read ⇒ rowCount=1.
**Score**: U5 E2 R2 → tier 1 — unlocks the whole authoring loop that write_datatable_rows already half-implements.
**Phase-2 verdict**: CORRECTED — all citations re-verified verbatim (DataTableFactory.h:12-27; DataTableFactory.cpp:194-207 incl. the RowStruct assignment at :203; IAssetTools.h:247,305). Two hazard additions: (1) `UDataTableFactory::ConfigureProperties` is ITSELF MODAL (`GEditor->EditorAddModalWindow`, DataTableFactory.cpp:176) — the CreateAsset route never calls it (verified, AssetTools.cpp:1627-1682), but never add a ConfigureProperties call. (2) `CanCreateAsset` raises modal dialogs for INVALID names too, not just overwrite: FMessageDialog at AssetTools.cpp:4294 (invalid object/package name), :4301 (map-name collision), :4331 (overwrite prompt); called from CreateAsset at :1647. Pre-validate with `FName::IsValidObjectName` + `FPackageName::IsValidLongPackageName` + `FEditorFileUtils::IsMapPackageAsset` + `DoesPackageExist`. Also pass a matching AssetClass or CreateAsset's own ensure-dialogs fire (AssetTools.cpp:1634,1640). Axis-B overlap: keep this endpoint even under a generic create_asset — RowStruct validation via IsValidTableStruct is table-specific. [Re-verified this pass: every citation re-opened — DataTableFactory.h:12-27, DataTableFactory.cpp:176/:194-207, AssetTools.cpp:1627-1682 (ensure-dialogs :1634/:1640, CanCreateAsset call :1647), CanCreateAsset dialogs :4294/:4301/:4331-4337, IAssetTools.h:247/:305 — all hold verbatim.]

### delete_datatable_row
**Purpose**: Remove ONE row; today the only deletion path is whole-table `replace` (read-all/rewrite-all — race-prone and O(table)).
**Engine API**:
```cpp
// Editor/UnrealEd/Public/DataTableEditorUtils.h:86
static UNREALED_API bool RemoveRow(UDataTable* DataTable, FName Name);
```
**Export**: `UNREALED_API` method-level static (struct FDataTableEditorUtils, DataTableEditorUtils.h:55). Already used by write_datatable_rows (MifBridgeDataTables.cpp:266). | **Module**: none. | **Guards**: none.
**Bucket**: transacted — RemoveRow already calls BroadcastPreChange/PostChange internally (it is the DT editor's own path); one blanket transaction gives undo.
**Async**: no.
**Params**: | path | — | string | — | yes | ; | rowName | row_name, row | string | — | yes | ; | confirm | — | bool | false | yes (destructive — match write_datatable_rows gate) |
**Failure modes**: row not found ⇒ `"row '<name>' not found in <table> — see read_datatable"` (RemoveRow returns false); table has no RowStruct ⇒ same guard as existing handler.
**Cooked**: works on loose tables; a .pak-mounted table mutates in memory but cannot be saved — say so in response (`savable:false`) like other mutators should.
**Verify**: `read_datatable` rowCount decreases by exactly 1; `get_datatable_row` for the name fails.
**Score**: U4 E1 R1 → tier 1.
**Phase-2 verdict**: CONFIRMED — RemoveRow signature exact (DataTableEditorUtils.h:86). Note: RemoveRow opens its OWN `FScopedTransaction` internally (DataTableEditorUtils.cpp:463); the blanket transaction nests harmlessly (merged undo entry). No dialogs anywhere in DataTableEditorUtils.cpp (grepped). [Re-verified this pass: :86 and cpp:463 hold; fresh grep confirms no EditorAddModalWindow/FMessageDialog in DataTableEditorUtils.cpp — internal transactions also at :572 (Duplicate), :595 (Rename), :671 (Move).]

### rename_datatable_row
**Purpose**: Rename a row key in place preserving data + order (currently impossible without full replace, which loses row order semantics).
**Engine API**:
```cpp
// Editor/UnrealEd/Public/DataTableEditorUtils.h:89
static UNREALED_API bool RenameRow(UDataTable* DataTable, FName OldName, FName NewName);
```
**Export**: `UNREALED_API` static. | **Module**: none. | **Guards**: none.
**Bucket**: transacted.
**Async**: no.
**Params**: | path | — | string | — | yes | ; | rowName | oldName, row_name | string | — | yes | ; | newName | new_name | string | — | yes |
**Failure modes**: old row missing ⇒ error naming the row; new name already present ⇒ pre-check `GetRowMap().Contains` ⇒ `"row '<new>' already exists — RenameRow would silently collide"`.
**Cooked**: as delete_datatable_row.
**Verify**: `get_datatable_row` under the new name returns the identical field values (compare a numeric field before/after); old name 404s; rowCount unchanged.
**Score**: U3 E1 R1 → tier 1.
**Phase-2 verdict**: CONFIRMED — RenameRow signature exact (DataTableEditorUtils.h:89). Opens its own FScopedTransaction internally (DataTableEditorUtils.cpp:595); nests harmlessly under the blanket transaction. No dialogs in the cpp (grepped).

### duplicate_datatable_row
**Purpose**: Clone a row under a new key — the natural "make a variant item/recipe" primitive for a data-driven game like DDS2.
**Engine API**:
```cpp
// Editor/UnrealEd/Public/DataTableEditorUtils.h:88
static UNREALED_API uint8* DuplicateRow(UDataTable* DataTable, FName SourceRowName, FName RowName);
```
**Export**: `UNREALED_API` static. | **Module**: none. | **Guards**: none.
**Bucket**: transacted.
**Async**: no.
**Params**: | path | — | string | — | yes | ; | sourceRow | source_row, from | string | — | yes | ; | newRow | new_row, to | string | — | yes |
**Failure modes**: source missing ⇒ error; target exists ⇒ pre-check and error (DuplicateRow returns nullptr); optionally accept `overrides` object applied after via FJsonObjectConverter (same code path as write_datatable_rows merge).
**Cooked**: as above.
**Verify**: rowCount +1; `get_datatable_row newRow` deep-equals sourceRow's JSON.
**Score**: U3 E1 R1 → tier 1.
**Phase-2 verdict**: CONFIRMED — DuplicateRow signature exact (DataTableEditorUtils.h:88). Opens its own FScopedTransaction internally (DataTableEditorUtils.cpp:572); no dialogs in the cpp (grepped).

### move_datatable_row
**Purpose**: Reorder rows (row order is meaningful for iteration order and designer diffing; unreachable today — replace mode reorders but rewrites every row).
**Engine API**:
```cpp
// Editor/UnrealEd/Public/DataTableEditorUtils.h:90
static UNREALED_API bool MoveRow(UDataTable* DataTable, FName RowName, ERowMoveDirection Direction, int32 NumRowsToMoveBy = 1);
```
`ERowMoveDirection` is a nested `enum class` INSIDE `FDataTableEditorUtils` (Up/Down — DataTableEditorUtils.h:65-69); spell it `FDataTableEditorUtils::ERowMoveDirection::Up` at call sites.
**Export**: `UNREALED_API` static. | **Module**: none. | **Guards**: none.
**Bucket**: transacted.
**Async**: no.
**Params**: | path | — | string | — | yes | ; | rowName | row | string | — | yes | ; | direction | dir | string enum `up`\|`down` | — | yes | ; | by | count, numRows | int ≥1 | 1 | no |
**Failure modes**: unknown direction string ⇒ `"direction must be 'up' or 'down'"`; row missing ⇒ error; move past ends clamps (MoveRow returns false when it did nothing ⇒ report `moved:false`).
**Cooked**: as above.
**Verify**: `read_datatable` (rows come back in table order via GetTableAsJSON) — index of the row shifts by exactly `by`.
**Score**: U2 E1 R1 → tier 2 — niche but trivially cheap alongside the other three row ops.
**Phase-2 verdict**: CORRECTED — MoveRow signature exact (DataTableEditorUtils.h:90), but `ERowMoveDirection` was mislocated: it is a nested enum class inside FDataTableEditorUtils (DataTableEditorUtils.h:65-69), not a free enum above the struct — unqualified use is a compile error; citation fixed in place. Internal FScopedTransaction at DataTableEditorUtils.cpp:671.

### export_datatable_csv
**Purpose**: Round-trip tables through the same CSV format designers/Excel use — read_datatable only emits JSON.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Engine/DataTable.h:325 (inside #if WITH_EDITOR, line 317)
ENGINE_API FString GetTableAsCSV(const EDataTableExportFlags InDTExportFlags = EDataTableExportFlags::None) const;
```
**Export**: method-level `ENGINE_API` (class is MinimalAPI, DataTable.h:65). | **Module**: none. | **Guards**: `#if WITH_EDITOR` (the method itself is editor-guarded — DataTable.h:317-344).
**Bucket**: read-only — pure serialisation.
**Async**: no.
**Params**: | path | — | string | — | yes | ; | writeTo | file, out | string abs/relative-to-project path | "" (return inline) | no — when set, write with FFileHelper and return `{bytes, file}` instead of megabyte JSON responses |
**Failure modes**: null RowStruct ⇒ `"table has no RowStruct — nothing to export"`; writeTo outside the project dir ⇒ refuse (path traversal guard).
**Cooked**: works — cooked tables retain row data; CSV export reflects in-memory rows.
**Verify**: line count == rowCount+1 (header); import back into a scratch table (see import_datatable_csv) ⇒ identical rowCount and per-row JSON.
**Score**: U3 E1 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — GetTableAsCSV signature exact (DataTable.h:325), ENGINE_API method-level inside the #if WITH_EDITOR block (:317 opens, :344 closes), class MinimalAPI (:65). All verified verbatim.

### import_datatable_csv
**Purpose**: Bulk-fill a table from CSV text (the designer-facing interchange format); complements write_datatable_rows' JSON-only paths.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Engine/DataTable.h:350
ENGINE_API TArray<FString> CreateTableFromCSVString(const FString& InString);
```
Comment verbatim (DataTable.h:345-349): "Create table from CSV style comma-separated string. RowStruct must be defined before calling this function. @return Set of problems encountered while processing input".
**Export**: method-level `ENGINE_API`. NOT editor-guarded (lives outside the WITH_EDITOR block — line 344 closes it). | **Module**: none. | **Guards**: none.
**Bucket**: transacted — single-object data mutation; pair with `FDataTableEditorUtils::BroadcastPostChange(Table, RowList)` + `MarkPackageDirty` exactly as write_datatable_rows replace mode does (MifBridgeDataTables.cpp:202-203).
**Async**: no.
**Params**: | path | — | string | — | yes | ; | csv | text | string | — | one of csv/file required | ; | file | from | string path | — | — | ; | confirm | — | bool | false | yes (REPLACES the whole table — CreateTableFromCSVString empties first) |
**Failure modes**: problems array non-empty ⇒ return them verbatim under `problems` and `replaced:false` claim only when empty (mirror the existing handler's guard, MifBridgeDataTables.cpp:196-215); no RowStruct ⇒ error; column/struct mismatch ⇒ the problems list already names each bad column.
**Cooked**: mutates in memory; unsavable for .pak tables — report `savable:false`.
**Verify**: response `rowCount` equals CSV line count minus header; spot-check one row via get_datatable_row.
**Score**: U4 E1 R2 → tier 1.
**Phase-2 verdict**: CONFIRMED — CreateTableFromCSVString signature exact (DataTable.h:350), sits OUTSIDE the WITH_EDITOR block (which closes at :344) as claimed; doc comment verbatim (:345-349). Handler-precedent citations hold (MifBridgeDataTables.cpp:196 CreateTableFromJSONString, :202-203 BroadcastPostChange + MarkPackageDirty).

### set_composite_datatable_parents
**Purpose**: Author UCompositeDataTable stacks (DLC/patch-style row overlays) — composite tables reject direct row writes, so the ONLY meaningful mutation is the parent list, which no endpoint touches.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Engine/CompositeDataTable.h:58
ENGINE_API void AppendParentTables(const TArray<UDataTable*>& NewTables);
// CompositeDataTable.h:76-77 (protected UPROPERTY — read/replace via reflection only)
UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = Tables)
TArray<TObjectPtr<UDataTable>> ParentTables;
```
Also relevant: `ENGINE_API virtual void EmptyTable() override;` (CompositeDataTable.h:44) — with `bClearParentTables` semantics internal; and the composite factory `Editor/UnrealEd/Classes/Factories/CompositeDataTableFactory.h` for creation (same pattern as create_datatable; fold in via `composite:true` param on create_datatable).
**Export**: `ENGINE_API` method-level on MinimalAPI class. | **Module**: none. | **Guards**: none.
**Bucket**: transacted — property-level mutation; replace mode must go through FProperty + `PostEditChangeProperty` so `OnParentTablesUpdated` fires (that hookup is done in PostEditChangeProperty per CompositeDataTable.h:52).
**Async**: no.
**Params**: | path | — | string | — | yes | ; | parents | tables | string[] of DT paths | — | yes | ; | mode | — | `append`\|`replace` | append | no |
**Failure modes**: any parent path unresolvable ⇒ error naming which index; parent not a UDataTable ⇒ error; self-reference/loop ⇒ engine detects via FindLoops (CompositeDataTable.h:64) but pre-check `parent != self` and report `"composite loop: '<p>' would include itself"`; target not a UCompositeDataTable ⇒ `"'<path>' is a plain UDataTable — set_composite_datatable_parents only applies to CompositeDataTable assets"`.
**Cooked**: append to cooked composite works in memory; unsavable.
**Verify**: `get_property path=<table> property=ParentTables` lists N entries (ParentTables is a UPROPERTY ⇒ readable by the existing reflection walker); `read_datatable` rowCount equals union of parents' rows.
**Score**: U3 E2 R2 → tier 2.
**Phase-2 verdict**: CONFIRMED — AppendParentTables (CompositeDataTable.h:58, ENGINE_API), protected ParentTables UPROPERTY (:76-77), EmptyTable (:44), FindLoops (:64 — protected + unexported, so the spec's parent!=self pre-check plus trusting the engine's internal loop detection is the right call), PostEditChangeProperty hookup (:52), row-mutation refusal comment verbatim (:43). All hold.

### create_curve
**Purpose**: Create UCurveFloat / UCurveVector / UCurveLinearColor assets — none of the 159 endpoints can make a standalone curve asset.
**Engine API**:
```cpp
// Editor/UnrealEd/Classes/Factories/CurveFactory.h:16-29
UCLASS(MinimalAPI)
class UCurveFactory : public UFactory
{
	UPROPERTY(EditAnywhere, Category=CurveFactory)
	TSubclassOf<UCurveBase> CurveClass;                      // line 22-23
	virtual UObject* FactoryCreateNew(...) override;         // line 27 — NO export macro
};
```
Constructor facts (read): `UCurveFloatFactory` ctor sets `SupportedClass` and `CurveClass = UCurveFloat::StaticClass()` (`Editor/UnrealEd/Private/Factories/EditorFactories.cpp:7276-7281`); `UCurveFactory::FactoryCreateNew` is just `NewObject<UCurveBase>(InParent, CurveClass, Name, Flags)` (EditorFactories.cpp:7261-7270).
**Export**: `UCurveFactory::FactoryCreateNew` has NO method-level export and the class is MinimalAPI ⇒ we CANNOT call it directly. Viable route (link-safe): `NewObject<UCurveFactory>()` (MinimalAPI exports StaticClass; construction goes through the UClass ctor pointer, not a linked symbol), set `CurveClass` (public data member — no symbol needed), pass the factory to `IAssetTools::CreateAsset(...)` which virtual-dispatches `FactoryCreateNew` inside the AssetTools module. This is exactly how the content browser invokes it. State this route in the handler comment. | **Module**: none. | **Guards**: none.
**Bucket**: self-managed — new asset/package creation (create_blueprint precedent).
**Async**: no.
**Params**: | path | — | string /Game/... | — | yes | ; | curveType | type, class | string enum `float`\|`vector`\|`linearcolor` (maps to UCurveFloat/UCurveVector/UCurveLinearColor::StaticClass()) | float | no |
**Failure modes**: package exists ⇒ pre-check (same modal-dialog hazard as create_datatable); unknown curveType ⇒ `"curveType must be one of float|vector|linearcolor"`.
**Cooked**: creates loose assets — unaffected.
**Verify**: `read_curve` (below) on the new asset returns keyCount 0 per channel; find_assets shows the asset with class UCurveFloat.
**Score**: U4 E2 R2 → tier 1.
**Phase-2 verdict**: CONFIRMED — CurveFactory.h:16 MinimalAPI, CurveClass UPROPERTY :22-23, FactoryCreateNew :27 with no export macro; impl EditorFactories.cpp:7261-7270 (returns nullptr when CurveClass null); UCurveFloatFactory ctor :7276-7281 — all verbatim. ADDED HAZARD (axis watch-item): base `UCurveFactory::ConfigureProperties` opens a MODAL SClassPickerDialog (EditorFactories.cpp:7231-7259, PickClass at :7251) — never call it. Simpler equivalent route: NewObject the typed subclass factories (UCurveFloatFactory / UCurveLinearColorFactory / UCurveVectorFactory) whose ctors preset CurveClass and whose ConfigureProperties overrides are non-modal `return true;` (:7283-7286, :7299-7302).

### set_curve_keys
**Purpose**: STRUCTURED key editing on any FRichCurve-bearing asset (UCurveFloat/Vector/LinearColor) — closes the roadmap's element-level-addressing gap for curves; set_property cannot address "key 3 of channel R", and curve keys via raw struct-array property writes skip tangent auto-computation.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Curves/RichCurve.h:231
ENGINE_API virtual FKeyHandle AddKey(float InTime, float InValue, const bool bUnwindRotation = false, FKeyHandle KeyHandle = FKeyHandle()) final override;
// RichCurve.h:258
ENGINE_API virtual FKeyHandle UpdateOrAddKey(float InTime, float InValue, const bool bUnwindRotation = false, float KeyTimeTolerance = UE_KINDA_SMALL_NUMBER) final override;
// RichCurve.h:261
ENGINE_API virtual void SetKeyTime(FKeyHandle KeyHandle, float NewTime) final override;
// RichCurve.h:267
ENGINE_API virtual void SetKeyValue(FKeyHandle KeyHandle, float NewValue, bool bAutoSetTangents = true) final override;
// RichCurve.h:282-283
ENGINE_API virtual void SetKeyInterpMode(FKeyHandle KeyHandle, ERichCurveInterpMode NewInterpMode) final override;
// RichCurve.h:255
ENGINE_API void DeleteKey(FKeyHandle KeyHandle) final override;
// RichCurve.h:304
ENGINE_API virtual void Reset() final override;
// RichCurve.h:313
ENGINE_API void AutoSetTangents(float Tension = 0.f);
```
Channel access:
```cpp
// Runtime/Engine/Classes/Curves/CurveFloat.h:36
FRichCurve FloatCurve;                                     // public member, direct access
// Runtime/Engine/Classes/Curves/CurveVector.h:36
FRichCurve FloatCurves[3];
// Runtime/Engine/Classes/Curves/CurveLinearColor.h:36
FRichCurve FloatCurves[4];
```
Post-edit notify: `ENGINE_API virtual void OnCurveChanged(const TArray<FRichCurveEditInfo>& ChangedCurveEditInfos) override;` (CurveBase.h:77) + `GetCurves()` (`ENGINE_API TArray<FRichCurveEditInfo> GetCurves()` — CurveFloat.h:48) to build the edit-info array, then MarkPackageDirty.
**Export**: every listed FRichCurve method is method-level `ENGINE_API` (struct itself unexported — RichCurve.h:197 `struct FRichCurve : public FRealCurve`); channel members are public data (no export needed); UCurve* classes are MinimalAPI with ENGINE_API accessors as cited. | **Module**: none. | **Guards**: none.
**Bucket**: transacted — in-place data edit on one asset; undo of key edits is safe.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| path | curve | string asset path | — | yes |
| channel | axis | string enum: float curve accepts only `0`/omitted; vector: `x|y|z|0|1|2`; color: `r|g|b|a|0..3` | 0 | no |
| keys | — | array of `{time:float, value:float, interp?:"linear"|"cubic"|"constant", tangentMode?:"auto"|"user"|"break"}` | — | yes |
| replace | — | bool (true ⇒ Reset() first; false ⇒ UpdateOrAddKey merge) | true | no |
interp maps to `ERichCurveInterpMode` RCIM_Linear/RCIM_Cubic/RCIM_Constant (Runtime/Engine/Classes/Curves/RealCurve.h — enum shared by all real curves). Unrecognised param or enum string ⇒ error naming it and listing accepted values.
**Failure modes**: asset not a UCurveBase ⇒ `"'<path>' is <class> — expected CurveFloat/CurveVector/CurveLinearColor"`; channel out of range for the concrete type ⇒ `"channel 'a' invalid for CurveVector (x|y|z)"`; keys not sorted ⇒ sort by time before insertion (AddKey handles arbitrary order but state the behaviour); NaN time/value ⇒ reject with param name.
**Cooked**: cooked curve assets keep their FRichCurve data ⇒ edits work in memory; unsavable for .pak assets (`savable:false`).
**Verify**: `read_curve` returns keyCount == len(keys) and `eval` samples: for keys {(0,0),(1,10)} linear, eval(0.5) == 5.0 exactly. Numbers, not pixels.
**Score**: U5 E2 R1 → tier 0 — named roadmap gap (element-level addressing for curves).
**Phase-2 verdict**: CORRECTED — every FRichCurve citation exact (RichCurve.h:205/:231/:255/:258/:261/:267/:282-283/:298/:304/:310/:313; struct decl :196-198; FRichCurveKey USTRUCT :78-89); channel members verified (CurveFloat.h:36, CurveVector.h:36, CurveLinearColor.h:36); OnCurveChanged (CurveBase.h:77) and GetCurves (CurveFloat.h:47-48) hold; ERichCurveInterpMode lives at RealCurve.h:12. THE FIX is to the shared curve-table row-addressing design note below: `AddRichCurve` hard-`check`s the table is not SimpleCurves-mode (CurveTable.cpp:554-557) — calling it on a simple-curve table CRASHES the editor. The handler MUST read `GetCurveTableMode()` (inline, CurveTable.h:56) first and refuse simple-mode tables with a typed error (or route those rows through AddSimpleCurve + FSimpleCurve ops).

### read_curve
**Purpose**: Read keys AND numerically evaluate a curve at N sample points — the verification half of set_curve_keys, and the only way an agent can "see" any curve (incl. cooked base-game curves).
**Engine API**:
```cpp
// Runtime/Engine/Classes/Curves/RichCurve.h:205
ENGINE_API TArray<FRichCurveKey> GetCopyOfKeys() const;
// RichCurve.h:310
ENGINE_API virtual float Eval(float InTime, float InDefaultValue = 0.0f) const final override;
// RichCurve.h:298
ENGINE_API virtual void GetTimeRange(float& MinTime, float& MaxTime) const final override;
// Typed conveniences: CurveFloat.h:44 ENGINE_API float GetFloatValue(float InTime) const;
// CurveVector.h:40 ENGINE_API FVector GetVectorValue(float InTime) const;
// CurveLinearColor.h:26/44 ENGINE_API FLinearColor GetLinearColorValue(float InTime) const;
```
`FRichCurveKey` fields (RichCurve.h:79-...): Time, Value, InterpMode, TangentMode, ArriveTangent, LeaveTangent — all public USTRUCT members, serialise each key as an object.
**Export**: all `ENGINE_API` method-level as cited. | **Module**: none. | **Guards**: none.
**Bucket**: read-only.
**Async**: no.
**Params**: | path | curve | string | — | yes | ; | samples | n | int 0..1024 (0 ⇒ keys only) | 0 | no | ; | t0 | from | float | curve min | no | ; | t1 | to | float | curve max | no |
**Failure modes**: not a curve asset ⇒ typed error as set_curve_keys; t1 < t0 ⇒ error naming both params.
**Cooked**: WORKS fully on .pak curves (pure read) — call this out; it is one of the few authoring-adjacent reads that is 100% functional on base-game content.
**Verify**: self-verifying (it IS the verifier); sanity: eval at an exact key time returns the key value.
**Score**: U4 E1 R5 → tier 0 (pairs with set_curve_keys).
**Phase-2 verdict**: CONFIRMED — GetCopyOfKeys (RichCurve.h:205), Eval (:310), GetTimeRange (:298), GetFloatValue (CurveFloat.h:44), GetVectorValue (CurveVector.h:40), GetLinearColorValue (CurveLinearColor.h:26 struct / :44 class) all exact; FRichCurveKey fields are public USTRUCT members (RichCurve.h:78-89). Pure read — no hazards found.

### create_curve_table
**Purpose**: Create a UCurveTable asset and optionally bulk-fill it from CSV — curve tables (named FRichCurve rows) back scaling/economy data and are currently untouchable.
**Engine API**:
```cpp
// Editor/UnrealEd/Classes/Factories/CurveTableFactory.h:22-27
UNREALED_API virtual UObject* FactoryCreateNew(UClass* Class, UObject* InParent, FName Name, EObjectFlags Flags, UObject* Context, FFeedbackContext* Warn) override;
UNREALED_API virtual UCurveTable* MakeNewCurveTable(UObject* InParent, FName Name, EObjectFlags Flags);
// Runtime/Engine/Classes/Engine/CurveTable.h:199
ENGINE_API TArray<FString> CreateTableFromCSVString(const FString& InString, ERichCurveInterpMode InterpMode = RCIM_Linear);
// CurveTable.h:60-61
ENGINE_API FRichCurve& AddRichCurve(FName RowName);
ENGINE_API FSimpleCurve& AddSimpleCurve(FName RowName);
// CurveTable.h:183
ENGINE_API FString GetTableAsCSV() const;
```
**Export**: UCurveTableFactory methods are method-level `UNREALED_API` (class MinimalAPI, CurveTableFactory.h:15); UCurveTable methods method-level `ENGINE_API` (class MinimalAPI, CurveTable.h:38). DANGER verified: `UCurveTableFactory::ConfigureProperties` opens a MODAL window (`GEditor->EditorAddModalWindow`, CurveTableFactory.cpp:55) — never call it; `IAssetTools::CreateAsset` does NOT call ConfigureProperties (verified by reading UAssetToolsImpl::CreateAsset body, AssetTools.cpp:1627-1682 — goes straight to FactoryCreateNew). | **Module**: none. | **Guards**: none.
**Bucket**: self-managed — new asset creation.
**Async**: no.
**Params**: | path | — | string /Game/... | — | yes | ; | csv | text | string | "" | no (fills after create) | ; | interp | interpMode | `linear`\|`cubic`\|`constant` → RCIM_* | linear | no |
**Failure modes**: package exists ⇒ pre-check (modal hazard, as create_datatable); CSV problems ⇒ return `problems[]` verbatim; unknown interp ⇒ error listing accepted values.
**Cooked**: creates loose assets — unaffected.
**Verify**: response rowCount (GetRowMap().Num()); export back via GetTableAsCSV and compare row count; eval a row via read_curve (see row addressing note below).
**Score**: U3 E2 R2 → tier 2.
**Phase-2 verdict**: CORRECTED — citations hold (FactoryCreateNew UNREALED_API at CurveTableFactory.h:23, MakeNewCurveTable :27, modal ConfigureProperties at CurveTableFactory.cpp:55, CurveTable.h:60-61/:183/:199), BUT the factory is NOT a clean creator: `MakeNewCurveTable` PRE-SEEDS a row named "Curve" (CurveTableFactory.cpp:63-77) — a SIMPLE curve carrying the factory's InterpMode when != RCIM_Cubic, a rich curve when cubic. Via IAssetTools::CreateAsset (ConfigureProperties skipped) the non-UPROPERTY `InterpMode` member is zero-initialized by NewObject = RCIM_Linear ⇒ the new table arrives in SimpleCurves mode with one stray "Curve" row, and any subsequent `AddRichCurve` trips `check(CurveTableMode != ECurveTableMode::SimpleCurves)` (CurveTable.cpp:556) ⇒ EDITOR CRASH. REQUIRED fix: immediately after creation call `EmptyTable()` (`ENGINE_API virtual void EmptyTable();` — CurveTable.h:217; resets CurveTableMode to Empty, CurveTable.cpp:525-542) before any AddRichCurve; the CSV path is safe as-is because CreateTableFromCSVString empties first (CurveTable.cpp:669) and sets mode from `interp` (SimpleCurves unless cubic — :671). Response should echo `curveTableMode` so agents know which row ops are legal. This also resolves the UNVERIFIED item on MakeNewCurveTable.

> **Curve-table row addressing (design note for set_curve_keys / read_curve)**: when `path`
> resolves to a UCurveTable instead of a UCurveBase, both endpoints accept `row` (FName) and
> operate on that row's curve: write side uses `AddRichCurve(row)` (CurveTable.h:60) then the same
> FRichCurve ops; read side uses `FindCurveUnchecked(row)` (inline, CurveTable.h:166) +
> `FRealCurve::Eval` — `ENGINE_API virtual float Eval(float InTime, float InDefaultValue = 0.0f) const PURE_VIRTUAL(FRealCurve::Eval, return 0.f;)`
> (Runtime/Engine/Classes/Curves/RealCurve.h:196) and `FIndexedCurve::GetNumKeys`
> (`ENGINE_API virtual int32 GetNumKeys() const PURE_VIRTUAL(FIndexedCurve::GetNumKeys, return 0;)` — Curves/IndexedCurve.h:41) —
> virtual dispatch, so simple-curve rows work too (key dumps limited to rich rows; eval works for both).
> `row` on a UCurveBase asset ⇒ error `"row only applies to CurveTable assets"`.
> **Phase-2 correction to this note**: the write side is mode-gated — `AddRichCurve` trips
> `check(CurveTableMode != ECurveTableMode::SimpleCurves)` (CurveTable.cpp:554-557) ⇒ editor crash
> on simple-curve tables (and CSV-created tables are simple-mode unless interp=cubic —
> CurveTable.cpp:671). Gate on `GetCurveTableMode()` (inline, CurveTable.h:56) and refuse
> simple-mode tables with `"curve table '<path>' holds simple curves — set_curve_keys currently
> supports rich-curve tables only"` (or add an FSimpleCurve write path later).

### create_string_table
**Purpose**: Create a UStringTable asset — first step of a localization-ready text pipeline; nothing in the 159 touches string tables.
**Engine API**:
```cpp
// Editor/UnrealEd/Classes/Factories/StringTableFactory.h:10-17
UCLASS(hidecategories=Object, MinimalAPI)
class UStringTableFactory : public UFactory
{
	UNREALED_API virtual UObject* FactoryCreateNew(UClass* Class, UObject* InParent, FName Name, EObjectFlags Flags, UObject* Context, FFeedbackContext* Warn) override;  // line 16
};
// impl is just NewObject<UStringTable> (EditorFactories.cpp:7917-7920)
// Runtime/Engine/Public/Internationalization/StringTable.h:35
ENGINE_API FStringTableRef GetMutableStringTable() const;
// Runtime/Core/Public/Internationalization/StringTableCore.h:111
CORE_API void SetNamespace(const FString& InNamespace);
```
**Export**: `UNREALED_API` method-level; UStringTable is UCLASS(MinimalAPI) with `ENGINE_API` accessors (StringTable.h:11,29-35); FStringTable methods all `CORE_API` method-level (class itself unexported — StringTableCore.h:80 — fine, we only call exported members through a ref). | **Module**: none. | **Guards**: none.
**Bucket**: self-managed — asset creation.
**Async**: no.
**Params**: | path | — | string /Game/... | — | yes | ; | namespace | ns | string | asset name | no (SetNamespace after create) |
**Failure modes**: package exists ⇒ pre-check; empty namespace allowed (engine default); unknown params ⇒ error naming them.
**Cooked**: creates loose assets.
**Verify**: list_string_table_entries returns count 0 and the namespace string.
**Score**: U4 E1 R2 → tier 1.
**Phase-2 verdict**: CONFIRMED — StringTableFactory.h:10-17 exact (FactoryCreateNew UNREALED_API at :16, class MinimalAPI :10); impl is bare `NewObject<UStringTable>` (EditorFactories.cpp:7917-7920); GetMutableStringTable ENGINE_API (StringTable.h:35); SetNamespace CORE_API (StringTableCore.h:111). This factory declares NO ConfigureProperties ⇒ no modal trap on this creator.

### set_string_table_entry
**Purpose**: Add/update/remove source strings in a string table — makes agent-authored UI text localizable instead of hard-coded literals in widgets.
**Engine API**:
```cpp
// Runtime/Core/Public/Internationalization/StringTableCore.h:117
CORE_API void SetSourceString(const FTextKey& InKey, const FString& InSourceString);
// StringTableCore.h:120
CORE_API void RemoveSourceString(const FTextKey& InKey);
// StringTableCore.h:114
CORE_API bool GetSourceString(const FTextKey& InKey, FString& OutSourceString) const;
```
Reached via `UStringTable::GetMutableStringTable()` (`ENGINE_API FStringTableRef` — StringTable.h:35). Mark dirty via MarkPackageDirty on the UStringTable.
**Export**: all `CORE_API` method-level. | **Module**: none (Core already linked). | **Guards**: none.
**Bucket**: transacted — data edit on one asset. HONESTY NOTE: FStringTable data is NOT a UPROPERTY (private FStringTablePtr — StringTable.h:39) so undo does NOT restore entries; the response must carry `undoable:false` rather than pretending.
**Async**: no.
**Params**: | path | table | string | — | yes | ; | key | — | string | — | yes | ; | value | text, sourceString | string | — | required unless remove=true | ; | remove | delete | bool | false | no |
**Failure modes**: remove of missing key ⇒ report `removed:false` (RemoveSourceString is void — pre-check with GetSourceString); asset not a UStringTable ⇒ typed error; both value and remove given ⇒ error naming the conflict.
**Cooked**: cooked string tables load their entries; in-memory edit works, unsavable (`savable:false`).
**Verify**: list_string_table_entries shows the key with the exact value; entry count delta ±1.
**Score**: U4 E1 R1 → tier 1.
**Phase-2 verdict**: CONFIRMED — SetSourceString (StringTableCore.h:117), RemoveSourceString (:120), GetSourceString (:114) all CORE_API method-level on the unexported FStringTable class (:80), reached via GetMutableStringTable (StringTable.h:35). The undoable:false honesty note re-verified: data lives in private non-UPROPERTY `FStringTablePtr StringTable;` (StringTable.h:38-39).

### list_string_table_entries
**Purpose**: Dump all key→source-string pairs (+namespace) of a string table — read half of the pair, works on cooked base-game text tables too.
**Engine API**:
```cpp
// Runtime/Core/Public/Internationalization/StringTableCore.h:124
CORE_API void EnumerateKeysAndSourceStrings(const TFunctionRef<bool(const FTextKey&, const FString&)>& InEnumerator) const;
// StringTableCore.h:108
CORE_API FString GetNamespace() const;
// Runtime/Engine/Public/Internationalization/StringTable.h:32
ENGINE_API FStringTableConstRef GetStringTable() const;
```
**Export**: `CORE_API`/`ENGINE_API` method-level as cited. | **Module**: none. | **Guards**: none.
**Bucket**: read-only.
**Async**: no.
**Params**: | path | table | string | — | yes | ; | filter | contains | string (substring on key or value) | "" | no | ; | maxEntries | — | int 1..10000 | 1000 | no |
**Failure modes**: not a UStringTable ⇒ typed error.
**Cooked**: WORKS — string table entries survive cooking (they are the runtime text source).
**Verify**: count field; re-run with filter equals subset count.
**Score**: U3 E1 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — EnumerateKeysAndSourceStrings (StringTableCore.h:124), GetNamespace (:108), GetStringTable (StringTable.h:32) all exact, CORE_API/ENGINE_API method-level as claimed.

### get_config_value
**Purpose**: Structured read of any .ini value or whole section (merged runtime view or a specific file) — agents currently guess at config state or scrape run_console output.
**Engine API**:
```cpp
// Runtime/Core/Public/Misc/ConfigCacheIni.h:762
CORE_API bool GetString( const TCHAR* Section, const TCHAR* Key, FString& Value, const FString& Filename );
// ConfigCacheIni.h:764
CORE_API bool GetSection( const TCHAR* Section, TArray<FString>& Result, const FString& Filename );
// ConfigCacheIni.h:802
CORE_API bool GetSectionNames( const FString& Filename, TArray<FString>& out_SectionNames );
// Runtime/Core/Public/CoreGlobals.h:96
extern CORE_API FConfigCacheIni* GConfig;
// CoreGlobals.h:391,399,406,407
extern CORE_API FString GEngineIni;  // GEditorIni:399, GInputIni:406, GGameIni:407 — same form
```
**Export**: all `CORE_API`. | **Module**: none. | **Guards**: none.
**Bucket**: read-only.
**Async**: no.
**Params**: | file | — | string enum `engine`\|`game`\|`editor`\|`input` (→ G*Ini merged views) or `DefaultGame`\|`DefaultEngine`\|`DefaultEditor`\|`DefaultInput` (→ FPaths::SourceConfigDir() files) | game | no | ; | section | — | string | — | no (omitted ⇒ GetSectionNames) | ; | key | — | string | — | no (omitted ⇒ whole section via GetSection) |
Explicit allowlist only — arbitrary file paths REFUSED (error: `"file must be one of engine|game|editor|input|DefaultGame|DefaultEngine|DefaultEditor|DefaultInput"`).
**Failure modes**: unknown section ⇒ `found:false` + section-name suggestions; key missing ⇒ `found:false` (absence is data, not an error).
**Cooked**: unaffected (config is not asset content).
**Verify**: read [/Script/EngineSettings.GameMapsSettings] GameDefaultMap and diff against the on-disk DefaultEngine.ini text.
**Score**: U4 E1 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — GetString (ConfigCacheIni.h:762), GetSection (:764), GetSectionNames (:802), GConfig (CoreGlobals.h:96), GEngineIni (:391), GEditorIni (:399), GInputIni (:406), GGameIni (:407) all exact and CORE_API.

### set_config_value
**Purpose**: Write a config value to a Default*.ini (project source config) with explicit flush — the config side of "make this setting stick", currently impossible without hand-editing files.
**Engine API**:
```cpp
// Runtime/Core/Public/Misc/ConfigCacheIni.h:771
CORE_API void SetString( const TCHAR* Section, const TCHAR* Key, const TCHAR* Value, const FString& Filename );
// ConfigCacheIni.h:773
CORE_API bool RemoveKey( const TCHAR* Section, const TCHAR* Key, const FString& Filename );
// ConfigCacheIni.h:755
CORE_API void Flush(bool bRemoveFromCache, const FString& Filename=TEXT(""));
```
**Export**: `CORE_API`. | **Module**: none. | **Guards**: none.
**Bucket**: self-managed — file I/O, not a UObject edit; nothing to transact (undo cannot restore an ini). Response echoes the previous value so the agent can restore.
**Async**: no.
**Params**: | file | — | `DefaultGame`\|`DefaultEngine`\|`DefaultEditor`\|`DefaultInput` ONLY (never the merged G*Ini caches, never arbitrary paths) | — | yes | ; | section | — | string | — | yes | ; | key | — | string | — | yes | ; | value | — | string | — | required unless remove=true | ; | remove | — | bool | false | no | ; | confirm | — | bool | false | YES |
**Failure modes**: missing confirm ⇒ `"set_config_value modifies <file>.ini and requires confirm=true (previous value is returned for rollback)"`; file not in allowlist ⇒ error naming allowed values. RISK flag (explicit, per axis brief): config writes change editor/game behaviour on next load; the write does NOT hot-apply to already-constructed objects ⇒ response carries `appliedLive:false`.
**Cooked**: unaffected.
**Verify**: get_config_value round-trips the exact string; file mtime changed.
**Score**: U3 E2 R3 → tier 2 — risk flagged, confirm-gated + old-value echo.
**Phase-2 verdict**: CONFIRMED — SetString (ConfigCacheIni.h:771), RemoveKey (:773), Flush (:755) all exact and CORE_API. Self-managed bucket is right (file I/O, nothing transactable).

### get_settings_config_source
**Purpose**: For any UDeveloperSettings-derived class, report WHICH ini file + section its values live in (the config-file side of settings; axis A owns enumerating settings classes — coordinated split, see brief).
**Engine API**:
```cpp
// Runtime/DeveloperSettings/Public/Engine/DeveloperSettings.h:31-35
DEVELOPERSETTINGS_API virtual FName GetContainerName() const;
DEVELOPERSETTINGS_API virtual FName GetCategoryName() const;
DEVELOPERSETTINGS_API virtual FName GetSectionName() const;
// Runtime/CoreUObject/Public/UObject/Object.h:1279
COREUOBJECT_API FString GetDefaultConfigFilename() const;
```
**Export**: `DEVELOPERSETTINGS_API` method-level (class UCLASS(Abstract, MinimalAPI) — DeveloperSettings.h:22-23); `COREUOBJECT_API` for the filename. | **Module**: **NEW DEP — `DeveloperSettings`** (runtime module, core engine, always loaded; fine for an editor-only plugin, no runtime leak since MifBridge itself never ships). | **Guards**: none.
**Bucket**: read-only (CDO inspection only).
**Async**: no.
**Params**: | class | settingsClass | string (class path or short name via existing ResolveClassStrict) | — | yes |
**Failure modes**: class not a UDeveloperSettings child ⇒ `"'<class>' does not derive from DeveloperSettings"`; abstract base itself passed ⇒ same error.
**Cooked**: unaffected (CDO-based).
**Verify**: for GameMapsSettings expect a filename ending `DefaultEngine.ini` (per-class override respected) and section `/Script/EngineSettings.GameMapsSettings`; cross-check with get_config_value.
**Score**: U2 E1 R5 → tier 2 — glue that makes get/set_config_value discoverable.
**Phase-2 verdict**: CONFIRMED — UCLASS(Abstract, MinimalAPI) (DeveloperSettings.h:22-23); GetContainerName/GetCategoryName/GetSectionName DEVELOPERSETTINGS_API method-level (:31/:33/:35); GetDefaultConfigFilename COREUOBJECT_API (Object.h:1279). New-dep claim verified against MifBridge.Build.cs (read in full — DeveloperSettings absent); module lives at Runtime/DeveloperSettings (runtime, engine-core, always loaded).

### list_cultures
**Purpose**: Enumerate cultures known to the engine's ICU data — read-only grounding for localization work (which locales exist to translate into).
**Engine API**:
```cpp
// Runtime/Core/Public/Internationalization/Internationalization.h:28
static CORE_API FInternationalization& Get();
// Internationalization.h:194
CORE_API void GetCultureNames(TArray<FString>& CultureNames) const;
```
**Export**: `CORE_API`. | **Module**: none. | **Guards**: none.
**Bucket**: read-only.
**Async**: no.
**Params**: | filter | prefix | string | "" | no |
**Failure modes**: none meaningful; missing ICU data ⇒ count 0 reported, not an error.
**Cooked**: unaffected.
**Verify**: count > 100 on a stock ICU build; "en" present.
**Score**: U2 E1 R5 → tier 2.
**Phase-2 verdict**: CONFIRMED — FInternationalization::Get (`static CORE_API`, Internationalization.h:28), GetCultureNames (CORE_API, :194) exact.

### list_savegames
**Purpose**: Enumerate save-game files (name, size, timestamp) under the project's SaveGames dir — grounding for read_savegame and for "did PIE actually save?" checks.
**Engine API**:
```cpp
// Runtime/Engine/Public/SaveGameSystem.h:46 (ISaveGameSystem; generic impl at :134)
virtual bool GetSaveGameNames(TArray<FString>& FoundSaves, const int32 UserIndex)
// SaveGameSystem.h:137 — the location convention, verbatim:
const FString SaveGameDirectory = FPaths::ProjectSavedDir() / TEXT("SaveGames/");
// Runtime/Core/Public/HAL/FileManager.h:147 + :67
virtual void FindFilesRecursive( TArray<FString>& FileNames, const TCHAR* StartDirectory, const TCHAR* Filename, bool Files, bool Directories, bool bClearFileNames=true) = 0; // utility
static CORE_API IFileManager& Get();
```
**Export**: `IFileManager::Get()` is `static CORE_API`; FindFilesRecursive is pure virtual (vtable dispatch — no link needed). File scan is authoritative and also catches RamaSave-format files regardless of extension. | **Module**: none. | **Guards**: none.
**Bucket**: read-only.
**Async**: no.
**Params**: | pattern | ext | string filename wildcard (`*.sav`) | `*.*` | no | ; | dir | — | string RELATIVE subdir under Saved/SaveGames only | "" | no |
**Failure modes**: dir escapes SaveGames root (`..`, absolute) ⇒ `"dir must stay under Saved/SaveGames"`; missing dir ⇒ count 0.
**Cooked**: unaffected (filesystem, not assets). Scope note: only the PROJECT's save dir — a player's shipped-game saves live elsewhere by design.
**Verify**: count/sizes match a directory listing. Sweep-time ground truth: `D:/DDS2SDK/Game/Saved/SaveGames/` contains exactly one file, `UserSettings.sav`.
**Score**: U3 E1 R5 → tier 2.
**Phase-2 verdict**: CONFIRMED — ISaveGameSystem::GetSaveGameNames default body `{ return false; }` (SaveGameSystem.h:46-49); FGenericSaveGameSystem override at :134-145 with the `FPaths::ProjectSavedDir() / TEXT("SaveGames/")` convention verbatim at :137; IFileManager::Get `static CORE_API` (FileManager.h:67); FindFilesRecursive pure-virtual (:147). The file-scan-as-authoritative design is right.

### read_savegame
**Purpose**: Load a standard UE .sav slot and dump the USaveGame object's properties through the existing reflection serializer — inspect PIE-produced saves numerically.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Kismet/GameplayStatics.h:1190
static ENGINE_API USaveGame* LoadGameFromSlot(const FString& SlotName, const int32 UserIndex);
// GameplayStatics.h:1154
static ENGINE_API bool DoesSaveGameExist(const FString& SlotName, const int32 UserIndex);
// GameplayStatics.h:1161
static ENGINE_API USaveGame* LoadGameFromMemory(const TArray<uint8>& InSaveData);
```
USaveGame is `UCLASS(abstract, Blueprintable, BlueprintType, MinimalAPI)` (Runtime/Engine/Classes/GameFramework/SaveGame.h:22-23) — property dump reuses the existing list_object_properties reflection code; no new serializer.
**Export**: `ENGINE_API` static methods. | **Module**: none. | **Guards**: none.
**Bucket**: read-only — loads a transient object (protect with FGCObjectScopeGuard while serializing).
**Async**: no — sync read of small saves is fine mid-frame; add a size guard (refuse > 64 MB with a clear error) instead of pretending to be async.
**Params**: | slot | slotName, name | string (no extension — engine appends .sav) | — | yes | ; | userIndex | — | int | 0 | no | ; | maxDepth | — | int | 4 | no |
**Failure modes**: slot missing ⇒ DoesSaveGameExist pre-check ⇒ `"slot '<s>' not found under Saved/SaveGames (list_savegames to enumerate)"`; not a UE SaveGame archive (e.g. RamaSave format) ⇒ LoadGameFromSlot returns null ⇒ `"file exists but is not a standard UE SaveGame archive — DDS2 gameplay saves use RamaSaveSystem (see read_rama_savefile)"`; save's USaveGame subclass unknown to the editor ⇒ null ⇒ same message + cooked-class hint.
**Cooked**: reads files, not assets. Verified-risk note: deserialization needs the save's USaveGame subclass to exist in-editor (native in modkit build, or BP class in a mounted .pak); otherwise null.
**Verify**: round-trip — save in PIE, read the slot, property values equal what was set before saving.
**Score**: U3 E2 R4 → tier 2.
**Phase-2 verdict**: CONFIRMED — LoadGameFromSlot (GameplayStatics.h:1190), DoesSaveGameExist (:1154), LoadGameFromMemory (:1161) all `static ENGINE_API`; USaveGame `UCLASS(abstract, Blueprintable, BlueprintType, MinimalAPI)` (SaveGame.h:22-23). Sync-with-size-guard call is honest (LoadGameFromSlot is itself synchronous engine-wide; the async variant is delegate-based — GameplayStatics.h:1181 — needless complexity for editor use).

### list_primary_assets
**Purpose**: Read-only dump of UAssetManager's primary asset types and their asset ids — shows how DDS2 organizes scannable content (maps, labels, chunks) without the asset-registry query language.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Engine/AssetManager.h:56
static ENGINE_API UAssetManager& Get();
// AssetManager.h:53
static ENGINE_API bool IsInitialized();
// AssetManager.h:221
ENGINE_API virtual void GetPrimaryAssetTypeInfoList(TArray<FPrimaryAssetTypeInfo>& AssetTypeInfoList) const;
// AssetManager.h:215
ENGINE_API virtual bool GetPrimaryAssetIdList(FPrimaryAssetType PrimaryAssetType, TArray<FPrimaryAssetId>& PrimaryAssetIdList, EAssetManagerFilter Filter = EAssetManagerFilter::Default) const;
// AssetManager.h:194
ENGINE_API virtual FSoftObjectPath GetPrimaryAssetPath(const FPrimaryAssetId& PrimaryAssetId) const;
```
`FPrimaryAssetTypeInfo` is a USTRUCT (Runtime/Engine/Classes/Engine/AssetManagerTypes.h:135-136) — serialize its fields via reflection.
**Export**: all `ENGINE_API` method-level (UCLASS(MinimalAPI) — AssetManager.h:41). | **Module**: none. | **Guards**: none — but gate on `UAssetManager::IsInitialized()`.
**Bucket**: read-only.
**Async**: no (pure list read; ScanPathsForPrimaryAssets deliberately NOT exposed).
**Params**: | type | — | string (omit ⇒ types overview; set ⇒ ids for that type) | "" | no | ; | includePaths | — | bool | false | no |
**Failure modes**: not initialized ⇒ `"asset manager not initialized yet — retry after editor finishes loading"`; unknown type ⇒ error echoing known type names.
**Cooked**: WORKS — primary asset info comes from the asset registry incl. .pak-mounted content.
**Verify**: types include the built-ins Map / PrimaryAssetLabel / PackageChunk (statics at AssetManager.h:67-73); per-type id counts are numbers cross-checkable against find_assets.
**Score**: U3 E1 R5 → tier 1.
**Phase-2 verdict**: CONFIRMED — UCLASS(MinimalAPI) (AssetManager.h:41), IsInitialized (:53), Get (:56), GetPrimaryAssetPath (:194), GetPrimaryAssetIdList (:215), GetPrimaryAssetTypeInfoList (:221), built-in type statics (:67-73) all exact ENGINE_API; FPrimaryAssetTypeInfo USTRUCT (AssetManagerTypes.h:134-138).

### set_struct_member
**Purpose**: Retype / re-default / rename an EXISTING UserDefinedStruct member in place — today the only route is remove+re-add, which reorders members and breaks graph pins. Closes the struct half of the element-addressing gap.
**Engine API**:
```cpp
// Editor/UnrealEd/Public/Kismet2/StructureEditorUtils.h:88
static UNREALED_API bool ChangeVariableType(UUserDefinedStruct* Struct, FGuid VarGuid, const FEdGraphPinType& NewType);
// StructureEditorUtils.h:90
static UNREALED_API bool ChangeVariableDefaultValue(UUserDefinedStruct* Struct, FGuid VarGuid, const FString& NewDefaultValue);
// StructureEditorUtils.h:84
static UNREALED_API bool RenameVariable(UUserDefinedStruct* Struct, FGuid VarGuid, const FString& NewDisplayNameStr);
// StructureEditorUtils.h:92
static UNREALED_API bool IsUniqueVariableFriendlyName(const UUserDefinedStruct* Struct, const FString& DisplayName);
```
MifBridge already builds FEdGraphPinType from type strings and resolves member GUIDs (H_add_struct_member / H_remove_struct_member use AddVariable/RemoveVariable/GetVarDesc — MifBridgeUserTypes.cpp:133-146,289,332) — reuse that machinery verbatim.
**Export**: `UNREALED_API` method-level statics. | **Module**: none. | **Guards**: none.
**Bucket**: self-managed — Change* triggers struct recompile + dependent-BP reinstancing (the hazard class the brief flags for compiles inside an outer transaction; MifBridgeUserTypes.cpp:7 already documents the stale-struct pitfall).
**Async**: no.
**Params**: | struct | path | string | — | yes | ; | member | name | string (display name → GUID via GetVarDesc, like existing handlers) | — | yes | ; | type | newType | string pin-type (same grammar as add_struct_member) | — | at least one of type/default/newName required | ; | default | defaultValue | string | — | — | ; | newName | rename | string | — | — |
**Failure modes**: member not found ⇒ error listing current member names; bad type string ⇒ reuse add_struct_member's resolver errors; rename collision ⇒ IsUniqueVariableFriendlyName pre-check ⇒ error; native struct ⇒ `"'<path>' is a native struct — only /Game UserDefinedStructs are editable"`.
**Cooked**: refuses on .pak structs (editor-only struct description data stripped) — detect and say so.
**Verify**: list_struct_members shows the new type/default/name; validate endpoint reports dependent-BP compile results.
**Score**: U4 E2 R2 → tier 1.
**Phase-2 verdict**: CONFIRMED — ChangeVariableType (StructureEditorUtils.h:88), ChangeVariableDefaultValue (:90), RenameVariable (:84; a string-name overload also exists at :86), IsUniqueVariableFriendlyName (:92) all UNREALED_API statics, exact. Existing-machinery citations spot-verified (MifBridgeUserTypes.cpp: AddVariable :133, GetVarDesc :138-144, RemoveVariable :387). Self-managed bucket is right for the reinstancing cascade.

### move_struct_member
**Purpose**: Reorder UserDefinedStruct members (pairs with set_struct_member to fully close in-place struct editing).
**Engine API**:
```cpp
// Editor/UnrealEd/Public/Kismet2/StructureEditorUtils.h:124
static UNREALED_API bool MoveVariable(UUserDefinedStruct* Struct, FGuid MoveVarGuid, FGuid RelativeToGuid, EMovePosition Position);
// StructureEditorUtils.h:127
static UNREALED_API bool CanMoveVariable(UUserDefinedStruct* Struct, FGuid MoveVarGuid, FGuid RelativeToGuid, EMovePosition Position);
```
(`EMovePosition` declared in the same header above MoveVariable.)
**Export**: `UNREALED_API` statics. | **Module**: none. | **Guards**: none.
**Bucket**: self-managed — same recompile cascade as set_struct_member.
**Async**: no.
**Params**: | struct | path | string | — | yes | ; | member | name | string | — | yes | ; | relativeTo | target | string member name | — | yes | ; | position | — | `above`\|`below` | below | no |
**Failure modes**: CanMoveVariable false ⇒ `moved:false` + reason; unknown member(s) ⇒ error naming which of the two params failed.
**Cooked**: refuses (as set_struct_member).
**Verify**: list_struct_members order index changes exactly as requested.
**Score**: U2 E1 R2 → tier 2.
**Phase-2 verdict**: CONFIRMED — MoveVariable (StructureEditorUtils.h:124), CanMoveVariable (:127) exact; `EMovePosition` is nested inside FStructureEditorUtils (:108-112, values PositionAbove/PositionBelow) — qualify as `FStructureEditorUtils::PositionAbove` at call sites.

### read_rama_savefile
**Purpose**: Read the static-data header of a DDS2 gameplay save (RamaSaveSystem format) — THE save format this game actually uses; read_savegame cannot parse it.
**Engine API** (project plugin — paths relative to D:/DDS2SDK/Game/Plugins):
```cpp
// Plugins_RamaThumb/RamaSaveSystem/Source/RamaSaveSystem/Public/RamaSaveLibrary.h:45-46
UFUNCTION(BlueprintCallable)
static URamaSaveObject* RamaSave_LoadStaticDataFromFile(bool& FileIOSuccess, const FString& Filename);
// RamaSaveLibrary.h:12-13
UCLASS(Blueprintable)
class RAMASAVESYSTEM_API URamaSaveLibrary : public UBlueprintFunctionLibrary { ... };
// also present: :43 RamaSave_LoadStreamingStateFromFile(...), :88 RamaFileIO_GetFiles(...)
```
**Export**: class-level `RAMASAVESYSTEM_API` + BlueprintCallable. DECISIVE CAVEAT (verified by reading the plugin source): the shipped Source tree is an SDK RECONSTRUCTION with STUB BODIES — `RamaSaveLibrary.cpp` returns `false`/`NULL`/`TEXT("")` from every function (read directly; e.g. `RenameFile { return false; }`). The real implementation exists only in the prebuilt `Binaries/Win64/UnrealEditor-RamaSaveSystem.dll`, and no import `.lib` ships ⇒ static linking impossible AND behaviour depends on which DLL the editor loads. Reflection route (`FindFunction` + `ProcessEvent` on the CDO — the route the brief blesses for BlueprintCallable) executes whatever the loaded DLL contains: real code if prebuilt, silent no-ops (`FileIOSuccess=false`) if locally rebuilt from stubs. MUST be probed live before implementation (one reflection call against a known-good save).
| **Module**: none added — reflection-only; do NOT add RamaSaveSystem to Build.cs. | **Guards**: none.
**Bucket**: read-only (transient URamaSaveObject; GC-guard during dump).
**Async**: no (static header only; RamaSave_LoadFromFile — the world-mutating loader — deliberately NOT exposed).
**Params**: | file | filename, path | string (absolute or Saved/SaveGames-relative) | — | yes |
**Failure modes**: FileIOSuccess false ⇒ `"RamaSave read failed — file missing/corrupt OR the editor's RamaSaveSystem.dll is the stub build (see audit note); test with a save the game itself wrote"`; null object with success=true ⇒ report both fields honestly; URamaSaveObject subclass only in game pak ⇒ partial base-class dump.
**Cooked**: file-based; the concrete URamaSaveObject subclass likely lives in the game's pak — loadable as a BP class, properties dump via reflection.
**Verify**: on a real game-written save: FileIOSuccess=true + non-null object + ≥1 non-default property.
**Score**: U3 E2 R3 → tier 3 — exotic until the stub-vs-real DLL question is answered live.
**Phase-2 verdict**: CONFIRMED — RamaSaveLibrary.h:12-13 (`UCLASS(Blueprintable)` + `RAMASAVESYSTEM_API`), RamaSave_LoadStaticDataFromFile :45-46, RamaSave_LoadStreamingStateFromFile :42-43, RamaFileIO_GetFiles :87-88 all exact; stub bodies re-read (RamaSaveLibrary.cpp:39-41 `return NULL;`, :6-8 `return false;`); Binaries/Win64 glob shows ONLY UnrealEditor-RamaSaveSystem.dll + .pdb + UnrealEditor.modules — no import .lib, so reflection-only route stands. Tier-3-pending-live-probe stance is correct.

## Compositions (no new endpoint needed)

- **DataAsset instance editing**: a UDataAsset/UPrimaryDataAsset instance is just properties on
  `/Game/Path/Asset.Asset` — fully covered by existing `set_property`/`get_property`/
  `list_object_properties`. No endpoint proposed.
- **DataAsset creation**: expected to be claimed by axis B's generic create_asset design. Facts
  verified here for whoever implements it: `UDataAssetFactory` has
  `UPROPERTY(EditAnywhere, Category=DataAsset) TSubclassOf<UDataAsset> DataAssetClass;`
  (Editor/UnrealEd/Classes/Factories/DataAssetFactory.h:18-19) and its `FactoryCreateNew` is
  `NewObject<UDataAsset>(InParent, DataAssetClass, Name, Flags | RF_Transactional)` when the class
  is set (EditorFactories.cpp:7480-7492). CAUTION: `FactoryCreateNew` carries NO export macro on a
  MinimalAPI class (DataAssetFactory.h:23) — it can only be invoked through
  `IAssetTools::CreateAsset` vtable dispatch, never called directly (link error otherwise).
- **Composite table creation**: `UCompositeDataTableFactory` derives from UDataTableFactory and
  only overrides `MakeNewDataTable` (`UNREALED_API virtual UDataTable* MakeNewDataTable(UObject* InParent, FName Name, EObjectFlags Flags) override;`
  — Editor/UnrealEd/Classes/Factories/CompositeDataTableFactory.h:8-14). So create_datatable can
  take `composite:true` and swap the factory class — same code path, no second endpoint. Note the
  composite factory still requires `Struct` to be set (FactoryCreateNew is inherited and
  null-checks Struct — DataTableFactory.cpp:197).
- **Reading a composite table's parent list**: `get_property` with `property=ParentTables` — it is
  a UPROPERTY (CompositeDataTable.h:76-77), the existing reflection walker reads it. Only the
  WRITE needs the proposed set_composite_datatable_parents.
- **Whole-table JSON export**: already `read_datatable` (GetTableAsJSON). Only CSV was missing.
- **Enum value rename/reorder (verified, deliberately not proposed — cheap follow-ups if demanded)**:
  `static UNREALED_API bool SetEnumeratorDisplayName(UUserDefinedEnum* Enum, int32 EnumeratorIndex, FText NewDisplayName);`
  (Editor/UnrealEd/Public/Kismet2/EnumEditorUtils.h:95) and
  `static UNREALED_API void MoveEnumeratorInUserDefinedEnum(class UUserDefinedEnum* Enum, int32 InitialEnumeratorIndex, int32 TargetIndex);`
  (EnumEditorUtils.h:69). The existing `add_enum_value` already calls SetEnumeratorDisplayName at
  add time (MifBridgeUserTypes.cpp, enum section), so only rename-existing and reorder are
  uncovered; both are one-call handlers if a phase-2 axis wants them.
- **CVar-backed settings**: anything reachable as a console variable stays with
  run_console/run_console_captured per the brief; get/set_config_value is only for values that are
  NOT CVars (paths, class names, arrays in ini sections).

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

1. **RamaSaveSystem is stub-source + binary-only in practice.** The plugin at
   `D:/DDS2SDK/Game/Plugins/Plugins_RamaThumb/RamaSaveSystem` ships reconstructed headers and cpp
   files whose bodies are empty (`URamaSaveLibrary::RenameFile { return false; }`,
   `RamaSave_LoadStaticDataFromFile { return NULL; }` — Source/RamaSaveSystem/Private/RamaSaveLibrary.cpp,
   read directly; whole Private dir totals 227 lines for 10 cpp files). Real logic lives only in
   `Binaries/Win64/UnrealEditor-RamaSaveSystem.dll`; no import .lib ships, so MifBridge can never
   link against RAMASAVESYSTEM_API symbols. Reflection calls are the only route, and they no-op if
   the editor loaded a stub-rebuilt DLL. Consequence: DDS2's actual gameplay-save format has no
   guaranteed reader from MifBridge; read_rama_savefile is tier 3 pending a live probe, and a
   from-scratch .rsav parser would mean reverse-engineering an undocumented format (out of scope).
   **Phase-2: confirmed** — stub bodies re-read (RamaSaveLibrary.cpp:6-8, :39-41); Binaries/Win64 globbed: only .dll/.pdb/.modules, no import .lib.
2. **Localization text gathering is commandlet-only ⇒ blocking ⇒ not a sync endpoint.** The gather
   pipeline exists solely as commandlets — dir listing of
   `Editor/UnrealEd/Classes/Commandlets/` shows GatherTextCommandlet.h, GatherTextCommandletBase.h,
   GatherTextFromAssetsCommandlet.h, GatherTextFromMetadataCommandlet.h,
   GatherTextFromSourceCommandlet.h, GenerateGatherArchiveCommandlet.h,
   GenerateGatherManifestCommandlet.h. Running a commandlet in-process blocks the game thread for
   the whole gather (brief invariant 3 violation). Viable future shape: out-of-process
   `UnrealEditor-Cmd -run=GatherText -config=<ini>` behind a request+poll pair, exactly the
   trigger_cook precedent — tier 3, needs its own design pass (gather-target ini authoring is a
   prerequisite problem). NOT proposed this phase.
   **Phase-2: confirmed** — all 7 commandlet headers exist (5 GatherText*.h plus GenerateGatherArchiveCommandlet.h and GenerateGatherManifestCommandlet.h; dir globbed both prefixes).
3. **UDataAssetFactory / UCurveFactory methods are unexported on MinimalAPI classes**
   (DataAssetFactory.h:22-23, CurveFactory.h:26-27 — no method macros). Direct calls = link
   errors. Documented workaround everywhere in this file: NewObject the factory (MinimalAPI
   exports StaticClass), set its public UPROPERTY members (data access needs no symbol), hand it
   to IAssetTools::CreateAsset (virtual dispatch happens inside the AssetTools module).
   **Phase-2: confirmed** — CurveFactory.h:26-27 and DataAssetFactory.h:22-23 carry no method macros
   on MinimalAPI classes. EXTENDED: BOTH factories' ConfigureProperties are MODAL class pickers
   (SClassPickerDialog::PickClass — EditorFactories.cpp:7251 for UCurveFactory, :7470 for
   UDataAssetFactory), adding two more modal traps to negative #4's inventory.
4. **Two modal-dialog traps on the asset-creation path** (both verified in source, both fatal to a
   mid-frame HTTP handler): `UCurveTableFactory::ConfigureProperties` opens a modal window
   (`GEditor->EditorAddModalWindow(Window.ToSharedRef());` — Editor/UnrealEd/Private/Factories/CurveTableFactory.cpp:55),
   and `UAssetToolsImpl::CreateAsset` calls `CanCreateAsset(...)` which raises an overwrite
   confirmation dialog when the package exists (AssetTools.cpp:1647). Rules: never call
   ConfigureProperties on any factory; always pre-check package existence and fail with a clean
   error. `CreateAsset` itself never calls ConfigureProperties (body read, AssetTools.cpp:1627-1682).
   **Phase-2: confirmed** — CurveTableFactory.cpp:55 and AssetTools.cpp:1647 re-read; CanCreateAsset
   raises FMessageDialog at :4294 (invalid name), :4301 (map-name collision), :4331-4337 (overwrite
   YesNo); CreateAsset body re-read (:1627-1682), no ConfigureProperties call. Known modal traps on
   the creation path now number FIVE: DataTableFactory.cpp:176, CurveTableFactory.cpp:55,
   EditorFactories.cpp:7251 (UCurveFactory), :7470 (UDataAssetFactory), plus CanCreateAsset itself.
5. **String-table edits are not undoable.** UStringTable holds its data in a private non-UPROPERTY
   `FStringTablePtr StringTable;` (Runtime/Engine/Public/Internationalization/StringTable.h:38-39),
   so FScopedTransaction cannot snapshot entries. set_string_table_entry must report
   `undoable:false` instead of pretending; restore = re-set the previous value (the endpoint echoes it).
   **Phase-2: confirmed** — StringTable.h:38-39 re-read: `FStringTablePtr StringTable;` private, no UPROPERTY.
6. **UCompositeDataTable::ParentTables is protected** (CompositeDataTable.h:76-77) — no exported
   setter besides append (`AppendParentTables`, :58). Replace/remove requires the FProperty
   reflection route + manual PostEditChangeProperty to trigger `OnParentTablesUpdated`
   (hooked in PostEditChangeProperty — CompositeDataTable.h:52). Composite tables also reject row
   mutation by design ("Composite data tables don't currently add or remove rows" —
   CompositeDataTable.h:43) ⇒ row-op endpoints must detect UCompositeDataTable and refuse with a
   pointer at set_composite_datatable_parents.
   **Phase-2: confirmed** — CompositeDataTable.h:43/:58/:76-77 re-read; all as stated.
7. **ISaveGameSystem::GetSaveGameNames is unreliable as sole source** — the interface default
   returns false (SaveGameSystem.h:46-49 region), only FGenericSaveGameSystem implements it
   (:134). list_savegames therefore specifies the IFileManager scan of
   `FPaths::ProjectSavedDir()/SaveGames/` (convention verbatim at SaveGameSystem.h:137) as the
   authoritative path.
   **Phase-2: confirmed** — default body `{ return false; }` at SaveGameSystem.h:46-49; FGenericSaveGameSystem override :134-145.
8. **read_savegame cannot decode DDS2 gameplay saves** (they are RamaSave-format, see #1) and
   cannot decode any save whose USaveGame subclass the editor doesn't have loaded — both failure
   modes produce null from LoadGameFromSlot and are spelled out in the endpoint's error text.
   **Phase-2: confirmed** — follows from #1 (stub plugin) + LoadGameFromSlot signature (GameplayStatics.h:1190).
9. **Live bridge unreachable during this sweep** (curl exit 7 / HTTP 000 on 127.0.0.1:8791 for
   pie_status and list_datatables) — zero live confirmations; every "works on cooked" claim here
   is source-reasoned, and the three live probes listed in the Coverage log remain open.
   **Phase-2: confirmed as historical, now SUPERSEDED — bridge is live.** Probed this pass:
   `POST http://127.0.0.1:8791/api/pie_status` returns `{"ok":false,"error":"invalid or missing
   X-Mif-Token header"}` (curl exit 0). Token-holding sessions can run the open live probes.
10. **DeveloperSettings module is a NEW Build.cs dependency** for get_settings_config_source
    (runtime module, always loaded, editor-safe) — the only new module dep this axis proposes;
    every other proposal links with the existing dependency set.
    **Phase-2: confirmed** — MifBridge.Build.cs read in full this pass: DeveloperSettings absent from both dependency lists; module directory is Runtime/DeveloperSettings.

## UNVERIFIED

- `set_editor_culture` (FInternationalization::SetCurrentCulture) — signature not read this sweep;
  also globally changes editor UI language, dubious value vs risk. Left out.
- Whether `UCurveTableFactory::MakeNewCurveTable` bakes the rich/simple choice from its protected
  `InterpMode` member — FactoryCreateNew body only partially read (CurveTableFactory.cpp:24
  region); create_curve_table therefore specifies explicit `AddRichCurve`/CSV fill instead of
  relying on factory state.
  **Phase-2: RESOLVED** — body read in full (CurveTableFactory.cpp:63-77): it DOES bake the choice
  AND pre-seeds a "Curve" row (simple unless InterpMode==RCIM_Cubic; zero-init via CreateAsset ⇒
  linear ⇒ SimpleCurves mode). See the create_curve_table verdict for the AddRichCurve
  check()-crash hazard and the mandatory EmptyTable() (CurveTable.h:217) fix.
- FSimpleCurve key-struct export surface (SimpleCurve.h not opened) — read_curve's key dump for
  simple-curve CurveTable rows is spec'd eval-only until that header is verified.
- Which RamaSaveSystem DLL the modkit editor actually loads (prebuilt-real vs stub-rebuilt) —
  decides read_rama_savefile's fate; needs one live reflection probe.
- MirrorDataTableFactory.h (anim mirroring tables) — seen in factory dir listing, not opened; niche.
- FStringTableRegistry (Core) global-registry route — not needed for the asset-based endpoints,
  unswept.
- EDataTableExportFlags option set (DataTable.h references it in every exporter) — flags not
  enumerated; export endpoints ship with defaults until read.

## Coverage log

**Swept and closed**: DataTable factory/create (header+impl), row ops via FDataTableEditorUtils
(full 20-static surface enumerated), CSV/JSON round-trip (DataTable.h methods + guards), composite
tables (header fully read + factory), curve factories (header+ctor impls), FRichCurve structured
editing + eval (RichCurve.h:197-321), UCurveBase change-notify, curve tables (CurveTable.h exported
surface), DataAsset factory (analysis handed to axis B via Compositions), UAssetManager read-only
lists, UStringTable + FStringTable full entry API, string-table factory, GConfig read/write +
ini-file globals, UDeveloperSettings config-source trio, FInternationalization culture enumeration,
UE SaveGame load/enumerate path (GameplayStatics + SaveGameSystem + FileManager), RamaSaveSystem
plugin (all 15 public headers listed, RamaSaveLibrary.h fully read, stub-body discovery in
Private/), FStructureEditorUtils delta vs existing MifBridgeUserTypes.cpp handlers (type/default/
rename/move missing → proposed; enum delta → Compositions), gather-text commandlet inventory
(negative result).

**Open for phase 2 / live session**: (1) probe RamaSaveSystem DLL behaviour via one reflection
call; (2) confirm list_datatables/read_curve behaviour against live cooked content once the bridge
is up; (3) axis-A handshake on UDeveloperSettings enumeration (this file owns only the
config-file-side endpoint); (4) axis-B handshake on generic create_asset vs the three creators
proposed here (create_datatable/create_curve/create_curve_table/create_string_table stay valuable
even under a generic create_asset because they validate RowStruct/curve-class/namespace
semantics the generic path cannot); (5) localization gather request+poll design if demanded.

**Proposal count**: 25 entries (target was 10-16; the surface was rich and every entry is
source-verified — tiers mark the cut line: 2×T0, 13×T1, 9×T2, 1×T3).
