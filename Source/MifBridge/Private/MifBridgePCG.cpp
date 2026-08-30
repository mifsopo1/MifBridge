// MifBridge — PCG (Procedural Content Generation): read the graphs, read the components, run them.
//
// WHY THIS EXISTS AT ALL, given it was explicitly declined once. The old measuring stick was "value
// for DDS2 cooked-game modding", and against that PCG scored nothing - the decline read "a DDS2 mod
// does not regenerate the world", which is true. That rule was superseded on 2026-08-26 and this was
// reopened on 2026-08-27, because Andre's other project is a CITY BUILDER on UE 5.7 and procedural
// generation is close to its whole point. It is the single feature the old rule cost most.
//
// THE PLUGIN MOVED BETWEEN ENGINE VERSIONS, which is the reason MIF_WITH_* detection searches rather
// than hardcodes:
//     5.3: Engine/Plugins/Experimental/PCG/PCG.uplugin
//     5.7: Engine/Plugins/PCG/PCG.uplugin          (promoted out of experimental)
// MifBridge.Build.cs finds descriptors with SearchOption.AllDirectories (:132), so this works in both
// without knowing either path. docs/02 section 14 records the same move for GameFeatures.
//
// Verified in BOTH trees before writing:
//   UPCGGraph::GetNodes()          inline in both, so it links despite 5.7's per-member UE_API
//   UPCGGraph::GetInputNode()      inline in both
//   UPCGComponent::GetGraph()      present in both
//   UPCGComponent::Generate()      present in both
//   UPCGComponent::Cleanup()       the no-arg overload is present in both
//   UPCGNode::GetSettings()        present in both
//
// AND ONE THAT IS NOT PORTABLE, caught before it could ship:
//   5.3: FText GetNodeTitle() const;
//   5.7: FText GetNodeTitle(EPCGNodeTitleType TitleType) const;
// Same name, same return type, different parameters - docs/02 section 14, direction F, the shape that
// passes every check short of a compiler. The node TYPE is taken from GetSettings()->GetClass()
// instead, which is identical in both and is the more stable answer anyway: a title is display text
// and can be renamed, a settings class is what the node actually IS.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"
// MIF_ENGINE_AT_LEAST, used below for GetNodeTitle. WITHOUT THIS INCLUDE the macro is undefined, and
// UE compiles C4668 (undefined macro in #if) as an ERROR - which is the trap this project documented
// in docs/02 section 14 hours before I walked into it.
//
// It was INVISIBLE ON 5.3 for a reason worth understanding: an undefined macro evaluates to 0, so the
// #if took the ELSE branch, and the else branch is the 5.3 spelling - GetNodeTitle() with no
// arguments. The wrong reason produced the right code on one engine and a hard error on the other.
#include "MifBridgeVersion.h"

#if MIF_WITH_PCG
#include "PCGPin.h"
#include "PCGEdge.h"
#include "PCGSettings.h"
#include "ScopedTransaction.h"
#include "PCGGraph.h"
#include "PCGComponent.h"
#include "PCGNode.h"
#include "PCGPin.h"
#include "PCGSettings.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Subsystems/EditorActorSubsystem.h"
#include "Editor.h"
#include "GameFramework/Actor.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_PCG
	namespace
	{
		/** One message for every PCG endpoint on an engine without the plugin. Names the plugin and
		 *  both places it has lived, because "PCG not found" against an engine that does ship it is
		 *  usually a path assumption rather than a missing plugin. */
		void PCGUnavailable(const TSharedRef<FJsonObject>& Out, const TCHAR* What)
		{
			Fail(Out, FString::Printf(
				TEXT("%s is unavailable: this MifBridge was built against an engine with no PCG "
					 "plugin. PCG lives at Engine/Plugins/Experimental/PCG on UE 5.3 and "
					 "Engine/Plugins/PCG on 5.7 - it was promoted out of experimental. The endpoint "
					 "stays registered so this answer is possible at all."), What));
		}
	}
#else
	namespace
	{
		/** Every PCG component in the open world, with the actor that owns it. */
		void PCGForEachComponent(TFunctionRef<void(AActor*, UPCGComponent*)> Fn)
		{
			UEditorActorSubsystem* Sub = GEditor
				? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
			if (!Sub) { return; }
			for (AActor* A : Sub->GetAllLevelActors())
			{
				if (!A) { continue; }
				// GetComponents rather than FindComponentByClass: an actor can carry more than one PCG
				// component, and reporting only the first would silently under-report a level.
				TArray<UPCGComponent*> Comps;
				A->GetComponents<UPCGComponent>(Comps);
				for (UPCGComponent* C : Comps) { if (C) { Fn(A, C); } }
			}
		}
	}
#endif

	// --- list_pcg_graphs ------------------------------------------------------------------------
	//   in:  { pathPrefix? = "/Game/" }
	//   out: { graphs[ { path, name } ], count }
	// Bucket: READ. Asset Registry only - LOADS NOTHING.
	void H_list_pcg_graphs(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("pathPrefix"), TEXT("prefix") },
			TEXT("pathPrefix (alias: prefix, default /Game/)"),
			{ { TEXT("graph"), TEXT("this LISTS graphs; describe_pcg_graph takes one") } }))
		{
			return;
		}
