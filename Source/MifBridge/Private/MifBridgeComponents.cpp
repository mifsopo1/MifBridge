// MifBridge — Phase 3 breadth: components via the SimpleConstructionScript (SCS) tree.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Components/ActorComponent.h"
#include "Components/SceneComponent.h"
#include "Engine/Blueprint.h"
#include "Engine/SCS_Node.h"
#include "Engine/SimpleConstructionScript.h"
#include "Kismet2/BlueprintEditorUtils.h"

namespace MifBridge
{
	namespace
	{
		bool ReadVec3(const TSharedRef<FJsonObject>& In, const TCHAR* Field, FVector& Out)
		{
			const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
			if (!In->TryGetArrayField(Field, Arr) || Arr == nullptr || Arr->Num() < 3)
			{
				return false;
			}
			Out.X = (*Arr)[0]->AsNumber();
			Out.Y = (*Arr)[1]->AsNumber();
			Out.Z = (*Arr)[2]->AsNumber();
			return true;
		}

		USimpleConstructionScript* ResolveSCS(UBlueprint* Blueprint, const TSharedRef<FJsonObject>& Out)
		{
			USimpleConstructionScript* SCS = Blueprint->SimpleConstructionScript;
			if (!SCS)
			{
				Fail(Out, TEXT("blueprint has no SimpleConstructionScript (needs an Actor-derived parent)"));
			}
			return SCS;
		}
	}

	// --- add_component ------------------------------------------------------

	void H_add_component(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		USimpleConstructionScript* SCS = ResolveSCS(Blueprint, Out);
		if (!SCS)
		{
			return;
		}

		const FString ClassName = JStr(In, TEXT("componentClass"));
		UClass* ComponentClass = ResolveClass(ClassName, Blueprint);
		if (!ComponentClass || !ComponentClass->IsChildOf(UActorComponent::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not an ActorComponent class: '%s'"), *ClassName));
			return;
		}

		FString Name = JStr(In, TEXT("name"));
		Name.TrimStartAndEndInline();
		if (!Name.IsEmpty() && !IsValidIdentifier(Name))
		{
			Fail(Out, FString::Printf(TEXT("invalid component name '%s'"), *Name));
			return;
		}
		const FName VarName = Name.IsEmpty() ? NAME_None : FName(*Name);

		// Resolve the parent BEFORE creating the node so a bad parentName fails cleanly
		// instead of silently attaching the new component as a root.
		const FString ParentName = JStr(In, TEXT("parentName"));
		USCS_Node* Parent = nullptr;
		if (!ParentName.IsEmpty())
		{
			Parent = SCS->FindSCSNode(FName(*ParentName));
			if (!Parent)
			{
				Fail(Out, FString::Printf(TEXT("parent component '%s' not found"), *ParentName));
				return;
			}
		}

		Blueprint->Modify();
		SCS->Modify();

		USCS_Node* Node = SCS->CreateNode(ComponentClass, VarName);
		if (!Node)
		{
			Fail(Out, TEXT("SCS CreateNode failed"));
			return;
		}

		if (Parent)
		{
			Parent->AddChildNode(Node);
		}
		else
		{
			SCS->AddNode(Node);
		}

		// Relative transform on the template (scene components only). Use *_Direct on a
		// non-registered template to avoid move side-effects.
		if (USceneComponent* SceneTemplate = Cast<USceneComponent>(Node->ComponentTemplate))
		{
			FVector Vec;
			if (ReadVec3(In, TEXT("location"), Vec))
			{
				SceneTemplate->SetRelativeLocation_Direct(Vec);
			}
			if (ReadVec3(In, TEXT("rotation"), Vec))
			{
				SceneTemplate->SetRelativeRotation_Direct(FRotator(Vec.X, Vec.Y, Vec.Z)); // [pitch,yaw,roll]
			}
			if (ReadVec3(In, TEXT("scale"), Vec))
			{
				SceneTemplate->SetRelativeScale3D_Direct(Vec);
			}
		}

		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);

		Out->SetStringField(TEXT("component"), Node->GetVariableName().ToString());
		Out->SetStringField(TEXT("class"), ComponentClass->GetName());
		if (Parent)
		{
			Out->SetStringField(TEXT("parent"), ParentName);
		}
	}

	// --- list_components ----------------------------------------------------

	void H_list_components(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		USimpleConstructionScript* SCS = Blueprint->SimpleConstructionScript;
		TArray<TSharedPtr<FJsonValue>> Arr;
		if (SCS)
		{
			const TArray<USCS_Node*>& Roots = SCS->GetRootNodes();
			for (USCS_Node* Node : SCS->GetAllNodes())
			{
				if (!Node)
				{
					continue;
				}
				TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
				Json->SetStringField(TEXT("name"), Node->GetVariableName().ToString());
				if (Node->ComponentClass)
				{
					Json->SetStringField(TEXT("class"), Node->ComponentClass->GetName());
				}
				Json->SetBoolField(TEXT("isRoot"), Roots.Contains(Node));
				Arr.Add(MakeShared<FJsonValueObject>(Json));
			}
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("components"), Arr);
	}

	// --- remove_component ---------------------------------------------------

	void H_remove_component(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_component requires confirm=true"));
			return;
		}
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		USimpleConstructionScript* SCS = ResolveSCS(Blueprint, Out);
		if (!SCS)
		{
			return;
		}
		const FString Name = JStr(In, TEXT("name"));
		USCS_Node* Node = SCS->FindSCSNode(FName(*Name));
		if (!Node)
		{
			Fail(Out, FString::Printf(TEXT("component '%s' not found"), *Name));
			return;
		}

		Blueprint->Modify();
		SCS->Modify();
		SCS->RemoveNodeAndPromoteChildren(Node);
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		Out->SetStringField(TEXT("removed"), Name);
	}

	// --- set_component_transform --------------------------------------------

	void H_set_component_transform(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}
		USimpleConstructionScript* SCS = ResolveSCS(Blueprint, Out);
		if (!SCS)
		{
			return;
		}
		const FString Name = JStr(In, TEXT("name"));
		USCS_Node* Node = SCS->FindSCSNode(FName(*Name));
		if (!Node)
		{
			Fail(Out, FString::Printf(TEXT("component '%s' not found"), *Name));
			return;
		}
		USceneComponent* SceneTemplate = Cast<USceneComponent>(Node->ComponentTemplate);
		if (!SceneTemplate)
		{
			Fail(Out, FString::Printf(TEXT("component '%s' is not a scene component (no transform)"), *Name));
			return;
		}

		Blueprint->Modify();
		Node->Modify();
		SceneTemplate->Modify();

		FVector Vec;
		bool bAny = false;
		if (ReadVec3(In, TEXT("location"), Vec)) { SceneTemplate->SetRelativeLocation_Direct(Vec); bAny = true; }
		if (ReadVec3(In, TEXT("rotation"), Vec)) { SceneTemplate->SetRelativeRotation_Direct(FRotator(Vec.X, Vec.Y, Vec.Z)); bAny = true; }
		if (ReadVec3(In, TEXT("scale"), Vec)) { SceneTemplate->SetRelativeScale3D_Direct(Vec); bAny = true; }

		if (!bAny)
		{
			Fail(Out, TEXT("provide at least one of location/rotation/scale as [x,y,z]"));
			return;
		}
		FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
		Out->SetStringField(TEXT("component"), Name);
	}
}
