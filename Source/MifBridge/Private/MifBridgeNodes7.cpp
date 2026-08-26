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
#include "InputAction.h"
#include "InputModifiers.h"
#include "InputTriggers.h"
#include "InputMappingContext.h"                  // EnhancedInput module — UInputAction
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
	// --- list_input_mappings -------------------------------------------------
	//   in:  { path (alias: context, assetPath) - an InputMappingContext asset }
	//   out: { context, count, mappings:[{ action, actionPath, key, triggers[], modifiers[], ignored }] }
	// Which key does what. add_enhanced_input_action could PLACE the event node for an action, but
	// nothing could answer the question that comes first - what is this action even bound to, and what
	// else is bound alongside it. An InputMappingContext is where that lives in modern UE5 input, and it
	// had no coverage at all.
	//
	// Triggers and modifiers are reported by CLASS NAME rather than expanded. Their settings are plain
	// UPROPERTYs, so get_property on the modifier's object path reaches them, and expanding every one
	// here would bury the mapping itself in configuration nobody asked for.
	void H_list_input_mappings(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("context"), TEXT("assetPath") },
			TEXT("path (aliases: context, assetPath) - the /Game/... path of an InputMappingContext"),
			{ { TEXT("action"), TEXT("this lists a CONTEXT's mappings; to find one action's bindings, read them all and filter on the action field") },
			  { TEXT("player"), TEXT("this reads the ASSET, not a live player's applied contexts - those exist only during PIE") } }))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("context"), TEXT("assetPath") });
		if (Path.IsEmpty()) { Fail(Out, TEXT("path is required - an InputMappingContext asset")); return; }

		UInputMappingContext* Context = LoadObject<UInputMappingContext>(nullptr, *Path);
		if (!Context)
		{
			// The same trailing-name retry add_enhanced_input_action uses: /Game/Foo/IMC_Bar is the package,
			// /Game/Foo/IMC_Bar.IMC_Bar is the object, and callers pass both.
			const FString Name = FPaths::GetBaseFilename(Path);
			Context = LoadObject<UInputMappingContext>(nullptr, *(Path + TEXT(".") + Name));
		}
		if (!Context)
		{
			Fail(Out, FString::Printf(
				TEXT("no InputMappingContext at '%s'. find_assets {class:\"InputMappingContext\"} lists them; ")
				TEXT("an object path looks like /Game/Input/IMC_Default.IMC_Default."), *Path));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Rows;
		for (const FEnhancedActionKeyMapping& Mapping : Context->GetMappings())
		{
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("action"), Mapping.Action ? Mapping.Action->GetName() : TEXT("<none>"));
			Row->SetStringField(TEXT("actionPath"), Mapping.Action ? Mapping.Action->GetPathName() : FString());
			// FKey's display name is what a human recognises; GetFName is what code matches on. Both, since
			// a caller comparing against config wants the second and a caller reading output wants the first.
			Row->SetStringField(TEXT("key"), Mapping.Key.GetFName().ToString());
			Row->SetStringField(TEXT("keyDisplay"), Mapping.Key.GetDisplayName().ToString());
			Row->SetBoolField(TEXT("ignored"), Mapping.bShouldBeIgnored != 0);

			TArray<TSharedPtr<FJsonValue>> Trig, Mods;
			for (const TObjectPtr<UInputTrigger>& T : Mapping.Triggers)
			{
				if (T) { Trig.Add(MakeShared<FJsonValueString>(T->GetClass()->GetName())); }
			}
			for (const TObjectPtr<UInputModifier>& Mo : Mapping.Modifiers)
			{
				if (Mo) { Mods.Add(MakeShared<FJsonValueString>(Mo->GetClass()->GetName())); }
			}
			Row->SetArrayField(TEXT("triggers"), Trig);
			Row->SetArrayField(TEXT("modifiers"), Mods);
			Rows.Add(MakeShared<FJsonValueObject>(Row));
		}

		Out->SetStringField(TEXT("context"), Context->GetPathName());
		Out->SetNumberField(TEXT("count"), Rows.Num());
		Out->SetArrayField(TEXT("mappings"), Rows);
		if (Rows.Num() == 0)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("this context exists but has no mappings - nothing is bound through it."));
		}
	}
}
