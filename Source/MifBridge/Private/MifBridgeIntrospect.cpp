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
#include "Engine/Engine.h"   // GEngine->Exec (run_console)
#include "Editor.h"          // GEditor editor world

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
		const FString FileName = FPackageName::LongPackageNameToFilename(Package->GetName(), FPackageName::GetAssetPackageExtension());

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
		const FString FileName = FPackageName::LongPackageNameToFilename(Package->GetName(), FPackageName::GetAssetPackageExtension());
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
		const FString FileName = FPackageName::LongPackageNameToFilename(Package->GetName(), FPackageName::GetAssetPackageExtension());
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
		if (!MakePinType(JStr(In, TEXT("type")), JStr(In, TEXT("container")), PinType, TypeError))
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
