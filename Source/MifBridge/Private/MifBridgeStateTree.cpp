// MifBridge — STATE TREE: the modern UE5 answer to Behavior Trees, read as a real hierarchy.
//
// Reopened 2026-08-27. The decline was one line - "Class does not resolve; DDS2 does not use it" -
// which is a fact about one test project and, under the corrected measuring stick, not a reason.
// MifBridge already reads Behavior Trees; a project on StateTree instead would have found the AI half
// of this bridge simply blank.
//
// Verified in BOTH trees before writing:
//   UStateTree::GetStates()      inline in both, so it links despite 5.7 dropping the class-level
//                                STATETREEMODULE_API for the MinimalAPI style
//   UStateTree::GetSchema()      inline in both
//   FCompactStateTreeState       Name / Parent / ChildrenBegin / ChildrenEnd / Type / HasChildren()
//                                all present and identical in both
//
// ONE ADDITIVE DIFFERENCE, handled rather than ignored: EStateTreeStateType gained LinkedAsset in 5.7.
// 5.3 has State, Group, Linked, Subtree. A switch over it therefore needs a default arm that reports
// the raw value, or a tree authored in 5.7 and read on 5.3 reports an empty type - the same trap
// ECollisionTraceFlag set in get_collision, where a missing arm would have produced an empty string
// that reads as "no type" rather than "a type this build does not know".
//
// THE STATES ARE FLAT WITH INDEX RANGES, not pointers. Each state carries ChildrenBegin/ChildrenEnd
// into the same array, which is why this can rebuild the hierarchy without loading anything beyond
// the asset itself.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#if MIF_WITH_STATETREE
#include "StateTree.h"
#include "StateTreeTypes.h"
#include "StateTreeSchema.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_STATETREE
	namespace
	{
		void StateTreeUnavailable(const TSharedRef<FJsonObject>& Out, const TCHAR* What)
		{
			Fail(Out, FString::Printf(
				TEXT("%s is unavailable: this MifBridge was built against an engine with no StateTree "
					 "plugin. It ships with UE5 at Engine/Plugins/Runtime/StateTree. The endpoint "
					 "stays registered so this answer is possible at all."), What));
		}
	}
#else
	namespace
	{
		/** The type name. DEFAULT ARM IS LOAD-BEARING: 5.7 added LinkedAsset and 5.3 has no such
		 *  value, so a tree authored in 5.7 and read here must report something honest rather than an
		 *  empty string that reads as "no type". */
		FString StateTypeName(EStateTreeStateType T)
		{
			switch (T)
			{
			case EStateTreeStateType::State:   return TEXT("State");
			case EStateTreeStateType::Group:   return TEXT("Group");
			case EStateTreeStateType::Linked:  return TEXT("Linked");
			case EStateTreeStateType::Subtree: return TEXT("Subtree");
			default:
				return FString::Printf(
					TEXT("(unrecognised: %d - this build predates it; 5.7 added LinkedAsset)"),
					(int32)T);
			}
		}
	}
#endif

	// --- list_state_trees -----------------------------------------------------------------------
	//   in:  { pathPrefix? = "/Game/" }
	//   out: { stateTrees[ { path, name } ], count }
	// Bucket: READ. Asset Registry only - LOADS NOTHING.
	void H_list_state_trees(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("pathPrefix"), TEXT("prefix") },
			TEXT("pathPrefix (alias: prefix, default /Game/)"),
			{ { TEXT("tree"), TEXT("this LISTS them; describe_state_tree takes one") },
			  { TEXT("behaviorTree"), TEXT("different system - list via find_assets, and describe_behavior_tree reads one") } }))
		{
			return;
		}
#if !MIF_WITH_STATETREE
		StateTreeUnavailable(Out, TEXT("list_state_trees"));