#if !MIF_WITH_PCG
		PCGUnavailable(Out, TEXT("list_pcg_graphs"));
#else
		const FString Prefix = JStrAny(In, { TEXT("pathPrefix"), TEXT("prefix") }, TEXT("/Game/"));
		IAssetRegistry& Registry =
			FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get();

		TArray<FAssetData> Found;
		Registry.GetAssetsByPath(FName(*Prefix), Found, /*bRecursive*/ true);

		TArray<TSharedPtr<FJsonValue>> Graphs;
		for (const FAssetData& A : Found)
		{
			// Compared against the CLASS PATH rather than a name string. GetAssetsByClass(FName) is
			// UE_DEPRECATED(5.1) and deleted in 5.7, and a name comparison would also match anything
			// else that happened to be called PCGGraph.
			if (A.AssetClassPath != UPCGGraph::StaticClass()->GetClassPathName()) { continue; }
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("path"), A.GetObjectPathString());
			J->SetStringField(TEXT("name"), A.AssetName.ToString());
			Graphs.Add(MakeShared<FJsonValueObject>(J));
		}
		Out->SetArrayField(TEXT("graphs"), Graphs);
		Out->SetNumberField(TEXT("count"), Graphs.Num());
		if (Registry.IsLoadingAssets())
		{
			Out->SetBoolField(TEXT("registryStillScanning"), true);
			Out->SetStringField(TEXT("scanNote"),
				TEXT("the asset registry is STILL SCANNING - a low count here may mean 'not finished "
					 "looking' rather than 'none exist'."));
		}
		Out->SetStringField(TEXT("source"), TEXT("asset registry only - nothing was loaded."));
#endif
	}

	// --- describe_pcg_graph ---------------------------------------------------------------------
	//   in:  { path (aliases: assetPath, graph) }
	//   out: { nodes[ { title, settingsClass, inputPins, outputPins } ], nodeCount, hasInputNode }
	// Bucket: READ. Loads the graph asset.
	void H_describe_pcg_graph(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("graph") },
			TEXT("path (aliases: assetPath, graph) - a PCGGraph asset"),
			{ { TEXT("component"), TEXT("that is a placed component, not the graph asset - list_pcg_components reports those") } }))
		{
			return;
		}
#if !MIF_WITH_PCG
		PCGUnavailable(Out, TEXT("describe_pcg_graph"));
#else
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("graph") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a PCGGraph asset. list_pcg_graphs reports them."));
			return;
		}
		UPCGGraph* Graph = LoadObject<UPCGGraph>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Graph)
		{
			Fail(Out, FString::Printf(TEXT("no PCGGraph at '%s'."), *Path));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Nodes;
		for (const UPCGNode* N : Graph->GetNodes())
		{
			if (!N) { continue; }
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();

			// THE SETTINGS CLASS IS THE STABLE IDENTITY. GetNodeTitle's signature differs between
			// engines (see the file header), and a title is display text that can be renamed anyway.
			const UPCGSettings* S = N->GetSettings();
			J->SetStringField(TEXT("settingsClass"), S ? S->GetClass()->GetName() : FString());
			J->SetStringField(TEXT("name"), N->GetName());

#if MIF_ENGINE_AT_LEAST(5, 7)
			J->SetStringField(TEXT("title"), N->GetNodeTitle(EPCGNodeTitleType::ListView).ToString());
#else
			J->SetStringField(TEXT("title"), N->GetNodeTitle().ToString());
#endif
			J->SetNumberField(TEXT("inputPins"), N->GetInputPins().Num());
			J->SetNumberField(TEXT("outputPins"), N->GetOutputPins().Num());
			// The LABELS, not just how many. connect_pcg_nodes addresses pins by label, so a count
			// alone left a caller guessing the very string the write half requires.
			auto PinLabels = [](const TArray<TObjectPtr<UPCGPin>>& Pins)
			{
				TArray<TSharedPtr<FJsonValue>> Out2;
				for (const UPCGPin* P : Pins)
				{
					if (P) { Out2.Add(MakeShared<FJsonValueString>(P->Properties.Label.ToString())); }
				}
				return Out2;
			};
			J->SetArrayField(TEXT("inputPinNames"), PinLabels(N->GetInputPins()));
			J->SetArrayField(TEXT("outputPinNames"), PinLabels(N->GetOutputPins()));
			Nodes.Add(MakeShared<FJsonValueObject>(J));
		}

		// EDGES - without them this endpoint said what was IN a graph and nothing about what it
		// DOES. Node lists plus pin COUNTS cannot tell you the shape of a graph; two graphs with
		// identical node lists and no shared wiring compute completely different things.
		//
		// Walked from the OUTPUT side only. Every edge is reachable from both ends, so walking both
		// would report each one twice - and an edge list with silent duplicates is worse than none,
		// because a caller counting connections gets the wrong answer with no way to tell.
		TArray<TSharedPtr<FJsonValue>> Edges;
		for (const UPCGNode* N : Graph->GetNodes())
		{
			if (!N) { continue; }
			for (const UPCGPin* Pin : N->GetOutputPins())
			{
				if (!Pin) { continue; }
				for (const UPCGEdge* E : Pin->Edges)
				{
					if (!E || !E->InputPin || !E->OutputPin) { continue; }
					// NAMING, stated because it is genuinely confusing in this API: an FPCGEdge's
					// InputPin is the pin the edge leaves FROM (it is that pin's input to the edge)
					// and OutputPin is where it ARRIVES. This response uses from/to instead, so a
					// reader does not have to hold that inversion in their head.
					const UPCGNode* ToNode = E->OutputPin->Node;
					TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
					J->SetStringField(TEXT("fromNode"), N->GetName());
					J->SetStringField(TEXT("fromPin"), Pin->Properties.Label.ToString());
					J->SetStringField(TEXT("toNode"), ToNode ? ToNode->GetName() : FString());
					J->SetStringField(TEXT("toPin"), E->OutputPin->Properties.Label.ToString());
					Edges.Add(MakeShared<FJsonValueObject>(J));
				}
			}
		}

		Out->SetStringField(TEXT("assetPath"), Graph->GetPathName());
		Out->SetArrayField(TEXT("nodes"), Nodes);
		Out->SetNumberField(TEXT("nodeCount"), Nodes.Num());
		Out->SetArrayField(TEXT("edges"), Edges);
		Out->SetNumberField(TEXT("edgeCount"), Edges.Num());
		// A graph with no input node produces nothing whatever else it contains - worth stating rather
		// than leaving a caller to infer it from an empty result at generation time.
		Out->SetBoolField(TEXT("hasInputNode"), Graph->GetInputNode() != nullptr);
		if (!Graph->GetInputNode())
		{
			Out->SetStringField(TEXT("note"),
				TEXT("this graph has NO INPUT NODE, so it has nothing to operate on and will generate "
					 "nothing regardless of the nodes above."));
		}
#endif
	}

	// --- list_pcg_components --------------------------------------------------------------------
	//   in:  {}
	//   out: { components[ { actor, actorPath, component, graph, generated, activated } ], count }
	// Bucket: READ.
	void H_list_pcg_components(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {},
			TEXT("no parameters - this lists every PCG component in the OPEN level"),
			{ { TEXT("path"), TEXT("this reads the open LEVEL, not an asset. list_pcg_graphs takes a path.") } }))
		{
			return;
		}
