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
#include "EnhancedInputModule.h"
#include "EnhancedInputLibrary.h"
#include "InputCoreTypes.h"
#include "ScopedTransaction.h"
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

	// =======================================================================
	// map_input_key / unmap_input_key - the write half of list_input_mappings
	// =======================================================================
	//
	// THE BRIDGE COULD ALREADY BUILD BOTH ENDS AND NOT CONNECT THEM. create_asset makes an
	// InputMappingContext, add_enhanced_input_action makes the IA_ event node, list_input_mappings
	// reads a context back - and nothing could put a single mapping into one. An empty IMC and a
	// disconnected input event is the whole feature except the part that makes it work.
	//
	// THE REFLECTIVE WORKAROUND SILENTLY BREAKS ON 5.7, which is what settles this as an endpoint
	// rather than a documented recipe. edit_container can append to UInputMappingContext::Mappings
	// on 5.3, and on 5.7 that array is DEPRECATED - the live data lives in
	// DefaultKeyMappings.Mappings (verified: 5.3's MapKey does Mappings.Add_GetRef, 5.7's does
	// DefaultKeyMappings.Mappings.Add_GetRef). So the reflective append lands in an array nothing
	// reads, and even list_input_mappings would not show it. A version-fragile silent no-op is not
	// something an agent can rely on across the 5.3-5.7 range this plugin targets.
	//
	// THE REBUILD IS ISSUED BY THIS ENDPOINT, NOT LEFT TO MapKey. MapKey does call
	// RequestRebuildControlMappingsUsingContext - BEFORE the Add, on both engines:
	//
	//     IEnhancedInputModule::Get().GetLibrary()->RequestRebuildControlMappingsUsingContext(this);
	//     return Mappings.Add_GetRef(...);
	//
	// so the mapping it just created is not in the state that was rebuilt. Anything already using
	// the context keeps the old mapping set until something else triggers a rebuild. The survey
	// proposed `rebuild?` as an option; it cannot be optional, and it has to come after.

	UInputMappingContext* MifResolveIMC(const TSharedRef<FJsonObject>& In,
										const TSharedRef<FJsonObject>& Out)
	{
		const FString Path = JStrAny(In, { TEXT("context"), TEXT("path"), TEXT("assetPath") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("context is required (aliases: path, assetPath) - an InputMappingContext ")
				TEXT("asset. NOTHING was changed."));
			return nullptr;
		}
		UInputMappingContext* Context = LoadObject<UInputMappingContext>(nullptr, *Path);
		if (!Context)
		{
			// The same trailing-name retry list_input_mappings uses - callers pass both spellings.
			const FString Name = FPaths::GetBaseFilename(Path);
			Context = LoadObject<UInputMappingContext>(nullptr, *(Path + TEXT(".") + Name));
		}
		if (!Context)
		{
			Fail(Out, FString::Printf(
				TEXT("no InputMappingContext at '%s'. find_assets {class:\"InputMappingContext\"} ")
				TEXT("lists them. NOTHING was changed."), *Path));
		}
		return Context;
	}

	int32 MifIMCMappingCount(const UInputMappingContext* Context)
	{
		return Context ? Context->GetMappings().Num() : 0;
	}

	void MifNoteCookedContext(UInputMappingContext* Context, const TSharedRef<FJsonObject>& Out)
	{
		// The mutation itself is safe on a cooked package - an IMC is a plain UDataAsset with no
		// editor-only payload - but it cannot be SAVED. Reported rather than left to be discovered
		// when save_package quietly does not round-trip it.
		if (IsCookedOrContainerPackage(Context->GetOutermost()))
		{
			Out->SetBoolField(TEXT("cooked"), true);
			Out->SetStringField(TEXT("cookedNote"),
				TEXT("this InputMappingContext lives in a COOKED package. The mapping was applied in "
					 "memory and works for this editor session, but save_package will not round-trip "
					 "it - mint an editable copy if it needs to persist."));
		}
	}

	void MifRebuildInputMappings(UInputMappingContext* Context)
	{
		if (UEnhancedInputLibrary* Lib = IEnhancedInputModule::Get().GetLibrary())
		{
			Lib->RequestRebuildControlMappingsUsingContext(Context);
		}
	}

	// --- map_input_key ------------------------------------------------------
	void H_map_input_key(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("context"), TEXT("path"), TEXT("assetPath"), TEXT("action"), TEXT("key") },
			TEXT("context (aliases: path, assetPath) - an InputMappingContext; action - an ")
			TEXT("InputAction asset path; key - an FKey NAME such as SpaceBar, LeftMouseButton, ")
			TEXT("Gamepad_FaceButton_Bottom"),
			{ { TEXT("triggers"), TEXT("not accepted yet - trigger and modifier classes are a ")
									TEXT("second pass; this maps action to key") },
			  { TEXT("modifiers"), TEXT("not accepted yet - see triggers") },
			  { TEXT("rebuild"), TEXT("not a parameter - the rebuild is ALWAYS issued after the ")
								 TEXT("mapping, because MapKey issues its own BEFORE adding and so ")
								 TEXT("misses the new mapping entirely") } }))
		{
			return;
		}

		UInputMappingContext* Context = MifResolveIMC(In, Out);
		if (!Context) { return; }

		const FString ActionPath = JStr(In, TEXT("action"));
		if (ActionPath.IsEmpty())
		{
			Fail(Out, TEXT("action is required - an InputAction asset path. NOTHING was changed."));
			return;
		}
		UInputAction* Action = LoadObject<UInputAction>(nullptr, *ActionPath);
		if (!Action)
		{
			const FString Name = FPaths::GetBaseFilename(ActionPath);
			Action = LoadObject<UInputAction>(nullptr, *(ActionPath + TEXT(".") + Name));
		}
		if (!Action)
		{
			Fail(Out, FString::Printf(
				TEXT("no InputAction at '%s'. find_assets {class:\"InputAction\"} lists them. ")
				TEXT("NOTHING was changed."), *ActionPath));
			return;
		}

		// THE KEY IS VALIDATED BY NAME BEFORE ANYTHING IS TOUCHED. FKey accepts any FName, so a typo
		// produces a perfectly constructible key that binds to nothing - the mapping would exist,
		// the endpoint would report success, and the input would never fire. EKeys::GetKeyDetails is
		// the engine's own test for whether a key actually exists.
		const FString KeyName = JStr(In, TEXT("key"));
		if (KeyName.IsEmpty())
		{
			Fail(Out, TEXT("key is required - an FKey name such as SpaceBar or LeftMouseButton. ")
				TEXT("NOTHING was changed."));
			return;
		}
		const FKey Key(*KeyName);
		if (!Key.IsValid() || !EKeys::GetKeyDetails(Key).IsValid())
		{
			// Offer near matches - a wrong key name is the likeliest mistake and "Space" vs
			// "SpaceBar" is exactly the shape it takes.
			TArray<FKey> All;
			EKeys::GetAllKeys(All);
			TArray<FString> Near;
			for (const FKey& K : All)
			{
				// Forward containment always; the REVERSE direction only for names long enough to
				// mean something. Without that length floor, "Space" suggests A, C, E, P and S -
				// every single-letter key is a substring of almost any typo, and a suggestion list
				// full of noise is worse than none.
				const FString KStr = K.ToString();
				if (KStr.Contains(KeyName) || (KStr.Len() >= 4 && KeyName.Contains(KStr)))
				{
					Near.Add(KStr);
					if (Near.Num() >= 8) { break; }
				}
			}
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a key this engine knows. FKey accepts any name, so a typo builds ")
				TEXT("fine and then binds to nothing - the mapping would exist and the input would ")
				TEXT("never fire, which is why this is checked. %s NOTHING was changed."),
				*KeyName,
				Near.Num() ? *FString::Printf(TEXT("Did you mean: %s?"),
											  *FString::Join(Near, TEXT(", ")))
						   : TEXT("EKeys has no similar name.")));
			return;
		}

		// Already mapped? Not a failure - the end state the caller asked for already holds.
		for (const FEnhancedActionKeyMapping& M : Context->GetMappings())
		{
			if (M.Action == Action && M.Key == Key)
			{
				Out->SetStringField(TEXT("context"), Context->GetPathName());
				Out->SetStringField(TEXT("action"), Action->GetPathName());
				Out->SetStringField(TEXT("key"), Key.ToString());
				Out->SetBoolField(TEXT("mapped"), false);
				Out->SetNumberField(TEXT("mappingCount"), MifIMCMappingCount(Context));
				Out->SetStringField(TEXT("note"),
					TEXT("that action is already bound to that key - nothing was added, and nothing "
						 "needed to be. mapped:false here means the end state you asked for is "
						 "already in place."));
				return;
			}
		}

		const int32 Before = MifIMCMappingCount(Context);
		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_MapInputKey", "Map Input Key"));
		Context->Modify();
		Context->MapKey(Action, Key);

		// REBUILD AFTER, because MapKey's own rebuild ran BEFORE the Add and therefore does not
		// include what was just added.
		MifRebuildInputMappings(Context);

		// READ BACK from the context's own mapping list, not from MapKey's returned reference.
		bool bFound = false;
		for (const FEnhancedActionKeyMapping& M : Context->GetMappings())
		{
			if (M.Action == Action && M.Key == Key) { bFound = true; break; }
		}
		if (!bFound)
		{
			Fail(Out, TEXT("MapKey ran and the context does not list the mapping on read-back. ")
				TEXT("NOTHING usable was produced."));
			return;
		}
		Context->MarkPackageDirty();

		Out->SetStringField(TEXT("context"), Context->GetPathName());
		Out->SetStringField(TEXT("action"), Action->GetPathName());
		Out->SetStringField(TEXT("key"), Key.ToString());
		Out->SetStringField(TEXT("keyDisplay"), Key.GetDisplayName().ToString());
		Out->SetBoolField(TEXT("mapped"), true);
		Out->SetNumberField(TEXT("mappingCountBefore"), Before);
		Out->SetNumberField(TEXT("mappingCount"), MifIMCMappingCount(Context));
		Out->SetBoolField(TEXT("rebuilt"), true);
		MifNoteCookedContext(Context, Out);
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the context is dirty and NOTHING has been saved."));
	}

	// --- unmap_input_key ----------------------------------------------------
	void H_unmap_input_key(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("context"), TEXT("path"), TEXT("assetPath"), TEXT("action"), TEXT("key"),
			  TEXT("all"), TEXT("confirm") },
			TEXT("context (aliases: path, assetPath); action - unbind this InputAction; key - ")
			TEXT("optional, unbinds only that one key (omit to unbind EVERY key from the action); ")
			TEXT("all:true with confirm:true clears the ENTIRE context"),
			{ { TEXT("clear"), TEXT("spell it all:true, and it needs confirm:true as well") } }))
		{
			return;
		}

		UInputMappingContext* Context = MifResolveIMC(In, Out);
		if (!Context) { return; }
		const int32 Before = MifIMCMappingCount(Context);

		// all:true is deliberately a SEPARATE, confirmed flag. Letting a missing key mean "delete
		// everything" is the kind of implicit widening that destroys work - so an omitted key
		// unbinds one ACTION, and clearing the context has to be asked for by name.
		if (JBool(In, TEXT("all"), false))
		{
			if (!JBool(In, TEXT("confirm"), false))
			{
				Fail(Out, FString::Printf(
					TEXT("all:true clears EVERY mapping in this context - %d of them - and this ")
					TEXT("endpoint cannot put them back. Pass confirm:true. NOTHING was changed."),
					Before));
				return;
			}
			FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_UnmapAll", "Unmap All Input Keys"));
			Context->Modify();
			Context->UnmapAll();
			MifRebuildInputMappings(Context);
			Context->MarkPackageDirty();
			Out->SetStringField(TEXT("context"), Context->GetPathName());
			Out->SetBoolField(TEXT("clearedAll"), true);
			Out->SetNumberField(TEXT("mappingCountBefore"), Before);
			Out->SetNumberField(TEXT("mappingCount"), MifIMCMappingCount(Context));
			Out->SetNumberField(TEXT("removed"), Before - MifIMCMappingCount(Context));
			MifNoteCookedContext(Context, Out);
			return;
		}

		const FString ActionPath = JStr(In, TEXT("action"));
		if (ActionPath.IsEmpty())
		{
			Fail(Out, TEXT("action is required (or all:true with confirm:true to clear the whole ")
				TEXT("context). NOTHING was changed."));
			return;
		}
		UInputAction* Action = LoadObject<UInputAction>(nullptr, *ActionPath);
		if (!Action)
		{
			const FString Name = FPaths::GetBaseFilename(ActionPath);
			Action = LoadObject<UInputAction>(nullptr, *(ActionPath + TEXT(".") + Name));
		}
		if (!Action)
		{
			Fail(Out, FString::Printf(
				TEXT("no InputAction at '%s'. NOTHING was changed."), *ActionPath));
			return;
		}

		const FString KeyName = JStr(In, TEXT("key"));
		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_UnmapInputKey", "Unmap Input Key"));
		Context->Modify();
		if (KeyName.IsEmpty())
		{
			Context->UnmapAllKeysFromAction(Action);
		}
		else
		{
			const FKey Key(*KeyName);
			if (!Key.IsValid() || !EKeys::GetKeyDetails(Key).IsValid())
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is not a key this engine knows, so nothing could be bound to it. ")
					TEXT("NOTHING was changed."), *KeyName));
				return;
			}
			Context->UnmapKey(Action, Key);
		}
		MifRebuildInputMappings(Context);

		const int32 After = MifIMCMappingCount(Context);
		Context->MarkPackageDirty();

		Out->SetStringField(TEXT("context"), Context->GetPathName());
		Out->SetStringField(TEXT("action"), Action->GetPathName());
		if (!KeyName.IsEmpty()) { Out->SetStringField(TEXT("key"), KeyName); }
		Out->SetNumberField(TEXT("mappingCountBefore"), Before);
		Out->SetNumberField(TEXT("mappingCount"), After);
		// The measured difference, never the request - UnmapKey and UnmapAllKeysFromAction are both
		// void and neither says whether it matched anything.
		Out->SetNumberField(TEXT("removed"), Before - After);
		if (Before == After)
		{
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("nothing matched, so nothing was removed - removed:0 is the measured difference "
					 "in the mapping count, not an assumption. UnmapKey and UnmapAllKeysFromAction "
					 "are both void and report nothing about whether they found anything. This "
					 "context has %d mapping(s); list_input_mappings shows them."), After));
		}
		MifNoteCookedContext(Context, Out);
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the context is dirty and NOTHING has been saved."));
	}
}
