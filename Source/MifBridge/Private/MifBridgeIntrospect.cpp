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
#include "Engine/Engine.h"   // GEngine->Exec (run_console)
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

		UPackage* Package = Blueprint->GetOutermost();
		// A World must be written as .umap, NOT .uasset. GetAssetPackageExtension() is unconditional,
		// so saving a map used to drop an M_Foo.uasset beside the real M_Foo.umap — and the resolver
		// searches .uasset FIRST, so the stray file then silently shadowed the actual level on every
		// later load. ContainsMap() is the same test the engine's own save path uses.
		const FString FileName = FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			Package->ContainsMap() ? FPackageName::GetMapPackageExtension() : FPackageName::GetAssetPackageExtension());
		if (!FPaths::FileExists(FileName))
		{
			Fail(Out, FString::Printf(TEXT("asset not saved to disk yet, nothing to back up: %s"), *FileName));
			return;
		}

		const FString BackupName = FileName + TEXT(".bak");
		if (IFileManager::Get().Copy(*BackupName, *FileName, /*bReplace*/ true, /*bEvenIfReadOnly*/ true) == COPY_OK)
		{
			Out->SetStringField(TEXT("backup"), BackupName);
		}
		else
		{
			Fail(Out, FString::Printf(TEXT("failed to write backup: %s"), *BackupName));
		}
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
		const FString Name = JStr(In, TEXT("class"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("class is required"));
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
				OutError = FString::Printf(TEXT("unknown replicationCondition '%s' (expected an ELifetimeCondition, e.g. COND_None, COND_OwnerOnly, COND_SkipOwner, COND_InitialOnly)"), *CondStr);
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

	void H_rename_variable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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

		// An event dispatcher is a PC_MCDelegate member variable PLUS a signature graph. Renaming
		// only the variable — which is all RenameMemberVariable does — leaves the graph behind under
		// the old name, and the next skeleton regen breaks the dispatcher. Refuse and redirect.
		for (const FBPVariableDescription& Var : Blueprint->NewVariables)
		{
			if (Var.VarName.ToString() == OldName && Var.VarType.PinCategory == UEdGraphSchema_K2::PC_MCDelegate)
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is the backing delegate of an event dispatcher, not a plain variable. ")
					TEXT("Renaming it here would orphan the signature graph and break the dispatcher on the next compile — ")
					TEXT("use rename_event_dispatcher, which renames both halves."), *OldName));
				return;
			}
		}

		Blueprint->Modify();
		FBlueprintEditorUtils::RenameMemberVariable(Blueprint, FName(*OldName), FName(*NewName));
		Out->SetStringField(TEXT("name"), NewName);
	}

	void H_remove_variable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
		Blueprint->Modify();
		FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, FName(*Name));
		Out->SetStringField(TEXT("removed"), Name);
	}

	void H_set_variable_default(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		const FString Name = JStr(In, TEXT("name"));
		const FString Value = JStr(In, TEXT("value"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required"));
			return;
		}

		Blueprint->Modify();
		bool bFound = false;
		for (FBPVariableDescription& Var : Blueprint->NewVariables)
		{
			if (Var.VarName.ToString() == Name)
			{
				Var.DefaultValue = Value;
				bFound = true;
				break;
			}
		}
		if (!bFound)
		{
			Fail(Out, FString::Printf(TEXT("variable '%s' not found"), *Name));
			return;
		}
		FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
		Out->SetStringField(TEXT("name"), Name);
		Out->SetStringField(TEXT("default"), Value);
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
	// (RunEndpoint dispatched us there). The command's output goes to the editor log; the caller tails
	// <Saved>/Logs/DrugDealerSimulator2.log to read it. This is what makes the reconstruct/verify loop drivable
	// programmatically — without it, mif.kr.* commands could only be typed into the editor console by hand.
	void H_run_console(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString Cmd = JStr(In, TEXT("command"));
		if (Cmd.IsEmpty()) { Fail(Out, TEXT("command is required")); return; }
		UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
		const bool bExecuted = GEngine ? GEngine->Exec(World, *Cmd) : false;
		Out->SetStringField(TEXT("command"), Cmd);
		Out->SetBoolField(TEXT("executed"), bExecuted);   // false = no handler claimed it (not necessarily an error)
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