#if !MIF_WITH_PCG
		PCGUnavailable(Out, TEXT("list_pcg_components"));
#else
		TArray<TSharedPtr<FJsonValue>> Comps;
		PCGForEachComponent([&Comps](AActor* A, UPCGComponent* C)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("actor"), A->GetActorLabel());
			J->SetStringField(TEXT("actorPath"), A->GetPathName());
			J->SetStringField(TEXT("component"), C->GetName());
			const UPCGGraph* G = C->GetGraph();
			// A component with NO GRAPH is inert. Reporting the empty string rather than omitting the
			// field keeps the shape stable and makes the problem visible in a listing.
			J->SetStringField(TEXT("graph"), G ? G->GetPathName() : FString());
			J->SetBoolField(TEXT("hasGraph"), G != nullptr);
			J->SetBoolField(TEXT("generated"), C->bGenerated);
			J->SetBoolField(TEXT("activated"), C->bActivated);
			Comps.Add(MakeShared<FJsonValueObject>(J));
		});
		Out->SetArrayField(TEXT("components"), Comps);
		Out->SetNumberField(TEXT("count"), Comps.Num());
#endif
	}

	// --- pcg_generate ---------------------------------------------------------------------------
	//   in:  { actorPath, confirm }
	//   out: { actor, component, graph, wasGenerated }
	// Bucket: MUTATES the open level, potentially by a LOT.
	//
	// CONFIRM-GATED, and not as a formality. Generating a PCG graph can spawn thousands of actors into
	// the open level - that is what it is for. It is also ASYNCHRONOUS: this schedules the work and
	// returns, so an immediate list_level_actors will not yet show the result. Saying so is the
	// difference between "it did nothing" and "it has not finished".
	void H_pcg_generate(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("confirm") },
			TEXT("actorPath (an actor with a PCG component); confirm:true"),
			{ { TEXT("graph"), TEXT("generation runs a COMPONENT in the level, not a graph asset - list_pcg_components reports the components") } }))
		{
			return;
		}
#if !MIF_WITH_PCG
		PCGUnavailable(Out, TEXT("pcg_generate"));