#else
		const FString Prefix = JStrAny(In, { TEXT("pathPrefix"), TEXT("prefix") }, TEXT("/Game/"));
		IAssetRegistry& Registry =
			FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get();

		TArray<FAssetData> Found;
		Registry.GetAssetsByPath(FName(*Prefix), Found, /*bRecursive*/ true);

		TArray<TSharedPtr<FJsonValue>> Trees;
		for (const FAssetData& A : Found)
		{
			// By CLASS PATH. GetAssetsByClass(FName) is UE_DEPRECATED(5.1) and deleted in 5.7.
			if (A.AssetClassPath != UStateTree::StaticClass()->GetClassPathName()) { continue; }
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("path"), A.GetObjectPathString());
			J->SetStringField(TEXT("name"), A.AssetName.ToString());
			Trees.Add(MakeShared<FJsonValueObject>(J));
		}
		Out->SetArrayField(TEXT("stateTrees"), Trees);
		Out->SetNumberField(TEXT("count"), Trees.Num());
		if (Registry.IsLoadingAssets())
		{
			Out->SetBoolField(TEXT("registryStillScanning"), true);
			Out->SetStringField(TEXT("scanNote"),
				TEXT("the asset registry is STILL SCANNING - a low count may mean 'not finished "
					 "looking' rather than 'none exist'."));
		}
		Out->SetStringField(TEXT("source"), TEXT("asset registry only - nothing was loaded."));
#endif
	}

	// --- describe_state_tree --------------------------------------------------------------------
	//   in:  { path (aliases: assetPath, tree) }
	//   out: { states[ { index, name, type, parent, children[] } ], stateCount, schema, compiled }
	// Bucket: READ. Loads the asset.
	void H_describe_state_tree(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("tree") },
			TEXT("path (aliases: assetPath, tree) - a StateTree asset"),
			{}))
		{
			return;
		}
#if !MIF_WITH_STATETREE
		StateTreeUnavailable(Out, TEXT("describe_state_tree"));
#else
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("tree") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a StateTree asset. list_state_trees reports them."));
			return;
		}
		UStateTree* Tree = LoadObject<UStateTree>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Tree)
		{
			Fail(Out, FString::Printf(TEXT("no StateTree at '%s'."), *Path));
			return;
		}

		TConstArrayView<FCompactStateTreeState> States = Tree->GetStates();

		TArray<TSharedPtr<FJsonValue>> Json;
		for (int32 i = 0; i < States.Num(); ++i)
		{
			const FCompactStateTreeState& S = States[i];
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetNumberField(TEXT("index"), i);
			J->SetStringField(TEXT("name"), S.Name.ToString());
			J->SetStringField(TEXT("type"), StateTypeName(S.Type));

			// The parent handle is INVALID for a root state rather than absent, so it is reported as
			// -1 rather than omitted - a caller reconstructing the tree needs to tell "root" from
			// "field missing".
			J->SetNumberField(TEXT("parent"),
				S.Parent.IsValid() ? (int32)S.Parent.Index : -1);

			// Children are an index RANGE into this same flat array, not pointers. Expanded here so a
			// caller does not have to know the layout.
			TArray<TSharedPtr<FJsonValue>> Kids;
			for (int32 c = S.ChildrenBegin; c < S.ChildrenEnd && c < States.Num(); ++c)
			{
				Kids.Add(MakeShared<FJsonValueNumber>(c));
			}
			J->SetArrayField(TEXT("children"), Kids);
			J->SetBoolField(TEXT("hasChildren"), S.HasChildren());
			Json.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetStringField(TEXT("assetPath"), Tree->GetPathName());
		Out->SetArrayField(TEXT("states"), Json);
		Out->SetNumberField(TEXT("stateCount"), States.Num());

		// The SCHEMA decides what this tree can be run against - an actor, a component, a mass
		// entity - and a tree is useless attached to the wrong thing. Worth reporting alongside the
		// states rather than leaving a caller to open the asset.
		const UStateTreeSchema* Schema = Tree->GetSchema();
		Out->SetStringField(TEXT("schema"), Schema ? Schema->GetClass()->GetName() : FString());

		if (States.Num() == 0)
		{
			// A StateTree that has never compiled has NO states, whatever its editor graph shows. That
			// is the single most likely reason for an empty result and it is invisible otherwise.
			Out->SetStringField(TEXT("note"),
				TEXT("this StateTree has NO compiled states. GetStates() returns the COMPILED data, so "
					 "an asset whose graph was edited and never recompiled reads as empty here even "
					 "though the editor shows states. Open it and compile, then read again."));
		}
#endif
	}
}
