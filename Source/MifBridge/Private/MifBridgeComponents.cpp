// MifBridge — Phase 3 breadth: components via the SimpleConstructionScript (SCS) tree.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Components/ActorComponent.h"
#include "Components/SceneComponent.h"
#include "Engine/Blueprint.h"
#include "Engine/SCS_Node.h"
#include "Engine/SimpleConstructionScript.h"
#include "Engine/InheritableComponentHandler.h"   // UInheritableComponentHandler::GetAllTemplates
#include "Kismet2/BlueprintEditorUtils.h"

namespace MifBridge
{
	namespace
	{
		// ReadVec3 is GONE — MifBridge::ReadVectorField / ReadRotatorField / ReadScaleField now
		// (MifBridgeCommon.cpp). It read the array form through FJsonValue::AsNumber(), which returns
		// 0.0 for a string and cannot report that it did, so ["oops",1,2] silently became (0,1,2):
		// Batch L defect 1, in the array spelling. The shared readers also accept the {x,y,z} object
		// form, which this endpoint refused while every other transform endpoint required it.

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

		// STRICT — an empty componentClass used to resolve to the blueprint's own class.
		UClass* ComponentClass = ResolveClassStrictField(In, { TEXT("componentClass"), TEXT("class") }, Blueprint, Out);
		if (!ComponentClass)
		{
			return;
		}
		if (!ComponentClass->IsChildOf(UActorComponent::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not an ActorComponent class: '%s'"), *ComponentClass->GetName()));
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

		// ---- BATCH M: VALIDATE THE TRANSFORM BEFORE THE SCS NODE EXISTS -----------------
		// This block used to sit AFTER SCS->CreateNode + SCS->AddNode, and its comment claimed
		// "RunEndpoint cancels the transaction on ok:false, so the component added above is rolled
		// back". That was never true: UTransBuffer::Cancel discards the undo entry without calling
		// FTransaction::Apply (EditorTransaction.cpp:1387-1437), so `add_component` with
		// location:{x:"not-a-number"} answered ok:false AND left the component in the blueprint —
		// the exact defect proved live on override_inherited_component. Nothing here needs the node:
		// the "is it a scene component" question is a property of the CLASS, and the seed values are
		// the CLASS DEFAULT, which is precisely what SCS->CreateNode initialises the fresh template
		// from. See docs/01_POSTMORTEMS.md PM-007.
		const bool bWantsTransform = JHasAny(In, { TEXT("location"), TEXT("rotation"), TEXT("scale") });
		const bool bIsSceneClass   = ComponentClass->IsChildOf(USceneComponent::StaticClass());
		if (!bIsSceneClass && bWantsTransform)
		{
			// The whole transform block used to sit inside a Cast on the created template, so adding a
			// UAudioComponent-style NON-scene component with a transform returned ok:true having
			// ignored all three keys. set_component_transform Fails correctly in exactly this
			// situation, so the two endpoints disagreed about the same impossible request; same
			// message, same verdict — and now with nothing created either.
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a USceneComponent, so it has no transform — location/rotation/scale cannot be applied. ")
				TEXT("Remove them, or add a scene component. No component was added."),
				*ComponentClass->GetName()));
			return;
		}

