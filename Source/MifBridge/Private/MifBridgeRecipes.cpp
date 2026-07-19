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
		// Atomically insert Call into the exec chain after (AfterNode, AfterPin): the old
		// downstream target(s) move to Call's exec-out. Returns the count moved.
		int32 SpliceAfter(UEdGraphNode* AfterNode, const FString& AfterPinName,
			UEdGraphNode* Call, const FString& CallInName, const FString& CallOutName)
		{
			UEdGraphPin* AfterOut = FindPin(AfterNode, AfterPinName, EGPD_Output, /*bRequireDir*/ true);
			UEdGraphPin* CallIn = FindPin(Call, CallInName, EGPD_Input, /*bRequireDir*/ true);
			UEdGraphPin* CallOut = FindPin(Call, CallOutName, EGPD_Output, /*bRequireDir*/ true);
			if (!AfterOut || !CallIn || !CallOut)
			{
				return -1;
			}
			TArray<UEdGraphPin*> OldTargets = AfterOut->LinkedTo;
			const UEdGraphSchema_K2* Schema = K2();
			AfterNode->Modify();
			Call->Modify();
			Schema->BreakPinLinks(*AfterOut, true);
			Schema->TryCreateConnection(AfterOut, CallIn);
			for (UEdGraphPin* Target : OldTargets)
			{
				if (Target)
				{
					if (UEdGraphNode* Owner = Target->GetOwningNodeUnchecked())
					{
						Owner->Modify();
					}
					Schema->TryCreateConnection(CallOut, Target);
				}
			}
			return OldTargets.Num();
		}
	}

	// --- recipe_add_debug_print --------------------------------------------
	// The DEBUG-gated log node we bake into everything. Targets a self-local
	// PrintToModLoader(Message:String) — created on the fly if missing — NOT
	// KismetSystemLibrary.PrintString (which is DevelopmentOnly and stripped in shipping).

	void H_recipe_add_debug_print(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
					const int32 Moved = SpliceAfter(AfterNode, JStr(In, TEXT("afterPin"), TEXT("then")),
						Call, TEXT("execute"), TEXT("then"));
					if (Moved < 0)
					{
						Out->SetStringField(TEXT("warning"), TEXT("could not splice: afterPin or the print node's exec pins were not found; node added unspliced"));
					}
					else
					{
						Out->SetNumberField(TEXT("splicedTargets"), Moved);
					}
				}
				else
				{
					Out->SetStringField(TEXT("warning"), FString::Printf(TEXT("afterNode not found: %s (node added unspliced)"), *AfterGuid));
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

		UObject* MacroObject = StaticLoadObject(UBlueprint::StaticClass(), nullptr,
			TEXT("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros"), nullptr, LOAD_NoWarn);
		UBlueprint* MacroLibrary = Cast<UBlueprint>(MacroObject);
		UEdGraph* ForEachGraph = nullptr;
		if (MacroLibrary)
		{
			for (UEdGraph* Candidate : MacroLibrary->MacroGraphs)
			{
				if (Candidate && Candidate->GetName() == TEXT("ForEachLoop"))
				{
					ForEachGraph = Candidate;
					break;
				}
			}
		}
		if (!ForEachGraph)
		{
			Fail(Out, TEXT("ForEachLoop macro graph not found in StandardMacros"));
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
		In->SetBoolField(TEXT("callParent"), true);
		H_add_override_event(In, Out);
	}

	// --- recipe_splice_before_parent ---------------------------------------
	// Insert a cluster (entry..exit) between whatever currently feeds a node's exec input
	// and that node — exactly the SteelRack "cluster before the Parent call" move.

	void H_recipe_splice_before_parent(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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

		TArray<UEdGraphPin*> Upstreams = ParentExec->LinkedTo;

		const UEdGraphSchema_K2* Schema = K2();
		ParentNode->Modify();
		ClusterEntry->Modify();
		ClusterExit->Modify();
		Schema->BreakPinLinks(*ParentExec, true);
		for (UEdGraphPin* Upstream : Upstreams)
		{
			if (Upstream)
			{
				if (UEdGraphNode* Owner = Upstream->GetOwningNodeUnchecked())
				{
					Owner->Modify();
				}
				Schema->TryCreateConnection(Upstream, EntryExecIn);
			}
		}
		Schema->TryCreateConnection(ExitExecOut, ParentExec);

		MarkStructural(Blueprint);
		Out->SetNumberField(TEXT("upstreamCount"), Upstreams.Num());
		Out->SetObjectField(TEXT("parentPin"), SerializePin(ParentExec));
	}

	// --- recipe_argmax_over_components -------------------------------------
	// Inside a loop body: if (score > bestScore) { bestScore = score; bestIndex = index; }
	// Generalised argmax cluster; caller supplies the score pin + index pin sources.

	void H_recipe_argmax_over_components(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
