// MifBridge — grow a node's variable pin list (add_node_pin).
//
// WHY (2026-08-04): three separate jobs stalled on "this node needs one more pin and the bridge
// cannot add one".
//   * Ramiro's dialogue needed a Switch on Int. A freshly created UK2Node_SwitchInteger exposes
//     ONLY its Default pin — every case pin is added by the editor's "Add pin" button — so the
//     menu had to be rebuilt as an Equal(Integer)+Branch ladder instead.
//   * The BotanistExpansion ModActor's append Sequence is FULL (then_0..then_24). Its own source
//     comment records "add_pin cannot extend a Sequence, so new blocks chain off the LAST block's
//     Print exec pin" — a workaround that exists purely because of this gap.
//   * add_make_array can only set numInputs at CREATION; an existing Make Array could not grow, so
//     a 1-pin array had to be deleted and rebuilt (and rebuilding loses the wired defaults).
//
// ONE endpoint covers all three because the engine already has one contract for it:
//   IK2Node_AddPinInterface::AddInputPin()      (K2Node_AddPinInterface.h:62, CanAddPin() at :70)
//     implemented by K2Node_ExecutionSequence, K2Node_MakeContainer (Array/Map/Set), K2Node_Select,
//     K2Node_CommutativeAssociativeBinaryOperator, K2Node_PromotableOperator, K2Node_DoOnceMultiInput
//   UK2Node_Switch::AddPinToSwitchNode()        (K2Node_Switch.h:75)
//     switches do NOT implement the interface, so they need their own call
// Anything else is refused by name rather than silently doing nothing.

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Engine/Blueprint.h"
#include "K2Node_AddPinInterface.h"
#include "K2Node_Switch.h"
#include "Kismet2/BlueprintEditorUtils.h"

namespace MifBridge
{
	static void EmitPinNames(const TSharedRef<FJsonObject>& Out, const TCHAR* Field, UEdGraphNode* Node)
	{
		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin)
			{
				Arr.Add(MakeShared<FJsonValueString>(Pin->PinName.ToString()));
			}
		}
		Out->SetArrayField(Field, Arr);
	}

	// --- add_node_pin ---------------------------------------------------------
	void H_add_node_pin(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("count") },
			TEXT("graphId, node (aliases: nodeGuid, guid, nodeId), count (how many pins to add, 1-32, default 1)"),
			{ { TEXT("pin"),     TEXT("add_node_pin adds the NEXT pin in the node's own sequence - you cannot name it. To set a value on the new pin use set_pin_default") },
			  { TEXT("pinName"), TEXT("the new pin's name is chosen by the node (then_N, [N], Case_N); read it back from the returned pins[]") },
			  { TEXT("value"),   TEXT("add the pin first, then set_pin_default on the name returned in addedPins[]") },
			  { TEXT("index"),   TEXT("pins are appended in order; there is no insert-at-index") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node)
		{
			return;
		}

		const int32 Count = FMath::Clamp(JInt(In, TEXT("count"), 1), 1, 32);

		// Record the pin set BEFORE, so addedPins[] is measured rather than assumed. A node that
		// silently refuses (CanAddPin false, or a max already reached) must not report success.
		TSet<FName> Before;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin)
			{
				Before.Add(Pin->PinName);
			}
		}

		Blueprint->Modify();
		Graph->Modify();
		Node->Modify();

		int32 Added = 0;
		FString Refusal;
		for (int32 i = 0; i < Count; ++i)
		{
			if (IK2Node_AddPinInterface* AddPin = Cast<IK2Node_AddPinInterface>(Node))
			{
				if (!AddPin->CanAddPin())
				{
					Refusal = TEXT("the node refused another pin (CanAddPin() == false) - it is at its maximum");
					break;
				}
				AddPin->AddInputPin();
				++Added;
			}
			else if (UK2Node_Switch* Switch = Cast<UK2Node_Switch>(Node))
			{
				// Switches are the reason this endpoint exists but are NOT part of the interface.
				Switch->AddPinToSwitchNode();
				++Added;
			}
			else
			{
				Refusal = FString::Printf(
					TEXT("node '%s' (%s) has no variable pin list - it implements neither IK2Node_AddPinInterface ")
					TEXT("nor UK2Node_Switch. Nodes that DO: Sequence, Make Array/Map/Set, Select, Switch, and the ")
					TEXT("commutative maths operators"),
					*Node->GetNodeTitle(ENodeTitleType::ListView).ToString(), *Node->GetClass()->GetName());
				break;
			}
		}

		if (Added == 0)
		{
			Fail(Out, Refusal.IsEmpty() ? TEXT("no pin was added") : Refusal);
			return;
		}

		TArray<TSharedPtr<FJsonValue>> AddedArr;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin && !Before.Contains(Pin->PinName))
			{
				AddedArr.Add(MakeShared<FJsonValueString>(Pin->PinName.ToString()));
			}
		}

		MarkStructural(Blueprint);

		Out->SetNumberField(TEXT("added"), Added);
		Out->SetNumberField(TEXT("requested"), Count);
		Out->SetArrayField(TEXT("addedPins"), AddedArr);
		EmitPinNames(Out, TEXT("pins"), Node);
		if (!Refusal.IsEmpty())
		{
			// Partial success is still success, but the caller must be told it did not get all it asked for.
			Out->SetStringField(TEXT("warning"), Refusal);
		}
		if (AddedArr.Num() != Added)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("addedPins[] differs from added - the node renamed existing pins while growing ")
				TEXT("(some nodes renumber). Trust pins[] for the current full list."));
		}
		UE_LOG(LogMifBridge, Log, TEXT("add_node_pin: %s +%d pin(s)"),
			*Node->GetClass()->GetName(), Added);
	}
}
