// MifBridge — session/assets, introspection, variables, and compile read-back endpoints.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "EdGraphToken.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "K2Node.h"
#include "K2Node_CallFunction.h"
#include "K2Node_Knot.h"
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
		const FString Filter = JStr(In, TEXT("filter"));

		FAssetRegistryModule& Module = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
		IAssetRegistry& Registry = Module.Get();

		TArray<FAssetData> Assets;
		Registry.GetAssetsByClass(UBlueprint::StaticClass()->GetClassPathName(), Assets, /*bSearchSubClasses*/ true);

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (const FAssetData& Asset : Assets)
		{
			const FString ObjectPath = Asset.GetObjectPathString();
			if (!Filter.IsEmpty() && !ObjectPath.Contains(Filter))
			{
				continue;
			}
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("blueprintId"), ObjectPath);
			Json->SetStringField(TEXT("name"), Asset.AssetName.ToString());
			Json->SetStringField(TEXT("package"), Asset.PackageName.ToString());
			Arr.Add(MakeShared<FJsonValueObject>(Json));
			if (Arr.Num() >= 5000)
			{
				break; // safety cap
			}
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("blueprints"), Arr);
	}

	void H_save_blueprint(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
		if (bSaved) Out->SetStringField(TEXT("savedTo"), FileName);
		else Fail(Out, FString::Printf(TEXT("save failed for %s"), *Package->GetName()));
	}

	void H_backup_blueprint(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
			Arr.Add(MakeShared<FJsonValueObject>(SerializeNode(Node, /*bIncludePins*/ true)));
		}
		Out->SetStringField(TEXT("graphId"), GraphIdOf(Blueprint, Graph));
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("nodes"), Arr);
	}

	void H_get_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
		if (!Node)
		{
			return;
		}
		Out->SetObjectField(TEXT("node"), SerializeNode(Node, /*bIncludePins*/ true));
	}

	void H_list_variables(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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

	void H_add_variable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// This guard is here because its ABSENCE cost a real user a working design. Wanting an object
		// variable typed to a specific class, they tried `class`, `className`, `parentClass`,
		// `objectClass` and `subType` alongside type:"object" — five spellings, all accepted, all
		// silently dropped, every call reporting ok:true and producing a plain UObject that would not
		// connect to a SceneComponent pin. They concluded the bridge could not type object variables
		// and redesigned around it. It can: the class goes INSIDE the type string.
		// The KeyNotes below turn that dead end into one round-trip.
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"),
			  TEXT("name"), TEXT("type"), TEXT("container"), TEXT("valueType"),
			  TEXT("scope"), TEXT("function"), TEXT("default") },
			TEXT("blueprintId (alias: path), name, type, container?, valueType?, scope? (member|local), ")
			TEXT("function? (required when scope=local), default?"),
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
		const FString Default = JStr(In, TEXT("default"));

		Blueprint->Modify();

		bool bAdded = false;
		if (Scope.Equals(TEXT("local"), ESearchCase::IgnoreCase))
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
			TEXT("deprecated"), TEXT("category"), TEXT("tooltip")
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
		Out->SetStringField(TEXT("scope"), Scope);
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
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		CompileBlueprintInto(Blueprint, Out);
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
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		CompileBlueprintInto(Blueprint, Out);
		Out->SetBoolField(TEXT("dryRun"), true);
	}
}
