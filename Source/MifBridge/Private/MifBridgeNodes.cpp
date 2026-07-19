// MifBridge — node creation, pin wiring, and batch endpoints (the graph-edit core).
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Engine/MemberReference.h"
#include "HAL/FileManager.h"
#include "K2Node_CallFunction.h"
#include "K2Node_CallParentFunction.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_Event.h"
#include "K2Node_GetArrayItem.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_Knot.h"
#include "K2Node_MacroInstance.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "ScopedTransaction.h"

namespace MifBridge
{
	namespace
	{
		bool BlueprintHasVariable(UBlueprint* Blueprint, const FString& Name)
		{
			for (const FBPVariableDescription& Var : Blueprint->NewVariables)
			{
				if (Var.VarName.ToString() == Name)
				{
					return true;
				}
			}
			const FName VarName(*Name);
			if (Blueprint->SkeletonGeneratedClass && Blueprint->SkeletonGeneratedClass->FindPropertyByName(VarName))
			{
				return true;
			}
			if (Blueprint->ParentClass && Blueprint->ParentClass->FindPropertyByName(VarName))
			{
				return true;
			}
			return false;
		}

		// Shared connect/reconnect body. When bBreakFirst is true both pins are cleared
		// before wiring (the wildcard-reset combo). Reports CanCreateConnection's reason.
		void DoConnect(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out, bool bBreakFirst)
		{
			UEdGraphNode* SrcNode = ResolveNodeField(In, TEXT("srcNode"), Out);
			if (!SrcNode)
			{
				return;
			}
			UEdGraphNode* DstNode = ResolveNodeField(In, TEXT("dstNode"), Out);
			if (!DstNode)
			{
				return;
			}

			UEdGraphPin* OutPin = FindPin(SrcNode, JStr(In, TEXT("srcPin")), EGPD_Output, /*bRequireDir*/ false);
			UEdGraphPin* InPin = FindPin(DstNode, JStr(In, TEXT("dstPin")), EGPD_Input, /*bRequireDir*/ false);
			if (!OutPin)
			{
				Fail(Out, FString::Printf(TEXT("src pin not found: '%s'"), *JStr(In, TEXT("srcPin"))));
				return;
			}
			if (!InPin)
			{
				Fail(Out, FString::Printf(TEXT("dst pin not found: '%s'"), *JStr(In, TEXT("dstPin"))));
				return;
			}

			// Tunnel through reroute (knot) chains to the real terminal pins.
			OutPin = SkipKnots(OutPin);
			InPin = SkipKnots(InPin);

			const UEdGraphSchema_K2* Schema = K2();
			UEdGraphNode* OutOwner = OutPin->GetOwningNodeUnchecked();
			UEdGraphNode* InOwner = InPin->GetOwningNodeUnchecked();
			if (!OutOwner || !InOwner)
			{
				Fail(Out, TEXT("resolved pin has no owning node (orphaned knot chain?)"));
				return;
			}
			OutOwner->Modify();
			InOwner->Modify();

			if (bBreakFirst)
			{
				Schema->BreakPinLinks(*OutPin, true);
				Schema->BreakPinLinks(*InPin, true);
			}

			const FPinConnectionResponse Response = Schema->CanCreateConnection(OutPin, InPin);
			if (Response.Response == CONNECT_RESPONSE_DISALLOW)
			{
				Fail(Out, Response.Message.ToString());
				return;
			}

			const bool bConnected = Schema->TryCreateConnection(OutPin, InPin);
			MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(OutOwner));

