// MifBridge — Phase 3 completion: interface-function implementation graphs + function removal.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "UObject/Class.h"
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
	// --- implement_interface_function ---------------------------------------
	// Adds the implementation graph for a return-valued interface function (mirrors
	// SMyBlueprint::ImplementFunction). Event-style interface functions (no return) are
	// placed on the event graph instead — use add_override_event for those.

	void H_implement_interface_function(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		FString FunctionName = JStr(In, TEXT("function"));
		FunctionName.TrimStartAndEndInline();
		if (FunctionName.IsEmpty())
		{
			Fail(Out, TEXT("function is required"));
			return;
		}

		FBlueprintEditorUtils::ConformImplementedInterfaces(Blueprint);

		UFunction* OverrideFunction = nullptr;
		UClass* OverrideClass = FBlueprintEditorUtils::GetOverrideFunctionClass(Blueprint, FName(*FunctionName), &OverrideFunction);
		if (!OverrideFunction || !OverrideClass)
		{
			Fail(Out, FString::Printf(TEXT("no overridable function '%s' found (is the interface implemented?)"), *FunctionName));
			return;
		}
		if (UEdGraphSchema_K2::FunctionCanBePlacedAsEvent(OverrideFunction))
		{
			Fail(Out, FString::Printf(TEXT("'%s' is an event-style interface function — use add_override_event instead"), *FunctionName));
			return;
		}

		// The implementation graph is named exactly after the interface function so the
		// terminators resolve the signature. Guard against a duplicate.
		if (FindObject<UEdGraph>(Blueprint, *FunctionName))
		{
			Fail(Out, FString::Printf(TEXT("function graph '%s' already exists"), *FunctionName));
			return;
		}

		Blueprint->Modify();
		UEdGraph* NewGraph = FBlueprintEditorUtils::CreateNewGraph(
			Blueprint, FName(*FunctionName), UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
		if (!NewGraph)
		{
			Fail(Out, TEXT("CreateNewGraph failed"));
			return;
		}
		// bIsUserCreated=false + the interface UClass makes the entry/result terminators lock
		// to the interface signature (including the return value).
		FBlueprintEditorUtils::AddFunctionGraph<UClass>(Blueprint, NewGraph, /*bIsUserCreated*/ false, OverrideClass);
		NewGraph->Modify();
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);

		Out->SetStringField(TEXT("graphId"), GraphIdOf(Blueprint, NewGraph));
		Out->SetStringField(TEXT("function"), FunctionName);
		Out->SetStringField(TEXT("interfaceClass"), OverrideClass->GetName());
	}

	// --- remove_function ----------------------------------------------------

	void H_remove_function(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_function requires confirm=true"));
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		const FString Name = JStr(In, TEXT("name"));

		UEdGraph* Graph = nullptr;
		for (UEdGraph* Candidate : Blueprint->FunctionGraphs)
		{
			if (Candidate && Candidate->GetName() == Name)
			{
				Graph = Candidate;
				break;
			}
		}
		if (!Graph)
		{
			Fail(Out, FString::Printf(TEXT("function graph '%s' not found"), *Name));
			return;
		}

		Blueprint->Modify();
		FBlueprintEditorUtils::RemoveGraph(Blueprint, Graph, EGraphRemoveFlags::Default);
		Out->SetStringField(TEXT("removed"), Name);
	}
}
