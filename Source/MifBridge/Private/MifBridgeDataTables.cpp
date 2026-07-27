// MifBridge — Phase 3 breadth: read-only DataTable access (list / read / row).
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "DataTableEditorUtils.h"
#include "DataTableUtils.h" // EDataTableExportFlags::UseSimpleText
#include "Engine/DataTable.h"
#include "JsonObjectConverter.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/UnrealType.h" // TFieldIterator<FTextProperty>
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
	namespace
	{
		// --- FText export format ------------------------------------------------
		// A user reported reads "double wrapping" DataTable descriptions with NSLOCTEXT. Nothing is
		// double-wrapped and nothing is corrupted; two engine behaviours combine:
		//
		//  1. UDataTable::GetTableAsJSON defaults to EDataTableExportFlags::None
		//     (Engine/Classes/Engine/DataTable.h:328). With None, every FText goes through
		//     ExportText_Direct — the full lossless form NSLOCTEXT("ns","key","source"). The
		//     readable alternative is gated on EDataTableExportFlags::UseSimpleText
		//     (DataTableUtils.cpp:213-219; "Export text properties as their display string, rather
		//     than their complex lossless form", DataTableUtils.h:21). UseSimpleText is LOSSY — it
		//     drops the namespace and key — so "export" stays the DEFAULT and "simple" is opt-in.
		//  2. write_datatable_rows' two modes disagree about FText, one flag apart.
		//     Merge (replace:false) parses through FJsonObjectConverter::JsonObjectToUStruct, which
		//     round-trips the NSLOCTEXT export form exactly. Replace (replace:true) goes through
		//     UDataTable::CreateTableFromJSONString -> DataTableUtils::AssignStringToProperty
		//     (DataTableJSON.cpp:753/772), which gives a PLAIN string assigned to an FText a
		//     generated namespace ("<TableName> [<guid>]") and key ("<RowName>_<ColumnName>").
		//     So plain text written by replace becomes a localized FText and reads back as
		//     NSLOCTEXT(...). It wraps ONCE and is then stable — verified live across three cycles,
		//     byte-identical from cycle 2 on, which is exactly what a genuinely localized FText does.
		//
		// The defects were therefore presentation and silence, not data loss: the read format was
		// hostile, and the merge/replace asymmetry was undocumented. Hence textFormat + textNote +
		// textLocalizationNote below.
		const TCHAR* const kTextFormatExport = TEXT("export");
		const TCHAR* const kTextFormatSimple = TEXT("simple");
		const TCHAR* const kTextFormatAccepted =
			TEXT("export (default; the lossless NSLOCTEXT form, safe to write back) ")
			TEXT("or simple (FText as its display string only; lossy - drops namespace and key)");

		const TCHAR* const kTextNote =
			TEXT("FText fields are exported in their lossless localized form ")
			TEXT("NSLOCTEXT(\"namespace\",\"key\",\"source\") - that is the engine's round-trip-safe export, ")
			TEXT("not a corrupted or double-wrapped value, and the readable display string is the third argument. ")
			TEXT("write_datatable_rows merge mode (replace:false) accepts these verbatim; pass ")
			TEXT("textFormat:\"simple\" for plain display strings (lossy - it drops the namespace and key).");

		const TCHAR* const kReplaceTextNote =
			TEXT("This row struct has at least one FText field. replace mode imports values through ")
			TEXT("DataTableUtils::AssignStringToProperty, which gives a PLAIN string assigned to an FText a ")
			TEXT("generated localization id - namespace \"<TableName> [<guid>]\", key \"<RowName>_<ColumnName>\". ")
			TEXT("Those fields will therefore read back as NSLOCTEXT(...) in the default textFormat:\"export\" ")
			TEXT("read; the display string is intact and nothing is corrupted. Merge mode (replace:false, the ")
			TEXT("default) parses through FJsonObjectConverter instead and does NOT do this - prefer it unless ")
			TEXT("you intend a full-table overwrite.");

		// Resolves textFormat / textMode / simpleText into ONE effective value. An unrecognised
		// VALUE is an error naming the accepted set - never a silent fall back to the default,
		// which is the failure mode that makes a typo look like the feature is broken.
		bool ResolveTextFormat(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
			EDataTableExportFlags& OutFlags, FString& OutEffective)
		{
			FString Resolved;

			const FString Raw = JStrAny(In, { TEXT("textFormat"), TEXT("textMode") }).TrimStartAndEnd().ToLower();
			if (!Raw.IsEmpty())
			{
				if (Raw != kTextFormatExport && Raw != kTextFormatSimple)
				{
					Fail(Out, FString::Printf(TEXT("unknown textFormat '%s' - accepted: %s"), *Raw, kTextFormatAccepted));
					return false;
				}
				Resolved = Raw;
			}

			// simpleText:true is the boolean spelling of textFormat:"simple".
			if (JHasAny(In, { TEXT("simpleText") }))
			{
				const FString FromBool = JBool(In, TEXT("simpleText"), false) ? kTextFormatSimple : kTextFormatExport;
				// Both spellings present and disagreeing: refuse rather than pick a winner silently.
				if (!Resolved.IsEmpty() && Resolved != FromBool)
				{
					Fail(Out, FString::Printf(
						TEXT("conflicting text format: textFormat resolves to '%s' but simpleText resolves to '%s' - pass only one"),
						*Resolved, *FromBool));
					return false;
				}
				Resolved = FromBool;
			}

			if (Resolved.IsEmpty())
			{
				Resolved = kTextFormatExport;
			}
			OutEffective = Resolved;
			OutFlags = (Resolved == kTextFormatSimple)
				? EDataTableExportFlags::UseSimpleText
				: EDataTableExportFlags::None;
			return true;
		}

		// True if any string anywhere below this value carries an NSLOCTEXT export. Scanning the
		// EMITTED values (post-truncation) rather than the raw table string keeps textNote honest:
		// a capped read whose surviving rows hold no localized text should stay quiet.
		bool ContainsNsLocText(const TSharedPtr<FJsonValue>& Value)
		{
			if (!Value.IsValid())
			{
				return false;
			}
			switch (Value->Type)
			{
			case EJson::String:
				return Value->AsString().Contains(TEXT("NSLOCTEXT("));
			case EJson::Array:
				for (const TSharedPtr<FJsonValue>& Item : Value->AsArray())
				{
					if (ContainsNsLocText(Item)) { return true; }
				}
				return false;
			case EJson::Object:
				if (const TSharedPtr<FJsonObject> Object = Value->AsObject())
				{
					for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Object->Values)
					{
						if (ContainsNsLocText(Pair.Value)) { return true; }
					}
				}
				return false;
			default:
				return false;
			}
		}

		bool ContainsNsLocText(const TArray<TSharedPtr<FJsonValue>>& Values)
		{
			for (const TSharedPtr<FJsonValue>& Value : Values)
			{
				if (ContainsNsLocText(Value)) { return true; }
			}
			return false;
		}

		// Only a row struct that actually exposes an FText can be bitten by replace mode's
		// generated-id behaviour, so only then is the warning worth emitting.
		bool RowStructHasTextProperty(const UScriptStruct* RowStruct)
		{
			if (!RowStruct)
			{
				return false;
			}
			for (TFieldIterator<FTextProperty> It(RowStruct); It; ++It)
			{
				return true;
			}
			return false;
		}

		UDataTable* LoadDataTable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
		{
			const FString Path = JStr(In, TEXT("path"));
			if (Path.IsEmpty())
			{
				Fail(Out, TEXT("path is required"));
				return nullptr;
			}
			UDataTable* Table = LoadObject<UDataTable>(nullptr, *Path, nullptr, LOAD_NoWarn);
			if (!Table)
			{
				Fail(Out, FString::Printf(TEXT("datatable not found: %s"), *Path));
			}
			return Table;
		}

		// Parse a JSON-array string into JSON values.
		bool ParseJsonArray(const FString& JsonText, TArray<TSharedPtr<FJsonValue>>& Out)
		{
			TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
			return FJsonSerializer::Deserialize(Reader, Out);
		}
	}

	// --- list_datatables ----------------------------------------------------

	void H_list_datatables(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString Filter = JStr(In, TEXT("filter"));
		FAssetRegistryModule& Module = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
		IAssetRegistry& Registry = Module.Get();

		TArray<FAssetData> Assets;
		Registry.GetAssetsByClass(UDataTable::StaticClass()->GetClassPathName(), Assets, /*bSearchSubClasses*/ true);

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (const FAssetData& Asset : Assets)
		{
			const FString ObjectPath = Asset.GetObjectPathString();
			if (!Filter.IsEmpty() && !ObjectPath.Contains(Filter))
			{
				continue;
			}
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("path"), ObjectPath);
			Json->SetStringField(TEXT("name"), Asset.AssetName.ToString());
			Arr.Add(MakeShared<FJsonValueObject>(Json));
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("datatables"), Arr);
	}

	// --- read_datatable -----------------------------------------------------

	void H_read_datatable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// 'op' is accepted because batch dispatches by handing the WHOLE op object to the handler
		// (MifBridgeNodes.cpp:1278) — omitting it would make the guard reject every batched call.
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("maxRows"), TEXT("textFormat"), TEXT("textMode"), TEXT("simpleText"), TEXT("op") },
			TEXT("path, maxRows, textFormat (aliases: textMode, simpleText:true)")))
		{
			return;
		}

		// Parsed BEFORE the asset is loaded so a bad value costs nothing.
		EDataTableExportFlags ExportFlags = EDataTableExportFlags::None;
		FString TextFormat;
		if (!ResolveTextFormat(In, Out, ExportFlags, TextFormat))
		{
			return;
		}

		UDataTable* Table = LoadDataTable(In, Out);
		if (!Table)
		{
			return;
		}

		Out->SetStringField(TEXT("path"), Table->GetPathName());
		Out->SetStringField(TEXT("textFormat"), TextFormat);
		if (const UScriptStruct* RowStruct = Table->GetRowStruct())
		{
			Out->SetStringField(TEXT("rowStruct"), RowStruct->GetName());
		}

		const TArray<FName> RowNames = Table->GetRowNames();
		Out->SetNumberField(TEXT("rowCount"), RowNames.Num());

