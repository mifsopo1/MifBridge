// MifBridge — session/assets, introspection, variables, and compile read-back endpoints.
#include "MifBridgeHandlers.h"
#include "MifBridgeVersion.h"
// FStringOutputDevice MOVED between the two engines this plugin targets:
//   5.3: declared in Containers/UnrealString.h, reached transitively through CoreMinimal
//   5.7: promoted to its own header, Misc/StringOutputDevice.h, and no longer pulled in for free
//
// So the include is REQUIRED on 5.7 and IMPOSSIBLE on 5.3 - that path does not exist there, and an
// unguarded include is a fatal C1083. The Curfew session hit the 5.7 half and could not see the 5.3
// half; building here caught it. A fifth shape for docs/02_GOTCHAS.md section 14: same type, same
// name, different HEADER.
#if MIF_ENGINE_5_7_PLUS
#include "Misc/StringOutputDevice.h"
#endif
#include "Engine/Level.h"   // ULevel::GetExternalActorsPath
#include "FileHelpers.h"   // FEditorFileUtils::GetDirtyContentPackages - save_package warns about unsaved external actors
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "EdGraphToken.h"
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"   // UBlueprintGeneratedClass - how a COOKED blueprint is registered
#include "HAL/FileManager.h"
#include "K2Node.h"
#include "K2Node_CallFunction.h"
#include "K2Node_FunctionEntry.h" // local variables live on the entry node (set_variable_type scope=local)
#include "K2Node_Knot.h"
#include "K2Node_Variable.h"      // FMemberReference retarget (retarget_variable_node)
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/CompilerResultsLog.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Logging/TokenizedMessage.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "UObject/SavePackage.h"
#include "UObject/UnrealType.h" // TFieldIterator<FProperty>, FMulticastDelegateProperty (describe_class)
#include "Engine/Engine.h"   // GEngine (run_console routes its Exec through MifBridge::RunEngineExec)
#include "Engine/World.h"    // UWorld must be COMPLETE for World->GetName() in run_console's response
#include "Editor.h"          // GEditor editor world
#include "GameFramework/Actor.h" // AActor::GetIsReplicated (replication sanity warning)
#include "Engine/EngineTypes.h"  // ELifetimeCondition (replication condition)

namespace MifBridge
{
	// --- Session / assets ---------------------------------------------------

