// MifBridge — Phase 3 breadth graph nodes: timeline, class-cast, switches, enum literal, set_pin_type.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Components/TimelineComponent.h"
#include "Curves/CurveFloat.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Engine/TimelineTemplate.h"
#include "K2Node_ClassDynamicCast.h"
#include "K2Node_EditablePinBase.h"
#include "K2Node_EnumLiteral.h"
#include "K2Node_SwitchEnum.h"
#include "K2Node_SwitchInteger.h"
#include "K2Node_SwitchString.h"
#include "K2Node_Timeline.h"
#include "K2Node_CallArrayFunction.h"   // set_pin_type must REFUSE on these: the node re-derives its
                                        // pin types from LinkedTo and wipes anything written directly
#include "Kismet2/BlueprintEditorUtils.h"
#include "UObject/Class.h"

namespace MifBridge
{
	namespace
	{
		UEnum* ResolveEnum(const FString& Name)
		{
			FString N = Name;
			N.TrimStartAndEndInline();
			FString Prefix, Inner;
			if (N.Split(TEXT(":"), &Prefix, &Inner) && Prefix.ToLower() == TEXT("enum"))
			{
				N = Inner.TrimStartAndEnd();
			}
			if (N.Contains(TEXT("/")) || N.Contains(TEXT(".")))
			{
				if (UEnum* Loaded = LoadObject<UEnum>(nullptr, *N, nullptr, LOAD_NoWarn))
				{
					return Loaded;
				}
			}
			return FindFirstObject<UEnum>(*N, EFindFirstObjectOptions::None);
		}

		// Mirror UK2Node_SwitchEnum::SetEnum (which is not BLUEPRINTGRAPH_API-exported) using
		// only public members + exported UEnum accessors.
		void PopulateEnumSwitch(UK2Node_SwitchEnum* Node, UEnum* Enum)
		{
			Node->Enum = Enum;
			Node->EnumEntries.Empty();
			Node->EnumFriendlyNames.Empty();
			for (int32 Index = 0; Index < Enum->NumEnums() - 1; ++Index)
			{
				if (Enum->HasMetaData(TEXT("Hidden"), Index) || Enum->HasMetaData(TEXT("Spacer"), Index))
				{
					continue;
				}
				Node->EnumEntries.Add(FName(*Enum->GetNameStringByIndex(Index)));
				Node->EnumFriendlyNames.Add(Enum->GetDisplayNameTextByIndex(Index));
			}
		}
	}

	// --- add_timeline -------------------------------------------------------

