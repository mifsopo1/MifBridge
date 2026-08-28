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
#include "Blueprint/BlueprintSupport.h"   // FBlueprintTags - 5.3 :36-40, 5.7 :30-34

namespace MifBridge
{
	namespace
	{
		/** "/Game/AI/BP_Guard.BP_Guard_C" -> "/Game/AI/BP_Guard.BP_Guard".
		 *
		 *  The parent tag holds a GENERATED CLASS path and the asset it names holds an ASSET path, and
		 *  they differ by the _C suffix. Joining children to parents without this produces a tree in
		 *  which every node is a root, because no child's parent ever matches an asset - a wrong answer
		 *  that looks like a plausible one. */
		FString ClassPathToAssetPath(const FString& In)
		{
			// EXPORT TEXT FIRST. The tag does not hold a bare path - it holds UE export-text form,
			// which is  BlueprintGeneratedClass'/Game/AI/BP_Guard.BP_Guard_C'  including the class
			// prefix and the surrounding single quotes.
			//
			// The first version of this called RemoveFromEnd("_C") on that string. The trailing quote
			// meant it never matched, so no child's parent ever equalled an asset path, and the tree
			// came back as 2855 roots with zero children - precisely the failure the comment above it
			// warned about, in the same commit that warned about it. Predicting a bug is not the same
			// as avoiding it; only running it told me.
			FString S = FPackageName::ExportTextPathToObjectPath(In);
			S.RemoveFromEnd(TEXT("_C"));
			return S;
		}

		/** "/Script/Engine.Actor" -> "Actor". The name a caller recognises, not the mangled path. */
		FString ClassPathToShortName(const FString& In)
		{
			// Same export-text unwrap - without it every native root came back as "Actor'" with a
			// trailing quote, which is the kind of detail that survives all the way into a UI.
			FString S = FPackageName::ExportTextPathToObjectPath(In);
			S.RemoveFromEnd(TEXT("_C"));
			int32 Dot = INDEX_NONE;
			if (S.FindLastChar(TEXT('.'), Dot)) { S = S.Mid(Dot + 1); }
			return S;
		}

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