		// Seeded from the CLASS DEFAULT so an omitted component keeps what the class default gave it,
		// then each SUPPLIED component must be a number.
		FVector  Loc(ForceInit);
		FRotator Rot(ForceInit);   // [pitch,yaw,roll]
		FVector  Scale(1.0);
		if (bIsSceneClass)
		{
			const USceneComponent* ClassDefault = ComponentClass->GetDefaultObject<USceneComponent>();
			Loc   = ClassDefault->GetRelativeLocation();
			Rot   = ClassDefault->GetRelativeRotation();
			Scale = ClassDefault->GetRelativeScale3D();
			FString ReadError;
			if (ReadVectorField(In, TEXT("location"), Loc, ReadError) == EJsonRead::Invalid
				|| ReadRotatorField(In, TEXT("rotation"), Rot, ReadError) == EJsonRead::Invalid
				|| ReadScaleField(In, TEXT("scale"), Scale, ReadError) == EJsonRead::Invalid)
			{
				Fail(Out, FString::Printf(
					TEXT("%s The component was NOT added: the transform is validated before the SCS node is created, so this blueprint is exactly as it was before the call."),
					*ReadError));
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
			if (In->HasField(TEXT("location"))) { SceneTemplate->SetRelativeLocation_Direct(Loc); }
			if (In->HasField(TEXT("rotation"))) { SceneTemplate->SetRelativeRotation_Direct(Rot); }
			if (In->HasField(TEXT("scale")))    { SceneTemplate->SetRelativeScale3D_Direct(Scale); }
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
	//   in:  { blueprintId (path), includeInherited? = true, includeNative? = true, limit? = 500 }
	//   out: { blueprint, parentClass, count, matched, truncated, components: [...],
	//          ownSCSCount, parentBlueprintSCSCount, nativeCount,
	//          inheritableComponentHandlerPath, existingOverrideCount }
	//
	// BATCH N - THE DISCOVERY HALF OF BATCH J.
	//
	// Batch J shipped the WRITE path for inherited components (get_/override_/revert_
	// inherited_component, MifBridgeInherited.cpp) and shipped NO way to find out what those
	// components are CALLED: get_inherited_component resolves ONE component BY NAME, and this
	// endpoint walked the child Blueprint's own SCS and nothing else. An agent editing a child
	// therefore saw a near-empty list and had no name to pass to the three endpoints the session had
	// just built. The feature was unusable for lack of a list.
	//
	// It now reports every component from all THREE origins, each row tagged with where it came from:
	//   ownSCS              this Blueprint's own SimpleConstructionScript
	//   parentBlueprintSCS  a parent BLUEPRINT's SCS, anywhere up the UBlueprintGeneratedClass chain
	//   native              a C++ component on the parent class chain, read off the CDO
	//
	// ADDITIVE, ON PURPOSE. Every field the old response carried (name, class, isRoot, templatePath,
	// parent, attachSocket, count, components) is emitted unchanged and with unchanged meaning, so
	// every existing caller keeps working; the new origins arrive as EXTRA rows and the new facts as
	// EXTRA fields.
	//
	// The new origins are ON BY DEFAULT. includeInherited / includeNative exist so a caller can ask
	// for exactly the old shape back (both false), not as an opt-in for the new one - discoverability
	// is the entire point of the change, and a default-off discovery feature is the same gap wearing
	// a parameter.
	//
	// templatePath means ONE thing on every row: "the objectPath to pass to set_property to change
	// this component's defaults FOR THIS BLUEPRINT".
	//   ownSCS              the SCS ComponentTemplate (<Class>:<Name>_GEN_VARIABLE)
	//   native              the CHILD CDO's own subobject - and the subobject name is NOT the
	//                       property name (Mesh -> CharacterMesh0, CharacterMovement -> CharMoveComp,
	//                       CapsuleComponent -> CollisionCylinder), which is why it is resolved from
	//                       the object rather than composed from the name. Same resolution
	//                       get_inherited_component uses; one implementation, in MifBridgeCommon.cpp.
	//   parentBlueprintSCS  the child's OVERRIDE template when one exists, and EMPTY when it does not
	//                       - because the only other template is the PARENT asset's, and writing
	//                       there would edit every other child too. parentTemplatePath carries it as
	//                       a read-only reference, and route says which endpoint mints the override.
	//
	// READ-ONLY, and it stays in IsReadOnlyEndpoint: EnumerateBlueprintComponents asks for the
	// InheritableComponentHandler with bCreateIfNecessary=FALSE, so listing a blueprint can never
	// mint an ICH on the asset.
	void H_list_components(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"),
			  TEXT("includeInherited"), TEXT("includeNative"), TEXT("limit") },
			TEXT("blueprintId (alias: path), includeInherited (default true), includeNative (default true), limit (default 500)"),
			{{ TEXT("component"),
			   TEXT("list_components enumerates ALL of them; get_inherited_component resolves ONE by name") }}))
		{
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}

		const bool  bIncludeInherited = JBool(In, TEXT("includeInherited"), true);
		const bool  bIncludeNative    = JBool(In, TEXT("includeNative"), true);
		const int32 Limit             = FMath::Clamp(JInt(In, TEXT("limit"), 500), 1, 5000);

		TArray<FComponentOriginRow> Rows;
		EnumerateBlueprintComponents(Blueprint, Rows, /*Cap*/ 0);

		TArray<TSharedPtr<FJsonValue>> Arr;
		int32 OwnCount = 0, ParentCount = 0, NativeCount = 0, Matched = 0;
		bool bTruncated = false;

