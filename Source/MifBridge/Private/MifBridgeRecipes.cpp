// MifBridge — composite "recipe" endpoints (§10): the multi-step patterns we hand-did,
// each one transaction. Bakes in the repo's DEBUG convention (self-local PrintToModLoader,
// since KismetSystemLibrary.PrintString is stripped in the shipped game).
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "K2Node_CallFunction.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_MacroInstance.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "ScopedTransaction.h"

namespace MifBridge
{
	namespace
	{
		// Resolve the three exec pins by name, then hand off to MifBridge::SpliceExecAfter, which
		// validates the whole new shape with CanCreateConnection BEFORE it breaks anything and counts
		// the links it actually made.
		//
		// This function's own comment used to say "Atomically insert" and it was not atomic: it broke
		// the exec chain first, discarded both TryCreateConnection results, and returned
		// OldTargets.Num() — so a refused connection left the chain severed while the caller was told
		// splicedTargets:N. Returns the moved count, or -1 with OutError set on any failure.
		int32 SpliceAfter(UEdGraphNode* AfterNode, const FString& AfterPinName,
			UEdGraphNode* Call, const FString& CallInName, const FString& CallOutName, FString& OutError)
		{
			UEdGraphPin* AfterOut = FindPin(AfterNode, AfterPinName, EGPD_Output, /*bRequireDir*/ true);
			UEdGraphPin* CallIn = FindPin(Call, CallInName, EGPD_Input, /*bRequireDir*/ true);
			UEdGraphPin* CallOut = FindPin(Call, CallOutName, EGPD_Output, /*bRequireDir*/ true);
			if (!AfterOut || !CallIn || !CallOut)
			{
				OutError = FString::Printf(TEXT("exec pin not found (%s='%s', %s='%s', %s='%s')"),
					TEXT("afterPin"),  AfterOut ? TEXT("ok") : *AfterPinName,
					TEXT("insertIn"),  CallIn   ? TEXT("ok") : *CallInName,
					TEXT("insertOut"), CallOut  ? TEXT("ok") : *CallOutName);
				return -1;
			}
			AfterNode->Modify();
			Call->Modify();

			int32 Moved = 0;
			if (!SpliceExecAfter(AfterOut, CallIn, CallOut, Moved, OutError))
			{
				return -1;
			}
			return Moved;
		}
	}

	// --- recipe_add_debug_print --------------------------------------------
	// The DEBUG-gated log node we bake into everything. Targets a self-local
	// PrintToModLoader(Message:String) — created on the fly if missing — NOT
	// KismetSystemLibrary.PrintString (which is DevelopmentOnly and stripped in shipping).

	void H_recipe_add_debug_print(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"),
			  TEXT("message"), TEXT("functionName"), TEXT("messageParam"),
			  TEXT("afterNode"), TEXT("afterPin"),
			  TEXT("x"), TEXT("y") },
			TEXT("graphId, message, functionName (default PrintToModLoader), messageParam (default Message), ")
			TEXT("afterNode, afterPin (default then), x, y"),
			{ { TEXT("blueprintId"), TEXT("the print node lands in ONE graph - pass graphId from list_graphs, not the blueprint path") },
			  { TEXT("text"), TEXT("the printed string is 'message'") },
			  { TEXT("nodeGuid"), TEXT("the splice anchor is 'afterNode' - this endpoint creates its own node, it does not edit one") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}

		const FString Message = JStr(In, TEXT("message"));
		const FString FunctionName = JStr(In, TEXT("functionName"), TEXT("PrintToModLoader"));
		const FString MessageParam = JStr(In, TEXT("messageParam"), TEXT("Message"));

		UClass* SelfClass = Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass : Blueprint->GeneratedClass;
		UFunction* Function = SelfClass ? SelfClass->FindFunctionByName(FName(*FunctionName)) : nullptr;

		bool bCreatedFunction = false;
		if (!Function)
		{
			// Phase 1: create the function graph inside a tight transaction, then compile
			// OUTSIDE it so the UFunction materialises on the skeleton class (reinstancing
			// is never captured as an undo step).
			{
				FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "DebugPrintFn", "Mif Bridge: create PrintToModLoader"));
				FString CreateError;
				UEdGraph* FunctionGraph = CreateFunctionGraph(Blueprint, FunctionName, /*bPure*/ false, CreateError);
				if (!FunctionGraph)
				{
					Fail(Out, FString::Printf(TEXT("could not create %s(): %s"), *FunctionName, *CreateError));
					return;
				}
				TArray<UK2Node_FunctionEntry*> Entries;
				FunctionGraph->GetNodesOfClass(Entries);
				if (Entries.Num() > 0)
				{
					Entries[0]->Modify();
					FEdGraphPinType StringType;
					StringType.PinCategory = UEdGraphSchema_K2::PC_String;
					Entries[0]->CreateUserDefinedPin(FName(*MessageParam), StringType, EGPD_Output, /*bUseUniqueName*/ true);
				}
				MarkStructural(Blueprint);
			}

