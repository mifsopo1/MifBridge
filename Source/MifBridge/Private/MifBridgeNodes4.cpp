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
#include "K2Node_MakeSet.h"
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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("x"), TEXT("y"), TEXT("outputs") },
			TEXT("graphId, x, y, outputs (then_N exec pin count, 2-64, default 2)"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("numOutputs"), TEXT("spell it outputs (add_make_array/add_make_map use numInputs; Sequence uses outputs)") },
			  { TEXT("pins"), TEXT("spell it outputs - it is the count of then_N exec pins") } }))
		{
			return;
		}

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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("actorClass"), TEXT("class"), TEXT("x"), TEXT("y") },
			TEXT("graphId, actorClass (alias: class), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("actor"), TEXT("SpawnActor takes the CLASS to spawn, not an instance - pass actorClass (e.g. /Game/BP/BP_Foo.BP_Foo_C)") },
			  { TEXT("transform"), TEXT("SpawnTransform is a pin - place the node, then set_pin_default or connect_pins") },
			  { TEXT("spawnTransform"), TEXT("SpawnTransform is a pin - place the node, then set_pin_default or connect_pins") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		// STRICT — an empty actorClass used to resolve to the blueprint's OWN class, and if that
		// blueprint is an Actor the IsChildOf check below passes: a silent "spawn a copy of myself".
		UClass* ActorClass = ResolveClassStrictField(In, { TEXT("actorClass"), TEXT("class") }, Blueprint, Out);
		if (!ActorClass)
		{
			return;
		}
		if (!ActorClass->IsChildOf(AActor::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not an Actor class: '%s'"), *ActorClass->GetName()));
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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("widgetClass"), TEXT("class"), TEXT("x"), TEXT("y") },
			TEXT("graphId, widgetClass (alias: class), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("widget"), TEXT("CreateWidget takes the CLASS to create - pass widgetClass (e.g. /Game/UI/W_Foo.W_Foo_C)") },
			  { TEXT("owningPlayer"), TEXT("Owning Player is a pin - place the node, then set_pin_default or connect_pins") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		// STRICT — see add_spawn_actor: on a Widget BP an empty widgetClass self-resolved and passed
		// the IsChildOf check, silently creating a widget of the very blueprint doing the creating.
		UClass* WidgetClass = ResolveClassStrictField(In, { TEXT("widgetClass"), TEXT("class") }, Blueprint, Out);
		if (!WidgetClass)
		{
			return;
		}
		if (!WidgetClass->IsChildOf(UUserWidget::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not a UserWidget class: '%s'"), *WidgetClass->GetName()));
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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("subsystemClass"), TEXT("class"), TEXT("x"), TEXT("y") },
			TEXT("graphId, subsystemClass (alias: class), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("subsystem"), TEXT("spell it subsystemClass - it must name a USubsystem-derived CLASS") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		UClass* SubsystemClass = ResolveClassStrictField(In, { TEXT("subsystemClass"), TEXT("class") }, Blueprint, Out);
		if (!SubsystemClass)
		{
			return;
		}
		if (!SubsystemClass->IsChildOf(USubsystem::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not a Subsystem class: '%s'"), *SubsystemClass->GetName()));
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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("numInputs"), TEXT("x"), TEXT("y") },
			TEXT("graphId, numInputs (element pin count, 1-64, default 1), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("num"), TEXT("spell it numInputs") },
			  { TEXT("count"), TEXT("spell it numInputs") },
			  { TEXT("items"), TEXT("the element values are pins - place the node, then set_pin_default or connect_pins") } }))
		{
			return;
		}

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

	// --- add_make_set -------------------------------------------------------
	// Make Set literal node (UK2Node_MakeSet). The THIRD UK2Node_MakeContainer, alongside MakeArray and
	// MakeMap - both of which this bridge could already place, which is exactly why this one was worth
	// finding: a family missing one member is invisible until somebody needs the missing one, and then
	// it looks like the bridge cannot do Blueprint containers at all.
	//
	// numInputs is the ELEMENT count, as with MakeArray - a Set has one pin per element, not the pin
	// PAIR that MakeMap gives each entry. Element type is wildcard until something is wired to it.
	//
	// Set literals matter more than they look: a Set is how a Blueprint expresses "these, no
	// duplicates, membership tested in constant time", and building one from a Make Array plus a
	// To Set conversion is three nodes where this is one.
	void H_add_make_set(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("numInputs"), TEXT("x"), TEXT("y") },
			TEXT("graphId, numInputs (element pin count, 1-64, default 1), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("num"), TEXT("spell it numInputs") },
			  { TEXT("count"), TEXT("spell it numInputs") },
			  { TEXT("container"), TEXT("not a parameter - add_make_array, add_make_map and add_make_set are separate endpoints, one per node type") },
			  { TEXT("items"), TEXT("the element values are pins - place the node, then set_pin_default or connect_pins") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_MakeSet* Node = NewObject<UK2Node_MakeSet>(Graph);
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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("numInputs"), TEXT("x"), TEXT("y") },
			TEXT("graphId, numInputs (entry count - each entry is one Key + Value pin pair, 1-64, default 1), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("numEntries"), TEXT("spell it numInputs - one 'input' is one Key/Value entry") },
			  { TEXT("entries"), TEXT("spell it numInputs for the COUNT; the keys and values themselves are pins") },
			  { TEXT("pairs"), TEXT("spell it numInputs for the COUNT; the keys and values themselves are pins") } }))
		{
			return;
		}

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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("format"), TEXT("x"), TEXT("y") },
			TEXT("graphId, format (the literal Format text - its {tokens} create the argument pins), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("text"), TEXT("spell it format") },
			  { TEXT("formatText"), TEXT("spell it format") },
			  { TEXT("args"), TEXT("argument pins come from the {tokens} inside format - place the node, then set_pin_default or connect_pins") } }))
		{
			return;
		}

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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("dataTable"), TEXT("rowName"), TEXT("x"), TEXT("y") },
			TEXT("graphId, dataTable (object path of the UDataTable), rowName, x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("table"), TEXT("spell it dataTable") },
			  { TEXT("dataTablePath"), TEXT("spell it dataTable") },
			  { TEXT("row"), TEXT("spell it rowName") } }))
		{
			return;
		}

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
			UEdGraphPin* RowPin = Node->GetRowNamePin();
			if (!RowPin)
			{
				Out->SetStringField(TEXT("rowNameWarning"),
					TEXT("the node has no RowName pin, so 'rowName' was not applied"));
			}
			else
			{
				// Void API, silent refusal - see add_enum_literal and set_pin_default. A row name the
				// table does not contain is dropped without complaint, leaving the node reading an
				// empty row while this reports the row that was asked for.
				FString Before, After, Err;
				bool bChanged = false;
				if (SetPinDefaultChecked(RowPin, RowName, Before, After, bChanged, Err))
				{
					Out->SetStringField(TEXT("rowNameApplied"), After);
				}
				else
				{
					Out->SetStringField(TEXT("rowNameApplied"), After);
					Out->SetStringField(TEXT("rowNameError"), FString::Printf(
						TEXT("row '%s' was NOT accepted (%s); the pin is still '%s'. read_datatable lists "
							 "the rows this table actually has."),
						*RowName, *Err, *After));
				}
			}
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}


	// --- set_node_state ------------------------------------------------------
	//   in:  { node, enabled: enabled|disabled|developmentOnly, comment, commentBubble }
	//   out: { node, enabledBefore, enabledAfter, comment, ... }
	//
	// WHAT THIS IS. The editor's right-click "Disable" on a node, plus the per-node comment that
	// shows above it. Both are things a person does constantly while debugging a graph and neither
	// had any bridge equivalent - an agent could ADD and DELETE nodes but not turn one off, so the
	// only way to bisect a misbehaving graph was to destroy nodes and rebuild them.
	//
	// DISABLED IS NOT DELETED, AND THAT IS THE POINT. A disabled node keeps its pins and its
	// connections; the compiler skips it and the links survive, so re-enabling restores the graph
	// exactly. Deleting and re-adding does not: BreakPinLinks cascades, and this plugin has a
	// postmortem about that.
	//
	// DEVELOPMENT-ONLY IS A THIRD STATE, not a synonym for enabled. ENodeEnabledState::DevelopmentOnly
	// compiles in editor/PIE builds and is stripped from a shipping cook - so a print-string an agent
	// leaves behind is harmless in the packaged game only if it is marked this way, and reporting
	// the state back is how a caller can check that rather than hope.
	void H_set_node_state(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("node"), TEXT("enabled"), TEXT("state"), TEXT("comment"),
			  TEXT("commentBubble") },
			TEXT("node (guid or objectPath); enabled (alias state): enabled | disabled | "
				 "developmentOnly; comment (the note shown on the node); commentBubble (bool, "
				 "whether that note is pinned open)"),
			{ { TEXT("text"), TEXT("that is add_comment's key - this sets the comment ON an existing node, not a comment BOX") },
			  { TEXT("enable"), TEXT("spell it `enabled`, and it takes a STATE not a bool - developmentOnly is a third value a bool cannot express") } }))
		{
			return;
		}

		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node)
		{
			return;   // ResolveNodeField has already said what it could not resolve
		}
		UEdGraph* Graph = Node->GetGraph();
		UBlueprint* Blueprint = Graph ? Cast<UBlueprint>(Graph->GetOuter()) : nullptr;

		const TCHAR* const StateNames[] = { TEXT("disabled"), TEXT("enabled"), TEXT("developmentOnly") };
		auto NameOf = [&StateNames](ENodeEnabledState S) -> FString
		{
			const int32 Idx = static_cast<int32>(S);
			return (Idx >= 0 && Idx < 3) ? FString(StateNames[Idx]) : FString(TEXT("unknown"));
		};

		const ENodeEnabledState Before = Node->GetDesiredEnabledState();
		Out->SetStringField(TEXT("enabledBefore"), NameOf(Before));
		Out->SetStringField(TEXT("commentBefore"), Node->NodeComment);

		const bool bWantsState = In->HasField(TEXT("enabled")) || In->HasField(TEXT("state"));
		const bool bWantsComment = In->HasField(TEXT("comment")) || In->HasField(TEXT("commentBubble"));
		if (!bWantsState && !bWantsComment)
		{
			Fail(Out, TEXT("nothing to change - pass enabled, comment or commentBubble. NOTHING was changed."));
			return;
		}

		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifSetNodeState", "Set Node State"));
		Node->Modify();
		if (Blueprint)
		{
			Blueprint->Modify();
		}

		if (bWantsState)
		{
			const FString Want = JStrAny(In, { TEXT("enabled"), TEXT("state") }).ToLower();
			ENodeEnabledState NewState = ENodeEnabledState::Enabled;
			if (Want == TEXT("enabled"))                     { NewState = ENodeEnabledState::Enabled; }
			else if (Want == TEXT("disabled"))               { NewState = ENodeEnabledState::Disabled; }
			else if (Want == TEXT("developmentonly"))        { NewState = ENodeEnabledState::DevelopmentOnly; }
			else
			{
				// Transaction unwinds on scope exit, so nothing above has to be undone by hand.
				Fail(Out, FString::Printf(
					TEXT("unknown enabled state '%s' - use enabled, disabled or developmentOnly. "
						 "NOTHING was changed."), *Want));
				return;
			}
			// SetEnabledState(bUserAction=true) is what the editor's own menu calls. Passing false
			// marks it as a compiler-driven change, which the editor then feels free to revert on
			// the next compile - the write appears to work and silently comes back.
			Node->SetEnabledState(NewState, /*bUserAction*/ true);
		}

		if (In->HasField(TEXT("comment")))
		{
			Node->NodeComment = JStr(In, TEXT("comment"));
		}
		if (In->HasField(TEXT("commentBubble")))
		{
			const bool bPinned = JBool(In, TEXT("commentBubble"), false);
			Node->bCommentBubblePinned = bPinned;
			Node->bCommentBubbleVisible = bPinned;
		}

		if (Blueprint)
		{
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		}

		// READ BACK off the node, so this reports what it IS. SetEnabledState can be a no-op on a
		// node type that refuses the state, and a caller who trusts the request would never know.
		const ENodeEnabledState After = Node->GetDesiredEnabledState();
		Out->SetStringField(TEXT("node"), Node->NodeGuid.ToString());
		Out->SetStringField(TEXT("enabledAfter"), NameOf(After));
		Out->SetBoolField(TEXT("enabledChanged"), Before != After);
		Out->SetStringField(TEXT("comment"), Node->NodeComment);
		Out->SetBoolField(TEXT("commentBubblePinned"), Node->bCommentBubblePinned);
		Out->SetStringField(TEXT("nodeTitle"), Node->GetNodeTitle(ENodeTitleType::ListView).ToString());
		if (After == ENodeEnabledState::Disabled)
		{
			Out->SetStringField(TEXT("disabledNote"),
				TEXT("DISABLED, not deleted - the node keeps its pins and every connection, the "
					 "compiler skips it, and setting enabled again restores the graph exactly. "
					 "Deleting and re-adding does not: breaking a pin link cascades."));
		}
		else if (After == ENodeEnabledState::DevelopmentOnly)
		{
			Out->SetStringField(TEXT("developmentNote"),
				TEXT("compiled in editor and PIE, STRIPPED from a shipping cook. This is how a "
					 "debug print is left in a graph without shipping it."));
		}
	}

	// --- add_comment --------------------------------------------------------

	void H_add_comment(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("x"), TEXT("y"), TEXT("width"), TEXT("height"), TEXT("text") },
			TEXT("graphId, x, y, width (default 400, min 32), height (default 150, min 32), text (the comment body)"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("comment"), TEXT("spell it text") },
			  { TEXT("nodeComment"), TEXT("spell it text") },
			  { TEXT("color"), TEXT("not supported - the box takes the editor's default comment colour") } }))
		{
			return;
		}

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
