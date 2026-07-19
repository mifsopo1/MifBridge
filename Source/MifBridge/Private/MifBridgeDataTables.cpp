// MifBridge — Phase 3 breadth: read-only DataTable access (list / read / row).
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "DataTableEditorUtils.h"
#include "Engine/DataTable.h"
#include "JsonObjectConverter.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
	namespace
	{
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
		UDataTable* Table = LoadDataTable(In, Out);
		if (!Table)
		{
			return;
		}

		Out->SetStringField(TEXT("path"), Table->GetPathName());
		if (const UScriptStruct* RowStruct = Table->GetRowStruct())
		{
			Out->SetStringField(TEXT("rowStruct"), RowStruct->GetName());
		}

		const TArray<FName> RowNames = Table->GetRowNames();
		Out->SetNumberField(TEXT("rowCount"), RowNames.Num());

#if WITH_EDITOR
		const int32 MaxRows = FMath::Clamp(JInt(In, TEXT("maxRows"), 500), 1, 10000);
		TArray<TSharedPtr<FJsonValue>> Rows;
		if (ParseJsonArray(Table->GetTableAsJSON(), Rows))
		{
			if (Rows.Num() > MaxRows)
			{
				Rows.SetNum(MaxRows);
				Out->SetBoolField(TEXT("truncated"), true);
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

#if WITH_EDITOR
		// GetTableAsJSON emits a "Name" field per row; find ours.
		TArray<TSharedPtr<FJsonValue>> Rows;
		if (ParseJsonArray(Table->GetTableAsJSON(), Rows))
		{
			for (const TSharedPtr<FJsonValue>& Value : Rows)
			{
				const TSharedPtr<FJsonObject>* RowObj = nullptr;
				if (Value.IsValid() && Value->TryGetObject(RowObj) && RowObj)
				{
					FString ThisName;
					if ((*RowObj)->TryGetStringField(TEXT("Name"), ThisName) && ThisName == RowName)
					{
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
