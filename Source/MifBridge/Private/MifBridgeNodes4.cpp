// MifBridge — Phase 3 completion common nodes: sequence, spawn actor, get subsystem,
// make array, format text, get data-table row, comment box.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphNode_Comment.h"
#include "EdGraphSchema_K2.h"
#include "Blueprint/UserWidget.h"
#include "Engine/Blueprint.h"
#include "Engine/DataTable.h"
#include "GameFramework/Actor.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_FormatText.h"
#include "K2Node_GetDataTableRow.h"
#include "K2Node_GetSubsystem.h"
#include "K2Node_MakeArray.h"
#include "K2Node_MakeMap.h"
#include "K2Node_SpawnActorFromClass.h"
#include "Nodes/K2Node_CreateWidget.h"   // UMGEditor private header (see MifBridge.Build.cs PrivateIncludePaths)
#include "Kismet2/BlueprintEditorUtils.h"
#include "Subsystems/Subsystem.h"
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
	// --- add_sequence -------------------------------------------------------

	void H_add_sequence(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_ExecutionSequence* Node = NewObject<UK2Node_ExecutionSequence>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y"))); // gives then_0, then_1

		const int32 Outputs = FMath::Clamp(JInt(In, TEXT("outputs"), 2), 2, 64);
		for (int32 Index = 2; Index < Outputs; ++Index)
		{
			Node->AddInputPin(); // IK2Node_AddPinInterface — on Sequence this adds an OUTPUT exec pin
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_spawn_actor ----------------------------------------------------

	void H_add_spawn_actor(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString ClassName = JStr(In, TEXT("actorClass"));
		UClass* ActorClass = ResolveClass(ClassName, Blueprint);
		if (!ActorClass || !ActorClass->IsChildOf(AActor::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not an Actor class: '%s'"), *ClassName));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_SpawnActorFromClass* Node = NewObject<UK2Node_SpawnActorFromClass>(Graph);
		// SpawnActor is SPECIAL: its PostPlacedNewNode() reads the ScaleMethod pin via
		// FindPinChecked (EdGraphNode.h:563 check(Result)), which only exists AFTER
		// AllocateDefaultPins. The generic PlaceAndInit order (PostPlacedNewNode -> AllocateDefaultPins)
		// therefore asserts and CRASHES the editor. Allocate pins FIRST for this node type only.
		Node->SetFlags(RF_Transactional);
		Graph->AddNode(Node, /*bFromUI*/ false, /*bSelectNewNode*/ false);
		Node->CreateNewGuid();
		Node->AllocateDefaultPins();
		Node->PostPlacedNewNode();
		Node->NodePosX = JInt(In, TEXT("x"));
		Node->NodePosY = JInt(In, TEXT("y"));

		// The class is a pin default, set AFTER AllocateDefaultPins; the change synthesises
		// the exposed spawn-var pins for that actor class.
		if (UEdGraphPin* ClassPin = Node->GetClassPin())
		{
			ClassPin->DefaultObject = ActorClass;
			Node->PinDefaultValueChanged(ClassPin);
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_create_widget --------------------------------------------------
	// UMG CreateWidget node (UK2Node_CreateWidget, same UK2Node_ConstructObjectFromClass
	// base as SpawnActorFromClass). Setting the Class pin default synthesises the widget's
	// exposed-on-spawn property pins. Needed for the per-mod ModLoaded splash (each mod
	// creates W_MifModLoaded and passes its own name).
	void H_add_create_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString ClassName = JStr(In, TEXT("widgetClass"));
		UClass* WidgetClass = ResolveClass(ClassName, Blueprint);
		if (!WidgetClass || !WidgetClass->IsChildOf(UUserWidget::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not a UserWidget class: '%s'"), *ClassName));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_CreateWidget* Node = NewObject<UK2Node_CreateWidget>(Graph);
		// Mirror the H_add_spawn_actor pins-first ordering (AllocateDefaultPins before
		// PostPlacedNewNode). CreateWidget is safe either way (it doesn't override
		// PostPlacedNewNode; the inherited ConstructObjectFromClass one is nullptr-safe),
		// but the whole ConstructObject family stays on one rule. Do NOT call PlaceAndInit
		// (its PostPlacedNewNode-first order is exactly what crashes SpawnActorFromClass).
		Node->SetFlags(RF_Transactional);
		Graph->AddNode(Node, /*bFromUI*/ false, /*bSelectNewNode*/ false);
		Node->CreateNewGuid();
		Node->AllocateDefaultPins();
		Node->PostPlacedNewNode();
		Node->NodePosX = JInt(In, TEXT("x"));
		Node->NodePosY = JInt(In, TEXT("y"));

		// Class is a pin default, set AFTER AllocateDefaultPins; the change synthesises
		// the exposed-on-spawn widget-property pins for that class. GetClassPin() is
		// inherited from UK2Node_ConstructObjectFromClass.
		if (UEdGraphPin* ClassPin = Node->GetClassPin())
		{
			ClassPin->DefaultObject = WidgetClass;
			Node->PinDefaultValueChanged(ClassPin);
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_get_subsystem --------------------------------------------------

	void H_add_get_subsystem(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString ClassName = JStr(In, TEXT("subsystemClass"));
		UClass* SubsystemClass = ResolveClass(ClassName, Blueprint);
		if (!SubsystemClass || !SubsystemClass->IsChildOf(USubsystem::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not a Subsystem class: '%s'"), *ClassName));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_GetSubsystem* Node = NewObject<UK2Node_GetSubsystem>(Graph);
		Node->Initialize(SubsystemClass); // assigns CustomClass; BEFORE AllocateDefaultPins
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_make_array -----------------------------------------------------

	void H_add_make_array(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_MakeArray* Node = NewObject<UK2Node_MakeArray>(Graph);
		Node->NumInputs = FMath::Clamp(JInt(In, TEXT("numInputs"), 1), 1, 64); // base member; before AllocateDefaultPins
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_make_map -------------------------------------------------------
	// Make Map literal node (UK2Node_MakeMap, same UK2Node_MakeContainer base as MakeArray). numInputs = entry count;
	// each entry gets a Key + Value pin ([0] Key/[0] Value, ...), output "Map". Key/Value pin types resolve on connect
	// (wildcard until wired). Needed for e.g. handler.Add(MakeMap(RecipeID -> TaskID)) — the MifModHelper crafting hook.
	void H_add_make_map(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_MakeMap* Node = NewObject<UK2Node_MakeMap>(Graph);
		Node->NumInputs = FMath::Clamp(JInt(In, TEXT("numInputs"), 1), 1, 64); // base member; before AllocateDefaultPins
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_format_text ----------------------------------------------------

	void H_add_format_text(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_FormatText* Node = NewObject<UK2Node_FormatText>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		// The {tokens} in the Format literal text drive the argument pins.
		const FString Format = JStr(In, TEXT("format"));
		if (!Format.IsEmpty())
		{
			if (UEdGraphPin* FormatPin = Node->GetFormatPin())
			{
				FormatPin->DefaultTextValue = FText::FromString(Format);
				Node->PinDefaultValueChanged(FormatPin);
			}
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_get_data_table_row ---------------------------------------------

	void H_add_get_data_table_row(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_GetDataTableRow* Node = NewObject<UK2Node_GetDataTableRow>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		const FString TablePath = JStr(In, TEXT("dataTable"));
		if (!TablePath.IsEmpty())
		{
			if (UDataTable* Table = LoadObject<UDataTable>(nullptr, *TablePath, nullptr, LOAD_NoWarn))
			{
				if (UEdGraphPin* TablePin = Node->GetDataTablePin())
				{
					TablePin->DefaultObject = Table;
					Node->PinDefaultValueChanged(TablePin); // retypes the result struct to the row struct
				}
			}
			else
			{
				Out->SetStringField(TEXT("warning"), FString::Printf(TEXT("datatable not found: %s"), *TablePath));
			}
		}
		const FString RowName = JStr(In, TEXT("rowName"));
		if (!RowName.IsEmpty())
		{
			if (UEdGraphPin* RowPin = Node->GetRowNamePin())
			{
				K2()->TrySetDefaultValue(*RowPin, RowName);
			}
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_comment --------------------------------------------------------

	void H_add_comment(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UEdGraphNode_Comment* Node = NewObject<UEdGraphNode_Comment>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y"))); // AllocateDefaultPins is a no-op

		Node->NodeWidth = FMath::Max(JInt(In, TEXT("width"), 400), 32);
		Node->NodeHeight = FMath::Max(JInt(In, TEXT("height"), 150), 32);
		Node->NodeComment = JStr(In, TEXT("text"));
		Node->MoveMode = ECommentBoxMode::NoGroupMovement; // don't drag enclosed nodes when moved

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}
}