#else
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("pcg_generate needs confirm:true. Generating a PCG graph can spawn "
						   "thousands of actors into the OPEN level - that is what it is for - and "
						   "there is no single undo for it. Use pcg_cleanup to remove what it made. "
						   "NOTHING was generated."));
			return;
		}
		const FString Path = JStrAny(In, { TEXT("actorPath"), TEXT("actor") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("actorPath is required - list_pcg_components reports one per component."));
			return;
		}

		AActor* Target = nullptr;
		UPCGComponent* Comp = nullptr;
		PCGForEachComponent([&](AActor* A, UPCGComponent* C)
		{
			if (!Target && (A->GetPathName() == Path || A->GetActorLabel() == Path))
			{
				Target = A; Comp = C;
			}
		});
		if (!Comp)
		{
			Fail(Out, FString::Printf(
				TEXT("no PCG component on '%s'. list_pcg_components reports every one in the open "
					 "level. NOTHING was generated."), *Path));
			return;
		}
		if (!Comp->GetGraph())
		{
			// Refused rather than run. Generating a component with no graph does nothing and would
			// report success, which is the silent-success shape this project keeps finding.
			Fail(Out, FString::Printf(
				TEXT("'%s' has a PCG component with NO GRAPH assigned, so generating it would do "
					 "nothing and report success. NOTHING was generated."), *Path));
			return;
		}

		Comp->Generate();

		Out->SetStringField(TEXT("actor"), Target->GetActorLabel());
		Out->SetStringField(TEXT("component"), Comp->GetName());
		Out->SetStringField(TEXT("graph"), Comp->GetGraph()->GetPathName());
		Out->SetBoolField(TEXT("wasGenerated"), Comp->bGenerated);
		Out->SetStringField(TEXT("note"),
			TEXT("generation is ASYNCHRONOUS - this scheduled it and returned. An immediate "
				 "list_level_actors may not show the result yet, and wasGenerated above is the state "
				 "BEFORE this call. Nothing was saved."));
		UE_LOG(LogMifBridge, Log, TEXT("pcg_generate: %s"), *Target->GetActorLabel());
#endif
	}

	// --- pcg_cleanup ----------------------------------------------------------------------------
	//   in:  { actorPath, confirm }
	//   out: { actor, component }
	// Bucket: MUTATES the open level - removes what generation made.
	void H_pcg_cleanup(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("confirm") },
			TEXT("actorPath (an actor with a PCG component); confirm:true"),
			{}))
		{
			return;
		}
#if !MIF_WITH_PCG
		PCGUnavailable(Out, TEXT("pcg_cleanup"));
#else
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("pcg_cleanup needs confirm:true - it DESTROYS the actors a PCG component "
						   "generated. NOTHING was removed."));
			return;
		}
		const FString Path = JStrAny(In, { TEXT("actorPath"), TEXT("actor") });
		AActor* Target = nullptr;
		UPCGComponent* Comp = nullptr;
		PCGForEachComponent([&](AActor* A, UPCGComponent* C)
		{
			if (!Target && (A->GetPathName() == Path || A->GetActorLabel() == Path))
			{
				Target = A; Comp = C;
			}
		});
		if (!Comp)
		{
			Fail(Out, FString::Printf(
				TEXT("no PCG component on '%s'. NOTHING was removed."), *Path));
			return;
		}

		// The NO-ARG overload deliberately. 5.3 also offers Cleanup(bool bRemoveComponents, bool
		// bSave=false) and 5.7 does not, so the two-argument form is not portable - and its bSave
		// parameter would write to disk, which this bridge does not do.
		Comp->Cleanup();

		Out->SetStringField(TEXT("actor"), Target->GetActorLabel());
		Out->SetStringField(TEXT("component"), Comp->GetName());
		Out->SetStringField(TEXT("note"),
			TEXT("cleanup is asynchronous like generation. Nothing was saved."));
		UE_LOG(LogMifBridge, Log, TEXT("pcg_cleanup: %s"), *Target->GetActorLabel());
#endif
	}

