// MifBridge — Enhanced Input authoring nodes.
//
// add_enhanced_input_action places a UK2Node_EnhancedInputAction (the "IA_Foo" event node you normally get by
// right-clicking a graph and searching for the action asset). It was the one node class the bridge could not
// author, which forced every Enhanced Input binding to be finished by hand in the editor UI.
//
// ⚠️ ORDERING TRAP: the node's pins are generated FROM the action (Triggered/Started/Ongoing/Canceled/Completed
// plus a value pin typed by the action's ValueType), so InputAction MUST be assigned BEFORE AllocateDefaultPins
// runs. PlaceAndInit is what calls AllocateDefaultPins, so the assignment goes above it — exactly the same
// constraint as UK2Node_CustomEvent::CustomFunctionName in MifBridgeNodes2.cpp.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "K2Node_EnhancedInputAction.h"   // InputBlueprintNodes module
#include "InputAction.h"                  // EnhancedInput module — UInputAction
#include "UObject/UObjectGlobals.h"       // LoadObject

namespace MifBridge
{
	void H_add_enhanced_input_action(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("inputAction"), TEXT("action"), TEXT("actionPath"), TEXT("x"), TEXT("y") },
			TEXT("graphId, inputAction (aliases: action, actionPath) - the UInputAction asset path, x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("blueprintId"), TEXT("this endpoint places a node in a GRAPH - pass graphId (list_graphs shows every graph by its full dotted path)") },
			  { TEXT("inputActionPath"), TEXT("spell it inputAction (aliases: action, actionPath)") },
			  { TEXT("class"), TEXT("pass the UInputAction ASSET path as inputAction, not a class") },
			  { TEXT("trigger"), TEXT("the Triggered/Started/Ongoing/Canceled/Completed exec pins are generated from the action - place the node, then connect_pins") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}

		const FString ActionPath = JStrAny(In, { TEXT("inputAction"), TEXT("action"), TEXT("actionPath") });
		if (ActionPath.IsEmpty())
		{
			// Named explicitly rather than defaulted: an empty value would otherwise produce a node with no
			// pins at all, which looks like a successful call and fails silently at connect time.
			Fail(Out, TEXT("'inputAction' is required and must name a UInputAction asset ")
			          TEXT("(e.g. /Game/MODS/DriveableScooter/IA_MifHandbrake)"));
			return;
		}

		FString Path = ActionPath;
		Path.TrimStartAndEndInline();

		UInputAction* Action = LoadObject<UInputAction>(nullptr, *Path);
		if (!Action)
		{
			// Accept the bare package path too: /Game/X/IA_Foo -> /Game/X/IA_Foo.IA_Foo
			FString Name;
			if (Path.Split(TEXT("/"), nullptr, &Name, ESearchCase::CaseSensitive, ESearchDir::FromEnd)
				&& !Name.Contains(TEXT(".")))
			{
				Action = LoadObject<UInputAction>(nullptr, *(Path + TEXT(".") + Name));
			}
		}
		if (!Action)
		{
			Fail(Out, FString::Printf(
				TEXT("InputAction not found: '%s'. Pass the object path (/Game/Path/IA_Foo.IA_Foo) or the ")
				TEXT("package path (/Game/Path/IA_Foo)."), *ActionPath));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_EnhancedInputAction* Node = NewObject<UK2Node_EnhancedInputAction>(Graph);
		Node->InputAction = Action;   // MUST precede AllocateDefaultPins — see the ordering trap above
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
		Out->SetStringField(TEXT("inputAction"), Action->GetPathName());
	}
}