#if WITH_EDITOR
		const int32 MaxRows = FMath::Clamp(JInt(In, TEXT("maxRows"), 500), 1, 10000);
		TArray<TSharedPtr<FJsonValue>> Rows;
		if (ParseJsonArray(Table->GetTableAsJSON(ExportFlags), Rows))
		{
			if (Rows.Num() > MaxRows)
			{
				Rows.SetNum(MaxRows);
				Out->SetBoolField(TEXT("truncated"), true);
			}
			// Only when the caller is actually looking at NSLOCTEXT — clean tables stay quiet.
			if (TextFormat == kTextFormatExport && ContainsNsLocText(Rows))
			{
				Out->SetStringField(TEXT("textNote"), kTextNote);
			}
			Out->SetArrayField(TEXT("rows"), Rows);
		}
		else
		{
			Out->SetStringField(TEXT("warning"), TEXT("could not serialise table rows to JSON (null row struct?)"));
		}
#else
		Out->SetStringField(TEXT("warning"), TEXT("row dump requires an editor build"));
#endif
	}

	// --- get_datatable_row --------------------------------------------------

	void H_get_datatable_row(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// 'op' — see the note on H_read_datatable.
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("rowName"), TEXT("textFormat"), TEXT("textMode"), TEXT("simpleText"), TEXT("op") },
			TEXT("path, rowName, textFormat (aliases: textMode, simpleText:true)")))
		{
			return;
		}

		EDataTableExportFlags ExportFlags = EDataTableExportFlags::None;
		FString TextFormat;
		if (!ResolveTextFormat(In, Out, ExportFlags, TextFormat))
		{
			return;
		}

		UDataTable* Table = LoadDataTable(In, Out);
		if (!Table)
		{
			return;
		}
		const FString RowName = JStr(In, TEXT("rowName"));
		if (RowName.IsEmpty())
		{
			Fail(Out, TEXT("rowName is required"));
			return;
		}
		if (!Table->GetRowMap().Contains(FName(*RowName)))
		{
			Fail(Out, FString::Printf(TEXT("row '%s' not found in %s"), *RowName, *Table->GetName()));
			return;
		}

		Out->SetStringField(TEXT("path"), Table->GetPathName());
		Out->SetStringField(TEXT("rowName"), RowName);
		Out->SetStringField(TEXT("textFormat"), TextFormat);

