// MifBridge — Phase 3 breadth: event dispatchers (Blueprint multicast delegates).
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "K2Node_AddDelegate.h"
#include "K2Node_CallDelegate.h"
#include "K2Node_FunctionEntry.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "ScopedTransaction.h"
#include "UObject/Script.h"
#include "UObject/UnrealType.h"

namespace MifBridge
{
	namespace
	{
		// ParseDispatcherParams was a copy of MifBridgeNodes2.cpp's ParsePinSpecs; both are now
		// MifBridge::ParsePinSpecs (MifBridgeCommon.cpp, declared in MifBridgeHandlers.h). The comment
		// that used to justify the copy ("kept file-local to avoid header/type coupling") was not true:
		// the shared header already forward-declares FEdGraphPinType and declares MakePinType, which is
		// the only type coupling the parser has. Do NOT re-add a local copy.

		// Spawn a delegate node bound to a dispatcher property. SetFromProperty MUST run
		// before AllocateDefaultPins, so it happens before PlaceAndInit.
		//
		// Optional "targetClass": binds to a dispatcher declared on an EXTERNAL class instead of
		// this Blueprint's own class (e.g. binding to a GameMode's multicast delegate from an
		// unrelated actor) — mirrors the visible-Target-pin pattern the editor itself produces
		// when you drag off a reference of that external type and pick "Bind Event to X". Without
		// it, behavior is unchanged: self-context, dispatcher must be declared on this Blueprint.
		template<typename TNode>
		void SpawnDelegateNode(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
		{
			UBlueprint* Blueprint = nullptr;
			UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
			if (!Graph)
			{
				return;
			}
			const FString Dispatcher = JStr(In, TEXT("dispatcher"));
			const FString TargetClassName = JStr(In, TEXT("targetClass"));

			UClass* OwnerClass = nullptr;
			bool bSelfContext = true;
			if (!TargetClassName.IsEmpty())
			{
				OwnerClass = ResolveClass(TargetClassName, Blueprint);
				bSelfContext = false;
				if (!OwnerClass)
				{
					Fail(Out, FString::Printf(TEXT("targetClass not found: '%s'"), *TargetClassName));
					return;
				}
			}
			else
			{
				OwnerClass = Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass : Blueprint->GeneratedClass;
			}

			FMulticastDelegateProperty* Prop = OwnerClass
				? CastField<FMulticastDelegateProperty>(OwnerClass->FindPropertyByName(FName(*Dispatcher)))
				: nullptr;
			if (!Prop)
			{
				Fail(Out, FString::Printf(TEXT("event dispatcher '%s' not found on %s"), *Dispatcher,
					OwnerClass ? *OwnerClass->GetName() : TEXT("(no class)")));
				return;
			}

			Blueprint->Modify();
			Graph->Modify();

			TNode* Node = NewObject<TNode>(Graph);
			Node->SetFromProperty(Prop, bSelfContext, OwnerClass);
			PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

			MarkStructural(Blueprint);
			EmitNode(Out, Node);
		}
	}

	// --- add_event_dispatcher (self-managed: compiles) ----------------------

	void H_add_event_dispatcher(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		const FString Raw = JStr(In, TEXT("name"));
		FString Name = Raw;
		Name.TrimStartAndEndInline();
		if (!IsValidIdentifier(Name))
		{
			Fail(Out, FString::Printf(TEXT("invalid dispatcher name '%s'"), *Raw));
			return;
		}
		for (UEdGraph* Graph : Blueprint->DelegateSignatureGraphs)
		{
			if (Graph && Graph->GetName() == Name)
			{
				Fail(Out, FString::Printf(TEXT("event dispatcher '%s' already exists"), *Name));
				return;
			}
		}

		TArray<TPair<FName, FEdGraphPinType>> Params;
		FString ParseError;
		if (!ParsePinSpecs(In, TEXT("inputs"), Params, ParseError))
		{
			Fail(Out, ParseError);
			return;
		}

		// A working dispatcher needs BOTH a PC_MCDelegate member variable AND a signature
		// graph named the same — the compiler synthesises the FMulticastDelegateProperty from
		// the member var, and ConformDelegateSignatureGraphs would STRIP the graph if no
		// matching member var exists. This mirrors FBlueprintEditor::OnAddNewDelegate.
		{
			FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "AddDispatcher", "Mif Bridge: add_event_dispatcher"));
			Blueprint->Modify();

			FEdGraphPinType DelegateType;
			DelegateType.PinCategory = UEdGraphSchema_K2::PC_MCDelegate;
			if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, FName(*Name), DelegateType))
			{
				Fail(Out, FString::Printf(TEXT("could not create delegate variable '%s' (name in use?)"), *Name));
				return;
			}

			UEdGraph* SignatureGraph = FBlueprintEditorUtils::CreateNewGraph(
				Blueprint, FName(*Name), UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
			if (!SignatureGraph)
			{
				FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, FName(*Name));
				Fail(Out, TEXT("could not create delegate signature graph"));
				return;
			}
			SignatureGraph->bEditable = false;

			const UEdGraphSchema_K2* Schema = GetDefault<UEdGraphSchema_K2>();
			Schema->CreateDefaultNodesForGraph(*SignatureGraph);
			Schema->CreateFunctionGraphTerminators(*SignatureGraph, static_cast<UClass*>(nullptr));
			Schema->AddExtraFunctionFlags(SignatureGraph, (FUNC_BlueprintCallable | FUNC_BlueprintEvent | FUNC_Public));
			Schema->MarkFunctionEntryAsEditable(SignatureGraph, true);

			Blueprint->DelegateSignatureGraphs.Add(SignatureGraph);

			if (Params.Num() > 0)
			{
				TArray<UK2Node_FunctionEntry*> Entries;
				SignatureGraph->GetNodesOfClass(Entries);
				if (Entries.Num() > 0)
				{
					Entries[0]->Modify();
					for (const TPair<FName, FEdGraphPinType>& Param : Params)
					{
						Entries[0]->CreateUserDefinedPin(Param.Key, Param.Value, EGPD_Output, /*bUseUniqueName*/ true);
					}
				}
			}

			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		}

		// Compile OUTSIDE the transaction so the multicast delegate property materialises.
		TSharedRef<FJsonObject> CompileOut = MakeShared<FJsonObject>();
		CompileBlueprintInto(Blueprint, CompileOut);

		Out->SetStringField(TEXT("dispatcher"), Name);
		Out->SetNumberField(TEXT("params"), Params.Num());
		Out->SetObjectField(TEXT("compile"), CompileOut);
	}

	// --- add_call_dispatcher / add_bind_dispatcher --------------------------

	void H_add_call_dispatcher(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		SpawnDelegateNode<UK2Node_CallDelegate>(In, Out);
	}

	void H_add_bind_dispatcher(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		SpawnDelegateNode<UK2Node_AddDelegate>(In, Out);
	}

	// --- list_dispatchers ---------------------------------------------------

	void H_list_dispatchers(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UEdGraph* Graph : Blueprint->DelegateSignatureGraphs)
		{
			if (!Graph)
			{
				continue;
			}
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), Graph->GetName());
			Arr.Add(MakeShared<FJsonValueObject>(Json));
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("dispatchers"), Arr);
	}
}
