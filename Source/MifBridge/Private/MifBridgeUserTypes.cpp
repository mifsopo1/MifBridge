// MifBridge — user-defined STRUCT and ENUM authoring.
//
// The last blocking gap from the capability audit: resolve_struct / add_make_struct /
// add_break_struct / add_switch_enum could only ever CONSUME types a human had made by hand in the
// editor. Nothing here is reachable through the generic reflection endpoints — a struct's members
// live in a container that set_property's dot-walker refuses to descend, and writing them raw would
// skip FStructureEditorUtils::CompileStructure, leaving the generated UScriptStruct stale.
//
// Both engine utility classes live in UnrealEd, which is already a dependency.
//
// A note on how the editor models these, because it drives the endpoint shapes below:
//   * Struct members are addressed by FGuid, not name. AddVariable() mints one with an
//     auto-generated name ("MemberVar_0"); giving it a real name is a SECOND call, RenameVariable().
//     Every mutation recompiles the struct, so member GUIDs are the only stable handle.
//   * Enum entries are addressed by INDEX, and the underlying FName is always "<EnumName>::NewEnum"
//     style — the human-readable text is a separate DisplayNameMap entry. So "add an entry called
//     Ready" is likewise two calls: AddNewEnumeratorForUserDefinedEnum then SetEnumeratorDisplayName.
// These endpoints hide that two-step so a caller says what it wants once.
#include "MifBridgeHandlers.h"
#include "GameFramework/Actor.h"          // create_asset refuses Actor classes
#include "Components/ActorComponent.h"    // ... and component classes
#include "Engine/Blueprint.h"             // ... and points Blueprint classes at create_blueprint
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "EdGraph/EdGraphPin.h"   // FEdGraphPinType — complete type needed for ToPinType()/MakePinType
#include "Engine/DataTable.h"
#include "Engine/UserDefinedEnum.h"
#include "Engine/UserDefinedStruct.h"
#include "Kismet2/EnumEditorUtils.h"
#include "Kismet2/StructureEditorUtils.h"
#include "Misc/PackageName.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"
#include "UserDefinedStructure/UserDefinedStructEditorData.h"

namespace MifBridge
{
	namespace
	{
		// Shared guard for minting a new /Game/ asset: valid path, valid identifier, nothing there.
		// Was ValidateNewAssetPath — see the note on ValidateNewMaterialAssetPath in
		// MifBridgeMaterials.cpp: same name, different signature, different failure convention, one
		// unity-blob shift away from silently sharing an overload set.
		bool ValidateNewUserTypePath(const FString& Path, FString& OutAssetName, FString& OutError)
		{
			if (Path.IsEmpty() || !Path.StartsWith(TEXT("/Game/")))
			{
				OutError = TEXT("path required, must start with /Game/ (e.g. /Game/Types/S_MyStruct)");
				return false;
			}
			OutAssetName = FPackageName::GetLongPackageAssetName(Path);
			if (!IsValidIdentifier(OutAssetName))
			{
				OutError = FString::Printf(TEXT("invalid asset name '%s' (from path '%s')"), *OutAssetName, *Path);
				return false;
			}
			const FString ObjectPath = Path + TEXT(".") + OutAssetName;
			if (StaticLoadObject(UObject::StaticClass(), nullptr, *ObjectPath, nullptr, LOAD_NoWarn | LOAD_Quiet))
			{
				OutError = FString::Printf(TEXT("an asset already exists at '%s' — pick a new path or delete it first"), *ObjectPath);
				return false;
			}
			return true;
		}

		UUserDefinedStruct* LoadUserStruct(const FString& Path, FString& OutError)
		{
			FString P = Path;
			P.TrimStartAndEndInline();
			if (P.IsEmpty())
			{
				OutError = TEXT("struct path is required");
				return nullptr;
			}
			UObject* Obj = StaticLoadObject(UUserDefinedStruct::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Obj && !P.Contains(TEXT(".")))
			{
				const FString Full = P + TEXT(".") + FPackageName::GetShortName(P);
				Obj = StaticLoadObject(UUserDefinedStruct::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
			UUserDefinedStruct* Struct = Cast<UUserDefinedStruct>(Obj);
			if (!Struct)
			{
				OutError = FString::Printf(
					TEXT("user-defined struct not found: '%s'. Native C++ structs cannot be edited — this only works on Blueprint structs."), *P);
				return nullptr;
			}

			// A COOKED struct loads perfectly well but carries no EditorData — that is editor-only and
			// is stripped on cook. Every FStructureEditorUtils entry point (GetVarDesc,
			// GetVarDescByGuid, AddVariable, RemoveVariable, …) CastChecked's EditorData, so touching a
			// cooked struct is a FATAL cast rather than an error return:
			//
			//     Cast of nullptr to UserDefinedStructEditorData failed
			//     FStructureEditorUtils::GetVarDesc()   StructureEditorUtils.cpp:648
			//
			// On a cooked-editor project EVERY base-game struct is cooked, so without this the struct
			// endpoints hard-crash the editor on any of them. Rejected HERE rather than in each handler
			// because list_struct_members / create_struct / add_struct_member / remove_struct_member all
			// reach GetVarDesc by different routes. Hit for real 2026-07-27 (list_struct_members).
			if (!Struct->EditorData)
			{
				OutError = FString::Printf(
					TEXT("struct '%s' is COOKED — its editor-only data was stripped, so it cannot be ")
					TEXT("inspected or edited. This works only on structs authored in this editor. ")
					TEXT("To read a cooked struct's field names, read_datatable one row and take the keys."),
					*P);
				return nullptr;
			}

			return Struct;
		}

		UUserDefinedEnum* LoadUserEnum(const FString& Path, FString& OutError)
		{
			FString P = Path;
			P.TrimStartAndEndInline();
			if (P.IsEmpty())
			{
				OutError = TEXT("enum path is required");
				return nullptr;
			}
			UObject* Obj = StaticLoadObject(UUserDefinedEnum::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Obj && !P.Contains(TEXT(".")))
			{
				const FString Full = P + TEXT(".") + FPackageName::GetShortName(P);
				Obj = StaticLoadObject(UUserDefinedEnum::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
			UUserDefinedEnum* Enum = Cast<UUserDefinedEnum>(Obj);
			if (!Enum)
			{
				OutError = FString::Printf(
					TEXT("user-defined enum not found: '%s'. Native C++ enums cannot be edited — this only works on Blueprint enums."), *P);
			}
			return Enum;
		}

		TSharedRef<FJsonObject> SerializeStructMember(const FStructVariableDescription& Desc)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), Desc.VarName.ToString());
			J->SetStringField(TEXT("friendlyName"), Desc.FriendlyName);
			J->SetStringField(TEXT("guid"), Desc.VarGuid.ToString());
			J->SetObjectField(TEXT("type"), SerializePinType(Desc.ToPinType()));
			if (!Desc.DefaultValue.IsEmpty())
			{
				J->SetStringField(TEXT("default"), Desc.DefaultValue);
			}
			if (Desc.bInvalidMember)
			{
				// A member whose type failed to resolve. It still occupies a slot but the struct
				// will not compile — worth surfacing rather than letting it look healthy.
				J->SetBoolField(TEXT("invalid"), true);
			}
			return J;
		}

		// AddVariable() names the new member automatically; rename it to what the caller asked for.
		// Returns the member GUID, or an invalid GUID on failure.
		FGuid AddStructMemberNamed(UUserDefinedStruct* Struct, const FString& Name,
			const FEdGraphPinType& PinType, const FString& Default, FString& OutError)
		{
			if (!FStructureEditorUtils::AddVariable(Struct, PinType))
			{
				OutError = FString::Printf(TEXT("AddVariable failed for member '%s' (unsupported type?)"), *Name);
				return FGuid();
			}
			const TArray<FStructVariableDescription>& Desc = FStructureEditorUtils::GetVarDesc(Struct);
			if (Desc.Num() == 0)
			{
				OutError = TEXT("AddVariable reported success but the struct has no members");
				return FGuid();
			}
			const FGuid NewGuid = Desc.Last().VarGuid;   // AddVariable appends

			if (!FStructureEditorUtils::RenameVariable(Struct, NewGuid, Name))
			{
				// Leave the auto-named member in place rather than silently dropping it — the caller
				// can see it in list_struct_members and fix it up.
				OutError = FString::Printf(
					TEXT("member was added but could not be renamed to '%s' (name already used in this struct?)"), *Name);
				return NewGuid;
			}
			if (!Default.IsEmpty())
			{
				FStructureEditorUtils::ChangeVariableDefaultValue(Struct, NewGuid, Default);
			}
			return NewGuid;
		}
	}