		for (const FComponentOriginRow& Row : Rows)
		{
			const bool bIsOwn    = (Row.Origin == kComponentOriginOwnSCS);
			const bool bIsParent = (Row.Origin == kComponentOriginParentSCS);
			const bool bIsNative = (Row.Origin == kComponentOriginNative);
			if (bIsParent && !bIncludeInherited) { continue; }
			if (bIsNative && !bIncludeNative)    { continue; }

			++Matched;
			if (Arr.Num() >= Limit) { bTruncated = true; continue; }   // keep counting so the cap never reads as completeness
			if (bIsOwn)    { ++OwnCount; }
			if (bIsParent) { ++ParentCount; }
			if (bIsNative) { ++NativeCount; }

			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			// --- fields the pre-Batch-N response already carried, unchanged ---
			Json->SetStringField(TEXT("name"), Row.Name.ToString());
			if (Row.ComponentClass)
			{
				Json->SetStringField(TEXT("class"), Row.ComponentClass->GetName());
				Json->SetStringField(TEXT("classPath"), Row.ComponentClass->GetPathName());
			}
			Json->SetBoolField(TEXT("isRoot"), Row.bIsRoot);
			if (Row.AttachParentNode)
			{
				Json->SetStringField(TEXT("parent"), Row.AttachParentNode->GetVariableName().ToString());
			}
			if (Row.AttachSocket != NAME_None)
			{
				Json->SetStringField(TEXT("attachSocket"), Row.AttachSocket.ToString());
			}

			// --- Batch N: where it came from, and what to call next ---
			Json->SetStringField(TEXT("origin"), Row.Origin);
			Json->SetStringField(TEXT("owningClass"), GetPathNameSafe(Row.OwningClass));

			if (bIsOwn)
			{
				if (Row.Template) { Json->SetStringField(TEXT("templatePath"), Row.Template->GetPathName()); }
				Json->SetBoolField(TEXT("inherited"), false);
				Json->SetBoolField(TEXT("overrideExists"), false);
				Json->SetBoolField(TEXT("canOverride"), false);
				Json->SetStringField(TEXT("route"), TEXT("set_property"));
				Json->SetStringField(TEXT("endpoint"), TEXT("set_property"));
				Json->SetStringField(TEXT("hint"), Row.Template
					? FString::Printf(TEXT("set_property {objectPath:\"%s\", propertyPath:\"<Prop>\", value:\"<v>\"}"),
						*Row.Template->GetPathName())
					: TEXT("this SCS node has no ComponentTemplate; recompile the blueprint"));
			}
			else if (bIsParent)
			{
				Json->SetBoolField(TEXT("inherited"), true);
				Json->SetStringField(TEXT("parentTemplatePath"), GetPathNameSafe(Row.Template));
				Json->SetBoolField(TEXT("overrideExists"), Row.OverrideTemplate != nullptr);
				Json->SetBoolField(TEXT("canOverride"), Row.bCanOverride);
				Json->SetBoolField(TEXT("editableWhenInherited"), Row.bEditableWhenInherited);
				if (!Row.bCanOverride && !Row.CannotOverrideReason.IsEmpty())
				{
					Json->SetStringField(TEXT("canOverrideReason"), Row.CannotOverrideReason);
				}
				if (Row.OverrideTemplate)
				{
					Json->SetStringField(TEXT("templatePath"), Row.OverrideTemplate->GetPathName());
					Json->SetStringField(TEXT("overrideTemplatePath"), Row.OverrideTemplate->GetPathName());
					Json->SetStringField(TEXT("route"), TEXT("set_property"));
					Json->SetStringField(TEXT("endpoint"), TEXT("set_property"));
					Json->SetStringField(TEXT("hint"), FString::Printf(
						TEXT("this child already overrides '%s' - write to the override directly with ")
						TEXT("set_property {objectPath:\"%s\", ...}, or revert_inherited_component to drop it and fall back to '%s'."),
						*Row.Name.ToString(), *Row.OverrideTemplate->GetPathName(), *GetPathNameSafe(Row.Template)));
				}
				else
				{
					// Deliberately NO templatePath: the only template that exists is the PARENT
					// asset's, and set_property there would change every other child of that parent.
					Json->SetStringField(TEXT("route"), Row.bCanOverride ? TEXT("override_inherited_component") : TEXT("none"));
					Json->SetStringField(TEXT("endpoint"), Row.bCanOverride ? TEXT("override_inherited_component") : TEXT("none"));
					Json->SetStringField(TEXT("hint"), Row.bCanOverride
						? FString::Printf(
							TEXT("no override yet - override_inherited_component {blueprint:\"%s\", component:\"%s\", properties:{...}} mints one ")
							TEXT("and writes it in a single transacted call, and returns overrideTemplatePath for set_property afterwards. ")
							TEXT("templatePath is deliberately absent: until the override exists the only template is the PARENT's ('%s'), and ")
							TEXT("writing there would change every other child of that parent."),
							*Blueprint->GetPathName(), *Row.Name.ToString(), *GetPathNameSafe(Row.Template))
						: FString::Printf(TEXT("inherited but not overridable - %s"), *Row.CannotOverrideReason));
				}
			}
			else   // native
			{
				Json->SetBoolField(TEXT("inherited"), true);
				Json->SetBoolField(TEXT("overrideExists"), false);
				Json->SetBoolField(TEXT("canOverride"), false);
				Json->SetStringField(TEXT("canOverrideReason"), Row.CannotOverrideReason);
				if (Row.SubobjectName != NAME_None)
				{
					Json->SetStringField(TEXT("subobjectName"), Row.SubobjectName.ToString());
				}
				if (Row.Template)
				{
					Json->SetStringField(TEXT("templatePath"), Row.Template->GetPathName());
					Json->SetStringField(TEXT("nativeCdoPath"), Row.Template->GetPathName());
					Json->SetStringField(TEXT("creationMethod"), ComponentCreationMethodString(Row.Template));
					Json->SetStringField(TEXT("route"), TEXT("set_property"));
					Json->SetStringField(TEXT("endpoint"), TEXT("set_property"));
					Json->SetStringField(TEXT("hint"), FString::Printf(
						TEXT("native: the CHILD's own CDO subobject, edited directly - set_property {objectPath:\"%s\", propertyPath:\"<Prop>\", value:\"<v>\"}. ")
						TEXT("Note the path carries the SUBOBJECT name ('%s'), not the property name ('%s')."),
						*Row.Template->GetPathName(), *Row.SubobjectName.ToString(), *Row.Name.ToString()));
				}
			}
			Arr.Add(MakeShared<FJsonValueObject>(Json));
		}