	// --- blueprint_inheritance_tree -------------------------------------------------------------
	//   in:  { pathPrefix? = "/Game/", root? (a class path or name to subtree from), maxDepth? = 0 }
	//   out: { roots[ { class, blueprint, children[...] } ], count, nativeRoots[], orphans[] }
	// Bucket: READ. Asset Registry only - LOADS NOTHING.
	//
	// THE WHOLE POINT IS THAT IT LOADS NOTHING. A blueprint's parent is published as an ASSET REGISTRY
	// TAG, so the entire inheritance graph of a project can be built from metadata the registry
	// already holds. Loading every Blueprint to ask GeneratedClass->GetSuperClass() would be correct,
	// far slower, and on a COOKED project actively dangerous - docs/02 section 6c records what loading
	// cooked Blueprints costs, and issue 16 is an editor that died doing it.
	//
	// Verified in BOTH trees before use:
	//   FBlueprintTags::ParentClassPath        5.3 BlueprintSupport.h:38   5.7 :32
	//   FBlueprintTags::NativeParentClassPath  5.3 :40                     5.7 :34
	//   FBlueprintTags::GeneratedClassPath     5.3 :36                     5.7 :30
	// Same names, same COREUOBJECT_API export, same meaning. No guard needed.
	//
	// THE TAG IS A PATH, NOT A NAME, and the two spellings do not match each other. The tag holds
	// something like "/Game/AI/BP_Guard.BP_Guard_C" - the GENERATED CLASS path, with the _C suffix -
	// while the asset it refers to is "/Game/AI/BP_Guard.BP_Guard". Joining children to parents means
	// normalising one to the other, and getting that wrong produces a tree where every node is a root
	// because nothing ever matches. Normalised here, once, in ClassPathToAssetPath.
	void H_blueprint_inheritance_tree(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("pathPrefix"), TEXT("prefix"), TEXT("root"), TEXT("maxDepth") },
			TEXT("pathPrefix (alias: prefix, default /Game/); root (a class or blueprint name to "
				 "subtree from); maxDepth (0 = unlimited)"),
			{ { TEXT("blueprintId"), TEXT("this reads the WHOLE project's tree from the asset registry; pass root to narrow it") },
			  { TEXT("class"), TEXT("spell it root - and it accepts a native class name like Actor as well as a blueprint") } }))
		{
			return;
		}

		const FString Prefix = JStrAny(In, { TEXT("pathPrefix"), TEXT("prefix") }, TEXT("/Game/"));
		const FString RootWanted = JStr(In, TEXT("root"));
		const int32 MaxDepth = (int32)JNum(In, TEXT("maxDepth"), 0.0);

		TArray<FAssetData> Assets;
		ProjRegistry().GetAssetsByPath(FName(*Prefix), Assets, /*bRecursive*/ true);

		// assetPath -> parent assetPath (or a NATIVE class name when the parent is not a blueprint)
		TMap<FString, FString> ParentOf;
		TMap<FString, FString> NativeParentOf;
		TSet<FString> Blueprints;

		for (const FAssetData& A : Assets)
		{
			// Blueprint-ness by TAG, not by class name: a Blueprint subclass such as
			// WidgetBlueprint or AnimBlueprint is still a blueprint and still has these tags, and
			// matching on ClassName would silently drop every one of them.
			FString ParentPath;
			if (!A.GetTagValue(FBlueprintTags::ParentClassPath, ParentPath) || ParentPath.IsEmpty())
			{
				continue;
			}
			const FString Self = A.GetObjectPathString();
			Blueprints.Add(Self);
			ParentOf.Add(Self, ClassPathToAssetPath(ParentPath));

			FString NativeParent;
			if (A.GetTagValue(FBlueprintTags::NativeParentClassPath, NativeParent))
			{
				NativeParentOf.Add(Self, ClassPathToShortName(NativeParent));
			}
		}

		// children keyed by parent. A parent that is not itself a blueprint in this scan is a NATIVE
		// root - the tree stops there, which is correct: C++ classes are not assets and have no
		// registry entry to walk.
		TMap<FString, TArray<FString>> ChildrenOf;
		TSet<FString> NativeRoots;
		for (const TPair<FString, FString>& P : ParentOf)
		{
			ChildrenOf.FindOrAdd(P.Value).Add(P.Key);
			if (!Blueprints.Contains(P.Value))
			{
				// FOUND LIVE: reporting NativeParentOf's value here unconditionally was wrong for a
				// SECOND reason, distinct from the ChildrenOf key-shape bug fixed above. "Not in
				// Blueprints" has two different causes this treated as one: the parent genuinely IS a
				// native (non-blueprint) class, OR the parent IS a blueprint but lives in a PLUGIN
				// content root (e.g. /Oceanology_Plugin/...) outside pathPrefix, so this /Game/-only
				// scan never saw it. NativeParentOf's value (from the NativeParentClassPath tag) walks
				// PAST every blueprint layer to the deepest native ancestor regardless of which case
				// this is - for the second case that name has no relationship to P.Value at all, so no
				// string transform of P.Value can ever match it, and root would refuse every value
				// this endpoint itself advertised. VERIFIED against real DDS2 content:
				// BP_OceanologyInfiniteOcean_ChildBTR's direct parent is
				// /Oceanology_Plugin/.../BP_OceanologyInfiniteOcean - a real blueprint, not a native
				// class - while NativeParentOf reported the unrelated "OceanologyInfiniteOcean" (its
				// native-most ancestor, several hops further up).
				//
				// A genuinely NATIVE class path always starts "/Script/" (export-text form
				// Class'/Script/Module.Name') - no blueprint asset, in-prefix or out, is ever shaped
				// that way. So only trust NativeParentOf when P.Value itself is a native reference;
				// otherwise this IS the case being described, and the honest, WALKABLE name is
				// P.Value's own short name - the same value ChildrenOf is actually keyed by.
				if (P.Value.StartsWith(TEXT("/Script/")))
				{
					// Report the NATIVE class name rather than the mangled path - "Actor", not
					// "/Script/Engine.Actor" - because that is the name a caller recognises.
					const FString* Native = NativeParentOf.Find(P.Key);
					NativeRoots.Add(Native ? *Native : ClassPathToShortName(P.Value));
				}
				else
				{
					NativeRoots.Add(ClassPathToShortName(P.Value));
				}
			}
		}
		for (TPair<FString, TArray<FString>>& P : ChildrenOf)
		{
			P.Value.Sort();   // stable output; a tree that reorders between calls is unreadable
		}

		// Recursive build with a VISITED set. A blueprint hierarchy cannot legally contain a cycle -
		// the editor refuses to create one - but this reads registry METADATA, which can be stale or
		// hand-edited, and a cycle here would hang the bridge on the game thread rather than returning
		// a bad answer. Cheap insurance against the failure mode that costs most.
		TSet<FString> Visited;
		TFunction<TSharedPtr<FJsonValue>(const FString&, int32)> Build =
			[&](const FString& Node, int32 Depth) -> TSharedPtr<FJsonValue>
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("blueprint"), Node);
			J->SetStringField(TEXT("name"), FPackageName::ObjectPathToObjectName(Node));
			if (const FString* Native = NativeParentOf.Find(Node))
			{
				J->SetStringField(TEXT("nativeParent"), *Native);
			}
			if (Visited.Contains(Node))
			{
				J->SetBoolField(TEXT("cycle"), true);
				J->SetStringField(TEXT("note"),
					TEXT("already seen on this branch - the registry metadata describes a cycle, which "
						 "a real blueprint hierarchy cannot contain. Not descended into."));
				return MakeShared<FJsonValueObject>(J);
			}
			Visited.Add(Node);

			const TArray<FString>* Kids = ChildrenOf.Find(Node);
			int32 Descendants = 0;
			if (Kids && (MaxDepth <= 0 || Depth < MaxDepth))
			{
				TArray<TSharedPtr<FJsonValue>> ChildJson;
				for (const FString& K : *Kids)
				{
					ChildJson.Add(Build(K, Depth + 1));
					++Descendants;
				}
				J->SetArrayField(TEXT("children"), ChildJson);
			}
			else if (Kids && Kids->Num() > 0)
			{
				// TRUNCATED, and it says so. A depth limit that silently drops children reports a leaf
				// that is not one.
				J->SetNumberField(TEXT("childrenNotShown"), Kids->Num());
				J->SetStringField(TEXT("note"), TEXT("maxDepth reached - children exist and were not expanded."));
			}
			J->SetNumberField(TEXT("directChildren"), Kids ? Kids->Num() : 0);
			Visited.Remove(Node);
			return MakeShared<FJsonValueObject>(J);
		};

		TArray<TSharedPtr<FJsonValue>> Roots;
		if (!RootWanted.IsEmpty())
		{
			// Subtree mode: match a blueprint by full path, by asset name, or by its _C class name.
			const FString WantedAsset = ClassPathToAssetPath(RootWanted);
			const FString WantedName = FPackageName::ObjectPathToObjectName(WantedAsset);
			bool bFound = false;
			for (const FString& BP : Blueprints)
			{
				if (BP == WantedAsset || FPackageName::ObjectPathToObjectName(BP) == WantedName)
				{
					Roots.Add(Build(BP, 0));
					bFound = true;
				}
			}
			if (!bFound)
			{
				// A NATIVE root is a legitimate thing to ask for - "show me everything deriving from
				// Actor" - and it is not a blueprint, so the loop above will never find it.
				//
				// FOUND LIVE, NOT ASSUMED: a direct ChildrenOf.Find(RootWanted) here always failed for
				// the exact values this endpoint itself advertises. ChildrenOf is keyed by
				// ClassPathToAssetPath's output for a native parent - the FULL path,
				// "/Script/Engine.Actor" - while nativeRoots (below) reports the SHORT name via
				// ClassPathToShortName, "Actor", because that is "the name a caller recognises". A
				// caller who did exactly what the error message tells them to - call with no root, read
				// nativeRoots, pass one back in - got refused every time. Fixed by matching on the short
				// name too, the same normalisation nativeRoots already applies.
				const TArray<FString>* Kids = ChildrenOf.Find(RootWanted);
				if (!Kids)
				{
					for (TPair<FString, TArray<FString>>& P : ChildrenOf)
					{
						if (ClassPathToShortName(P.Key).Equals(RootWanted, ESearchCase::IgnoreCase))
						{
							Kids = &P.Value;
							break;
						}
					}
				}
				if (!Kids)
				{
					Fail(Out, FString::Printf(
						TEXT("no blueprint or native root called '%s' under %s. nativeRoots in a call "
							 "with no root parameter lists the native classes this project actually "
							 "derives from."), *RootWanted, *Prefix));
					return;
				}
				for (const FString& K : *Kids) { Roots.Add(Build(K, 0)); }
			}
		}
		else
		{
			// Whole project: a root is a blueprint whose parent is not itself a blueprint here.
			TArray<FString> Sorted;
			for (const FString& BP : Blueprints)
			{
				const FString* P = ParentOf.Find(BP);
				if (!P || !Blueprints.Contains(*P)) { Sorted.Add(BP); }
			}
			Sorted.Sort();
			for (const FString& BP : Sorted) { Roots.Add(Build(BP, 0)); }
		}

		TArray<TSharedPtr<FJsonValue>> NativeJson;
		TArray<FString> NativeSorted = NativeRoots.Array();
		NativeSorted.Sort();
		for (const FString& N : NativeSorted) { NativeJson.Add(MakeShared<FJsonValueString>(N)); }

		Out->SetArrayField(TEXT("roots"), Roots);
		Out->SetNumberField(TEXT("blueprintCount"), Blueprints.Num());
		Out->SetArrayField(TEXT("nativeRoots"), NativeJson);
		Out->SetNumberField(TEXT("assetsScanned"), Assets.Num());
		Out->SetStringField(TEXT("source"),
			TEXT("asset registry tags only - NOTHING was loaded. Parent is FBlueprintTags::"
				 "ParentClassPath, published per asset, so this is safe on a cooked project where "
				 "loading Blueprints is not."));

		// The registry can still be scanning at editor startup, and a partial tree looks exactly like
		// a small project. Say which one it is rather than letting the count be misread.
		if (ProjRegistry().IsLoadingAssets())
		{
			Out->SetBoolField(TEXT("registryStillScanning"), true);
			Out->SetStringField(TEXT("scanNote"),
				TEXT("the asset registry is STILL SCANNING, so this tree is incomplete. Call again "
					 "once it settles - a partial tree is indistinguishable from a small project."));
		}
	}

}