	void H_open_blueprint(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// ResolveBlueprintField reads blueprintId and falls back to path (MifBridgeCommon.cpp:3041-3047),
		// and server.py's open_blueprint posts 'path' - so BOTH spellings must stay accepted here.
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path) - the blueprint asset to open; returns blueprintId, name, class, parentClass and graphs"),
			{ { TEXT("name"), TEXT("open_blueprint addresses the asset by path, e.g. path:\"/Game/Foo/BP_Bar\"; list_blueprints {filter} finds one by a name fragment first") },
			  { TEXT("graphId"), TEXT("open_blueprint opens a whole blueprint and RETURNS its graphIds; to read one graph use list_nodes {graphId}") } }))
		{
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}

		Out->SetStringField(TEXT("blueprintId"), Blueprint->GetPathName());
		Out->SetStringField(TEXT("name"), Blueprint->GetName());
		if (Blueprint->GeneratedClass)
		{
			Out->SetStringField(TEXT("class"), Blueprint->GeneratedClass->GetPathName());
		}
		if (Blueprint->ParentClass)
		{
			Out->SetStringField(TEXT("parentClass"), Blueprint->ParentClass->GetPathName());
		}

		TArray<UEdGraph*> Graphs;
		GatherGraphs(Blueprint, Graphs);
		TArray<TSharedPtr<FJsonValue>> GraphArr;
		for (UEdGraph* Graph : Graphs)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("graphId"), GraphIdOf(Blueprint, Graph));
			Json->SetStringField(TEXT("name"), Graph->GetName());
			Json->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
			GraphArr.Add(MakeShared<FJsonValueObject>(Json));
		}
		Out->SetArrayField(TEXT("graphs"), GraphArr);
	}

	void H_list_blueprints(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("filter") },
			TEXT("filter (optional; substring matched against each blueprint's full object path - omit to list every blueprint, capped at 5000)"),
			{ { TEXT("path"),  TEXT("list_blueprints takes no path - pass the path fragment as filter, e.g. filter:\"/Game/Blueprints/\"") },
			  { TEXT("name"),  TEXT("matching runs against the FULL object path, so pass the name fragment as filter, e.g. filter:\"BP_Player\"") },
			  { TEXT("limit"), TEXT("there is no limit parameter - the result is capped at 5000 entries; narrow it with filter") } }))
		{
			return;
		}
		const FString Filter = JStr(In, TEXT("filter"));

		FAssetRegistryModule& Module = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
		IAssetRegistry& Registry = Module.Get();

		// BOTH SPELLINGS, and this used to be one. On a COOKED project a blueprint is registered as
		// its GENERATED CLASS - BlueprintGeneratedClass - and not as UBlueprint at all, so querying
		// UBlueprint alone listed only the uncooked ones. bSearchSubClasses does not help and looks
		// like it should: UBlueprint and UBlueprintGeneratedClass are different hierarchies.
		//
		// The failure had the worst possible shape. On DDS2 it returned 1818 entries - a large,
		// entirely plausible number - while filter:"VehicleBoat" returned 0 against 15 that exist.
		// An agent asking this endpoint what a cooked project contains was told a confident fraction,
		// and the 1818 gave it no reason to doubt the answer. See docs/02 section 15.
		//
		// The rest of the bridge already handles cooked blueprints properly: list_components reads
		// them, and list_graphs refuses with a real explanation that points at the KismetReconstructor
		// and create_editable_child. Only DISCOVERY was blind, so nothing ever led a caller there.
		TArray<FAssetData> Assets;
		Registry.GetAssetsByClass(UBlueprint::StaticClass()->GetClassPathName(), Assets, /*bSearchSubClasses*/ true);
		const int32 NumUncooked = Assets.Num();
		Registry.GetAssetsByClass(UBlueprintGeneratedClass::StaticClass()->GetClassPathName(), Assets, /*bSearchSubClasses*/ true);

		// An UNCOOKED blueprint can be registered under both spellings, and listing it twice would
		// make the count wrong in the other direction. Keyed on PACKAGE, which both rows share.
		TSet<FName> SeenPackages;
		TArray<TSharedPtr<FJsonValue>> Arr;
		int32 CookedListed = 0;
		bool bTruncated = false;
		for (int32 Index = 0; Index < Assets.Num(); ++Index)
		{
			const FAssetData& Asset = Assets[Index];
			const FString ObjectPath = Asset.GetObjectPathString();
			if (!Filter.IsEmpty() && !ObjectPath.Contains(Filter))
			{
				continue;
			}
			bool bAlreadySeen = false;
			SeenPackages.Add(Asset.PackageName, &bAlreadySeen);
			if (bAlreadySeen)
			{
				continue;
			}
			// FROM WHICH QUERY, not from the class path. This compared AssetClassPath against
			// UBlueprintGeneratedClass exactly, and a WidgetBlueprintGeneratedClass is a SUBCLASS
			// living in /Script/UMG - so every cooked widget and anim blueprint was listed correctly
			// and then labelled cooked:false. The rows were right and the flag was wrong, which is
			// the same shape of defect this endpoint was just fixed for.
			//
			// The append order is the exact answer: everything below NumUncooked came from the
			// UBlueprint query. Dedup keeps the FIRST hit, so a blueprint registered under both
			// spellings is correctly reported as uncooked.
			const bool bCooked = Index >= NumUncooked;
			if (bCooked) { ++CookedListed; }
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("blueprintId"), ObjectPath);
			Json->SetStringField(TEXT("name"), Asset.AssetName.ToString());
			Json->SetStringField(TEXT("package"), Asset.PackageName.ToString());
			Json->SetBoolField(TEXT("cooked"), bCooked);
			Arr.Add(MakeShared<FJsonValueObject>(Json));
			if (Arr.Num() >= 5000)
			{
				// SAY SO. Stopping here silently is a truncation the caller cannot see: there is no limit
				// parameter to blame, so the answer looks complete. Someone searching for a blueprint that
				// sorts after the 5000th would be told it does not exist. Not reachable on this project
				// today (1744 blueprints), which is exactly why it is worth flagging now rather than on the
				// day it starts lying.
				bTruncated = true;
				break; // safety cap
			}
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetNumberField(TEXT("cookedCount"), CookedListed);
		Out->SetNumberField(TEXT("uncookedRegistered"), NumUncooked);
		Out->SetArrayField(TEXT("blueprints"), Arr);
		if (CookedListed > 0)
		{
			// Said once at the top level rather than repeated on every row. A caller who sees
			// cooked:true and does not know what it implies would otherwise go straight to list_graphs
			// and get a refusal - which is a good refusal, but a wasted round trip.
			Out->SetStringField(TEXT("cookedNote"), FString::Printf(
				TEXT("%d of these are COOKED (cooked:true). Cooked packages strip Blueprint graphs, so "
					 "list_graphs / list_nodes / find_nodes cannot read them - their components and "
					 "properties still read normally. To read the logic, decompile with "
					 "run_console {\"command\":\"mif.kr.Reconstruct <Name>\"}; to edit it, mint an "
					 "editable copy with create_editable_child."), CookedListed));
		}
		if (bTruncated)
		{
			Out->SetBoolField(TEXT("truncated"), true);
			Out->SetStringField(TEXT("truncatedNote"),
				TEXT("stopped at the 5000-entry safety cap, so this list is INCOMPLETE and a blueprint you "
					 "cannot find here may still exist. Narrow it with filter."));
		}
	}

	void H_save_blueprint(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path) - writes the package that owns this blueprint back to disk, in place"),
			{ { TEXT("savePath"), TEXT("save_blueprint has no save-as: it rewrites the blueprint's OWN package. To save a different asset use save_package {path}.") },
			  { TEXT("compile"),  TEXT("save_blueprint does not compile - call compile {blueprintId} first if the blueprint has pending structural changes") } }))
		{
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}

		UPackage* Package = Blueprint->GetOutermost();
		// A World must be written as .umap, NOT .uasset. GetAssetPackageExtension() is unconditional,
		// so saving a map used to drop an M_Foo.uasset beside the real M_Foo.umap — and the resolver
		// searches .uasset FIRST, so the stray file then silently shadowed the actual level on every
		// later load. ContainsMap() is the same test the engine's own save path uses.
		const FString FileName = FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			Package->ContainsMap() ? FPackageName::GetMapPackageExtension() : FPackageName::GetAssetPackageExtension());

		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;

		const bool bSaved = UPackage::SavePackage(Package, nullptr, *FileName, SaveArgs);
		if (bSaved)
		{
			Out->SetStringField(TEXT("savedTo"), FileName);
		}
		else
		{
			Fail(Out, FString::Printf(TEXT("save failed for %s"), *Package->GetName()));
		}
	}

	// Save ANY asset's package to disk by /Game/ path (DataTables, materials, etc. — not just Blueprints).
	// An asset the editor loaded from a mounted game pak saves as a LOOSE Content override, which the cook then
	// bakes into a _P — the DataTable-redirect lane (repoint SoftEquipmentActorClass to a child + save + cook).
	void H_save_package(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path") },
			TEXT("path - the /Game/ object path of ANY asset; the package that owns it is marked dirty and written to disk"),
			{ { TEXT("blueprintId"), TEXT("save_package addresses any asset by its /Game/ object path, so pass it as path. For a Blueprint, save_blueprint {blueprintId} does the same thing.") },
			  { TEXT("package"),     TEXT("pass the ASSET's object path as path (e.g. /Game/Data/DT_Items) - the owning package is derived from it") },
			  { TEXT("assetPath"),   TEXT("spell it path") } }))
		{
			return;
		}
		const FString Path = JStr(In, TEXT("path"));
		if (Path.IsEmpty()) { Fail(Out, TEXT("path is required")); return; }
		UObject* Asset = LoadObject<UObject>(nullptr, *Path);
		if (!Asset) { Fail(Out, FString::Printf(TEXT("asset not found: %s"), *Path)); return; }
		UPackage* Package = Asset->GetOutermost();
		Package->MarkPackageDirty();
		// A World must be written as .umap, NOT .uasset. GetAssetPackageExtension() is unconditional,
		// so saving a map used to drop an M_Foo.uasset beside the real M_Foo.umap — and the resolver
		// searches .uasset FIRST, so the stray file then silently shadowed the actual level on every
		// later load. ContainsMap() is the same test the engine's own save path uses.
		const FString FileName = FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			Package->ContainsMap() ? FPackageName::GetMapPackageExtension() : FPackageName::GetAssetPackageExtension());
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		const bool bSaved = UPackage::SavePackage(Package, nullptr, *FileName, SaveArgs);
		if (!bSaved)
		{
			Fail(Out, FString::Printf(TEXT("save failed for %s"), *Package->GetName()));
			return;
		}
		Out->SetStringField(TEXT("savedTo"), FileName);

		// A TRUE ok THAT LOSES A SESSION'S WORK. On a World Partition map with One-File-Per-Actor, every
		// actor lives in its OWN package under __ExternalActors__. Saving the map package writes the map
		// and nothing else, so ok:true is perfectly accurate and 409 placed actors stay dirty in memory,
		// one level reload away from being gone. That happened for real while building L_City_P in Curfew
		// and is the most expensive item in the field reports merged on 2026-08-26.
		//
		// The accuracy is exactly what makes it dangerous: nothing in the response was wrong, it simply
		// answered a narrower question than the caller asked. So say what is left.
		if (Package->ContainsMap())
		{
			if (const UWorld* World = UWorld::FindWorldInPackage(Package))
			{
				if (World->IsPartitionedWorld())
				{
					const FString ExternalRoot = ULevel::GetExternalActorsPath(Package);
					TArray<UPackage*> DirtyContent;
					FEditorFileUtils::GetDirtyContentPackages(DirtyContent);
					int32 DirtyExternal = 0;
					for (const UPackage* P : DirtyContent)
					{
						if (P && !ExternalRoot.IsEmpty() && P->GetName().StartsWith(ExternalRoot))
						{
							++DirtyExternal;
						}
					}
					Out->SetBoolField(TEXT("partitionedWorld"), true);
					Out->SetNumberField(TEXT("dirtyExternalActorPackages"), DirtyExternal);
					if (DirtyExternal > 0)
					{
						Out->SetStringField(TEXT("note"), FString::Printf(
							TEXT("the MAP package was written, but this is a World Partition map and %d external ")
							TEXT("actor package(s) are STILL DIRTY - their actors live in their own packages and ")
							TEXT("are NOT saved by this call. They will be lost on the next level reload. Use ")
							TEXT("save_dirty_packages {maps:true, content:true}."), DirtyExternal));
					}
				}
			}
		}
	}

	void H_backup_blueprint(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path) - copies the blueprint's package file on disk to a backup, returned as 'backup'"),
			{ { TEXT("destination"), TEXT("backup_blueprint picks the backup location itself and reports it as 'backup' in the response; it takes no destination") } }))
		{
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}

		// The body that used to live here (ContainsMap branch, COPY_OK check, not-on-disk refusal) is
		// now MifBridge::BackupPackage in MifBridgeCommon.cpp, because batch had a DEGRADED inline copy
		// of it: hardcoded .uasset, discarded Copy()'s return, silent skip. One implementation means a
		// caller passing backup:true to batch gets the same guarantees this endpoint already gave.
		FString BackupPath, BackupError;
		if (!BackupPackage(Blueprint->GetOutermost(), BackupPath, BackupError))
		{
			Fail(Out, BackupError);
			return;
		}
		Out->SetStringField(TEXT("backup"), BackupPath);
	}

	// --- Introspection ------------------------------------------------------

	void H_list_graphs(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path) - lists every graph in the blueprint, nested ones included, with its graphId"),
			{ { TEXT("graphId"), TEXT("list_graphs RETURNS graphIds, it does not take one - to read a single graph use list_nodes {graphId}") },
			  { TEXT("filter"),  TEXT("list_graphs has no filter; it returns every graph. find_nodes {graphId, byTitle} searches inside one graph.") } }))
		{
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		TArray<UEdGraph*> Graphs;
		GatherGraphs(Blueprint, Graphs);
		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UEdGraph* Graph : Graphs)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("graphId"), GraphIdOf(Blueprint, Graph));
			Json->SetStringField(TEXT("name"), Graph->GetName());
			Json->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
			Arr.Add(MakeShared<FJsonValueObject>(Json));
		}
		Out->SetArrayField(TEXT("graphs"), Arr);
	}

	void H_list_nodes(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// ResolveGraphField reads ONLY graphId (MifBridgeCommon.cpp:3205-3212); hideKnots is read here.
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("hideKnots") },
			TEXT("graphId ('<blueprintPath>::<graphName>', exactly as open_blueprint/list_graphs return it), hideKnots (default false; true skips reroute nodes)"),
			{ { TEXT("graph"),       TEXT("spell it graphId") },
			  { TEXT("blueprintId"), TEXT("list_nodes reads ONE graph - pass graphId from open_blueprint/list_graphs, not a blueprint path") },
			  { TEXT("path"),        TEXT("this endpoint selects a GRAPH, so pass graphId ('<blueprintPath>::<graphName>'); a bare blueprint path does not name a graph") },
			  { TEXT("hideReroute"), TEXT("spell it hideKnots (a reroute node is a UK2Node_Knot)") } }))
		{
			return;
		}
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const bool bHideKnots = JBool(In, TEXT("hideKnots"), false);
		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (!Node)
			{
				continue;
			}
			if (bHideKnots && Node->IsA<UK2Node_Knot>())
			{
				continue;
			}
			// Resolve links through knots ONLY when knots are hidden. With hideKnots=false the knot
			// nodes are present in the response, so the raw links are already resolvable and the
			// caller should see the real topology.
			Arr.Add(MakeShared<FJsonValueObject>(
				SerializeNode(Node, /*bIncludePins*/ true, /*bResolveThroughKnots*/ bHideKnots)));
		}
		Out->SetStringField(TEXT("graphId"), GraphIdOf(Blueprint, Graph));
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("nodes"), Arr);
		if (bHideKnots)
		{
			int32 KnotsHidden = 0;
			for (UEdGraphNode* N : Graph->Nodes)
			{
				if (N && N->IsA<UK2Node_Knot>()) { ++KnotsHidden; }
			}
			Out->SetNumberField(TEXT("knotsHidden"), KnotsHidden);
			Out->SetBoolField(TEXT("linksResolvedThroughKnots"), true);
			if (KnotsHidden > 0)
			{
				Out->SetStringField(TEXT("knotNote"), FString::Printf(
					TEXT("%d reroute node(s) were omitted and every link through them was resolved to its logical far end (marked viaKnots). "
						 "The response is self-contained: no linkedTo entry points at a node missing from nodes[]. "
						 "A link that could NOT be resolved (a knot fan-out) is marked unresolvedKnot instead of silently dangling."),
					KnotsHidden));
			}
		}
	}

	void H_get_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// The node-id aliases are NOT garnish: ResolveNodeField treats "nodeGuid" as a GENERIC field and
		// reads JStrAny(In, { nodeGuid, node, guid, nodeId }), and it also honours an optional graphId to
		// scope the lookup (MifBridgeCommon.cpp:3272-3326). Listing only "nodeGuid" would turn a payload
		// that works today into a hard "unrecognised parameter" failure - the set_pin_type break again.
		if (RejectUnknownParams(In, Out,
			{ TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId") },
			TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional - scopes the guid lookup to that one graph, the only way to disambiguate two loaded copies of a blueprint sharing NodeGuids)"),
			{ { TEXT("pin"),         TEXT("get_node already returns EVERY pin on the node; there is no pin filter") },
			  { TEXT("blueprintId"), TEXT("a node is addressed by its guid, not by its blueprint - pass graphId if you need to disambiguate two loaded copies") } }))
		{
			return;
		}
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
		if (!Node)
		{
			return;
		}
		Out->SetObjectField(TEXT("node"), SerializeNode(Node, /*bIncludePins*/ true));
	}

	void H_list_variables(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path) - lists the blueprint's MEMBER variables with name, type, default, flags and a suspiciousName marker"),
			{ { TEXT("filter"), TEXT("list_variables has no filter; it returns every member variable") },
			  { TEXT("scope"),  TEXT("list_variables reports member variables only (scope is always \"member\" in the response); a local variable lives on its function graph and is not listed here") },
			  { TEXT("name"),   TEXT("list_variables lists them all - there is no single-variable lookup; read the entry you want out of variables[]") } }))
		{
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		TArray<TSharedPtr<FJsonValue>> Arr;
		for (const FBPVariableDescription& Var : Blueprint->NewVariables)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			const FString NameStr = Var.VarName.ToString();
			Json->SetStringField(TEXT("name"), NameStr);
			Json->SetStringField(TEXT("scope"), TEXT("member"));
			Json->SetObjectField(TEXT("type"), SerializePinType(Var.VarType));
			if (!Var.DefaultValue.IsEmpty())
			{
				Json->SetStringField(TEXT("default"), Var.DefaultValue);
			}
			// Replication / SaveGame / editability state, so set_variable_flags is verifiable
			// without opening the Details panel.
			Json->SetObjectField(TEXT("flags"), SerializeVariableFlags(Blueprint, Var));
			// Flag names with trailing/leading whitespace or non-identifier bytes — the
			// exact trap ("BestPotIndex ") that was invisible in the details panel.
			FString Trimmed = NameStr;
			Trimmed.TrimStartAndEndInline();
			if (Trimmed != NameStr || !IsValidIdentifier(NameStr))
			{
				Json->SetBoolField(TEXT("suspiciousName"), true);
			}
			Arr.Add(MakeShared<FJsonValueObject>(Json));
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("variables"), Arr);
	}

	void H_list_functions(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path) - lists the blueprint's own function graphs with name and graphId"),
			{ { TEXT("filter"), TEXT("list_functions has no filter; it returns every function graph") },
			  { TEXT("class"),  TEXT("list_functions reads a BLUEPRINT's own function graphs - to reflect over any class's BlueprintCallable functions use describe_class {class, filter}") } }))
		{
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			if (!Graph)
			{
				continue;
			}
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), Graph->GetName());
			Json->SetStringField(TEXT("graphId"), GraphIdOf(Blueprint, Graph));
			Arr.Add(MakeShared<FJsonValueObject>(Json));
		}
		Out->SetArrayField(TEXT("functions"), Arr);
	}

	// --- describe_class -------------------------------------------------------
	// Reflects over ANY resolvable class (native or Blueprint-generated) — its BlueprintCallable
	// functions (with param names/types/direction), BlueprintVisible properties, and multicast
	// delegates (dispatchers, with their signature params). Added after repeatedly having to
	// fall back to reading decompiled/engine source just to find out whether a class exposed a
	// particular function or dispatcher (e.g. hunting for a GameMode's player-join delegate).
	// Optional "filter": substring match against function/property names.
	void H_describe_class(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// 'className' is not a courtesy alias: server.py's describe_class tool has always posted
		// className, and this handler read only 'class', so EVERY MCP call to it answered
		// "class is required" to a caller that plainly supplied a class — an error naming the wrong
		// party, 100% of the time, surviving because the handler had no guard to name the mismatch.
		// The alias fixes today's callers; the guard makes the next spelling drift loud instead.
		if (RejectUnknownParams(In, Out,
			{ TEXT("class"), TEXT("className"), TEXT("filter") },
			TEXT("class (alias: className), filter (optional substring match)")))
		{
			return;
		}
		const FString Name = JStrAny(In, { TEXT("class"), TEXT("className") });
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("class is required (alias: className)"));
			return;
		}
		UClass* Class = ResolveClass(Name, nullptr);
		if (!Class)
		{
			Fail(Out, FString::Printf(TEXT("class not found: '%s'"), *Name));
			return;
		}
		const FString Filter = JStr(In, TEXT("filter"));

		Out->SetStringField(TEXT("class"), Class->GetName());
		Out->SetStringField(TEXT("path"), Class->GetPathName());
		Out->SetStringField(TEXT("parentClass"), Class->GetSuperClass() ? Class->GetSuperClass()->GetPathName() : FString());

		TArray<TSharedPtr<FJsonValue>> Functions;
		for (TFieldIterator<UFunction> FuncIt(Class); FuncIt; ++FuncIt)
		{
			UFunction* Func = *FuncIt;
			if (!Func || !Func->HasAnyFunctionFlags(FUNC_BlueprintCallable) || Func->HasAnyFunctionFlags(FUNC_Delegate))
			{
				continue;
			}
			const FString FuncName = Func->GetName();
			if (!Filter.IsEmpty() && !FuncName.Contains(Filter))
			{
				continue;
			}

			TArray<TSharedPtr<FJsonValue>> Params;
			for (TFieldIterator<FProperty> PropIt(Func); PropIt && PropIt->HasAnyPropertyFlags(CPF_Parm); ++PropIt)
			{
				FProperty* Prop = *PropIt;
				TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
				P->SetStringField(TEXT("name"), Prop->GetName());
				P->SetStringField(TEXT("type"), Prop->GetCPPType());
				const TCHAR* Direction = Prop->HasAnyPropertyFlags(CPF_ReturnParm) ? TEXT("return")
					: (Prop->HasAnyPropertyFlags(CPF_OutParm) && !Prop->HasAnyPropertyFlags(CPF_ConstParm)) ? TEXT("out")
					: TEXT("in");
				P->SetStringField(TEXT("direction"), Direction);
				Params.Add(MakeShared<FJsonValueObject>(P));
			}

			TSharedRef<FJsonObject> F = MakeShared<FJsonObject>();
			F->SetStringField(TEXT("name"), FuncName);
			F->SetBoolField(TEXT("isPure"), Func->HasAnyFunctionFlags(FUNC_BlueprintPure));
			F->SetBoolField(TEXT("isStatic"), Func->HasAnyFunctionFlags(FUNC_Static));
			F->SetArrayField(TEXT("params"), Params);
			Functions.Add(MakeShared<FJsonValueObject>(F));
		}
		Out->SetArrayField(TEXT("functions"), Functions);

		TArray<TSharedPtr<FJsonValue>> Properties;
		TArray<TSharedPtr<FJsonValue>> Dispatchers;
		for (TFieldIterator<FProperty> PropIt(Class); PropIt; ++PropIt)
		{
			FProperty* Prop = *PropIt;
			if (!Prop)
			{
				continue;
			}
			const FString PropName = Prop->GetName();
			if (!Filter.IsEmpty() && !PropName.Contains(Filter))
			{
				continue;
			}

			if (FMulticastDelegateProperty* Delegate = CastField<FMulticastDelegateProperty>(Prop))
			{
				TSharedRef<FJsonObject> D = MakeShared<FJsonObject>();
				D->SetStringField(TEXT("name"), PropName);
				TArray<TSharedPtr<FJsonValue>> Params;
				if (UFunction* Sig = Delegate->SignatureFunction)
				{
					for (TFieldIterator<FProperty> SigIt(Sig); SigIt && SigIt->HasAnyPropertyFlags(CPF_Parm); ++SigIt)
					{
						TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
						P->SetStringField(TEXT("name"), SigIt->GetName());
						P->SetStringField(TEXT("type"), SigIt->GetCPPType());
						Params.Add(MakeShared<FJsonValueObject>(P));
					}
				}
				D->SetArrayField(TEXT("params"), Params);
				Dispatchers.Add(MakeShared<FJsonValueObject>(D));
			}
			else if (Prop->HasAnyPropertyFlags(CPF_BlueprintVisible))
			{
				TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
				P->SetStringField(TEXT("name"), PropName);
				P->SetStringField(TEXT("type"), Prop->GetCPPType());
				Properties.Add(MakeShared<FJsonValueObject>(P));
			}
		}
		Out->SetArrayField(TEXT("properties"), Properties);
		Out->SetArrayField(TEXT("dispatchers"), Dispatchers);
	}

	void H_find_nodes(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// ResolveGraphField reads ONLY graphId (MifBridgeCommon.cpp:3205-3212); the three by* filters
		// are read below. An unlisted filter spelling used to be dropped silently, which returns EVERY
		// node in the graph while looking like a successful search.
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("byClass"), TEXT("byTitle"), TEXT("byFunction") },
			TEXT("graphId, byClass (substring of the node's C++ class name), byTitle (substring of the node title), byFunction (substring of the called function name) - every filter is optional and they are ANDed"),
			{ { TEXT("class"),       TEXT("spell it byClass, e.g. byClass:\"K2Node_CallFunction\"") },
			  { TEXT("title"),       TEXT("spell it byTitle") },
			  { TEXT("function"),    TEXT("spell it byFunction") },
			  { TEXT("name"),        TEXT("find_nodes has no 'name': use byTitle for the node's displayed title, or byFunction for the name of the function it calls") },
			  { TEXT("blueprintId"), TEXT("find_nodes searches ONE graph - pass graphId from open_blueprint/list_graphs") } }))
		{
			return;
		}
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		const FString ByClass = JStr(In, TEXT("byClass"));
		const FString ByTitle = JStr(In, TEXT("byTitle"));
		const FString ByFunction = JStr(In, TEXT("byFunction"));

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (!Node)
			{
				continue;
			}
			bool bMatch = true;
			if (!ByClass.IsEmpty() && !Node->GetClass()->GetName().Contains(ByClass))
			{
				bMatch = false;
			}
			if (bMatch && !ByTitle.IsEmpty() && !Node->GetNodeTitle(ENodeTitleType::ListView).ToString().Contains(ByTitle))
			{
				bMatch = false;
			}
			if (bMatch && !ByFunction.IsEmpty())
			{
				UK2Node_CallFunction* CallFn = Cast<UK2Node_CallFunction>(Node);
				if (!CallFn || !CallFn->FunctionReference.GetMemberName().ToString().Contains(ByFunction))
				{
					bMatch = false;
				}
			}
			if (bMatch)
			{
				Arr.Add(MakeShared<FJsonValueObject>(SerializeNode(Node, /*bIncludePins*/ false)));
			}
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("nodes"), Arr);
	}

	// --- Variables ----------------------------------------------------------

	// Replication / SaveGame / editability flags.
	//
	// These are the checkboxes in the variable Details panel. Only SOME of them have an engine setter
	// (SetVariableSaveGameFlag / SetVariableTransientFlag / ...); replication in particular has none —
	// FBlueprintVarActionDetails::OnChangeReplication pokes the flag word returned by
	// GetBlueprintVariablePropertyFlags directly, and stores the OnRep function name separately via
	// SetBlueprintVariableRepNotifyFunc. We mirror that sequence exactly rather than inventing one.
	// (BlueprintDetailsCustomization.cpp, UE 5.3: OnChangeReplication / ReplicationOnRepFuncChanged /
	// OnChangeReplicationCondition.)

	TSharedRef<FJsonObject> SerializeVariableFlags(UBlueprint* Blueprint, const FBPVariableDescription& Var)
	{
		TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
		const uint64 F = Var.PropertyFlags;
		J->SetBoolField(TEXT("replicated"), (F & CPF_Net) != 0);
		J->SetBoolField(TEXT("repNotify"), (F & CPF_RepNotify) != 0);
		if (Var.RepNotifyFunc != NAME_None)
		{
			J->SetStringField(TEXT("repNotifyFunction"), Var.RepNotifyFunc.ToString());
		}
		if (const UEnum* CondEnum = StaticEnum<ELifetimeCondition>())
		{
			J->SetStringField(TEXT("replicationCondition"), CondEnum->GetNameStringByValue((int64)Var.ReplicationCondition.GetValue()));
		}
		J->SetBoolField(TEXT("saveGame"), (F & CPF_SaveGame) != 0);
		J->SetBoolField(TEXT("transient"), (F & CPF_Transient) != 0);
		J->SetBoolField(TEXT("config"), (F & CPF_Config) != 0);
		// "Instance Editable" is the ABSENCE of DisableEditOnInstance plus Edit — matching the checkbox.
		J->SetBoolField(TEXT("instanceEditable"), (F & CPF_Edit) != 0 && (F & CPF_DisableEditOnInstance) == 0);
		J->SetBoolField(TEXT("blueprintReadOnly"), (F & CPF_BlueprintReadOnly) != 0);
		J->SetBoolField(TEXT("exposeOnSpawn"), (F & CPF_ExposeOnSpawn) != 0);
		J->SetBoolField(TEXT("advancedDisplay"), (F & CPF_AdvancedDisplay) != 0);
		J->SetBoolField(TEXT("interp"), (F & CPF_Interp) != 0);
		J->SetBoolField(TEXT("deprecated"), (F & CPF_Deprecated) != 0);
		J->SetStringField(TEXT("category"), Var.Category.ToString());
		// The tooltip is writable through set_variable_flags but was not reported anywhere, so it
		// could be set and never read back to confirm it landed - found by the round-trip audit.
		bool bFieldNotify = false;
		for (const FBPVariableMetaDataEntry& Meta : Var.MetaDataArray)
		{
			if (Meta.DataKey == FBlueprintMetadata::MD_Tooltip && !Meta.DataValue.IsEmpty())
			{
				J->SetStringField(TEXT("tooltip"), Meta.DataValue);
			}
			else if (Meta.DataKey == FBlueprintMetadata::MD_FieldNotify)
			{
				bFieldNotify = true;
			}
		}
		J->SetBoolField(TEXT("fieldNotify"), bFieldNotify);
		return J;
	}

	static FBPVariableDescription* FindMemberVariable(UBlueprint* Blueprint, const FName& VarName)
	{
		const int32 Index = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VarName);
		return Index != INDEX_NONE ? &Blueprint->NewVariables[Index] : nullptr;
	}

	bool ApplyVariableFlags(UBlueprint* Blueprint, const FName& VarName, const TSharedRef<FJsonObject>& In,
		const TSharedRef<FJsonObject>& Out, FString& OutError)
	{
		if (!FindMemberVariable(Blueprint, VarName))
		{
			// Local (function-scope) variables have no replication/SaveGame concept at all: they live on
			// the stack of one call, never on the CDO, so there is nothing for the net driver or
			// SaveGame serializer to see. Say that instead of silently no-op'ing.
			OutError = FString::Printf(
				TEXT("'%s' is not a MEMBER variable of %s. These flags apply to member variables only ")
				TEXT("(local/function-scope variables are never replicated or saved)."),
				*VarName.ToString(), *Blueprint->GetName());
			return false;
		}

		Blueprint->Modify();
		bool bTouched = false;

		// --- Replication -------------------------------------------------------------
		// GetBlueprintVariablePropertyFlags returns a POINTER INTO NewVariables[i].PropertyFlags,
		// so writing through it is the edit. Re-fetch after any call that could reallocate the array.
		if (JHasAny(In, { TEXT("replicated"), TEXT("repNotifyFunction"), TEXT("repNotify") }))
		{
			uint64* FlagPtr = FBlueprintEditorUtils::GetBlueprintVariablePropertyFlags(Blueprint, VarName);
			if (!FlagPtr)
			{
				OutError = FString::Printf(TEXT("could not access property flags for '%s'"), *VarName.ToString());
				return false;
			}

			FString RepNotifyFn = JStr(In, TEXT("repNotifyFunction"));
			RepNotifyFn.TrimStartAndEndInline();
			const bool bWantRepNotify = !RepNotifyFn.IsEmpty() || JBool(In, TEXT("repNotify"), false);
			// Asking for a RepNotify implies replication — the editor's RepNotify option sets CPF_Net too.
			const bool bReplicated = JBool(In, TEXT("replicated"), bWantRepNotify) || bWantRepNotify;

			if (bReplicated)
			{
				*FlagPtr |= CPF_Net;

				if (bWantRepNotify)
				{
					// Default to the engine's own naming so the graph matches what the Details panel makes.
					if (RepNotifyFn.IsEmpty())
					{
						RepNotifyFn = FString::Printf(TEXT("OnRep_%s"), *VarName.ToString());
					}
					if (!IsValidIdentifier(RepNotifyFn))
					{
						OutError = FString::Printf(TEXT("invalid repNotifyFunction '%s'"), *RepNotifyFn);
						return false;
					}
					// The OnRep handler must EXIST or the compiler errors out. Mint the graph if absent —
					// same as FBlueprintVarActionDetails::OnChangeReplication's RepNotify branch.
					UEdGraph* FuncGraph = FindObject<UEdGraph>(Blueprint, *RepNotifyFn);
					if (!FuncGraph)
					{
						FuncGraph = FBlueprintEditorUtils::CreateNewGraph(
							Blueprint, FName(*RepNotifyFn), UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
						FBlueprintEditorUtils::AddFunctionGraph<UClass>(Blueprint, FuncGraph, /*bIsUserCreated*/ false, static_cast<UClass*>(nullptr));
						Out->SetStringField(TEXT("createdRepNotifyGraph"), RepNotifyFn);
					}
					FBlueprintEditorUtils::SetBlueprintVariableRepNotifyFunc(Blueprint, VarName, FName(*RepNotifyFn));
					FlagPtr = FBlueprintEditorUtils::GetBlueprintVariablePropertyFlags(Blueprint, VarName);
					if (FlagPtr) { *FlagPtr |= (CPF_RepNotify | CPF_Net); }
				}
				else
				{
					FBlueprintEditorUtils::SetBlueprintVariableRepNotifyFunc(Blueprint, VarName, NAME_None);
					FlagPtr = FBlueprintEditorUtils::GetBlueprintVariablePropertyFlags(Blueprint, VarName);
					if (FlagPtr) { *FlagPtr &= ~CPF_RepNotify; }
				}
			}
			else
			{
				*FlagPtr &= ~CPF_Net;
				FBlueprintEditorUtils::SetBlueprintVariableRepNotifyFunc(Blueprint, VarName, NAME_None);
				FlagPtr = FBlueprintEditorUtils::GetBlueprintVariablePropertyFlags(Blueprint, VarName);
				if (FlagPtr) { *FlagPtr &= ~CPF_RepNotify; }
				if (FBPVariableDescription* Var = FindMemberVariable(Blueprint, VarName))
				{
					Var->ReplicationCondition = COND_None;   // mirrors the editor's None branch
				}
			}
			bTouched = true;
		}

		// --- Replication condition (COND_*) -----------------------------------------
		if (In->HasField(TEXT("replicationCondition")))
		{
			const FString CondStr = JStr(In, TEXT("replicationCondition"));
			const UEnum* CondEnum = StaticEnum<ELifetimeCondition>();
			int64 CondValue = CondEnum ? CondEnum->GetValueByNameString(CondStr) : INDEX_NONE;
			if (CondValue == INDEX_NONE && CondEnum && !CondStr.StartsWith(TEXT("COND_")))
			{
				CondValue = CondEnum->GetValueByNameString(TEXT("COND_") + CondStr);
			}
			if (CondValue == INDEX_NONE)
			{
				// Batch M, option (c): the repNotify branch above may already have MINTED an OnRep
				// function graph, and a cancelled transaction discards the undo entry rather than
				// removing it (PM-007). The response already names it in createdRepNotifyGraph; say
				// so here too, because the caller reads the error string first.
				OutError = FString::Printf(TEXT("unknown replicationCondition '%s' (expected an ELifetimeCondition, e.g. COND_None, COND_OwnerOnly, COND_SkipOwner, COND_InitialOnly). If repNotify was also requested, an OnRep function graph may already have been created for it - see createdRepNotifyGraph in this response; it is NOT removed by this failure."), *CondStr);
				return false;
			}
			FBPVariableDescription* Var = FindMemberVariable(Blueprint, VarName);
			if (Var)
			{
				// The condition is only consulted when the property is actually replicated.
				if ((Var->PropertyFlags & CPF_Net) == 0)
				{
					Out->SetStringField(TEXT("warning"),
						TEXT("replicationCondition was set but the variable is not replicated — pass replicated=true for it to take effect"));
				}
				Var->ReplicationCondition = (ELifetimeCondition)CondValue;
				bTouched = true;
			}
		}

		// --- Engine-provided flag setters -------------------------------------------
		if (In->HasField(TEXT("saveGame")))
		{
			FBlueprintEditorUtils::SetVariableSaveGameFlag(Blueprint, VarName, JBool(In, TEXT("saveGame")));
			bTouched = true;
		}
		if (In->HasField(TEXT("transient")))
		{
			FBlueprintEditorUtils::SetVariableTransientFlag(Blueprint, VarName, JBool(In, TEXT("transient")));
			bTouched = true;
		}
		if (In->HasField(TEXT("advancedDisplay")))
		{
			FBlueprintEditorUtils::SetVariableAdvancedDisplayFlag(Blueprint, VarName, JBool(In, TEXT("advancedDisplay")));
			bTouched = true;
		}
		if (In->HasField(TEXT("deprecated")))
		{
			FBlueprintEditorUtils::SetVariableDeprecatedFlag(Blueprint, VarName, JBool(In, TEXT("deprecated")));
			bTouched = true;
		}
		if (In->HasField(TEXT("interp")))
		{
			FBlueprintEditorUtils::SetInterpFlag(Blueprint, VarName, JBool(In, TEXT("interp")));
			bTouched = true;
		}
		if (In->HasField(TEXT("blueprintReadOnly")))
		{
			FBlueprintEditorUtils::SetBlueprintPropertyReadOnlyFlag(Blueprint, VarName, JBool(In, TEXT("blueprintReadOnly")));
			bTouched = true;
		}
		if (In->HasField(TEXT("category")))
		{
			const FString Category = JStr(In, TEXT("category"));
			FBlueprintEditorUtils::SetBlueprintVariableCategory(Blueprint, VarName, nullptr, FText::FromString(Category));
			bTouched = true;
		}
		if (In->HasField(TEXT("tooltip")))
		{
			FBlueprintEditorUtils::SetBlueprintVariableMetaData(Blueprint, VarName, nullptr, TEXT("ToolTip"), JStr(In, TEXT("tooltip")));
			bTouched = true;
		}
		// FIELD NOTIFY - the MVVM binding system's "this property broadcasts when it changes" flag,
		// the same checkbox FieldNotifyToggle.cpp puts in the Blueprint Variables panel. Only
		// meaningful on a class implementing INotifyFieldValueChanged (UMVVMViewModelBase and its
		// Blueprint children) - checked nowhere here on purpose, same as every other flag in this
		// function: the engine call itself is the source of truth, not a guess about which classes
		// happen to support a flag today.
		if (In->HasField(TEXT("fieldNotify")))
		{
			if (JBool(In, TEXT("fieldNotify")))
			{
				FBlueprintEditorUtils::SetBlueprintVariableMetaData(
					Blueprint, VarName, nullptr, FBlueprintMetadata::MD_FieldNotify, FString());
			}
			else
			{
				// Two calls, matching FieldNotifyToggle.cpp's OFF branch exactly: the plain metadata
				// remove alone leaves compiled delegate bindings referencing a field that no longer
				// broadcasts - RemoveFieldNotifyFromAllMetadata is the one that also cleans those up.
				FBlueprintEditorUtils::RemoveFieldNotifyFromAllMetadata(Blueprint, VarName);
				FBlueprintEditorUtils::RemoveBlueprintVariableMetaData(
					Blueprint, VarName, nullptr, FBlueprintMetadata::MD_FieldNotify);
			}
			bTouched = true;
		}

		// --- Flags with no engine setter: poke the description directly --------------
		{
			// exposeOnSpawn implies instanceEditable (a spawn pin the caller fills must be per-instance).
			const bool bHasExpose = In->HasField(TEXT("exposeOnSpawn"));
			const bool bHasEditable = In->HasField(TEXT("instanceEditable"));
			const bool bExposeOnSpawn = JBool(In, TEXT("exposeOnSpawn"), false);
			if (bHasExpose || bHasEditable || In->HasField(TEXT("config")))
			{
				FBPVariableDescription* Var = FindMemberVariable(Blueprint, VarName);
				if (Var)
				{
					if (bHasEditable || bExposeOnSpawn)
					{
						if (JBool(In, TEXT("instanceEditable"), false) || bExposeOnSpawn)
						{
							Var->PropertyFlags &= ~CPF_DisableEditOnInstance;
							Var->PropertyFlags |= (CPF_Edit | CPF_BlueprintVisible);
						}
						else
						{
							Var->PropertyFlags |= CPF_DisableEditOnInstance;
						}
					}
					if (bHasExpose)
					{
						if (bExposeOnSpawn)
						{
							Var->PropertyFlags |= CPF_ExposeOnSpawn;
							Var->SetMetaData(TEXT("ExposeOnSpawn"), TEXT("true"));
						}
						else
						{
							Var->PropertyFlags &= ~CPF_ExposeOnSpawn;
							Var->RemoveMetaData(TEXT("ExposeOnSpawn"));
						}
					}
					if (In->HasField(TEXT("config")))
					{
						if (JBool(In, TEXT("config"))) { Var->PropertyFlags |= CPF_Config; }
						else                           { Var->PropertyFlags &= ~CPF_Config; }
					}
					bTouched = true;
				}
			}
		}

		if (bTouched)
		{
			// Skeleton regen — the FProperty carrying these flags is synthesised from NewVariables.
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		}

		if (const FBPVariableDescription* Var = FindMemberVariable(Blueprint, VarName))
		{
			Out->SetObjectField(TEXT("flags"), SerializeVariableFlags(Blueprint, *Var));
			// A replicated property does nothing unless the owning Actor itself replicates. This is the
			// single most common "I ticked Replicated and nothing happened" cause, so surface it rather
			// than flipping bReplicates behind the caller's back.
			if ((Var->PropertyFlags & CPF_Net) != 0)
			{
				// Non-Actor blueprints (widgets, objects, components) fall out of the Cast and are
				// correctly left alone — bReplicates is an Actor concept.
				if (AActor* ActorCDO = Blueprint->GeneratedClass ? Cast<AActor>(Blueprint->GeneratedClass->GetDefaultObject()) : nullptr)
				{
					if (!ActorCDO->GetIsReplicated())
					{
						Out->SetStringField(TEXT("replicationWarning"),
							TEXT("variable is replicated but the owning Actor has bReplicates=false — set it with "
							     "set_property {propertyPath:\"bReplicates\", value:\"True\"} on the class default object, "
							     "or the property will never be sent"));
					}
				}
			}
		}
		return true;
	}

	//   in:  { blueprintId, name, replicated?, repNotify?, repNotifyFunction?, replicationCondition?,
	//          saveGame?, transient?, config?, instanceEditable?, blueprintReadOnly?, exposeOnSpawn?,
	//          advancedDisplay?, interp?, deprecated?, category?, tooltip? }
	//   out: { name, flags:{...}, createdRepNotifyGraph?, replicationWarning? }
	// Only keys actually PRESENT are applied, so this is a partial update — omitting a flag leaves it alone.
	void H_set_variable_flags(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// The flag keys are NOT read in this body - ApplyVariableFlags above reads every one of them
		// (replicated/repNotify/repNotifyFunction/replicationCondition via JHasAny, the rest via
		// HasField). They MUST all be listed or a working {replicated:true} call becomes a hard failure.
		// blueprintId/path come from ResolveBlueprintField; name/var/variable from the JStrAny below.
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"),
			  TEXT("name"), TEXT("var"), TEXT("variable"),
			  TEXT("replicated"), TEXT("repNotify"), TEXT("repNotifyFunction"), TEXT("replicationCondition"),
			  TEXT("saveGame"), TEXT("transient"), TEXT("config"),
			  TEXT("instanceEditable"), TEXT("blueprintReadOnly"), TEXT("exposeOnSpawn"),
			  TEXT("advancedDisplay"), TEXT("interp"), TEXT("deprecated"),
			  TEXT("category"), TEXT("tooltip"), TEXT("fieldNotify") },
			TEXT("blueprintId (alias: path), name (aliases: var, variable), then any of replicated, repNotify, ")
			TEXT("repNotifyFunction, replicationCondition, saveGame, transient, config, instanceEditable, ")
			TEXT("blueprintReadOnly, exposeOnSpawn, advancedDisplay, interp, deprecated, category, tooltip, ")
			TEXT("fieldNotify (the MVVM \"broadcasts on change\" flag, meaningful only on a class ")
			TEXT("implementing INotifyFieldValueChanged such as an MVVM ViewModel Blueprint) ")
			TEXT("- PARTIAL UPDATE: only the keys actually present are applied, the rest are left alone"),
			{ { TEXT("variableName"), TEXT("spell it name (aliases: var, variable)") },
			  { TEXT("replicate"),    TEXT("spell it replicated - and repNotify:true already implies it") },
			  { TEXT("editable"),     TEXT("spell it instanceEditable (the Details-panel \"Instance Editable\" checkbox)") },
			  { TEXT("readOnly"),     TEXT("spell it blueprintReadOnly") },
			  { TEXT("condition"),    TEXT("spell it replicationCondition - an ELifetimeCondition such as COND_OwnerOnly; the COND_ prefix is optional") },
			  { TEXT("onRep"),        TEXT("spell it repNotifyFunction; omit it and repNotify:true mints OnRep_<Name> for you") },
			  { TEXT("default"),      TEXT("set_variable_flags only sets flags - use set_variable_default {blueprintId, name, value} to change a variable's default") },
			  { TEXT("type"),         TEXT("set_variable_flags cannot retype a variable; the type is fixed at add_variable {type:\"object:X\"} time") } }))
		{
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		const FString Name = JStrAny(In, { TEXT("name"), TEXT("var"), TEXT("variable") });
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required (the member variable to flag)"));
			return;
		}

		FString Error;
		if (!ApplyVariableFlags(Blueprint, FName(*Name), In, Out, Error))
		{
			Fail(Out, Error);
			return;
		}
		Out->SetStringField(TEXT("name"), Name);
	}

	// Resolve the `scope` parameter, or refuse it.
	//
	// Both add_variable and set_variable_type did `Scope.Equals("local")` and treated EVERYTHING else
	// as member - so scope:"loca1", scope:"function", scope:"banana" all silently produced a MEMBER
	// variable. add_variable then echoed the request back as `scope`, so a typo answered
	// ok:true scope:"loca1" for a variable that is a member. The documented values are member|local;
	// anything else is a caller mistake and saying so costs one comparison.
	//
	// This is the silent-ignore class the module already has a backstop for - a supplied parameter
	// that is accepted and then quietly means something other than what was asked.
	static bool MifResolveVariableScope(const FString& Raw, bool& bOutLocal, FString& OutError)
	{
		FString S = Raw;
		S.TrimStartAndEndInline();
		if (S.IsEmpty() || S.Equals(TEXT("member"), ESearchCase::IgnoreCase))
		{
			bOutLocal = false;
			return true;
		}
		if (S.Equals(TEXT("local"), ESearchCase::IgnoreCase))
		{
			bOutLocal = true;
			return true;
		}
		OutError = FString::Printf(
			TEXT("scope '%s' is not recognised - the only values are 'member' (the default, a variable on ")
			TEXT("the blueprint) and 'local' (a variable on one function graph, which also needs ")
			TEXT("'function'). Nothing was changed."), *Raw);
		return false;
	}

	void H_add_variable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// This guard is here because its ABSENCE cost a real user a working design. Wanting an object
		// variable typed to a specific class, they tried `class`, `className`, `parentClass`,
		// `objectClass` and `subType` alongside type:"object" — five spellings, all accepted, all
		// silently dropped, every call reporting ok:true and producing a plain UObject that would not
		// connect to a SceneComponent pin. They concluded the bridge could not type object variables
		// and redesigned around it. It can: the class goes INSIDE the type string.
		// The KeyNotes below turn that dead end into one round-trip.
		// The flag keys (replicated..tooltip, fieldNotify) are NOT read in this body - ApplyVariableFlags
		// below reads every one of them, the same shared path set_variable_flags uses. They MUST all be
		// listed here or a working add_variable{..., replicated:true} call becomes a hard "unrecognised
		// parameter" failure - which is exactly what was happening: this list omitted every one of them
		// even though the FlagKeys[] block further down was already written to apply them. Found and
		// fixed 2026-08-28, filed as task_1920c65f the night it was found.
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"),
			  TEXT("name"), TEXT("type"), TEXT("container"), TEXT("valueType"),
			  TEXT("scope"), TEXT("function"), TEXT("default"),
			  TEXT("replicated"), TEXT("repNotify"), TEXT("repNotifyFunction"), TEXT("replicationCondition"),
			  TEXT("saveGame"), TEXT("transient"), TEXT("config"), TEXT("instanceEditable"),
			  TEXT("blueprintReadOnly"), TEXT("exposeOnSpawn"), TEXT("advancedDisplay"), TEXT("interp"),
			  TEXT("deprecated"), TEXT("category"), TEXT("tooltip"), TEXT("fieldNotify") },
			TEXT("blueprintId (alias: path), name, type, container?, valueType?, scope? (member|local), ")
			TEXT("function? (required when scope=local), default?, and optionally any set_variable_flags ")
			TEXT("flag (replicated, repNotify, repNotifyFunction, replicationCondition, saveGame, transient, ")
			TEXT("config, instanceEditable, blueprintReadOnly, exposeOnSpawn, advancedDisplay, interp, ")
			TEXT("deprecated, category, tooltip, fieldNotify) to set at creation time - member scope only"),
			{ { TEXT("class"),       TEXT("the class belongs IN the type string, not in its own key: type:\"object:SceneComponent\". Prefixes: object:X, class:X, subclassof:X, softobject:X, softclass:X") },
			  { TEXT("className"),   TEXT("use type:\"object:X\" (or class:X / subclassof:X / softobject:X / softclass:X)") },
			  { TEXT("parentClass"), TEXT("add_variable does not take a parent class. For a typed object variable use type:\"object:X\"; to override a parent's event use add_override_event") },
			  { TEXT("objectClass"), TEXT("use type:\"object:X\"") },
			  { TEXT("subType"),     TEXT("use type:\"object:X\" for the referenced class, or valueType for a map's value type") } }))
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
			Fail(Out, FString::Printf(TEXT("invalid variable name '%s' (must match ^[A-Za-z_][A-Za-z0-9_]*$)"), *Raw));
			return;
		}

		FEdGraphPinType PinType;
		FString TypeError;
		if (!MakePinType(JStr(In, TEXT("type")), JStr(In, TEXT("container")), PinType, TypeError, JStr(In, TEXT("valueType"))))
		{
			Fail(Out, TypeError);
			return;
		}

		const FString Scope = JStr(In, TEXT("scope"), TEXT("member"));
		bool bScopeLocal = false;
		{
			FString ScopeError;
			if (!MifResolveVariableScope(Scope, bScopeLocal, ScopeError))
			{
				Fail(Out, ScopeError);
				return;
			}
		}
		const FString Default = JStr(In, TEXT("default"));

		Blueprint->Modify();

		bool bAdded = false;
		if (bScopeLocal)
		{
			const FString FunctionName = JStr(In, TEXT("function"));
			UEdGraph* FunctionGraph = nullptr;
			for (UEdGraph* Graph : Blueprint->FunctionGraphs)
			{
				if (Graph && Graph->GetName() == FunctionName)
				{
					FunctionGraph = Graph;
					break;
				}
			}
			if (!FunctionGraph)
			{
				Fail(Out, FString::Printf(TEXT("function graph '%s' not found for a local variable"), *FunctionName));
				return;
			}
			bAdded = FBlueprintEditorUtils::AddLocalVariable(Blueprint, FunctionGraph, FName(*Name), PinType, Default);
		}
		else
		{
			bAdded = FBlueprintEditorUtils::AddMemberVariable(Blueprint, FName(*Name), PinType, Default);
		}

		if (!bAdded)
		{
			Fail(Out, FString::Printf(TEXT("failed to add variable '%s' (name already in use?)"), *Name));
			return;
		}

		// Apply any flags passed at creation time (replicated / repNotify / saveGame / instanceEditable /
		// exposeOnSpawn / ...) through the SAME path set_variable_flags uses, so the two can never drift.
		// Member variables only — locals have none of these concepts.
		const bool bIsLocal = Scope.Equals(TEXT("local"), ESearchCase::IgnoreCase);
		static const TCHAR* const FlagKeys[] = {
			TEXT("replicated"), TEXT("repNotify"), TEXT("repNotifyFunction"), TEXT("replicationCondition"),
			TEXT("saveGame"), TEXT("transient"), TEXT("config"), TEXT("instanceEditable"),
			TEXT("blueprintReadOnly"), TEXT("exposeOnSpawn"), TEXT("advancedDisplay"), TEXT("interp"),
			TEXT("deprecated"), TEXT("category"), TEXT("tooltip"), TEXT("fieldNotify")
		};
		bool bAnyFlagRequested = false;
		for (const TCHAR* Key : FlagKeys)
		{
			if (In->HasField(Key)) { bAnyFlagRequested = true; break; }
		}

		if (bAnyFlagRequested && bIsLocal)
		{
			Out->SetStringField(TEXT("warning"),
				TEXT("flag options (replicated/saveGame/instanceEditable/...) were ignored: they apply to member variables only, and scope=local was requested"));
		}
		else if (bAnyFlagRequested)
		{
			FString FlagError;
			if (!ApplyVariableFlags(Blueprint, FName(*Name), In, Out, FlagError))
			{
				// The variable itself was created; report the flag failure without pretending it wasn't.
				Fail(Out, FString::Printf(TEXT("variable '%s' was created but its flags could not be applied: %s"), *Name, *FlagError));
				return;
			}
		}

		Out->SetStringField(TEXT("name"), Name); // canonical (trimmed) name
		// The RESOLVED scope, not the string that was sent. Echoing the request meant a value the
		// handler had reinterpreted was reported back as though it had been honoured.
		Out->SetStringField(TEXT("scope"), bScopeLocal ? TEXT("local") : TEXT("member"));
		Out->SetObjectField(TEXT("type"), SerializePinType(PinType));
	}

	// Member-variable names on a blueprint, for near-miss suggestions in not-found errors.
	static TArray<FString> MemberVariableNames(UBlueprint* Blueprint)
	{
		TArray<FString> Names;
		if (Blueprint)
		{
			for (const FBPVariableDescription& Var : Blueprint->NewVariables)
			{
				Names.Add(Var.VarName.ToString());
			}
		}
		return Names;
	}

	// "inherited from AActor" when the name exists on the parent class rather than on this blueprint,
	// otherwise empty. remove_variable/rename_variable only ever search Blueprint->NewVariables, and
	// the engine calls they wrap early-return on a miss, so without this an inherited name produced a
	// confident ok:true for a no-op.
	static FString DescribeInheritedVariable(UBlueprint* Blueprint, const FString& Name)
	{
		if (!Blueprint || !Blueprint->ParentClass || Name.IsEmpty()) { return FString(); }
		if (FProperty* Inherited = Blueprint->ParentClass->FindPropertyByName(FName(*Name)))
		{
			const UStruct* Owner = Inherited->GetOwnerStruct();
			return Owner ? Owner->GetName() : Blueprint->ParentClass->GetName();
		}
		return FString();
	}

	void H_rename_variable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("oldName"), TEXT("newName"), TEXT("confirm") },
			TEXT("blueprintId (alias: path), oldName, newName, confirm=true"),
			{ { TEXT("name"), TEXT("rename_variable needs BOTH oldName and newName; there is no single 'name'") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("rename_variable requires confirm=true"));
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		const FString OldName = JStr(In, TEXT("oldName"));
		FString NewName = JStr(In, TEXT("newName"));
		NewName.TrimStartAndEndInline();
		if (OldName.IsEmpty() || NewName.IsEmpty())
		{
			Fail(Out, TEXT("oldName and newName are required"));
			return;
		}
		if (!IsValidIdentifier(NewName))
		{
			Fail(Out, FString::Printf(TEXT("invalid new name '%s'"), *NewName));
			return;
		}

		// FBlueprintEditorUtils::RenameMemberVariable is VOID and early-returns when the variable does
		// not exist (BlueprintEditorUtils.cpp:4823-4824), so the old code reported
		// ok:true, name:"<NewName>" for a rename that never happened. Every refusal below exists
		// because the engine's own answer to it is silence.
		const int32 VarIndex = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*OldName));
		if (VarIndex == INDEX_NONE)
		{
			const FString Inherited = DescribeInheritedVariable(Blueprint, OldName);
			if (!Inherited.IsEmpty())
			{
				Fail(Out, FString::Printf(
					TEXT("oldName '%s' is INHERITED from %s, not declared on '%s' — a blueprint cannot rename a ")
					TEXT("variable it does not own. Rename it where it is declared, or add a new variable here."),
					*OldName, *Inherited, *Blueprint->GetName()));
				return;
			}
			Fail(Out, FString::Printf(TEXT("oldName: no member variable '%s' on '%s'%s — list_variables shows what exists"),
				*OldName, *Blueprint->GetName(), *NearMissSuggestion(MemberVariableNames(Blueprint), OldName)));
			return;
		}

		// FName comparison is case-insensitive, and RenameMemberVariable early-returns on equal names
		// (BlueprintEditorUtils.cpp:4821) — which also means "fix the casing of Health to health" is
		// not something this endpoint can do, so say so rather than reporting a rename that did not run.
		if (FName(*OldName) == FName(*NewName))
		{
			Fail(Out, FString::Printf(
				TEXT("newName '%s' is the same variable name as oldName '%s' (blueprint variable names compare ")
				TEXT("case-insensitively), so there is nothing to rename — the engine would silently do nothing."),
				*NewName, *OldName));
			return;
		}
		if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*NewName)) != INDEX_NONE)
		{
			Fail(Out, FString::Printf(TEXT("newName '%s' is already a member variable on '%s' — pick a free name"),
				*NewName, *Blueprint->GetName()));
			return;
		}

		const FBPVariableDescription& Var = Blueprint->NewVariables[VarIndex];

		// An event dispatcher is a PC_MCDelegate member variable PLUS a signature graph. Renaming
		// only the variable — which is all RenameMemberVariable does — leaves the graph behind under
		// the old name, and the next skeleton regen breaks the dispatcher. Refuse and redirect.
		if (Var.VarType.PinCategory == UEdGraphSchema_K2::PC_MCDelegate)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is the backing delegate of an event dispatcher, not a plain variable. ")
				TEXT("Renaming it here would orphan the signature graph and break the dispatcher on the next compile — ")
				TEXT("use rename_event_dispatcher, which renames both halves."), *OldName));
			return;
		}

		// MODAL HAZARD — the reason this refusal exists at all. With a RepNotify function set,
		// RenameMemberVariable calls VerifyUserWantsRepNotifyVariableNameChanged
		// (BlueprintEditorUtils.cpp:4837), which pops an FSuppressableWarningDialog. Every bridge
		// handler runs INLINE on the game thread inside the HTTP ticker (MifBridgeServer.cpp), so a
		// modal stops the ticker: the socket is never read again and the WHOLE bridge hangs until a
		// human clicks the dialog — the docs/02_GOTCHAS.md §8 failure that took the bridge down live.
		// Worse, clicking "No" makes the engine revert the name (:4841) while this handler would still
		// have answered ok:true. delete_asset passes bShowConfirmation=false to close the same class of
		// hole; RenameMemberVariable offers no such flag, so the only safe move is to make the modal
		// path UNREACHABLE from HTTP and tell the caller how to clear the gate themselves.
		if (Var.RepNotifyFunc != NAME_None)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has a RepNotify function ('%s'), and the engine's rename path opens a MODAL dialog for that ")
				TEXT("case. A modal blocks the game thread this HTTP server runs on, so it would hang the entire bridge ")
				TEXT("until someone clicks it. Clear the RepNotify first with ")
				TEXT("set_variable_flags {blueprintId, name:\"%s\", repNotify:false}, rename, then set it again."),
				*OldName, *Var.RepNotifyFunc.ToString(), *OldName));
			return;
		}

		Blueprint->Modify();
		FBlueprintEditorUtils::RenameMemberVariable(Blueprint, FName(*OldName), FName(*NewName));

		// READ BACK. The engine call is void; the only honest evidence the rename happened is that the
		// new name now resolves and the old one does not.
		const bool bNewPresent = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*NewName)) != INDEX_NONE;
		const bool bOldGone    = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*OldName)) == INDEX_NONE;
		if (!bNewPresent || !bOldGone)
		{
			// A cancelled transaction discards the undo entry; it does NOT undo the engine call above
			// (PM-007). This branch means RenameMemberVariable did not take, so there is nothing to
			// undo — but do not read the old comment here ("leaves the blueprint untouched") as a
			// general guarantee, because it is not one.
			Fail(Out, FString::Printf(
				TEXT("rename of '%s' to '%s' did not take (after the call: newName present=%s, oldName gone=%s). ")
				TEXT("Nothing was changed."),
				*OldName, *NewName, bNewPresent ? TEXT("true") : TEXT("false"), bOldGone ? TEXT("true") : TEXT("false")));
			return;
		}
		Out->SetStringField(TEXT("name"), NewName);
		Out->SetStringField(TEXT("previousName"), OldName);
		Out->SetBoolField(TEXT("renamed"), true);
	}

	void H_remove_variable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm") },
			TEXT("blueprintId (alias: path), name, confirm=true")))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_variable requires confirm=true"));
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
			Fail(Out, TEXT("name is required"));
			return;
		}

		// FBlueprintEditorUtils::RemoveMemberVariable is VOID and early-returns when the variable is
		// absent (BlueprintEditorUtils.cpp:4609-4610), so {name:"Typo", confirm:true} used to answer
		// ok:true, removed:"Typo" having removed nothing — a confirm-gated destructive endpoint whose
		// success report was unconditional. delete_datatable_rows in this same plugin gets this right
		// (it emits notFound[]); this is drift, not an unknown.
		if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*Name)) == INDEX_NONE)
		{
			// Only NewVariables is searched by the engine call, so an inherited name is a guaranteed
			// no-op and deserves its own answer rather than a bare "not found".
			const FString Inherited = DescribeInheritedVariable(Blueprint, Name);
			if (!Inherited.IsEmpty())
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is INHERITED from %s, not declared on '%s'. A blueprint cannot remove a variable it ")
					TEXT("does not own — this call would have changed nothing. Remove it where it is declared."),
					*Name, *Inherited, *Blueprint->GetName()));
				return;
			}
			Fail(Out, FString::Printf(TEXT("no member variable '%s' on '%s'%s — list_variables shows what exists"),
				*Name, *Blueprint->GetName(), *NearMissSuggestion(MemberVariableNames(Blueprint), Name)));
			return;
		}

		Blueprint->Modify();
		FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, FName(*Name));

		// READ BACK: the engine call reports nothing, so "removed" must be an observation.
		if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*Name)) != INDEX_NONE)
		{
			// A cancelled transaction discards the undo entry; it does NOT undo the engine call above
			// (PM-007). This branch means RemoveMemberVariable did not take, so there is nothing to
			// undo — but do not read the old comment here as a general guarantee, because it is not one.
			Fail(Out, FString::Printf(TEXT("'%s' is still a member variable after RemoveMemberVariable — nothing was removed"), *Name));
			return;
		}
		Out->SetStringField(TEXT("removed"), Name);
		Out->SetBoolField(TEXT("removedVerified"), true);
	}

	// Finds the function graph a local variable lives on, and its scope struct.
	// Locals are stored on the function's K2Node_FunctionEntry, NOT in Blueprint->NewVariables,
	// which is why list_variables/remove_variable (member-only) cannot see or touch them.
	static UEdGraph* FindFunctionGraphByName(UBlueprint* Blueprint, const FString& FunctionName)
	{
		if (!Blueprint) { return nullptr; }
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			if (Graph && Graph->GetName() == FunctionName) { return Graph; }
		}
		return nullptr;
	}

	// Reads a local variable's current pin type off the function entry node. Used for the
	// before/after read-back: ChangeLocalVariableType is void like its member sibling.
	static bool FindLocalVariableType(UEdGraph* FunctionGraph, const FName VarName, FEdGraphPinType& OutType)
	{
		if (!FunctionGraph) { return false; }
		for (UEdGraphNode* Node : FunctionGraph->Nodes)
		{
			UK2Node_FunctionEntry* Entry = Cast<UK2Node_FunctionEntry>(Node);
			if (!Entry) { continue; }
			for (const FBPVariableDescription& Local : Entry->LocalVariables)
			{
				if (Local.VarName == VarName) { OutType = Local.VarType; return true; }
			}
		}
		return false;
	}

	// --- set_variable_type ------------------------------------------------------
	//   in:  { blueprintId|path, name, type, container?, valueType?, scope?, function? }
	//   out: { name, scope, typeBefore, typeAfter, changed }
	//
	// The gap this fills: there was no way to RETYPE an existing variable. The only route was
	// remove_variable + add_variable, which drops every Get/Set node referencing it and forces a
	// manual rewire of each — and remove_variable is member-only, so a local could not be retyped
	// at ALL. Retyping in place is exactly what the engine's own "change variable type" dropdown
	// does: it keeps the nodes and reconnects what still type-checks.
	void H_set_variable_type(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("name"),
			  TEXT("type"), TEXT("container"), TEXT("valueType"),
			  TEXT("scope"), TEXT("function") },
			TEXT("blueprintId (alias: path), name, type, container?, valueType?, scope? (member|local), ")
			TEXT("function? (required when scope=local)"),
			{ { TEXT("class"),       TEXT("the class belongs IN the type string: type:\"object:BP_Foo_C\". Prefixes: object:X, class:X, subclassof:X, softobject:X, softclass:X") },
			  { TEXT("newType"),     TEXT("spell it type") },
			  { TEXT("targetClass"), TEXT("use type:\"object:X\" — targetClass is retarget_variable_node's key, for repointing a NODE at another class") } }))
		{
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint) { return; }

		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty()) { Fail(Out, TEXT("name is required (the variable to retype)")); return; }

		FEdGraphPinType NewType;
		FString TypeError;
		if (!MakePinType(JStr(In, TEXT("type")), JStr(In, TEXT("container")), NewType, TypeError, JStr(In, TEXT("valueType"))))
		{
			Fail(Out, TypeError);
			return;
		}

		const FString Scope = JStr(In, TEXT("scope"), TEXT("member"));
		bool bLocal = false;
		{
			FString ScopeError;
			if (!MifResolveVariableScope(Scope, bLocal, ScopeError))
			{
				Fail(Out, ScopeError);
				return;
			}
		}

		FEdGraphPinType BeforeType;
		UEdGraph* FunctionGraph = nullptr;

		if (bLocal)
		{
			const FString FunctionName = JStr(In, TEXT("function"));
			if (FunctionName.IsEmpty())
			{
				Fail(Out, TEXT("scope=local requires 'function' (the function graph the local lives on)"));
				return;
			}
			FunctionGraph = FindFunctionGraphByName(Blueprint, FunctionName);
			if (!FunctionGraph)
			{
				Fail(Out, FString::Printf(TEXT("function graph '%s' not found — list_graphs shows what exists"), *FunctionName));
				return;
			}
			if (!FindLocalVariableType(FunctionGraph, FName(*Name), BeforeType))
			{
				Fail(Out, FString::Printf(TEXT("no local variable '%s' on function '%s'"), *Name, *FunctionName));
				return;
			}
		}
		else
		{
			const int32 VarIndex = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*Name));
			if (VarIndex == INDEX_NONE)
			{
				// Same reasoning as remove_variable: ChangeMemberVariableType early-returns on a miss,
				// so without this an inherited or misspelled name would report a retype that never ran.
				const FString Inherited = DescribeInheritedVariable(Blueprint, Name);
				if (!Inherited.IsEmpty())
				{
					Fail(Out, FString::Printf(
						TEXT("'%s' is INHERITED from %s, not declared on '%s' — a blueprint cannot retype a variable it ")
						TEXT("does not own. Retype it where it is declared."),
						*Name, *Inherited, *Blueprint->GetName()));
					return;
				}
				Fail(Out, FString::Printf(TEXT("no member variable '%s' on '%s'%s — list_variables shows what exists"),
					*Name, *Blueprint->GetName(), *NearMissSuggestion(MemberVariableNames(Blueprint), Name)));
				return;
			}
			BeforeType = Blueprint->NewVariables[VarIndex].VarType;
		}

		if (BeforeType == NewType)
		{
			// Not a failure, but say so plainly rather than reporting changed:true for a no-op.
			Out->SetStringField(TEXT("name"), Name);
			Out->SetStringField(TEXT("scope"), bLocal ? TEXT("local") : TEXT("member"));
			Out->SetObjectField(TEXT("typeBefore"), SerializePinType(BeforeType));
			Out->SetObjectField(TEXT("typeAfter"), SerializePinType(BeforeType));
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetStringField(TEXT("note"), TEXT("variable already has this exact type — nothing was written"));
			return;
		}

		// MODAL HAZARD - this is the bug the suppression guard exists for, and it was a live hang.
		// Both engine calls below check whether the variable has ANY referencing node - in this
		// blueprint or in a loaded CHILD blueprint - and if so open an FSuppressableWarningDialog
		// titled "Change Variable Type" (BlueprintEditorUtils.cpp:5035 and :5605). A modal on the
		// game thread stops the HTTP ticker outright: the bridge stops answering, and only a human
		// clicking the box brings it back. Retyping a variable that HAS nodes is the normal case,
		// so this was reachable from an ordinary call - add_variable, add a Get node, retype.
		//
		// The refusal used by rename_variable is not available here: refusing every variable that
		// has nodes would refuse the whole point of the endpoint. Suppressing the dialog instead
		// makes the engine take its Suppressed branch, which BOTH verify-functions treat as
		// consent - the same answer the caller gave by calling this endpoint. The read-back below
		// is unchanged and still decides what is reported, so a suppressed dialog cannot turn a
		// refusal into a false ok.
		Blueprint->Modify();
		if (bLocal)
		{
			// ChangeLocalVariableType wants the SCOPE struct (the generated function), not the graph.
			// The skeleton class is the scope the engine itself resolves locals against between
			// compiles; fall back to the generated class if the skeleton has not been built yet.
			UStruct* LocalScope = nullptr;
			if (UClass* Skel = Blueprint->SkeletonGeneratedClass)
			{
				LocalScope = Skel->FindFunctionByName(FunctionGraph->GetFName());
			}
			if (!LocalScope && Blueprint->GeneratedClass)
			{
				LocalScope = Blueprint->GeneratedClass->FindFunctionByName(FunctionGraph->GetFName());
			}
			if (!LocalScope)
			{
				Fail(Out, FString::Printf(
					TEXT("could not resolve the scope struct for function '%s'. Compile the blueprint once, then retry — ")
					TEXT("ChangeLocalVariableType needs the generated function to exist."), *FunctionGraph->GetName()));
				return;
			}
			{
				FMifScopedDialogSuppression NoModal(TEXT("ChangeVariableType_Warning"));
				FBlueprintEditorUtils::ChangeLocalVariableType(Blueprint, LocalScope, FName(*Name), NewType);
			}
		}
		else
		{
			FMifScopedDialogSuppression NoModal(TEXT("ChangeVariableType_Warning"));
			FBlueprintEditorUtils::ChangeMemberVariableType(Blueprint, FName(*Name), NewType);
		}

		// READ BACK. Both engine calls are void and both early-return on rejection (e.g. a type the
		// schema refuses), so "changed" has to be an observation, never an assumption.
		FEdGraphPinType AfterType;
		bool bFound = false;
		if (bLocal)
		{
			bFound = FindLocalVariableType(FunctionGraph, FName(*Name), AfterType);
		}
		else
		{
			const int32 Idx = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*Name));
			if (Idx != INDEX_NONE) { AfterType = Blueprint->NewVariables[Idx].VarType; bFound = true; }
		}
		if (!bFound)
		{
			Fail(Out, FString::Printf(TEXT("'%s' could not be read back after the retype — the variable is missing. Nothing was verified."), *Name));
			return;
		}
		if (!(AfterType == NewType))
		{
			Fail(Out, FString::Printf(
				TEXT("retype of '%s' did NOT take: the variable is still '%s' after the call. The schema rejected the ")
				TEXT("requested type. Nothing was changed."),
				*Name, *UEdGraphSchema_K2::TypeToText(AfterType).ToString()));
			return;
		}

		Out->SetStringField(TEXT("name"), Name);
		Out->SetStringField(TEXT("scope"), bLocal ? TEXT("local") : TEXT("member"));
		Out->SetObjectField(TEXT("typeBefore"), SerializePinType(BeforeType));
		Out->SetObjectField(TEXT("typeAfter"), SerializePinType(AfterType));
		Out->SetBoolField(TEXT("changed"), true);
		Out->SetStringField(TEXT("note"),
			TEXT("existing Get/Set nodes were kept and reconstructed; links whose types no longer match were dropped by the schema — compile to see which"));
	}

	// --- retarget_variable_node -------------------------------------------------
	//   in:  { graphId, node (aliases: nodeGuid/guid/nodeId), targetClass|self }
	//   out: { node, variable, ownerBefore, ownerAfter, changed }
	//
	// A K2Node_VariableGet/Set carries an FMemberReference naming BOTH the variable and the class
	// that declares it. set_pin_type can repaint the pins but leaves that reference pointing at the
	// old class, which compiles as "Variable node ... uses an invalid target". This repoints the
	// reference itself and reconstructs the node, which is the only thing that actually fixes it.
	void H_retarget_variable_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"),
			  TEXT("targetClass"), TEXT("class"), TEXT("self") },
			TEXT("graphId, node (aliases: nodeGuid, guid, nodeId), targetClass (alias: class) OR self:true"),
			{ { TEXT("type"), TEXT("retarget_variable_node changes WHICH CLASS declares the variable, not the pin type — use set_variable_type for the type") },
			  { TEXT("var"),  TEXT("the variable is taken from the node you name; to place a NEW node use add_variable_get/add_variable_set with targetClass") } }))
		{
			return;
		}

		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node) { return; }
		UEdGraph* Graph = Node->GetGraph();

		UK2Node_Variable* VarNode = Cast<UK2Node_Variable>(Node);
		if (!VarNode)
		{
			Fail(Out, FString::Printf(
				TEXT("node '%s' is a %s, not a variable Get/Set — there is no variable reference to retarget"),
				*Node->NodeGuid.ToString(EGuidFormats::Digits), *Node->GetClass()->GetName()));
			return;
		}

		const FName VarName = VarNode->VariableReference.GetMemberName();
		UClass* OwnerBefore = VarNode->VariableReference.GetMemberParentClass();
		const bool bWantSelf = JBool(In, TEXT("self"), false);
		const FString TargetClassStr = JStr(In, TEXT("targetClass"), JStr(In, TEXT("class")));

		if (!bWantSelf && TargetClassStr.IsEmpty())
		{
			Fail(Out, TEXT("pass targetClass (the class that declares the variable) or self:true (this blueprint declares it)"));
			return;
		}

		UClass* NewOwner = nullptr;
		if (!bWantSelf)
		{
			UBlueprint* ContextBP = FBlueprintEditorUtils::FindBlueprintForNode(Node);
			FString ClassError;
			NewOwner = ResolveClassStrict(TargetClassStr, ContextBP, TEXT("targetClass"), ClassError);
			if (!NewOwner)
			{
				Fail(Out, ClassError);
				return;
			}
			// Refuse a retarget the compiler is guaranteed to reject, rather than writing it and
			// letting the next compile report an "invalid target" the caller has to decode.
			if (!NewOwner->FindPropertyByName(VarName))
			{
				Fail(Out, FString::Printf(
					TEXT("class '%s' has no property named '%s' — retargeting there would produce an invalid variable ")
					TEXT("node. Nothing was changed."), *NewOwner->GetName(), *VarName.ToString()));
				return;
			}
		}

		Node->Modify();
		Graph->Modify();
		if (bWantSelf) { VarNode->VariableReference.SetSelfMember(VarName); }
		else           { VarNode->VariableReference.SetExternalMember(VarName, NewOwner); }
		VarNode->ReconstructNode();

		if (UBlueprint* OwningBP = FBlueprintEditorUtils::FindBlueprintForNode(Node))
		{
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(OwningBP);
		}

		// READ BACK: SetExternalMember/SetSelfMember are void.
		UClass* OwnerAfter = VarNode->VariableReference.GetMemberParentClass();
		const bool bSelfAfter = VarNode->VariableReference.IsSelfContext();
		if (bWantSelf && !bSelfAfter)
		{
			Fail(Out, TEXT("retarget to self did not take — the node is still an external member reference. Nothing was verified."));
			return;
		}
		if (!bWantSelf && OwnerAfter != NewOwner)
		{
			Fail(Out, FString::Printf(TEXT("retarget did not take: owner is still '%s'. Nothing was verified."),
				OwnerAfter ? *OwnerAfter->GetName() : TEXT("<none>")));
			return;
		}

		Out->SetStringField(TEXT("node"), Node->NodeGuid.ToString(EGuidFormats::Digits));
		Out->SetStringField(TEXT("variable"), VarName.ToString());
		Out->SetStringField(TEXT("ownerBefore"), OwnerBefore ? OwnerBefore->GetName() : TEXT("<self>"));
		Out->SetStringField(TEXT("ownerAfter"), bSelfAfter ? TEXT("<self>") : (OwnerAfter ? OwnerAfter->GetName() : TEXT("<none>")));
		Out->SetBoolField(TEXT("changed"), true);
	}

	// --- set_variable_default ---------------------------------------------------
	//   in:  { blueprintId|path, name, value (aliases: default, defaultValue) }
	//   out: { name, valueBefore, valueAfter, changed, typeValidated }
	//
	// This endpoint destroyed the value it was meant to set. `JStr(In, "value")` returns "" both for a
	// MISSING key and for any JSON value that is not a string (FJsonValue::TryGetString is false for
	// array/object/bool/number — JsonValue.h:69), and the result was assigned to Var.DefaultValue
	// unconditionally and then echoed back as `default`. So:
	//   {name:"Health"}                        -> Health's default WIPED,       ok:true, default:""
	//   {name:"Health", defaultValue:"100"}    -> wiped (add_variable spells the key `default`,
	//                                             this endpoint spelled it `value`, neither guarded)
	//   {name:"Items",  value:["a","b"]}       -> wiped, ok:true
	//   {name:"Health", value:"banana"} on int -> stored verbatim, ok:true
	// That is PM-003's class (a call that failed to specify destroyed what it was meant to set) plus
	// the exact JSON-array bug set_property was already hardened against (MifBridgeNodes5.cpp:8-18).
	//
	// Now: the key must be PRESENT (all three spellings accepted), the value is routed through the
	// SAME JsonToPropertyText converter set_property uses — against the variable's real FProperty, so
	// an int gets int rules and an array gets array rules — and the response is a read-back of
	// Var.DefaultValue before and after, never an echo of the request.
	void H_set_variable_default(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("value"), TEXT("default"), TEXT("defaultValue") },
			TEXT("blueprintId (alias: path), name, value (aliases: default, defaultValue)")))
		{
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
			Fail(Out, TEXT("name is required"));
			return;
		}

		// PRESENCE, not emptiness. An omitted value is a caller mistake, never an instruction to blank
		// the default — that read is what wiped live defaults and reported success.
		static const TCHAR* const ValueKeys[] = { TEXT("value"), TEXT("default"), TEXT("defaultValue") };
		const TCHAR* PresentKey = nullptr;
		TSharedPtr<FJsonValue> ValueJson;
		for (const TCHAR* Key : ValueKeys)
		{
			if (const TSharedPtr<FJsonValue> Found = In->TryGetField(Key))
			{
				if (PresentKey)
				{
					Fail(Out, FString::Printf(
						TEXT("pass the new default ONCE: both '%s' and '%s' were supplied and they are aliases of the ")
						TEXT("same parameter."), PresentKey, Key));
					return;
				}
				PresentKey = Key;
				ValueJson = Found;
			}
		}
		if (!PresentKey)
		{
			Fail(Out, FString::Printf(
				TEXT("value is required (aliases: default, defaultValue). Omitting it used to WIPE the default of '%s' and ")
				TEXT("report ok:true; it is now refused. To clear a default deliberately, pass value:null."), *Name));
			return;
		}

		const int32 VarIndex = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, FName(*Name));
		if (VarIndex == INDEX_NONE)
		{
			const FString Inherited = DescribeInheritedVariable(Blueprint, Name);
			if (!Inherited.IsEmpty())
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is INHERITED from %s, not declared on '%s'. A member-variable default cannot be set ")
					TEXT("here — use set_property against the blueprint's CDO (objectPath: '%s') instead."),
					*Name, *Inherited, *Blueprint->GetName(),
					Blueprint->GeneratedClass ? *Blueprint->GeneratedClass->GetPathName() : TEXT("<compile first>")));
				return;
			}
			Fail(Out, FString::Printf(TEXT("no member variable '%s' on '%s'%s — list_variables shows what exists"),
				*Name, *Blueprint->GetName(), *NearMissSuggestion(MemberVariableNames(Blueprint), Name)));
			return;
		}

		FBPVariableDescription& Var = Blueprint->NewVariables[VarIndex];
		const FString ValueBefore = Var.DefaultValue;

		// The variable's real reflection property carries the type rules. The skeleton class is
		// regenerated on every structural change, so it has the variable even before a full compile;
		// GeneratedClass is the fallback for a blueprint whose skeleton has not been rebuilt yet.
		const FProperty* VarProp = nullptr;
		if (Blueprint->SkeletonGeneratedClass) { VarProp = Blueprint->SkeletonGeneratedClass->FindPropertyByName(FName(*Name)); }
		if (!VarProp && Blueprint->GeneratedClass) { VarProp = Blueprint->GeneratedClass->FindPropertyByName(FName(*Name)); }

		FString NewText;
		bool bTypeValidated = false;
		const EJson ValueType = ValueJson.IsValid() ? ValueJson->Type : EJson::None;

		if (ValueType == EJson::Null)
		{
			// The one deliberate way to blank a default. Explicit, so it is not the accident above.
			NewText.Reset();
			bTypeValidated = VarProp != nullptr;
		}
		else if (VarProp)
		{
			// SAME converter as set_property (MifBridgeNodes5.cpp, declared in MifBridgeHandlers.h):
			// JSON arrays/objects/numbers/bools become the property's own export text, and anything
			// that cannot convert faithfully — "banana" for an int, a JSON object for a float — is
			// REFUSED naming the property and the form it wants, instead of being stored verbatim.
			FString ConvError;
			if (!JsonToPropertyText(ValueJson, VarProp, /*bDelimited*/ false, Blueprint->GeneratedClass
					? Blueprint->GeneratedClass->GetDefaultObject(/*bCreateIfNeeded*/ false) : nullptr,
					/*Depth*/ 0, Name, NewText, ConvError))
			{
				Fail(Out, FString::Printf(TEXT("%s (parameter '%s')"), *ConvError, PresentKey));
				return;
			}
			bTypeValidated = true;
		}
		else if (ValueType == EJson::String)
		{
			// No reflection property to validate against (a blueprint whose skeleton has not been
			// generated). A string is stored as-is — that is what this endpoint always did — but the
			// response says the type was NOT checked rather than implying it was.
			NewText = ValueJson->AsString();
			Out->SetStringField(TEXT("warning"),
				TEXT("the variable has no compiled reflection property yet, so the value was stored without type ")
				TEXT("validation — run compile and re-read with list_variables to confirm it is legal for this type"));
		}
		else
		{
			// A non-string JSON value with no property to convert against is exactly the input that
			// used to silently become "". Refuse it; do not guess an encoding.
			Fail(Out, FString::Printf(
				TEXT("'%s' is a JSON %s, and '%s' on '%s' has no compiled reflection property to convert it against ")
				TEXT("(the blueprint has never been compiled). Compile the blueprint first, or pass the value as a ")
				TEXT("string in UE export-text form."),
				PresentKey, JsonTypeName(ValueType), *Name, *Blueprint->GetName()));
			return;
		}

		Blueprint->Modify();
		Var.DefaultValue = NewText;
		FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);

		// READ BACK from the array, not from the local — the response must describe stored state.
		const FString ValueAfter = Blueprint->NewVariables[VarIndex].DefaultValue;
		if (ValueAfter != NewText)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' default did not take: wrote '%s', reads back '%s'. Nothing was changed."),
				*Name, *NewText, *ValueAfter));
			return;
		}

		Out->SetStringField(TEXT("name"), Name);
		Out->SetStringField(TEXT("valueBefore"), ValueBefore);
		Out->SetStringField(TEXT("valueAfter"), ValueAfter);
		// changed:false is not a failure here — unlike set_property's importer, a plain FString
		// assignment cannot half-succeed, so an unchanged value means the default was already that.
		Out->SetBoolField(TEXT("changed"), ValueAfter != ValueBefore);
		Out->SetBoolField(TEXT("typeValidated"), bTypeValidated);
		// Legacy field, now a READ-BACK rather than an echo of the request.
		Out->SetStringField(TEXT("default"), ValueAfter);
	}

	// --- Compile read-back --------------------------------------------------

	static FString SeverityStr(EMessageSeverity::Type Severity)
	{
		switch (Severity)
		{
		case EMessageSeverity::Error:
			return TEXT("error");
		case EMessageSeverity::PerformanceWarning:
		case EMessageSeverity::Warning:
			return TEXT("warning");
		default:
			return TEXT("info");
		}
	}

	void CompileBlueprintInto(UBlueprint* Blueprint, const TSharedRef<FJsonObject>& Out)
	{
		FCompilerResultsLog Results;
		Results.bAnnotateMentionedNodes = true;
		Results.SetSourcePath(Blueprint->GetPathName());

		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::None, &Results);

		Out->SetBoolField(TEXT("ok"), Results.NumErrors == 0);
		Out->SetNumberField(TEXT("numErrors"), Results.NumErrors);
		Out->SetNumberField(TEXT("numWarnings"), Results.NumWarnings);

		TArray<TSharedPtr<FJsonValue>> MessageArr;
		for (const TSharedRef<FTokenizedMessage>& Message : Results.Messages)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("severity"), SeverityStr(Message->GetSeverity()));
			Json->SetStringField(TEXT("text"), Message->ToText().ToString());

			// Map each message back to the offending node/pin so a fix can target it
			// exactly — this is the whole point of the bridge over a JPEG screenshot.
			for (const TSharedRef<IMessageToken>& Token : Message->GetMessageTokens())
			{
				if (Token->GetType() != EMessageToken::EdGraph)
				{
					continue;
				}
				const FEdGraphToken* GraphToken = static_cast<const FEdGraphToken*>(&Token.Get());
				const UEdGraphPin* Pin = GraphToken->GetPin();
				if (Pin)
				{
					Json->SetStringField(TEXT("pinName"), Pin->PinName.ToString());
				}
				if (const UObject* GraphObj = GraphToken->GetGraphObject())
				{
					if (const UEdGraphNode* Node = Cast<UEdGraphNode>(GraphObj))
					{
						Json->SetStringField(TEXT("nodeGuid"), Node->NodeGuid.ToString());
					}
				}
				else if (Pin && Pin->GetOwningNodeUnchecked())
				{
					Json->SetStringField(TEXT("nodeGuid"), Pin->GetOwningNodeUnchecked()->NodeGuid.ToString());
				}
			}
			MessageArr.Add(MakeShared<FJsonValueObject>(Json));
		}
		Out->SetArrayField(TEXT("messages"), MessageArr);
	}

	void H_compile(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// CompileBlueprintInto takes no params of its own - blueprintId/path is the whole surface.
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path) - compiles the blueprint and returns {ok, numErrors, numWarnings, messages[{severity,text,nodeGuid,pinName}]}"),
			{ { TEXT("save"),   TEXT("compile does not write to disk - call save_blueprint {blueprintId} afterwards to persist") },
			  { TEXT("dryRun"), TEXT("compile always commits the compiled class; validate {blueprintId} is the dry-run form and returns the same messages") } }))
		{
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}

		// PERSISTENCE / MUTATION CONTRACT, reported rather than inferred.
		// A caller was left guessing whether compile wrote to disk, dirtied the package, or
		// restructured the source graphs - and had to infer it from editor stars, file timestamps and
		// later disk hashes. Compiling MUTATES (it rebuilds the generated class and can reconstruct
		// nodes) and NEVER SAVES. Both halves are now stated on every response, and the node census is
		// taken either side so a GUID-sensitive caller knows when to refresh its snapshot.
		UPackage* Package = Blueprint->GetOutermost();
		const bool bDirtyBefore = Package && Package->IsDirty();

		TArray<UEdGraph*> GraphsBefore;
		GatherGraphs(Blueprint, GraphsBefore);
		int32 NodesBefore = 0;
		TSet<FGuid> GuidsBefore;
		for (UEdGraph* G : GraphsBefore)
		{
			if (!G) { continue; }
			NodesBefore += G->Nodes.Num();
			for (UEdGraphNode* N : G->Nodes) { if (N) { GuidsBefore.Add(N->NodeGuid); } }
		}

		CompileBlueprintInto(Blueprint, Out);

		TArray<UEdGraph*> GraphsAfter;
		GatherGraphs(Blueprint, GraphsAfter);
		int32 NodesAfter = 0, GuidsAdded = 0;
		for (UEdGraph* G : GraphsAfter)
		{
			if (!G) { continue; }
			NodesAfter += G->Nodes.Num();
			for (UEdGraphNode* N : G->Nodes)
			{
				if (N && !GuidsBefore.Contains(N->NodeGuid)) { ++GuidsAdded; }
			}
		}

		Out->SetBoolField(TEXT("compiled"), true);
		// Flat false, always. compile has never written to disk and must not start: a caller that
		// believes otherwise skips save_blueprint and loses the work on the next crash.
		Out->SetBoolField(TEXT("savedToDisk"), false);
		Out->SetStringField(TEXT("packagePath"), Package ? Package->GetName() : TEXT(""));
		Out->SetBoolField(TEXT("packageDirtyBefore"), bDirtyBefore);
		Out->SetBoolField(TEXT("packageDirtyAfter"), Package && Package->IsDirty());
		Out->SetNumberField(TEXT("graphNodesBefore"), NodesBefore);
		Out->SetNumberField(TEXT("graphNodesAfter"), NodesAfter);
		Out->SetNumberField(TEXT("newNodeGuids"), GuidsAdded);
		Out->SetBoolField(TEXT("graphStructureChanged"), NodesBefore != NodesAfter || GuidsAdded > 0);
		if (NodesBefore != NodesAfter || GuidsAdded > 0)
		{
			Out->SetStringField(TEXT("structureNote"), FString::Printf(
				TEXT("the SOURCE graphs changed across this compile (%d -> %d nodes, %d new NodeGuid(s)). ")
				TEXT("Any node snapshot taken before this call is stale - re-read with list_nodes."),
				NodesBefore, NodesAfter, GuidsAdded));
		}
		Out->SetStringField(TEXT("persistenceNote"),
			TEXT("compile mutates the in-memory blueprint and its generated class; it does NOT write to disk. "
				 "Call save_blueprint {blueprintId} to persist. A clean compile is not durability."));
	}

	// Execute an editor console command (e.g. "mif.kr.VerifyFidelity BP_Foo"). We are already on the game thread
	// (RunEndpoint dispatched us there). This is what makes the reconstruct/verify loop drivable
	// programmatically — without it, mif.kr.* commands could only be typed into the editor console by hand.
	//
	// BATCH O — WHY THERE IS NO SEPARATE `run_editor_exec`.
	// The UI-automation spec (docs/audit/work/R2_UI_AUTOMATION.md §5.1) ranked a `run_editor_exec`
	// endpoint third, over GEditor->Exec with a captured FStringOutputDevice and an editor-world
	// target. That endpoint would have been a THIRD copy of "call UEngine::Exec and describe the
	// result" — this one and run_console_captured are the first two — and a third copy of a shared
	// behaviour is precisely the bug class PM-005 exists for. Everything it was supposed to ADD is
	// therefore folded in HERE, additively:
	//   * structured result — `execOutput` / `execOutputLines`: what the command wrote to its OWN
	//     FOutputDevice, which is a different thing from the log lines run_console_captured brackets,
	//     and the field means exactly that on both endpoints because both go through
	//     MifBridge::RunEngineExec.
	//   * editor-target routing — `world`: editor (default, unchanged) | pie | active.
	//   * strict params — RejectUnknownParams, which this endpoint never had, so `run_console
	//     {command:"x", target:"editor"}` used to answer ok:true having silently ignored `target`.
	// Nothing was renamed: `command` and `executed` mean what they always meant, and captureOutput:false
	// reproduces the old call byte for byte (Ar = *GLog).
	//
	// THE OUTPUT DEVICE TEES. run_console's documented workflow is "run it, then tail the log", so a
	// capture that REPLACED *GLog would delete from the log exactly the output the caller was told to
	// go and read. RunEngineExec forwards every Serialize to GLog and keeps a copy.
	//
	// MODAL DISPOSITION: an exec command is arbitrary registered code and CAN open a dialog (or block
	// for minutes). This runs inline on the game thread, so a modal stops the ticker and this call
	// never returns — docs/02_GOTCHAS.md §8, same as every other invoking endpoint. There is no
	// deny-list here: the console surface is open-ended and a name-based list would be theatre. Use
	// list_editor_commands {includeConsole:true} to see what a prefix actually offers before running it.
	void H_run_console(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("command"), TEXT("cmd"), TEXT("world"), TEXT("captureOutput") },
			TEXT("command (alias: cmd), world (editor|pie|active; default editor), captureOutput (default true)"),
			{ { TEXT("filter"), TEXT("log-line filtering belongs to run_console_captured, which brackets GLog; this endpoint returns the command's own output device text") } }))
		{
			return;
		}

		const FString Cmd = JStrAny(In, { TEXT("command"), TEXT("cmd") });
		if (Cmd.IsEmpty())
		{
			Fail(Out, TEXT("command is required — the console command text, e.g. \"mif.kr.Reconstruct BP_Foo\" or \"stat unit\". list_editor_commands {includeConsole:true, consolePrefix:\"mif.\"} enumerates what is registered."));
			return;
		}

		// Editor-target routing. Default is "editor", which is exactly what this endpoint always did.
		const FString WorldWant = JStr(In, TEXT("world"), TEXT("editor")).ToLower();
		UWorld* World = nullptr;
		if (WorldWant == TEXT("editor"))
		{
			World = EditorWorld();
		}
		else if (WorldWant == TEXT("active"))
		{
			World = ActiveWorld();
		}
		else if (WorldWant == TEXT("pie"))
		{
			TArray<UWorld*> PIEWorlds;
			CollectPIEWorlds(PIEWorlds);
			if (PIEWorlds.Num() == 0)
			{
				Fail(Out, TEXT("world:\"pie\" was requested but no PIE world exists — nothing was executed. start_pie, then poll pie_status until state==\"running\", or use world:\"active\" to mean \"PIE if playing, else the editor world\"."));
				return;
			}
			World = PIEWorlds[0];
		}
		else
		{
			Fail(Out, FString::Printf(
				TEXT("world '%s' is not recognised — accepted values are editor (default; the editor world), pie (a running PIE world, refused when none exists) and active (PIE when playing, otherwise the editor world). An unrecognised value is an error, never a silent fall back to the default."),
				*JStr(In, TEXT("world"))));
			return;
		}

		const bool bCapture = JBool(In, TEXT("captureOutput"), true);
		FString ExecText;
		const bool bExecuted = RunEngineExec(World, Cmd, bCapture ? &ExecText : nullptr);

		Out->SetStringField(TEXT("command"), Cmd);
		Out->SetBoolField(TEXT("executed"), bExecuted);   // false = no handler claimed it (not necessarily an error)
		Out->SetStringField(TEXT("worldTarget"), WorldWant);
		Out->SetStringField(TEXT("world"), World ? World->GetName() : TEXT("<none>"));
		Out->SetBoolField(TEXT("outputCaptured"), bCapture);
		if (bCapture)
		{
			Out->SetStringField(TEXT("execOutput"), ExecText);
			TArray<FString> Lines;
			ExecText.ParseIntoArrayLines(Lines, /*bCullEmpty*/ false);
			TArray<TSharedPtr<FJsonValue>> Arr;
			for (const FString& Line : Lines) { Arr.Add(MakeShared<FJsonValueString>(Line)); }
			Out->SetArrayField(TEXT("execOutputLines"), Arr);
			Out->SetStringField(TEXT("outputNote"),
				TEXT("execOutput is what the command wrote to its OWN FOutputDevice, and it was ALSO forwarded to the editor log (the device tees). A command that reports via UE_LOG instead — most mif.kr.* commands do — writes nothing here: use run_console_captured, which brackets GLog, or tail <Saved>/Logs/."));
		}
		UE_LOG(LogMifBridge, Log, TEXT("run_console: %s -> %s"), *Cmd, bExecuted ? TEXT("handled") : TEXT("unhandled"));
	}

	void H_validate(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// validate == compile without saving. Neither compile nor validate writes the
		// asset to disk; use save_blueprint to persist once the compile is clean.
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path) - compiles WITHOUT saving and returns the same {ok, numErrors, numWarnings, messages[]} as compile, plus dryRun:true"),
			{ { TEXT("dryRun"), TEXT("validate is ALWAYS a dry run and reports dryRun:true in the response; it is not an input") },
			  { TEXT("save"),   TEXT("validate never writes to disk - run save_blueprint {blueprintId} once the compile is clean") } }))
		{
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		CompileBlueprintInto(Blueprint, Out);
		Out->SetBoolField(TEXT("dryRun"), true);
	}
}
