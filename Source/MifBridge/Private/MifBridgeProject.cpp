// Project-wide structure — the data behind the "brainmap".
//
// Andre sent screenshots of the competitor's Project Dashboard (Dependency Graph, Complexity Heatmap,
// Asset Distribution, Inheritance Tree, Performance) and asked for the equivalent. The data is the part
// that matters and the part MifBridge is good at: an interactive force-directed graph widget is a large
// Slate build, but a graph is worthless without something to draw, and these answers are independently
// useful over MCP with no widget at all.
//
// ============================================================================================
// EVERY ENDPOINT HERE IS BOUNDED, AND SAYS SO WHEN IT BOUNDED SOMETHING.
// ============================================================================================
//
// This is not caution for its own sake. GetReferencers runs PER ASSET, and DDS2 has thousands of them
// across 588 discovered plugins — an unbounded graph is not slow, it is a stopped game thread, and a
// handler that blocks the game thread takes the whole bridge offline for its duration. A caller can
// retry an error; it cannot cancel a stall.
//
// The guard shape is copied deliberately from H_audit_unused (MifBridgeAssetOps.cpp:774), which learned
// all three of these the hard way:
//   * refuse while the registry is still scanning, rather than calling WaitForCompletion();
//   * refuse a pathPrefix of fewer than two segments, because a mount root is minutes of work;
//   * cap the node count, and REPORT the cap — silent truncation reads as "I covered everything",
//     which is the single most repeated defect in this project's history.
//
// One deprecation trap, the same one docs/02_GOTCHAS.md section 14 is about: IAssetRegistry's
// GetAssetsByClass(FName) overload is UE_DEPRECATED(5.1) in 5.3 and DELETED in 5.7. Everything here
// passes GetClassPathName().

#include "MifBridgeHandlers.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Misc/PackageName.h"

namespace MifBridge
{
	namespace
	{
		IAssetRegistry& ProjRegistry()
		{
			return FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
		}

		// Shared entry guard. Returns false and fills Out when the request must not proceed.
		bool ProjectScanGuard(const FString& Prefix, const TSharedRef<FJsonObject>& Out,
							  bool bRequireDeepPrefix)
		{
			if (ProjRegistry().IsLoadingAssets())
			{
				// A REFUSAL, not a wait. WaitForCompletion here would stall the game thread for as long
				// as the scan takes, and the bridge answers nothing while it does.
				Fail(Out, TEXT("the asset registry is still scanning, so any answer would be a partial "
							   "one presented as complete. Ask again once it settles - list_blueprints "
							   "or find_assets will report registryStillScanning:false when it has."));
				return false;
			}
			if (bRequireDeepPrefix)
			{
				// "/Game" is one segment and means the whole project. Two segments ("/Game/Blueprints")
				// is the shallowest thing that is a real answer rather than a whole-project traversal.
				FString Trimmed = Prefix;
				Trimmed.RemoveFromEnd(TEXT("/"));
				int32 Segments = 0;
				for (int32 i = 0; i < Trimmed.Len(); ++i)
				{
					if (Trimmed[i] == TEXT('/')) { ++Segments; }
				}
				if (Segments < 2)
				{
					Fail(Out, FString::Printf(
						TEXT("pathPrefix '%s' is too broad - it needs at least two segments, e.g. "
							 "/Game/Blueprints. A mount root walks every asset in the project and "
							 "GetReferencers runs per asset, which stops the game thread for minutes. "
							 "Narrow it, or use project_asset_distribution, which is cheap because it "
							 "never touches referencers."), *Prefix));
					return false;
				}
			}
			return true;
		}
	}