#if MIF_WITH_PCG
	// =======================================================================
	// PCG GRAPH AUTHORING - add/remove nodes, connect/disconnect pins
	// =======================================================================
	//
	// THE TRAP THAT SHAPES ALL OF THIS: UPCGGraph::AddEdge CANNOT REPORT FAILURE. It calls
	// AddLabeledEdge, THROWS THE RESULT AWAY, and returns `To` unconditionally
	// (PCGGraph.cpp:473-477). So a wrong pin label returns a perfectly good node pointer, logs an
	// error to LogPCG that no HTTP caller will ever see, and wires nothing.
	//
	// AND AddLabeledEdge's OWN BOOL IS AMBIGUOUS, which is the part that is easy to get wrong twice.
	// It returns false for "invalid node", false for "no such from-pin", false for "no such to-pin" -
	// and then, on the SUCCESS path, it returns bToPinBrokeOtherEdges (PCGGraph.cpp:521). So false
	// means EITHER "nothing happened" OR "it worked cleanly", and those are opposites.
	//
	// The resolution is to make the ambiguity impossible rather than to interpret it: every failure
	// case is checked HERE first, so by the time AddLabeledEdge is called its false can only mean
	// "added without displacing anything". The edge is then verified by reading the graph back, and
	// displacement is reported as a MEASURED count of what left the target pin, never from the bool.

	UPCGGraph* ResolvePCGGraph(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString Path = JStrAny(In, { TEXT("graph"), TEXT("path"), TEXT("assetPath") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("graph is required (aliases: path, assetPath) - a PCGGraph asset. ")
				TEXT("list_pcg_graphs reports them. NOTHING was changed."));
			return nullptr;
		}
		UPCGGraph* Graph = LoadObject<UPCGGraph>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Graph)
		{
			Fail(Out, FString::Printf(
				TEXT("no PCGGraph at '%s'. list_pcg_graphs reports them. NOTHING was changed."), *Path));
			return nullptr;
		}
		// COOKED IS REFUSED, not attempted. A cooked UPCGGraph still has its Nodes array, so the
		// mutation would appear to work - but nothing can save it, and PCG's editor notification
		// path is WITH_EDITOR-only, so neither the graph editor nor any placed component would pick
		// the change up. An edit that quietly evaporates is worse than a refusal.
		if (IsCookedOrContainerPackage(Graph->GetOutermost()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' came from a COOKED package. Nodes could be added in memory, but nothing "
					 "would save and no placed PCG component would regenerate - PCG's editor "
					 "notification path does not exist in a cooked context. Mint an editable copy "
					 "first. NOTHING was changed."), *Graph->GetPathName()));
			return nullptr;
		}
		return Graph;
	}

	UPCGNode* FindPCGNode(UPCGGraph* Graph, const FString& Name)
	{
		for (UPCGNode* N : Graph->GetNodes())
		{
			if (N && N->GetName() == Name) { return N; }
		}
		// The input and output nodes are NOT in GetNodes() - they are the graph's own endpoints, and
		// leaving them unreachable would make it impossible to wire a graph to its own input, which
		// is the first edge anyone needs.
		if (UPCGNode* In2 = Graph->GetInputNode())  { if (In2->GetName() == Name) { return In2; } }
		if (UPCGNode* Out2 = Graph->GetOutputNode()) { if (Out2->GetName() == Name) { return Out2; } }
		return nullptr;
	}

	FString PCGNodeNameList(UPCGGraph* Graph)
	{
		TArray<FString> Names;
		if (const UPCGNode* N = Graph->GetInputNode())  { Names.Add(N->GetName()); }
		if (const UPCGNode* N = Graph->GetOutputNode()) { Names.Add(N->GetName()); }
		for (const UPCGNode* N : Graph->GetNodes()) { if (N) { Names.Add(N->GetName()); } }
		return Names.Num() ? FString::Join(Names, TEXT(", ")) : TEXT("(none)");
	}

	FString PCGPinLabelList(const TArray<TObjectPtr<UPCGPin>>& Pins)
	{
		TArray<FString> Labels;
		for (const UPCGPin* P : Pins) { if (P) { Labels.Add(P->Properties.Label.ToString()); } }
		return Labels.Num() ? FString::Join(Labels, TEXT(", ")) : TEXT("(none)");
	}

	void WritePCGNodeJson(const UPCGNode* N, const TSharedRef<FJsonObject>& Out)
	{
		if (!N) { return; }
		Out->SetStringField(TEXT("node"), N->GetName());
		const UPCGSettings* S = N->GetSettings();
		Out->SetStringField(TEXT("settingsClass"), S ? S->GetClass()->GetName() : FString());
		// THE REASON THIS FIELD EXISTS: a new node's settings are where every parameter lives, and
		// returning the path means set_property can configure it in the very next call instead of
		// the caller having to work out how to address a node's settings object.
		Out->SetStringField(TEXT("settingsPath"), S ? S->GetPathName() : FString());
		TArray<TSharedPtr<FJsonValue>> InPins, OutPins;
		for (const UPCGPin* P : N->GetInputPins())
		{
			if (P) { InPins.Add(MakeShared<FJsonValueString>(P->Properties.Label.ToString())); }
		}
		for (const UPCGPin* P : N->GetOutputPins())
		{
			if (P) { OutPins.Add(MakeShared<FJsonValueString>(P->Properties.Label.ToString())); }
		}
		Out->SetArrayField(TEXT("inputPins"), InPins);
		Out->SetArrayField(TEXT("outputPins"), OutPins);
	}

	int32 CountEdgesOn(const UPCGPin* Pin)
	{
		return Pin ? Pin->Edges.Num() : 0;
	}
#endif

	// --- add_pcg_node -------------------------------------------------------
	void H_add_pcg_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graph"), TEXT("path"), TEXT("assetPath"), TEXT("settingsClass"), TEXT("class"),
			  TEXT("x"), TEXT("y") },
			TEXT("graph (aliases: path, assetPath); settingsClass (alias: class) - a UPCGSettings ")
			TEXT("subclass such as PCGSurfaceSamplerSettings; x, y - optional editor position"),
			{ { TEXT("title"), TEXT("a node's title is display text derived from its settings; the "
									"settings CLASS is its stable identity and is what this takes") },
			  { TEXT("node"), TEXT("that is an OUTPUT - the new node's name is returned to you") } }))
		{
			return;
		}
#if !MIF_WITH_PCG
		PCGUnavailable(Out, TEXT("add_pcg_node"));