#if WITH_EDITOR
		// GetTableAsJSON emits a "Name" field per row; find ours.
		TArray<TSharedPtr<FJsonValue>> Rows;
		if (ParseJsonArray(Table->GetTableAsJSON(ExportFlags), Rows))
		{
			for (const TSharedPtr<FJsonValue>& Value : Rows)
			{
				const TSharedPtr<FJsonObject>* RowObj = nullptr;
				if (Value.IsValid() && Value->TryGetObject(RowObj) && RowObj)
				{
					FString ThisName;
					if ((*RowObj)->TryGetStringField(TEXT("Name"), ThisName) && ThisName == RowName)
					{
						// Scoped to THIS row, so a table with one localized row elsewhere stays quiet here.
						if (TextFormat == kTextFormatExport && ContainsNsLocText(Value))
						{
							Out->SetStringField(TEXT("textNote"), kTextNote);
						}
						Out->SetObjectField(TEXT("row"), *RowObj);
						return;
					}
				}
			}
		}
		Out->SetStringField(TEXT("warning"), TEXT("row exists but could not be serialised to JSON"));
#else
		Out->SetStringField(TEXT("warning"), TEXT("row dump requires an editor build"));
#endif
	}

	// --- write_datatable_rows (confirm-gated) -------------------------------

	void H_write_datatable_rows(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// 'op' — see the note on H_read_datatable. No textFormat here: writes never render FText,
		// they only parse it, and the two modes' parsers differ (see kReplaceTextNote).
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("rows"), TEXT("replace"), TEXT("confirm"), TEXT("op") },
			TEXT("path, rows, replace, confirm")))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("write_datatable_rows requires confirm=true"));
			return;
		}
		UDataTable* Table = LoadDataTable(In, Out);
		if (!Table)
		{
			return;
		}
		if (!Table->GetRowStruct())
		{
			Fail(Out, TEXT("datatable has no RowStruct"));
			return;
		}

