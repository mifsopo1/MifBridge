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
			if (In->TryGetArrayField(TEXT("floatTracks"), Tracks) && Tracks)
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

		// Node-first: spawning the timeline node runs PostPlacedNewNode, which creates the
		// UTimelineTemplate (name/GUID kept in sync). Set name + flags BEFORE PlaceAndInit.
		UK2Node_Timeline* Node = NewObject<UK2Node_Timeline>(EventGraph);
		Node->TimelineName = TimelineName;
		Node->bAutoPlay = bAutoPlay;
		Node->bLoop = bLoop;
		PlaceAndInit(EventGraph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		TArray<FString> AddedTracks;
		UTimelineTemplate* Template = Blueprint->FindTimelineTemplateByVariableName(Node->TimelineName);
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
		if (In->TryGetArrayField(TEXT("cases"), Cases) && Cases)
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
			if (UEdGraphPin* EnumPin = Node->FindPin(UK2Node_EnumLiteral::GetEnumInputPinName()))
			{
				K2()->TrySetDefaultValue(*EnumPin, Value);
			}
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	// --- set_pin_type -------------------------------------------------------

	void H_set_pin_type(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
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

		FEdGraphPinType NewType;
		FString TypeError;
		if (!MakePinType(JStr(In, TEXT("type")), JStr(In, TEXT("container")), NewType, TypeError, JStr(In, TEXT("valueType"))))
		{
			Fail(Out, TypeError);
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

		MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(Node));
		Out->SetObjectField(TEXT("pin"), Pin ? SerializePin(Pin) : MakeShared<FJsonObject>());
	}
}
