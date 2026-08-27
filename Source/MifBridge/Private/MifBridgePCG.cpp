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

#if MIF_WITH_PCG
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
			Nodes.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetStringField(TEXT("assetPath"), Graph->GetPathName());
		Out->SetArrayField(TEXT("nodes"), Nodes);
		Out->SetNumberField(TEXT("nodeCount"), Nodes.Num());
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
}