#else
		UPCGGraph* Graph = ResolvePCGGraph(In, Out);
		if (!Graph) { return; }

		const FString ClassName = JStrAny(In, { TEXT("settingsClass"), TEXT("class") });
		if (ClassName.IsEmpty())
		{
			Fail(Out, TEXT("settingsClass is required - a UPCGSettings subclass. NOTHING was changed."));
			return;
		}
		UClass* Found = nullptr;
		TArray<UClass*> Candidates;
		GetDerivedClasses(UPCGSettings::StaticClass(), Candidates, /*bRecursive*/ true);
		TArray<FString> Near;
		for (UClass* C : Candidates)
		{
			if (!C || C->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated)) { continue; }
			if (C->GetName().Equals(ClassName, ESearchCase::IgnoreCase)) { Found = C; break; }
			if (Near.Num() < 10 && C->GetName().Contains(ClassName)) { Near.Add(C->GetName()); }
		}
		if (!Found)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a concrete UPCGSettings subclass (%d are registered). %s ")
				TEXT("NOTHING was changed."),
				*ClassName, Candidates.Num(),
				Near.Num() ? *FString::Printf(TEXT("Did you mean: %s?"), *FString::Join(Near, TEXT(", ")))
						   : TEXT("Node classes are named like PCGSurfaceSamplerSettings.")));
			return;
		}

		const int32 Before = Graph->GetNodes().Num();
		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_AddPCGNode", "Add PCG Node"));
		Graph->Modify();
		UPCGSettings* NewSettings = nullptr;
		UPCGNode* Node = Graph->AddNodeOfType(Found, NewSettings);
		if (!Node)
		{
			Fail(Out, FString::Printf(
				TEXT("AddNodeOfType returned nothing for '%s'. NOTHING usable was produced."),
				*Found->GetName()));
			return;
		}
#if WITH_EDITOR
		if (In->HasField(TEXT("x")) || In->HasField(TEXT("y")))
		{
			Node->SetNodePosition(static_cast<int32>(JNum(In, TEXT("x"), 0.0)),
								  static_cast<int32>(JNum(In, TEXT("y"), 0.0)));
		}
#endif
		// Read back from the GRAPH, not from the returned pointer - the pointer is non-null whether
		// or not the graph actually took it.
		if (!Graph->Contains(Node))
		{
			Fail(Out, TEXT("the node was created and the graph does not contain it on read-back. ")
				TEXT("NOTHING usable was produced."));
			return;
		}
		Graph->MarkPackageDirty();

		Out->SetStringField(TEXT("graph"), Graph->GetPathName());
		WritePCGNodeJson(Node, Out);
		Out->SetNumberField(TEXT("nodeCountBefore"), Before);
		Out->SetNumberField(TEXT("nodeCount"), Graph->GetNodes().Num());
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the graph is dirty and NOTHING has been saved. The new node is UNWIRED - a node "
				 "connected to nothing contributes nothing; use connect_pcg_nodes next, and "
				 "set_property on settingsPath to configure it."));
#endif
	}

	// --- remove_pcg_node ----------------------------------------------------
	void H_remove_pcg_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graph"), TEXT("path"), TEXT("assetPath"), TEXT("node"), TEXT("confirm") },
			TEXT("graph (aliases: path, assetPath); node - the node NAME from describe_pcg_graph; ")
			TEXT("confirm:true - removing a node also destroys every edge attached to it"),
			{ { TEXT("settingsClass"), TEXT("that identifies a node TYPE, and a graph can hold many "
											"of one type - address the one you mean by node name") } }))
		{
			return;
		}
#if !MIF_WITH_PCG
		PCGUnavailable(Out, TEXT("remove_pcg_node"));
#else
		UPCGGraph* Graph = ResolvePCGGraph(In, Out);
		if (!Graph) { return; }

		const FString NodeName = JStr(In, TEXT("node"));
		if (NodeName.IsEmpty())
		{
			Fail(Out, TEXT("node is required - a node NAME from describe_pcg_graph. NOTHING was changed."));
			return;
		}
		UPCGNode* Node = FindPCGNode(Graph, NodeName);
		if (!Node)
		{
			Fail(Out, FString::Printf(
				TEXT("no node named '%s' in this graph. It holds: %s. NOTHING was changed."),
				*NodeName, *PCGNodeNameList(Graph)));
			return;
		}
		if (Node == Graph->GetInputNode() || Node == Graph->GetOutputNode())
		{
			Fail(Out, TEXT("that is the graph's own input or output node, not an ordinary node - ")
				TEXT("removing it would leave the graph unable to receive or emit anything, and "
					 "RemoveNode does not expect it. NOTHING was changed."));
			return;
		}

		// COUNT WHAT WILL BE DESTROYED before asking, so the confirmation states a real number
		// rather than a vague warning.
		int32 AttachedEdges = 0;
		for (const UPCGPin* P : Node->GetInputPins())  { AttachedEdges += CountEdgesOn(P); }
		for (const UPCGPin* P : Node->GetOutputPins()) { AttachedEdges += CountEdgesOn(P); }

		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, FString::Printf(
				TEXT("removing '%s' also destroys the %d edge(s) attached to it, and this endpoint ")
				TEXT("cannot put them back. Pass confirm:true. NOTHING was changed."),
				*NodeName, AttachedEdges));
			return;
		}

		const int32 Before = Graph->GetNodes().Num();
		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_RemovePCGNode", "Remove PCG Node"));
		Graph->Modify();
		Graph->RemoveNode(Node);
		const int32 After = Graph->GetNodes().Num();
		if (After >= Before)
		{
			Fail(Out, FString::Printf(
				TEXT("RemoveNode ran and the graph still holds %d node(s). NOTHING was removed."),
				After));
			return;
		}
		Graph->MarkPackageDirty();

		Out->SetStringField(TEXT("graph"), Graph->GetPathName());
		Out->SetStringField(TEXT("node"), NodeName);
		Out->SetBoolField(TEXT("removed"), true);
		Out->SetNumberField(TEXT("edgesDestroyed"), AttachedEdges);
		Out->SetNumberField(TEXT("nodeCountBefore"), Before);
		Out->SetNumberField(TEXT("nodeCount"), After);
		Out->SetStringField(TEXT("assetNote"), TEXT("the graph is dirty and NOTHING has been saved."));