			TSharedRef<FJsonObject> Ignore = MakeShared<FJsonObject>();
			CompileBlueprintInto(Blueprint, Ignore);

			SelfClass = Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass : Blueprint->GeneratedClass;
			Function = SelfClass ? SelfClass->FindFunctionByName(FName(*FunctionName)) : nullptr;
			bCreatedFunction = true;
		}

		if (!Function)
		{
			Fail(Out, FString::Printf(TEXT("%s is unavailable after creation"), *FunctionName));
			return;
		}

		// Phase 2: add + wire the call node (transacted; no compile inside).
		UK2Node_CallFunction* Call = nullptr;
		{
			FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "DebugPrintCall", "Mif Bridge: recipe_add_debug_print"));
			Blueprint->Modify();
			Graph->Modify();

			Call = NewObject<UK2Node_CallFunction>(Graph);
			Call->SetFromFunction(Function);
			PlaceAndInit(Graph, Call, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

			if (UEdGraphPin* MessagePin = FindPin(Call, MessageParam, EGPD_Input, /*bRequireDir*/ false))
			{
				K2()->TrySetDefaultValue(*MessagePin, Message);
			}

			const FString AfterGuid = JStr(In, TEXT("afterNode"));
			if (!AfterGuid.IsEmpty())
			{
				FString ResolveError;
				if (UEdGraphNode* AfterNode = ResolveNode(AfterGuid, ResolveError))
				{
					FString SpliceError;
					const int32 Moved = SpliceAfter(AfterNode, JStr(In, TEXT("afterPin"), TEXT("then")),
						Call, TEXT("execute"), TEXT("then"), SpliceError);
					if (Moved < 0)
					{
						// The caller asked for the print node to be spliced into a specific place in the
						// exec chain. Leaving a floating node and reporting ok:true with a warning is the
						// silent-failure shape: the node exists, so a later "did it work" check on
						// list_nodes passes, and the print never runs.
						//
						// Batch M, option (c). This endpoint is SELF-MANAGED and opens its own
						// FScopedTransaction, which COMMITS when this returns — and even a cancel would
						// only discard the undo entry, not remove the node (PM-007). Say so.
						// THE COMMENT ABOVE PROMISED PM-007 AND THE STRING DID NOT SAY IT, until
						// 2026-09-05. The sibling path below (afterNode did not resolve) explains
						// why nothing is rolled back; this one disclosed the leftover, named
						// remove_node, and stopped - so a caller who hit THIS path was told what
						// was left without being told it is permanent, and would reasonably try
						// undo_transactions and believe it worked.
						//
						// Found on stock UE 5.7: the fork always took the other branch, so the
						// half-message was never reached by a test until the suites were pointed at
						// an uncooked project. Two paths out of one failure, one of them honest.
						Fail(Out, FString::Printf(
							TEXT("afterNode was given but the splice failed: %s WHAT IS LEFT BEHIND: the Print String node HAS been created in the graph, unwired, and is not removed by this failure (a self-managed transaction commits, and a cancel would only discard the undo entry, not apply it - PM-007). Remove it with remove_node (confirm:true), or wire it yourself with connect_pins."),
							*SpliceError));
						return;
					}
					Out->SetNumberField(TEXT("splicedTargets"), Moved);
				}
				else
				{
					Fail(Out, FString::Printf(
						TEXT("afterNode '%s' not found, so the print node could not be spliced into the exec chain ")
						TEXT("(a node that is never executed is not what was asked for). Omit afterNode to place an ")
						TEXT("unwired node deliberately. WHAT IS LEFT BEHIND: the Print String node HAS been created ")
						TEXT("in the graph, unwired, and is not removed by this failure (a self-managed transaction ")
						TEXT("commits, and a cancel would only discard the undo entry - PM-007). Remove it with ")
						TEXT("remove_node (confirm:true)."), *AfterGuid));
					return;
				}
			}

			MarkStructural(Blueprint);
		}

		Out->SetBoolField(TEXT("createdFunction"), bCreatedFunction);
		Out->SetStringField(TEXT("functionName"), FunctionName);
		EmitNode(Out, Call);
	}

	// --- recipe_reset_and_loop ---------------------------------------------
	// SET index (=-1) -> [SET score (=-2.0)] -> ForEachLoop over an array var. The array
	// wildcard resolves because we wire it with TryCreateConnection (the paste path failed).

	void H_recipe_reset_and_loop(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"),
			  TEXT("arrayVar"), TEXT("indexVar"), TEXT("scoreVar"),
			  TEXT("indexInit"), TEXT("scoreInit"),
			  TEXT("afterNode"), TEXT("afterPin"),
			  TEXT("x"), TEXT("y") },
			TEXT("graphId, arrayVar, indexVar, scoreVar (omit to skip the score SET), indexInit (default -1), ")
			TEXT("scoreInit (default -2.0), afterNode, afterPin (default then), x, y"),
			{ { TEXT("blueprintId"), TEXT("this recipe builds nodes in ONE graph - pass graphId from list_graphs, not the blueprint path") },
			  { TEXT("array"), TEXT("the array variable NAME is 'arrayVar'") },
			  { TEXT("index"), TEXT("'indexVar' names the variable; 'indexInit' is the value it is reset to") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}

		const FString ArrayVar = JStr(In, TEXT("arrayVar"));
		const FString IndexVar = JStr(In, TEXT("indexVar"));
		const FString ScoreVar = JStr(In, TEXT("scoreVar"));
		if (ArrayVar.IsEmpty() || IndexVar.IsEmpty())
		{
			Fail(Out, TEXT("arrayVar and indexVar are required"));
			return;
		}
		const FString IndexInit = JStr(In, TEXT("indexInit"), TEXT("-1"));
		const FString ScoreInit = JStr(In, TEXT("scoreInit"), TEXT("-2.0"));
		const int32 X = JInt(In, TEXT("x"));
		const int32 Y = JInt(In, TEXT("y"));

		// Shared resolver, not a hardcoded path. StandardMacros is only a PREFERENCE here - it is
		// where ForEachLoop lives today, and if that ever stops being true the registry search finds
		// it anyway. The literal-path form is what stopped a user finding "Switch Has Authority",
		// which is in ActorMacros; copying the registry scan here instead of sharing it would be the
		// drifting-copy problem this module avoids elsewhere.
		UBlueprint* MacroLibrary = nullptr;
		UEdGraph* ForEachGraph = ResolveMacroGraph(TEXT("ForEachLoop"),
			TEXT("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros"), MacroLibrary);
		if (!ForEachGraph)
		{
			Fail(Out, TEXT("no macro graph named 'ForEachLoop' exists in any macro library the asset "
						   "registry knows about, including StandardMacros. This recipe cannot build a "
						   "loop without it."));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_VariableSet* SetIndex = NewObject<UK2Node_VariableSet>(Graph);
		SetIndex->VariableReference.SetSelfMember(FName(*IndexVar));
		PlaceAndInit(Graph, SetIndex, X, Y);

		UK2Node_VariableSet* SetScore = nullptr;
		if (!ScoreVar.IsEmpty())
		{
			SetScore = NewObject<UK2Node_VariableSet>(Graph);
			SetScore->VariableReference.SetSelfMember(FName(*ScoreVar));
			PlaceAndInit(Graph, SetScore, X, Y + 130);
		}

		UK2Node_MacroInstance* ForEach = NewObject<UK2Node_MacroInstance>(Graph);
		ForEach->SetMacroGraph(ForEachGraph);
		PlaceAndInit(Graph, ForEach, X + 340, Y);

		UK2Node_VariableGet* GetArray = NewObject<UK2Node_VariableGet>(Graph);
		GetArray->VariableReference.SetSelfMember(FName(*ArrayVar));
		PlaceAndInit(Graph, GetArray, X, Y + 260);

		MarkStructural(Blueprint);

		if (UEdGraphPin* IndexValue = FindPin(SetIndex, IndexVar, EGPD_Input, /*bRequireDir*/ false))
		{
			K2()->TrySetDefaultValue(*IndexValue, IndexInit);
		}
		if (SetScore)
		{
			if (UEdGraphPin* ScoreValue = FindPin(SetScore, ScoreVar, EGPD_Input, /*bRequireDir*/ false))
			{
				K2()->TrySetDefaultValue(*ScoreValue, ScoreInit);
			}
		}

		TArray<TSharedPtr<FJsonValue>> Warnings;
		auto Wire = [&Warnings](UEdGraphNode* Src, const FString& SrcPin, UEdGraphNode* Dst, const FString& DstPin)
		{
			FString Error;
			if (!ConnectPinsChecked(Src, SrcPin, Dst, DstPin, /*bBreakFirst*/ false, Error))
			{
				Warnings.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("%s.%s -> %s.%s: %s"),
					*Src->GetName(), *SrcPin, *Dst->GetName(), *DstPin, *Error)));
			}
		};

		const FString AfterGuid = JStr(In, TEXT("afterNode"));
		if (!AfterGuid.IsEmpty())
		{
			FString ResolveError;
			if (UEdGraphNode* AfterNode = ResolveNode(AfterGuid, ResolveError))
			{
				Wire(AfterNode, JStr(In, TEXT("afterPin"), TEXT("then")), SetIndex, TEXT("execute"));
			}
		}

		UEdGraphNode* ExecTail = SetIndex;
		if (SetScore)
		{
			Wire(SetIndex, TEXT("then"), SetScore, TEXT("execute"));
			ExecTail = SetScore;
		}
		Wire(ExecTail, TEXT("then"), ForEach, TEXT("Exec"));

		// The wildcard array edge — the whole reason this recipe exists.
		FString ArrayError;
		const bool bArrayWired = ConnectPinsChecked(GetArray, ArrayVar, ForEach, TEXT("Array"), /*bBreakFirst*/ false, ArrayError);

		MarkStructural(Blueprint);

		Out->SetStringField(TEXT("setIndexNode"), SetIndex->NodeGuid.ToString());
		if (SetScore)
		{
			Out->SetStringField(TEXT("setScoreNode"), SetScore->NodeGuid.ToString());
		}
		Out->SetStringField(TEXT("forEachNode"), ForEach->NodeGuid.ToString());
		Out->SetStringField(TEXT("getArrayNode"), GetArray->NodeGuid.ToString());
		Out->SetBoolField(TEXT("arrayWired"), bArrayWired);
		if (!bArrayWired)
		{
			Out->SetStringField(TEXT("arrayWireError"), ArrayError);
		}
		Out->SetArrayField(TEXT("warnings"), Warnings);
		// The ForEach pins (Loop Body / Array Element / Array Index / Completed) for the caller.
		Out->SetObjectField(TEXT("forEach"), SerializeNode(ForEach, /*bIncludePins*/ true));
	}

	// --- recipe_override_and_call_parent -----------------------------------

	void H_recipe_override_and_call_parent(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// Same shape as add_override_event with the parent call forced on.
		//
		// REFUSE THE FLAG THIS RECIPE EXISTS TO FORCE. Delegating sets callParent:true over
		// whatever the caller sent, so `callParent:false` used to be accepted and then silently
		// inverted - the caller asked for no parent call and got one, with an ok:true. That is the
		// silent-override this codebase refuses everywhere else (set_layer_visibility rejects
		// `hidden` by name rather than quietly inverting it), and it is worse here because the
		// endpoint's own name promises the opposite of what the parameter asked for.
		//
		// Checked by PRESENCE, not value: `callParent:true` is harmless but still means the caller
		// believes they are choosing something they are not. All three spellings are refused,
		// because add_override_event accepts addParentCall and withParentCall as aliases and a
		// refusal that only catches one spelling is a refusal a caller routes around by accident.
		//
		// THIS DOES NOT CLOSE harvest_param_table's "no RejectUnknownParams" report, and it should
		// not be made to. That report is accurate: this endpoint has no guard of its OWN, and its
		// accepted keys are add_override_event's, reached by delegation - harvest deliberately does
		// not follow H_ to H_ calls, because attributing one handler's key list to another is how a
		// table row starts lying.
		//
		// Adding a RejectUnknownParams here to silence it would be the wrong fix twice over: it
		// would duplicate the delegate's key list, which then drifts the first time either changes,
		// AND the duplicate would be WRONG on day one, because this endpoint's real surface is that
		// list MINUS the three spellings refused just below. One permanently-reported endpoint is a
		// cheaper price than a table row that claims callParent is accepted here.
		for (const TCHAR* Spelling : { TEXT("callParent"), TEXT("addParentCall"),
									   TEXT("withParentCall") })
		{
			if (In->HasField(Spelling))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is not accepted here - this recipe IS add_override_event with the "
						 "parent call forced on, which is the only thing that distinguishes them. "
						 "Passing it would be overwritten rather than honoured. Use "
						 "add_override_event if you want to choose. NOTHING was added."), Spelling));
				return;
			}
		}
		In->SetBoolField(TEXT("callParent"), true);
		H_add_override_event(In, Out);
	}

	// --- recipe_splice_before_parent ---------------------------------------
	// Insert a cluster (entry..exit) between whatever currently feeds a node's exec input
	// and that node — exactly the SteelRack "cluster before the Parent call" move.

	void H_recipe_splice_before_parent(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"),
			  TEXT("parentNode"), TEXT("clusterEntry"), TEXT("clusterExit"),
			  TEXT("clusterEntryExecIn"), TEXT("clusterExitExecOut") },
			TEXT("graphId, parentNode, clusterEntry, clusterExit, clusterEntryExecIn (default execute), ")
			TEXT("clusterExitExecOut (default then)"),
			{ { TEXT("node"), TEXT("three DISTINCT nodes are required here - parentNode, clusterEntry, clusterExit - so there is no generic 'node' alias") },
			  { TEXT("parentNodeGuid"), TEXT("spelled 'parentNode' on this endpoint (add_override_event RETURNS it as parentNodeGuid)") },
			  { TEXT("entryNode"), TEXT("spelled 'clusterEntry'") },
			  { TEXT("exitNode"), TEXT("spelled 'clusterExit'") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		UEdGraphNode* ParentNode = ResolveNodeField(In, TEXT("parentNode"), Out);
		if (!ParentNode)
		{
			return;
		}
		UEdGraphNode* ClusterEntry = ResolveNodeField(In, TEXT("clusterEntry"), Out);
		if (!ClusterEntry)
		{
			return;
		}
		UEdGraphNode* ClusterExit = ResolveNodeField(In, TEXT("clusterExit"), Out);
		if (!ClusterExit)
		{
			return;
		}

		UEdGraphPin* ParentExec = FindPin(ParentNode, TEXT("execute"), EGPD_Input, /*bRequireDir*/ true);
		UEdGraphPin* EntryExecIn = FindPin(ClusterEntry, JStr(In, TEXT("clusterEntryExecIn"), TEXT("execute")), EGPD_Input, /*bRequireDir*/ true);
		UEdGraphPin* ExitExecOut = FindPin(ClusterExit, JStr(In, TEXT("clusterExitExecOut"), TEXT("then")), EGPD_Output, /*bRequireDir*/ true);
		if (!ParentExec)
		{
			Fail(Out, TEXT("parentNode has no 'execute' exec input"));
			return;
		}
		if (!EntryExecIn || !ExitExecOut)
		{
			Fail(Out, TEXT("cluster entry/exit exec pins not found"));
			return;
		}

		ParentNode->Modify();
		ClusterEntry->Modify();
		ClusterExit->Modify();

		// Was: break ParentExec, then fire off TryCreateConnection calls and discard every result, then
		// report upstreamCount = the number of links we INTENDED to move. A single refusal left the
		// cluster orphaned and the parent unreachable, under ok:true. SpliceExecBefore approves the
		// whole shape first and returns the count it actually wired.
		int32 MovedUpstreams = 0;
		FString SpliceError;
		if (!SpliceExecBefore(ParentExec, EntryExecIn, ExitExecOut, MovedUpstreams, SpliceError))
		{
			Fail(Out, SpliceError);
			return;
		}

		MarkStructural(Blueprint);
		Out->SetNumberField(TEXT("upstreamCount"), MovedUpstreams);
		Out->SetObjectField(TEXT("parentPin"), SerializePin(ParentExec));
	}

	// --- recipe_argmax_over_components -------------------------------------
	// Inside a loop body: if (score > bestScore) { bestScore = score; bestIndex = index; }
	// Generalised argmax cluster; caller supplies the score pin + index pin sources.

	void H_recipe_argmax_over_components(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"),
			  TEXT("loopBodyNode"), TEXT("loopBodyPin"),
			  TEXT("scoreNode"), TEXT("scorePin"),
			  TEXT("indexNode"), TEXT("indexPin"),
			  TEXT("bestScoreVar"), TEXT("bestIndexVar"),
			  TEXT("x"), TEXT("y") },
			TEXT("graphId, loopBodyNode, loopBodyPin (default 'Loop Body'), scoreNode, scorePin, indexNode, ")
			TEXT("indexPin, bestScoreVar, bestIndexVar, x, y"),
			{ { TEXT("node"), TEXT("three DISTINCT nodes are required here - loopBodyNode, scoreNode, indexNode - so there is no generic 'node' alias") },
			  { TEXT("forEachNode"), TEXT("spelled 'loopBodyNode' here (recipe_reset_and_loop returns that guid as forEachNode)") },
			  { TEXT("blueprintId"), TEXT("this recipe builds nodes in ONE graph - pass graphId from list_graphs, not the blueprint path") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		UEdGraphNode* LoopBodyNode = ResolveNodeField(In, TEXT("loopBodyNode"), Out);
		if (!LoopBodyNode)
		{
			return;
		}
		UEdGraphNode* ScoreNode = ResolveNodeField(In, TEXT("scoreNode"), Out);
		if (!ScoreNode)
		{
			return;
		}
		UEdGraphNode* IndexNode = ResolveNodeField(In, TEXT("indexNode"), Out);
		if (!IndexNode)
		{
			return;
		}

		const FString LoopBodyPin = JStr(In, TEXT("loopBodyPin"), TEXT("Loop Body"));
		const FString ScorePin = JStr(In, TEXT("scorePin"));
		const FString IndexPin = JStr(In, TEXT("indexPin"));
		const FString BestScoreVar = JStr(In, TEXT("bestScoreVar"));
		const FString BestIndexVar = JStr(In, TEXT("bestIndexVar"));
		if (ScorePin.IsEmpty() || IndexPin.IsEmpty() || BestScoreVar.IsEmpty() || BestIndexVar.IsEmpty())
		{
			Fail(Out, TEXT("scorePin, indexPin, bestScoreVar, bestIndexVar are all required"));
			return;
		}
		const int32 X = JInt(In, TEXT("x"));
		const int32 Y = JInt(In, TEXT("y"));

		UClass* MathLibrary = ResolveClass(TEXT("KismetMathLibrary"), Blueprint);
		UFunction* GreaterFn = ResolveFunctionByCandidates(MathLibrary,
			{ TEXT("Greater_DoubleDouble"), TEXT("Greater_FloatFloat") });
		if (!GreaterFn)
		{
			Fail(Out, TEXT("KismetMathLibrary Greater function not found"));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_VariableGet* GetBestScore = NewObject<UK2Node_VariableGet>(Graph);
		GetBestScore->VariableReference.SetSelfMember(FName(*BestScoreVar));
		PlaceAndInit(Graph, GetBestScore, X, Y + 220);

		UK2Node_CallFunction* Compare = NewObject<UK2Node_CallFunction>(Graph);
		Compare->SetFromFunction(GreaterFn);
		PlaceAndInit(Graph, Compare, X + 200, Y + 140);

		UK2Node_IfThenElse* Branch = NewObject<UK2Node_IfThenElse>(Graph);
		PlaceAndInit(Graph, Branch, X + 420, Y);

		UK2Node_VariableSet* SetBestScore = NewObject<UK2Node_VariableSet>(Graph);
		SetBestScore->VariableReference.SetSelfMember(FName(*BestScoreVar));
		PlaceAndInit(Graph, SetBestScore, X + 640, Y);

		UK2Node_VariableSet* SetBestIndex = NewObject<UK2Node_VariableSet>(Graph);
		SetBestIndex->VariableReference.SetSelfMember(FName(*BestIndexVar));
		PlaceAndInit(Graph, SetBestIndex, X + 860, Y);

		MarkStructural(Blueprint);

		TArray<TSharedPtr<FJsonValue>> Warnings;
		auto Wire = [&Warnings](UEdGraphNode* Src, const FString& SrcPin, UEdGraphNode* Dst, const FString& DstPin)
		{
			FString Error;
			if (!ConnectPinsChecked(Src, SrcPin, Dst, DstPin, /*bBreakFirst*/ false, Error))
			{
				Warnings.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("%s.%s -> %s.%s: %s"),
					*Src->GetName(), *SrcPin, *Dst->GetName(), *DstPin, *Error)));
			}
		};

		// data
		Wire(ScoreNode, ScorePin, Compare, TEXT("A"));
		Wire(GetBestScore, BestScoreVar, Compare, TEXT("B"));
		Wire(Compare, TEXT("ReturnValue"), Branch, TEXT("Condition"));
		// exec
		Wire(LoopBodyNode, LoopBodyPin, Branch, TEXT("execute"));
		Wire(Branch, TEXT("then"), SetBestScore, TEXT("execute"));
		Wire(SetBestScore, TEXT("then"), SetBestIndex, TEXT("execute"));
		// update
		Wire(ScoreNode, ScorePin, SetBestScore, BestScoreVar);
		Wire(IndexNode, IndexPin, SetBestIndex, BestIndexVar);

		MarkStructural(Blueprint);

		Out->SetStringField(TEXT("getBestScoreNode"), GetBestScore->NodeGuid.ToString());
		Out->SetStringField(TEXT("compareNode"), Compare->NodeGuid.ToString());
		Out->SetStringField(TEXT("branchNode"), Branch->NodeGuid.ToString());
		Out->SetStringField(TEXT("setBestScoreNode"), SetBestScore->NodeGuid.ToString());
		Out->SetStringField(TEXT("setBestIndexNode"), SetBestIndex->NodeGuid.ToString());
		Out->SetArrayField(TEXT("warnings"), Warnings);
	}
}
