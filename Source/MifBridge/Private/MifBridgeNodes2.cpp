// MifBridge — phase-3 node endpoints: custom event, make/break struct, self, object literal,
// function creation, and the resolve_struct introspection helper.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "K2Node_BreakStruct.h"
#include "K2Node_CustomEvent.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_FunctionResult.h"
#include "K2Node_Literal.h"
#include "K2Node_MakeStruct.h"
#include "K2Node_Self.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "Kismet/BlueprintFunctionLibrary.h"   // UBlueprintFunctionLibrary — function-library base for create_blueprint
#include "UObject/Interface.h"                  // UInterface — blueprint interface base
#include "WidgetBlueprint.h"                          // UWidgetBlueprint — WidgetBlueprint create
#include "Blueprint/WidgetBlueprintGeneratedClass.h"  // UWidgetBlueprintGeneratedClass
#include "Blueprint/WidgetTree.h"                      // UWidgetTree::ConstructWidget
#include "Blueprint/UserWidget.h"                      // UUserWidget parent
#include "Components/CanvasPanel.h"                    // UCanvasPanel root
#include "AssetRegistry/AssetRegistryModule.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"
#include "UObject/Script.h"
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
	// Shared: create an empty function graph (entry + result terminators). Callers add pins.
	UEdGraph* CreateFunctionGraph(UBlueprint* Blueprint, const FString& Name, bool bPure, FString& OutError)
	{
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			if (Graph && Graph->GetName() == Name)
			{
				OutError = FString::Printf(TEXT("function already exists: %s"), *Name);
				return nullptr;
			}
		}

		Blueprint->Modify();
		UEdGraph* NewGraph = FBlueprintEditorUtils::CreateNewGraph(
			Blueprint, FName(*Name), UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
		if (!NewGraph)
		{
			OutError = TEXT("CreateNewGraph failed");
			return nullptr;
		}

		// AddFunctionGraph spawns the entry (and result) terminator nodes, appends to
		// FunctionGraphs, and marks the blueprint structurally modified.
		FBlueprintEditorUtils::AddFunctionGraph<UClass>(Blueprint, NewGraph, /*bIsUserCreated*/ true, static_cast<UClass*>(nullptr));

		if (bPure)
		{
			TArray<UK2Node_FunctionEntry*> Entries;
			NewGraph->GetNodesOfClass(Entries);
			if (Entries.Num() > 0)
			{
				Entries[0]->Modify();
				Entries[0]->AddExtraFlags(static_cast<int32>(FUNC_BlueprintPure));
			}
		}
		return NewGraph;
	}

	namespace
	{
		// Parse a JSON array of {name, type, container?} into (name, pin-type) pairs.
		bool ParsePinSpecs(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
			TArray<TPair<FName, FEdGraphPinType>>& OutPins, FString& OutError)
		{
			const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
			if (!In->TryGetArrayField(Field, Arr) || Arr == nullptr)
			{
				return true; // absent = no pins, not an error
			}
			for (const TSharedPtr<FJsonValue>& Value : *Arr)
			{
				const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
				if (!Value.IsValid() || !Value->TryGetObject(ObjPtr) || ObjPtr == nullptr)
				{
					continue;
				}
				const TSharedRef<FJsonObject> Obj = ObjPtr->ToSharedRef();
				FString PinName = JStr(Obj, TEXT("name"));
				PinName.TrimStartAndEndInline();
				if (!IsValidIdentifier(PinName))
				{
					OutError = FString::Printf(TEXT("invalid pin name '%s' in %s"), *PinName, Field);
					return false;
				}
				FEdGraphPinType PinType;
				if (!MakePinType(JStr(Obj, TEXT("type")), JStr(Obj, TEXT("container")), PinType, OutError))
				{
					return false;
				}
				OutPins.Emplace(FName(*PinName), PinType);
			}
			return true;
		}
	}

	// --- resolve_struct -----------------------------------------------------

	void H_resolve_struct(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required"));
			return;
		}
		if (UScriptStruct* Struct = ResolveStruct(Name))
		{
			Out->SetBoolField(TEXT("found"), true);
			Out->SetStringField(TEXT("name"), Struct->GetName());
			Out->SetStringField(TEXT("path"), Struct->GetPathName());
		}
		else
		{
			Out->SetBoolField(TEXT("found"), false);
			Out->SetStringField(TEXT("message"), FString::Printf(TEXT("no UScriptStruct resolved for '%s'"), *Name));
		}
	}

	// --- Nodes --------------------------------------------------------------

	void H_add_self(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_Self* Node = NewObject<UK2Node_Self>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_custom_event(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString Raw = JStr(In, TEXT("name"));
		FString Name = Raw;
		Name.TrimStartAndEndInline();
		if (!IsValidIdentifier(Name))
		{
			Fail(Out, FString::Printf(TEXT("invalid event name '%s'"), *Raw));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		// Optional typed parameters {name,type,container?}. Event params flow OUT of the node.
		TArray<TPair<FName, FEdGraphPinType>> Params;
		FString ParseError;
		if (!ParsePinSpecs(In, TEXT("inputs"), Params, ParseError))
		{
			Fail(Out, ParseError);
			return;
		}

		UK2Node_CustomEvent* Node = NewObject<UK2Node_CustomEvent>(Graph);
		Node->CustomFunctionName = FName(*Name); // inherited from UK2Node_Event; must precede AllocateDefaultPins
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		if (Params.Num() > 0)
		{
			Node->Modify();
			for (const TPair<FName, FEdGraphPinType>& Param : Params)
			{
				Node->CreateUserDefinedPin(Param.Key, Param.Value, EGPD_Output, /*bUseUniqueName*/ true);
			}
			Node->ReconstructNode();
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_make_struct(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString StructName = JStr(In, TEXT("structName"));
		UScriptStruct* Struct = ResolveStruct(StructName);
		if (!Struct)
		{
			Fail(Out, FString::Printf(TEXT("struct not found: '%s'"), *StructName));
			return;
		}
		if (!UK2Node_MakeStruct::CanBeMade(Struct))
		{
			Fail(Out, FString::Printf(TEXT("struct '%s' cannot be made in a Blueprint (no BP-visible members?)"), *StructName));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_MakeStruct* Node = NewObject<UK2Node_MakeStruct>(Graph);
		Node->StructType = Struct;               // inherited from UK2Node_StructOperation
		Node->bMadeAfterOverridePinRemoval = true; // skip legacy override-pin upgrade
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_break_struct(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString StructName = JStr(In, TEXT("structName"));
		UScriptStruct* Struct = ResolveStruct(StructName);
		if (!Struct)
		{
			Fail(Out, FString::Printf(TEXT("struct not found: '%s'"), *StructName));
			return;
		}
		// Note: UK2Node_BreakStruct::CanBeBroken is not BLUEPRINTGRAPH_API-exported (unlike
		// MakeStruct::CanBeMade), so we can't pre-check it here — a struct with no breakable
		// members simply yields an empty break node, visible via compile read-back.

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_BreakStruct* Node = NewObject<UK2Node_BreakStruct>(Graph);
		Node->StructType = Struct;
		Node->bMadeAfterOverridePinRemoval = true;
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_literal(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		// UK2Node_Literal is for OBJECT-reference literals only. For scalar literals
		// (int/float/bool/string/name), set the consuming pin's default via set_pin_default.
		const FString ObjectPath = JStr(In, TEXT("object"));
		UObject* Object = nullptr;
		if (!ObjectPath.IsEmpty())
		{
			Object = StaticLoadObject(UObject::StaticClass(), nullptr, *ObjectPath, nullptr, LOAD_NoWarn);
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_Literal* Node = NewObject<UK2Node_Literal>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));
		if (Object)
		{
			Node->SetObjectRef(Object); // retypes the value pin to the object's class
		}

		MarkStructural(Blueprint);
		if (!ObjectPath.IsEmpty() && !Object)
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(TEXT("object not found: '%s' (created an untyped literal)"), *ObjectPath));
		}
		EmitNode(Out, Node);
	}

	// --- create_function ----------------------------------------------------

	void H_create_function(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
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
			Fail(Out, FString::Printf(TEXT("invalid function name '%s'"), *Raw));
			return;
		}

		TArray<TPair<FName, FEdGraphPinType>> Inputs;
		TArray<TPair<FName, FEdGraphPinType>> Outputs;
		FString ParseError;
		if (!ParsePinSpecs(In, TEXT("inputs"), Inputs, ParseError) ||
			!ParsePinSpecs(In, TEXT("outputs"), Outputs, ParseError))
		{
			Fail(Out, ParseError);
			return;
		}

		const bool bPure = JBool(In, TEXT("pure"), false);

		// Structural edits are transacted in a tight scope; the compile runs AFTER the
		// transaction closes so class reinstancing is never captured as an undo step.
		UEdGraph* Graph = nullptr;
		UK2Node_FunctionEntry* Entry = nullptr;
		{
			FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "CreateFunction", "Mif Bridge: create_function"));

			FString CreateError;
			Graph = CreateFunctionGraph(Blueprint, Name, bPure, CreateError);
			if (!Graph)
			{
				Fail(Out, CreateError);
				return;
			}

			// Inputs live on the ENTRY node as EGPD_Output (entry outputs args into the graph).
			TArray<UK2Node_FunctionEntry*> Entries;
			Graph->GetNodesOfClass(Entries);
			Entry = Entries.Num() > 0 ? Entries[0] : nullptr;
			if (Entry)
			{
				Entry->Modify();
				for (const TPair<FName, FEdGraphPinType>& Pin : Inputs)
				{
					Entry->CreateUserDefinedPin(Pin.Key, Pin.Value, EGPD_Output, /*bUseUniqueName*/ true);
				}
			}

			// Outputs live on the RESULT node as EGPD_Input. Create one if the void signature had none.
			if (Outputs.Num() > 0)
			{
				TArray<UK2Node_FunctionResult*> Results;
				Graph->GetNodesOfClass(Results);
				UK2Node_FunctionResult* Result = Results.Num() > 0 ? Results[0] : nullptr;
				if (!Result)
				{
					Result = NewObject<UK2Node_FunctionResult>(Graph);
					PlaceAndInit(Graph, Result, 800, 0);
				}
				// ALWAYS ensure entry.then -> result.execute. AddFunctionGraph's default result node ships with that
				// exec pin UNCONNECTED, so a function WITH outputs (result pre-exists, so the create branch is skipped)
				// otherwise has an unreachable Return: it compiles valid-but-INERT, the out-param is never written, and
				// its feeding value is dead-code-eliminated. (This left every authored testbed function silently doing
				// nothing until the missing link was wired by hand.)
				if (Entry)
				{
					UEdGraphPin* EntryThen = FindPin(Entry, TEXT("then"), EGPD_Output, /*bRequireDir*/ true);
					UEdGraphPin* ResultExec = FindPin(Result, TEXT("execute"), EGPD_Input, /*bRequireDir*/ true);
					if (EntryThen && ResultExec && ResultExec->LinkedTo.Num() == 0)
					{
						K2()->TryCreateConnection(EntryThen, ResultExec);
					}
				}
				Result->Modify();
				for (const TPair<FName, FEdGraphPinType>& Pin : Outputs)
				{
					Result->CreateUserDefinedPin(Pin.Key, Pin.Value, EGPD_Input, /*bUseUniqueName*/ true);
				}
			}

			MarkStructural(Blueprint);
		}

		// Compile OUTSIDE the transaction so the UFunction materialises on the skeleton class
		// (callable immediately) without capturing reinstancing in the undo buffer.
		TSharedRef<FJsonObject> CompileOut = MakeShared<FJsonObject>();
		CompileBlueprintInto(Blueprint, CompileOut);

		Out->SetStringField(TEXT("graphId"), GraphIdOf(Blueprint, Graph));
		Out->SetStringField(TEXT("name"), Name);
		Out->SetNumberField(TEXT("inputs"), Inputs.Num());
		Out->SetNumberField(TEXT("outputs"), Outputs.Num());
		if (Entry)
		{
			Out->SetStringField(TEXT("entryNodeGuid"), Entry->NodeGuid.ToString());
		}
		Out->SetObjectField(TEXT("compile"), CompileOut);
	}

	// Mint a fresh Blueprint asset. The bridge was built to EDIT existing BPs; this is the one thing it couldn't do,
	// and it's what the reconstructor testbed needs (author a known graph → cook → reconstruct → diff = ground truth).
	// SELF-MANAGED: CreateBlueprint + CompileBlueprint reinstance a class, which must never sit inside RunEndpoint's
	// transaction (a later Ctrl-Z would restore a dead CDO and crash) — registered in IsSelfManagedEndpoint.
	//   in:  { path: "/Game/MifTestbed/BP_Foo", parentClass?: "Actor" (default), overwrite?: false }
	//   out: { blueprintId, class, parentClass, eventGraphId? }
	void H_create_blueprint(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString Path = JStr(In, TEXT("path"));
		if (Path.IsEmpty() || !Path.StartsWith(TEXT("/Game/")))
		{
			Fail(Out, TEXT("path required, must start with /Game/ (e.g. /Game/MifTestbed/BP_Foo)"));
			return;
		}
		const FString AssetName = FPackageName::GetLongPackageAssetName(Path);
		if (!IsValidIdentifier(AssetName))
		{
			Fail(Out, FString::Printf(TEXT("invalid asset name '%s' (from path '%s')"), *AssetName, *Path));
			return;
		}

		const FString ParentName = JStr(In, TEXT("parentClass"), TEXT("Actor"));
		// blueprintType: "Normal" (default), "FunctionLibrary", "Interface", "MacroLibrary".
		// Library/interface types are NOT "blueprintable of a parent class" (CanCreateBlueprintOfClass rejects them),
		// so they take a fixed base + the matching EBlueprintType and bypass that check.
		const FString BpTypeStr = JStr(In, TEXT("blueprintType"), TEXT("Normal"));
		EBlueprintType BpType = BPTYPE_Normal;
		UClass* ParentClass = nullptr;
		bool bLibraryLike = false;
		bool bWidget      = false;   // WidgetBlueprint via blueprintType=WidgetBlueprint
		if (BpTypeStr.Equals(TEXT("FunctionLibrary"), ESearchCase::IgnoreCase))
		{
			BpType = BPTYPE_FunctionLibrary;
			ParentClass = UBlueprintFunctionLibrary::StaticClass();
			bLibraryLike = true;
		}
		else if (BpTypeStr.Equals(TEXT("Interface"), ESearchCase::IgnoreCase))
		{
			BpType = BPTYPE_Interface;
			ParentClass = UInterface::StaticClass();
			bLibraryLike = true;
		}
		else if (BpTypeStr.Equals(TEXT("MacroLibrary"), ESearchCase::IgnoreCase))
		{
			BpType = BPTYPE_MacroLibrary;
			ParentClass = ResolveClass(ParentName, nullptr);   // macro libs still parent to a real class
			bLibraryLike = true;
		}
		else if (BpTypeStr.Equals(TEXT("WidgetBlueprint"), ESearchCase::IgnoreCase))
		{
			// Widgets go the normal blueprintable path (CanCreateBlueprintOfClass(UUserWidget)==true),
			// so NO bLibraryLike bypass. Only the class-type pair + a post-create root panel differ.
			BpType  = BPTYPE_Normal;
			bWidget = true;
			const FString WidgetParent = JStr(In, TEXT("parentClass"), TEXT("UserWidget"));
			ParentClass = ResolveClass(WidgetParent, nullptr);
			if (ParentClass && !ParentClass->IsChildOf(UUserWidget::StaticClass()))
			{
				Fail(Out, FString::Printf(TEXT("parentClass '%s' is not a UUserWidget subclass"), *ParentClass->GetName()));
				return;
			}
		}
		else
		{
			ParentClass = ResolveClass(ParentName, nullptr);
		}
		if (!ParentClass)
		{
			Fail(Out, FString::Printf(TEXT("parent class '%s' not found"), *ParentName));
			return;
		}
		if (!bLibraryLike && !FKismetEditorUtilities::CanCreateBlueprintOfClass(ParentClass))
		{
			Fail(Out, FString::Printf(TEXT("cannot create a Blueprint of parent class '%s' (for a function library/interface pass blueprintType=FunctionLibrary/Interface)"), *ParentClass->GetName()));
			return;
		}

		// Refuse to clobber silently (mirrors the confirm-destructive rule). Overwrite is deliberately NOT supported
		// here — deleting a loaded asset safely is out of scope; pick a new path or delete it in the editor.
		const FString ObjectPath = Path + TEXT(".") + AssetName;
		if (StaticLoadObject(UBlueprint::StaticClass(), nullptr, *ObjectPath, nullptr, LOAD_NoWarn | LOAD_Quiet))
		{
			Fail(Out, FString::Printf(TEXT("a Blueprint already exists at '%s' — pick a new path or delete it first"), *ObjectPath));
			return;
		}

		UPackage* Package = CreatePackage(*Path);
		if (!Package)
		{
			Fail(Out, FString::Printf(TEXT("failed to create package '%s'"), *Path));
			return;
		}

		// BpType selects Normal vs FunctionLibrary/Interface/MacroLibrary (see above).
		// Widget BPs need the UWidgetBlueprint / UWidgetBlueprintGeneratedClass pair.
		TSubclassOf<UBlueprint>               BpClass  = UBlueprint::StaticClass();
		TSubclassOf<UBlueprintGeneratedClass> GenClass = UBlueprintGeneratedClass::StaticClass();
		if (bWidget)
		{
			BpClass  = UWidgetBlueprint::StaticClass();
			GenClass = UWidgetBlueprintGeneratedClass::StaticClass();
		}
		UBlueprint* NewBP = FKismetEditorUtilities::CreateBlueprint(
			ParentClass, Package, FName(*AssetName), BpType,
			BpClass, GenClass, TEXT("MifBridge"));
		if (!NewBP)
		{
			Fail(Out, TEXT("CreateBlueprint returned null"));
			return;
		}

		if (bWidget)
		{
			// WidgetTree is already a default subobject; only add a root CanvasPanel if absent.
			// Must happen BEFORE CompileBlueprint or the asset ships with a null root.
			UWidgetBlueprint* WBP = CastChecked<UWidgetBlueprint>(NewBP);
			if (WBP->WidgetTree && WBP->WidgetTree->RootWidget == nullptr)
			{
				UCanvasPanel* Root = WBP->WidgetTree->ConstructWidget<UCanvasPanel>(UCanvasPanel::StaticClass());
				WBP->WidgetTree->RootWidget = Root;
			}
		}

		FAssetRegistryModule::AssetCreated(NewBP);
		Package->MarkPackageDirty();
		FKismetEditorUtilities::CompileBlueprint(NewBP);   // outside any transaction (self-managed)

		Out->SetStringField(TEXT("blueprintId"), NewBP->GetPathName());
		Out->SetStringField(TEXT("name"), NewBP->GetName());
		if (NewBP->GeneratedClass) { Out->SetStringField(TEXT("class"), NewBP->GeneratedClass->GetPathName()); }
		Out->SetStringField(TEXT("parentClass"), ParentClass->GetPathName());
		if (UEdGraph* EventGraph = FBlueprintEditorUtils::FindEventGraph(NewBP))
		{
			Out->SetStringField(TEXT("eventGraphId"), GraphIdOf(NewBP, EventGraph));
		}
		UE_LOG(LogMifBridge, Log, TEXT("create_blueprint: %s (parent %s)"), *NewBP->GetPathName(), *ParentClass->GetName());
	}
}
