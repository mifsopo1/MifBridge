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
#include "K2Node_EditablePinBase.h"   // RemoveUserDefinedPinByName / UserDefinedPins (remove_pin)
#include "K2Node_FunctionResult.h"    // sibling Return-node signature sync (remove_pin)
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

		// Point a variable get/set node at a property on ANOTHER class.
		//
		// SetExternalMember(Name, Class) alone is not enough to guarantee a resolved node:
		// UK2Node_Variable::CreatePinForVariable bails and produces NO pins when
		// FMemberReference::ResolveMember<FProperty> comes back null, which is exactly what happens
		// when the property does not exist on the class handed in. That is how the bridge used to emit
		// the "unresolved, pinless" node — the failure was silent, deferred to compile time, and the
		// returned JSON looked plausible. Two fixes: (1) resolve against the SKELETON class, which is
		// the one carrying freshly-added Blueprint variables before a full compile; (2) verify the
		// property up front and refuse rather than emit a dead node.
		//
		// The FGuid overload of SetExternalMember makes the reference survive a later rename of the
		// property on the target Blueprint (name-only references silently break).
		bool PointAtExternalMember(FMemberReference& Reference, const FString& VarName, UClass* TargetClass, FString& OutError)
		{
			if (!TargetClass)
			{
				OutError = TEXT("null target class");
				return false;
			}

			// Prefer the skeleton class: it is regenerated on every structural change, so a variable
			// added moments ago exists there even though GeneratedClass is still stale.
			UClass* ResolveAgainst = TargetClass;
			if (UBlueprint* TargetBP = Cast<UBlueprint>(TargetClass->ClassGeneratedBy))
			{
				if (TargetBP->SkeletonGeneratedClass)
				{
					ResolveAgainst = TargetBP->SkeletonGeneratedClass;
				}
			}

			const FName MemberName(*VarName);
			FProperty* Property = ResolveAgainst->FindPropertyByName(MemberName);
			if (!Property)
			{
				// Fall back to the display-name lookup the editor uses for renamed/redirected variables.
				Property = FindFProperty<FProperty>(ResolveAgainst, MemberName);
			}
			if (!Property)
			{
				OutError = FString::Printf(
					TEXT("property '%s' not found on class '%s' — describe_class {className:\"%s\"} lists what it has. ")
					TEXT("(Without this check the node would be created unresolved and pinless.)"),
					*VarName, *ResolveAgainst->GetName(), *ResolveAgainst->GetName());
				return false;
			}
			if (!Property->HasAnyPropertyFlags(CPF_BlueprintVisible))
			{
				OutError = FString::Printf(
					TEXT("property '%s' on '%s' is not BlueprintVisible, so a Blueprint graph cannot read it"),
					*VarName, *ResolveAgainst->GetName());
				return false;
			}

			FGuid MemberGuid;
			if (UBlueprint::GetGuidFromClassByFieldName<FProperty>(ResolveAgainst, MemberName, MemberGuid) && MemberGuid.IsValid())
			{
				Reference.SetExternalMember(MemberName, ResolveAgainst, MemberGuid);
			}
			else
			{
				// Native properties have no Blueprint GUID — name-only is correct and stable for those.
				Reference.SetExternalMember(MemberName, ResolveAgainst);
			}
			return true;
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

		const FString TargetClassName = JStrAny(In, { TEXT("targetClass"), TEXT("class"), TEXT("ownerClass") });
		UK2Node_VariableGet* Node = NewObject<UK2Node_VariableGet>(Graph);
		if (!TargetClassName.IsEmpty())
		{
			// EXTERNAL target: read a property OFF another object (e.g. a spawned/passed actor's var), not self/local.
			// Gives the node a Target ("self") input pin the caller wires to the object ref.
			UClass* TargetClass = ResolveClass(TargetClassName, Blueprint);
			if (!TargetClass)
			{
				Fail(Out, FString::Printf(TEXT("targetClass not found: '%s' (try the full class path, e.g. /Game/BP/BP_Foo.BP_Foo_C)"), *TargetClassName));
				return;
			}
			FString RefError;
			if (!PointAtExternalMember(Node->VariableReference, Var, TargetClass, RefError))
			{
				Fail(Out, RefError);
				return;
			}
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
		// A variable node with no value pin never resolved. Say so in the response instead of
		// returning a healthy-looking node that only fails at compile time.
		if (Node->Pins.Num() == 0)
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("get node for '%s' resolved to NO pins — the variable reference is dead. Check the name/targetClass, then remove_node and retry."), *Var));
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

		const FString TargetClassName = JStrAny(In, { TEXT("targetClass"), TEXT("class"), TEXT("ownerClass") });
		UK2Node_VariableSet* Node = NewObject<UK2Node_VariableSet>(Graph);
		if (!TargetClassName.IsEmpty())
		{
			// EXTERNAL target: set a property on ANOTHER object (e.g. a spawned actor's exposed var), not a self/local.
			// Points the node at TargetClass's property and gives it a Target ("self") input pin the caller wires to
			// the object reference (e.g. SpawnActor's ReturnValue). Enables the MifModHelper spawn+set pattern
			// (BrandosModHelper's AddMapMarker/AddNewShop/… set props on the spawned BP this way).
			UClass* TargetClass = ResolveClass(TargetClassName, Blueprint);
			if (!TargetClass)
			{
				Fail(Out, FString::Printf(TEXT("targetClass not found: '%s' (try the full class path, e.g. /Game/BP/BP_Foo.BP_Foo_C)"), *TargetClassName));
				return;
			}
			FString RefError;
			if (!PointAtExternalMember(Node->VariableReference, Var, TargetClass, RefError))
			{
				Fail(Out, RefError);
				return;
			}
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
		// Exec-only pins mean the value pin never materialised — the reference is dead (see get).
		if (Node->Pins.Num() == 0)
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("set node for '%s' resolved to NO pins — the variable reference is dead. Check the name/targetClass, then remove_node and retry."), *Var));
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
		// STRICT: an empty/absent class must not fall through to ResolveClass's "self" behaviour.
		// It used to, so passing the wrong key (class / to / castTo / targetType instead of
		// targetClass) produced a cast of the blueprint to ITSELF — which always succeeds, compiles
		// clean, and is nearly invisible. Accept the common spellings; refuse the empty case.
		UClass* TargetClass = ResolveClassStrictField(
			In, { TEXT("targetClass"), TEXT("class"), TEXT("castTo"), TEXT("to"), TEXT("targetType") }, Blueprint, Out);
		if (!TargetClass)
		{
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

	// --- remove_pin ---------------------------------------------------------
	//   in:  { node|nodeGuid, pin, graphId?, direction?: "input"|"output", confirm: true }
	//   out: { removed, pin, kind: "userDefined"|"duplicate", node }
	//
	// Two jobs:
	//  1. Delete a user-defined pin (function input/output, custom-event param, tunnel pin) — the
	//     Details-panel X button. UK2Node_EditablePinBase::RemoveUserDefinedPinByName drops both the
	//     live UEdGraphPin and its FUserPinInfo record; skipping the record would leave the node
	//     "out-of-date" at compile because reconstruct re-derives pins FROM that record.
	//  2. Delete a DUPLICATE pin — two pins sharing a name+direction where only one can be real.
	//     This is the escape hatch for assets already carrying the spurious second "execute" pin that
	//     create_function used to mint (see PlaceAndInit in MifBridgeCommon.cpp). We keep whichever
	//     copy is wired and drop an unwired twin, so removing it can never break existing exec flow.
	//
	// A pin that is neither user-defined nor duplicated is REFUSED: engine-allocated pins are
	// re-created by AllocateDefaultPins on the next reconstruct, so "removing" one is a lie that
	// silently reverts. Say that instead of pretending.
	void H_remove_pin(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_pin requires confirm=true"));
			return;
		}
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node)
		{
			return;
		}
		const FString PinName = JStrAny(In, { TEXT("pin"), TEXT("pinName"), TEXT("name") });
		if (PinName.IsEmpty())
		{
			Fail(Out, TEXT("pin is required (the pin name to remove)"));
			return;
		}

		// Optional direction filter — needed when a node has same-named pins on both sides.
		const FString DirStr = JStr(In, TEXT("direction"));
		const bool bHasDir = !DirStr.IsEmpty();
		const EEdGraphPinDirection WantDir = DirStr.StartsWith(TEXT("out")) ? EGPD_Output : EGPD_Input;

		TArray<UEdGraphPin*> Matches;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin && Pin->PinName.ToString().Equals(PinName, ESearchCase::IgnoreCase)
				&& (!bHasDir || Pin->Direction == WantDir))
			{
				Matches.Add(Pin);
			}
		}
		if (Matches.Num() == 0)
		{
			Fail(Out, FString::Printf(TEXT("pin not found on node: '%s'%s"), *PinName,
				bHasDir ? *FString::Printf(TEXT(" (direction=%s)"), *DirStr) : TEXT("")));
			return;
		}

		UBlueprint* Blueprint = FBlueprintEditorUtils::FindBlueprintForNode(Node);
		UEdGraph* Graph = Cast<UEdGraph>(Node->GetOuter());
		UK2Node_EditablePinBase* Editable = Cast<UK2Node_EditablePinBase>(Node);

		const bool bUserDefined = Editable && Editable->UserDefinedPins.ContainsByPredicate(
			[&PinName](const TSharedPtr<FUserPinInfo>& Info)
			{
				return Info.IsValid() && Info->PinName.ToString().Equals(PinName, ESearchCase::IgnoreCase);
			});

		if (Graph) { Graph->Modify(); }
		Node->Modify();

		FString Kind;
		if (bUserDefined)
		{
			// Break links first so nothing holds a stale pointer, then drop pin + record.
			for (UEdGraphPin* Pin : Matches)
			{
				K2()->BreakPinLinks(*Pin, /*bSendsNodeNotification*/ true);
			}
			Editable->RemoveUserDefinedPinByName(FName(*PinName));

			// A function graph may have SEVERAL Return nodes; they all share one signature, so an
			// output removed from one must be removed from the rest or the graph won't compile.
			int32 SiblingsUpdated = 0;
			if (Graph && Node->IsA<UK2Node_FunctionResult>())
			{
				TArray<UK2Node_FunctionResult*> Results;
				Graph->GetNodesOfClass(Results);
				for (UK2Node_FunctionResult* Sibling : Results)
				{
					if (Sibling && Sibling != Node)
					{
						Sibling->Modify();
						Sibling->RemoveUserDefinedPinByName(FName(*PinName));
						Sibling->ReconstructNode();
						++SiblingsUpdated;
					}
				}
			}
			Editable->ReconstructNode();
			Kind = TEXT("userDefined");
			Out->SetNumberField(TEXT("siblingResultNodesUpdated"), SiblingsUpdated);
		}
		else if (Matches.Num() > 1)
		{
			// Duplicate cleanup. Keep a linked copy if there is exactly one; otherwise keep the first.
			UEdGraphPin* Keep = nullptr;
			for (UEdGraphPin* Pin : Matches)
			{
				if (Pin->LinkedTo.Num() > 0) { Keep = Pin; break; }
			}
			if (!Keep) { Keep = Matches[0]; }

			int32 Removed = 0;
			for (UEdGraphPin* Pin : Matches)
			{
				if (Pin == Keep) { continue; }
				K2()->BreakPinLinks(*Pin, /*bSendsNodeNotification*/ false);
				Node->Pins.Remove(Pin);
				Pin->MarkAsGarbage();
				++Removed;
			}
			Kind = TEXT("duplicate");
			Out->SetNumberField(TEXT("duplicatesRemoved"), Removed);
			Out->SetBoolField(TEXT("keptLinkedCopy"), Keep->LinkedTo.Num() > 0);
		}
		else
		{
			Fail(Out, FString::Printf(
				TEXT("pin '%s' on %s is engine-allocated, not user-defined, and is not duplicated — it cannot be removed. ")
				TEXT("AllocateDefaultPins would recreate it on the next reconstruct. Only user-defined pins ")
				TEXT("(function/event/tunnel parameters) and duplicate pins can be deleted."),
				*PinName, *Node->GetClass()->GetName()));
			return;
		}

		MarkStructural(Blueprint);
		Out->SetBoolField(TEXT("removed"), true);
		Out->SetStringField(TEXT("pin"), PinName);
		Out->SetStringField(TEXT("kind"), Kind);
		Out->SetObjectField(TEXT("node"), SerializeNode(Node, /*bIncludePins*/ true));
		UE_LOG(LogMifBridge, Log, TEXT("remove_pin: %s.%s (%s)"), *Node->GetName(), *PinName, *Kind);
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