	void H_add_timeline(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// `path` is NOT decoration: ResolveBlueprintField falls back to it when blueprintId is absent
		// (MifBridgeCommon.cpp:3043-3047), so omitting it here would turn a working {path:...} call into
		// a hard "unrecognised parameter" failure.
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"),
			  TEXT("name"), TEXT("floatTracks"), TEXT("length"), TEXT("autoPlay"), TEXT("loop"),
			  TEXT("x"), TEXT("y") },
			TEXT("blueprintId (alias: path), name?, floatTracks? (array of track name strings), length?, ")
			TEXT("autoPlay? (default false), loop? (default false), x, y"),
			{ { TEXT("graphId"), TEXT("add_timeline takes a blueprintId, not a graphId - the node is placed in the blueprint's own event graph") },
			  { TEXT("tracks"), TEXT("spell it floatTracks (an array of non-empty track name strings)") },
			  { TEXT("timelineName"), TEXT("spell it name; omit it entirely for an auto-generated unique name") },
			  { TEXT("curve"), TEXT("a UCurveFloat is created per entry in floatTracks; you cannot supply one here") } }))
		{
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		if (!FBlueprintEditorUtils::DoesSupportTimelines(Blueprint))
		{
			Fail(Out, TEXT("this blueprint does not support timelines (needs an Actor-derived parent)"));
			return;
		}

		FString Raw = JStr(In, TEXT("name"));
		Raw.TrimStartAndEndInline();
		const FName TimelineName = Raw.IsEmpty() ? FBlueprintEditorUtils::FindUniqueTimelineName(Blueprint) : FName(*Raw);

		UEdGraph* EventGraph = FBlueprintEditorUtils::FindEventGraph(Blueprint);
		if (!EventGraph && Blueprint->UbergraphPages.Num() > 0)
		{
			EventGraph = Blueprint->UbergraphPages[0];
		}
		if (!EventGraph)
		{
			Fail(Out, TEXT("blueprint has no event graph to host the timeline node"));
			return;
		}

		const bool bAutoPlay = JBool(In, TEXT("autoPlay"), false);
		const bool bLoop = JBool(In, TEXT("loop"), false);

		// ---- BATCH M: TRACK NAMES ARE CHECKED BEFORE THE NODE EXISTS -------------------
		// The floatTracks[] validation used to run after the node and its UTimelineTemplate existed,
		// on the strength of "RunEndpoint cancels the transaction on ok:false, so the half-made node
		// goes with it". Cancel discards the undo entry; it never calls FTransaction::Apply
		// (EditorTransaction.cpp:1387-1437), so floatTracks:["A",""] left a real timeline node and a
		// real timeline variable in the blueprint from a call that reported failure. The names are
		// plain JSON strings — nothing about checking them needs the node. See PM-007.
		TArray<FString> WantedTracks;
		{
			const TArray<TSharedPtr<FJsonValue>>* Tracks = nullptr;
			if (JArray(In, TEXT("floatTracks"), Tracks) && Tracks)
			{
				int32 TrackOrdinal = INDEX_NONE;
				for (const TSharedPtr<FJsonValue>& Value : *Tracks)
				{
					++TrackOrdinal;
					FString TrackName;
					if (!Value.IsValid() || !Value->TryGetString(TrackName) || TrackName.IsEmpty())
					{
						// Skipped in silence before Batch L, so floatTracks:["A","",{}] answered ok:true
						// with one track.
						Fail(Out, FString::Printf(
							TEXT("floatTracks[%d] must be a non-empty string (a track name). Nothing was created - the array is checked before the timeline node exists."), TrackOrdinal));
						return;
					}
					WantedTracks.Add(TrackName);
				}
			}
		}

		Blueprint->Modify();
		EventGraph->Modify();

		// TEMPLATE FIRST, and explicitly.
		//
		// This used to place the node and expect PostPlacedNewNode to build the UTimelineTemplate.
		// UK2Node_Timeline has no PostPlacedNewNode override - its only Post* override is
		// PostPasteNode, and the template-creating code lives there, on the PASTE path. So placing a
		// timeline node created no template, every call fell into the "template not found" branch,
		// and the endpoint failed on a brand-new blueprint while blaming a name collision that did
		// not exist. The editor's own Add Timeline action calls AddNewTimeline; so does this now.
		//
		// Creating the template BEFORE the node also turns the one failure the preflight could not
		// predict into a checked one: AddNewTimeline returns null when the name is already taken,
		// which is reported here with nothing left behind.
		UTimelineTemplate* Template = FBlueprintEditorUtils::AddNewTimeline(Blueprint, TimelineName);
		if (!Template)
		{
			Fail(Out, FString::Printf(
				TEXT("could not create a timeline named '%s' - the name is already taken by another "
					 "timeline or variable on this blueprint. NOTHING was created; pick another name "
					 "(list_variables shows what is taken)."),
				*TimelineName.ToString()));
			return;
		}

		UK2Node_Timeline* Node = NewObject<UK2Node_Timeline>(EventGraph);
		Node->TimelineName = TimelineName;
		Node->TimelineGuid = Template->TimelineGuid;   // node and template must agree, or DestroyNode
													   // cannot find the template to clean up
		PlaceAndInit(EventGraph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		TArray<FString> AddedTracks;
		// Re-resolve rather than trust the pointer across PlaceAndInit, and prove the node and the
		// template really did end up agreeing on a name.
		Template = Blueprint->FindTimelineTemplateByVariableName(Node->TimelineName);
		if (!Template)
		{
			// length, autoPlay, loop and floatTracks[] ALL lived inside `if (Template)`, so a null
			// template produced a bare timeline, ok:true, and a response that did not even contain a
			// floatTracks key — every configuration the caller asked for discarded in one branch.
			//
			// BATCH M, option (c): this is the one failure the preflight above CANNOT predict — it
			// depends on engine state after PostPlacedNewNode — and unwinding a timeline means undoing
			// both a graph node and the blueprint's timeline bookkeeping, which the bridge has no safe
			// API for. So SAY what is left behind instead of claiming a rollback that does not happen.
			Out->SetStringField(TEXT("leftBehind"), Node->TimelineName.ToString());
			Fail(Out, FString::Printf(
				TEXT("the timeline node was created but its UTimelineTemplate could not be found by variable name '%s', ")
				TEXT("so length/autoPlay/loop/floatTracks could not be applied. This usually means the name collides with ")
				TEXT("an existing timeline or variable — try a different name. WHAT IS LEFT BEHIND: the timeline node '%s' ")
				TEXT("IS in the event graph and this call does not remove it (a cancelled transaction discards the undo ")
				TEXT("entry, it does not roll a creation back). Find it with list_nodes and remove it with delete_node ")
				TEXT("before retrying, or the colliding name will still be taken."),
				*Node->TimelineName.ToString(), *Node->TimelineName.ToString()));
			return;
		}
		{
			Template->bAutoPlay = bAutoPlay;
			Template->bLoop = bLoop;
			const double Length = JNum(In, TEXT("length"), 0.0);
			if (Length > 0.0)
			{
				Template->TimelineLength = static_cast<float>(Length);
				Template->LengthMode = TL_TimelineLength;
			}

			// Optional float tracks. Every name was validated above, so this loop cannot fail.
			// The curve is embedded in the template.
			for (const FString& TrackName : WantedTracks)
			{
				UCurveFloat* Curve = NewObject<UCurveFloat>(Template, NAME_None, RF_Transactional | RF_Public);
				FTTFloatTrack NewTrack;
				NewTrack.CurveFloat = Curve;
				NewTrack.SetTrackName(FName(*TrackName), Template);
				const int32 TrackIndex = Template->FloatTracks.Add(NewTrack);
				Template->AddDisplayTrack(FTTTrackId(FTTTrackBase::TT_FloatInterp, TrackIndex));
				AddedTracks.Add(TrackName);
			}
		}

		if (AddedTracks.Num() > 0)
		{
			Node->ReconstructNode(); // grow the per-track value output pins
		}

		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);

		Out->SetStringField(TEXT("timeline"), Node->TimelineName.ToString());
		if (AddedTracks.Num() > 0)
		{
			TArray<TSharedPtr<FJsonValue>> TrackArr;
			for (const FString& T : AddedTracks)
			{
				TrackArr.Add(MakeShared<FJsonValueString>(T));
			}
			Out->SetArrayField(TEXT("floatTracks"), TrackArr);
		}
		EmitNode(Out, Node);
	}

	// --- add_class_cast -----------------------------------------------------

	void H_add_class_cast(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// The class aliases are exactly the five ResolveClassStrictField is called with below — no more.
		// add_cast (MifBridgeNodes.cpp:1099) also reads cls/className; this endpoint does NOT, so listing
		// them here would accept a key nothing reads, which is the silent-ignore bug this guard exists to
		// stop. They get a KeyNote instead.
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"),
			  TEXT("targetClass"), TEXT("class"), TEXT("castTo"), TEXT("to"), TEXT("targetType"),
			  TEXT("x"), TEXT("y") },
			TEXT("graphId, targetClass (aliases: class, castTo, to, targetType), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("cls"), TEXT("add_cast accepts cls, add_class_cast does not - use targetClass") },
			  { TEXT("className"), TEXT("add_cast accepts className, add_class_cast does not - use targetClass") },
			  { TEXT("object"), TEXT("the class value to cast is a pin - place the node, then connect_pins into its input pin") } }))
		{
			return;
		}
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		// STRICT — an empty class must not silently resolve to this blueprint's own class (self-cast).
		UClass* TargetClass = ResolveClassStrictField(
			In, { TEXT("targetClass"), TEXT("class"), TEXT("castTo"), TEXT("to"), TEXT("targetType") }, Blueprint, Out);
		if (!TargetClass)
		{
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_ClassDynamicCast* Node = NewObject<UK2Node_ClassDynamicCast>(Graph);
		Node->TargetType = TargetClass; // inherited from UK2Node_DynamicCast; before AllocateDefaultPins
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_switch_enum ----------------------------------------------------

	// --- list_enum_values -------------------------------------------------------
	// Returns the real enumerator names for a UENUM, so pin defaults on plain byte/enum pins
	// (which need the exact name text, not a guess) can be set correctly on the first try.
	void H_list_enum_values(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// server.py's list_enum_values tool has always posted `enumName`, and this handler read only
		// `enum`, so every MCP call answered "enum is required" to a caller that supplied one. Note
		// `enumName` is the plugin's USUAL spelling — add_switch_enum and add_enum_literal in this very
		// file read it — so this endpoint was the odd one out, not the wrapper. Aliased here rather
		// than changed in the wrapper for that reason, and guarded so the next drift is named.
		if (RejectUnknownParams(In, Out,
			{ TEXT("enum"), TEXT("enumName") },
			TEXT("enum (alias: enumName)")))
		{
			return;
		}
		const FString Name = JStrAny(In, { TEXT("enum"), TEXT("enumName") });
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("enum is required (alias: enumName)"));
			return;
		}
		UEnum* Enum = ResolveEnum(Name);
		if (!Enum)
		{
			Fail(Out, FString::Printf(TEXT("enum not found: '%s'"), *Name));
			return;
		}
		Out->SetStringField(TEXT("enum"), Enum->GetName());
		Out->SetStringField(TEXT("path"), Enum->GetPathName());

		TArray<TSharedPtr<FJsonValue>> Values;
		const int32 Num = Enum->NumEnums();
		for (int32 Index = 0; Index < Num; ++Index)
		{
			const FString ValueName = Enum->GetNameStringByIndex(Index);
			if (ValueName.EndsWith(TEXT("_MAX")))
			{
				continue; // auto-generated sentinel, not a real value
			}
			Values.Add(MakeShared<FJsonValueString>(ValueName));
		}
		Out->SetArrayField(TEXT("values"), Values);
	}

	void H_add_switch_enum(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// `enum` is deliberately NOT accepted: this handler reads JStr(In, "enumName") only, so accepting
		// `enum` would take the key and then resolve an empty enum name. list_enum_values above is the
		// endpoint that reads both. Named in the KeyNotes so the caller is told, not ignored.
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("enumName"), TEXT("hasDefault"), TEXT("x"), TEXT("y") },
			TEXT("graphId, enumName, hasDefault? (default false), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("enum"), TEXT("spell it enumName here - list_enum_values takes either, this endpoint reads only enumName") },
			  { TEXT("cases"), TEXT("the case pins come from the enum's own entries; list them with list_enum_values") },
			  { TEXT("selection"), TEXT("the Selection input is a pin - place the node, then set_pin_default or connect_pins") } }))
		{
			return;
		}
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString EnumName = JStr(In, TEXT("enumName"));
		UEnum* Enum = ResolveEnum(EnumName);
		if (!Enum)
		{
			Fail(Out, FString::Printf(TEXT("enum not found: '%s'"), *EnumName));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_SwitchEnum* Node = NewObject<UK2Node_SwitchEnum>(Graph);
		PopulateEnumSwitch(Node, Enum); // set Enum + EnumEntries before AllocateDefaultPins
		Node->bHasDefaultPin = JBool(In, TEXT("hasDefault"), false);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_switch_int -----------------------------------------------------

	void H_add_switch_int(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("cases"), TEXT("startIndex"), TEXT("hasDefault"), TEXT("x"), TEXT("y") },
			TEXT("graphId, cases? (NUMBER of case pins, clamped 0-256), startIndex? (default 0), ")
			TEXT("hasDefault? (default true), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("count"), TEXT("spell it cases (the number of case pins to create)") },
			  { TEXT("caseLabels"), TEXT("an int switch has no labels - pass cases as a count and startIndex as the first value; add_switch_string is the one that takes an array") },
			  { TEXT("selection"), TEXT("the Selection input is a pin - place the node, then set_pin_default or connect_pins") } }))
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

		UK2Node_SwitchInteger* Node = NewObject<UK2Node_SwitchInteger>(Graph);
		Node->StartIndex = JInt(In, TEXT("startIndex"), 0);
		Node->bHasDefaultPin = JBool(In, TEXT("hasDefault"), true);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		const int32 Cases = FMath::Clamp(JInt(In, TEXT("cases"), 0), 0, 256);
		for (int32 Index = 0; Index < Cases; ++Index)
		{
			Node->AddPinToSwitchNode(); // inherited from UK2Node_Switch (exported)
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_switch_string --------------------------------------------------

	void H_add_switch_string(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("cases"), TEXT("caseSensitive"), TEXT("hasDefault"), TEXT("x"), TEXT("y") },
			TEXT("graphId, cases? (ARRAY of non-empty, non-duplicate label strings), caseSensitive? (default false), ")
			TEXT("hasDefault? (default true), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("caseLabels"), TEXT("spell it cases (an array of label strings)") },
			  { TEXT("selection"), TEXT("the Selection input is a pin - place the node, then set_pin_default or connect_pins") } }))
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

		UK2Node_SwitchString* Node = NewObject<UK2Node_SwitchString>(Graph);
		Node->bIsCaseSensitive = JBool(In, TEXT("caseSensitive"), false);
		Node->bHasDefaultPin = JBool(In, TEXT("hasDefault"), true);

		// Case labels drive the case pins — populate PinNames before AllocateDefaultPins.
		// `cases` was undocumented (no doc block on this handler) AND lenient: a non-string or empty
		// entry was dropped in silence, so the switch came back with fewer case pins than the caller
		// listed and nothing said which one was missing.
		//   in:  { graphId, cases?: ["A","B"], caseSensitive?: false, hasDefault?: true, x?, y? }
		//   out: { node:{...} }
		const TArray<TSharedPtr<FJsonValue>>* Cases = nullptr;
		if (JArray(In, TEXT("cases"), Cases) && Cases)
		{
			int32 CaseOrdinal = INDEX_NONE;
			for (const TSharedPtr<FJsonValue>& Value : *Cases)
			{
				++CaseOrdinal;
				FString CaseName;
				if (!Value.IsValid() || !Value->TryGetString(CaseName) || CaseName.IsEmpty())
				{
					Fail(Out, FString::Printf(
						TEXT("cases[%d] must be a non-empty string. Nothing was kept."), CaseOrdinal));
					return;
				}
				if (Node->PinNames.Contains(FName(*CaseName)))
				{
					// A duplicate silently collapses into one pin, so the node would have fewer cases
					// than the request listed — the same undercount, one step later.
					Fail(Out, FString::Printf(
						TEXT("cases[%d] '%s' is a duplicate; a switch cannot have two identical cases. Nothing was kept."),
						CaseOrdinal, *CaseName));
					return;
				}
				Node->PinNames.Add(FName(*CaseName));
			}
		}
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- add_enum_literal ---------------------------------------------------

	void H_add_enum_literal(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// Same as add_switch_enum: JStr(In, "enumName") is the only spelling this handler reads.
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("enumName"), TEXT("value"), TEXT("x"), TEXT("y") },
			TEXT("graphId, enumName, value? (the enumerator NAME, e.g. \"NewEnumerator0\"), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("enum"), TEXT("spell it enumName here - list_enum_values takes either, this endpoint reads only enumName") },
			  { TEXT("default"), TEXT("spell it value - and it is the enumerator name, not an index; get the exact text from list_enum_values") },
			  { TEXT("enumerator"), TEXT("spell it value (the enumerator name from list_enum_values)") } }))
		{
			return;
		}
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString EnumName = JStr(In, TEXT("enumName"));
		UEnum* Enum = ResolveEnum(EnumName);
		if (!Enum)
		{
			Fail(Out, FString::Printf(TEXT("enum not found: '%s'"), *EnumName));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_EnumLiteral* Node = NewObject<UK2Node_EnumLiteral>(Graph);
		Node->Enum = Enum; // public UPROPERTY; before AllocateDefaultPins
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		const FString Value = JStr(In, TEXT("value"));
		if (!Value.IsEmpty())
		{
			UEdGraphPin* EnumPin = Node->FindPin(UK2Node_EnumLiteral::GetEnumInputPinName());
			if (!EnumPin)
			{
				Out->SetStringField(TEXT("valueWarning"),
					TEXT("the node has no enum input pin, so 'value' was not applied"));
			}
			else
			{
				// TrySetDefaultValue is VOID and silently refuses a literal it cannot parse - the
				// same defect set_pin_default was fixed for. An enumerator's DISPLAY name is not its
				// internal name, so a caller reading the editor UI gets a value the schema quietly
				// drops, and the node stays on the enum's first entry while this reports success.
				FString Before, After, Err;
				bool bChanged = false;
				if (SetPinDefaultChecked(EnumPin, Value, Before, After, bChanged, Err))
				{
					Out->SetStringField(TEXT("valueApplied"), After);
				}
				else
				{
					Out->SetStringField(TEXT("valueApplied"), After);
					Out->SetStringField(TEXT("valueError"), FString::Printf(
						TEXT("'%s' was NOT accepted for this enum (%s); the pin is still '%s'. Use the "
							 "enumerator's internal name - describe_enum lists them - not the display text."),
						*Value, *Err, *After));
				}
			}
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- set_pin_type -------------------------------------------------------

	void H_set_pin_type(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// The node-id aliases are NOT optional garnish: ResolveNodeField treats "node" as a GENERIC
		// field and reads JStrAny(In, { nodeGuid, node, guid, nodeId }) (MifBridgeCommon.cpp:3280-3285),
		// and its own failure text advertises all four. Listing only "node" here would make a
		// {nodeGuid, ...} payload — which worked at 8e813fe — a hard "unrecognised parameter" failure.
		// That is the same back-compat break this session removed from connect_pins, re-introduced one
		// file over. Mirrors H_disconnect_pin's list (MifBridgeNodes.cpp:1673-1676).
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"),
			  TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"),
			  TEXT("pin"), TEXT("pinName"), TEXT("name"),
			  TEXT("type"), TEXT("container"), TEXT("valueType") },
			TEXT("graphId, node (aliases: nodeGuid, guid, nodeId), pin (aliases: pinName, name), ")
			TEXT("type, container?, valueType?")))
		{
			return;
		}
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node)
		{
			return;
		}
		// JStrAny, not JStr: the guard above accepts pinName/name, so they must actually be READ.
		// Accepting a key and never reading it is the silent-ignore bug class this module exists to kill.
		const FString PinName = JStrAny(In, { TEXT("pin"), TEXT("pinName"), TEXT("name") });
		UEdGraphPin* Pin = FindPin(Node, PinName, EGPD_Input, /*bRequireDir*/ false);
		if (!Pin)
		{
			Fail(Out, FString::Printf(TEXT("pin not found: '%s'"), *PinName));
			return;
		}

		FEdGraphPinType NewType;
		FString TypeError;
		if (!MakePinType(JStr(In, TEXT("type")), JStr(In, TEXT("container")), NewType, TypeError, JStr(In, TEXT("valueType"))))
		{
			Fail(Out, TypeError);
			return;
		}

		// PREFLIGHT — refuse before mutating, rather than write-then-revert-then-lie.
		//
		// UK2Node_CallArrayFunction NEVER trusts its serialised pin types. AllocateDefaultPins forces
		// the target array pin back to PC_Wildcard unconditionally (K2Node_CallArrayFunction.cpp:46)
		// and ReallocatePinsDuringReconstruction calls it on every ReconstructNode (K2Node.cpp:647-651)
		// — so on every load, every reconstruct and every cook. PostReconstructNode (:66-78) then
		// re-derives the type from ONE input: whether the pin has a link. The LINK is the only durable
		// state; the FEdGraphPinType is a cache that is overwritten before anything reads it.
		//
		// Worse, writing the type here used to be undone INSIDE THIS CALL. The
		// PinConnectionListChanged below reaches :118-140, and for a pin with no link that path sets
		// PinCategory back to PC_Wildcard (:134) and propagates the wipe to every sibling pin (:137).
		// The handler then reported success. That is the silent no-op behind "array wildcards cannot be
		// durably typed" — the reversion is immediate, not on reload.
		//
		// There is no fix available from this endpoint: the engine is doing the right thing for a node
		// whose contract is "my type is whatever is wired into me". Say so, and name the actual route.
		// SCOPE — deliberately narrow, for two independent reasons.
		//
		// (1) Correctness. The engine's wipe is NOT "any unlinked pin on the node". It fires only when
		//     PinsToCheck.Contains(ChangedPin), where PinsToCheck is GetArrayTypeDependentPins(), and
		//     only when NO pin in that set is linked (K2Node_CallArrayFunction.cpp:86-100, :123-130).
		//     A plain non-array pin on the same node retypes fine, so refusing it would be wrong.
		// (2) Linkage. UK2Node_CallArrayFunction is UCLASS(MinimalAPI), so ONLY members carrying
		//     BLUEPRINTGRAPH_API link from this module. GetTargetArrayPin() does (K2Node_CallArrayFunction.h:50,
		//     and MifBridgeNodes.cpp:921 already calls it). GetArrayTypeDependentPins() does NOT (:75) —
		//     calling it here would be LNK2019, so the full dependent-pin set is simply unavailable to us.
		//
		// So: refuse only the target array pin, which is provably in PinsToCheck and provably wiped when
		// nothing on the node is linked. Every other pin falls through to the verify-after-write below,
		// which reports honestly if the node overrode us. Narrow preflight, general verify.
		UK2Node_CallArrayFunction* ArrayNode = Cast<UK2Node_CallArrayFunction>(Node);
		if (ArrayNode != nullptr && Pin == ArrayNode->GetTargetArrayPin() && Pin->LinkedTo.Num() == 0)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is an array-function node pin with nothing connected, and its type cannot be ")
				TEXT("set directly. This node re-derives every pin type from what is wired into it and ")
				TEXT("wipes the pin back to wildcard on load, on reconstruct and during cook, so a forced ")
				TEXT("type would not survive — it would not even survive this call. Connect a typed array ")
				TEXT("to the array pin instead (connect_pins), and the wildcard pins resolve from it."),
				*PinName));
			Out->SetBoolField(TEXT("nothingModified"), true);
			Out->SetStringField(TEXT("outcome"), TEXT("preflight-rejected-nothing-created"));
			Out->SetStringField(TEXT("route"), TEXT("connect_pins"));
			return;
		}

		Node->Modify();
		Pin->PinType = NewType;

		// Custom events / function entries / tunnels own their pin signature as a SEPARATE
		// UserDefinedPins record (FUserPinInfo), independent of the live UEdGraphPin. Retyping
		// only the live pin leaves that record stale, and compile then rejects the node outright
		// ("Event node X is out-of-date. Please refresh it.") because refresh would just re-derive
		// the old type from UserDefinedPins. Keep both in sync, then ReconstructNode (not just
		// PinConnectionListChanged) so the node's cached signature actually reflects the new type.
		if (UK2Node_EditablePinBase* EditableNode = Cast<UK2Node_EditablePinBase>(Node))
		{
			for (const TSharedPtr<FUserPinInfo>& UserPin : EditableNode->UserDefinedPins)
			{
				if (UserPin.IsValid() && UserPin->PinName == Pin->PinName)
				{
					UserPin->PinType = NewType;
					break;
				}
			}
			EditableNode->ReconstructNode();
			Pin = FindPin(Node, PinName, EGPD_Input, /*bRequireDir*/ false); // node was rebuilt; re-resolve
		}
		else
		{
			Node->PinConnectionListChanged(Pin); // let the node react to the retype
		}

		// VERIFY AFTER WRITE. This handler used to emit SerializePin(Pin) without ever comparing it to
		// what was asked for, so any node that re-derived its own pin types reported success while
		// having silently reverted. Compare, and fail honestly when the node overrode us — the
		// preflight above catches the one case we can name, this catches the ones we cannot.
		// Mark BEFORE the verdict: Node->Modify() has already run, and the UK2Node_EditablePinBase path
		// above also ran ReconstructNode(), so the blueprint really is mutated whether or not the type
		// survived. Returning early without marking would leave it dirty-in-memory but unflagged.
		MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(Node));

		if (!Pin)
		{
			Fail(Out, TEXT("pin disappeared during retype (the node rebuilt itself and did not recreate "
			              "this pin) - the retype did not stick"));
			return;
		}
		if (!(Pin->PinType == NewType))
		{
			Fail(Out, FString::Printf(
				TEXT("retype of '%s' did not stick: the node overrode it. Requested '%s', pin is now '%s'. ")
				TEXT("Nodes that derive their pin types from their connections ignore a directly written ")
				TEXT("type; wire the pin instead."),
				*PinName,
				*NewType.PinCategory.ToString(),
				*Pin->PinType.PinCategory.ToString()));
			Out->SetObjectField(TEXT("pin"), SerializePin(Pin));
			Out->SetBoolField(TEXT("reverted"), true);
			return;
		}

		MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(Node));
		Out->SetObjectField(TEXT("pin"), SerializePin(Pin));
		Out->SetBoolField(TEXT("verified"), true);
	}
}
