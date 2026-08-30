// MifBridge — Phase 3 breadth: event dispatchers (Blueprint multicast delegates).
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "K2Node_AddDelegate.h"
#include "K2Node_CreateDelegate.h"
#include "EdGraphSchema_K2.h"
#include "K2Node_ClearDelegate.h"
#include "K2Node_RemoveDelegate.h"
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
			// ONE guard serves add_call_dispatcher AND add_bind_dispatcher: this shared body is the
			// whole of both handlers and they take an identical shape, so guarding here is the same
			// pattern as DoAddVariableNode (MifBridgeNodes.cpp:369). Do NOT add a second guard in
			// either H_ function — the key list would then have two places to drift apart.
			if (RejectUnknownParams(In, Out,
				{ TEXT("graphId"), TEXT("dispatcher"), TEXT("targetClass"), TEXT("x"), TEXT("y"),
				  TEXT("op") },
				TEXT("graphId, dispatcher, targetClass (optional — bind/call a dispatcher declared on that ")
				TEXT("EXTERNAL class instead of this blueprint's own), x, y, op (bind|unbind|unbindAll ")
				TEXT("on add_bind_dispatcher; add_call_dispatcher is the call spelling)"),
				{ { TEXT("graph"), TEXT("spell it graphId") },
				  { TEXT("name"), TEXT("the existing dispatcher is named by 'dispatcher'; 'name' is add_event_dispatcher's key for CREATING one") },
				  { TEXT("dispatcherName"), TEXT("spell it dispatcher") },
				  { TEXT("blueprintId"), TEXT("graphId already names the blueprint — pass the graph the node lands in") },
				  { TEXT("target"), TEXT("targetClass names the CLASS that declares the dispatcher; the OBJECT goes into the node's Target/self pin via connect_pins, never here") },
				  { TEXT("event"), TEXT("the handler is wired into the bind node's Delegate pin — add_custom_event then connect_pins; this endpoint only places the node") } }))
			{
				return;
			}

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
		// blueprintId/path come from ResolveBlueprintField; 'inputs' is the array ParsePinSpecs reads
		// (its per-entry keys name/type/container/valueType live INSIDE the array, not up here).
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("inputs") },
			TEXT("blueprintId (alias: path), name, inputs (array of {name, type, container?, valueType?} — ")
			TEXT("the dispatcher's signature parameters)"),
			{ { TEXT("dispatcher"), TEXT("'dispatcher' names an EXISTING dispatcher on add_call_dispatcher/add_bind_dispatcher; the one being created here is named by 'name'") },
			  { TEXT("params"), TEXT("spell it inputs (the response reports the count back as 'params')") },
			  { TEXT("parameters"), TEXT("spell it inputs") },
			  { TEXT("outputs"), TEXT("a dispatcher signature has inputs only — they surface as OUTPUT pins on the bound event") },
			  { TEXT("graphId"), TEXT("a dispatcher belongs to the blueprint, not to one graph — pass blueprintId") } }))
		{
			return;
		}

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
		// op is accepted by the shared guard, so it has to be answered here rather than silently
		// ignored - a caller passing op:"unbind" to the CALL endpoint means something specific and
		// deserves to be routed rather than handed a broadcast node.
		const FString Op = JStr(In, TEXT("op"), TEXT("call")).ToLower();
		if (Op != TEXT("call") && Op != TEXT("broadcast"))
		{
			Fail(Out, FString::Printf(
				TEXT("add_call_dispatcher broadcasts; op '%s' belongs to add_bind_dispatcher, which "
					 "takes bind, unbind and unbindAll. NOTHING was added."), *Op));
			return;
		}
		SpawnDelegateNode<UK2Node_CallDelegate>(In, Out);
	}

	// =======================================================================
	// THE TEARDOWN HALF - unbind and unbindAll
	// =======================================================================
	//
	// The dispatcher subsystem was not half missing: declaration (add_event_dispatcher,
	// rename/remove, list_dispatchers), broadcast (add_call_dispatcher) and bind
	// (add_bind_dispatcher, add_component_bound_event) all shipped. What was absent is two of the
	// four UK2Node_BaseMCDelegate subclasses, both on the TEARDOWN path - and there was no
	// workaround at all, because the node classes are the only way to emit those calls.
	//
	// A PARAMETER, NOT NEW ENDPOINT NAMES. All four subclasses take the identical single
	// configuration call - SetFromProperty - so a new name per node kind would be four spellings
	// of one thing. add_call_dispatcher keeps its own name because it is already in the MCP tool
	// surface and removing it would break callers.
	//
	// UK2Node_ClearDelegate HAS NO DELEGATE PIN AT ALL. K2Node_MCDelegate.cpp:368-390 gives it a
	// title and a node handler and nothing else, so op:"unbindAll" comes back with a different pin
	// set from bind/unbind - there is no Delegate pin to wire, because clearing removes every
	// binding rather than one named handler. EmitNode reports whatever pins exist, but a caller
	// expecting to wire a Delegate would sit there looking for it, so the response says so.
	void H_add_bind_dispatcher(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString Op = JStr(In, TEXT("op"), TEXT("bind")).ToLower();
		if (Op == TEXT("bind"))
		{
			SpawnDelegateNode<UK2Node_AddDelegate>(In, Out);
			return;
		}
		if (Op == TEXT("unbind"))
		{
			SpawnDelegateNode<UK2Node_RemoveDelegate>(In, Out);
			return;
		}
		if (Op == TEXT("unbindall") || Op == TEXT("clear"))
		{
			SpawnDelegateNode<UK2Node_ClearDelegate>(In, Out);
			if (Out->HasField(TEXT("nodeGuid")))
			{
				Out->SetStringField(TEXT("pinNote"),
					TEXT("a ClearDelegate node has NO Delegate pin - clearing removes EVERY binding "
						 "rather than one named handler, so there is nothing to wire an event into. "
						 "That is why this node's pin set differs from op:\"bind\" and "
						 "op:\"unbind\"; it is not a missing pin."));
			}
			return;
		}
		if (Op == TEXT("call") || Op == TEXT("broadcast"))
		{
			Fail(Out, TEXT("op:\"call\" belongs to add_call_dispatcher, which already exists and is "
				TEXT("already in the tool surface. This endpoint covers bind, unbind and unbindAll. "
					 "NOTHING was added.")));
			return;
		}
		Fail(Out, FString::Printf(
			TEXT("unknown op '%s' - accepted: bind (the default), unbind, unbindAll. Broadcasting is "
				 "add_call_dispatcher. NOTHING was added."), *Op));
	}

	// --- list_dispatchers ---------------------------------------------------

	void H_list_dispatchers(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path)"),
			{ { TEXT("graphId"), TEXT("list_dispatchers is blueprint-scoped — pass blueprintId") },
			  { TEXT("filter"), TEXT("this endpoint takes no filter; it returns every dispatcher on the blueprint") } }))
		{
			return;
		}

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

	// =======================================================================
	// add_create_event - and an ordering that erases what you just set
	// =======================================================================
	//
	// WHAT IS ACTUALLY MISSING, stated narrowly because the survey overstated it. "The only way to
	// bind an inherited event" is false: UK2Node_Event::AllocateDefaultPins creates the
	// PC_Delegate OutputDelegate pin on EVERY event node (K2Node_Event.cpp:104-106), not just
	// custom ones, and add_override_event already spawns UK2Node_Event - so inherited and override
	// events in the ubergraph are bindable today with add_override_event + connect_pins. The two
	// cases genuinely uncovered are binding an ordinary existing FUNCTION (a Blueprint function
	// graph or a native UFUNCTION), and binding from inside a function or macro graph where no
	// event node can exist at all.
	//
	// THE ORDERING IS THE WHOLE DIFFICULTY, and the obvious sequence is wrong in a way that leaves
	// no trace. UK2Node_CreateDelegate::HandleAnyChangeWithoutNotifying ends with
	// (K2Node_CreateDelegate.cpp:236-243):
	//
	//     if (DelegatePin->LinkedTo.Num() == 0) { SelectedFunctionName = NAME_None; }
	//     SelectedFunctionGuid.Invalidate();
	//
	// and it reaches that branch whenever IsValid() fails. On a freshly placed, UNCONNECTED node
	// IsValid() ALWAYS fails, because GetDelegateSignature returns nullptr unless the delegate pin
	// is linked - its own message is "Unable to determine expected signature - is the delegate pin
	// connected?". So "place, SetFunction, HandleAnyChange" silently ERASES the function it just
	// set. The node must be CONNECTED first, which is why this endpoint takes the destination and
	// makes the connection itself rather than leaving it to a later connect_pins call.
	//
	// IsValid IS NOT CALLABLE FROM A PLUGIN. K2Node_CreateDelegate.h:59 declares it with NO
	// BLUEPRINTGRAPH_API on a MinimalAPI class and defines it out-of-line, so it will not link -
	// docs/audit/03_GAPS_AND_RISKS.md:37 already records this. Validation goes through the exported
	// GetDelegateSignature() != nullptr instead, plus a read-back of GetFunctionName() which is what
	// actually proves the erase above did not happen.
	//
	// THERE IS NO scopeClass PARAMETER, and that is not an omission. GetScopeClass derives the scope
	// ENTIRELY from what is linked to the Self pin (:357-400) - there is no setter and no UPROPERTY
	// behind it. Accepting a scopeClass string would be silently ignored, which is the exact
	// failure mode RejectUnknownParams exists to prevent. To bind a function on another class, wire
	// an object pin of that type into the Self pin afterwards.

	void H_add_create_event(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("function"), TEXT("bindNode"), TEXT("bindPin"),
			  TEXT("x"), TEXT("y") },
			TEXT("graphId; function - the function or custom event to wrap; bindNode - the guid of ")
			TEXT("the bind node whose Delegate pin this feeds; bindPin (default \"Delegate\"); x, y"),
			{ { TEXT("scopeClass"), TEXT("there is no setter for the scope - GetScopeClass derives "
										 "it entirely from what is wired into the Self pin, so a "
										 "scopeClass parameter would be silently ignored. Wire an "
										 "object of that type into Self instead") },
			  { TEXT("event"), TEXT("spell it function - this wraps a function OR a custom event by "
									"name, and the parameter covers both") },
			  { TEXT("delegate"), TEXT("the dispatcher is named on the BIND node; this endpoint only "
									   "needs that node's guid") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph || !Blueprint) { return; }

		const FString FunctionName = JStr(In, TEXT("function"));
		if (FunctionName.IsEmpty())
		{
			Fail(Out, TEXT("function is required - the function or custom event to wrap. NOTHING "
				TEXT("was added.")));
			return;
		}

		// --- the destination, resolved BEFORE anything is placed -------------------------------
		UEdGraphNode* BindNode = ResolveNodeField(In, TEXT("bindNode"), Out);
		if (!BindNode) { return; }
		if (BindNode->GetGraph() != Graph)
		{
			Fail(Out, TEXT("bindNode is in a different graph. A delegate pin can only be wired "
				TEXT("within one graph. NOTHING was added.")));
			return;
		}
		const FString BindPinName = JStr(In, TEXT("bindPin"), TEXT("Delegate"));
		UEdGraphPin* BindPin = FindPin(BindNode, BindPinName, EGPD_Input, /*bRequireDir*/ false);
		if (!BindPin)
		{
			TArray<FString> Have;
			for (const UEdGraphPin* Pin : BindNode->Pins)
			{
				if (Pin) { Have.Add(Pin->PinName.ToString()); }
			}
			Fail(Out, FString::Printf(
				TEXT("no pin '%s' on that node. It has: %s. Note a ClearDelegate (unbindAll) node "
					 "has NO Delegate pin at all - there is nothing to wire an event into, because "
					 "it clears every binding. NOTHING was added."),
				*BindPinName, *FString::Join(Have, TEXT(", "))));
			return;
		}

		// --- the function must be usable as a delegate ------------------------------------------
		UClass* Scope = Blueprint->GeneratedClass ? Blueprint->GeneratedClass
												  : Blueprint->SkeletonGeneratedClass;
		UFunction* Target = Scope ? Scope->FindFunctionByName(FName(*FunctionName)) : nullptr;
		if (!Target)
		{
			TArray<FString> Some;
			if (Scope)
			{
				for (TFieldIterator<UFunction> It(Scope); It && Some.Num() < 12; ++It)
				{
					Some.Add(It->GetName());
				}
			}
			Fail(Out, FString::Printf(
				TEXT("'%s' has no function or event named '%s'. Some it does have: %s. NOTHING was "
					 "added."), *Blueprint->GetName(), *FunctionName,
				Some.Num() ? *FString::Join(Some, TEXT(", ")) : TEXT("(none)")));
			return;
		}
		if (const UEdGraphSchema_K2* K2 = Cast<UEdGraphSchema_K2>(Graph->GetSchema()))
		{
			if (!K2->FunctionCanBeUsedInDelegate(Target))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' cannot be used as a delegate - pure, latent and deprecated functions "
						 "are excluded by UEdGraphSchema_K2::FunctionCanBeUsedInDelegate. NOTHING "
						 "was added."), *FunctionName));
				return;
			}
		}

		const int32 X = JInt(In, TEXT("x"), 0);
		const int32 Y = JInt(In, TEXT("y"), 0);

		UK2Node_CreateDelegate* Node = NewObject<UK2Node_CreateDelegate>(Graph);
		PlaceAndInit(Graph, Node, X, Y);
		if (!Graph->Nodes.Contains(Node))
		{
			Fail(Out, TEXT("the CreateDelegate node was constructed and the graph does not list it. "
				TEXT("NOTHING usable was produced.")));
			return;
		}

		// --- CONNECT FIRST. This is the whole point of the endpoint's shape --------------------
		UEdGraphPin* OutPin = Node->GetDelegateOutPin();
		if (!OutPin)
		{
			Graph->RemoveNode(Node);
			Fail(Out, TEXT("the CreateDelegate node has no delegate output pin. NOTHING was added."));
			return;
		}
		if (!Graph->GetSchema()->TryCreateConnection(OutPin, BindPin))
		{
			Graph->RemoveNode(Node);
			Fail(Out, FString::Printf(
				TEXT("could not connect the event to '%s' on the bind node - the pin types do not "
					 "match. The node was removed rather than left dangling. NOTHING was added."),
				*BindPinName));
			return;
		}

		// THE LINK, READ BACK. TryCreateConnection returning true is the engine's own report; the
		// postcondition is that the pins are actually linked - and everything downstream depends on
		// it, because the function survives only while the delegate pin has links.
		if (!OutPin->LinkedTo.Contains(BindPin))
		{
			Graph->RemoveNode(Node);
			Fail(Out, TEXT("the connection reported success and the pins are not linked on "
				TEXT("read-back. The node was removed. NOTHING usable was produced.")));
			return;
		}

		// --- ONLY NOW is the function safe to set ------------------------------------------------
		Node->SetFunction(FName(*FunctionName));
		Node->HandleAnyChange(/*bForceModify*/ true);

		// THE READ-BACK THAT PROVES THE ERASE DID NOT HAPPEN. HandleAnyChange clears
		// SelectedFunctionName when the delegate pin is unlinked and the signature cannot be
		// resolved, so GetFunctionName coming back as the one we asked for is the only evidence
		// the ordering above actually worked.
		const FName Now = Node->GetFunctionName();
		UFunction* Signature = Node->GetDelegateSignature();
		if (Now != FName(*FunctionName))
		{
			Graph->RemoveNode(Node);
			Fail(Out, FString::Printf(
				TEXT("the function was set to '%s' and reads back as '%s' - HandleAnyChange cleared "
					 "it, which happens when the delegate signature cannot be resolved from what the "
					 "node is connected to. Check that '%s' matches the dispatcher's signature. The "
					 "node was removed. NOTHING usable was produced."),
				*FunctionName, *Now.ToString(), *FunctionName));
			return;
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
		Out->SetStringField(TEXT("function"), Now.ToString());
		Out->SetBoolField(TEXT("connected"), true);
		Out->SetStringField(TEXT("boundTo"), FString::Printf(TEXT("%s.%s"),
			*BindNode->NodeGuid.ToString(EGuidFormats::Digits), *BindPinName));
		Out->SetBoolField(TEXT("signatureResolved"), Signature != nullptr);
		if (!Signature)
		{
			// Not fatal - the function survived, so the node is usable - but the signature not
			// resolving is worth surfacing rather than leaving to compile time.
			Out->SetStringField(TEXT("signatureNote"),
				TEXT("the function stuck, but the delegate signature could not be resolved from the "
					 "connection. That usually means the wrapped function's parameters do not match "
					 "the dispatcher's. compile will say so precisely."));
		}
		Out->SetStringField(TEXT("scopeNote"),
			TEXT("the scope is derived from the Self pin, which is unconnected - so this wraps a "
				 "function on THIS blueprint. To wrap one on another class, wire an object of that "
				 "type into Self; there is no scopeClass parameter because the engine has no "
				 "setter for it."));
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the Blueprint is dirty and NOT compiled."));
	}
}