#endif
	}

	// --- connect_pcg_nodes --------------------------------------------------
	void H_connect_pcg_nodes(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graph"), TEXT("path"), TEXT("assetPath"), TEXT("fromNode"), TEXT("fromPin"),
			  TEXT("toNode"), TEXT("toPin") },
			TEXT("graph (aliases: path, assetPath); fromNode + fromPin (an OUTPUT pin label); ")
			TEXT("toNode + toPin (an INPUT pin label). describe_pcg_graph reports every node's ")
			TEXT("inputPinNames and outputPinNames."),
			{ { TEXT("index"), TEXT("pins are addressed by LABEL, not position - describe_pcg_graph "
									"reports the labels") } }))
		{
			return;
		}
#if !MIF_WITH_PCG
		PCGUnavailable(Out, TEXT("connect_pcg_nodes"));
#else
		UPCGGraph* Graph = ResolvePCGGraph(In, Out);
		if (!Graph) { return; }

		const FString FromName = JStr(In, TEXT("fromNode"));
		const FString ToName   = JStr(In, TEXT("toNode"));
		UPCGNode* From = FindPCGNode(Graph, FromName);
		UPCGNode* To   = FindPCGNode(Graph, ToName);
		if (!From || !To)
		{
			Fail(Out, FString::Printf(
				TEXT("no node named '%s' in this graph. It holds: %s. NOTHING was changed."),
				From ? *ToName : *FromName, *PCGNodeNameList(Graph)));
			return;
		}

		// EVERY FAILURE CASE AddLabeledEdge CHECKS IS CHECKED HERE FIRST. That is what makes its
		// return value usable: having ruled out null nodes and both bad-pin cases, a false from it
		// can only mean "added without displacing anything". Doing it the other way round - calling
		// first and interpreting the bool - cannot distinguish success from failure at all.
		const FName FromPinLabel(*JStr(In, TEXT("fromPin")));
		const FName ToPinLabel(*JStr(In, TEXT("toPin")));
		UPCGPin* FromPin = From->GetOutputPin(FromPinLabel);
		if (!FromPin)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no OUTPUT pin labelled '%s'. Its output pins are: %s. AddEdge would ")
				TEXT("have returned a valid-looking node here and wired nothing. NOTHING was changed."),
				*FromName, *FromPinLabel.ToString(), *PCGPinLabelList(From->GetOutputPins())));
			return;
		}
		UPCGPin* ToPin = To->GetInputPin(ToPinLabel);
		if (!ToPin)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no INPUT pin labelled '%s'. Its input pins are: %s. NOTHING was changed."),
				*ToName, *ToPinLabel.ToString(), *PCGPinLabelList(To->GetInputPins())));
			return;
		}

		// Already connected? The end state the caller asked for already holds.
		for (const UPCGEdge* E : FromPin->Edges)
		{
			if (E && E->OutputPin == ToPin)
			{
				Out->SetStringField(TEXT("graph"), Graph->GetPathName());
				Out->SetBoolField(TEXT("connected"), false);
				Out->SetNumberField(TEXT("edgeCount"), CountEdgesOn(FromPin));
				Out->SetStringField(TEXT("note"),
					TEXT("those pins are already connected - nothing was added, and nothing needed "
						 "to be. connected:false here means the end state you asked for is in place."));
				return;
			}
		}

		const int32 ToEdgesBefore = CountEdgesOn(ToPin);
		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_ConnectPCG", "Connect PCG Nodes"));
		Graph->Modify();
		Graph->AddLabeledEdge(From, FromPinLabel, To, ToPinLabel);

		// VERIFIED BY READING THE GRAPH BACK, never from a return value. Neither AddEdge nor
		// AddLabeledEdge can tell us this: AddEdge discards the result entirely, and
		// AddLabeledEdge's false is shared between failure and clean success.
		bool bConnected = false;
		for (const UPCGEdge* E : FromPin->Edges)
		{
			if (E && E->OutputPin == ToPin) { bConnected = true; break; }
		}
		if (!bConnected)
		{
			Fail(Out, TEXT("the edge was requested and the graph does not report it on read-back. ")
				TEXT("NOTHING usable was produced."));
			return;
		}
		Graph->MarkPackageDirty();

		// MEASURED displacement, not the bool. A single-capacity input pin silently breaks whatever
		// was already attached to it, and a caller who is not told that has lost work without any
		// error to notice.
		const int32 ToEdgesAfter = CountEdgesOn(ToPin);
		const int32 Displaced = FMath::Max(0, ToEdgesBefore + 1 - ToEdgesAfter);

		Out->SetStringField(TEXT("graph"), Graph->GetPathName());
		Out->SetBoolField(TEXT("connected"), true);
		Out->SetStringField(TEXT("from"), FString::Printf(TEXT("%s.%s"), *FromName,
														  *FromPinLabel.ToString()));
		Out->SetStringField(TEXT("to"), FString::Printf(TEXT("%s.%s"), *ToName,
														*ToPinLabel.ToString()));
		Out->SetNumberField(TEXT("edgeCount"), CountEdgesOn(FromPin));
		Out->SetNumberField(TEXT("replacedEdges"), Displaced);
		if (Displaced > 0)
		{
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("'%s.%s' does not accept multiple connections, so %d existing edge(s) were "
					 "BROKEN to make room for this one. That is the engine's behaviour, not this "
					 "endpoint's choice, and it is reported because nothing else would tell you."),
				*ToName, *ToPinLabel.ToString(), Displaced));
		}
		Out->SetStringField(TEXT("assetNote"), TEXT("the graph is dirty and NOTHING has been saved."));
