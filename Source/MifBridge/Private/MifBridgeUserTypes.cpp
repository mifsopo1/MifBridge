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
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "EdGraph/EdGraphPin.h"   // FEdGraphPinType — complete type needed for ToPinType()/MakePinType
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
		const bool bHasMembers = In->TryGetArrayField(TEXT("members"), MemberArr) && MemberArr && MemberArr->Num() > 0;

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

	// --- list_struct_members ------------------------------------------------
	//   in:  { struct: "/Game/Types/S_Foo" }   out: { structPath, members[] }
	void H_list_struct_members(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
		if (In->TryGetArrayField(TEXT("values"), ValueArr) && ValueArr)
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
