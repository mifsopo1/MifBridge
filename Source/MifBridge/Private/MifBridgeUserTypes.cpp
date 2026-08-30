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
#include "AssetRegistry/IAssetRegistry.h"      // GetAssetByObjectPath - create_asset verifies registration through the registry, not the object hash
#include "EdGraph/EdGraphPin.h"   // FEdGraphPinType — complete type needed for ToPinType()/MakePinType
#include "Engine/DataTable.h"
#include "Engine/UserDefinedEnum.h"
#include "LevelSequence.h"        // ULevelSequence::Initialize() - create_asset's one post-construction special case
#if MIF_WITH_NIAGARA
#include "NiagaraSystem.h"
#include "NiagaraSystemFactoryNew.h"   // UNiagaraSystemFactoryNew::InitializeSystem - see the NiagaraSystem special case below
#endif
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
			// AN ENUM CREATED WITHOUT SetEnums(..., Namespaced) IS A CRASH BOMB, and one may
			// already exist on disk from before create_asset was fixed. GenerateFullEnumName
			// asserts check(CppForm == ECppForm::Namespaced), so the first operation naming an
			// enumerator would take the editor down. Refuse it by name instead.
			if (UUserDefinedEnum* Malformed = Cast<UUserDefinedEnum>(Obj))
			{
				if (Malformed->GetCppForm() != UEnum::ECppForm::Namespaced)
				{
					OutError = FString::Printf(
						TEXT("'%s' is a malformed user-defined enum - its CppForm is not Namespaced, "
							 "which means it was created without SetEnums(..., Namespaced). Any "
							 "operation that names an enumerator would hit "
							 "check(CppForm == ECppForm::Namespaced) and TERMINATE the editor. "
							 "Delete it and create a new one. NOTHING was changed."),
						*Malformed->GetPathName());
					return nullptr;
				}
			}

			// THE COOKED HOLE, closed here rather than in each caller. DisplayNameMap SURVIVES the
			// cook and nothing in this loader checked the package, so a UUserDefinedEnum mounted
			// from a .pak loaded fine and every write against it - add_enum_value,
			// remove_enum_value, a rename - reported success and evaporated on restart. That is a
			// wrong answer rather than an error, which is the worse kind. Every enum endpoint goes
			// through this function, so one check covers all of them.
			if (UUserDefinedEnum* Cooked = Cast<UUserDefinedEnum>(Obj))
			{
				if (IsCookedOrContainerPackage(Cooked->GetOutermost()))
				{
					OutError = FString::Printf(
						TEXT("'%s' came from a COOKED package. A user-defined enum's entries can be "
							 "changed in memory there, and the change CANNOT be saved - it would "
							 "report success and vanish on restart. Reading it is fine; writing to "
							 "it is not. NOTHING was changed."), *Cooked->GetPathName());
					return nullptr;
				}
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
				// ChangeVariableDefaultValue RETURNS whether the value took, and this discarded it -
				// while the RenameVariable call directly above already checks its own. It validates the
				// string against the member's pin type (K2Schema->DefaultValueSimpleValidation, same in
				// 5.3 and 5.7) and refuses one that does not parse, so default:"abc" on an int member
				// left the member with NO default while the response reported the one that was asked for.
				//
				// READ BACK rather than test the bool: it also returns false when the stored value is
				// ALREADY the requested one, which is not a failure. Comparing what is stored answers the
				// question the caller actually has - is the default what I asked for - and treats those
				// two cases correctly without having to tell them apart.
				FStructureEditorUtils::ChangeVariableDefaultValue(Struct, NewGuid, Default);
				const FStructVariableDescription* Now = FStructureEditorUtils::GetVarDescByGuid(Struct, NewGuid);
				if (!Now || Now->DefaultValue != Default)
				{
					// The member STAYS, matching the rename case above: a member with the wrong default is
					// visible in list_struct_members and fixable in place, and dropping it here would also
					// mint a new GUID on the retry - which reorders the struct and breaks every Make/Break
					// Struct pin. Reported through OutError, which both callers already surface as a
					// warning when the GUID came back valid.
					OutError = FString::Printf(
						TEXT("member '%s' was added, but the default '%s' was REFUSED as not valid for this type. ")
						TEXT("Its default is now '%s'. The member itself is fine - correct the value with ")
						TEXT("set_struct_member, which re-defaults in place without renumbering the struct."),
						*Name, *Default,
						Now ? (Now->DefaultValue.IsEmpty() ? TEXT("(empty)") : *Now->DefaultValue)
						     : TEXT("(the member could not be read back at all)"));
				}
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

		// A BARE NewObject<ULevelSequence> IS MALFORMED - "the asset exists but is malformed", found
		// live 2026-08-28 driving add_sequence_possessable against one. ULevelSequenceFactoryNew (the
		// stock content-browser "Add Level Sequence" action, LevelSequenceFactoryNew.cpp) does exactly
		// this NewObject call and then ONE more: NewLevelSequence->Initialize(), which creates and
		// assigns the internal UMovieScene sub-object every other Sequencer endpoint in this plugin
		// assumes exists (add_sequence_possessable's own error names it: "has no MovieScene"). Checked
		// by exact type, not by name-string like the cooked-asset guards elsewhere in this plugin,
		// because this is a construction step to RUN, not a class to refuse - a wrong match here would
		// silently skip real initialisation rather than silently allow a crash.
		if (ULevelSequence* NewSequence = Cast<ULevelSequence>(Asset))
		{
			NewSequence->Initialize();
		}

		// ---------------------------------------------------------------------------------------
		// CLASSES WHOSE ENGINE FACTORY DOES MORE THAN NewObject
		// ---------------------------------------------------------------------------------------
		//
		// Found by tools/audit_factory_init.py, which reads the engine's own UFactory sources and
		// reports every FactoryCreateNew that calls something on the object after constructing it.
		// It was written because this had already bitten twice - ULevelSequence malformed on
		// 2026-08-28, UUserDefinedEnum fatal on 2026-08-30 - and twice is a pattern rather than two
		// accidents.
		//
		// THIS WARNS RATHER THAN REFUSING, and the distinction is the honest part. The audit found
		// 22 factories, and reading them shows the calls are not all equal: USkeleton's factory
		// REQUIRES a target skeletal mesh and opens a dialog without one, so a bare skeleton is
		// genuinely malformed - while USoundClass's InitSoundClasses is a global audio-device
		// refresh that says nothing about the asset. Refusing all of them would block legitimate
		// creations to catch a few; silently creating them all is what produced the two bugs above.
		// So the ones this plugin does NOT replicate are named, with what the factory does, and the
		// caller decides.
		//
		// A class that gets proper handling here should be REMOVED from this list rather than left
		// warning about a problem that no longer exists.
		static const TCHAR* FactoryInitClasses[] = {
			TEXT("AnimComposite"), TEXT("AnimMontage"), TEXT("AnimSequence"), TEXT("AnimStreamable"),
			TEXT("PoseAsset"), TEXT("Skeleton"), TEXT("GroomAsset"), TEXT("ChaosClothAsset"),
			TEXT("HLODLayer"), TEXT("PaperSprite"), TEXT("PaperTileSet"),
			TEXT("NiagaraParameterCollectionInstance"), TEXT("SoundClass"), TEXT("SoundSubmix"),
			TEXT("EndpointSubmix"), TEXT("SoundfieldSubmix"), TEXT("SoundfieldEndpointSubmix"),
			TEXT("AnimNextGraph"), TEXT("AnimNextParameterBlock"),
		};
		{
			const FString CreatedClass = Class->GetName();
			for (const TCHAR* Known : FactoryInitClasses)
			{
				if (CreatedClass == Known)
				{
					Out->SetBoolField(TEXT("factoryInitIncomplete"), true);
					Out->SetStringField(TEXT("factoryNote"), FString::Printf(
						TEXT("the engine creates a %s through a UFactory that does MORE than "
							 "NewObject - it calls further setup this endpoint does not replicate, "
							 "and several of those factories need input create_asset has no "
							 "parameter for (a USkeleton's factory requires a target skeletal mesh, "
							 "for instance). The asset exists and may well be usable, but VERIFY it "
							 "before relying on it, and prefer the editor's own creation flow when "
							 "the asset needs a source. tools/audit_factory_init.py --class U%s "
							 "shows exactly what that factory does."),
						*CreatedClass, *CreatedClass));
					break;
				}
			}
		}

		// AND A BARE NewObject<UUserDefinedEnum> IS A CRASH BOMB - the same shape as the sequence
		// above, one step worse. Found live 2026-08-30: create_asset made one, add_enum_value was
		// called on it, and the editor died on
		//
		//     Assertion failed: CppForm == ECppForm::Namespaced
		//     [UserDefinedEnum.cpp:49, in GenerateFullEnumName]
		//
		// FEnumEditorUtils::CreateUserDefinedEnum - the stock "Add Enumeration" action - does this
		// same NewObject and then TWO more things (EnumEditorUtils.cpp:46-52):
		//
		//     Enum->SetEnums(EmptyNames, UEnum::ECppForm::Namespaced);
		//     Enum->SetMetaData(TEXT("BlueprintType"), TEXT("true"));
		//
		// Without the first, CppForm stays Regular and the FIRST operation that names an
		// enumerator asserts. The asset looked perfectly fine in the content browser until
		// something touched it, which is the worst way for this to be found. Without the second it
		// is invisible to Blueprint variable types.
		//
		// By exact type, not by name-string, for the reason the sequence comment above gives: this
		// is a construction step to RUN, and a wrong match would silently skip real initialisation.
		if (UUserDefinedEnum* NewEnum = Cast<UUserDefinedEnum>(Asset))
		{
			TArray<TPair<FName, int64>> EmptyNames;
			NewEnum->SetEnums(EmptyNames, UEnum::ECppForm::Namespaced);
			NewEnum->SetMetaData(TEXT("BlueprintType"), TEXT("true"));
			// VERIFIED, because this is precisely the state whose absence is fatal. SetEnums
			// returns void, and shipping an enum that asserts on first use is what this block
			// exists to stop - so it refuses to hand one back rather than trusting the call.
			if (NewEnum->GetCppForm() != UEnum::ECppForm::Namespaced)
			{
				Fail(Out, TEXT("the new enum's CppForm is not Namespaced after initialisation. "
					TEXT("Handing it back would produce an asset that TERMINATES the editor on the "
						 "first operation naming an enumerator. NOTHING usable was produced.")));
				return;
			}
		}

#if MIF_WITH_NIAGARA
		// A BARE NewObject<UNiagaraSystem> CRASHES THE EDITOR - found live 2026-08-29, not assumed.
		// The stock "New Niagara System" factory (UNiagaraSystemFactoryNew::FactoryCreateNew,
		// NiagaraSystemFactoryNew.cpp:111-171) does exactly this NewObject call and then ONE more:
		// InitializeSystem(NewSystem, true), which sets up the exposed-parameters store and the
		// default System-Update/Emitter pipeline stages every other Niagara operation on this system
		// assumes exist. Skipping it left the system in a state that crashed inside this very handler
		// before it could even respond - the crash journal showed a "start" for this create_asset call
		// with no matching "end". Same shape as ULevelSequence's fix above, one asset class over: a
		// generic NewObject is not what the engine's own "New X" action actually does.
		// InitializeSystem is public and static (NiagaraSystemFactoryNew.h:29), verified identical in
		// both engines (5.3 and 5.7, same line number). Deliberately NOT also calling
		// NewSystem->RequestCompile(false), which the real factory does last: that starts real script
		// compilation, a heavier and separately-triggerable operation, and InitializeSystem alone is
		// the specific call proven necessary to stop the crash.
		if (UNiagaraSystem* NewNiagaraSystem = Cast<UNiagaraSystem>(Asset))
		{
			UNiagaraSystemFactoryNew::InitializeSystem(NewNiagaraSystem, /*bCreateDefaultNodes*/ true);
		}
#endif

		// WITHOUT THESE TWO LINES THE ASSET IS A GHOST. It answers get_property and set_property
		// perfectly, never appears in find_assets or save_dirty_packages, and evaporates on restart -
		// a whole session reporting ok:true and losing everything it did.
		FAssetRegistryModule::AssetCreated(Asset);
		Package->MarkPackageDirty();

		// Verify through the registry rather than trusting the pointer we already hold: "created" and
		// "registered" are the two different things this endpoint exists to keep together.
		// ASK THE REGISTRY, NOT THE OBJECT HASH. This used to be
		//   if (!FindObject<UObject>(nullptr, *Asset->GetPathName()))
		// which reads the global UObject hash - where NewObject above has already put the asset - so it
		// was self-confirming and could never observe whether registration happened. The comment above it
		// promised a registry check and the code did not perform one.
		//
		// It matters because AssetCreated is void and has two silent early-outs: it does nothing at all
		// when NewAsset->IsAsset() is false, and it skips the FAssetData construction and every
		// AssetAdded broadcast when ShouldSkipAsset() is true. IsAsset() is virtual and false for several
		// concrete classes this endpoint accepts - it only gates out CLASS_Abstract, AActor,
		// UActorComponent and UBlueprint - so this is reachable from the endpoint own parameters rather
		// than hypothetical. The result would be exactly the ghost asset the comment above says these
		// lines exist to prevent, reported back as prevented.
		const bool bInRegistry = IAssetRegistry::GetChecked()
			.GetAssetByObjectPath(FSoftObjectPath(Asset)).IsValid();
		if (!bInRegistry)
		{
			Fail(Out, TEXT("the asset was created in memory but the Asset Registry did not take it, so it "
						   "is a GHOST: it will answer get_property and set_property perfectly, never appear "
						   "in find_assets or save_dirty_packages, and evaporate on restart. The usual cause "
						   "is a class whose IsAsset() returns false. Pick a different class."));
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
			// USE THE RETURN VALUE. The pre-check above is IsProperNameForUserDefinedEnumerator, which
			// validates the AUTHORED name - and for a UUserDefinedEnum the authored names are always
			// NewEnumeratorN, so that check has essentially nothing to reject and effectively always
			// passes. The gate that actually matters is inside SetEnumeratorDisplayName:
			// IsEnumeratorDisplayNameValid, which refuses a duplicate DISPLAY name (EnumEditorUtils.cpp
			// :496-501). Its bool was discarded, so a refused name left the entry as NewEnumeratorN
			// while the caller was told nothing - the chosen name lives ONLY in the display name, so
			// that is the whole value of the call being silently lost.
			//
			// Same class as the add_enum_value bug closed on 2026-08-25, which was fixed by reading the
			// applied display name back rather than trusting the write.
			if (!FEnumEditorUtils::SetEnumeratorDisplayName(Enum, Index, FText::FromString(Wanted[Index])))
			{
				Warnings.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("'%s' was refused as a display name (a duplicate of another entry?); entry %d kept its "
						 "generated name. The name you asked for is NOT set - read the enum back with "
						 "list_enum_values before relying on it."), *Wanted[Index], Index)));
			}
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

		// READ IT BACK. SetEnumeratorDisplayName returns a bool - this comment used to say void, which
		// was simply wrong - and it declines a name it does not like
		// without saying so, and IsProperNameForUserDefinedEnumerator above does NOT catch every case
		// it declines - a duplicate display name passes that guard and is then silently refused here,
		// leaving the auto-generated NewEnumeratorN in place. The result was ok:true, an appended
		// entry, and the requested name nowhere.
		const FString Applied = Enum->GetDisplayNameTextByIndex(NewIndex).ToString();
		if (!Applied.Equals(DisplayName))
		{
			// ROLL BACK the entry that was just appended. Failing without this still leaves a junk
			// NewEnumeratorN in the enum, and an enum quietly growing nameless entries is worse than a
			// refused call: it costs nothing today and corrupts meaning later.
			FEnumEditorUtils::RemoveEnumeratorFromUserDefinedEnum(Enum, NewIndex);
			Fail(Out, FString::Printf(
				TEXT("the entry was appended but its display name came back as '%s' instead of '%s', so "
					 "the engine refused the name - most often because another entry already uses it. "
					 "The appended entry has been REMOVED again, so this enum is exactly as it was "
					 "before the call. Pick a different name, or list_enum_values to see what is "
					 "taken."), *Applied, *DisplayName));
			return;
		}

		Out->SetStringField(TEXT("enumPath"), Enum->GetPathName());
		Out->SetNumberField(TEXT("index"), NewIndex);
		Out->SetStringField(TEXT("displayName"), Applied);
		Out->SetStringField(TEXT("name"), Enum->GetNameStringByIndex(NewIndex));
		// Said once, because a caller who assumes these are the same writes the display name into a
		// pin and gets a silent mismatch. add_enum_value has always returned both; list_enum_values
		// only started to on 2026-08-26.
		Out->SetStringField(TEXT("nameNote"),
			TEXT("displayName is the name you chose; name is what the engine stores and what "
				 "set_pin_default and add_enum_literal expect. On a user-defined enum they never "
				 "match."));
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

	// =======================================================================
	// set_enum_value - reorder, bitflags, and a hardened rename
	// =======================================================================
	//
	// SCOPE, NARROWED AFTER CHECKING. Renaming an entry is ALREADY reachable: DisplayNameMap is a
	// plain UPROPERTY TMap<FName,FText> (UserDefinedEnum.h:41), set_property accepts any asset by
	// objectPath, and the {Key} map accessor exists. So rename here is a HARDENING - it adds
	// IsEnumeratorDisplayNameValid's duplicate check and the BroadcastChanges that a raw property
	// write skips - not a new capability, and the spec says so.
	//
	// What is genuinely unreachable is reordering and bitflags. UEnum::Names is a protected
	// non-UPROPERTY (Class.h:2517) so no reflective path touches it, and the bitflags state is
	// UObject metadata rather than a property.
	//
	// SCOPES ARE NOT MIXED. bitflags is a property of the ENUM; index/value address an ENTRY.
	// A call carrying both is refused rather than served in some arbitrary order, because either
	// order would surprise half the callers.

	void H_set_enum_value(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("enum"), TEXT("enumPath"), TEXT("path"), TEXT("index"), TEXT("value"),
			  TEXT("displayName"), TEXT("newName"), TEXT("moveTo"), TEXT("bitflags") },
			TEXT("enum (aliases enumPath, path); then EITHER bitflags (enum-scoped) OR an entry ")
			TEXT("addressed by index or value (alias displayName) plus newName and/or moveTo"),
			{ { TEXT("add"), TEXT("add_enum_value creates an entry; this changes an existing one") },
			  { TEXT("remove"), TEXT("remove_enum_value deletes one") },
			  { TEXT("order"), TEXT("spell it moveTo - the target INDEX, not an ordering") } }))
		{
			return;
		}

		FString Error;
		UUserDefinedEnum* Enum = LoadUserEnum(
			JStrAny(In, { TEXT("enum"), TEXT("enumPath"), TEXT("path") }), Error);
		if (!Enum) { Fail(Out, Error); return; }

		const bool bHasBitflags = In->HasField(TEXT("bitflags"));
		const bool bHasEntry = In->HasField(TEXT("index")) || In->HasField(TEXT("value"))
							|| In->HasField(TEXT("displayName"));
		if (bHasBitflags && bHasEntry)
		{
			Fail(Out, TEXT("bitflags is a property of the whole ENUM and index/value address one "
				TEXT("ENTRY - a call carrying both would have to pick an order, and either choice "
					 "surprises half the callers. Make two calls. NOTHING was changed.")));
			return;
		}
		if (!bHasBitflags && !bHasEntry)
		{
			Fail(Out, TEXT("nothing to change - pass bitflags, or address an entry with index or "
				TEXT("value. NOTHING was changed.")));
			return;
		}

		// The user-facing entry count excludes the hidden _MAX the engine appends.
		const int32 Count = FMath::Max(0, Enum->NumEnums() - 1);
		Out->SetStringField(TEXT("enum"), Enum->GetPathName());
		Out->SetNumberField(TEXT("entryCount"), Count);

		// --- enum-scoped: bitflags ---------------------------------------------------------------
		if (bHasBitflags)
		{
			const bool bWant = JBool(In, TEXT("bitflags"), false);
			const bool bWas = FEnumEditorUtils::IsEnumeratorBitflagsType(Enum);
			if (bWas == bWant)
			{
				Out->SetBoolField(TEXT("bitflags"), bWas);
				Out->SetBoolField(TEXT("changed"), false);
				Out->SetStringField(TEXT("note"),
					TEXT("the enum is already in that state - nothing was changed, and nothing "
						 "needed to be."));
				return;
			}
			FEnumEditorUtils::SetEnumeratorBitflagsTypeState(Enum, bWant);
			// READ BACK: the setter returns void.
			const bool bNow = FEnumEditorUtils::IsEnumeratorBitflagsType(Enum);
			if (bNow != bWant)
			{
				Fail(Out, TEXT("the bitflags state was set and reads back unchanged. NOTHING "
					TEXT("usable was produced.")));
				return;
			}
			Enum->MarkPackageDirty();
			Out->SetBoolField(TEXT("bitflags"), bNow);
			Out->SetBoolField(TEXT("changed"), true);
			Out->SetStringField(TEXT("bitflagsNote"),
				TEXT("bitflags is enum METADATA, not a property - which is why nothing reflective "
					 "could reach it. Turning it on does NOT renumber existing entries, so values "
					 "that were 0,1,2 are still 0,1,2 rather than 1,2,4."));
			Out->SetStringField(TEXT("assetNote"),
				TEXT("the enum is dirty and NOTHING has been saved."));
			return;
		}

		// --- entry-scoped: address it ------------------------------------------------------------
		int32 Index = INDEX_NONE;
		if (In->HasField(TEXT("index")))
		{
			Index = JInt(In, TEXT("index"), -1);
		}
		else
		{
			const FString Want = JStrAny(In, { TEXT("value"), TEXT("displayName") });
			for (int32 i = 0; i < Count; ++i)
			{
				if (Enum->GetDisplayNameTextByIndex(i).ToString() == Want)
				{
					Index = i;
					break;
				}
			}
			if (Index == INDEX_NONE)
			{
				TArray<FString> Have;
				for (int32 i = 0; i < Count; ++i)
				{
					Have.Add(Enum->GetDisplayNameTextByIndex(i).ToString());
				}
				Fail(Out, FString::Printf(
					TEXT("no entry displayed as '%s'. This enum has: %s. NOTHING was changed."),
					*Want, *FString::Join(Have, TEXT(", "))));
				return;
			}
		}
		if (Index < 0 || Index >= Count)
		{
			Fail(Out, FString::Printf(
				TEXT("index %d is out of range - this enum has %d entr%s, so valid indices are "
					 "0..%d. NOTHING was changed."),
				Index, Count, Count == 1 ? TEXT("y") : TEXT("ies"), Count - 1));
			return;
		}
		Out->SetNumberField(TEXT("index"), Index);
		Out->SetStringField(TEXT("wasNamed"), Enum->GetDisplayNameTextByIndex(Index).ToString());

		// --- rename ------------------------------------------------------------------------------
		const FString NewName = JStr(In, TEXT("newName"));
		if (!NewName.IsEmpty())
		{
			// THE CHECK A RAW set_property WRITE SKIPS. Two entries with the same display name
			// compile, and then a Blueprint switch on the enum has two indistinguishable pins.
			if (!FEnumEditorUtils::IsEnumeratorDisplayNameValid(Enum, Index, FText::FromString(NewName)))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is not a valid display name here - the usual cause is that another "
						 "entry already uses it, which compiles and then gives you two "
						 "indistinguishable pins on every switch. NOTHING was changed."), *NewName));
				return;
			}
			FEnumEditorUtils::SetEnumeratorDisplayName(Enum, Index, FText::FromString(NewName));
			const FString Now = Enum->GetDisplayNameTextByIndex(Index).ToString();
			if (Now != NewName)
			{
				Fail(Out, FString::Printf(
					TEXT("the rename was applied and entry %d reads back as '%s'. NOTHING reliable "
						 "was produced."), Index, *Now));
				return;
			}
			Out->SetStringField(TEXT("newName"), Now);
		}

		// --- reorder -----------------------------------------------------------------------------
		if (In->HasField(TEXT("moveTo")))
		{
			const int32 To = JInt(In, TEXT("moveTo"), -1);
			if (To < 0 || To >= Count)
			{
				Fail(Out, FString::Printf(
					TEXT("moveTo %d is out of range - valid targets are 0..%d.%s"), To, Count - 1,
					NewName.IsEmpty() ? TEXT(" NOTHING was changed.")
									  : TEXT(" The RENAME above was already applied.")));
				return;
			}
			if (To != Index)
			{
				const FString Moving = Enum->GetDisplayNameTextByIndex(Index).ToString();
				FEnumEditorUtils::MoveEnumeratorInUserDefinedEnum(Enum, Index, To);
				// READ BACK from the enum, because the move returns void and reordering is
				// exactly the operation where an off-by-one is invisible.
				const FString Landed = Enum->GetDisplayNameTextByIndex(To).ToString();
				if (Landed != Moving)
				{
					Fail(Out, FString::Printf(
						TEXT("entry '%s' was moved to index %d and that slot now reads '%s'. "
							 "NOTHING reliable was produced."), *Moving, To, *Landed));
					return;
				}
				Out->SetNumberField(TEXT("movedTo"), To);
				Out->SetStringField(TEXT("reorderNote"),
					TEXT("reordering changes each entry's INDEX, not its stored value - anything "
						 "that saved an index rather than a name now points somewhere else. "
						 "Blueprints referencing entries by name are unaffected."));
			}
			else
			{
				Out->SetNumberField(TEXT("movedTo"), To);
				Out->SetStringField(TEXT("note"),
					TEXT("moveTo names the index it already occupies, so nothing moved and nothing "
						 "needed to."));
			}
		}

		// The whole order, so a caller can see the result rather than infer it.
		TArray<TSharedPtr<FJsonValue>> Order;
		for (int32 i = 0; i < FMath::Max(0, Enum->NumEnums() - 1); ++i)
		{
			Order.Add(MakeShared<FJsonValueString>(Enum->GetDisplayNameTextByIndex(i).ToString()));
		}
		Out->SetArrayField(TEXT("entries"), Order);
		Enum->MarkPackageDirty();
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the enum is dirty and NOTHING has been saved."));
	}
}
