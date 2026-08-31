// MifBridge — phase-3 node endpoints: custom event, make/break struct, self, object literal,
// function creation, and the resolve_struct introspection helper.
#include "MifBridgeHandlers.h"
#include "K2Node_Tunnel.h"
#include "Animation/Skeleton.h"
#include "Animation/AnimInstance.h"
#include "Animation/AnimBlueprintGeneratedClass.h"
#include "Animation/AnimBlueprint.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "UObject/UnrealType.h"      // FProperty::GetFName - the orphan count below
#include "K2Node_BaseMCDelegate.h"   // GetPropertyName - the orphan count in remove_event_dispatcher
#include "K2Node_BreakStruct.h"
#include "K2Node_CustomEvent.h"
#include "K2Node_EditablePinBase.h"   // shared base of FunctionEntry + CustomEvent (set_function_flags)
#include "K2Node_FunctionEntry.h"
#include "K2Node_FunctionResult.h"
#include "K2Node_Literal.h"
#include "K2Node_MakeStruct.h"
#include "K2Node_Self.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "Engine/SCS_Node.h"                    // USCS_Node::Modify (reparent_blueprint)
#include "Engine/SimpleConstructionScript.h"    // GetAllNodes (reparent_blueprint)
#include "Kismet/BlueprintFunctionLibrary.h"   // UBlueprintFunctionLibrary — function-library base for create_blueprint
#include "UObject/Interface.h"                  // UInterface — blueprint interface base
#include "WidgetBlueprint.h"                          // UWidgetBlueprint — WidgetBlueprint create
#include "Blueprint/WidgetBlueprintGeneratedClass.h"  // UWidgetBlueprintGeneratedClass
#include "Blueprint/WidgetTree.h"                      // UWidgetTree::ConstructWidget
#include "Blueprint/UserWidget.h"                      // UUserWidget parent
#include "Components/CanvasPanel.h"                    // UCanvasPanel root
#include "AssetRegistry/AssetRegistryModule.h"
#include "GameFramework/Actor.h"    // AActor::GetIsReplicated — RPC sanity warning (set_function_flags)
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

	// ParsePinSpecs moved to MifBridgeCommon.cpp (declared in MifBridgeHandlers.h). It was duplicated
	// as ParseDispatcherParams in MifBridgeDelegates.cpp — same signature shape, effectively identical
	// body — so the two disagreed only in error wording and in nothing that mattered until one of them
	// got fixed. Do NOT re-add a local copy.

	// --- resolve_struct -----------------------------------------------------

	void H_resolve_struct(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("name") },
			TEXT("name (bare name, C++ name or full path - e.g. Vector, FGuid, /Script/CoreUObject.Transform)"),
			{ { TEXT("structName"), TEXT("resolve_struct spells it name; structName is what add_make_struct/add_break_struct use") },
			  { TEXT("struct"),     TEXT("spell it name") },
			  { TEXT("path"),       TEXT("pass the path as the VALUE of name - name accepts a bare name or a full struct path in the same field") } }))
		{
			return;
		}
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
		if (RejectUnknownParams(In, Out, { TEXT("graphId"), TEXT("x"), TEXT("y") }, TEXT("graphId, x, y"),
			{ { TEXT("graph"),       TEXT("spell it graphId") },
			  { TEXT("blueprintId"), TEXT("a node is placed in a GRAPH - pass graphId (list_graphs shows every graph of a blueprint); the owning blueprint is inferred from it") } }))
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

		UK2Node_Self* Node = NewObject<UK2Node_Self>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_custom_event(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("name"), TEXT("inputs"), TEXT("x"), TEXT("y") },
			TEXT("graphId, name, inputs? ([{name, type, container?, valueType?}] - the event's parameters), x, y"),
			{ { TEXT("graph"),      TEXT("spell it graphId") },
			  { TEXT("outputs"),    TEXT("a custom event's parameters ARE its output pins - list them under inputs; there is no outputs key here (create_function is the endpoint that has both)") },
			  { TEXT("params"),     TEXT("spell it inputs") },
			  { TEXT("parameters"), TEXT("spell it inputs") },
			  { TEXT("eventName"),  TEXT("spell it name") } }))
		{
			return;
		}

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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("structName"), TEXT("x"), TEXT("y") },
			TEXT("graphId, structName, x, y"),
			{ { TEXT("graph"),  TEXT("spell it graphId") },
			  { TEXT("struct"), TEXT("spell it structName") },
			  { TEXT("name"),   TEXT("the struct is named by structName; resolve_struct is the endpoint whose parameter is called name") },
			  { TEXT("type"),   TEXT("spell it structName") } }))
		{
			return;
		}

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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("structName"), TEXT("x"), TEXT("y") },
			TEXT("graphId, structName, x, y"),
			{ { TEXT("graph"),  TEXT("spell it graphId") },
			  { TEXT("struct"), TEXT("spell it structName") },
			  { TEXT("name"),   TEXT("the struct is named by structName; resolve_struct is the endpoint whose parameter is called name") },
			  { TEXT("type"),   TEXT("spell it structName") } }))
		{
			return;
		}

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
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("object"), TEXT("x"), TEXT("y") },
			TEXT("graphId, object (an asset OBJECT PATH; object-reference literals only), x, y"),
			{ { TEXT("graph"),      TEXT("spell it graphId") },
			  { TEXT("value"),      TEXT("add_literal makes an OBJECT-reference literal only - for a scalar (int/float/bool/string/name) place the consuming node and use set_pin_default on its pin instead") },
			  { TEXT("path"),       TEXT("the asset path goes in object") },
			  { TEXT("objectPath"), TEXT("spell it object") },
			  { TEXT("asset"),      TEXT("spell it object") },
			  { TEXT("type"),       TEXT("the literal's type comes from the resolved object's class; there is nothing to declare") } }))
		{
			return;
		}

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
		// override/parentClass are NOT accepted, and saying so is the point. A user trying to override
		// a parent function passed override:true and parentClass here; both were silently dropped, a
		// plain new function was created with the parent's name, and the resulting collision produced
		// six compile errors with nothing in the response hinting at the cause. They then probed nine
		// invented endpoint names, all 404, and concluded the bridge could not do overrides at all —
		// while add_override_event, which does exactly this and even takes parentClass, was already
		// registered. The KeyNotes route that guess to the right endpoint on the first call.
		// The pin names the engine ACTUALLY used. Declared at function scope on purpose: they are
		// filled inside the graph-building block below and read again when the response is written,
		// which is two levels shallower.
		TArray<TSharedPtr<FJsonValue>> ActualInputNames;
		TArray<TSharedPtr<FJsonValue>> ActualOutputNames;
		TArray<FString> RenamedPins;

		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"),
			  TEXT("name"), TEXT("inputs"), TEXT("outputs"), TEXT("pure") },
			TEXT("blueprintId (alias: path), name, inputs?, outputs?, pure?"),
			{ { TEXT("override"),    TEXT("create_function makes a NEW function; it cannot override. Use add_override_event {event, parentClass?, callParent?} — naming a parent's function here creates a COLLIDING duplicate that fails to compile") },
			  { TEXT("parentClass"), TEXT("create_function does not take a parent class. add_override_event accepts parentClass (aliases: class, interfaceOrParent, ownerClass, targetClass)") },
			  { TEXT("interface"),   TEXT("to implement an interface function use implement_interface_function; to override a parent event use add_override_event") },
			  { TEXT("event"),       TEXT("events live in the event graph — use add_custom_event for a new one, or add_override_event to override a parent's") } }))
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
			Fail(Out, FString::Printf(TEXT("invalid function name '%s'"), *Raw));
			return;
		}

		// REFUSE A NAME THE PARENT ALREADY OWNS. Creating it anyway is what produced the six compile
		// errors: the graph is added, the Blueprint compiles, and the collision only surfaces as
		// "conflicts with a function in the parent" from the compiler — long after the ok:true.
		if (UClass* ParentClass = Blueprint->ParentClass)
		{
			if (const UFunction* Clash = ParentClass->FindFunctionByName(FName(*Name)))
			{
				const bool bOverridable = Clash->HasAnyFunctionFlags(FUNC_BlueprintEvent)
					|| (Clash->HasAnyFunctionFlags(FUNC_BlueprintCallable) && !Clash->HasAnyFunctionFlags(FUNC_Final));
				Fail(Out, FString::Printf(
					TEXT("'%s' is already declared by the parent class '%s', so creating a new function ")
					TEXT("with that name would produce a COLLIDING duplicate that fails to compile. %s"),
					*Name, *ParentClass->GetPathName(),
					bOverridable
						? TEXT("That function is overridable — call add_override_event {event:\"<name>\", callParent:true} instead, which creates the override graph properly.")
						: TEXT("That function is NOT overridable (it is final or not a BlueprintEvent), so pick a different name.")));
				Out->SetBoolField(TEXT("nothingModified"), true);
				Out->SetStringField(TEXT("outcome"), TEXT("preflight-rejected-nothing-created"));
				Out->SetStringField(TEXT("conflictsWith"), Clash->GetPathName());
				Out->SetBoolField(TEXT("parentFunctionIsOverridable"), bOverridable);
				if (bOverridable)
				{
					Out->SetStringField(TEXT("route"), TEXT("add_override_event"));
				}
				return;
			}
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
				// bUseUniqueName IS TRUE, so the engine RENAMES on collision and hands back the pin it
				// actually made - and that return value was discarded. A caller asking for a parameter
				// named "then" (the entry node's own exec pin) or naming two inputs the same got a
				// differently-named pin and no way to learn it, then failed later trying to wire the
				// name they asked for. Same shape as GenerateNewComponentName in add_component.
				for (const TPair<FName, FEdGraphPinType>& Pin : Inputs)
				{
					if (UEdGraphPin* Made = Entry->CreateUserDefinedPin(Pin.Key, Pin.Value, EGPD_Output, /*bUseUniqueName*/ true))
					{
						ActualInputNames.Add(MakeShared<FJsonValueString>(Made->PinName.ToString()));
						if (Made->PinName != Pin.Key) { RenamedPins.Add(FString::Printf(TEXT("%s -> %s"), *Pin.Key.ToString(), *Made->PinName.ToString())); }
					}
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
					if (UEdGraphPin* Made = Result->CreateUserDefinedPin(Pin.Key, Pin.Value, EGPD_Input, /*bUseUniqueName*/ true))
					{
						ActualOutputNames.Add(MakeShared<FJsonValueString>(Made->PinName.ToString()));
						if (Made->PinName != Pin.Key) { RenamedPins.Add(FString::Printf(TEXT("%s -> %s"), *Pin.Key.ToString(), *Made->PinName.ToString())); }
					}
				}

				// Belt-and-braces: drop any duplicate same-name/same-direction pin, keeping a wired copy.
				// The root cause is fixed in PlaceAndInit (UK2Node_FunctionResult::PostPlacedNewNode
				// already allocates, and its AllocateDefaultPins re-CreatePin's PN_Execute with no guard),
				// but this makes create_function self-healing if any other terminator ever behaves the same.
				int32 DuplicatePinsRemoved = 0;
				{
					TSet<TPair<FName, int32>> Seen;
					for (int32 i = Result->Pins.Num() - 1; i >= 0; --i)
					{
						UEdGraphPin* Pin = Result->Pins[i];
						if (!Pin) { continue; }
						const TPair<FName, int32> Key(Pin->PinName, (int32)Pin->Direction);
						// Walk backwards, so the FIRST-declared pin is the one kept — unless a later
						// duplicate is the wired one, in which case leave the wired copy alone.
						if (Seen.Contains(Key) && Pin->LinkedTo.Num() == 0)
						{
							Result->Pins.RemoveAt(i);
							Pin->MarkAsGarbage();
							++DuplicatePinsRemoved;
							continue;
						}
						Seen.Add(Key);
					}
				}
				if (DuplicatePinsRemoved > 0)
				{
					Out->SetNumberField(TEXT("duplicatePinsRemoved"), DuplicatePinsRemoved);
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
		// COUNTS ALONE IMPLY THE NAMES WERE HONOURED. They are not always: see the bUseUniqueName note
		// above. Reporting the real names lets a caller wire what actually exists.
		Out->SetArrayField(TEXT("inputNames"), ActualInputNames);
		Out->SetArrayField(TEXT("outputNames"), ActualOutputNames);
		if (RenamedPins.Num() > 0)
		{
			Out->SetStringField(TEXT("pinsRenamed"), FString::Join(RenamedPins, TEXT(", ")));
			Out->SetStringField(TEXT("pinsRenamedNote"),
				TEXT("the engine renamed these parameters because the name was already taken on that node "
					 "(the entry node owns 'then', the result node owns 'execute'). Wire the names in "
					 "inputNames/outputNames, not the ones you asked for."));
		}
		if (Entry)
		{
			Out->SetStringField(TEXT("entryNodeGuid"), Entry->NodeGuid.ToString());
		}
		Out->SetObjectField(TEXT("compile"), CompileOut);
	}

	// --- rename_function / rename_event / rename_event_dispatcher ------------
	//
	// FBlueprintEditorUtils::RenameGraph does the heavy lifting for graphs: it renames the UEdGraph,
	// repoints FunctionReference on the entry/result terminators, and fixes override graphs in CHILD
	// blueprints. What it does NOT do is touch a delegate's backing member variable — a dispatcher is
	// a signature graph PLUS a PC_MCDelegate variable, and renaming only one of the two breaks it.
	// That asymmetry is why rename_event_dispatcher exists separately instead of being "rename the graph".

	void H_rename_function(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// Both selector branches are accepted here: graphId addresses the graph directly, otherwise
		// ResolveBlueprintField (blueprintId/path) + oldName looks it up by name.
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("blueprintId"), TEXT("path"),
			  TEXT("oldName"), TEXT("function"), TEXT("name"),
			  TEXT("newName"), TEXT("to"), TEXT("confirm") },
			TEXT("graphId, OR blueprintId (alias: path) + oldName (aliases: function, name); plus newName (alias: to), confirm (required, must be true)"),
			{ { TEXT("from"),            TEXT("the current name is oldName (aliases: function, name) - only the destination has a short spelling ('to' = newName)") },
			  { TEXT("graph"),           TEXT("spell it graphId") },
			  { TEXT("newFunctionName"), TEXT("spell it newName (alias: to)") },
			  { TEXT("dispatcher"),      TEXT("an event dispatcher is a signature graph PLUS a backing delegate variable - use rename_event_dispatcher, which renames both") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("rename_function requires confirm=true"));
			return;
		}
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;

		const FString GraphId = JStr(In, TEXT("graphId"));
		if (!GraphId.IsEmpty())
		{
			Graph = ResolveGraphField(In, Out, Blueprint);
			if (!Graph) { return; }
		}
		else
		{
			Blueprint = ResolveBlueprintField(In, Out);
			if (!Blueprint) { return; }
			const FString OldName = JStrAny(In, { TEXT("oldName"), TEXT("function"), TEXT("name") });
			if (OldName.IsEmpty())
			{
				Fail(Out, TEXT("supply graphId, or blueprintId + oldName"));
				return;
			}
			for (UEdGraph* G : Blueprint->FunctionGraphs)
			{
				if (G && G->GetName() == OldName) { Graph = G; break; }
			}
			if (!Graph)
			{
				Fail(Out, FString::Printf(TEXT("function graph '%s' not found in %s"), *OldName, *Blueprint->GetName()));
				return;
			}
		}

		FString NewName = JStrAny(In, { TEXT("newName"), TEXT("to") });
		NewName.TrimStartAndEndInline();
		if (!IsValidIdentifier(NewName))
		{
			Fail(Out, FString::Printf(TEXT("invalid new function name '%s'"), *NewName));
			return;
		}
		if (FBlueprintEditorUtils::IsDelegateSignatureGraph(Graph))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is an event-dispatcher signature graph, not a function — use rename_event_dispatcher, which also renames the backing delegate variable"),
				*Graph->GetName()));
			return;
		}
		const FString OldGraphName = Graph->GetName();
		if (OldGraphName == NewName)
		{
			Fail(Out, TEXT("newName is the same as the current name"));
			return;
		}
		for (UEdGraph* G : Blueprint->FunctionGraphs)
		{
			if (G && G->GetName() == NewName)
			{
				Fail(Out, FString::Printf(TEXT("a function named '%s' already exists"), *NewName));
				return;
			}
		}

		Blueprint->Modify();
		FBlueprintEditorUtils::RenameGraph(Graph, NewName);
		MarkStructural(Blueprint);

		Out->SetStringField(TEXT("oldName"), OldGraphName);
		Out->SetStringField(TEXT("name"), Graph->GetName());
		Out->SetStringField(TEXT("graphId"), GraphIdOf(Blueprint, Graph));
		// Call sites in OTHER blueprints resolve by name and do not auto-fix.
		Out->SetStringField(TEXT("warning"),
			TEXT("call sites in this blueprint are repointed automatically; callers in OTHER blueprints resolve by name and must be recompiled (or will show as errors)"));
	}

	void H_rename_event(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// graphId is listed because ResolveNodeField reads it too: when present it scopes the guid
		// lookup to that graph, which is how a guid duplicated across loaded copies is disambiguated.
		if (RejectUnknownParams(In, Out,
			{ TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"),
			  TEXT("newName"), TEXT("name"), TEXT("to"), TEXT("confirm") },
			TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), newName (aliases: name, to), confirm (required, must be true)"),
			{ { TEXT("oldName"),     TEXT("rename_event addresses the event by nodeGuid, not by its current name - only the new name is passed (newName, aliases: name, to)") },
			  { TEXT("from"),        TEXT("rename_event addresses the event by nodeGuid; the destination is newName (aliases: name, to)") },
			  { TEXT("event"),       TEXT("address the custom event by nodeGuid (aliases: node, guid, nodeId) - find_nodes locates it") },
			  { TEXT("blueprintId"), TEXT("the owning blueprint is inferred from the node; pass graphId if the same guid exists in more than one loaded copy") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("rename_event requires confirm=true"));
			return;
		}
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
		if (!Node)
		{
			return;
		}
		UK2Node_CustomEvent* Event = Cast<UK2Node_CustomEvent>(Node);
		if (!Event)
		{
			Fail(Out, FString::Printf(
				TEXT("node is a %s — rename_event targets a Custom Event. (An OVERRIDE event's name comes from its parent and cannot be changed.)"),
				*Node->GetClass()->GetName()));
			return;
		}
		if (Event->IsOverride())
		{
			Fail(Out, TEXT("this event overrides a parent event — its name is fixed by the parent declaration"));
			return;
		}
		FString NewName = JStrAny(In, { TEXT("newName"), TEXT("name"), TEXT("to") });
		NewName.TrimStartAndEndInline();
		if (!IsValidIdentifier(NewName))
		{
			Fail(Out, FString::Printf(TEXT("invalid new event name '%s'"), *NewName));
			return;
		}

		const FString OldName = Event->CustomFunctionName.ToString();
		UBlueprint* Blueprint = FBlueprintEditorUtils::FindBlueprintForNode(Node);
		Event->Modify();
		// OnRenameNode is the node's own rename entry point — it updates CustomFunctionName and
		// keeps the node's cached title in sync. Setting CustomFunctionName directly would leave
		// the title stale until the next reconstruct.
		Event->OnRenameNode(NewName);
		MarkStructural(Blueprint);

		// VERIFY. OnRenameNode is void, and it declines a name that collides with an existing event
		// rather than reporting anything. The post-rename name below was already being returned, so
		// the truth was in the payload - but nothing compared it to the request, and no caller diffs
		// a response field against what they just asked for. Renaming is this endpoint's whole job,
		// so a rename that did not happen is a failure, not a footnote.
		const FString ActualName = Event->CustomFunctionName.ToString();
		Out->SetStringField(TEXT("oldName"), OldName);
		Out->SetStringField(TEXT("name"), ActualName);
		Out->SetStringField(TEXT("requestedName"), NewName);
		Out->SetStringField(TEXT("nodeGuid"), Event->NodeGuid.ToString());

		if (!ActualName.Equals(NewName, ESearchCase::CaseSensitive))
		{
			Fail(Out, FString::Printf(
				TEXT("the rename did not take: the event is still called '%s', not '%s'. OnRenameNode "
					 "declines a name that collides with another event in this blueprint and cannot "
					 "report why, so this is read back rather than assumed. Check list_nodes for an "
					 "existing '%s'."),
				*ActualName, *NewName, *NewName));
			return;
		}
	}

	void H_rename_event_dispatcher(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"),
			  TEXT("oldName"), TEXT("name"), TEXT("dispatcher"),
			  TEXT("newName"), TEXT("to"), TEXT("confirm") },
			TEXT("blueprintId (alias: path), oldName (aliases: name, dispatcher), newName (alias: to), confirm (required, must be true)"),
			{ { TEXT("from"),    TEXT("the current name is oldName (aliases: name, dispatcher) - only the destination has a short spelling ('to' = newName)") },
			  { TEXT("graphId"), TEXT("a dispatcher is a signature GRAPH plus a backing delegate VARIABLE - it is addressed by blueprintId + oldName so both halves can be renamed together") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("rename_event_dispatcher requires confirm=true"));
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		const FString OldName = JStrAny(In, { TEXT("oldName"), TEXT("name"), TEXT("dispatcher") });
		FString NewName = JStrAny(In, { TEXT("newName"), TEXT("to") });
		NewName.TrimStartAndEndInline();
		if (OldName.IsEmpty() || !IsValidIdentifier(NewName))
		{
			Fail(Out, FString::Printf(TEXT("oldName and a valid newName are required (got newName='%s')"), *NewName));
			return;
		}

		UEdGraph* SignatureGraph = FBlueprintEditorUtils::GetDelegateSignatureGraphByName(Blueprint, FName(*OldName));
		if (!SignatureGraph)
		{
			Fail(Out, FString::Printf(
				TEXT("event dispatcher '%s' not found in %s — list_dispatchers shows what exists"), *OldName, *Blueprint->GetName()));
			return;
		}

		// MODAL HAZARD, same family as PM-011. RenameMemberVariable opens an FSuppressableWarningDialog
		// when the variable carries a RepNotify function (BlueprintEditorUtils.cpp:4837), and a modal on
		// the game thread does not ask a question - it stops the HTTP ticker and the bridge is gone until
		// someone clicks it. A dispatcher's backing delegate should never have a RepNotify: the Details
		// panel does not offer one for a multicast delegate. But "should never" is an argument, not a
		// check, and the cost of the argument being wrong is the whole bridge.
		//
		// Refused rather than suppressed, for the same reason rename_variable refuses: declining that
		// particular dialog makes the engine REVERT the name (:4841), so a suppressed-and-declined run
		// would report a rename that silently did not happen. Checked BEFORE the two renames below,
		// because refusing between them would leave the dispatcher half-renamed.
		const int32 DelegateVarIdx = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*OldName));
		if (DelegateVarIdx != INDEX_NONE
			&& Blueprint->NewVariables[DelegateVarIdx].RepNotifyFunc != NAME_None)
		{
			Fail(Out, FString::Printf(
				TEXT("the delegate variable behind dispatcher '%s' has a RepNotify function ('%s'), and the ")
				TEXT("engine's rename path opens a MODAL dialog for that case - it would hang the bridge until ")
				TEXT("a human clicked it. Clear it first with set_variable_flags {name:'%s', repNotify:false}, ")
				TEXT("rename, then set it again. Nothing was changed."),
				*OldName, *Blueprint->NewVariables[DelegateVarIdx].RepNotifyFunc.ToString(), *OldName));
			return;
		}

		Blueprint->Modify();
		// BOTH halves, or the dispatcher breaks: the signature graph carries the parameter list, the
		// member variable is what call/bind nodes actually reference. RenameGraph does not touch the
		// variable, and RenameMemberVariable does not touch the graph.
		FBlueprintEditorUtils::RenameGraph(SignatureGraph, NewName);
		FBlueprintEditorUtils::RenameMemberVariable(Blueprint, FName(*OldName), FName(*NewName));

		// READ BACK BOTH HALVES. These two flags used to be literal `true`, over engine calls that give
		// no answer: RenameMemberVariable is VOID and early-returns silently when the variable is
		// absent or the names already match (BlueprintEditorUtils.cpp:4609-4610), and RenameGraph
		// reports nothing either. rename_variable reads back for exactly this reason, and its comment
		// says so; this endpoint asserted instead.
		//
		// A HALF rename is the specific disaster, and the comment directly above says why: the
		// signature graph carries the parameter list and the member variable is what call/bind nodes
		// reference, so one renamed without the other leaves a dispatcher that resolves under two
		// different names and breaks on the next compile. remove_event_dispatcher verifies both halves;
		// so does this now.
		const bool bGraphRenamed =
			(FBlueprintEditorUtils::GetDelegateSignatureGraphByName(Blueprint, FName(*NewName)) != nullptr)
			&& (FBlueprintEditorUtils::GetDelegateSignatureGraphByName(Blueprint, FName(*OldName)) == nullptr);
		const bool bVarRenamed =
			(FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*NewName)) != INDEX_NONE)
			&& (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*OldName)) == INDEX_NONE);

		MarkStructural(Blueprint);

		if (!bGraphRenamed || !bVarRenamed)
		{
			Fail(Out, FString::Printf(
				TEXT("rename of dispatcher '%s' to '%s' did not complete (signature graph renamed=%s, delegate ")
				TEXT("variable renamed=%s). The blueprint may now hold HALF a rename, which resolves under two ")
				TEXT("names and breaks on the next compile - check list_dispatchers and list_variables before ")
				TEXT("doing anything else. A cancelled transaction does not undo this (PM-007)."),
				*OldName, *NewName,
				bGraphRenamed ? TEXT("yes") : TEXT("no"), bVarRenamed ? TEXT("yes") : TEXT("no")));
			return;
		}

		Out->SetStringField(TEXT("oldName"), OldName);
		Out->SetStringField(TEXT("name"), NewName);
		Out->SetBoolField(TEXT("renamedSignatureGraph"), bGraphRenamed);
		Out->SetBoolField(TEXT("renamedDelegateVariable"), bVarRenamed);
	}

	// --- remove_event_dispatcher ------------------------------------------------
	//   in:  { blueprintId|path, name, confirm }
	//   out: { removed, removedSignatureGraph, removedDelegateVariable, orphanedNodeCount }
	//
	// The gap this fills: dispatchers could be added, renamed and listed, but never removed. Every
	// other member of the family has a remover - remove_variable, remove_function, remove_component,
	// remove_interface - so the only way to drop a dispatcher was to delete the blueprint and rebuild
	// it. Found by asking what the SECOND identical call does across the add_* family, which is also
	// what turned up the add_component naming bug.
	//
	// BOTH HALVES, for the same reason rename_event_dispatcher renames both: a dispatcher is a
	// signature GRAPH (the parameter list) plus a member VARIABLE (what call/bind nodes reference).
	// Removing one and leaving the other is worse than removing neither - the leftover half still
	// resolves by name, so the blueprint looks like it still has a dispatcher that no longer works.
	void H_remove_event_dispatcher(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm") },
			TEXT("blueprintId (alias: path), name - the dispatcher to delete, confirm - must be true"),
			{ { TEXT("dispatcher"), TEXT("this endpoint's key is 'name'") },
			  { TEXT("force"),      TEXT("the required acknowledgement is confirm:true") },
			  { TEXT("graphId"),    TEXT("remove_event_dispatcher matches by NAME on the given blueprint - it does not take a graphId") } }))
		{
			return;
		}

		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_event_dispatcher requires confirm=true"));
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required (the dispatcher to remove) - list_dispatchers shows what exists"));
			return;
		}

		UEdGraph* SignatureGraph = FBlueprintEditorUtils::GetDelegateSignatureGraphByName(Blueprint, FName(*Name));
		if (!SignatureGraph)
		{
			Fail(Out, FString::Printf(
				TEXT("event dispatcher '%s' not found in %s - list_dispatchers shows what exists"),
				*Name, *Blueprint->GetName()));
			return;
		}

		// Counted BEFORE the removal, because afterwards there is no variable to count against. These
		// nodes survive the removal as orphans that fail on the next compile, and a caller who is
		// told the number can go and fix them; one who is not will meet them as compile errors with
		// no obvious cause.
		// FBlueprintEditorUtils::GetNodesForVariable would be the obvious call and is PROTECTED, which
		// the compiler catches rather than the linker. Every call/bind/unbind node for a dispatcher
		// derives from UK2Node_BaseMCDelegate and answers GetPropertyName(), so walking the graphs is
		// both public and more precise - it counts delegate nodes rather than any node that happens to
		// mention the name.
		int32 OrphanCount = 0;
		{
			TArray<UEdGraph*> AllGraphs;
			Blueprint->GetAllGraphs(AllGraphs);
			const FName DelegateName(*Name);
			for (const UEdGraph* Gr : AllGraphs)
			{
				if (!Gr) { continue; }
				for (const UEdGraphNode* Nd : Gr->Nodes)
				{
					const UK2Node_BaseMCDelegate* Del = Cast<UK2Node_BaseMCDelegate>(Nd);
					if (!Del) { continue; }
					// RESOLVE, do not read the stored name. FMemberReference::GetMemberName returns the
					// name the reference was CREATED with, and a dispatcher rename repoints the node by
					// GUID without rewriting that string - so a node that correctly follows a rename
					// still answers with the old name. Counting on GetPropertyName alone reported 1
					// orphan for a fresh dispatcher and 0 for a renamed one whose node had followed
					// perfectly well. GetProperty() resolves through the GUID and gives the name the
					// property actually has now.
					//
					// Both are checked because neither alone is safe: the resolved property is null once
					// the delegate is already gone, and the stored name is stale after a rename. An
					// over-count here is a caller looking at one node too many; an under-count is a
					// caller told there is nothing to fix when there is.
					const FProperty* Resolved = Del->GetProperty();
					if ((Resolved && Resolved->GetFName() == DelegateName)
						|| Del->GetPropertyName() == DelegateName)
					{
						++OrphanCount;
					}
				}
			}
		}

		Blueprint->Modify();
		FBlueprintEditorUtils::RemoveGraph(Blueprint, SignatureGraph, EGraphRemoveFlags::Default);
		FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, FName(*Name));

		// READ BACK BOTH HALVES. RemoveMemberVariable is void and early-returns when the variable is
		// absent (BlueprintEditorUtils.cpp:4609-4610) - the same trap remove_variable was fixed for -
		// and RemoveGraph gives no answer either. Reporting a removal that did not happen is the one
		// outcome a confirm-gated destructive endpoint must never produce, and a HALF removal is the
		// specific risk here.
		const bool bGraphGone = (FBlueprintEditorUtils::GetDelegateSignatureGraphByName(Blueprint, FName(*Name)) == nullptr);
		const bool bVarGone   = (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*Name)) == INDEX_NONE);
		if (!bGraphGone || !bVarGone)
		{
			Fail(Out, FString::Printf(
				TEXT("removal of dispatcher '%s' did not complete (signature graph gone=%s, delegate variable gone=%s). ")
				TEXT("The blueprint may now hold half a dispatcher; check list_dispatchers and list_variables ")
				TEXT("before doing anything else."),
				*Name, bGraphGone ? TEXT("yes") : TEXT("no"), bVarGone ? TEXT("yes") : TEXT("no")));
			return;
		}

		MarkStructural(Blueprint);

		Out->SetStringField(TEXT("removed"), Name);
		Out->SetBoolField(TEXT("removedSignatureGraph"), true);
		Out->SetBoolField(TEXT("removedDelegateVariable"), true);
		Out->SetNumberField(TEXT("orphanedNodeCount"), OrphanCount);
		if (OrphanCount > 0)
		{
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("%d node(s) referenced this dispatcher and are now orphaned - they will fail the next compile. ")
				TEXT("find_nodes locates them."), OrphanCount));
		}
	}

	// --- set_function_flags -------------------------------------------------
	//
	// RPC / replication and access flags on a FUNCTION or a CUSTOM EVENT — the "Replicates"
	// dropdown (Not Replicated / Multicast / Run on Server / Run on owning Client), the "Reliable"
	// checkbox, and the access specifier / pure / const / CallInEditor boxes beside them.
	//
	// One endpoint covers both node kinds because the engine does: FBlueprintGraphActionDetails::
	// SetNetFlags takes a UK2Node_EditablePinBase and branches internally, since UK2Node_FunctionEntry
	// and UK2Node_CustomEvent share that base. The storage differs — the entry node keeps flags in
	// ExtraFlags (Get/Set/Add/ClearExtraFlags), the custom event in its public FunctionFlags word —
	// so both branches are needed, exactly as the editor has them.
	//
	//   in:  { blueprintId?, graphId? | function? | nodeGuid? ,
	//          replicates?: "none"|"multicast"|"server"|"client", reliable?,
	//          access?: "public"|"protected"|"private", pure?, const?, callInEditor?,
	//          category?, tooltip?, keywords? }
	//   out: { target, kind:"function"|"customEvent", flags:{...}, warnings[] }
	// Partial update, same contract as set_variable_flags: only keys present in In are touched.
	namespace
	{
		// Mirrors FBlueprintGraphActionDetails::SetNetFlags (BlueprintDetailsCustomization.cpp).
		// NetFlags is ONE of FUNC_NetMulticast / FUNC_NetServer / FUNC_NetClient, or 0 for "not
		// replicated". FUNC_Net is set alongside the mode; all four are cleared first, so switching
		// modes can never leave a stale second mode bit behind.
		void ApplyNetFlags(UK2Node_EditablePinBase* Node, uint32 NetFlags)
		{
			const int32 FlagsToSet   = NetFlags ? (FUNC_Net | NetFlags) : 0;
			const int32 FlagsToClear = FUNC_Net | FUNC_NetMulticast | FUNC_NetServer | FUNC_NetClient;

			Node->Modify();
			if (UK2Node_FunctionEntry* Entry = Cast<UK2Node_FunctionEntry>(Node))
			{
				int32 Extra = Entry->GetExtraFlags();
				Extra &= ~FlagsToClear;
				Extra |= FlagsToSet;
				Entry->SetExtraFlags(Extra);
			}
			else if (UK2Node_CustomEvent* Event = Cast<UK2Node_CustomEvent>(Node))
			{
				Event->FunctionFlags &= ~FlagsToClear;
				Event->FunctionFlags |= FlagsToSet;
			}
		}

		void ApplyFlagBit(UK2Node_EditablePinBase* Node, int32 Flag, bool bEnable)
		{
			Node->Modify();
			if (UK2Node_FunctionEntry* Entry = Cast<UK2Node_FunctionEntry>(Node))
			{
				if (bEnable) { Entry->AddExtraFlags(Flag); }
				else         { Entry->ClearExtraFlags(Flag); }
			}
			else if (UK2Node_CustomEvent* Event = Cast<UK2Node_CustomEvent>(Node))
			{
				if (bEnable) { Event->FunctionFlags |= Flag; }
				else         { Event->FunctionFlags &= ~Flag; }
			}
		}

		// Access specifiers are mutually exclusive and live behind one mask. Clear all three, set one.
		void ApplyAccessSpecifier(UK2Node_EditablePinBase* Node, int32 Specifier)
		{
			const int32 ClearMask = ~((int32)FUNC_AccessSpecifiers);
			Node->Modify();
			if (UK2Node_FunctionEntry* Entry = Cast<UK2Node_FunctionEntry>(Node))
			{
				int32 Extra = Entry->GetExtraFlags();
				Extra &= ClearMask;
				Extra |= Specifier;
				Entry->SetExtraFlags(Extra);
			}
			else if (UK2Node_Event* Event = Cast<UK2Node_Event>(Node))
			{
				Event->FunctionFlags &= ClearMask;
				Event->FunctionFlags |= Specifier;
			}
		}

		uint32 CurrentFlagsOf(UK2Node_EditablePinBase* Node)
		{
			if (UK2Node_FunctionEntry* Entry = Cast<UK2Node_FunctionEntry>(Node))
			{
				return (uint32)Entry->GetExtraFlags();
			}
			if (UK2Node_CustomEvent* Event = Cast<UK2Node_CustomEvent>(Node))
			{
				return Event->FunctionFlags;
			}
			return 0;
		}

		// What the ENGINE will actually act on, as opposed to the raw stored word.
		// UK2Node_CustomEvent::GetNetFlags() applies two corrections the raw field does not:
		// an OVERRIDE inherits the parent's net flags, and any mode bit without FUNC_Net is zeroed.
		// Reading the raw word would let the response claim "multicast" for a node the compiler
		// treats as not replicated.
		uint32 EffectiveFlagsOf(UK2Node_EditablePinBase* Node)
		{
			if (UK2Node_CustomEvent* Event = Cast<UK2Node_CustomEvent>(Node))
			{
				const uint32 NonNet = Event->FunctionFlags & ~((uint32)FUNC_NetFuncFlags);
				return NonNet | Event->GetNetFlags();
			}
			return CurrentFlagsOf(Node);
		}

		TSharedRef<FJsonObject> SerializeFunctionFlags(uint32 F)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			// Mode is only meaningful WITH FUNC_Net — mirrors the engine's own sanitize, so a stray
			// mode bit can't read back as replicated when the compiler will discard it.
			const TCHAR* Mode = TEXT("none");
			if (F & FUNC_Net)
			{
				if      (F & FUNC_NetMulticast) { Mode = TEXT("multicast"); }
				else if (F & FUNC_NetServer)    { Mode = TEXT("server"); }
				else if (F & FUNC_NetClient)    { Mode = TEXT("client"); }
			}
			J->SetStringField(TEXT("replicates"), Mode);
			J->SetBoolField(TEXT("net"), (F & FUNC_Net) != 0);
			J->SetBoolField(TEXT("reliable"), (F & FUNC_NetReliable) != 0);
			// Report the access word through the mask so a corrupt two-bit combination surfaces as
			// "invalid" instead of being masked by an if/else chain that reports the first bit it sees.
			const uint32 AccessBits = F & (uint32)FUNC_AccessSpecifiers;
			const TCHAR* AccessStr =
				(AccessBits == FUNC_Public)    ? TEXT("public")    :
				(AccessBits == FUNC_Protected) ? TEXT("protected") :
				(AccessBits == FUNC_Private)   ? TEXT("private")   :
				(AccessBits == 0)              ? TEXT("unspecified") : TEXT("invalid");
			J->SetStringField(TEXT("access"), AccessStr);
			J->SetBoolField(TEXT("pure"), (F & FUNC_BlueprintPure) != 0);
			J->SetBoolField(TEXT("isConst"), (F & FUNC_Const) != 0);
			J->SetBoolField(TEXT("static"), (F & FUNC_Static) != 0);
			J->SetBoolField(TEXT("authorityOnly"), (F & FUNC_BlueprintAuthorityOnly) != 0);
			return J;
		}
	}

	void H_set_function_flags(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// All THREE selector spellings are live (nodeGuid+aliases, graphId, blueprintId+function), and
		// 'const'/'isConst' are both read by the JHasAny/JBoolAny pair below — server.py sends isConst.
		// The read-only members of the response's flags object (net, static, authorityOnly) get notes
		// because a caller doing read-modify-write on that object would otherwise echo them straight back.
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("graphId"),
			  TEXT("function"), TEXT("functionName"), TEXT("name"),
			  TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"),
			  TEXT("replicates"), TEXT("reliable"), TEXT("access"),
			  TEXT("pure"), TEXT("const"), TEXT("isConst"), TEXT("callInEditor"),
			  TEXT("category"), TEXT("tooltip"), TEXT("keywords") },
			TEXT("target by nodeGuid (aliases: node, guid, nodeId), OR graphId, OR blueprintId (alias: path) + function (aliases: functionName, name); ")
			TEXT("flags: replicates (none|multicast|server|client), reliable, access (public|protected|private), pure, const (alias: isConst), callInEditor, category, tooltip, keywords"),
			{ { TEXT("replication"),   TEXT("spell it replicates (none | multicast | server | client)") },
			  { TEXT("net"),           TEXT("read-only in the response - set the mode with replicates; FUNC_Net is derived from it and cannot be set on its own") },
			  { TEXT("static"),        TEXT("read-only in the response - a Blueprint function's static-ness is not editable here") },
			  { TEXT("authorityOnly"), TEXT("read-only in the response - not settable through this endpoint") },
			  { TEXT("flags"),         TEXT("pass each flag as a TOP-LEVEL key (replicates, reliable, access, pure, const, callInEditor, category, tooltip, keywords); the response's 'flags' object is read-back only") },
			  { TEXT("event"),         TEXT("address a custom event by nodeGuid (aliases: node, guid, nodeId); a function graph by graphId or blueprintId + function") } }))
		{
			return;
		}

		// --- Resolve the target: a custom-event node, or a function graph's entry node ---------
		UBlueprint* Blueprint = nullptr;
		UK2Node_EditablePinBase* Target = nullptr;
		FString Kind;
		FString TargetName;

		// "nodeId" is accepted by ResolveNodeField too — the selector must know every spelling or a
		// caller using it silently falls through to the function-graph branch.
		const FString NodeGuid = JStrAny(In, { TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId") });
		if (!NodeGuid.IsEmpty())
		{
			UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
			if (!Node)
			{
				return;
			}
			// Require one of the two CONCRETE types. UK2Node_EditablePinBase alone is far too loose:
			// FunctionResult, Tunnel, Composite, MacroInstance and plain (non-custom) Event all derive
			// from it, would pass the cast, then match neither branch below — writing nothing while
			// still reporting ok. Fail loudly instead of silently no-op'ing.
			const bool bIsCustomEvent   = Node->IsA<UK2Node_CustomEvent>();
			const bool bIsFunctionEntry = Node->IsA<UK2Node_FunctionEntry>();
			if (!bIsCustomEvent && !bIsFunctionEntry)
			{
				Fail(Out, FString::Printf(
					TEXT("node %s is a %s — these flags apply only to a Custom Event or a function ENTRY node. ")
					TEXT("For a function graph pass graphId or blueprintId+function instead."),
					*NodeGuid, *Node->GetClass()->GetName()));
				return;
			}
			Target = CastChecked<UK2Node_EditablePinBase>(Node);
			Blueprint = FBlueprintEditorUtils::FindBlueprintForNode(Node);
			Kind = bIsCustomEvent ? TEXT("customEvent") : TEXT("function");
			TargetName = Node->GetNodeTitle(ENodeTitleType::ListView).ToString();
		}
		else
		{
			// Address a function graph by graphId, or by blueprintId + function name.
			UEdGraph* Graph = nullptr;
			const FString GraphId = JStr(In, TEXT("graphId"));
			if (!GraphId.IsEmpty())
			{
				Graph = ResolveGraphField(In, Out, Blueprint);
				if (!Graph)
				{
					return;
				}
			}
			else
			{
				Blueprint = ResolveBlueprintField(In, Out);
				if (!Blueprint)
				{
					return;
				}
				const FString FunctionName = JStrAny(In, { TEXT("function"), TEXT("functionName"), TEXT("name") });
				if (FunctionName.IsEmpty())
				{
					Fail(Out, TEXT("supply one of: nodeGuid (a custom event), graphId, or blueprintId + function"));
					return;
				}
				for (UEdGraph* G : Blueprint->FunctionGraphs)
				{
					if (G && G->GetName() == FunctionName) { Graph = G; break; }
				}
				if (!Graph)
				{
					Fail(Out, FString::Printf(TEXT("function graph '%s' not found in %s"), *FunctionName, *Blueprint->GetName()));
					return;
				}
			}

			TArray<UK2Node_FunctionEntry*> Entries;
			Graph->GetNodesOfClass(Entries);
			if (Entries.Num() == 0)
			{
				Fail(Out, FString::Printf(TEXT("graph '%s' has no function entry node (is it an event graph? address custom events by nodeGuid)"), *Graph->GetName()));
				return;
			}
			Target = Entries[0];
			Kind = TEXT("function");
			TargetName = Graph->GetName();
		}

		if (!Blueprint)
		{
			Fail(Out, TEXT("could not resolve the owning blueprint"));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Warnings;

		// A custom event that OVERRIDES a parent event takes its net flags from the SUPER function —
		// the editor disables the whole replication row for exactly this case
		// ("Cannot alter a custom-event's replication settings when it overrides an event declared
		// in a parent."). Writing them here would look like it worked and change nothing.
		if (UK2Node_CustomEvent* AsEvent = Cast<UK2Node_CustomEvent>(Target))
		{
			if (AsEvent->IsOverride() && JHasAny(In, { TEXT("replicates"), TEXT("reliable") }))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' overrides a parent event, so its replication settings come from the parent and cannot be changed here (same rule as the Details panel). Change them on the declaring class."),
					*TargetName));
				return;
			}
		}

		// Net changes need a full compile afterwards, so the mutations go in their OWN tight
		// transaction and the compile happens after it closes (set_function_flags is registered in
		// IsSelfManagedEndpoint, so RunEndpoint does not wrap us).
		const bool bNetTouched = JHasAny(In, { TEXT("replicates"), TEXT("reliable") });
		bool bTouched = false;
		{
		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "SetFunctionFlags", "Mif Bridge: set_function_flags"));
		Blueprint->Modify();

		// --- Replication mode --------------------------------------------------------
		if (In->HasField(TEXT("replicates")))
		{
			const FString Mode = JStr(In, TEXT("replicates")).ToLower();
			uint32 NetFlags = 0;
			if      (Mode == TEXT("none") || Mode == TEXT("notreplicated") || Mode.IsEmpty()) { NetFlags = 0; }
			else if (Mode == TEXT("multicast"))                                { NetFlags = FUNC_NetMulticast; }
			else if (Mode == TEXT("server") || Mode == TEXT("runonserver"))    { NetFlags = FUNC_NetServer; }
			else if (Mode == TEXT("client") || Mode == TEXT("runonclient")
				  || Mode == TEXT("owningclient"))                             { NetFlags = FUNC_NetClient; }
			else
			{
				Fail(Out, FString::Printf(
					TEXT("unknown replicates value '%s' (expected: none | multicast | server | client)"), *Mode));
				return;
			}

			// An RPC only does anything on a replicated Actor. Warn rather than silently flipping
			// bReplicates on the caller's behalf.
			if (NetFlags != 0)
			{
				if (!Blueprint->ParentClass || !Blueprint->ParentClass->IsChildOf(AActor::StaticClass()))
				{
					Warnings.Add(MakeShared<FJsonValueString>(
						TEXT("RPCs only work on Actors (or their components); this blueprint's parent is not an AActor subclass, so the flag will have no effect")));
				}
				else if (AActor* CDO = Blueprint->GeneratedClass ? Cast<AActor>(Blueprint->GeneratedClass->GetDefaultObject()) : nullptr)
				{
					if (!CDO->GetIsReplicated())
					{
						Warnings.Add(MakeShared<FJsonValueString>(
							TEXT("owning Actor has bReplicates=false — set it with set_property {propertyPath:\"bReplicates\", value:\"True\"} or the RPC will never be sent")));
					}
				}
			}

			ApplyNetFlags(Target, NetFlags);
			bTouched = true;
		}

		// --- Reliable ----------------------------------------------------------------
		if (In->HasField(TEXT("reliable")))
		{
			const bool bReliable = JBool(In, TEXT("reliable"));
			// Reliable is only meaningful on a replicated function — the editor greys the checkbox
			// out unless FUNC_Net is set (CanSetReliabilityProperty).
			if (bReliable && (CurrentFlagsOf(Target) & FUNC_Net) == 0)
			{
				Warnings.Add(MakeShared<FJsonValueString>(
					TEXT("reliable=true has no effect on a non-replicated function — pass replicates=multicast|server|client as well")));
			}
			ApplyFlagBit(Target, FUNC_NetReliable, bReliable);
			bTouched = true;
		}

		// --- Access specifier ---------------------------------------------------------
		if (In->HasField(TEXT("access")))
		{
			const FString Access = JStr(In, TEXT("access")).ToLower();
			int32 Specifier = 0;
			if      (Access == TEXT("public"))    { Specifier = FUNC_Public; }
			else if (Access == TEXT("protected")) { Specifier = FUNC_Protected; }
			else if (Access == TEXT("private"))   { Specifier = FUNC_Private; }
			else
			{
				Fail(Out, FString::Printf(TEXT("unknown access '%s' (expected: public | protected | private)"), *Access));
				return;
			}
			// Clear the WHOLE access mask, then set exactly one — mirroring
			// FBlueprintGraphActionDetails::OnAccessSpecifierSelected. Both node kinds are BORN with
			// FUNC_Public set (K2Node_CustomEvent.cpp ctor; BlueprintEditorUtils.h ExtraFunctionFlags),
			// so merely setting FUNC_Private would leave Public|Private — an invalid two-bit access
			// word that makes the compiler emit "Wrong access specifier" and the panel show "Error".
			ApplyAccessSpecifier(Target, Specifier);
			bTouched = true;
		}

		// --- Pure / const / CallInEditor ----------------------------------------------
		// Both are function-graph concepts; the Details panel hides them for events
		// (IsPureFunctionVisible / IsConstFunctionVisible both gate on Cast<UK2Node_FunctionEntry>).
		// Writing them onto a custom event would set bits nothing ever reads.
		if (In->HasField(TEXT("pure")))
		{
			if (!Target->IsA<UK2Node_FunctionEntry>())
			{
				Fail(Out, TEXT("'pure' applies to function graphs only — a custom event is never pure"));
				return;
			}
			const bool bPure = JBool(In, TEXT("pure"));
			if (bPure && (CurrentFlagsOf(Target) & FUNC_Net) != 0)
			{
				// Deliberately precise: the Blueprint compiler does NOT reject pure+RPC (there is no
				// such check anywhere in Editor/KismetCompiler). What actually happens is worse —
				// it compiles, then the call is routed by network callspace and its return value is
				// zeroed whenever it executes remotely (ScriptCore.cpp ProcessInternal/ClearReturnValue).
				Warnings.Add(MakeShared<FJsonValueString>(
					TEXT("pure + RPC is not rejected by the compiler, but the return value is zeroed whenever the call executes remotely — you almost certainly want one or the other")));
			}
			ApplyFlagBit(Target, FUNC_BlueprintPure, bPure);
			bTouched = true;
		}
		if (JHasAny(In, { TEXT("const"), TEXT("isConst") }))
		{
			if (!Target->IsA<UK2Node_FunctionEntry>())
			{
				Fail(Out, TEXT("'const' applies to function graphs only — a custom event has no const concept"));
				return;
			}
			ApplyFlagBit(Target, FUNC_Const, JBoolAny(In, { TEXT("const"), TEXT("isConst") }));
			bTouched = true;
		}
		if (In->HasField(TEXT("callInEditor")))
		{
			// CallInEditor is metadata on the entry node, not a FUNC_ flag.
			if (UK2Node_FunctionEntry* Entry = Cast<UK2Node_FunctionEntry>(Target))
			{
				Entry->Modify();
				Entry->MetaData.bCallInEditor = JBool(In, TEXT("callInEditor"));
				bTouched = true;
			}
			else
			{
				Warnings.Add(MakeShared<FJsonValueString>(TEXT("callInEditor applies to functions, not custom events — ignored")));
			}
		}

		// --- Category / tooltip / keywords (entry-node metadata) -----------------------
		if (UK2Node_FunctionEntry* Entry = Cast<UK2Node_FunctionEntry>(Target))
		{
			if (In->HasField(TEXT("category")))
			{
				Entry->Modify();
				Entry->MetaData.Category = FText::FromString(JStr(In, TEXT("category")));
				bTouched = true;
			}
			if (In->HasField(TEXT("tooltip")))
			{
				Entry->Modify();
				Entry->MetaData.ToolTip = FText::FromString(JStr(In, TEXT("tooltip")));
				bTouched = true;
			}
			if (In->HasField(TEXT("keywords")))
			{
				Entry->Modify();
				Entry->MetaData.Keywords = FText::FromString(JStr(In, TEXT("keywords")));
				bTouched = true;
			}
		}

		if (!bTouched)
		{
			Fail(Out, TEXT("no flags supplied — pass at least one of: replicates, reliable, access, pure, const, callInEditor, category, tooltip, keywords"));
			return;
		}

		MarkStructural(Blueprint);
		}   // tight transaction closes here — a full compile must never be captured by it

		// A skeleton regen is enough for access/pure/const/metadata, but NOT for the NET flags: the
		// replication machinery (SetUpRuntimeReplicationData, the class NetFields list) is only built
		// by a full compile, and existing call sites keep their EX_LocalFinalFunction bytecode until
		// they are recompiled too. So a replication change that only skeleton-regens looks applied and
		// does nothing at runtime. Compile OUTSIDE the transaction (reinstancing + Ctrl-Z = dead CDO).
		if (bNetTouched)
		{
			TSharedRef<FJsonObject> CompileOut = MakeShared<FJsonObject>();
			CompileBlueprintInto(Blueprint, CompileOut);
			Out->SetObjectField(TEXT("compile"), CompileOut);
			// Callers of this function in OTHER blueprints keep stale call-site bytecode until they
			// are themselves recompiled — say so rather than letting it be discovered at runtime.
			Warnings.Add(MakeShared<FJsonValueString>(
				TEXT("replication changed: other blueprints that CALL this function must be recompiled before their call sites route over the network")));
		}

		Out->SetStringField(TEXT("target"), TargetName);
		Out->SetStringField(TEXT("kind"), Kind);
		// Read back the EFFECTIVE state (GetNetFlags applies the engine's own sanitize + override
		// inheritance), not the raw word, so the response can't claim "multicast" for something the
		// compiler will treat as not replicated.
		Out->SetObjectField(TEXT("flags"), SerializeFunctionFlags(EffectiveFlagsOf(Target)));
		Out->SetArrayField(TEXT("warnings"), Warnings);

		// THE FLAG IS AUTHORED HERE; IT REACHES THE GENERATED CLASS ON COMPILE. These flags live on the
		// function's entry node, and `flags` above is read back from there - so it is true immediately.
		// What executes, and what describe_class reflects, is the GENERATED class, and that does not
		// change until the blueprint is compiled.
		//
		// Without saying so, the two disagree in a way that reads as a failed write: set pure:true,
		// then ask describe_class, and it still answers isPure:false. Measured - false before the set,
		// false after the set, true after a compile. A caller comparing the two would reasonably
		// conclude the set did not take.
		//
		// Reported the same way the widget-tree endpoints report it, since it is the same situation:
		// authored now, effective after a compile.
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
		UE_LOG(LogMifBridge, Log, TEXT("set_function_flags: %s (%s)"), *TargetName, *Kind);
	}

	// Mint a fresh Blueprint asset. The bridge was built to EDIT existing BPs; this is the one thing it couldn't do,
	// and it's what the reconstructor testbed needs (author a known graph → cook → reconstruct → diff = ground truth).
	// SELF-MANAGED: CreateBlueprint + CompileBlueprint reinstance a class, which must never sit inside RunEndpoint's
	// transaction (a later Ctrl-Z would restore a dead CDO and crash) — registered in IsSelfManagedEndpoint.
	//   in:  { path: "/Game/MifTestbed/BP_Foo", parentClass?: "Actor" (default),
	//          blueprintType?: "Normal" | FunctionLibrary | Interface | MacroLibrary | WidgetBlueprint }
	//   out: { blueprintId, class, parentClass, eventGraphId? }
	//
	// This line used to advertise `overwrite?: false`, which NO line of the handler reads, and to omit
	// `blueprintType`, which the handler does read (and whose absence from the contract caused PM-002).
	// Both halves are true now, and `overwrite` is a named refusal in the guard below rather than a
	// silently-dropped key that leaves the caller staring at "a Blueprint already exists" wondering why
	// the flag they passed did nothing — the third instance of that class this session after
	// create_material_instance.textures and duplicate_actors.rotationOffset.
	void H_create_blueprint(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("parentClass"), TEXT("blueprintType"), TEXT("skeleton"), TEXT("targetSkeleton") },
			TEXT("path (must start with /Game/), parentClass (default \"Actor\"), blueprintType ")
			TEXT("(Normal | FunctionLibrary | Interface | MacroLibrary | WidgetBlueprint | AnimBlueprint), ")
			TEXT("skeleton (alias targetSkeleton) - REQUIRED for AnimBlueprint"),
			{ { TEXT("overwrite"), TEXT("NOT supported — this endpoint refuses to clobber an existing asset. delete_asset the old one first, or pick a new path") },
			  { TEXT("name"), TEXT("the asset name is the last segment of path") },
			  { TEXT("parent"), TEXT("the base class parameter is called parentClass") } }))
		{
			return;
		}
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
		// blueprintType: "Normal" (default), "FunctionLibrary", "Interface", "MacroLibrary", "WidgetBlueprint".
		// Library/interface types are NOT "blueprintable of a parent class" (CanCreateBlueprintOfClass rejects them),
		// so they take a fixed base + the matching EBlueprintType and bypass that check.
		const FString BpTypeStr = JStr(In, TEXT("blueprintType"), TEXT("Normal"));

		// REJECT anything not on the list. The chain below used to fall through to a plain Blueprint for
		// ANY unrecognised string, so blueprintType:"Widget" (a very natural guess for "WidgetBlueprint")
		// silently produced a plain UBlueprint parented to UserWidget: no WidgetTree, no designer, and
		// every widget endpoint failing afterwards on an asset that looked correct in the content browser.
		{
			static const TCHAR* const ValidTypes[] = {
				TEXT("Normal"), TEXT("FunctionLibrary"), TEXT("Interface"), TEXT("MacroLibrary"), TEXT("WidgetBlueprint"), TEXT("AnimBlueprint")
			};
			bool bKnownType = false;
			for (const TCHAR* Valid : ValidTypes)
			{
				if (BpTypeStr.Equals(Valid, ESearchCase::IgnoreCase)) { bKnownType = true; break; }
			}
			if (!bKnownType)
			{
				Fail(Out, FString::Printf(
					TEXT("unknown blueprintType '%s'. Valid values: Normal (default), FunctionLibrary, Interface, ")
					TEXT("MacroLibrary, WidgetBlueprint, AnimBlueprint. (Note it is \"WidgetBlueprint\", not \"Widget\".)"), *BpTypeStr));
				return;
			}
		}

		EBlueprintType BpType = BPTYPE_Normal;
		UClass* ParentClass = nullptr;
		bool bLibraryLike = false;
		bool bWidget      = false;   // WidgetBlueprint via blueprintType=WidgetBlueprint
		bool bAnim        = false;   // AnimBlueprint  via blueprintType=AnimBlueprint
		USkeleton* AnimSkeleton = nullptr;
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
		else if (BpTypeStr.Equals(TEXT("AnimBlueprint"), ESearchCase::IgnoreCase))
		{
			// An Animation Blueprint is a UAnimBlueprint, not a UBlueprint parented to UAnimInstance.
			// The distinction is not cosmetic: only the UAnimBlueprint class type gets an AnimGraph,
			// and a TargetSkeleton is mandatory - without one the asset has no bone space to compile
			// against. Creating it the "obvious" way produced an EventGraph-only asset that reported
			// success and could never play an animation.
			BpType = BPTYPE_Normal;
			bAnim  = true;
			const FString AnimParent = JStr(In, TEXT("parentClass"), TEXT("AnimInstance"));
			ParentClass = ResolveClass(AnimParent, nullptr);
			if (ParentClass && !ParentClass->IsChildOf(UAnimInstance::StaticClass()))
			{
				Fail(Out, FString::Printf(TEXT("parentClass '%s' is not a UAnimInstance subclass"), *ParentClass->GetName()));
				return;
			}
			const FString SkelPath = JStrAny(In, { TEXT("skeleton"), TEXT("targetSkeleton") });
			if (SkelPath.IsEmpty())
			{
				Fail(Out, TEXT("skeleton is required for blueprintType=AnimBlueprint - an Animation Blueprint cannot exist without a TargetSkeleton. Pass the USkeleton asset path (find one with find_assets class=Skeleton)."));
				return;
			}
			AnimSkeleton = LoadObject<USkeleton>(nullptr, *SkelPath);
			if (!AnimSkeleton)
			{
				Fail(Out, FString::Printf(TEXT("could not load USkeleton '%s'"), *SkelPath));
				return;
			}
		}
		else
		{
			ParentClass = ResolveClass(ParentName, nullptr);
			// Catch the near-miss that silently produced a graph-less asset: a plain Blueprint parented
			// to UAnimInstance looks like an Animation Blueprint and is not one.
			if (ParentClass && ParentClass->IsChildOf(UAnimInstance::StaticClass()))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is a UAnimInstance, so this would create a plain Blueprint with an EventGraph and NO AnimGraph - it could never play an animation. Pass blueprintType=AnimBlueprint together with skeleton=<USkeleton path>."),
					*ParentClass->GetName()));
				return;
			}
			// THE SAME NEAR-MISS, one class family over, and it was missing until 2026-08-30. A plain
			// Blueprint parented to UUserWidget looks exactly like a Widget Blueprint and is not one:
			// UWidgetBlueprint is a distinct blueprint class with a WidgetTree, and without it every
			// widget endpoint refuses the asset ("not a Widget Blueprint"). It was found by calling
			// create_blueprint{parentClass:"UserWidget"} while trying to test something else - it
			// answered ok:true and handed back an asset nothing could use, with no warning at all.
			// The AnimInstance guard above exists for precisely this failure and simply had no
			// widget counterpart.
			if (ParentClass && ParentClass->IsChildOf(UUserWidget::StaticClass()))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is a UUserWidget, so this would create a plain Blueprint with an EventGraph and NO WidgetTree - every widget endpoint would refuse it with \"not a Widget Blueprint\". Pass blueprintType=WidgetBlueprint (parentClass is optional there and defaults to UserWidget). NOTHING was created."),
					*ParentClass->GetName()));
				return;
			}
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
		// A DELETED OBJECT IS NOT AN EXISTING ASSET (docs/06 issue 28). delete_asset ->
		// ObjectTools::DeleteAssets unregisters the asset and clears RF_Public|RF_Standalone, but
		// the UObject stays resident until a GC pass. This lookup found that corpse and refused,
		// while delete_asset - which consults the REGISTRY - answered "no asset found at package".
		// So an agent told to "delete it first" was then told there was nothing to delete, and the
		// path stayed unusable for the rest of the editor session with no way out from the bridge.
		// Reproduced live on 2026-08-31 before this was touched. IsValid() is false for a garbage
		// object, which makes the two endpoints agree on what exists.
		if (IsValid(StaticLoadObject(UBlueprint::StaticClass(), nullptr, *ObjectPath, nullptr, LOAD_NoWarn | LOAD_Quiet)))
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
		else if (bAnim)
		{
			BpClass  = UAnimBlueprint::StaticClass();
			GenClass = UAnimBlueprintGeneratedClass::StaticClass();
		}
		UBlueprint* NewBP = FKismetEditorUtilities::CreateBlueprint(
			ParentClass, Package, FName(*AssetName), BpType,
			BpClass, GenClass, TEXT("MifBridge"));
		if (!NewBP)
		{
			Fail(Out, TEXT("CreateBlueprint returned null"));
			return;
		}

		if (bAnim)
		{
			// Set BEFORE the compile below: the AnimGraph is compiled against the skeleton, and a
			// null TargetSkeleton at compile time produces an asset the editor refuses to open.
			UAnimBlueprint* ABP = CastChecked<UAnimBlueprint>(NewBP);
			ABP->TargetSkeleton = AnimSkeleton;
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

	// Reparent an EXISTING Blueprint to a new parent class - the programmatic equivalent of the
	// Blueprint editor's Class Settings > "Parent Class" picker (FBlueprintEditor::
	// ReparentBlueprint_NewParentChosen). create_blueprint above can only MINT a brand-new
	// Blueprint; there was previously no way to change an EXISTING one's parent short of
	// recreating it from scratch and manually re-porting every graph/variable/struct member by
	// hand, which is exactly the wall this was added to get past.
	// SELF-MANAGED: reparenting forces a full class reinstance (the old CDO/instances are
	// discarded), which must never sit inside RunEndpoint's transaction - see IsSelfManagedEndpoint
	// and the identical justification on H_create_blueprint above.
	//   in:  { blueprintId (alias: path), newParentClass (alias: parentClass) }
	//   out: { blueprintId, oldParentClass, newParentClass, changed, eventGraphId? }
	void H_reparent_blueprint(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("newParentClass"), TEXT("parentClass") },
			TEXT("blueprintId (alias: path), newParentClass (alias: parentClass)"),
			{ { TEXT("newParent"), TEXT("spell it newParentClass (alias parentClass)") },
			  { TEXT("class"), TEXT("the new parent class parameter is called newParentClass") } }))
		{
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}

		FString NewParentName = JStr(In, TEXT("newParentClass"));
		if (NewParentName.IsEmpty())
		{
			NewParentName = JStr(In, TEXT("parentClass"));
		}
		if (NewParentName.IsEmpty())
		{
			Fail(Out, TEXT("newParentClass required"));
			return;
		}

		UClass* NewParentClass = ResolveClass(NewParentName, nullptr);
		if (!NewParentClass)
		{
			Fail(Out, FString::Printf(TEXT("parent class '%s' not found"), *NewParentName));
			return;
		}

		UClass* OldParentClass = Blueprint->ParentClass;
		if (NewParentClass == OldParentClass)
		{
			Out->SetStringField(TEXT("blueprintId"), Blueprint->GetPathName());
			Out->SetStringField(TEXT("oldParentClass"), OldParentClass ? OldParentClass->GetPathName() : TEXT("None"));
			Out->SetStringField(TEXT("newParentClass"), NewParentClass->GetPathName());
			Out->SetBoolField(TEXT("changed"), false);
			return;
		}

		// Refuse a cycle: reparenting to your own generated class (or a child of it) would make the
		// class its own ancestor.
		if (Blueprint->GeneratedClass && NewParentClass->IsChildOf(Blueprint->GeneratedClass))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is this Blueprint's own generated class or a child of it - reparenting to it would create a cycle"),
				*NewParentClass->GetPathName()));
			return;
		}

		if (!FKismetEditorUtilities::CanCreateBlueprintOfClass(NewParentClass))
		{
			Fail(Out, FString::Printf(TEXT("cannot reparent to '%s' - not a Blueprintable class"), *NewParentClass->GetName()));
			return;
		}

		// Mirrors FBlueprintEditor::ReparentBlueprint_NewParentChosen (BlueprintEditor.cpp) minus the
		// interactive warning dialogs, sparse-class-data conform, and open-editor-instance bookkeeping
		// (namespace imports, the subobject-editor tree refresh) that only matter for a live
		// Blueprint Editor tab. Mutations go in their OWN tight transaction and the compile happens
		// AFTER it closes - same idiom as H_set_function_flags earlier in this file.
		{
			FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "ReparentBlueprint", "Mif Bridge: reparent_blueprint"));
			Blueprint->Modify();
			if (USimpleConstructionScript* SCS = Blueprint->SimpleConstructionScript)
			{
				SCS->Modify();
				for (USCS_Node* Node : SCS->GetAllNodes())
				{
					Node->Modify();
				}
			}

			Blueprint->ParentClass = NewParentClass;

			FBlueprintEditorUtils::RefreshAllNodes(Blueprint);
			FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
		}   // transaction closes here, BEFORE the compile

		FKismetEditorUtilities::CompileBlueprint(Blueprint);   // outside any transaction (self-managed)
		Blueprint->GetOutermost()->MarkPackageDirty();

		// READ THE COMPILE VERDICT. CompileBlueprint is void and this discarded it, so a reparent that
		// BROKE the blueprint answered changed:true with nothing to suggest anything was wrong.
		// Reparenting is the operation most likely to break one: the new parent may not declare a
		// function an override implements, may clash with a variable name, or may drop a component the
		// graphs reference. Blueprint->Status carries the answer and cost nothing to read.
		//
		// Reported, NOT failed: the reparent really did happen, so ok:false would be a lie in the
		// other direction and would imply the blueprint was left alone. The caller needs to know both
		// things - it changed, and it no longer compiles.
		const EBlueprintStatus PostStatus = Blueprint->Status;
		const TCHAR* StatusText =
			PostStatus == BS_UpToDate             ? TEXT("upToDate")             :
			PostStatus == BS_UpToDateWithWarnings ? TEXT("upToDateWithWarnings") :
			PostStatus == BS_Error                ? TEXT("error")                :
			PostStatus == BS_Dirty                ? TEXT("dirty")                :
			PostStatus == BS_BeingCreated         ? TEXT("beingCreated")         : TEXT("unknown");
		Out->SetStringField(TEXT("compileStatus"), StatusText);
		Out->SetBoolField(TEXT("compiled"), PostStatus != BS_Error);
		if (PostStatus == BS_Error)
		{
			Out->SetStringField(TEXT("compileNote"), FString::Printf(
				TEXT("the reparent to '%s' WAS applied, and the blueprint no longer compiles. That is "
					 "usually an override whose function the new parent does not declare, or a variable "
					 "or component name that now clashes. Call compile for the per-node errors, or "
					 "reparent back to '%s' to undo it."),
				*NewParentClass->GetName(),
				OldParentClass ? *OldParentClass->GetName() : TEXT("the previous parent")));
		}

		Out->SetStringField(TEXT("blueprintId"), Blueprint->GetPathName());
		Out->SetStringField(TEXT("oldParentClass"), OldParentClass ? OldParentClass->GetPathName() : TEXT("None"));
		Out->SetStringField(TEXT("newParentClass"), NewParentClass->GetPathName());
		Out->SetBoolField(TEXT("changed"), true);
		if (UEdGraph* EventGraph = FBlueprintEditorUtils::FindEventGraph(Blueprint))
		{
			Out->SetStringField(TEXT("eventGraphId"), GraphIdOf(Blueprint, EventGraph));
		}
		UE_LOG(LogMifBridge, Log, TEXT("reparent_blueprint: %s (%s -> %s)"), *Blueprint->GetPathName(),
			OldParentClass ? *OldParentClass->GetName() : TEXT("None"), *NewParentClass->GetName());
	}

	// =======================================================================
	// create_macro - filling a container this plugin already shipped empty
	// =======================================================================
	//
	// THIS ONE IS A DEAD END WE MADE. create_blueprint accepts blueprintType:"MacroLibrary" and
	// produces a Blueprint Macro Library, and nothing in the plugin could then put a macro in it -
	// so an agent could create a container it had no way to fill. Meanwhile add_macro_instance,
	// list_graphs and ResolveMacroGraph all CONSUME macros. Read half and consumer half both
	// present, author half absent.
	//
	// create_function is a real workaround for most reusable logic, which is why this is medium
	// rather than high - but a macro is not a function: it inlines, it can carry multiple exec
	// paths in and out, and a Macro Library is the only place to share one across Blueprints.
	//
	// TWO IMPLEMENTATION CORRECTIONS FROM THE VETTING, both checked against the engine source:
	//
	// 1. DO NOT CALL CreateMacroGraphTerminators. FBlueprintEditorUtils::AddMacroGraph already does
	//    it (BlueprintEditorUtils.cpp:2310), and calling it again would add a second pair of tunnel
	//    nodes to the same graph - which compiles into nonsense rather than failing loudly.
	//
	// 2. THE TWO TUNNELS ARE TOLD APART BY THEIR FLAGS, not by order or by name. A macro's entry
	//    and exit are both UK2Node_Tunnel; the entry has bCanHaveOutputs (it feeds the graph) and
	//    the exit has bCanHaveInputs (it collects from it). So an INPUT to the macro is created as
	//    EGPD_Output on the entry tunnel, and an OUTPUT as EGPD_Input on the exit - the same
	//    inversion create_function has on its entry node, and the same one that reads as a bug
	//    every time until you say it out loud.
	//
	// PIN NAMES ARE ECHOED FROM THE ENGINE. CreateUserDefinedPin is called with bUseUniqueName
	// true, so it RENAMES on a collision and returns the pin it actually made. create_function
	// learned that the hard way - a caller who names a parameter after an existing pin gets a
	// different name and no way to find out, then fails later wiring the name they asked for.

	void H_create_macro(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		TArray<TSharedPtr<FJsonValue>> ActualInputNames;
		TArray<TSharedPtr<FJsonValue>> ActualOutputNames;
		TArray<FString> RenamedPins;

		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("inputs"), TEXT("outputs") },
			TEXT("blueprintId (alias: path) - a Blueprint or a Blueprint Macro Library; name; ")
			TEXT("inputs?[{name,type,...}]; outputs?"),
			{ { TEXT("pure"), TEXT("macros have no pure/impure distinction - that is create_function. "
								   "A macro inlines wherever it is used") },
			  { TEXT("category"), TEXT("not set here; set_property on the graph's metadata after "
									   "creation if you need one") },
			  { TEXT("override"), TEXT("a macro cannot override anything - use add_override_event "
									   "for a parent event, or create_function") } }))
		{
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint) { return; }

		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required - the macro's name. NOTHING was created."));
			return;
		}

		// A NAME ALREADY IN USE would produce a second graph the editor shows twice, so it is
		// refused rather than uniquified: unlike a pin, a graph name is what the caller will
		// address it by afterwards.
		for (const UEdGraph* G : Blueprint->MacroGraphs)
		{
			if (G && G->GetFName() == FName(*Name))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' already has a macro named '%s'. Graph names are how you address a "
						 "macro afterwards, so this is refused rather than renamed. NOTHING was "
						 "created."), *Blueprint->GetName(), *Name));
				return;
			}
		}
		for (const UEdGraph* G : Blueprint->FunctionGraphs)
		{
			if (G && G->GetFName() == FName(*Name))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' already has a FUNCTION named '%s'. A macro and a function cannot "
						 "share a name in one Blueprint. NOTHING was created."),
					*Blueprint->GetName(), *Name));
				return;
			}
		}

		TArray<TPair<FName, FEdGraphPinType>> Inputs;
		TArray<TPair<FName, FEdGraphPinType>> Outputs;
		FString ParseError;
		if (!ParsePinSpecs(In, TEXT("inputs"), Inputs, ParseError)
			|| !ParsePinSpecs(In, TEXT("outputs"), Outputs, ParseError))
		{
			Fail(Out, ParseError);
			return;
		}

		UEdGraph* Graph = nullptr;
		{
			FScopedTransaction Transaction(
				NSLOCTEXT("MifBridge", "CreateMacro", "Mif Bridge: create_macro"));

			Graph = FBlueprintEditorUtils::CreateNewGraph(
				Blueprint, FName(*Name), UEdGraph::StaticClass(),
				UEdGraphSchema_K2::StaticClass());
			if (!Graph)
			{
				Fail(Out, TEXT("CreateNewGraph returned null. NOTHING was created."));
				return;
			}
			// AddMacroGraph CALLS CreateMacroGraphTerminators ITSELF - calling it again here would
			// give the graph a second pair of tunnels.
			FBlueprintEditorUtils::AddMacroGraph(Blueprint, Graph, /*bIsUserCreated*/ true,
												 /*SignatureFromClass*/ nullptr);

			// THE TWO TUNNELS, told apart by their flags rather than by order. Both are
			// UK2Node_Tunnel; only the flags say which end each one is.
			UK2Node_Tunnel* EntryTunnel = nullptr;
			UK2Node_Tunnel* ExitTunnel = nullptr;
			TArray<UK2Node_Tunnel*> Tunnels;
			Graph->GetNodesOfClass(Tunnels);
			for (UK2Node_Tunnel* T : Tunnels)
			{
				if (!T) { continue; }
				if (T->bCanHaveOutputs && !EntryTunnel) { EntryTunnel = T; }
				else if (T->bCanHaveInputs && !ExitTunnel) { ExitTunnel = T; }
			}
			if (!EntryTunnel || !ExitTunnel)
			{
				Fail(Out, FString::Printf(
					TEXT("the new macro graph has %d tunnel node(s) and this needs an entry and an "
						 "exit. NOTHING usable was produced."), Tunnels.Num()));
				return;
			}

			// AN INPUT TO THE MACRO IS AN OUTPUT ON THE ENTRY TUNNEL. The entry feeds the graph, so
			// the direction is inverted from the caller's point of view - the same inversion
			// create_function has, and it reads as a bug every time until it is said out loud.
			EntryTunnel->Modify();
			for (const TPair<FName, FEdGraphPinType>& Pin : Inputs)
			{
				if (UEdGraphPin* Made = EntryTunnel->CreateUserDefinedPin(
						Pin.Key, Pin.Value, EGPD_Output, /*bUseUniqueName*/ true))
				{
					ActualInputNames.Add(MakeShared<FJsonValueString>(Made->PinName.ToString()));
					if (Made->PinName != Pin.Key)
					{
						RenamedPins.Add(FString::Printf(TEXT("%s -> %s"),
							*Pin.Key.ToString(), *Made->PinName.ToString()));
					}
				}
			}
			ExitTunnel->Modify();
			for (const TPair<FName, FEdGraphPinType>& Pin : Outputs)
			{
				if (UEdGraphPin* Made = ExitTunnel->CreateUserDefinedPin(
						Pin.Key, Pin.Value, EGPD_Input, /*bUseUniqueName*/ true))
				{
					ActualOutputNames.Add(MakeShared<FJsonValueString>(Made->PinName.ToString()));
					if (Made->PinName != Pin.Key)
					{
						RenamedPins.Add(FString::Printf(TEXT("%s -> %s"),
							*Pin.Key.ToString(), *Made->PinName.ToString()));
					}
				}
			}
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		}

		// READ BACK from the blueprint's own macro list, not from the pointer we were handed.
		bool bListed = false;
		for (const UEdGraph* G : Blueprint->MacroGraphs)
		{
			if (G == Graph) { bListed = true; break; }
		}
		if (!bListed)
		{
			Fail(Out, TEXT("the macro graph was created and the Blueprint does not list it on "
				TEXT("read-back. NOTHING usable was produced.")));
			return;
		}

		Out->SetStringField(TEXT("blueprint"), Blueprint->GetPathName());
		Out->SetStringField(TEXT("macro"), Graph->GetName());
		// The same graphId form list_graphs emits, so the caller can go straight to add_node.
		Out->SetStringField(TEXT("graphId"), GraphIdOf(Blueprint, Graph));
		Out->SetArrayField(TEXT("inputs"), ActualInputNames);
		Out->SetArrayField(TEXT("outputs"), ActualOutputNames);
		Out->SetNumberField(TEXT("macroCount"), Blueprint->MacroGraphs.Num());
		if (RenamedPins.Num() > 0)
		{
			// bUseUniqueName is TRUE, so the engine renames rather than failing - and a caller who
			// never learns the new name fails later wiring the one they asked for.
			Out->SetStringField(TEXT("renamedPins"), FString::Join(RenamedPins, TEXT(", ")));
			Out->SetStringField(TEXT("renameNote"),
				TEXT("the engine renamed these pins because the names collided with pins the tunnel "
					 "already had. The names above are the ones that exist - wire those, not the "
					 "ones you asked for."));
		}
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the Blueprint is dirty and NOT compiled or saved. Call compile when the macro's "
				 "body is built - an empty macro compiles fine, it simply does nothing."));
	}
}
