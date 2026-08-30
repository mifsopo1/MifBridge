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
#include "GameFramework/InputSettings.h"
#include "GameFramework/PlayerInput.h"   // FInputActionKeyMapping / FInputAxisKeyMapping
#include "EnhancedInputModule.h"
#include "K2Node_BaseAsyncTask.h"
#include "EdGraphSchema_K2.h"
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

	// =======================================================================
	// LEGACY (pre-Enhanced) INPUT - UInputSettings action and axis mappings
	// =======================================================================
	//
	// THE ONE INPUT SYSTEM WITH NO COVERAGE AT ALL, read or write. Enhanced Input has had a read half
	// since list_input_mappings and a write half since map_input_key; legacy input had neither, and
	// it is still what a large amount of existing UE content and every UE4-era tutorial uses. A
	// project being migrated has both systems live at once, and an agent could see only one of them.
	//
	// THESE ARE SEPARATE ENDPOINTS, NOT A settings:true BRANCH ON map_input_key, which is what the
	// survey proposed. The two systems only look alike from a distance:
	//
	//     Enhanced   context (an IMC asset) + action (an InputAction ASSET) + key
	//     Legacy     no context at all      + a bare FName                 + key
	//                                       + bShift/bCtrl/bAlt/bCmd for actions
	//                                       + scale for axes
	//
	// A settings:true flag would make `context` meaningless, change what `action` even is, and switch
	// four more parameters on. Half a signature going dead depending on a boolean is precisely the
	// shape audit_mode_params.py exists to find, and building one deliberately to save an endpoint
	// name would be the wrong trade.
	//
	// PERSISTENCE IS A SEPARATE, GATED ENDPOINT for the same reason save_package is. These two only
	// ever mutate the in-memory UInputSettings CDO, which reverts on editor restart like every other
	// write this bridge makes. Writing Config/DefaultInput.ini is UInputSettings::SaveKeyMappings,
	// and that reaches DISK in the user's project - so it lives in save_input_settings, which is on
	// the safety gate's unsafe list.
	//
	// CORRECTION, 2026-08-30, same day: this comment first said "a parameter could not have been
	// gated at all". That was wrong and is worth leaving corrected rather than quietly deleted.
	// RefuseIfGated does classify per ENDPOINT NAME and cannot see parameters - but a handler can
	// gate one itself by calling GetWriteMode(), and add_gameplay_tag has done exactly that since it
	// was written (MifBridgeGameplayTags.cpp:263, refusing a persistent tag unless transient:true or
	// full mode). So a save:true parameter WAS possible.
	//
	// The separate endpoint is still the better shape here, for reasons that survive the correction:
	// the dispatcher refuses it before the handler is entered at all, which is a stronger guarantee
	// than a check the handler has to remember to make; it is discoverable, so an agent reading the
	// endpoint list can see that a persist step exists; and it matches save_package's precedent
	// rather than inventing a second convention for the same idea.

	UInputSettings* MifInputSettings(const TSharedRef<FJsonObject>& Out)
	{
		UInputSettings* Settings = UInputSettings::GetInputSettings();
		if (!Settings)
		{
			Fail(Out, TEXT("UInputSettings::GetInputSettings() returned null, which should not happen "
				TEXT("in a running editor. NOTHING was changed.")));
		}
		return Settings;
	}

	void MifWriteLegacyMappings(UInputSettings* Settings, const TSharedRef<FJsonObject>& Out)
	{
		TArray<TSharedPtr<FJsonValue>> Actions;
		for (const FInputActionKeyMapping& M : Settings->GetActionMappings())
		{
			TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
			R->SetStringField(TEXT("name"), M.ActionName.ToString());
			R->SetStringField(TEXT("key"), M.Key.ToString());
			R->SetStringField(TEXT("keyDisplay"), M.Key.GetDisplayName().ToString());
			// The modifiers are what make two mappings with the same name and key different things -
			// Ctrl+S and S are separate bindings - so they are always reported, not only when set.
			R->SetBoolField(TEXT("shift"), M.bShift != 0);
			R->SetBoolField(TEXT("ctrl"), M.bCtrl != 0);
			R->SetBoolField(TEXT("alt"), M.bAlt != 0);
			R->SetBoolField(TEXT("cmd"), M.bCmd != 0);
			Actions.Add(MakeShared<FJsonValueObject>(R));
		}
		TArray<TSharedPtr<FJsonValue>> Axes;
		for (const FInputAxisKeyMapping& M : Settings->GetAxisMappings())
		{
			TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
			R->SetStringField(TEXT("name"), M.AxisName.ToString());
			R->SetStringField(TEXT("key"), M.Key.ToString());
			R->SetStringField(TEXT("keyDisplay"), M.Key.GetDisplayName().ToString());
			R->SetNumberField(TEXT("scale"), M.Scale);
			Axes.Add(MakeShared<FJsonValueObject>(R));
		}
		Out->SetArrayField(TEXT("actionMappings"), Actions);
		Out->SetArrayField(TEXT("axisMappings"), Axes);
		Out->SetNumberField(TEXT("actionCount"), Actions.Num());
		Out->SetNumberField(TEXT("axisCount"), Axes.Num());
	}

	/** Shared key validation. See map_input_key for why a bad FKey name must never reach a mapping. */
	bool MifResolveInputKey(const FString& KeyName, FKey& OutKey, const TSharedRef<FJsonObject>& Out)
	{
		if (KeyName.IsEmpty())
		{
			Fail(Out, TEXT("key is required - an FKey name such as SpaceBar or LeftMouseButton. ")
				TEXT("NOTHING was changed."));
			return false;
		}
		OutKey = FKey(*KeyName);
		if (!OutKey.IsValid() || !EKeys::GetKeyDetails(OutKey).IsValid())
		{
			TArray<FKey> All;
			EKeys::GetAllKeys(All);
			TArray<FString> Near;
			for (const FKey& K : All)
			{
				const FString KStr = K.ToString();
				if (KStr.Contains(KeyName) || (KStr.Len() >= 4 && KeyName.Contains(KStr)))
				{
					Near.Add(KStr);
					if (Near.Num() >= 8) { break; }
				}
			}
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a key this engine knows. FKey accepts any name, so a typo builds ")
				TEXT("fine and then binds to nothing. %s NOTHING was changed."),
				*KeyName,
				Near.Num() ? *FString::Printf(TEXT("Did you mean: %s?"),
											  *FString::Join(Near, TEXT(", ")))
						   : TEXT("EKeys has no similar name.")));
			return false;
		}
		return true;
	}

	// --- list_legacy_input_mappings -----------------------------------------
	void H_list_legacy_input_mappings(const TSharedRef<FJsonObject>& In,
									  const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("name") },
			TEXT("name - optional, report only mappings with this action or axis name"),
			{ { TEXT("context"), TEXT("legacy input has no contexts - that is Enhanced Input. Use ")
								 TEXT("list_input_mappings for an InputMappingContext.") } }))
		{
			return;
		}
		UInputSettings* Settings = MifInputSettings(Out);
		if (!Settings) { return; }

		MifWriteLegacyMappings(Settings, Out);

		const FString Filter = JStr(In, TEXT("name"));
		if (!Filter.IsEmpty())
		{
			// Filter after building, so the unfiltered counts stay available as context.
			auto Keep = [&Filter](const TArray<TSharedPtr<FJsonValue>>& In2)
			{
				TArray<TSharedPtr<FJsonValue>> Kept;
				for (const TSharedPtr<FJsonValue>& V : In2)
				{
					const TSharedPtr<FJsonObject>* O = nullptr;
					if (V->TryGetObject(O) && (*O)->GetStringField(TEXT("name")).Equals(
							Filter, ESearchCase::IgnoreCase))
					{
						Kept.Add(V);
					}
				}
				return Kept;
			};
			const TArray<TSharedPtr<FJsonValue>> A = Keep(Out->GetArrayField(TEXT("actionMappings")));
			const TArray<TSharedPtr<FJsonValue>> X = Keep(Out->GetArrayField(TEXT("axisMappings")));
			Out->SetNumberField(TEXT("actionCountTotal"), Out->GetNumberField(TEXT("actionCount")));
			Out->SetNumberField(TEXT("axisCountTotal"), Out->GetNumberField(TEXT("axisCount")));
			Out->SetArrayField(TEXT("actionMappings"), A);
			Out->SetArrayField(TEXT("axisMappings"), X);
			Out->SetNumberField(TEXT("actionCount"), A.Num());
			Out->SetNumberField(TEXT("axisCount"), X.Num());
			Out->SetStringField(TEXT("filteredBy"), Filter);
		}

		if (Out->GetNumberField(TEXT("actionCount")) == 0
			&& Out->GetNumberField(TEXT("axisCount")) == 0)
		{
			Out->SetStringField(TEXT("note"),
				Filter.IsEmpty()
					? TEXT("this project defines no legacy input mappings at all. That is normal for "
						   "anything authored against Enhanced Input - list_input_mappings reads that "
						   "system, and the two are independent.")
					: TEXT("no legacy mapping has that name. actionCountTotal and axisCountTotal say "
						   "how many exist in total; call without `name` to see them."));
		}
	}

	// --- map_legacy_input ---------------------------------------------------
	void H_map_legacy_input(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("name"), TEXT("key"), TEXT("axis"), TEXT("scale"), TEXT("shift"), TEXT("ctrl"),
			  TEXT("alt"), TEXT("cmd") },
			TEXT("name - the action or axis name; key - an FKey name; axis:true for an axis mapping ")
			TEXT("(then scale, default 1.0); shift/ctrl/alt/cmd for an action mapping's modifiers"),
			{ { TEXT("context"), TEXT("legacy input has no contexts - use map_input_key for Enhanced ")
								 TEXT("Input") },
			  { TEXT("action"), TEXT("spell it `name`, and it is a bare name here, not an asset path") },
			  { TEXT("save"), TEXT("this only edits memory. Persisting to Config/DefaultInput.ini is ")
							  TEXT("save_input_settings, which is separate because it writes to disk") } }))
		{
			return;
		}
		UInputSettings* Settings = MifInputSettings(Out);
		if (!Settings) { return; }

		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required - the action or axis name, e.g. \"Jump\" or ")
				TEXT("\"MoveForward\". NOTHING was changed."));
			return;
		}
		FKey Key;
		if (!MifResolveInputKey(JStr(In, TEXT("key")), Key, Out)) { return; }

		const bool bAxis = JBool(In, TEXT("axis"), false);
		if (!bAxis)
		{
			for (const TCHAR* Bad : { TEXT("scale") })
			{
				if (In->HasField(Bad))
				{
					Fail(Out, TEXT("scale only applies to an axis mapping - pass axis:true, or drop ")
						TEXT("scale. NOTHING was changed."));
					return;
				}
			}
		}
		else
		{
			for (const TCHAR* Bad : { TEXT("shift"), TEXT("ctrl"), TEXT("alt"), TEXT("cmd") })
			{
				if (In->HasField(Bad))
				{
					// Refused rather than ignored. An axis mapping has no modifier fields at all, so
					// accepting shift:true would silently drop it and report success.
					Fail(Out, FString::Printf(
						TEXT("'%s' is an ACTION mapping modifier and FInputAxisKeyMapping has no such ")
						TEXT("field - it would be silently dropped. Drop it, or remove axis:true. ")
						TEXT("NOTHING was changed."), Bad));
					return;
				}
			}
		}

		const int32 ABefore = Settings->GetActionMappings().Num();
		const int32 XBefore = Settings->GetAxisMappings().Num();

		if (bAxis)
		{
			FInputAxisKeyMapping M;
			M.AxisName = FName(*Name);
			M.Key = Key;
			M.Scale = static_cast<float>(JNum(In, TEXT("scale"), 1.0));
			for (const FInputAxisKeyMapping& E : Settings->GetAxisMappings())
			{
				if (E.AxisName == M.AxisName && E.Key == M.Key
					&& FMath::IsNearlyEqual(E.Scale, M.Scale))
				{
					Out->SetBoolField(TEXT("mapped"), false);
					Out->SetStringField(TEXT("note"),
						TEXT("that axis is already bound to that key with that scale - nothing was "
							 "added, and nothing needed to be."));
					MifWriteLegacyMappings(Settings, Out);
					return;
				}
			}
			Settings->AddAxisMapping(M);
		}
		else
		{
			FInputActionKeyMapping M;
			M.ActionName = FName(*Name);
			M.Key = Key;
			M.bShift = JBool(In, TEXT("shift"), false);
			M.bCtrl = JBool(In, TEXT("ctrl"), false);
			M.bAlt = JBool(In, TEXT("alt"), false);
			M.bCmd = JBool(In, TEXT("cmd"), false);
			for (const FInputActionKeyMapping& E : Settings->GetActionMappings())
			{
				if (E.ActionName == M.ActionName && E.Key == M.Key && E.bShift == M.bShift
					&& E.bCtrl == M.bCtrl && E.bAlt == M.bAlt && E.bCmd == M.bCmd)
				{
					Out->SetBoolField(TEXT("mapped"), false);
					Out->SetStringField(TEXT("note"),
						TEXT("that action is already bound to that key with those modifiers - nothing "
							 "was added, and nothing needed to be."));
					MifWriteLegacyMappings(Settings, Out);
					return;
				}
			}
			Settings->AddActionMapping(M);
		}

		// READ BACK from the settings object, not from the struct that was handed in.
		const int32 AAfter = Settings->GetActionMappings().Num();
		const int32 XAfter = Settings->GetAxisMappings().Num();
		if ((bAxis && XAfter <= XBefore) || (!bAxis && AAfter <= ABefore))
		{
			Fail(Out, TEXT("the mapping was added and the settings do not list it on read-back. ")
				TEXT("NOTHING usable was produced."));
			return;
		}

		Out->SetStringField(TEXT("name"), Name);
		Out->SetStringField(TEXT("key"), Key.ToString());
		Out->SetBoolField(TEXT("axis"), bAxis);
		Out->SetBoolField(TEXT("mapped"), true);
		MifWriteLegacyMappings(Settings, Out);
		Out->SetStringField(TEXT("persistNote"),
			TEXT("this changed the in-memory input settings ONLY and reverts on editor restart. "
				 "Config/DefaultInput.ini is untouched - save_input_settings writes it, and it is on "
				 "the safety gate's unsafe list because it reaches disk."));
	}

	// --- unmap_legacy_input -------------------------------------------------
	void H_unmap_legacy_input(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("name"), TEXT("key"), TEXT("axis"), TEXT("scale"), TEXT("shift"), TEXT("ctrl"),
			  TEXT("alt"), TEXT("cmd") },
			TEXT("name - the action or axis name; key - the FKey name to unbind; axis:true for an ")
			TEXT("axis mapping. Modifiers must match the mapping being removed."),
			{ { TEXT("all"), TEXT("not supported - legacy mappings are project-wide settings, not a ")
							 TEXT("scratch container, so there is no bulk clear here on purpose") } }))
		{
			return;
		}
		UInputSettings* Settings = MifInputSettings(Out);
		if (!Settings) { return; }

		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required. NOTHING was changed."));
			return;
		}
		FKey Key;
		if (!MifResolveInputKey(JStr(In, TEXT("key")), Key, Out)) { return; }

		const bool bAxis = JBool(In, TEXT("axis"), false);
		const int32 Before = bAxis ? Settings->GetAxisMappings().Num()
								   : Settings->GetActionMappings().Num();

		if (bAxis)
		{
			FInputAxisKeyMapping M;
			M.AxisName = FName(*Name);
			M.Key = Key;
			M.Scale = static_cast<float>(JNum(In, TEXT("scale"), 1.0));
			Settings->RemoveAxisMapping(M);
		}
		else
		{
			FInputActionKeyMapping M;
			M.ActionName = FName(*Name);
			M.Key = Key;
			M.bShift = JBool(In, TEXT("shift"), false);
			M.bCtrl = JBool(In, TEXT("ctrl"), false);
			M.bAlt = JBool(In, TEXT("alt"), false);
			M.bCmd = JBool(In, TEXT("cmd"), false);
			Settings->RemoveActionMapping(M);
		}

		const int32 After = bAxis ? Settings->GetAxisMappings().Num()
								  : Settings->GetActionMappings().Num();
		Out->SetStringField(TEXT("name"), Name);
		Out->SetStringField(TEXT("key"), Key.ToString());
		Out->SetBoolField(TEXT("axis"), bAxis);
		// Measured, never assumed - RemoveActionMapping and RemoveAxisMapping both return void.
		Out->SetNumberField(TEXT("removed"), Before - After);
		MifWriteLegacyMappings(Settings, Out);
		if (Before == After)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("nothing matched, so nothing was removed - removed:0 is the measured difference "
					 "in the mapping count, since RemoveActionMapping returns void and reports "
					 "nothing. A legacy mapping matches on name, key AND every modifier: removing "
					 "Ctrl+S needs ctrl:true, and without it you are asking to remove a different "
					 "binding. list_legacy_input_mappings {name} shows what is really there."));
		}
		Out->SetStringField(TEXT("persistNote"),
			TEXT("in-memory only - reverts on editor restart, and Config/DefaultInput.ini is "
				 "untouched."));
	}

	// --- save_input_settings ------------------------------------------------
	void H_save_input_settings(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// ITS OWN ENDPOINT SO THE SAFETY GATE CAN SEE IT. RefuseIfGated runs in the dispatcher and
		// classifies per endpoint name, so a save:true PARAMETER on map_legacy_input could not have
		// been gated at all - it would have been a disk write hiding inside an endpoint whose
		// contract says it makes none. This is on UnsafeEndpoints() alongside save_package.
		if (RejectUnknownParams(In, Out, { TEXT("confirm") },
			TEXT("confirm:true - this WRITES Config/DefaultInput.ini in the project"),
			{ { TEXT("path"), TEXT("not selectable - SaveKeyMappings writes the project's own "
								   "DefaultInput.ini and takes no path") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("save_input_settings WRITES Config/DefaultInput.ini in the project - a "
				TEXT("real file on disk, not an in-memory edit that reverts on restart. Pass ")
				TEXT("confirm:true. NOTHING was written.")));
			return;
		}
		UInputSettings* Settings = MifInputSettings(Out);
		if (!Settings) { return; }
		Settings->SaveKeyMappings();
		Out->SetBoolField(TEXT("saved"), true);
		Out->SetStringField(TEXT("file"), TEXT("Config/DefaultInput.ini"));
		MifWriteLegacyMappings(Settings, Out);
	}

	// =======================================================================
	// add_k2_node - the generic adder, built instead of add_async_action
	// =======================================================================
	//
	// WHY THIS SHAPE AND NOT THE ONE ASKED FOR. The backlog item was add_async_action. The vetting
	// pointed at docs/06_CAPABILITY_ROADMAP.md:92, which frames that as one symptom of "no generic
	// add-node-by-class" alongside UK2Node_Select and GenericCreateObject - so a narrow
	// add_async_action would leave its siblings out for the same day of work. This plugin already
	// has forty-odd class-specific add_* endpoints; each exists because its node needs real
	// class-specific configuration (add_cast picks a target type, add_macro_instance resolves a
	// graph). What was missing is the case where a node needs only CONSTRUCTION plus a couple of
	// reflective property writes, which is a long tail nobody will ever build one endpoint at a
	// time.
	//
	// THE ASYNC FAMILY IS THE MOTIVATING CASE, and its configuration is genuinely small: set
	// ProxyFactoryFunctionName, ProxyFactoryClass and ProxyClass before AllocateDefaultPins -
	// exactly what the engine's own spawner does in K2Node_AsyncAction.cpp:43-44. Those three are
	// declared protected on UK2Node_BaseAsyncTask, but UHT reflection ignores C++ access, so
	// FindPropertyByName reaches them.
	//
	// AND ProxyActivateFunctionName IS DELIBERATELY LEFT ALONE. UK2Node_AsyncAction's constructor
	// already sets it to UBlueprintAsyncActionBase::Activate; a subclass that overrides the activate
	// function would be silently broken by writing it here. Set the three the spawner sets, and
	// nothing else.
	//
	// TWO CORRECTIONS THAT CHANGED THE GUARDS:
	//
	// 1. THE CRASH JUSTIFICATION WAS FALSE, and it is worth not repeating. 5.7's
	//    InitializeProxyFromFunction uses ensure(), not check(), and a half-configured node does not
	//    crash on 5.3 either - GetFactoryFunction is null-tolerant and AllocateDefaultPins guards
	//    every use behind `if (Function)`, producing a node titled "Async Task: Missing Function".
	//    The factory function IS still validated here, because refusing beats emitting a dead node -
	//    but as a quality guard, not a crash guard, and this comment says so rather than inheriting
	//    a scary story that is not true.
	//
	// 2. ASYNC NODES CANNOT GO IN A FUNCTION GRAPH. UK2Node_BaseAsyncTask::IsCompatibleWithGraph
	//    restricts placement to GT_Ubergraph and GT_Macro (K2Node_BaseAsyncTask.cpp:82-92), so the
	//    graph is checked BEFORE the node is made - otherwise the node lands and the compiler
	//    rejects it later, far from the call that caused it.
	//
	// WHAT THIS IS NOT. It does not replace the specific adders and must not be reached for when one
	// exists: add_function_call resolves overloads and self-context, add_cast picks a target class,
	// add_macro_instance resolves a graph. Those do work this cannot. The response says so when the
	// requested class has a dedicated endpoint.

	/** Class names that already have a purpose-built endpoint, and what to use instead. */
	static const TCHAR* MifDedicatedAdderFor(const FString& ClassName)
	{
		if (ClassName == TEXT("K2Node_CallFunction"))     { return TEXT("add_function_call"); }
		if (ClassName == TEXT("K2Node_CustomEvent"))      { return TEXT("add_custom_event"); }
		if (ClassName == TEXT("K2Node_DynamicCast"))      { return TEXT("add_cast"); }
		if (ClassName == TEXT("K2Node_MacroInstance"))    { return TEXT("add_macro_instance"); }
		if (ClassName == TEXT("K2Node_IfThenElse"))       { return TEXT("add_branch"); }
		if (ClassName == TEXT("K2Node_VariableGet")
			|| ClassName == TEXT("K2Node_VariableSet"))   { return TEXT("add_variable_node"); }
		if (ClassName == TEXT("K2Node_Comment"))          { return TEXT("add_comment"); }
		if (ClassName == TEXT("K2Node_EnhancedInputAction")) { return TEXT("add_enhanced_input_action"); }
		return nullptr;
	}

	void H_add_k2_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("nodeClass"), TEXT("class"), TEXT("x"), TEXT("y"),
			  TEXT("proxyFactoryFunction"), TEXT("proxyFactoryClass"), TEXT("proxyClass"),
			  TEXT("properties") },
			TEXT("graphId; nodeClass (alias class) - a UK2Node subclass, e.g. ")
			TEXT("\"K2Node_AsyncAction\" or \"K2Node_Select\"; x, y; for the async family ")
			TEXT("proxyFactoryFunction + proxyFactoryClass (+ proxyClass, inferred from the ")
			TEXT("function's return when omitted); properties{} - reflective writes applied BEFORE ")
			TEXT("pins are allocated"),
			{ { TEXT("function"), TEXT("for an ordinary function call use add_function_call - it "
									   "resolves overloads and self-context, which this does not") },
			  { TEXT("pins"), TEXT("pins are allocated by the node itself from its configuration. "
								   "Wire them afterwards with connect_pins") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph || !Blueprint) { return; }

		const FString ClassName = JStrAny(In, { TEXT("nodeClass"), TEXT("class") });
		if (ClassName.IsEmpty())
		{
			Fail(Out, TEXT("nodeClass is required - a UK2Node subclass name. NOTHING was added."));
			return;
		}

		UClass* NodeClass = nullptr;
		{
			FString Err;
			NodeClass = ResolveClassStrict(ClassName, Blueprint, TEXT("nodeClass"), Err);
			if (!NodeClass) { Fail(Out, Err); return; }
			// ResolveClassStrict does not constrain the base, so the K2Node check is made here -
			// without it, any UClass name would construct something the graph cannot hold.
			if (!NodeClass->IsChildOf(UK2Node::StaticClass()))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is not a UK2Node, so it cannot be placed in a Blueprint graph. "
						 "NOTHING was added."), *NodeClass->GetName()));
				return;
			}
		}
		if (NodeClass->HasAnyClassFlags(CLASS_Abstract))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is ABSTRACT and cannot be placed. Pick a concrete subclass. NOTHING was "
					 "added."), *ClassName));
			return;
		}
		if (const TCHAR* Better = MifDedicatedAdderFor(NodeClass->GetName()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has a purpose-built endpoint: %s. It does work this generic adder cannot "
					 "- resolving overloads, self-context, target classes or graphs - so use it "
					 "instead. NOTHING was added."), *NodeClass->GetName(), Better));
			return;
		}

		// --- the async family's own graph restriction, checked BEFORE anything is made ----------
		const bool bAsyncFamily = NodeClass->IsChildOf(UK2Node_BaseAsyncTask::StaticClass());
		if (bAsyncFamily)
		{
			const UEdGraphSchema_K2* K2Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
			const EGraphType GraphType = K2Schema ? K2Schema->GetGraphType(Graph) : GT_MAX;
			if (GraphType != GT_Ubergraph && GraphType != GT_Macro)
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is an async/latent node, and UK2Node_BaseAsyncTask::"
						 "IsCompatibleWithGraph allows those only in an event graph or a macro - "
						 "not in a function graph. Placing it here would land a node the compiler "
						 "then rejects, far from this call. Use the EventGraph. NOTHING was added."),
					*NodeClass->GetName()));
				return;
			}
		}

		const int32 X = JInt(In, TEXT("x"), 0);
		const int32 Y = JInt(In, TEXT("y"), 0);

		UK2Node* Node = NewObject<UK2Node>(Graph, NodeClass);
		if (!Node)
		{
			Fail(Out, FString::Printf(TEXT("could not construct a '%s'. NOTHING was added."),
									  *ClassName));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Applied;
		// --- the async triple, set the way the engine's own spawner sets it ---------------------
		if (bAsyncFamily)
		{
			const FString FactoryFn = JStr(In, TEXT("proxyFactoryFunction"));
			const FString FactoryClassName = JStr(In, TEXT("proxyFactoryClass"));
			if (FactoryFn.IsEmpty() || FactoryClassName.IsEmpty())
			{
				Fail(Out, TEXT("an async node needs proxyFactoryFunction and proxyFactoryClass - "
					TEXT("the static UFUNCTION that creates its proxy. Without them the node places "
						 "but titles itself \"Async Task: Missing Function\" and does nothing, which "
						 "is worse than a refusal. NOTHING was added.")));
				return;
			}
			FString Err;
			UClass* FactoryClass = ResolveClassStrict(FactoryClassName, Blueprint,
													  TEXT("proxyFactoryClass"), Err);
			if (!FactoryClass) { Fail(Out, Err); return; }

			// VALIDATED AS A QUALITY GUARD, NOT A CRASH GUARD - the engine is null-tolerant here
			// and would simply produce a dead node. Refusing beats emitting one.
			UFunction* Factory = FactoryClass->FindFunctionByName(FName(*FactoryFn));
			if (!Factory)
			{
				TArray<FString> Statics;
				for (TFieldIterator<UFunction> It(FactoryClass); It; ++It)
				{
					if (It->HasAnyFunctionFlags(FUNC_Static) && Statics.Num() < 12)
					{
						Statics.Add(It->GetName());
					}
				}
				Fail(Out, FString::Printf(
					TEXT("'%s' has no function '%s'. Its static functions include: %s. NOTHING was "
						 "added."), *FactoryClass->GetName(), *FactoryFn,
					Statics.Num() ? *FString::Join(Statics, TEXT(", ")) : TEXT("(none)")));
				return;
			}
			if (!Factory->HasAnyFunctionFlags(FUNC_Static))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s::%s' is not STATIC. An async node's proxy factory must be a static "
						 "UFUNCTION. NOTHING was added."), *FactoryClass->GetName(), *FactoryFn));
				return;
			}

			// PROPERTIES, NOT MEMBERS: these are protected on UK2Node_BaseAsyncTask, and UHT
			// reflection ignores C++ access. ProxyActivateFunctionName is NOT written - the
			// constructor already set it, and a subclass overriding it would be silently broken.
			if (FNameProperty* P = CastField<FNameProperty>(
					NodeClass->FindPropertyByName(TEXT("ProxyFactoryFunctionName"))))
			{
				P->SetPropertyValue_InContainer(Node, FName(*FactoryFn));
				Applied.Add(MakeShared<FJsonValueString>(TEXT("ProxyFactoryFunctionName")));
			}
			if (FObjectProperty* P = CastField<FObjectProperty>(
					NodeClass->FindPropertyByName(TEXT("ProxyFactoryClass"))))
			{
				P->SetObjectPropertyValue_InContainer(Node, FactoryClass);
				Applied.Add(MakeShared<FJsonValueString>(TEXT("ProxyFactoryClass")));
			}
			// ProxyClass defaults to the factory's return type, which is what the spawner uses.
			UClass* ProxyClass = nullptr;
			const FString ProxyName = JStr(In, TEXT("proxyClass"));
			if (!ProxyName.IsEmpty())
			{
				FString PErr;
				ProxyClass = ResolveClassStrict(ProxyName, Blueprint, TEXT("proxyClass"), PErr);
				if (!ProxyClass) { Fail(Out, PErr); return; }
			}
			else if (FObjectProperty* Ret =
						 CastField<FObjectProperty>(Factory->GetReturnProperty()))
			{
				ProxyClass = Ret->PropertyClass;
			}
			if (ProxyClass)
			{
				if (FObjectProperty* P = CastField<FObjectProperty>(
						NodeClass->FindPropertyByName(TEXT("ProxyClass"))))
				{
					P->SetObjectPropertyValue_InContainer(Node, ProxyClass);
					Applied.Add(MakeShared<FJsonValueString>(TEXT("ProxyClass")));
				}
			}
			else
			{
				Fail(Out, FString::Printf(
					TEXT("could not determine the proxy class: '%s::%s' returns no UObject, and no "
						 "proxyClass was given. NOTHING was added."),
					*FactoryClass->GetName(), *FactoryFn));
				return;
			}
		}

		// --- arbitrary reflective properties, applied BEFORE pins are allocated -----------------
		const TSharedPtr<FJsonObject>* PropsObj = nullptr;
		if (In->TryGetObjectField(TEXT("properties"), PropsObj) && PropsObj && PropsObj->IsValid())
		{
			for (const auto& Pair : (*PropsObj)->Values)
			{
				FProperty* Prop = NodeClass->FindPropertyByName(FName(*Pair.Key));
				if (!Prop)
				{
					Fail(Out, FString::Printf(
						TEXT("'%s' has no property '%s'. NOTHING was added - the node is discarded "
							 "rather than left half-configured."), *NodeClass->GetName(),
						*Pair.Key));
					return;
				}
				FString Text;
				if (!Pair.Value.IsValid() || !Pair.Value->TryGetString(Text))
				{
					double D = 0.0;
					bool B = false;
					if (Pair.Value.IsValid() && Pair.Value->TryGetNumber(D))
					{
						Text = FString::SanitizeFloat(D);
					}
					else if (Pair.Value.IsValid() && Pair.Value->TryGetBool(B))
					{
						Text = B ? TEXT("true") : TEXT("false");
					}
				}
				const TCHAR* Result = Prop->ImportText_InContainer(*Text, Node, Node,
																   PPF_None);
				if (!Result)
				{
					Fail(Out, FString::Printf(
						TEXT("could not set '%s' to %s on a %s. NOTHING was added."),
						*Pair.Key, *Text, *NodeClass->GetName()));
					return;
				}
				Applied.Add(MakeShared<FJsonValueString>(Pair.Key));
			}
		}

		// EVERY SETUP CALL HAPPENS BEFORE THIS. PlaceAndInit runs AllocateDefaultPins, and these
		// nodes generate their pins FROM the configuration above - the same ordering trap
		// add_enhanced_input_action documents at the top of this file.
		PlaceAndInit(Graph, Node, X, Y);

		// READ BACK from the graph's own node list. PlaceAndInit returns void, so "the node is in
		// the graph" is a claim nothing has checked until this does - and every other endpoint
		// tonight learned the same lesson about trusting a void call.
		if (!Graph->Nodes.Contains(Node))
		{
			Fail(Out, FString::Printf(
				TEXT("a '%s' was constructed and the graph does not list it on read-back. NOTHING "
					 "usable was produced."), *NodeClass->GetName()));
			return;
		}
		MarkStructural(Blueprint);

		EmitNode(Out, Node);
		Out->SetStringField(TEXT("nodeClass"), NodeClass->GetName());
		Out->SetArrayField(TEXT("configured"), Applied);
		Out->SetNumberField(TEXT("pinCount"), Node->Pins.Num());
		Out->SetStringField(TEXT("title"),
			Node->GetNodeTitle(ENodeTitleType::ListView).ToString());
		if (bAsyncFamily)
		{
			// The title is the honest read on whether the configuration took: a node whose factory
			// did not resolve names itself "Async Task: Missing Function" and has almost no pins.
			Out->SetStringField(TEXT("asyncNote"),
				TEXT("an async node's output exec pins come from its proxy's delegates - that is why "
					 "it cannot be synthesised from an ordinary call node. Check `title` and "
					 "`pinCount`: a node whose factory did not resolve titles itself \"Async Task: "
					 "Missing Function\" and carries almost no pins."));
		}
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the Blueprint is dirty and NOT compiled. Wire the pins with connect_pins, then "
				 "compile."));
	}
}