	// --- project_dependency_graph ---------------------------------------------
	//   in:  { pathPrefix, maxNodes?, includeExternal? }
	//   out: { nodes:[{package,name,class,dependsOn,referencedBy}], edges:[{from,to}],
	//          nodeCount, edgeCount, truncated, matched }
	//
	// The brainmap's data. Nodes are packages under pathPrefix; an edge from A to B means A depends on
	// B. Edges to packages OUTSIDE the prefix are dropped by default, because a graph whose every node
	// trails off into /Engine is unreadable - includeExternal:true keeps them.
	void H_project_dependency_graph(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("pathPrefix"), TEXT("path"), TEXT("maxNodes"), TEXT("includeExternal") },
			TEXT("pathPrefix (alias: path) - at least two segments, e.g. /Game/Blueprints; "
				 "maxNodes (default 300); includeExternal (default false - keep edges that leave the "
				 "prefix)"),
			{ { TEXT("depth"), TEXT("this returns the whole dependency set under the prefix in one pass; there is no recursion depth to set") },
			  { TEXT("limit"), TEXT("maxNodes is the cap here - and it is reported as `truncated` rather than applied silently") } }))
		{
			return;
		}

		FString Prefix = JStrAny(In, { TEXT("pathPrefix"), TEXT("path") });
		if (Prefix.IsEmpty())
		{
			Fail(Out, TEXT("pathPrefix is required, e.g. /Game/Blueprints"));
			return;
		}
		if (!ProjectScanGuard(Prefix, Out, /*bRequireDeepPrefix*/ true)) { return; }

		const int32 MaxNodes = FMath::Clamp(JInt(In, TEXT("maxNodes"), 300), 1, 5000);
		const bool bIncludeExternal = JBool(In, TEXT("includeExternal"), false);

		TArray<FAssetData> Assets;
		ProjRegistry().GetAssetsByPath(FName(*Prefix), Assets, /*bRecursive*/ true);

		// The set of packages IN the prefix, so an edge can be classified as internal or leaving.
		TSet<FName> InPrefix;
		for (const FAssetData& A : Assets) { InPrefix.Add(A.PackageName); }

		TArray<TSharedPtr<FJsonValue>> Nodes, Edges;
		int32 EdgeCount = 0;
		bool bTruncated = false;

		for (const FAssetData& A : Assets)
		{
			if (Nodes.Num() >= MaxNodes)
			{
				bTruncated = true;
				break;
			}

			TArray<FName> Deps, Refs;
			ProjRegistry().GetDependencies(A.PackageName, Deps);
			ProjRegistry().GetReferencers(A.PackageName, Refs);

			int32 KeptDeps = 0;
			for (const FName& D : Deps)
			{
				const bool bInternal = InPrefix.Contains(D);
				if (!bInternal && !bIncludeExternal) { continue; }
				++KeptDeps;
				++EdgeCount;
				TSharedRef<FJsonObject> E = MakeShared<FJsonObject>();
				E->SetStringField(TEXT("from"), A.PackageName.ToString());
				E->SetStringField(TEXT("to"), D.ToString());
				E->SetBoolField(TEXT("external"), !bInternal);
				Edges.Add(MakeShared<FJsonValueObject>(E));
			}

			TSharedRef<FJsonObject> N = MakeShared<FJsonObject>();
			N->SetStringField(TEXT("package"), A.PackageName.ToString());
			N->SetStringField(TEXT("name"), A.AssetName.ToString());
			N->SetStringField(TEXT("class"), A.AssetClassPath.GetAssetName().ToString());
			// BOTH directions, because they answer different questions: dependsOn is "what does this
			// need", referencedBy is "what breaks if I delete it". A heatmap wants the second.
			N->SetNumberField(TEXT("dependsOn"), KeptDeps);
			N->SetNumberField(TEXT("dependsOnTotal"), Deps.Num());
			N->SetNumberField(TEXT("referencedBy"), Refs.Num());
			Nodes.Add(MakeShared<FJsonValueObject>(N));
		}

		Out->SetStringField(TEXT("pathPrefix"), Prefix);
		Out->SetNumberField(TEXT("nodeCount"), Nodes.Num());
		Out->SetNumberField(TEXT("edgeCount"), EdgeCount);
		// matched is the unfiltered truth. A capped list must never be able to read as completeness.
		Out->SetNumberField(TEXT("matched"), Assets.Num());
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("nodes"), Nodes);
		Out->SetArrayField(TEXT("edges"), Edges);
		if (bTruncated)
		{
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("stopped at maxNodes=%d of %d matching assets. The graph below is a PREFIX of the "
					 "real one, not a sample of it - narrow pathPrefix for a complete picture of a "
					 "smaller area rather than raising the cap."), MaxNodes, Assets.Num()));
		}
	}

	// --- project_asset_distribution -------------------------------------------
	//   in:  { pathPrefix?, topFolders? }
	//   out: { totalAssets, byClass:[{class,count}], byFolder:[{folder,count}],
	//          registryStillScanning }
	//
	// Cheap by construction: pure Asset Registry, LOADS NOTHING, and never touches referencers - which
	// is why it is the one endpoint here that accepts a bare /Game prefix.
	void H_project_asset_distribution(const TSharedRef<FJsonObject>& In,
									  const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("pathPrefix"), TEXT("path"), TEXT("topFolders"), TEXT("topClasses") },
			TEXT("pathPrefix (alias: path, default /Game); topFolders (default 25); topClasses "
				 "(default 25)"),
			{ { TEXT("class"), TEXT("this reports the distribution ACROSS classes - find_assets is the one that filters to a class") } }))
		{
			return;
		}

		FString Prefix = JStrAny(In, { TEXT("pathPrefix"), TEXT("path") });
		if (Prefix.IsEmpty()) { Prefix = TEXT("/Game"); }
		const int32 TopFolders = FMath::Clamp(JInt(In, TEXT("topFolders"), 25), 1, 500);
		const int32 TopClasses = FMath::Clamp(JInt(In, TEXT("topClasses"), 25), 1, 500);

		// No deep-prefix requirement: this walks asset DATA, not referencers, so a whole-project sweep
		// is a registry iteration rather than thousands of graph queries.
		if (!ProjectScanGuard(Prefix, Out, /*bRequireDeepPrefix*/ false)) { return; }

		TArray<FAssetData> Assets;
		ProjRegistry().GetAssetsByPath(FName(*Prefix), Assets, /*bRecursive*/ true);

		TMap<FString, int32> ByClass, ByFolder;
		for (const FAssetData& A : Assets)
		{
			ByClass.FindOrAdd(A.AssetClassPath.GetAssetName().ToString())++;
			// Group by the folder ONE level below the prefix. Grouping by full path would produce one
			// bucket per asset and tell you nothing; this is the level a human thinks in.
			FString Rel = A.PackagePath.ToString();
			Rel.RemoveFromStart(Prefix);
			Rel.RemoveFromStart(TEXT("/"));
			int32 Slash = INDEX_NONE;
			const FString Bucket = Rel.FindChar(TEXT('/'), Slash) ? Rel.Left(Slash)
								 : (Rel.IsEmpty() ? TEXT("(root)") : Rel);
			ByFolder.FindOrAdd(Bucket)++;
		}

		auto Emit = [](TMap<FString, int32>& Map, int32 Top, const TCHAR* KeyName)
		{
			Map.ValueSort([](int32 A, int32 B) { return A > B; });
			TArray<TSharedPtr<FJsonValue>> Rows;
			for (const TPair<FString, int32>& P : Map)
			{
				if (Rows.Num() >= Top) { break; }
				TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
				R->SetStringField(KeyName, P.Key);
				R->SetNumberField(TEXT("count"), P.Value);
				Rows.Add(MakeShared<FJsonValueObject>(R));
			}
			return Rows;
		};

		Out->SetStringField(TEXT("pathPrefix"), Prefix);
		Out->SetNumberField(TEXT("totalAssets"), Assets.Num());
		Out->SetNumberField(TEXT("distinctClasses"), ByClass.Num());
		Out->SetNumberField(TEXT("distinctFolders"), ByFolder.Num());
		Out->SetArrayField(TEXT("byClass"), Emit(ByClass, TopClasses, TEXT("class")));
		Out->SetArrayField(TEXT("byFolder"), Emit(ByFolder, TopFolders, TEXT("folder")));
		// distinctClasses/distinctFolders are reported alongside the truncated lists so a caller can
		// see that a top-25 view is a top-25 view.
		Out->SetBoolField(TEXT("classesTruncated"), ByClass.Num() > TopClasses);
		Out->SetBoolField(TEXT("foldersTruncated"), ByFolder.Num() > TopFolders);
		Out->SetBoolField(TEXT("registryStillScanning"), ProjRegistry().IsLoadingAssets());
	}
}