#endif
	}

	// --- disconnect_pcg_nodes -----------------------------------------------
	void H_disconnect_pcg_nodes(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graph"), TEXT("path"), TEXT("assetPath"), TEXT("fromNode"), TEXT("fromPin"),
			  TEXT("toNode"), TEXT("toPin") },
			TEXT("graph (aliases: path, assetPath); fromNode + fromPin, toNode + toPin - the same ")
			TEXT("four that named the edge when it was created"),
			{ { TEXT("all"), TEXT("not supported - name the edge. Removing every edge on a node is "
								  "what remove_pcg_node does, and it says how many it will destroy") } }))
		{
			return;
		}
#if !MIF_WITH_PCG
		PCGUnavailable(Out, TEXT("disconnect_pcg_nodes"));
#else
		UPCGGraph* Graph = ResolvePCGGraph(In, Out);
		if (!Graph) { return; }

		const FString FromName = JStr(In, TEXT("fromNode"));
		const FString ToName   = JStr(In, TEXT("toNode"));
		UPCGNode* From = FindPCGNode(Graph, FromName);
		UPCGNode* To   = FindPCGNode(Graph, ToName);
		if (!From || !To)
		{
			Fail(Out, FString::Printf(
				TEXT("no node named '%s' in this graph. It holds: %s. NOTHING was changed."),
				From ? *ToName : *FromName, *PCGNodeNameList(Graph)));
			return;
		}
		const FName FromPinLabel(*JStr(In, TEXT("fromPin")));
		const FName ToPinLabel(*JStr(In, TEXT("toPin")));
		UPCGPin* FromPin = From->GetOutputPin(FromPinLabel);
		if (!FromPin)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no OUTPUT pin labelled '%s'. Its output pins are: %s. NOTHING was changed."),
				*FromName, *FromPinLabel.ToString(), *PCGPinLabelList(From->GetOutputPins())));
			return;
		}

		const int32 Before = CountEdgesOn(FromPin);
		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_DisconnectPCG", "Disconnect PCG Nodes"));
		Graph->Modify();
		const bool bReported = Graph->RemoveEdge(From, FromPinLabel, To, ToPinLabel);
		const int32 After = CountEdgesOn(FromPin);
		Graph->MarkPackageDirty();

		Out->SetStringField(TEXT("graph"), Graph->GetPathName());
		Out->SetStringField(TEXT("from"), FString::Printf(TEXT("%s.%s"), *FromName,
														  *FromPinLabel.ToString()));
		Out->SetStringField(TEXT("to"), FString::Printf(TEXT("%s.%s"), *ToName,
														*ToPinLabel.ToString()));
		// MEASURED, and cross-checked against what RemoveEdge claimed. Unlike AddEdge this one does
		// return something meaningful, so a disagreement between the two is worth surfacing rather
		// than silently trusting either.
		Out->SetNumberField(TEXT("removed"), Before - After);
		Out->SetNumberField(TEXT("edgeCount"), After);
		if (Before == After)
		{
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("no such edge, so nothing was removed - removed:0 is the measured difference in "
					 "the pin's edge count. RemoveEdge reported %s. describe_pcg_graph's edges[] "
					 "lists what is really connected."),
				bReported ? TEXT("true, which disagrees with the count and is worth knowing")
						  : TEXT("false, which agrees")));
		}
		Out->SetStringField(TEXT("assetNote"), TEXT("the graph is dirty and NOTHING has been saved."));
#endif
	}
}