			Out->SetBoolField(TEXT("connected"), bConnected);
			if (!Response.Message.IsEmpty())
			{
				Out->SetStringField(TEXT("response"), Response.Message.ToString());
			}
			Out->SetObjectField(TEXT("srcPin"), SerializePin(OutPin));
			Out->SetObjectField(TEXT("dstPin"), SerializePin(InPin));
		}
	}

	// --- Node creation ------------------------------------------------------

	void H_add_function_call(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}

		const FString ClassName = JStr(In, TEXT("class"), TEXT("self"));
		const FString FunctionName = JStr(In, TEXT("function"));
		if (FunctionName.IsEmpty())
		{
			Fail(Out, TEXT("function is required"));
			return;
		}

		UClass* TargetClass = ResolveClass(ClassName, Blueprint);
		if (!TargetClass)
		{
			Fail(Out, FString::Printf(TEXT("class not found: '%s'"), *ClassName));
			return;
		}
		UFunction* Function = TargetClass->FindFunctionByName(FName(*FunctionName));
		if (!Function)
		{
			Fail(Out, FString::Printf(TEXT("function '%s' not found on class '%s'"), *FunctionName, *TargetClass->GetName()));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_CallFunction* Node = NewObject<UK2Node_CallFunction>(Graph);
		Node->SetFromFunction(Function); // derives purity, self/target, param pins, containers
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_variable_get(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString Var = JStr(In, TEXT("var"));
		if (Var.IsEmpty())
		{
			Fail(Out, TEXT("var is required"));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		const FString TargetClassName = JStr(In, TEXT("targetClass"));
		UK2Node_VariableGet* Node = NewObject<UK2Node_VariableGet>(Graph);
		if (!TargetClassName.IsEmpty())
		{
			// EXTERNAL target: read a property OFF another object (e.g. a spawned/passed actor's var), not self/local.
			// SetExternalMember + a Target ("self") input pin the caller wires to the object ref. Mirrors add_variable_set.
			UClass* TargetClass = ResolveClass(TargetClassName, Blueprint);
			if (!TargetClass)
			{
				Fail(Out, FString::Printf(TEXT("targetClass not found: '%s'"), *TargetClassName));
				return;
			}
			Node->VariableReference.SetExternalMember(FName(*Var), TargetClass);
		}
		else
		{
			// Auto-detect scope: a variable DECLARED on this function graph is a LOCAL and must resolve via SetLocalMember
			// (SetSelfMember would search the class for a member of that name → "Could not find a variable named X" and an
			// unresolved node). A member/instance variable falls through to SetSelfMember. No scope param needed.
			const FGuid LocalGuid = FBlueprintEditorUtils::FindLocalVariableGuidByName(Blueprint, Graph, FName(*Var));
			if (LocalGuid.IsValid()) { Node->VariableReference.SetLocalMember(FName(*Var), Graph->GetName(), LocalGuid); }
			else                     { Node->VariableReference.SetSelfMember(FName(*Var)); }
		}
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		if (TargetClassName.IsEmpty() && !BlueprintHasVariable(Blueprint, Var))
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(TEXT("variable '%s' not found on this blueprint; the get node may be unresolved until it exists"), *Var));
		}
		EmitNode(Out, Node);
	}

	void H_add_variable_set(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString Var = JStr(In, TEXT("var"));
		if (Var.IsEmpty())
		{
			Fail(Out, TEXT("var is required"));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		const FString TargetClassName = JStr(In, TEXT("targetClass"));
		UK2Node_VariableSet* Node = NewObject<UK2Node_VariableSet>(Graph);
		if (!TargetClassName.IsEmpty())
		{
			// EXTERNAL target: set a property on ANOTHER object (e.g. a spawned actor's exposed var), not a self/local.
			// SetExternalMember points the node at TargetClass's property and gives it a Target ("self") input pin the
			// caller wires to the object reference (e.g. SpawnActor's ReturnValue). Enables the MifModHelper spawn+set
			// pattern (BrandosModHelper's AddMapMarker/AddNewShop/… set props on the spawned BP this way).
			UClass* TargetClass = ResolveClass(TargetClassName, Blueprint);
			if (!TargetClass)
			{
				Fail(Out, FString::Printf(TEXT("targetClass not found: '%s'"), *TargetClassName));
				return;
			}
			Node->VariableReference.SetExternalMember(FName(*Var), TargetClass);
		}
		else
		{
			// Auto-detect scope: a variable DECLARED on this function graph is a LOCAL and must resolve via SetLocalMember
			// (SetSelfMember would search the class for a member of that name → "Could not find a variable named X" and an
			// unresolved node). A member/instance variable falls through to SetSelfMember. No scope param needed.
			const FGuid LocalGuid = FBlueprintEditorUtils::FindLocalVariableGuidByName(Blueprint, Graph, FName(*Var));
			if (LocalGuid.IsValid()) { Node->VariableReference.SetLocalMember(FName(*Var), Graph->GetName(), LocalGuid); }
			else                     { Node->VariableReference.SetSelfMember(FName(*Var)); }
		}
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		if (TargetClassName.IsEmpty() && !BlueprintHasVariable(Blueprint, Var))
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(TEXT("variable '%s' not found on this blueprint; the set node may be unresolved until it exists"), *Var));
		}
		EmitNode(Out, Node);
	}

	void H_add_branch(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_IfThenElse* Node = NewObject<UK2Node_IfThenElse>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_macro_instance(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}

		const FString MacroPath = JStr(In, TEXT("macroPath"), TEXT("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros"));
		const FString MacroName = JStr(In, TEXT("macroGraph"));
		if (MacroName.IsEmpty())
		{
			Fail(Out, TEXT("macroGraph is required (e.g. 'ForEachLoop')"));
			return;
		}

		UObject* MacroObject = StaticLoadObject(UBlueprint::StaticClass(), nullptr, *MacroPath, nullptr, LOAD_NoWarn);
		UBlueprint* MacroLibrary = Cast<UBlueprint>(MacroObject);
		if (!MacroLibrary)
		{
			Fail(Out, FString::Printf(TEXT("macro library not found: %s"), *MacroPath));
			return;
		}

		UEdGraph* MacroGraph = nullptr;
		for (UEdGraph* Candidate : MacroLibrary->MacroGraphs)
		{
			if (Candidate && Candidate->GetName() == MacroName)
			{
				MacroGraph = Candidate;
				break;
			}
		}
		if (!MacroGraph)
		{
			Fail(Out, FString::Printf(TEXT("macro graph '%s' not found in %s"), *MacroName, *MacroPath));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		// Spawn fresh + AllocateDefaultPins — never paste. This is the fix for the
		// ForEachLoop wildcard that stayed 'undetermined' via the clipboard path.
		UK2Node_MacroInstance* Node = NewObject<UK2Node_MacroInstance>(Graph);
		Node->SetMacroGraph(MacroGraph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_get_array_item(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_GetArrayItem* Node = NewObject<UK2Node_GetArrayItem>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);

		// Surface the real (quirky) pin names so callers use array/index/out semantics.
		if (UEdGraphPin* ArrayPin = Node->GetTargetArrayPin())
		{
			Out->SetStringField(TEXT("arrayPin"), ArrayPin->PinName.ToString());
		}
		if (Node->Pins.IsValidIndex(1))
		{
			if (UEdGraphPin* IndexPin = Node->GetIndexPin())
			{
				Out->SetStringField(TEXT("indexPin"), IndexPin->PinName.ToString());
			}
		}
		if (Node->Pins.IsValidIndex(2))
		{
			if (UEdGraphPin* ResultPin = Node->GetResultPin())
			{
				Out->SetStringField(TEXT("outPin"), ResultPin->PinName.ToString());
			}
		}
		EmitNode(Out, Node);
	}

	void H_add_override_event(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}

		UEdGraph* EventGraph = FBlueprintEditorUtils::FindEventGraph(Blueprint);
		if (!EventGraph && Blueprint->UbergraphPages.Num() > 0)
		{
			EventGraph = Blueprint->UbergraphPages[0];
		}
		if (!EventGraph)
		{
			Fail(Out, TEXT("blueprint has no event graph to host the override"));
			return;
		}

		const FString InterfaceOrParent = JStr(In, TEXT("interfaceOrParent"));
		const FString EventName = JStr(In, TEXT("event"));
		if (EventName.IsEmpty())
		{
			Fail(Out, TEXT("event is required"));
			return;
		}

		UClass* HostClass = InterfaceOrParent.IsEmpty() ? Blueprint->ParentClass : ResolveClass(InterfaceOrParent, Blueprint);
		if (!HostClass)
		{
			Fail(Out, FString::Printf(TEXT("interfaceOrParent class not found: '%s'"), *InterfaceOrParent));
			return;
		}
		UFunction* EventFunction = HostClass->FindFunctionByName(FName(*EventName));
		if (!EventFunction)
		{
			Fail(Out, FString::Printf(TEXT("event '%s' not found on '%s'"), *EventName, *HostClass->GetName()));
			return;
		}

		for (UEdGraphNode* Existing : EventGraph->Nodes)
		{
			UK2Node_Event* AsEvent = Cast<UK2Node_Event>(Existing);
			if (AsEvent && AsEvent->EventReference.GetMemberName() == FName(*EventName))
			{
				Fail(Out, FString::Printf(TEXT("event '%s' is already present in the graph"), *EventName));
				return;
			}
		}

		const int32 X = JInt(In, TEXT("x"));
		const int32 Y = JInt(In, TEXT("y"));

		Blueprint->Modify();
		EventGraph->Modify();

		UK2Node_Event* Node = NewObject<UK2Node_Event>(EventGraph);
		Node->EventReference.SetExternalMember(FName(*EventName), HostClass);
		Node->bOverrideFunction = true;
		PlaceAndInit(EventGraph, Node, X, Y);

		MarkStructural(Blueprint);
		EmitNode(Out, Node);

		if (JBool(In, TEXT("callParent"), false))
		{
			UK2Node_CallParentFunction* ParentNode = NewObject<UK2Node_CallParentFunction>(EventGraph);
			ParentNode->SetFromFunction(EventFunction);
			PlaceAndInit(EventGraph, ParentNode, X + 320, Y);

			UEdGraphPin* ThenPin = FindPin(Node, TEXT("then"), EGPD_Output, /*bRequireDir*/ true);
			UEdGraphPin* ParentExec = FindPin(ParentNode, TEXT("execute"), EGPD_Input, /*bRequireDir*/ true);
			if (ThenPin && ParentExec)
			{
				K2()->TryCreateConnection(ThenPin, ParentExec);
			}
			MarkStructural(Blueprint);

			Out->SetStringField(TEXT("parentNodeGuid"), ParentNode->NodeGuid.ToString());
			Out->SetObjectField(TEXT("parentNode"), SerializeNode(ParentNode, /*bIncludePins*/ true));
		}
	}

	void H_add_parent_call(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}

		const FString ParentName = JStr(In, TEXT("parentClass"));
		const FString FunctionName = JStr(In, TEXT("function"));
		if (FunctionName.IsEmpty())
		{
			Fail(Out, TEXT("function is required"));
			return;
		}

		UClass* ParentClass = ParentName.IsEmpty() ? Blueprint->ParentClass : ResolveClass(ParentName, Blueprint);
		if (!ParentClass)
		{
			Fail(Out, FString::Printf(TEXT("parent class not found: '%s'"), *ParentName));
			return;
		}
		UFunction* Function = ParentClass->FindFunctionByName(FName(*FunctionName));
		if (!Function)
		{
			Fail(Out, FString::Printf(TEXT("function '%s' not found on parent '%s'"), *FunctionName, *ParentClass->GetName()));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_CallParentFunction* Node = NewObject<UK2Node_CallParentFunction>(Graph);
		Node->SetFromFunction(Function);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_cast(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString TargetName = JStr(In, TEXT("targetClass"));
		UClass* TargetClass = ResolveClass(TargetName, Blueprint);
		if (!TargetClass)
		{
			Fail(Out, FString::Printf(TEXT("target class not found: '%s'"), *TargetName));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_DynamicCast* Node = NewObject<UK2Node_DynamicCast>(Graph);
		Node->TargetType = TargetClass;
		Node->SetPurity(false); // impure cast: exposes exec then / Cast Failed pins
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_move_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
		if (!Node)
		{
			return;
		}
		if (UEdGraph* Graph = Cast<UEdGraph>(Node->GetOuter()))
		{
			Graph->Modify();
		}
		Node->Modify();
		Node->NodePosX = JInt(In, TEXT("x"), Node->NodePosX);
		Node->NodePosY = JInt(In, TEXT("y"), Node->NodePosY);
		Out->SetObjectField(TEXT("node"), SerializeNode(Node, /*bIncludePins*/ false));
	}

	void H_remove_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_node requires confirm=true"));
			return;
		}
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
		if (!Node)
		{
			return;
		}
		const FString Guid = Node->NodeGuid.ToString();
		UBlueprint* Blueprint = FBlueprintEditorUtils::FindBlueprintForNode(Node);
		if (Blueprint)
		{
			Blueprint->Modify();
			FBlueprintEditorUtils::RemoveNode(Blueprint, Node, /*bDontRecompile*/ true);
			MarkStructural(Blueprint);
		}
		else if (UEdGraph* Graph = Cast<UEdGraph>(Node->GetOuter()))
		{
			Graph->Modify();
			Graph->RemoveNode(Node);
		}
		Out->SetStringField(TEXT("removed"), Guid);
	}

	void H_refresh_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
		if (!Node)
		{
			return;
		}
		Node->Modify();
		Node->ReconstructNode();
		MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(Node));
		Out->SetObjectField(TEXT("node"), SerializeNode(Node, /*bIncludePins*/ true));
	}

	// --- Pins / wiring ------------------------------------------------------

	void H_connect_pins(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		DoConnect(In, Out, /*bBreakFirst*/ false);
	}

	void H_reconnect_pin(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		DoConnect(In, Out, /*bBreakFirst*/ true);
	}

	void H_disconnect_pin(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node)
		{
			return;
		}
		const FString PinName = JStr(In, TEXT("pin"));
		UEdGraphPin* Pin = FindPin(Node, PinName, EGPD_Input, /*bRequireDir*/ false);
		if (!Pin)
		{
			Fail(Out, FString::Printf(TEXT("pin not found: '%s'"), *PinName));
			return;
		}
		Node->Modify();
		K2()->BreakPinLinks(*Pin, /*bSendsNodeNotification*/ true);
		MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(Node));
		Out->SetObjectField(TEXT("pin"), SerializePin(Pin));
	}

	void H_set_pin_default(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node)
		{
			return;
		}
		const FString PinName = JStr(In, TEXT("pin"));
		const FString Value = JStr(In, TEXT("value"));
		UEdGraphPin* Pin = FindPin(Node, PinName, EGPD_Input, /*bRequireDir*/ false);
		if (!Pin)
		{
			Fail(Out, FString::Printf(TEXT("pin not found: '%s'"), *PinName));
			return;
		}
		Node->Modify();
		K2()->TrySetDefaultValue(*Pin, Value);
		Out->SetObjectField(TEXT("pin"), SerializePin(Pin));
	}

	void H_splice_into_exec(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UEdGraphNode* AfterNode = ResolveNodeField(In, TEXT("afterNode"), Out);
		if (!AfterNode)
		{
			return;
		}
		UEdGraphNode* InsertNode = ResolveNodeField(In, TEXT("insertNode"), Out);
		if (!InsertNode)
		{
			return;
		}

		UEdGraphPin* AfterOut = FindPin(AfterNode, JStr(In, TEXT("afterPin"), TEXT("then")), EGPD_Output, /*bRequireDir*/ true);
		UEdGraphPin* InsertIn = FindPin(InsertNode, JStr(In, TEXT("insertExecIn"), TEXT("execute")), EGPD_Input, /*bRequireDir*/ true);
		UEdGraphPin* InsertOut = FindPin(InsertNode, JStr(In, TEXT("insertExecOut"), TEXT("then")), EGPD_Output, /*bRequireDir*/ true);
		if (!AfterOut)
		{
			Fail(Out, FString::Printf(TEXT("afterPin (exec out) not found: '%s'"), *JStr(In, TEXT("afterPin"), TEXT("then"))));
			return;
		}
		if (!InsertIn)
		{
			Fail(Out, FString::Printf(TEXT("insertExecIn not found: '%s'"), *JStr(In, TEXT("insertExecIn"), TEXT("execute"))));
			return;
		}
		if (!InsertOut)
		{
			Fail(Out, FString::Printf(TEXT("insertExecOut not found: '%s'"), *JStr(In, TEXT("insertExecOut"), TEXT("then"))));
			return;
		}

		// Capture the current downstream target(s) before breaking the link.
		TArray<UEdGraphPin*> OldTargets = AfterOut->LinkedTo;

		const UEdGraphSchema_K2* Schema = K2();
		AfterNode->Modify();
		InsertNode->Modify();

		Schema->BreakPinLinks(*AfterOut, true);
		Schema->TryCreateConnection(AfterOut, InsertIn);
		for (UEdGraphPin* Target : OldTargets)
		{
			if (Target)
			{
				if (UEdGraphNode* Owner = Target->GetOwningNodeUnchecked())
				{
					Owner->Modify();
				}
				Schema->TryCreateConnection(InsertOut, Target);
			}
		}

		MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(AfterNode));
		Out->SetNumberField(TEXT("reconnectedTargets"), OldTargets.Num());
		Out->SetObjectField(TEXT("afterPin"), SerializePin(AfterOut));
	}

	// --- Batch --------------------------------------------------------------

	void H_batch(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const TArray<TSharedPtr<FJsonValue>>* Ops = nullptr;
		if (!In->TryGetArrayField(TEXT("ops"), Ops) || Ops == nullptr)
		{
			Fail(Out, TEXT("batch requires an 'ops' array"));
			return;
		}

		// Optional backup of the top-level blueprintId before mutating.
		const FString TopBlueprintId = JStr(In, TEXT("blueprintId"));
		if (JBool(In, TEXT("backup"), false) && !TopBlueprintId.IsEmpty())
		{
			FString ResolveError;
			if (UBlueprint* BackupBP = ResolveBlueprint(TopBlueprintId, ResolveError))
			{
				UPackage* Package = BackupBP->GetOutermost();
				const FString FileName = FPackageName::LongPackageNameToFilename(Package->GetName(), FPackageName::GetAssetPackageExtension());
				if (FPaths::FileExists(FileName))
				{
					IFileManager::Get().Copy(*(FileName + TEXT(".bak")), *FileName, true, true);
					Out->SetStringField(TEXT("backup"), FileName + TEXT(".bak"));
				}
			}
		}

		TArray<TSharedPtr<FJsonValue>> Results;
		TSet<UBlueprint*> Touched;
		bool bAllOk = true;

		const TMap<FString, FHandlerFn>& Registry = Handlers();

		// All op mutations are captured in ONE transaction (one Ctrl-Z). It closes BEFORE
		// the compileAtEnd step so reinstancing is never captured as an undo step. Ops that
		// themselves compile (create_function, recipe_add_debug_print, nested batch) are
		// disallowed here — call them standalone.
		{
			FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "Batch", "Mif Bridge: batch"));

			for (const TSharedPtr<FJsonValue>& OpValue : *Ops)
			{
				const TSharedPtr<FJsonObject>* OpObjectPtr = nullptr;
				if (!OpValue.IsValid() || !OpValue->TryGetObject(OpObjectPtr) || OpObjectPtr == nullptr)
				{
					continue;
				}
				const TSharedRef<FJsonObject> OpIn = OpObjectPtr->ToSharedRef();
				const FString OpName = JStr(OpIn, TEXT("op"));

				TSharedRef<FJsonObject> OpOut = MakeShared<FJsonObject>();
				OpOut->SetBoolField(TEXT("ok"), true);
				OpOut->SetStringField(TEXT("op"), OpName);

				if (OpName == TEXT("batch") || OpName == TEXT("create_function") ||
					OpName == TEXT("recipe_add_debug_print") || OpName == TEXT("add_event_dispatcher"))
				{
					Fail(OpOut, FString::Printf(TEXT("op '%s' is not allowed inside batch (it runs a compile); call it standalone"), *OpName));
				}
				else if (const FHandlerFn* Fn = Registry.Find(OpName))
				{
					(*Fn)(OpIn, OpOut); // runs inside the batch's single transaction
				}
				else
				{
					Fail(OpOut, FString::Printf(TEXT("unknown op: '%s'"), *OpName));
				}

				if (!IsOk(OpOut))
				{
					bAllOk = false;
				}

				// Track which blueprint each op touched so we can compile them once at the end.
				FString ResolveError;
				if (OpIn->HasField(TEXT("graphId")))
				{
					UBlueprint* OpBlueprint = nullptr;
					if (ResolveGraph(JStr(OpIn, TEXT("graphId")), OpBlueprint, ResolveError) && OpBlueprint)
					{
						Touched.Add(OpBlueprint);
					}
				}
				else if (OpIn->HasField(TEXT("blueprintId")))
				{
					if (UBlueprint* OpBlueprint = ResolveBlueprint(JStr(OpIn, TEXT("blueprintId")), ResolveError))
					{
						Touched.Add(OpBlueprint);
					}
				}

				Results.Add(MakeShared<FJsonValueObject>(OpOut));
			}
		}

		Out->SetBoolField(TEXT("ok"), bAllOk);
		Out->SetNumberField(TEXT("opCount"), Results.Num());
		Out->SetArrayField(TEXT("results"), Results);

		if (JBool(In, TEXT("compileAtEnd"), true))
		{
			if (!TopBlueprintId.IsEmpty())
			{
				FString ResolveError;
				if (UBlueprint* TopBP = ResolveBlueprint(TopBlueprintId, ResolveError))
				{
					Touched.Add(TopBP);
				}
			}

			TArray<TSharedPtr<FJsonValue>> Compiles;
			for (UBlueprint* Blueprint : Touched)
			{
				TSharedRef<FJsonObject> CompileOut = MakeShared<FJsonObject>();
				CompileBlueprintInto(Blueprint, CompileOut);
				CompileOut->SetStringField(TEXT("blueprintId"), Blueprint->GetPathName());
				if (!IsOk(CompileOut))
				{
					Out->SetBoolField(TEXT("ok"), false);
				}
				Compiles.Add(MakeShared<FJsonValueObject>(CompileOut));
			}
			Out->SetArrayField(TEXT("compile"), Compiles);
		}
	}
}
