// MifBridge — Phase 3 breadth: Blueprint interface implement / remove / list.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Engine/Blueprint.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "UObject/Interface.h"
#include "UObject/TopLevelAssetPath.h"

namespace MifBridge
{
	namespace
	{
		bool IsInterfaceClass(UClass* Class)
		{
			return Class && (Class->HasAnyClassFlags(CLASS_Interface) || Class->IsChildOf(UInterface::StaticClass()));
		}
	}

	// --- add_interface ------------------------------------------------------

	void H_add_interface(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		const FString InterfaceName = JStr(In, TEXT("interface"));
		UClass* InterfaceClass = ResolveClass(InterfaceName, Blueprint);
		if (!InterfaceClass)
		{
			Fail(Out, FString::Printf(TEXT("interface class not found: '%s'"), *InterfaceName));
			return;
		}
		if (!IsInterfaceClass(InterfaceClass))
		{
			Fail(Out, FString::Printf(TEXT("'%s' is not an interface class"), *InterfaceName));
			return;
		}

		Blueprint->Modify();
		const bool bOk = FBlueprintEditorUtils::ImplementNewInterface(Blueprint, FTopLevelAssetPath(InterfaceClass));
		if (!bOk)
		{
			Fail(Out, FString::Printf(TEXT("could not implement '%s' (already implemented?)"), *InterfaceName));
			return;
		}
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		Out->SetStringField(TEXT("interface"), InterfaceClass->GetPathName());
	}

	// --- remove_interface ---------------------------------------------------

	void H_remove_interface(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_interface requires confirm=true"));
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		const FString InterfaceName = JStr(In, TEXT("interface"));
		UClass* InterfaceClass = ResolveClass(InterfaceName, Blueprint);
		if (!InterfaceClass)
		{
			Fail(Out, FString::Printf(TEXT("interface class not found: '%s'"), *InterfaceName));
			return;
		}
		if (!IsInterfaceClass(InterfaceClass))
		{
			Fail(Out, FString::Printf(TEXT("'%s' is not an interface class"), *InterfaceName));
			return;
		}

		// Only report success if it was actually implemented.
		TArray<UClass*> Implemented;
		FBlueprintEditorUtils::FindImplementedInterfaces(Blueprint, /*bGetAllInterfaces*/ false, Implemented);
		if (!Implemented.Contains(InterfaceClass))
		{
			Fail(Out, FString::Printf(TEXT("'%s' is not implemented by this blueprint"), *InterfaceName));
			return;
		}

		Blueprint->Modify();
		FBlueprintEditorUtils::RemoveInterface(Blueprint, FTopLevelAssetPath(InterfaceClass), /*bPreserveFunctions*/ false);
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		Out->SetStringField(TEXT("removed"), InterfaceClass->GetPathName());
	}

	// --- list_interfaces ----------------------------------------------------

	void H_list_interfaces(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		const bool bIncludeInherited = JBool(In, TEXT("includeInherited"), false);
		TArray<UClass*> Interfaces;
		FBlueprintEditorUtils::FindImplementedInterfaces(Blueprint, bIncludeInherited, Interfaces);

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UClass* InterfaceClass : Interfaces)
		{
			if (!InterfaceClass)
			{
				continue;
			}
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), InterfaceClass->GetName());
			Json->SetStringField(TEXT("path"), InterfaceClass->GetPathName());
			Arr.Add(MakeShared<FJsonValueObject>(Json));
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("interfaces"), Arr);
	}
}