		// Non-creating handler read, so a caller can see whether this asset carries any overrides at
		// all without the ICH being minted by the question.
		UInheritableComponentHandler* ICH = Blueprint->GetInheritableComponentHandler(/*bCreateIfNecessary*/ false);
		int32 ExistingOverrides = 0;
		if (ICH)
		{
			TArray<UActorComponent*> Templates;
			ICH->GetAllTemplates(Templates, /*bIncludeTransientTemplates*/ false);
			ExistingOverrides = Templates.Num();
		}

		Out->SetStringField(TEXT("blueprint"), Blueprint->GetPathName());
		Out->SetStringField(TEXT("parentClass"), GetPathNameSafe(Blueprint->ParentClass));
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetNumberField(TEXT("matched"), Matched);
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetNumberField(TEXT("ownSCSCount"), OwnCount);
		Out->SetNumberField(TEXT("parentBlueprintSCSCount"), ParentCount);
		Out->SetNumberField(TEXT("nativeCount"), NativeCount);
		Out->SetBoolField(TEXT("includeInherited"), bIncludeInherited);
		Out->SetBoolField(TEXT("includeNative"), bIncludeNative);
		Out->SetStringField(TEXT("inheritableComponentHandlerPath"), ICH ? ICH->GetPathName() : FString());
		Out->SetNumberField(TEXT("existingOverrideCount"), ExistingOverrides);
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

		// Seeded from the component's CURRENT relative transform, so an omitted key keeps its value;
		// a SUPPLIED one that is not a number is a hard error (Batch L defect 1). Both {x,y,z} and
		// [x,y,z] are accepted — this endpoint used to require the array form while every other
		// transform endpoint required the object form.
		FVector  Loc   = SceneTemplate->GetRelativeLocation();
		FRotator Rot   = SceneTemplate->GetRelativeRotation();   // [pitch,yaw,roll]
		FVector  Scale = SceneTemplate->GetRelativeScale3D();
		FString ReadError;
		const EJsonRead LocRead   = ReadVectorField(In, TEXT("location"), Loc, ReadError);
		if (LocRead == EJsonRead::Invalid) { Fail(Out, FString::Printf(TEXT("%s Nothing was changed."), *ReadError)); return; }
		const EJsonRead RotRead   = ReadRotatorField(In, TEXT("rotation"), Rot, ReadError);
		if (RotRead == EJsonRead::Invalid) { Fail(Out, FString::Printf(TEXT("%s Nothing was changed."), *ReadError)); return; }
		const EJsonRead ScaleRead = ReadScaleField(In, TEXT("scale"), Scale, ReadError);
		if (ScaleRead == EJsonRead::Invalid) { Fail(Out, FString::Printf(TEXT("%s Nothing was changed."), *ReadError)); return; }

		if (LocRead != EJsonRead::Read && RotRead != EJsonRead::Read && ScaleRead != EJsonRead::Read)
		{
			Fail(Out, TEXT("provide at least one of location/rotation/scale as {x,y,z} or [x,y,z]"));
			return;
		}
		if (LocRead   == EJsonRead::Read) { SceneTemplate->SetRelativeLocation_Direct(Loc); }
		if (RotRead   == EJsonRead::Read) { SceneTemplate->SetRelativeRotation_Direct(Rot); }
		if (ScaleRead == EJsonRead::Read) { SceneTemplate->SetRelativeScale3D_Direct(Scale); }
		FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
		Out->SetStringField(TEXT("component"), Name);
	}
}