#if WITH_EDITOR
		const TArray<TSharedPtr<FJsonValue>>* Rows = nullptr;
		if (!In->TryGetArrayField(TEXT("rows"), Rows) || Rows == nullptr)
		{
			Fail(Out, TEXT("'rows' array is required (each row an object with a 'Name' field)"));
			return;
		}

		if (JBool(In, TEXT("replace"), false))
		{
			// Whole-table replace from a JSON array string. CreateTableFromJSONString empties
			// the table first, so this is a full overwrite.
			FString JsonText;
			TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
			FJsonSerializer::Serialize(*Rows, Writer);

			const TArray<FString> Problems = Table->CreateTableFromJSONString(JsonText);
			// CreateTableFromJSONString early-returns WITHOUT emptying the table when the
			// array is empty or fails to parse, so only claim success + notify when clean.
			const bool bReplaced = (Problems.Num() == 0);
			if (bReplaced)
			{
				FDataTableEditorUtils::BroadcastPostChange(Table, FDataTableEditorUtils::EDataTableChangeInfo::RowList);
				Table->MarkPackageDirty();
			}
			Out->SetBoolField(TEXT("replaced"), bReplaced);
			Out->SetNumberField(TEXT("rowCount"), Table->GetRowNames().Num());
			// The undocumented merge-vs-replace asymmetry that produced the "double wrapping" report.
			// Only raised when the row struct can actually hold an FText.
			if (bReplaced && RowStructHasTextProperty(Table->GetRowStruct()))
			{
				Out->SetStringField(TEXT("textLocalizationNote"), kReplaceTextNote);
			}
			if (Problems.Num() > 0)
			{
				TArray<TSharedPtr<FJsonValue>> Arr;
				for (const FString& P : Problems)
				{
					Arr.Add(MakeShared<FJsonValueString>(P));
				}
				Out->SetArrayField(TEXT("problems"), Arr);
			}
			return;
		}

		// Merge/update mode: add or update each row in place.
		int32 Added = 0;
		int32 Updated = 0;
		TArray<TSharedPtr<FJsonValue>> Warnings;
		for (const TSharedPtr<FJsonValue>& Value : *Rows)
		{
			const TSharedPtr<FJsonObject>* RowObjPtr = nullptr;
			if (!Value.IsValid() || !Value->TryGetObject(RowObjPtr) || RowObjPtr == nullptr)
			{
				continue;
			}
			const TSharedRef<FJsonObject> RowObj = RowObjPtr->ToSharedRef();
			FString RowName = JStr(RowObj, TEXT("Name"));
			if (RowName.IsEmpty())
			{
				RowName = JStr(RowObj, TEXT("name"));
			}
			if (RowName.IsEmpty())
			{
				Warnings.Add(MakeShared<FJsonValueString>(TEXT("row skipped: missing 'Name'")));
				continue;
			}

			uint8* Row = FDataTableEditorUtils::AddRow(Table, FName(*RowName));
			const bool bIsNew = (Row != nullptr);
			if (!Row)
			{
				Row = Table->FindRowUnchecked(FName(*RowName)); // existing → update in place
			}
			if (!Row)
			{
				Warnings.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("row '%s': could not allocate/find"), *RowName)));
				continue;
			}

			// JsonObjectToUStruct reflects over the (runtime-only-known) row struct; it ignores
			// the extra 'Name' key.
			FText FailReason;
			if (FJsonObjectConverter::JsonObjectToUStruct(RowObj, Table->GetRowStruct(), Row, 0, 0, false, &FailReason))
			{
				bIsNew ? ++Added : ++Updated;
			}
			else
			{
				// Don't leave a half-written default row we just added.
				if (bIsNew)
				{
					FDataTableEditorUtils::RemoveRow(Table, FName(*RowName));
				}
				Warnings.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("row '%s': %s"), *RowName, *FailReason.ToString())));
			}
		}

		FDataTableEditorUtils::BroadcastPostChange(Table, FDataTableEditorUtils::EDataTableChangeInfo::RowList);
		Table->MarkPackageDirty();

		Out->SetNumberField(TEXT("added"), Added);
		Out->SetNumberField(TEXT("updated"), Updated);
		Out->SetNumberField(TEXT("rowCount"), Table->GetRowNames().Num());
		if (Warnings.Num() > 0)
		{
			Out->SetArrayField(TEXT("warnings"), Warnings);
		}
#else
		Fail(Out, TEXT("write requires an editor build"));
#endif
	}
}