	// --- create_struct ------------------------------------------------------
	//   in:  { path: "/Game/Types/S_Foo", members?: [{name, type, container?, valueType?, default?}] }
	//   out: { structPath, name, members[] }
	void H_create_struct(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("members") },
			TEXT("path (must start with /Game/ - the struct is named after the last segment), ")
			TEXT("members[] (each: name, type, container?, valueType?, default?)"),
			{ { TEXT("name"),       TEXT("the struct's name comes from the last segment of path - pass path:\"/Game/Types/S_Foo\"") },
			  { TEXT("struct"),     TEXT("create_struct MAKES the struct; the new asset location goes in path. To edit an existing struct use add_struct_member") },
			  { TEXT("structPath"), TEXT("the new asset location parameter is called path (structPath is what the response returns)") },
			  { TEXT("fields"),     TEXT("the member list parameter is called members[]") } }))
		{
			return;
		}
		const FString Path = JStr(In, TEXT("path"));
		FString AssetName, PathError;
		if (!ValidateNewUserTypePath(Path, AssetName, PathError))
		{
			Fail(Out, PathError);
			return;
		}

		UPackage* Package = CreatePackage(*Path);
		if (!Package)
		{
			Fail(Out, FString::Printf(TEXT("failed to create package '%s'"), *Path));
			return;
		}

		UUserDefinedStruct* Struct = FStructureEditorUtils::CreateUserDefinedStruct(
			Package, FName(*AssetName), RF_Public | RF_Standalone | RF_Transactional);
		if (!Struct)
		{
			Fail(Out, TEXT("CreateUserDefinedStruct returned null"));
			return;
		}

		// A freshly minted struct ships with one placeholder member. Remove it only if the caller
		// supplied their own — an empty struct does not compile, so leaving it is the safe default.
		const TArray<TSharedPtr<FJsonValue>>* MemberArr = nullptr;
		const bool bHasMembers = JArray(In, TEXT("members"), MemberArr) && MemberArr && MemberArr->Num() > 0;

		TArray<TSharedPtr<FJsonValue>> Added;
		TArray<TSharedPtr<FJsonValue>> Warnings;
		if (bHasMembers)
		{
			TArray<FGuid> Placeholders;
			for (const FStructVariableDescription& D : FStructureEditorUtils::GetVarDesc(Struct))
			{
				Placeholders.Add(D.VarGuid);
			}

			for (const TSharedPtr<FJsonValue>& Value : *MemberArr)
			{
				const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
				if (!Value.IsValid() || !Value->TryGetObject(ObjPtr) || ObjPtr == nullptr)
				{
					continue;
				}
				const TSharedRef<FJsonObject> Obj = ObjPtr->ToSharedRef();
				FString MemberName = JStr(Obj, TEXT("name"));
				MemberName.TrimStartAndEndInline();
				if (!IsValidIdentifier(MemberName))
				{
					// Batch M, option (c): the struct asset and its package already exist in memory at
					// this point, and a cancelled transaction does not remove them (PM-007). They are
					// never registered (AssetCreated/MarkPackageDirty are at the tail), so nothing
					// reaches the content browser or disk - but the package path is taken for the rest
					// of the editor session.
					Fail(Out, FString::Printf(
						TEXT("invalid struct member name '%s'. WHAT IS LEFT BEHIND: the UUserDefinedStruct and its package were already created in memory and are NOT removed; they are unregistered and unsaved, but retrying at the SAME path in this editor session will meet the existing object. Use a different path, or restart the editor."),
						*MemberName));
					return;
				}
				FEdGraphPinType PinType;
				FString TypeError;
				if (!MakePinType(JStr(Obj, TEXT("type")), JStr(Obj, TEXT("container")), PinType, TypeError,
					JStr(Obj, TEXT("valueType"))))
				{
					Fail(Out, FString::Printf(TEXT("member '%s': %s"), *MemberName, *TypeError));
					return;
				}
				FString AddError;
				const FGuid Guid = AddStructMemberNamed(Struct, MemberName, PinType, JStr(Obj, TEXT("default")), AddError);
				if (!Guid.IsValid())
				{
					Fail(Out, AddError);
					return;
				}
				if (!AddError.IsEmpty())
				{
					Warnings.Add(MakeShared<FJsonValueString>(AddError));
				}
				Added.Add(MakeShared<FJsonValueString>(MemberName));
			}

			// Drop the placeholders now that real members exist.
			for (const FGuid& Placeholder : Placeholders)
			{
				FStructureEditorUtils::RemoveVariable(Struct, Placeholder);
			}
		}

		FAssetRegistryModule::AssetCreated(Struct);
		Package->MarkPackageDirty();

		Out->SetStringField(TEXT("structPath"), Struct->GetPathName());
		Out->SetStringField(TEXT("name"), Struct->GetName());
		Out->SetArrayField(TEXT("added"), Added);
		if (Warnings.Num() > 0) { Out->SetArrayField(TEXT("warnings"), Warnings); }

		TArray<TSharedPtr<FJsonValue>> Members;
		for (const FStructVariableDescription& D : FStructureEditorUtils::GetVarDesc(Struct))
		{
			Members.Add(MakeShared<FJsonValueObject>(SerializeStructMember(D)));
		}
		Out->SetArrayField(TEXT("members"), Members);
		UE_LOG(LogMifBridge, Log, TEXT("create_struct: %s (%d members)"), *Struct->GetPathName(), Members.Num());
	}

	// --- create_datatable ---------------------------------------------------
	//   in:  { path: "/Game/Foo/DT_Bar", rowStruct: "RichTextStyleRow" }
	//   out: { dataTablePath, name, rowStruct }
	//
	// WHY THIS EXISTS: the DataTable surface used to be rows-only (read/write/delete/list), so a
	// table could be FILLED but never CREATED, and all three workarounds are closed:
	//   * duplicate_asset applies its /Game/ guard to the SOURCE, so an engine- or plugin-mounted
	//     table (e.g. /DDS2Casino/.../DT_CasinoTutorial_RichText) cannot be copied as a starting point;
	//   * import_asset can name CSVImportFactory but cannot set AutomatedImportSettings.ImportRowStruct
	//     (per-factory option objects are not exposed), and with Task->bAutomated=true UE takes the
	//     automated branch, logs "A Data table row type must be specified" and imports nothing.
	// See docs/06_OPEN_ISSUES_FROM_USE.md section 8.
	//
	// UDataTableFactory is deliberately NOT used: its only job over NewObject is ConfigureProperties(),
	// which opens a MODAL struct picker. This server runs handlers synchronously inside the HTTP
	// ticker, so a modal here would deadlock the bridge.
	void H_create_datatable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("rowStruct"), TEXT("struct") },
			TEXT("path (must start with /Game/ - the table is named after the last segment), ")
			TEXT("rowStruct (alias: struct) - a struct deriving from FTableRowBase, by name or object path"),
			{ { TEXT("name"),      TEXT("the table's name comes from the last segment of path - pass path:\"/Game/Foo/DT_Bar\"") },
			  { TEXT("rows"),      TEXT("create_datatable only MAKES the empty table; fill it with write_datatable_rows") },
			  { TEXT("rowType"),   TEXT("spell it rowStruct") },
			  { TEXT("structPath"),TEXT("pass the row struct as rowStruct; path is the NEW table's location") } }))
		{
			return;
		}

		const FString Path = JStr(In, TEXT("path"));
		FString AssetName, PathError;
		if (!ValidateNewUserTypePath(Path, AssetName, PathError))
		{
			Fail(Out, PathError);
			return;
		}

		FString RowStructName = JStr(In, TEXT("rowStruct"));
		if (RowStructName.IsEmpty()) { RowStructName = JStr(In, TEXT("struct")); }
		if (RowStructName.IsEmpty())
		{
			Fail(Out, TEXT("rowStruct is required (e.g. \"RichTextStyleRow\", \"RichImageRow\", ")
				TEXT("or a user struct path like \"/Game/Types/S_MyRow\")"));
			return;
		}

		// Accept a bare name, an F-prefixed name, or a full object path. ResolveStruct handles the
		// first two; a /Game/ or /Script/ path has to be loaded directly.
		UScriptStruct* RowStruct = ResolveStruct(RowStructName);
		if (!RowStruct && RowStructName.StartsWith(TEXT("/")))
		{
			RowStruct = LoadObject<UScriptStruct>(nullptr, *RowStructName, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!RowStruct)
			{
				// user-defined structs live at <package>.<name>, so retry with the suffix appended
				const FString Suffixed = RowStructName + TEXT(".") + FPackageName::GetLongPackageAssetName(RowStructName);
				RowStruct = LoadObject<UScriptStruct>(nullptr, *Suffixed, nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
		}
		if (!RowStruct)
		{
			Fail(Out, FString::Printf(
				TEXT("row struct '%s' not found. Pass a native struct name (RichTextStyleRow, RichImageRow), ")
				TEXT("or a user struct's asset path (/Game/Types/S_MyRow)"), *RowStructName));
			return;
		}

		// A DataTable whose row struct is not a FTableRowBase child loads but is unusable, and the
		// editor reports it only when the table is opened - refuse it here instead.
		if (!RowStruct->IsChildOf(FTableRowBase::StaticStruct()))
		{
			Fail(Out, FString::Printf(
				TEXT("row struct '%s' does not derive from FTableRowBase, so it cannot be a DataTable row type"),
				*RowStruct->GetName()));
			return;
		}

		UPackage* Package = CreatePackage(*Path);
		if (!Package)
		{
			Fail(Out, FString::Printf(TEXT("failed to create package '%s'"), *Path));
			return;
		}

		UDataTable* Table = NewObject<UDataTable>(
			Package, FName(*AssetName), RF_Public | RF_Standalone | RF_Transactional);
		if (!Table)
		{
			Fail(Out, TEXT("NewObject<UDataTable> returned null"));
			return;
		}
		Table->RowStruct = RowStruct;

		FAssetRegistryModule::AssetCreated(Table);
		Package->MarkPackageDirty();

		Out->SetStringField(TEXT("dataTablePath"), Table->GetPathName());
		Out->SetStringField(TEXT("name"), Table->GetName());
		Out->SetStringField(TEXT("rowStruct"), RowStruct->GetPathName());
		Out->SetNumberField(TEXT("rowCount"), 0);
		Out->SetStringField(TEXT("note"),
			TEXT("empty table created - fill it with write_datatable_rows, then save_dirty_packages"));
		UE_LOG(LogMifBridge, Log, TEXT("create_datatable: %s (row struct %s)"),
			*Table->GetPathName(), *RowStruct->GetName());
	}


	// --- set_struct_member ---------------------------------------------------
	//   in:  { struct, member, newName?, type?, container?, valueType?, default? }
	//   out: { member:{...}, renamed?, retyped?, redefaulted?, dependentDataTables:[...] }
	//
	// Rename / retype / re-default in place. Without this the only correction is remove + re-add,
	// which mints a new GUID, APPENDS the member at the end, reorders the struct, breaks every
	// Make/Break Struct pin and drops that column from every row of every dependent DataTable.
	//
	// LoadUserStruct is load-bearing: every FStructureEditorUtils entry point CastChecked's EditorData,
	// which is stripped on cook, so a cooked struct is a FATAL cast rather than an error - and every
	// base-game DDS2 struct is cooked.
	void H_set_struct_member(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("struct"), TEXT("structPath"), TEXT("path"), TEXT("member"), TEXT("memberName"),
			  TEXT("guid"), TEXT("newName"), TEXT("type"), TEXT("container"), TEXT("valueType"),
			  TEXT("default") },
			TEXT("struct (aliases: structPath, path), member (alias: memberName, or guid), and at least "
				 "one of newName, type (+container/valueType), default"),
			{ { TEXT("name"), TEXT("ambiguous here - 'member' names the member to change and 'newName' is what to call it") },
			  { TEXT("index"), TEXT("members are addressed by NAME or GUID, not position; reordering is not supported (it would change every Make/Break Struct pin order)") },
			  { TEXT("rename"), TEXT("the parameter is newName") } }))
		{
			return;
		}

		FString Error;
		UUserDefinedStruct* Struct = LoadUserStruct(
			JStrAny(In, { TEXT("struct"), TEXT("structPath"), TEXT("path") }), Error);
		if (!Struct)
		{
			Fail(Out, Error);
			return;
		}

		// Address by GUID if given, otherwise by friendly name. GUID is exact; the name is what a
		// caller has in hand after list_struct_members.
		const FString Wanted = JStrAny(In, { TEXT("member"), TEXT("memberName") });
		const FString WantedGuid = JStr(In, TEXT("guid"));
		if (Wanted.IsEmpty() && WantedGuid.IsEmpty())
		{
			Fail(Out, TEXT("member (or guid) is required - name the member to change. "
						   "list_struct_members shows both. NOTHING was changed."));
			return;
		}

		FGuid Guid;
		const TArray<FStructVariableDescription>& Descs = FStructureEditorUtils::GetVarDesc(Struct);
		for (const FStructVariableDescription& D : Descs)
		{
			if (!WantedGuid.IsEmpty())
			{
				if (D.VarGuid.ToString() == WantedGuid) { Guid = D.VarGuid; break; }
			}
			else if (D.FriendlyName == Wanted)
			{
				Guid = D.VarGuid;
				break;
			}
		}
		if (!Guid.IsValid())
		{
			TArray<FString> Have;
			for (const FStructVariableDescription& D : Descs) { Have.Add(D.FriendlyName); }
			Fail(Out, FString::Printf(
				TEXT("no member '%s' on %s. It has: %s. NOTHING was changed."),
				*(WantedGuid.IsEmpty() ? Wanted : WantedGuid), *Struct->GetPathName(),
				Have.Num() ? *FString::Join(Have, TEXT(", ")) : TEXT("(none)")));
			return;
		}

		const bool bWantRename = In->HasField(TEXT("newName"));
		const bool bWantRetype = In->HasField(TEXT("type"));
		const bool bWantDefault = In->HasField(TEXT("default"));
		if (!bWantRename && !bWantRetype && !bWantDefault)
		{
			Fail(Out, TEXT("nothing to change - pass at least one of newName, type or default. "
						   "NOTHING was changed."));
			return;
		}

		// COUNT THE BLAST RADIUS BEFORE TOUCHING ANYTHING. Retyping a column re-defaults it in every
		// row of every DataTable built on this struct, and a caller who is not told will not look.
		TArray<TSharedPtr<FJsonValue>> Dependents;
		{
			FAssetRegistryModule& ARM = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
			TArray<FName> Referencers;
			ARM.Get().GetReferencers(FName(*Struct->GetOutermost()->GetName()), Referencers);
			for (const FName& R : Referencers)
			{
				TArray<FAssetData> Assets;
				ARM.Get().GetAssetsByPackageName(R, Assets);
				for (const FAssetData& A : Assets)
				{
					if (A.AssetClassPath.GetAssetName() == FName(TEXT("DataTable")))
					{
						Dependents.Add(MakeShared<FJsonValueString>(A.GetObjectPathString()));
					}
				}
			}
		}

		Struct->Modify();
		bool bRenamed = false, bRetyped = false, bRedefaulted = false;

		if (bWantRename)
		{
			const FString NewName = JStr(In, TEXT("newName"));
			if (NewName.IsEmpty() || !IsValidIdentifier(NewName))
			{
				Fail(Out, FString::Printf(
					TEXT("newName '%s' is not a valid identifier. NOTHING was changed."), *NewName));
				return;
			}
			bRenamed = FStructureEditorUtils::RenameVariable(Struct, Guid, NewName);
			if (!bRenamed)
			{
				Fail(Out, FString::Printf(
					TEXT("rename to '%s' was refused - the name is probably already used by another "
						 "member. NOTHING was changed."), *NewName));
				return;
			}
		}

		if (bWantRetype)
		{
			FEdGraphPinType PinType;
			FString TypeError;
			if (!MakePinType(JStr(In, TEXT("type")), JStr(In, TEXT("container")), PinType, TypeError,
				JStr(In, TEXT("valueType"))))
			{
				Fail(Out, TypeError);
				return;
			}
			bRetyped = FStructureEditorUtils::ChangeVariableType(Struct, Guid, PinType);
			if (!bRetyped)
			{
				Fail(Out, TEXT("the type change was refused. A struct cannot contain itself, and some "
							   "types are not valid as struct members."));
				return;
			}
		}

		if (bWantDefault)
		{
			const FString Default = JStr(In, TEXT("default"));
			bRedefaulted = FStructureEditorUtils::ChangeVariableDefaultValue(Struct, Guid, Default);
			if (!bRedefaulted)
			{
				Fail(Out, FString::Printf(
					TEXT("default value '%s' was refused for this member's type."), *Default));
				return;
			}
		}

		// READ IT BACK. The mutators return a bool, and reporting ok on that alone is the shape that
		// let add_timeline claim success for years without ever creating a timeline.
		const FStructVariableDescription* After = nullptr;
		for (const FStructVariableDescription& D : FStructureEditorUtils::GetVarDesc(Struct))
		{
			if (D.VarGuid == Guid) { After = &D; break; }
		}
		if (!After)
		{
			Fail(Out, TEXT("the member vanished from the struct after the change - re-read it with "
						   "list_struct_members before doing anything else."));
			return;
		}

		TSharedRef<FJsonObject> MJ = MakeShared<FJsonObject>();
		MJ->SetStringField(TEXT("name"), After->FriendlyName);
		MJ->SetStringField(TEXT("guid"), After->VarGuid.ToString());
		MJ->SetStringField(TEXT("category"), After->Category.ToString());
		if (!After->SubCategory.IsNone()) { MJ->SetStringField(TEXT("subCategory"), After->SubCategory.ToString()); }
		MJ->SetStringField(TEXT("default"), After->DefaultValue);
		Out->SetObjectField(TEXT("member"), MJ);
		Out->SetBoolField(TEXT("renamed"), bRenamed);
		Out->SetBoolField(TEXT("retyped"), bRetyped);
		Out->SetBoolField(TEXT("redefaulted"), bRedefaulted);
		Out->SetStringField(TEXT("struct"), Struct->GetPathName());
		Out->SetNumberField(TEXT("dependentDataTableCount"), Dependents.Num());
		Out->SetArrayField(TEXT("dependentDataTables"), Dependents);
		if (Dependents.Num() > 0 && bRetyped)
		{
			// Naming them is the point - this is data loss the caller cannot see from here.
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("this member was RETYPED and %d DataTable(s) are built on this struct. Every row "
					 "of each has had that column reset to the new type's default. Check them with "
					 "read_datatable before saving."), Dependents.Num()));
		}
		UE_LOG(LogMifBridge, Log, TEXT("set_struct_member: %s.%s (rename=%d retype=%d default=%d)"),
			*Struct->GetName(), *After->FriendlyName, bRenamed ? 1 : 0, bRetyped ? 1 : 0, bRedefaulted ? 1 : 0);
	}

	// --- create_asset --------------------------------------------------------
	//   in:  { path, class }
	//   out: { assetPath, name, class, note }
	//
	// create_blueprint can author a DataAsset CLASS that nothing could then instantiate. This closes
	// that asymmetry. Bare NewObject rather than IAssetTools::CreateAsset ON PURPOSE: CanCreateAsset
	// raises FMessageDialog on an invalid name, a map-name collision, or "replace existing object?",
	// and a modal on the game thread takes this whole bridge down - which is exactly what happened to
	// duplicate_asset earlier today (01_POSTMORTEMS.md).
	void H_create_asset(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("class"), TEXT("assetClass"), TEXT("className") },
			TEXT("path (/Game/...), class (alias: assetClass, className) - a concrete UObject class, "
				 "typically a UDataAsset or UPrimaryDataAsset subclass"),
			{ { TEXT("parentClass"), TEXT("that is create_blueprint's key - this endpoint instantiates an existing class rather than authoring a new one") },
			  { TEXT("blueprintType"), TEXT("create_asset makes a DATA asset, not a blueprint - use create_blueprint for those") },
			  { TEXT("rowStruct"), TEXT("that is create_datatable's key") } }))
		{
			return;
		}

		const FString Path = JStr(In, TEXT("path"));
		FString AssetName, PathError;
		if (!ValidateNewUserTypePath(Path, AssetName, PathError))
		{
			Fail(Out, PathError);
			return;
		}

		// ResolveClassStrictField, not ResolveClass: an empty/"self" name resolves to the CONTEXT
		// blueprint's own class, and with no context here that would be a silent nonsense creation.
		// Strict makes the empty case an error and writes the failure itself.
		UClass* Class = ResolveClassStrictField(
			In, { TEXT("class"), TEXT("assetClass"), TEXT("className") }, nullptr, Out);
		if (!Class)
		{
			return;   // ResolveClassStrictField has already said what it could not resolve
		}

		// AN ABSTRACT CLASS PRODUCES AN ASSET THE COOKED GAME CAN NEVER LOAD, and the editor says
		// nothing about it until runtime. Refuse here, where the caller can still act on it.
		if (Class->HasAnyClassFlags(CLASS_Abstract))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is ABSTRACT and cannot be instantiated - an asset of it would load in the "
					 "editor and fail in the cooked game. Pass a concrete subclass. NOTHING was created."),
				*Class->GetPathName()));
			return;
		}
		// Actors and ActorComponents are not assets; they belong in a level or on a blueprint.
		if (Class->IsChildOf(AActor::StaticClass()) || Class->IsChildOf(UActorComponent::StaticClass()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is an Actor/Component class, which is placed rather than saved as an asset. "
					 "Use spawn_actor_in_level or add_component. NOTHING was created."),
				*Class->GetPathName()));
			return;
		}
		if (Class->IsChildOf(UBlueprint::StaticClass()))
		{
			Fail(Out, TEXT("use create_blueprint to author a Blueprint - this endpoint instantiates an "
						   "existing class as a data asset. NOTHING was created."));
			return;
		}

		// DESTINATION CHECK, AND THE FILTER IS LOAD-BEARING IN A COOKED MOD KIT.
		//
		// Plain FPackageName::DoesPackageExist consults the IoDispatcher as well as the filesystem,
		// and in this CookedEditorModKit setup /Game resolves through a pak container - so it answers
		// TRUE for essentially any well-formed /Game path, including ones nothing has ever written.
		// The first version of this guard used it and refused every single creation with "already
		// taken", for paths describe_package simultaneously reported as existsOnDisk:false,
		// inRegistry:false, loaded:false.
		//
		// What "would I overwrite something?" actually means here is: is there a real file on disk,
		// or is an object already loaded at that path.
		const bool bOnDisk = FPackageName::DoesPackageExistEx(
			FPackagePath::FromPackageNameChecked(Path),
			FPackageName::EPackageLocationFilter::FileSystem) != FPackageName::EPackageLocationFilter::None;
		// FindObject with a null Outer and a PACKAGE path resolves the UPackage itself, which exists
		// in memory the moment anything has touched that path - including a previous failed attempt in
		// the same session. The question is whether an ASSET is there, so look for the object inside
		// the package rather than the package around it.
		const FString ObjectPath = Path + TEXT(".") + AssetName;
		UObject* Existing = FindObject<UObject>(nullptr, *ObjectPath);
		if (bOnDisk || Existing)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is already taken (%s) - create_asset never overwrites. delete_asset the "
					 "existing one first or pick another path. NOTHING was created."),
				*Path, bOnDisk ? TEXT("a package file exists on disk")
							   : TEXT("an object is already loaded there")));
			return;
		}

		UPackage* Package = CreatePackage(*Path);
		if (!Package)
		{
			Fail(Out, FString::Printf(TEXT("failed to create package '%s'"), *Path));
			return;
		}
		UObject* Asset = NewObject<UObject>(
			Package, Class, FName(*AssetName), RF_Public | RF_Standalone | RF_Transactional);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("NewObject<%s> returned null"), *Class->GetName()));
			return;
		}

		// WITHOUT THESE TWO LINES THE ASSET IS A GHOST. It answers get_property and set_property
		// perfectly, never appears in find_assets or save_dirty_packages, and evaporates on restart -
		// a whole session reporting ok:true and losing everything it did.
		FAssetRegistryModule::AssetCreated(Asset);
		Package->MarkPackageDirty();

		// Verify through the registry rather than trusting the pointer we already hold: "created" and
		// "registered" are the two different things this endpoint exists to keep together.
		if (!FindObject<UObject>(nullptr, *Asset->GetPathName()))
		{
			Fail(Out, TEXT("the asset was created but cannot be found by path afterwards - it would not "
						   "survive a restart. Read it back with find_assets before relying on it."));
			return;
		}

		Out->SetStringField(TEXT("assetPath"), Asset->GetPathName());
		Out->SetStringField(TEXT("name"), Asset->GetName());
		Out->SetStringField(TEXT("class"), Class->GetPathName());
		Out->SetBoolField(TEXT("registered"), true);
		Out->SetStringField(TEXT("note"),
			TEXT("created and registered but NOT saved - set its properties with set_property, then "
				 "save_dirty_packages or it is lost on restart"));
		UE_LOG(LogMifBridge, Log, TEXT("create_asset: %s (%s)"), *Asset->GetPathName(), *Class->GetName());
	}

	// --- list_struct_members ------------------------------------------------
	//   in:  { struct: "/Game/Types/S_Foo" }   out: { structPath, members[] }
	void H_list_struct_members(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("struct"), TEXT("structPath"), TEXT("path") },
			TEXT("struct (aliases: structPath, path) - asset path of a Blueprint user-defined struct")))
		{
			return;
		}
		FString Error;
		UUserDefinedStruct* Struct = LoadUserStruct(JStrAny(In, { TEXT("struct"), TEXT("structPath"), TEXT("path") }), Error);
		if (!Struct)
		{
			Fail(Out, Error);
			return;
		}
		// (cooked structs are rejected up front by LoadUserStruct - see the EditorData guard there)

		Out->SetStringField(TEXT("structPath"), Struct->GetPathName());
		TArray<TSharedPtr<FJsonValue>> Members;
		for (const FStructVariableDescription& D : FStructureEditorUtils::GetVarDesc(Struct))
		{
			Members.Add(MakeShared<FJsonValueObject>(SerializeStructMember(D)));
		}
		Out->SetNumberField(TEXT("count"), Members.Num());
		Out->SetArrayField(TEXT("members"), Members);
	}

	// --- add_struct_member --------------------------------------------------
	//   in:  { struct, name, type, container?, valueType?, default? }
	void H_add_struct_member(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("struct"), TEXT("structPath"), TEXT("path"),
			  TEXT("name"), TEXT("type"), TEXT("container"), TEXT("valueType"), TEXT("default") },
			TEXT("struct (aliases: structPath, path), name, type, container?, valueType?, default?"),
			{ { TEXT("class"),        TEXT("the class belongs IN the type string, not in its own key: type:\"object:SceneComponent\". Prefixes: object:X, class:X, subclassof:X, softobject:X, softclass:X") },
			  { TEXT("subType"),      TEXT("use type:\"object:X\" for the referenced class, or valueType for a map's value type") },
			  { TEXT("memberName"),   TEXT("the member name parameter is called name") },
			  { TEXT("defaultValue"), TEXT("the parameter is called default") } }))
		{
			return;
		}
		FString Error;
		UUserDefinedStruct* Struct = LoadUserStruct(JStrAny(In, { TEXT("struct"), TEXT("structPath"), TEXT("path") }), Error);
		if (!Struct)
		{
			Fail(Out, Error);
			return;
		}
		FString Name = JStr(In, TEXT("name"));
		Name.TrimStartAndEndInline();
		if (!IsValidIdentifier(Name))
		{
			Fail(Out, FString::Printf(TEXT("invalid member name '%s'"), *Name));
			return;
		}
		FEdGraphPinType PinType;
		FString TypeError;
		if (!MakePinType(JStr(In, TEXT("type")), JStr(In, TEXT("container")), PinType, TypeError, JStr(In, TEXT("valueType"))))
		{
			Fail(Out, TypeError);
			return;
		}

		FString AddError;
		const FGuid Guid = AddStructMemberNamed(Struct, Name, PinType, JStr(In, TEXT("default")), AddError);
		if (!Guid.IsValid())
		{
			Fail(Out, AddError);
			return;
		}
		if (!AddError.IsEmpty()) { Out->SetStringField(TEXT("warning"), AddError); }

		Out->SetStringField(TEXT("structPath"), Struct->GetPathName());
		Out->SetStringField(TEXT("guid"), Guid.ToString());
		if (const FStructVariableDescription* Desc = FStructureEditorUtils::GetVarDescByGuid(Struct, Guid))
		{
			Out->SetObjectField(TEXT("member"), SerializeStructMember(*Desc));
		}
	}

	// --- remove_struct_member -----------------------------------------------
	//   in:  { struct, name? | guid?, confirm: true }
	void H_remove_struct_member(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("struct"), TEXT("structPath"), TEXT("path"),
			  TEXT("name"), TEXT("guid"), TEXT("confirm") },
			TEXT("struct (aliases: structPath, path), name or guid, confirm=true"),
			{ { TEXT("member"),     TEXT("the member is addressed by name or by guid") },
			  { TEXT("memberName"), TEXT("the member name parameter is called name") },
			  { TEXT("index"),      TEXT("struct members are addressed by name or guid, never by index - index is remove_enum_value's parameter") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_struct_member requires confirm=true"));
			return;
		}
		FString Error;
		UUserDefinedStruct* Struct = LoadUserStruct(JStrAny(In, { TEXT("struct"), TEXT("structPath"), TEXT("path") }), Error);
		if (!Struct)
		{
			Fail(Out, Error);
			return;
		}

		// Members are addressed by GUID internally; accept a name and resolve it.
		FGuid Target;
		const FString GuidStr = JStr(In, TEXT("guid"));
		const FString Name = JStr(In, TEXT("name"));
		if (!GuidStr.IsEmpty())
		{
			if (!FGuid::Parse(GuidStr, Target))
			{
				Fail(Out, FString::Printf(TEXT("bad member guid: %s"), *GuidStr));
				return;
			}
		}
		else if (!Name.IsEmpty())
		{
			for (const FStructVariableDescription& D : FStructureEditorUtils::GetVarDesc(Struct))
			{
				// VarName carries a GUID suffix; FriendlyName is what the user sees.
				if (D.FriendlyName.Equals(Name, ESearchCase::IgnoreCase) || D.VarName.ToString() == Name)
				{
					Target = D.VarGuid;
					break;
				}
			}
			if (!Target.IsValid())
			{
				Fail(Out, FString::Printf(TEXT("member '%s' not found in %s"), *Name, *Struct->GetName()));
				return;
			}
		}
		else
		{
			Fail(Out, TEXT("supply name or guid"));
			return;
		}

		if (FStructureEditorUtils::GetVarDesc(Struct).Num() <= 1)
		{
			Fail(Out, TEXT("cannot remove the last member — a user-defined struct must keep at least one, or it will not compile"));
			return;
		}
		if (!FStructureEditorUtils::RemoveVariable(Struct, Target))
		{
			Fail(Out, TEXT("RemoveVariable failed"));
			return;
		}
		Out->SetStringField(TEXT("structPath"), Struct->GetPathName());
		Out->SetStringField(TEXT("removed"), Name.IsEmpty() ? Target.ToString() : Name);
	}

	// --- create_enum --------------------------------------------------------
	//   in:  { path: "/Game/Types/E_Foo", values?: ["Idle","Running"] }
	//   out: { enumPath, name, values[] }
	void H_create_enum(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("values") },
			TEXT("path (must start with /Game/ - the enum is named after the last segment), ")
			TEXT("values[] (entry display names, in order)"),
			{ { TEXT("name"),     TEXT("the enum's name comes from the last segment of path - pass path:\"/Game/Types/E_Foo\"") },
			  { TEXT("enum"),     TEXT("create_enum MAKES the enum; the new asset location goes in path. To extend an existing enum use add_enum_value") },
			  { TEXT("enumPath"), TEXT("the new asset location parameter is called path (enumPath is what the response returns)") },
			  { TEXT("entries"),  TEXT("the entry list parameter is called values[]") },
			  { TEXT("members"),  TEXT("members[] is create_struct's parameter; an enum's entries go in values[]") } }))
		{
			return;
		}
		const FString Path = JStr(In, TEXT("path"));
		FString AssetName, PathError;
		if (!ValidateNewUserTypePath(Path, AssetName, PathError))
		{
			Fail(Out, PathError);
			return;
		}
		if (!FEnumEditorUtils::IsNameAvailebleForUserDefinedEnum(FName(*AssetName)))
		{
			Fail(Out, FString::Printf(TEXT("enum name '%s' collides with an existing type"), *AssetName));
			return;
		}

		UPackage* Package = CreatePackage(*Path);
		if (!Package)
		{
			Fail(Out, FString::Printf(TEXT("failed to create package '%s'"), *Path));
			return;
		}

		UEnum* Created = FEnumEditorUtils::CreateUserDefinedEnum(
			Package, FName(*AssetName), RF_Public | RF_Standalone | RF_Transactional);
		UUserDefinedEnum* Enum = Cast<UUserDefinedEnum>(Created);
		if (!Enum)
		{
			Fail(Out, TEXT("CreateUserDefinedEnum returned null"));
			return;
		}

		// A new enum starts with one entry. Entries are added by index and then given a display
		// name — the underlying FName is engine-generated and is NOT what the user sees.
		const TArray<TSharedPtr<FJsonValue>>* ValueArr = nullptr;
		TArray<FString> Wanted;
		if (JArray(In, TEXT("values"), ValueArr) && ValueArr)
		{
			for (const TSharedPtr<FJsonValue>& V : *ValueArr)
			{
				FString S;
				if (V.IsValid() && V->TryGetString(S))
				{
					S.TrimStartAndEndInline();
					if (!S.IsEmpty()) { Wanted.Add(S); }
				}
			}
		}

		TArray<TSharedPtr<FJsonValue>> Warnings;
		for (int32 Index = 0; Index < Wanted.Num(); ++Index)
		{
			// NumEnums() includes the trailing _MAX sentinel, so the real entry count is one less.
			const int32 Existing = Enum->NumEnums() - 1;
			if (Index >= Existing)
			{
				FEnumEditorUtils::AddNewEnumeratorForUserDefinedEnum(Enum);
			}
			if (!FEnumEditorUtils::IsProperNameForUserDefinedEnumerator(Enum, Wanted[Index]))
			{
				Warnings.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("'%s' is not a valid enumerator display name here (duplicate?); the entry kept its generated name"), *Wanted[Index])));
				continue;
			}
			FEnumEditorUtils::SetEnumeratorDisplayName(Enum, Index, FText::FromString(Wanted[Index]));
		}

		FAssetRegistryModule::AssetCreated(Enum);
		Package->MarkPackageDirty();

		Out->SetStringField(TEXT("enumPath"), Enum->GetPathName());
		Out->SetStringField(TEXT("name"), Enum->GetName());
		if (Warnings.Num() > 0) { Out->SetArrayField(TEXT("warnings"), Warnings); }

		TArray<TSharedPtr<FJsonValue>> Values;
		for (int32 Index = 0; Index < Enum->NumEnums() - 1; ++Index)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetNumberField(TEXT("index"), Index);
			J->SetStringField(TEXT("name"), Enum->GetNameStringByIndex(Index));
			J->SetStringField(TEXT("displayName"), Enum->GetDisplayNameTextByIndex(Index).ToString());
			Values.Add(MakeShared<FJsonValueObject>(J));
		}
		Out->SetArrayField(TEXT("values"), Values);
		UE_LOG(LogMifBridge, Log, TEXT("create_enum: %s (%d values)"), *Enum->GetPathName(), Values.Num());
	}

	// --- add_enum_value / remove_enum_value ---------------------------------
	void H_add_enum_value(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("enum"), TEXT("enumPath"), TEXT("path"),
			  TEXT("value"), TEXT("name"), TEXT("displayName") },
			TEXT("enum (aliases: enumPath, path), value (aliases: name, displayName) - the display name of the one new entry"),
			{ { TEXT("values"), TEXT("add_enum_value appends ONE entry; pass value:\"Ready\". The values[] array belongs to create_enum") },
			  { TEXT("index"),  TEXT("the new entry is always appended; its index comes back in the response") } }))
		{
			return;
		}
		FString Error;
		UUserDefinedEnum* Enum = LoadUserEnum(JStrAny(In, { TEXT("enum"), TEXT("enumPath"), TEXT("path") }), Error);
		if (!Enum)
		{
			Fail(Out, Error);
			return;
		}
		FString DisplayName = JStrAny(In, { TEXT("value"), TEXT("name"), TEXT("displayName") });
		DisplayName.TrimStartAndEndInline();
		if (DisplayName.IsEmpty())
		{
			Fail(Out, TEXT("value is required (the display name of the new entry)"));
			return;
		}
		if (!FEnumEditorUtils::IsProperNameForUserDefinedEnumerator(Enum, DisplayName))
		{
			Fail(Out, FString::Printf(TEXT("'%s' is not a valid enumerator name here (already used?)"), *DisplayName));
			return;
		}

		FEnumEditorUtils::AddNewEnumeratorForUserDefinedEnum(Enum);
		const int32 NewIndex = Enum->NumEnums() - 2;   // last real entry, before _MAX
		if (NewIndex < 0)
		{
			Fail(Out, TEXT("enum has no entries after add"));
			return;
		}
		FEnumEditorUtils::SetEnumeratorDisplayName(Enum, NewIndex, FText::FromString(DisplayName));

		Out->SetStringField(TEXT("enumPath"), Enum->GetPathName());
		Out->SetNumberField(TEXT("index"), NewIndex);
		Out->SetStringField(TEXT("displayName"), Enum->GetDisplayNameTextByIndex(NewIndex).ToString());
		Out->SetStringField(TEXT("name"), Enum->GetNameStringByIndex(NewIndex));
	}

	void H_remove_enum_value(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("enum"), TEXT("enumPath"), TEXT("path"),
			  TEXT("index"), TEXT("value"), TEXT("name"), TEXT("displayName"), TEXT("confirm") },
			TEXT("enum (aliases: enumPath, path), index or value (aliases: name, displayName), confirm=true"),
			{ { TEXT("guid"),   TEXT("enum entries are addressed by index or by value/display name, never by guid - guid is remove_struct_member's parameter") },
			  { TEXT("values"), TEXT("remove_enum_value removes ONE entry; pass value:\"Ready\" or index:2") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_enum_value requires confirm=true"));
			return;
		}
		FString Error;
		UUserDefinedEnum* Enum = LoadUserEnum(JStrAny(In, { TEXT("enum"), TEXT("enumPath"), TEXT("path") }), Error);
		if (!Enum)
		{
			Fail(Out, Error);
			return;
		}

		const int32 RealCount = Enum->NumEnums() - 1;
		int32 Index = -1;
		if (In->HasField(TEXT("index")))
		{
			Index = JInt(In, TEXT("index"), -1);
		}
		else
		{
			const FString Wanted = JStrAny(In, { TEXT("value"), TEXT("name"), TEXT("displayName") });
			for (int32 i = 0; i < RealCount; ++i)
			{
				if (Enum->GetDisplayNameTextByIndex(i).ToString().Equals(Wanted, ESearchCase::IgnoreCase)
					|| Enum->GetNameStringByIndex(i).Equals(Wanted, ESearchCase::IgnoreCase))
				{
					Index = i;
					break;
				}
			}
			if (Index < 0)
			{
				Fail(Out, FString::Printf(TEXT("enum value '%s' not found in %s"), *Wanted, *Enum->GetName()));
				return;
			}
		}
		if (Index < 0 || Index >= RealCount)
		{
			Fail(Out, FString::Printf(TEXT("index %d out of range (enum has %d values)"), Index, RealCount));
			return;
		}
		if (RealCount <= 1)
		{
			Fail(Out, TEXT("cannot remove the last enum value — an empty enum will not compile"));
			return;
		}

		// Removing an entry SHIFTS every later index. Anything that stored this enum by index
		// (switch nodes, saved defaults) silently re-points; say so.
		const FString Removed = Enum->GetDisplayNameTextByIndex(Index).ToString();
		FEnumEditorUtils::RemoveEnumeratorFromUserDefinedEnum(Enum, Index);

		Out->SetStringField(TEXT("enumPath"), Enum->GetPathName());
		Out->SetStringField(TEXT("removed"), Removed);
		Out->SetNumberField(TEXT("remaining"), Enum->NumEnums() - 1);
		if (Index < RealCount - 1)
		{
			Out->SetStringField(TEXT("warning"),
				TEXT("values after the removed one shifted down by one index — refresh any switch-on-enum nodes and re-check stored defaults"));
		}
	}
}
