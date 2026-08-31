// MifBridge — Phase 3 breadth: components via the SimpleConstructionScript (SCS) tree.
#include "MifBridgeHandlers.h"
#include "Kismet2/ComponentEditorUtils.h"   // CanDeleteComponent / IsComponentNameAvailable
#include "Subsystems/EditorActorSubsystem.h"
#include "Components/SceneComponent.h"
#include "GameFramework/Actor.h"
#include "ScopedTransaction.h"
#include "MifBridgeLog.h"

#include "Components/ActorComponent.h"
#include "Components/SceneComponent.h"
#include "GameFramework/Actor.h"                  // AActor::GetRootComponent, for the native pass
#include "UObject/Class.h"                        // TFieldIterator, UClass::GetDefaultObject
#include "UObject/UnrealType.h"                   // FObjectPropertyBase
#include "Engine/Blueprint.h"
#include "Engine/SCS_Node.h"
#include "Engine/SimpleConstructionScript.h"
#include "Engine/InheritableComponentHandler.h"   // UInheritableComponentHandler::GetAllTemplates
#include "Engine/BlueprintGeneratedClass.h"       // cooked targets: the SCS survives cooking, the UBlueprint does not
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/PackageName.h"                     // GetShortName, for the "<Package>.<Short>_C" class probe
#include "UObject/ObjectRedirector.h"             // a moved cooked asset resolves through a redirector
#include "UObject/UObjectGlobals.h"               // StaticLoadObject, LOAD_NoWarn/LOAD_Quiet

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

		// --- COOKED-ONLY TARGETS -------------------------------------------------------------
		//
		// A cooked package ships the UBlueprintGeneratedClass and NOT the UBlueprint, so
		// ResolveBlueprint (MifBridgeCommon.cpp:2751) casts to null and every blueprint verb refuses
		// the asset. For the WRITE verbs that is correct. For list_components it is not: the SCS
		// SURVIVES COOKING, so the names are right there and the endpoint was refusing to read data it
		// already had - which left get_inherited_component with no names to be called with, and left
		// "mint a whole new asset" as the only offered answer to a read-only question.
		//
		// UBlueprintGeneratedClass::SimpleConstructionScript / InheritableComponentHandler are plain
		// UPROPERTY()s with no WITH_EDITORONLY_DATA guard (BlueprintGeneratedClass.h:682-689), as are
		// USimpleConstructionScript::RootNodes/AllNodes and USCS_Node::ComponentClass /
		// ComponentTemplate / AttachToName / VariableGuid / InternalVariableName. Walking the
		// GetSuperClass() chain reading SimpleConstructionScript is verbatim what the engine itself
		// does in cooked builds - UBlueprintGeneratedClass::GetDefaultObjectPreloadDependencies
		// (BlueprintGeneratedClass.cpp:1811-1831), which is not editor-gated.
		//
		// This is NOT a second enumerator competing with EnumerateBlueprintComponents: it produces the
		// same FComponentOriginRow vocabulary, in the same precedence order (own SCS, then each
		// ancestor's SCS, then CDO natives), and the editable path still goes through the shared one
		// untouched. It exists here only because the shared one is typed UBlueprint* and this file may
		// not change MifBridgeCommon.cpp. EVICTION TRIGGER, not an open-ended note: the next change to
		// MifBridgeCommon.cpp that touches EnumerateBlueprintComponents must fold this in as the
		// class-based core and reduce this to a call.

		// The generated class behind a path, for the case where no UBlueprint exists. Same two
		// spellings DescribeMissingBlueprint (MifBridgeCommon.cpp:2803-2816) probes, because they must
		// agree about what "there is a class here" means - it is the message a caller gets when this
		// returns null.
		UBlueprintGeneratedClass* MifComponentsLoadGeneratedClass(const FString& InPath)
		{
			FString P = InPath;
			P.TrimStartAndEndInline();
			if (P.IsEmpty()) { return nullptr; }

			FString PackageName = P;
			{
				FString Left, Right;
				if (P.Split(TEXT("."), &Left, &Right)) { PackageName = Left; }
			}
			const FString ShortName = FPackageName::GetShortName(PackageName);

			for (const FString& Candidate : { PackageName + TEXT(".") + ShortName + TEXT("_C"), P })
			{
				UObject* Found = StaticLoadObject(UObject::StaticClass(), nullptr, *Candidate, nullptr, LOAD_NoWarn | LOAD_Quiet);
				if (UObjectRedirector* Redirector = Cast<UObjectRedirector>(Found))
				{
					Found = Redirector->DestinationObject;
				}
				if (UBlueprintGeneratedClass* BPGC = Cast<UBlueprintGeneratedClass>(Found))
				{
					return BPGC;
				}
			}
			return nullptr;
		}

		// Every component reachable from a COOKED generated class. bOutCdoMissing reports that the
		// native pass was skipped rather than empty - GetDefaultObject(bCreateIfNeeded=FALSE) keeps
		// list_components in IsReadOnlyEndpoint (MifBridgeCommon.cpp:386) by never constructing a CDO
		// that did not already exist, so "no natives" and "could not look" are different answers and
		// are reported as different answers.
		void MifComponentsEnumerateGeneratedClassComponents(UBlueprintGeneratedClass* TargetClass,
			TArray<FComponentOriginRow>& OutRows, int32 Cap, bool& bOutCdoMissing)
		{
			OutRows.Reset();
			bOutCdoMissing = false;
			if (!TargetClass) { return; }

			TSet<FName> Seen;
			auto HasRoom = [&OutRows, Cap]() { return Cap <= 0 || OutRows.Num() < Cap; };
			const FString TargetPath = TargetClass->GetPathName();

			// WHY the rows below cannot be written, stated from the DISCRIMINATOR rather than asserted.
			// ClassGeneratedBy is the editor back-pointer to the generating asset and is null exactly when
			// the package is cooked - the same test MifBridgeCommon.cpp:2928 already makes. H_list_components
			// retargets to that asset when it is a UBlueprint, so reaching this function with a NON-null
			// ClassGeneratedBy means "a generator exists but is not an editable UBlueprint": still read-only,
			// for a different reason, and calling it COOKED would be a fabrication.
			UObject* const GeneratedBy = TargetClass->ClassGeneratedBy;
			const bool bGenuinelyCooked = (GeneratedBy == nullptr);
			const FString TargetNoun = bGenuinelyCooked
				? FString::Printf(TEXT("the COOKED class '%s'"), *TargetPath)
				: FString::Printf(TEXT("the class '%s'"), *TargetPath);
			const FString NoEditableClause = bGenuinelyCooked
				? TEXT("there is no editable UBlueprint behind it (its ClassGeneratedBy is null, which is what a cooked package looks like in the editor)")
				: FString::Printf(TEXT("its generating asset '%s' is not a UBlueprint"), *GeneratedBy->GetPathName());

			// 1 + 2. The class's OWN SCS, then every ancestor BLUEPRINT class's SCS. One loop, because
			//        on this path level 0 is a class exactly like every other level - there is no
			//        UBlueprint whose authoring SCS could differ from the compiled one.
			for (UBlueprintGeneratedClass* BPGC = TargetClass;
				 BPGC != nullptr;
				 BPGC = Cast<UBlueprintGeneratedClass>(BPGC->GetSuperClass()))
			{
				USimpleConstructionScript* SCS = BPGC->SimpleConstructionScript;
				if (!SCS) { continue; }
				const bool bOwnLevel = (BPGC == TargetClass);
				const TArray<USCS_Node*>& Roots = SCS->GetRootNodes();
				for (USCS_Node* Node : SCS->GetAllNodes())
				{
					if (!Node || !HasRoom()) { continue; }
					const FName N = Node->GetVariableName();
					if (N == NAME_None || Seen.Contains(N)) { continue; }
					Seen.Add(N);

					FComponentOriginRow Row;
					Row.Name             = N;
					Row.Origin           = bOwnLevel ? kComponentOriginOwnSCS : kComponentOriginParentSCS;
					Row.ComponentClass   = Node->ComponentClass;
					Row.OwningClass      = BPGC;
					Row.Node             = Node;
					Row.AttachParentNode = bOwnLevel ? SCS->FindParentNode(Node) : nullptr;
					// The EFFECTIVE template as this class sees it: GetActualComponentTemplate walks
					// TargetClass's InheritableComponentHandler chain and falls back to the node's own
					// ComponentTemplate (SCS_Node.cpp:29-54). A cooked child that overrides an ancestor's
					// component would otherwise be reported with the ancestor's archetype.
					Row.Template         = Node->GetActualComponentTemplate(TargetClass);
					if (!Row.Template) { Row.Template = Node->ComponentTemplate; }
					Row.bIsRoot          = Roots.Contains(Node);
					Row.AttachSocket     = Node->AttachToName;
					if (Row.Template) { Row.bEditableWhenInherited = Row.Template->IsEditableWhenInherited(); }

					// canOverride means one thing everywhere: "override_inherited_component will accept
					// this". It cannot: that verb needs an editable UBlueprint to hang the ICH record on,
					// and this target has none. So it is false on EVERY row here, with the reason saying
					// which of the two situations it is rather than leaving the caller to guess.
					Row.bCanOverride = false;
					Row.CannotOverrideReason = bOwnLevel
						? FString::Printf(
							TEXT("declared in %s itself, and %s, so neither its template nor an override can be written from here."),
							*TargetNoun, *NoEditableClause)
						: FString::Printf(
							TEXT("inherited by %s from '%s'. Overriding needs an editable UBlueprint to hold the ")
							TEXT("InheritableComponentHandler record, and %s."),
							*TargetNoun, *BPGC->GetPathName(), *NoEditableClause);
					OutRows.Add(Row);
				}
			}

			// 3. NATIVE components on the CDO, under the PROPERTY name a caller would type, with the
			//    real subobject name carried separately (Mesh -> CharacterMesh0). Same shape as step 3
			//    of EnumerateBlueprintComponents (MifBridgeCommon.cpp:2187-2232) so the two paths report
			//    natives identically.
			UObject* CDO = TargetClass->GetDefaultObject(/*bCreateIfNeeded*/ false);
			if (!CDO)
			{
				bOutCdoMissing = true;
				return;
			}

			// A lambda, not a free function: MifBridgeCommon.cpp already has this walk as
			// MifDeclaringClassOfNativeSubobject in an anonymous namespace, and under a Unity build a
			// second namespace-scope copy is a C2084 even with internal linkage. It goes away with this
			// whole block when the enumerator is folded into MifBridgeCommon.cpp.
			auto DeclaringClassOfSubobject = [](UClass* StartClass, const FName SubobjectName) -> UClass*
			{
				UClass* Best = nullptr;
				for (UClass* C = StartClass; C != nullptr; C = C->GetSuperClass())
				{
					UObject* ClassDefault = C->GetDefaultObject(/*bCreateIfNeeded*/ false);
					if (ClassDefault && ClassDefault->GetDefaultSubobjectByName(SubobjectName)) { Best = C; }
				}
				return Best;
			};

			TMap<UActorComponent*, FProperty*> PropertyForComponent;
			for (TFieldIterator<FObjectPropertyBase> It(TargetClass); It; ++It)
			{
				FObjectPropertyBase* OP = *It;
				if (!OP) { continue; }
				UObject* Value = OP->GetObjectPropertyValue(OP->ContainerPtrToValuePtr<void>(CDO));
				UActorComponent* Comp = Cast<UActorComponent>(Value);
				if (Comp && Comp->GetOuter() == CDO && !PropertyForComponent.Contains(Comp))
				{
					PropertyForComponent.Add(Comp, OP);
				}
			}
			const AActor* ActorCDO = Cast<AActor>(CDO);
			TArray<UObject*> Subobjects;
			CDO->GetDefaultSubobjects(Subobjects);
			for (UObject* Sub : Subobjects)
			{
				UActorComponent* Comp = Cast<UActorComponent>(Sub);
				if (!Comp || !HasRoom()) { continue; }
				FProperty** Found = PropertyForComponent.Find(Comp);
				const FName RowName = Found ? (*Found)->GetFName() : Comp->GetFName();
				if (RowName == NAME_None || Seen.Contains(RowName)) { continue; }
				Seen.Add(RowName);

				FComponentOriginRow Row;
				Row.Name           = RowName;
				Row.Origin         = kComponentOriginNative;
				Row.ComponentClass = Comp->GetClass();
				Row.Template       = Comp;
				Row.SubobjectName  = Comp->GetFName();
				Row.OwningClass    = Found ? (*Found)->GetOwnerClass()
				                           : DeclaringClassOfSubobject(TargetClass, Comp->GetFName());
				Row.bIsRoot        = ActorCDO && ActorCDO->GetRootComponent() == Comp;
				Row.bCanOverride   = false;
				Row.CannotOverrideReason = FString::Printf(
					TEXT("native component: declared in a C++ parent class, not in a Blueprint's SCS. ")
					TEXT("UInheritableComponentHandler never applies to it (SubobjectData.cpp:148 excludes it), and this CDO ")
					TEXT("belongs to %s where %s, so it cannot be written either."),
					*TargetNoun, *NoEditableClause);
				OutRows.Add(Row);
			}
		}

		// The same projection GatherAvailableComponents emits on get_inherited_component's notFound
		// path (MifBridgeInherited.cpp:238-263), from rows this endpoint has already enumerated. Kept
		// to the same field names on purpose: an agent that learned the shape from one verb must not
		// have to learn a second one from the other.
		void MifComponentsEmitAvailableNames(const TArray<FComponentOriginRow>& Rows,
			const TSharedRef<FJsonObject>& Out, int32 Cap)
		{
			TArray<TSharedPtr<FJsonValue>> Json;
			for (const FComponentOriginRow& Row : Rows)
			{
				if (Cap > 0 && Json.Num() >= Cap) { break; }
				TSharedRef<FJsonObject> RowJson = MakeShared<FJsonObject>();
				RowJson->SetStringField(TEXT("name"), Row.Name.ToString());
				RowJson->SetStringField(TEXT("origin"), Row.Origin);
				if (Row.ComponentClass) { RowJson->SetStringField(TEXT("class"), Row.ComponentClass->GetName()); }
				if (Row.SubobjectName != NAME_None)
				{
					RowJson->SetStringField(TEXT("subobjectName"), Row.SubobjectName.ToString());
				}
				Json.Add(MakeShared<FJsonValueObject>(RowJson));
			}
			Out->SetArrayField(TEXT("availableComponents"), Json);
			Out->SetNumberField(TEXT("availableComponentCount"), Json.Num());
			Out->SetNumberField(TEXT("availableComponentTotal"), Rows.Num());
		}
	}

	// --- add_component ------------------------------------------------------


	// =======================================================================
	// INSTANCE COMPONENTS - the same four verbs aimed at a PLACED ACTOR
	// =======================================================================
	//
	// "PUT A POINT LIGHT ON THIS ONE LAMP POST." Until now that meant editing the shared
	// BP_LampPost asset, which changes all ninety of them. Everything in this file above addresses a
	// Blueprint's SCS - the template every instance is built from - and there was no way to touch
	// ONE placed actor.
	//
	// AND IT IS THE COMPONENT ROUTE A COOKED PROJECT HAS. The SCS route needs a UBlueprint, which
	// cooking strips - this file's own header says so. Instance components are pure runtime object
	// graph: no SCS, no MeshDescription, no source data. So on a cooked project this is not a
	// second-best path, it is the only one.
	//
	// ROUTED, NOT REWRITTEN. Each of the four handlers gains an early branch when actorPath is
	// present and is otherwise untouched, because the Blueprint paths above are cooked-aware in ways
	// that took a while to get right and nothing here should disturb them. actorPath and blueprintId
	// are mutually exclusive and passing both is refused rather than silently preferring one.

	AActor* MifInstResolveActor(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UEditorActorSubsystem* Sub = GEditor ? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
		if (!Sub)
		{
			Fail(Out, TEXT("no EditorActorSubsystem - this is not a running editor."));
			return nullptr;
		}
		// ResolveActor, never GetActorReference: the latter does not resolve the paths
		// list_level_actors emits, and this repo has now written that resolver three times learning
		// it. See MifBridgeStreaming.cpp's note.
		TSharedRef<FJsonObject> One = MakeShared<FJsonObject>();
		One->SetStringField(TEXT("actorPath"), JStrAny(In, { TEXT("actorPath"), TEXT("actor") }));
		return ResolveActor(Sub, One, Out);
	}

	/** Where a component came from, which decides what may be done to it. */
	const TCHAR* MifComponentOrigin(const AActor* Actor, const UActorComponent* Comp)
	{
		if (Actor->GetInstanceComponents().Contains(const_cast<UActorComponent*>(Comp)))
		{
			return TEXT("instance");
		}
		if (Comp->CreationMethod == EComponentCreationMethod::SimpleConstructionScript
			|| Comp->CreationMethod == EComponentCreationMethod::UserConstructionScript)
		{
			return TEXT("blueprintCreated");
		}
		return TEXT("native");
	}

	UActorComponent* MifFindComponentOn(AActor* Actor, const FString& Name)
	{
		TArray<UActorComponent*> All;
		Actor->GetComponents(All);
		for (UActorComponent* C : All)
		{
			if (C && (C->GetName() == Name || C->GetFName().ToString() == Name))
			{
				return C;
			}
		}
		return nullptr;
	}

	TSharedRef<FJsonObject> MifSerializeInstanceComponent(AActor* Actor, UActorComponent* Comp)
	{
		TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
		J->SetStringField(TEXT("name"), Comp->GetName());
		J->SetStringField(TEXT("class"), Comp->GetClass()->GetPathName());
		// componentPath is directly usable as set_property's objectPath, which is the whole reason a
		// caller wants this list.
		J->SetStringField(TEXT("componentPath"), Comp->GetPathName());
		J->SetStringField(TEXT("origin"), MifComponentOrigin(Actor, Comp));
		J->SetBoolField(TEXT("registered"), Comp->IsRegistered());
		if (const USceneComponent* Scene = Cast<USceneComponent>(Comp))
		{
			if (const USceneComponent* AttachParent = Scene->GetAttachParent())
			{
				J->SetStringField(TEXT("attachParent"), AttachParent->GetName());
			}
			J->SetBoolField(TEXT("isRoot"), Actor->GetRootComponent() == Scene);
			auto Vec = [](const FVector& V)
			{
				TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
				O->SetNumberField(TEXT("x"), V.X); O->SetNumberField(TEXT("y"), V.Y);
				O->SetNumberField(TEXT("z"), V.Z);
				return O;
			};
			const FRotator R = Scene->GetRelativeRotation();
			TSharedRef<FJsonObject> Rot = MakeShared<FJsonObject>();
			Rot->SetNumberField(TEXT("pitch"), R.Pitch); Rot->SetNumberField(TEXT("yaw"), R.Yaw);
			Rot->SetNumberField(TEXT("roll"), R.Roll);
			J->SetObjectField(TEXT("relativeLocation"), Vec(Scene->GetRelativeLocation()));
			J->SetObjectField(TEXT("relativeRotation"), Rot);
			J->SetObjectField(TEXT("relativeScale"), Vec(Scene->GetRelativeScale3D()));
		}
		return J;
	}

	void MifInstListComponents(AActor* Actor, const TSharedRef<FJsonObject>& In,
							   const TSharedRef<FJsonObject>& Out)
	{
		TArray<UActorComponent*> All;
		Actor->GetComponents(All);
		const FString Only = JStrAny(In, { TEXT("component"), TEXT("componentName") });
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 500), 1, 5000);

		TArray<TSharedPtr<FJsonValue>> Rows;
		int32 Instances = 0;
		for (UActorComponent* C : All)
		{
			if (!C) { continue; }
			if (FString(MifComponentOrigin(Actor, C)) == TEXT("instance")) { ++Instances; }
			if (!Only.IsEmpty() && C->GetName() != Only) { continue; }
			if (Rows.Num() >= Limit) { continue; }
			Rows.Add(MakeShared<FJsonValueObject>(MifSerializeInstanceComponent(Actor, C)));
		}
		Out->SetStringField(TEXT("targetKind"), TEXT("levelActor"));
		Out->SetStringField(TEXT("actorPath"), Actor->GetPathName());
		Out->SetStringField(TEXT("actorLabel"), Actor->GetActorLabel());
		Out->SetNumberField(TEXT("count"), All.Num());
		Out->SetNumberField(TEXT("instanceComponents"), Instances);
		Out->SetArrayField(TEXT("components"), Rows);
		if (!Only.IsEmpty() && Rows.Num() == 0)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no component named '%s'. It has %d. NOTHING was changed."),
				*Actor->GetActorLabel(), *Only, All.Num()));
		}
	}

	void MifInstAddComponent(AActor* Actor, const TSharedRef<FJsonObject>& In,
							 const TSharedRef<FJsonObject>& Out)
	{
		const FString ClassName = JStrAny(In, { TEXT("componentClass"), TEXT("class") });
		FString ClassError;
		UClass* CompClass = ResolveClassStrict(ClassName, nullptr, TEXT("componentClass"), ClassError);
		if (!CompClass)
		{
			Fail(Out, ClassError.IsEmpty()
				? FString::Printf(TEXT("component class not found: '%s'. NOTHING was created."), *ClassName)
				: ClassError);
			return;
		}
		if (!CompClass->IsChildOf(UActorComponent::StaticClass()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not an ActorComponent. NOTHING was created."), *CompClass->GetName()));
			return;
		}
		if (CompClass->HasAnyClassFlags(CLASS_Abstract))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is ABSTRACT and cannot be instantiated - pick a concrete subclass. ")
				TEXT("NOTHING was created."), *CompClass->GetName()));
			return;
		}

		// NAMING THROUGH THE ENGINE'S OWN VALIDATOR, so a name that collides is refused rather than
		// silently uniquified into something the caller did not ask for - the add_component defect
		// this file already fixed once on the SCS side.
		FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty())
		{
			Name = FComponentEditorUtils::GenerateValidVariableName(CompClass, Actor);
		}
		else if (!FComponentEditorUtils::IsComponentNameAvailable(Name, Actor))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' already has something named '%s'. Component names must be unique on an ")
				TEXT("actor, and silently renaming yours would leave you addressing a component that ")
				TEXT("does not exist. NOTHING was created."), *Actor->GetActorLabel(), *Name));
			return;
		}

		// THE PARENT DECISION, made explicitly rather than left to chance. A Blueprint- or
		// construction-script-created component is DESTROYED and rebuilt whenever the construction
		// script reruns (ActorConstruction.cpp detaches instance children first and leaves
		// re-attachment to FComponentInstanceDataCache), so parenting to one is a promise this
		// endpoint cannot keep. Native and instance parents are stable; those are allowed and the
		// rest is refused by name.
		USceneComponent* AttachTo = nullptr;
		const FString ParentName = JStrAny(In, { TEXT("parentName"), TEXT("parent") });
		if (!ParentName.IsEmpty())
		{
			UActorComponent* ParentComp = MifFindComponentOn(Actor, ParentName);
			if (!ParentComp)
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' has no component named '%s' to attach to. NOTHING was created."),
					*Actor->GetActorLabel(), *ParentName));
				return;
			}
			AttachTo = Cast<USceneComponent>(ParentComp);
			if (!AttachTo)
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is not a SceneComponent, so nothing can be attached to it. NOTHING ")
					TEXT("was created."), *ParentName));
				return;
			}
			const FString Origin = MifComponentOrigin(Actor, ParentComp);
			if (Origin == TEXT("blueprintCreated"))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' was created by this actor's construction script, and every rerun of ")
					TEXT("that script DESTROYS and rebuilds it - your component would be detached and ")
					TEXT("its re-attachment left to the instance-data cache. Attach to a NATIVE ")
					TEXT("component or to the root instead, or add this to the Blueprint's SCS with ")
					TEXT("blueprintId so it is rebuilt with everything else. NOTHING was created."),
					*ParentName));
				return;
			}
		}
		else
		{
			AttachTo = Actor->GetRootComponent();
		}

		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_AddInstanceComponent",
												 "Add Instance Component"));
		Actor->Modify();

		UActorComponent* Comp = NewObject<UActorComponent>(Actor, CompClass, FName(*Name),
														   RF_Transactional);
		if (!Comp)
		{
			Fail(Out, FString::Printf(TEXT("failed to construct '%s'. NOTHING was created."),
				*CompClass->GetName()));
			return;
		}
		Actor->AddInstanceComponent(Comp);
		if (USceneComponent* AsScene = Cast<USceneComponent>(Comp))
		{
			if (AttachTo)
			{
				AsScene->AttachToComponent(AttachTo, FAttachmentTransformRules::KeepRelativeTransform);
			}
			else if (!Actor->GetRootComponent())
			{
				// An actor with no root at all - give it one rather than leaving a scene component
				// floating unattached, which renders nowhere and reports a transform nobody set.
				Actor->SetRootComponent(AsScene);
			}
		}
		// REGISTER BEFORE READING ANYTHING BACK. An unregistered component has no world transform
		// and does not render; reporting its transform before this would be a number that is not yet
		// true.
		Comp->RegisterComponent();

		// READ BACK from the actor rather than from the pointer we just made.
		UActorComponent* Found = MifFindComponentOn(Actor, Comp->GetName());
		if (!Found)
		{
			Fail(Out, FString::Printf(
				TEXT("the component was constructed and '%s' does not report it on read-back. ")
				TEXT("NOTHING usable was produced."), *Actor->GetActorLabel()));
			return;
		}
		Actor->MarkPackageDirty();

		Out->SetStringField(TEXT("targetKind"), TEXT("levelActor"));
		Out->SetStringField(TEXT("actorPath"), Actor->GetPathName());
		Out->SetObjectField(TEXT("component"), MifSerializeInstanceComponent(Actor, Found));
		Out->SetStringField(TEXT("nameRequested"), JStr(In, TEXT("name")));
		Out->SetBoolField(TEXT("created"), true);
		Out->SetStringField(TEXT("levelNote"),
			TEXT("this is an INSTANCE component - it exists on this one actor and not on the "
				 "Blueprint, so no other placed copy is affected. The level is dirty and NOTHING has "
				 "been saved."));
	}

	void MifInstRemoveComponent(AActor* Actor, const TSharedRef<FJsonObject>& In,
								const TSharedRef<FJsonObject>& Out)
	{
		const FString Name = JStrAny(In, { TEXT("component"), TEXT("componentName"), TEXT("name") });
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("component (aliases: componentName, name) is required. NOTHING was changed."));
			return;
		}
		UActorComponent* Comp = MifFindComponentOn(Actor, Name);
		if (!Comp)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no component named '%s'. NOTHING was changed."),
				*Actor->GetActorLabel(), *Name));
			return;
		}

		// REFUSE ANYTHING THAT IS NOT AN INSTANCE COMPONENT, by name. RemoveInstanceComponent only
		// touches the InstanceComponents array, so on a native or SCS-created component it is a
		// SILENT NO-OP - the endpoint would report success having removed nothing, on exactly the
		// components an agent reaches for first. FComponentEditorUtils::CanDeleteComponent is the
		// engine's own test and is consulted too.
		const FString Origin = MifComponentOrigin(Actor, Comp);
		if (Origin != TEXT("instance"))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s component, not an instance one, and cannot be removed from a ")
				TEXT("single actor - RemoveInstanceComponent would silently do nothing. A %s ")
				TEXT("component belongs to the class: remove it with remove_component ")
				TEXT("blueprintId=<the Blueprint>, which changes every placed copy. NOTHING was ")
				TEXT("changed."), *Name, *Origin, *Origin));
			return;
		}
		if (!FComponentEditorUtils::CanDeleteComponent(Comp))
		{
			Fail(Out, FString::Printf(
				TEXT("the editor refuses to delete '%s' - FComponentEditorUtils::CanDeleteComponent ")
				TEXT("says no. NOTHING was changed."), *Name));
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, FString::Printf(
				TEXT("removing '%s' from '%s' destroys it and anything attached below it. Pass ")
				TEXT("confirm:true. NOTHING was changed."), *Name, *Actor->GetActorLabel()));
			return;
		}

		TArray<UActorComponent*> Before;
		Actor->GetComponents(Before);
		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_RemoveInstanceComponent",
												 "Remove Instance Component"));
		Actor->Modify();
		Actor->RemoveInstanceComponent(Comp);
		Comp->DestroyComponent();

		// READ BACK - both calls are void.
		if (MifFindComponentOn(Actor, Name))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is still present on read-back after being removed and destroyed."), *Name));
			return;
		}
		TArray<UActorComponent*> After;
		Actor->GetComponents(After);
		Actor->MarkPackageDirty();

		Out->SetStringField(TEXT("targetKind"), TEXT("levelActor"));
		Out->SetStringField(TEXT("actorPath"), Actor->GetPathName());
		Out->SetStringField(TEXT("component"), Name);
		Out->SetBoolField(TEXT("removed"), true);
		Out->SetNumberField(TEXT("componentsBefore"), Before.Num());
		Out->SetNumberField(TEXT("remaining"), After.Num());

		// MORE THAN ONE COMPONENT CAN GO. Found live 2026-08-30: adding a PointLightComponent also
		// creates the editor BILLBOARD sprite that visualises it, and destroying the light takes the
		// billboard with it - so componentsBefore 3 became remaining 1 for a single named removal.
		// That is correct engine behaviour and it is NOT obvious from two counts; a caller diffing
		// them would think something else had been destroyed. DestroyComponent also takes anything
		// attached BELOW the removed component, which is the other way this happens.
		const int32 Went = Before.Num() - After.Num();
		Out->SetNumberField(TEXT("componentsRemoved"), Went);
		if (Went > 1)
		{
			TArray<TSharedPtr<FJsonValue>> Extra;
			for (UActorComponent* B : Before)
			{
				if (B && !After.Contains(B) && B->GetName() != Name)
				{
					Extra.Add(MakeShared<FJsonValueString>(B->GetName()));
				}
			}
			Out->SetArrayField(TEXT("alsoRemoved"), Extra);
			Out->SetStringField(TEXT("alsoRemovedNote"),
				TEXT("removing this component took others with it - named above rather than left as "
					 "a gap between two counts. Usually an editor visualiser the engine created "
					 "alongside it (a light's billboard sprite, for instance), or something that was "
					 "attached BELOW it."));
		}
		Out->SetStringField(TEXT("levelNote"),
			TEXT("the level is dirty and NOTHING has been saved."));
	}

	void MifInstSetComponentTransform(AActor* Actor, const TSharedRef<FJsonObject>& In,
									  const TSharedRef<FJsonObject>& Out)
	{
		const FString Name = JStrAny(In, { TEXT("component"), TEXT("componentName"), TEXT("name") });
		// An absent name is not a component that does not exist. Reported "has no component
		// named ''" before, which reads as a wrong name rather than a missing argument.
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("component is required (aliases: componentName, name) - list_components ")
					  TEXT("{actorPath} names every one on the actor. NOTHING was changed."));
			return;
		}
		UActorComponent* Comp = MifFindComponentOn(Actor, Name);
		if (!Comp)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no component named '%s'. NOTHING was changed."),
				*Actor->GetActorLabel(), *Name));
			return;
		}
		USceneComponent* Scene = Cast<USceneComponent>(Comp);
		if (!Scene)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s, which has no transform - only SceneComponents do. NOTHING was ")
				TEXT("changed."), *Name, *Comp->GetClass()->GetName()));
			return;
		}

		FVector Loc = Scene->GetRelativeLocation();
		FRotator Rot = Scene->GetRelativeRotation();
		FVector Scale = Scene->GetRelativeScale3D();
		// The shared readers return Absent / Read / Invalid rather than a bool, which matters: a
		// component the caller SUPPLIED but got wrong must fail, not be quietly ignored. That
		// distinction is the whole reason those four hand-rolled vector readers were replaced.
		FString ReadError;
		const EJsonRead GotLoc = ReadVectorField(In, TEXT("location"), Loc, ReadError);
		if (GotLoc == EJsonRead::Invalid) { Fail(Out, ReadError + TEXT(" NOTHING was changed.")); return; }
		const EJsonRead GotRot = ReadRotatorField(In, TEXT("rotation"), Rot, ReadError);
		if (GotRot == EJsonRead::Invalid) { Fail(Out, ReadError + TEXT(" NOTHING was changed.")); return; }
		const EJsonRead GotScale = ReadScaleField(In, TEXT("scale"), Scale, ReadError);
		if (GotScale == EJsonRead::Invalid) { Fail(Out, ReadError + TEXT(" NOTHING was changed.")); return; }

		if (GotLoc != EJsonRead::Read && GotRot != EJsonRead::Read && GotScale != EJsonRead::Read)
		{
			Fail(Out, TEXT("name at least one of location, rotation or scale. NOTHING was changed."));
			return;
		}

		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_SetInstanceCompTransform",
												 "Set Component Transform"));
		Scene->Modify();
		Scene->SetRelativeLocation(Loc);
		Scene->SetRelativeRotation(Rot);
		Scene->SetRelativeScale3D(Scale);
		Actor->MarkPackageDirty();

		Out->SetStringField(TEXT("targetKind"), TEXT("levelActor"));
		Out->SetStringField(TEXT("actorPath"), Actor->GetPathName());
		// The read-back is the whole response, not an echo of the request: a component attached to a
		// parent with non-uniform scale does not necessarily keep what it was handed.
		Out->SetObjectField(TEXT("component"), MifSerializeInstanceComponent(Actor, Comp));
		Out->SetStringField(TEXT("levelNote"),
			TEXT("the level is dirty and NOTHING has been saved."));
	}

	/** Shared entry: is this call aimed at a placed actor? Refuses both spellings together. */
	bool MifInstWantsActor(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
						   bool& bOutRefused)
	{
		bOutRefused = false;
		const bool bActor = !JStrAny(In, { TEXT("actorPath"), TEXT("actor") }).IsEmpty();
		const bool bBlueprint = !JStrAny(In, { TEXT("blueprintId"), TEXT("path") }).IsEmpty();
		if (bActor && bBlueprint)
		{
			Fail(Out, TEXT("actorPath and blueprintId are ALTERNATIVES and mean different things: ")
				TEXT("blueprintId edits the class and changes every placed copy, actorPath edits ")
				TEXT("this one actor only. Passing both would make this endpoint choose for you. ")
				TEXT("NOTHING was changed."));
			bOutRefused = true;
			return false;
		}
		return bActor;
	}

	void H_add_component(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("blueprintId"), TEXT("path"), TEXT("componentClass"), TEXT("class"), TEXT("name"),
			  TEXT("parentName"), TEXT("location"), TEXT("rotation"), TEXT("scale") },
			TEXT("blueprintId (alias: path), componentClass (alias: class), name (optional - the new component's variable name), parentName (an EXISTING component to attach under), location, rotation, scale"),
			{ { TEXT("componentName"), TEXT("spell it name - it is the NEW component's variable name") },
			  { TEXT("component"), TEXT("spell it name for the new component, or parentName for the existing one to attach it under") },
			  { TEXT("parent"), TEXT("spell it parentName - the EXISTING component the new one is attached under") },
			  { TEXT("transform"), TEXT("pass location / rotation / scale as separate keys; there is no combined transform key") } }))
		{
			return;
		}

		// INSTANCE-COMPONENT BRANCH. actorPath aims this at ONE placed actor instead of the class;
		// see the block above H_add_component for why the two are mutually exclusive and why the
		// Blueprint path below is left untouched rather than merged.
		{
			bool bRefused = false;
			if (MifInstWantsActor(In, Out, bRefused))
			{
				if (AActor* Actor = MifInstResolveActor(In, Out))
				{
					MifInstAddComponent(Actor, In, Out);
				}
				return;
			}
			if (bRefused) { return; }
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

		// A NAME THE CALLER ASKED FOR IS A NAME THEY EXPECT TO GET. SCS->CreateNode runs the requested
		// name through GenerateNewComponentName, which quietly returns "Turret1" when "Turret" is
		// taken - so add_component answered ok:true for a component the caller never asked for. The
		// response does carry the real name, but nothing marks it as different from the request, and a
		// caller who does not compare believes they have "Turret". Worse, a setup script run twice
		// silently accumulates Turret, Turret1, Turret2, which is the same repeat-run trap that broke
		// five suites in one night.
		//
		// Its three siblings all refuse this: add_variable ("name already in use"), create_function
		// ("function already exists") and add_event_dispatcher. add_component was the odd one out, so
		// this is consistency rather than a new policy - and it matches add_enum_value, which rolls
		// back and fails when the applied name differs from the requested one.
		//
		// Asked of the ENGINE rather than reimplemented: GenerateNewComponentName IS the rule
		// CreateNode applies, so this cannot drift from it. Checked BEFORE Modify()/CreateNode, so a
		// refusal leaves the blueprint untouched rather than relying on the transaction to undo it,
		// which PM-007 established it will not do.
		//
		// An OMITTED name is untouched by this: NAME_None means "engine, pick one", and it still does.
		if (VarName != NAME_None)
		{
			const FName Applied = SCS->GenerateNewComponentName(ComponentClass, VarName);
			if (Applied != VarName)
			{
				Fail(Out, FString::Printf(
					TEXT("a component named '%s' already exists on '%s' - the engine would have added it as '%s' ")
					TEXT("instead, which is not what was asked for. Nothing was added. Pick another name, omit ")
					TEXT("'name' to let the engine choose one, or edit the existing component."),
					*Name, *Blueprint->GetName(), *Applied.ToString()));
				return;
			}
		}

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
	//   in:  { blueprintId (path), component? (componentName), includeInherited? = true,
	//          includeNative? = true, limit? = 500 }
	//   out: { blueprint, targetKind, readOnly, parentClass, count, matched, truncated,
	//          totalComponentCount, components: [...], ownSCSCount, parentBlueprintSCSCount,
	//          nativeCount, inheritableComponentHandlerPath, existingOverrideCount,
	//          editableBlueprintExists, editableBlueprintPath? }
	//   out (component supplied): + { requestedComponent, exists, origin, canOverride,
	//          canOverrideReason, route } and, when exists=false, availableComponents[]
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
	// mint an ICH on the asset. The cooked path holds the same line - UBlueprintGeneratedClass::
	// GetInheritableComponentHandler defaults to bCreateIfNecessary=false (BlueprintGeneratedClass.h:749)
	// and the CDO is read with bCreateIfNeeded=FALSE.
	//
	// "COOKED" IS DECIDED BY UClass::ClassGeneratedBy, NOT BY "ResolveBlueprint FAILED". Those are not
	// the same question: ResolveBlueprint loads UBlueprint::StaticClass() only, so naming an EDITABLE
	// blueprint by its generated-class path ('/Game/A/BP_Foo.BP_Foo_C') fails it. That used to be
	// reported as targetKind:"cookedClass" - a read-only response, with every row routed to
	// create_editable_child - for an asset that was fully editable all along. A _C path whose class has
	// a UBlueprint in ClassGeneratedBy now RETARGETS onto that UBlueprint and gets the ordinary editable
	// response in every field; editableBlueprintExists/editableBlueprintPath say so explicitly.
	//
	// COOKED-ONLY TARGETS. blueprintId may name an asset that has NO editable UBlueprint - a cooked
	// package ships only the UBlueprintGeneratedClass. That used to be a hard refusal here, which was
	// wrong for a read: the SimpleConstructionScript survives cooking, so the component names were
	// present and being withheld, and get_inherited_component (which resolves ONE component BY NAME)
	// had no way to learn a name to ask about. Such a target now enumerates normally and is flagged
	// targetKind:"cookedClass", readOnly:true. Every row on that path reports canOverride:false with a
	// reason and route:"create_editable_child", and NO templatePath: the archetype lives inside a
	// cooked, pak-mounted package, so set_property on it cannot be persisted. cookedTemplatePath
	// carries it as a read-only reference instead. The editable path is unchanged in every field.
	//
	// NAME LOOKUP. Passing `component` answers ONE question - "does this name exist here, and what can
	// I do with it" - and answers it in a form that cannot be confused with "it exists but is not
	// overridable": exists:false + origin:"notFound" + route:"none" + availableComponents[] when the
	// name is unknown, exists:true + the row's own origin/canOverride/canOverrideReason when it is.
	// canOverride:false is NEVER the discriminator, on either verb: three of the four origins report it
	// legitimately. `exists` and `origin` are. The origin filters are deliberately IGNORED for a named
	// lookup - includeNative:false must not turn a native component into "no such component".
	void H_list_components(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("blueprintId"), TEXT("path"), TEXT("component"), TEXT("componentName"),
			  TEXT("includeInherited"), TEXT("includeNative"), TEXT("limit") },
			TEXT("EITHER blueprintId (alias: path) for a Blueprint's SCS, OR actorPath (alias: actor) for the ")
			TEXT("components a PLACED actor actually has - including instance components that exist on that ")
			TEXT("one actor only. component (alias: componentName; optional - omit for the whole list), ")
			TEXT("includeInherited (default true), includeNative (default true), limit (default 500)")))
		{
			return;
		}

		// INSTANCE-COMPONENT BRANCH. actorPath aims this at ONE placed actor instead of the class;
		// see the block above H_add_component for why the two are mutually exclusive and why the
		// Blueprint path below is left untouched rather than merged.
		{
			bool bRefused = false;
			if (MifInstWantsActor(In, Out, bRefused))
			{
				if (AActor* Actor = MifInstResolveActor(In, Out))
				{
					MifInstListComponents(Actor, In, Out);
				}
				return;
			}
			if (bRefused) { return; }
		}

		// ResolveBlueprint rather than ResolveBlueprintField: the field form writes its failure into
		// Out immediately, and a cooked target has to get past that failure to be answered. The exact
		// same graded message (DescribeMissingBlueprint) is still what a caller gets when there is no
		// generated class either, so nothing about the genuine not-found case changes.
		const FString TargetPath = JStrAny(In, { TEXT("blueprintId"), TEXT("path") });
		FString ResolveError;
		UBlueprint* Blueprint = ResolveBlueprint(TargetPath, ResolveError);
		UBlueprintGeneratedClass* CookedClass = nullptr;
		if (!Blueprint)
		{
			CookedClass = MifComponentsLoadGeneratedClass(TargetPath);
			if (!CookedClass)
			{
				Fail(Out, ResolveError);
				return;
			}
			// "ResolveBlueprint failed" is a SYMPTOM, not the discriminator, and treating it as one was a
			// live defect: ResolveBlueprint loads UBlueprint::StaticClass() ONLY (MifBridgeCommon.cpp:2868),
			// so a caller who names a perfectly EDITABLE blueprint by its generated-class path
			// ('<Package>.<Short>_C' - the spelling MifComponentsLoadGeneratedClass probes FIRST, and the
			// spelling half the class-facing endpoints hand back) failed it and was answered
			// targetKind:"cookedClass", readOnly:true, every route pointing at create_editable_child for an
			// asset that needed no such thing. The real test is the editor back-pointer: ClassGeneratedBy
			// is null exactly when the package is cooked (MifBridgeCommon.cpp:2928 makes the same test;
			// MifBridgeNodes.cpp:76 makes the same cast). Retarget onto it and the full editable response -
			// live templatePath, real canOverride, override_inherited_component routes - follows unchanged.
			if (UBlueprint* GeneratedByBlueprint = Cast<UBlueprint>(CookedClass->ClassGeneratedBy))
			{
				Blueprint = GeneratedByBlueprint;
			}
		}
		const bool bCookedTarget = (Blueprint == nullptr);
		// Only consulted on the cooked branch. A non-null ClassGeneratedBy that survived the retarget above
		// is a generator that is NOT a UBlueprint: read-only here too, but not cooked, and the strings below
		// must not say "cooked" about it.
		const bool bGenuinelyCooked = bCookedTarget && (CookedClass->ClassGeneratedBy == nullptr);

		const FString WantedNameStr = JStrAny(In, { TEXT("component"), TEXT("componentName") }).TrimStartAndEnd();
		const bool  bNameLookup       = !WantedNameStr.IsEmpty();
		const FName WantedName        = bNameLookup ? FName(*WantedNameStr) : NAME_None;
		const bool  bIncludeInherited = bNameLookup ? true : JBool(In, TEXT("includeInherited"), true);
		const bool  bIncludeNative    = bNameLookup ? true : JBool(In, TEXT("includeNative"), true);
		const int32 Limit             = FMath::Clamp(JInt(In, TEXT("limit"), 500), 1, 5000);

		TArray<FComponentOriginRow> Rows;
		bool bCdoMissing = false;
		if (bCookedTarget)
		{
			MifComponentsEnumerateGeneratedClassComponents(CookedClass, Rows, /*Cap*/ 0, bCdoMissing);
		}
		else
		{
			EnumerateBlueprintComponents(Blueprint, Rows, /*Cap*/ 0);
		}

		// Resolved ONCE for the whole response: "which component is the actor's real scene root, and
		// where did it come from". Every isRoot / isOwnSCSRoot below is relative to this.
		TSharedRef<FJsonObject> RootResolution = MakeShared<FJsonObject>();
		if (!bCookedTarget && Blueprint && Blueprint->SimpleConstructionScript)
		{
			USimpleConstructionScript* SCS = Blueprint->SimpleConstructionScript;
			USCS_Node* RootSCSNode = nullptr;
			USceneComponent* SceneRoot = SCS->GetSceneRootComponentTemplate(true, &RootSCSNode);
			RootResolution->SetNumberField(TEXT("ownSCSRootNodeCount"), SCS->GetRootNodes().Num());
			RootResolution->SetStringField(TEXT("effectiveSceneRoot"),
				SceneRoot ? SceneRoot->GetName() : TEXT(""));
			// Where that root LIVES is the question isRoot could not answer. A root node in this
			// blueprint's own SCS is a different situation from a root inherited from a parent
			// blueprint or provided natively, and they need different handling by a caller.
			const TCHAR* RootKind = TEXT("unknown");
			if (!SceneRoot)                                   { RootKind = TEXT("none"); }
			else if (RootSCSNode && SCS->GetRootNodes().Contains(RootSCSNode)) { RootKind = TEXT("own_scs"); }
			else if (RootSCSNode)                             { RootKind = TEXT("inherited_scs"); }
			else                                              { RootKind = TEXT("native_or_inherited_native"); }
			RootResolution->SetStringField(TEXT("rootKind"), RootKind);
			RootResolution->SetStringField(TEXT("note"),
				TEXT("isRoot on a row means 'root of THIS blueprint's own SCS', not 'the actor's root'. "
					 "effectiveSceneRoot is the actor's real root once inheritance is applied; a row whose "
					 "attachmentResolution is inherited_root_fallback will hang beneath it at runtime."));
		}

		TArray<TSharedPtr<FJsonValue>> Arr;
		int32 OwnCount = 0, ParentCount = 0, NativeCount = 0, Matched = 0;
		bool bTruncated = false;
		const FComponentOriginRow* NamedRow = nullptr;   // points into Rows, which outlives this loop
		FString NamedRowRoute;

		for (const FComponentOriginRow& Row : Rows)
		{
			const bool bIsOwn    = (Row.Origin == kComponentOriginOwnSCS);
			const bool bIsParent = (Row.Origin == kComponentOriginParentSCS);
			const bool bIsNative = (Row.Origin == kComponentOriginNative);
			if (bNameLookup && Row.Name != WantedName) { continue; }
			if (bIsParent && !bIncludeInherited) { continue; }
			if (bIsNative && !bIncludeNative)    { continue; }
			if (bNameLookup) { NamedRow = &Row; }

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

			// ROOT OF WHAT? isRoot alone is ambiguous, and that ambiguity was reported from production:
			// a component added to a blueprint whose local SCS was previously EMPTY came back
			// isRoot:true with AttachParent None, and nothing in the response said how it would attach
			// beneath the INHERITED root at runtime. The caller could not prove the attachment before
			// compiling and testing in a packaged build.
			//
			// A component template's AttachParent does not describe SCS hierarchy - the USCS_Node does.
			// These fields separate the four things isRoot was conflating.
			if (!bCookedTarget && bIsOwn && Blueprint && Blueprint->SimpleConstructionScript)
			{
				USimpleConstructionScript* SCS = Blueprint->SimpleConstructionScript;
				if (USCS_Node* Node = SCS->FindSCSNode(Row.Name))
				{
					const bool bIsOwnSCSRoot = SCS->GetRootNodes().Contains(Node);
					Json->SetBoolField(TEXT("isOwnSCSRoot"), bIsOwnSCSRoot);

					// What the node DECLARES about its parent. None on a root node - which is exactly
					// the case that used to look like "attached to nothing".
					if (Node->ParentComponentOrVariableName != NAME_None)
					{
						Json->SetStringField(TEXT("declaredParentVariable"),
							Node->ParentComponentOrVariableName.ToString());
					}
					if (Node->ParentComponentOwnerClassName != NAME_None)
					{
						Json->SetStringField(TEXT("parentOwnerClass"),
							Node->ParentComponentOwnerClassName.ToString());
					}
					Json->SetBoolField(TEXT("parentIsNative"), Node->bIsParentComponentNative);

					// parentKind names WHICH of the four possible parents this is, so a caller never
					// has to infer it from a combination of booleans.
					const TCHAR* ParentKind = TEXT("none");
					if (Node->ParentComponentOrVariableName != NAME_None)
					{
						ParentKind = Node->bIsParentComponentNative ? TEXT("native_inherited")
																	: TEXT("inherited_blueprint");
					}
					else if (!bIsOwnSCSRoot)
					{
						ParentKind = TEXT("own_scs_component");
					}
					Json->SetStringField(TEXT("parentKind"), ParentKind);

					// The one a caller actually wants: what this will hang off at runtime. For an own
					// SCS root with no declared parent, the engine attaches it beneath whatever the
					// inherited/native scene root turns out to be.
					if (USceneComponent* DeclaredParent = Node->GetParentComponentTemplate(Blueprint))
					{
						Json->SetStringField(TEXT("expectedRuntimeAttachParent"), DeclaredParent->GetName());
						Json->SetStringField(TEXT("attachmentResolution"), TEXT("declared_parent"));
					}
					else if (bIsOwnSCSRoot)
					{
						USCS_Node* RootSCSNode = nullptr;
						USceneComponent* SceneRoot =
							SCS->GetSceneRootComponentTemplate(/*bShouldUseDefaultRoot*/ true, &RootSCSNode);
						if (SceneRoot && SceneRoot->GetFName() != Row.Name)
						{
							Json->SetStringField(TEXT("expectedRuntimeAttachParent"), SceneRoot->GetName());
							Json->SetStringField(TEXT("attachmentResolution"), TEXT("inherited_root_fallback"));
						}
						else
						{
							Json->SetStringField(TEXT("attachmentResolution"), TEXT("is_the_actor_root"));
						}
					}
				}
			}
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
			// Every row that is emitted at all is a row for a component that EXISTS. Stated rather than
			// implied so `exists` means the same thing here as it does on the named-lookup response,
			// where its whole job is to separate "cannot be overridden" from "no such name".
			Json->SetBoolField(TEXT("exists"), true);

			if (bCookedTarget)
			{
				// One branch for all three origins: on a cooked target the origin still says where the
				// component came from, but the answer to "what do I call next" is the same for all of
				// them, because nothing in a cooked package is writable.
				Json->SetBoolField(TEXT("inherited"), !bIsOwn);
				Json->SetBoolField(TEXT("readOnly"), true);
				Json->SetBoolField(TEXT("overrideExists"), false);
				Json->SetBoolField(TEXT("canOverride"), false);
				Json->SetStringField(TEXT("canOverrideReason"), Row.CannotOverrideReason);
				Json->SetBoolField(TEXT("editableWhenInherited"), Row.bEditableWhenInherited);
				if (Row.SubobjectName != NAME_None)
				{
					Json->SetStringField(TEXT("subobjectName"), Row.SubobjectName.ToString());
				}
				if (Row.Template)
				{
					// NOT templatePath. templatePath means "pass this to set_property" everywhere in
					// this response, and this archetype is inside a cooked, pak-mounted package: a write
					// there cannot be saved back, and save_package would emit a loose Content override of
					// a cooked package instead. Read-only reference only.
					Json->SetStringField(TEXT("cookedTemplatePath"), Row.Template->GetPathName());
					Json->SetStringField(TEXT("creationMethod"), ComponentCreationMethodString(Row.Template));
					if (bIsParent)
					{
						Json->SetStringField(TEXT("parentTemplatePath"), Row.Template->GetPathName());
					}
				}
				Json->SetStringField(TEXT("route"), TEXT("create_editable_child"));
				Json->SetStringField(TEXT("endpoint"), TEXT("create_editable_child"));
				Json->SetStringField(TEXT("hint"), FString::Printf(
					TEXT("'%s' is %s: this component is readable but nothing here is writable. ")
					TEXT("create_editable_child {sourceAsset:\"%s\", variant:\"child\"} mints an editable child; on that child ")
					TEXT("'%s' becomes an inherited component and %s. Until then, cookedTemplatePath is a read-only reference - ")
					TEXT("do NOT pass it to set_property."),
					*GetPathNameSafe(CookedClass),
					bGenuinelyCooked
						? TEXT("a COOKED class")
						: TEXT("a generated class whose generating asset is not an editable UBlueprint"),
					*GetPathNameSafe(CookedClass), *Row.Name.ToString(),
					bIsNative
						? TEXT("is edited on the child's own CDO subobject with set_property (list_components on the child reports templatePath)")
						: TEXT("override_inherited_component can mint a per-child override for it")));
			}
			else if (bIsOwn)
			{
				if (Row.Template) { Json->SetStringField(TEXT("templatePath"), Row.Template->GetPathName()); }
				Json->SetBoolField(TEXT("inherited"), false);
				Json->SetBoolField(TEXT("overrideExists"), false);
				Json->SetBoolField(TEXT("canOverride"), false);
				// This row used to be the ONLY canOverride:false row in the response with no reason
				// beside it - the parent and native rows both carry one - so a caller keyed on
				// {canOverride, canOverrideReason} read an absent reason and could not tell it from a
				// name that does not exist. EnumerateBlueprintComponents has always populated it
				// (MifBridgeCommon.cpp:2121); it was simply not emitted.
				Json->SetStringField(TEXT("canOverrideReason"), Row.CannotOverrideReason);
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
				if (!Row.bCanOverride)
				{
					// Emitted unconditionally when false. It used to be suppressed when the enumerator
					// left the string empty, which produced the one shape a caller cannot read: a false
					// with no reason, indistinguishable from a name that is not there at all.
					Json->SetStringField(TEXT("canOverrideReason"), Row.CannotOverrideReason.IsEmpty()
						? TEXT("inherited from a parent blueprint's SCS, but override_inherited_component will not accept it (no more specific reason was recorded)")
						: Row.CannotOverrideReason);
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
			// Read back rather than recomputed: the top-level route on a named lookup is the SAME
			// string the row carries, by construction, so the two can never drift apart.
			if (bNameLookup) { Json->TryGetStringField(TEXT("route"), NamedRowRoute); }
			Arr.Add(MakeShared<FJsonValueObject>(Json));
		}

		// Non-creating handler read, so a caller can see whether this asset carries any overrides at
		// all without the ICH being minted by the question. Both accessors default to
		// bCreateIfNecessary=false, which is what keeps this endpoint in IsReadOnlyEndpoint on either
		// path (UBlueprint's overload has no default and is passed false explicitly;
		// UBlueprintGeneratedClass's defaults to false, BlueprintGeneratedClass.h:749).
		UInheritableComponentHandler* ICH = bCookedTarget
			? CookedClass->GetInheritableComponentHandler()
			: Blueprint->GetInheritableComponentHandler(/*bCreateIfNecessary*/ false);
		int32 ExistingOverrides = 0;
		if (ICH)
		{
			TArray<UActorComponent*> Templates;
			ICH->GetAllTemplates(Templates, /*bIncludeTransientTemplates*/ false);
			ExistingOverrides = Templates.Num();
		}

		Out->SetStringField(TEXT("blueprint"), bCookedTarget ? CookedClass->GetPathName() : Blueprint->GetPathName());
		Out->SetStringField(TEXT("parentClass"), bCookedTarget
			? GetPathNameSafe(CookedClass->GetSuperClass())
			: GetPathNameSafe(Blueprint->ParentClass));
		// targetKind, not a guess from the presence of some other field: "blueprint" means an editable
		// UBlueprint asset and every route below is live; "cookedClass" means the generated class only,
		// and every row is read-only.
		Out->SetStringField(TEXT("targetKind"), bCookedTarget ? TEXT("cookedClass") : TEXT("blueprint"));
		Out->SetBoolField(TEXT("readOnly"), bCookedTarget);
		// COMPUTED from ClassGeneratedBy, never hardcoded. It was a literal false on the cooked branch,
		// which told a caller who had merely named an editable blueprint by its _C path to go mint a
		// DUPLICATE asset. Now emitted on BOTH branches with one meaning - "an editable UBlueprint backs
		// this target" - and, when it does, editableBlueprintPath is the asset to retarget every write
		// verb at. Additive on the editable branch; unchanged type and unchanged (false) value on the
		// genuinely cooked one.
		Out->SetBoolField(TEXT("editableBlueprintExists"), Blueprint != nullptr);
		if (Blueprint)
		{
			Out->SetStringField(TEXT("editableBlueprintPath"), Blueprint->GetPathName());
		}
		if (bCookedTarget)
		{
			Out->SetStringField(TEXT("cookedClassPath"), CookedClass->GetPathName());
			if (!bGenuinelyCooked)
			{
				// Not a UBlueprint, or the retarget at the top would have taken it - but an asset DOES
				// generate this class, and naming it beats implying nothing exists.
				Out->SetStringField(TEXT("classGeneratedByPath"), CookedClass->ClassGeneratedBy->GetPathName());
			}
			Out->SetStringField(TEXT("targetNote"), bGenuinelyCooked
				? FString::Printf(
					TEXT("'%s' has no editable UBlueprint (COOKED: its ClassGeneratedBy is null): its Blueprint graphs are stripped, but the SimpleConstructionScript ")
					TEXT("survives cooking, so the components below are the real, complete set and their names are the names ")
					TEXT("get_inherited_component and override_inherited_component take. Nothing here is writable - ")
					TEXT("create_editable_child {sourceAsset:\"%s\", variant:\"child\"} first, then re-run list_components on the child."),
					*CookedClass->GetPathName(), *CookedClass->GetPathName())
				: FString::Printf(
					TEXT("'%s' is NOT cooked - it is generated by '%s' - but that asset is not a UBlueprint, so none of this endpoint's ")
					TEXT("editable routes apply to it. The components below are the real, complete set and are readable; classGeneratedByPath ")
					TEXT("carries the generating asset in case a type-specific endpoint can edit it."),
					*CookedClass->GetPathName(), *CookedClass->ClassGeneratedBy->GetPathName()));
			if (bCdoMissing)
			{
				// "no native components" and "the native pass could not run" are different answers.
				Out->SetBoolField(TEXT("nativeEnumerated"), false);
				Out->SetStringField(TEXT("nativeNote"),
					TEXT("native components were NOT enumerated: this class has no constructed class-default object and one was deliberately not created for a read-only call, so nativeCount:0 means 'not looked at', not 'none'"));
			}
			else
			{
				Out->SetBoolField(TEXT("nativeEnumerated"), true);
			}
		}
		Out->SetNumberField(TEXT("totalComponentCount"), Rows.Num());
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
		Out->SetObjectField(TEXT("rootResolution"), RootResolution);

		// --- named lookup: the answer, at the top level, in a form that cannot be misread ---------
		//
		// The two states a caller has to tell apart are "this component exists but you cannot override
		// it, because X" and "there is no component with that name". canOverride:false is not the
		// discriminator - it is legitimately false for an own-SCS component, for a native component,
		// and for an inherited one whose key or class check fails. `exists` and `origin` are, and both
		// are always present on this branch.
		if (bNameLookup)
		{
			Out->SetStringField(TEXT("requestedComponent"), WantedNameStr);
			if (NamedRow)
			{
				Out->SetBoolField(TEXT("exists"), true);
				Out->SetStringField(TEXT("origin"), NamedRow->Origin);
				Out->SetStringField(TEXT("owningClass"), GetPathNameSafe(NamedRow->OwningClass));
				Out->SetStringField(TEXT("componentClass"), GetPathNameSafe(NamedRow->ComponentClass));
				const bool bRowCanOverride = !bCookedTarget && NamedRow->bCanOverride;
				Out->SetBoolField(TEXT("canOverride"), bRowCanOverride);
				if (!bRowCanOverride)
				{
					Out->SetStringField(TEXT("canOverrideReason"), NamedRow->CannotOverrideReason.IsEmpty()
						? TEXT("override_inherited_component will not accept this component (no more specific reason was recorded)")
						: NamedRow->CannotOverrideReason);
				}
				// route/endpoint/hint for this one row are already on components[0]; echoing the route
				// here saves the caller a hop, and it is the row's own string so the two cannot drift.
				if (!NamedRowRoute.IsEmpty())
				{
					Out->SetStringField(TEXT("route"), NamedRowRoute);
				}
			}
			else
			{
				// NOT ok:false. "Does this name exist here" was asked and has been answered; a
				// transport-level failure would tell the caller nothing about the name.
				Out->SetBoolField(TEXT("exists"), false);
				Out->SetStringField(TEXT("origin"), kComponentOriginNotFound);
				Out->SetBoolField(TEXT("canOverride"), false);
				Out->SetStringField(TEXT("canOverrideReason"), FString::Printf(
					TEXT("NO COMPONENT NAMED '%s' EXISTS on '%s' - not in its own SimpleConstructionScript, not in any parent ")
					TEXT("blueprint's SCS up the class chain, and not among its class-default object's native subobjects. ")
					TEXT("This is a name-not-found, NOT an 'exists but is not overridable': see exists:false and ")
					TEXT("origin:\"notFound\". The names that DO exist are in availableComponents."),
					*WantedNameStr, *(bCookedTarget ? CookedClass->GetPathName() : Blueprint->GetPathName())));
				Out->SetStringField(TEXT("route"), TEXT("none"));
				Out->SetStringField(TEXT("hint"),
					TEXT("pick a name from availableComponents and call again; component names are matched as FNames (case-insensitive) and are the variable names the Details panel shows, not the subobject names - for a native component those differ (Mesh -> CharacterMesh0) and subobjectName carries the second one"));
				MifComponentsEmitAvailableNames(Rows, Out, /*Cap*/ 80);
			}
		}
	}

	// --- remove_component ---------------------------------------------------

	void H_remove_component(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm") },
			TEXT("EITHER blueprintId (alias: path) to remove from the CLASS - which changes every placed copy ")
			TEXT("- OR actorPath (alias: actor) to remove an INSTANCE component from one placed actor. ")
			TEXT("name (the component's name), confirm (required true)"),
			{ { TEXT("component"), TEXT("spell it name here - list_components takes 'component', remove_component takes 'name'") },
			  { TEXT("componentName"), TEXT("spell it name") } }))
		{
			return;
		}

		// INSTANCE-COMPONENT BRANCH. actorPath aims this at ONE placed actor instead of the class;
		// see the block above H_add_component for why the two are mutually exclusive and why the
		// Blueprint path below is left untouched rather than merged.
		{
			bool bRefused = false;
			if (MifInstWantsActor(In, Out, bRefused))
			{
				if (AActor* Actor = MifInstResolveActor(In, Out))
				{
					MifInstRemoveComponent(Actor, In, Out);
				}
				return;
			}
			if (bRefused) { return; }
		}

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

		// The CHILDREN, counted before the removal, because the call PROMOTES them and afterwards
		// they are no longer this node's. A caller who removed a parent component and got back only
		// "removed": true has no idea its children were reparented onto the root - which is the same
		// shape as remove_tree_widget deleting a whole subtree and reporting one line (docs/06).
		TArray<FString> Promoted;
		for (const USCS_Node* Child : Node->GetChildNodes())
		{
			if (Child) { Promoted.Add(Child->GetVariableName().ToString()); }
		}

		Blueprint->Modify();
		SCS->Modify();
		SCS->RemoveNodeAndPromoteChildren(Node);
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);

		// RE-QUERY, because RemoveNodeAndPromoteChildren is VOID in both engines and cannot report a
		// refusal. This is PM-007 exactly: RemoveMemberVariable is also void and also early-returns
		// when it declines, and remove_variable learned to check afterwards. These two removals never
		// did, so they reported "removed" whether or not anything was.
		if (SCS->FindSCSNode(FName(*Name)) != nullptr)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is STILL a component after RemoveNodeAndPromoteChildren - nothing was "
					 "removed. The call is void and cannot refuse out loud, so this is the only way to "
					 "tell. An INHERITED component is the usual reason: it is declared on a parent "
					 "class and cannot be removed here, only overridden."), *Name));
			return;
		}

		Out->SetStringField(TEXT("removed"), Name);
		Out->SetNumberField(TEXT("childrenPromoted"), Promoted.Num());
		if (Promoted.Num() > 0)
		{
			TArray<TSharedPtr<FJsonValue>> Kids;
			for (const FString& K : Promoted) { Kids.Add(MakeShared<FJsonValueString>(K)); }
			Out->SetArrayField(TEXT("promotedChildren"), Kids);
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("%d child component(s) were PROMOTED rather than deleted - they are still in the "
					 "blueprint, reparented onto what held '%s'. Their world transforms may have "
					 "changed as a result."), Promoted.Num(), *Name));
		}
	}

	// --- set_component_transform --------------------------------------------

	void H_set_component_transform(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("name"), TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("location"), TEXT("rotation"), TEXT("scale") },
			TEXT("blueprintId (alias: path), name (the component's variable name), location, rotation, scale - each {x,y,z} or [x,y,z]"),
			{ { TEXT("component"), TEXT("spell it name here - list_components takes 'component', set_component_transform takes 'name'") },
			  { TEXT("componentName"), TEXT("spell it name") },
			  { TEXT("relativeLocation"), TEXT("spell it location - the transform written here is already the RELATIVE one") },
			  { TEXT("transform"), TEXT("pass location / rotation / scale as separate keys; there is no combined transform key") } }))
		{
			return;
		}

		// INSTANCE-COMPONENT BRANCH. actorPath aims this at ONE placed actor instead of the class;
		// see the block above H_add_component for why the two are mutually exclusive and why the
		// Blueprint path below is left untouched rather than merged.
		{
			bool bRefused = false;
			if (MifInstWantsActor(In, Out, bRefused))
			{
				if (AActor* Actor = MifInstResolveActor(In, Out))
				{
					MifInstSetComponentTransform(Actor, In, Out);
				}
				return;
			}
			if (bRefused) { return; }
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

		// Report the transform the component ACTUALLY carries now. The _Direct setters write the
		// field outright and cannot refuse, so this is not a silent-failure guard - it is so a caller
		// can confirm the result without a second round trip, which is what set_actor_transform
		// already does with locationApplied/rotationApplied/scaleApplied. Reading it back also states
		// which of the three were written and which were left alone.
		Out->SetBoolField(TEXT("locationApplied"), LocRead == EJsonRead::Read);
		Out->SetBoolField(TEXT("rotationApplied"), RotRead == EJsonRead::Read);
		Out->SetBoolField(TEXT("scaleApplied"), ScaleRead == EJsonRead::Read);
		const FRotator NowRot = SceneTemplate->GetRelativeRotation();
		Out->SetObjectField(TEXT("relativeLocation"), Vec3(SceneTemplate->GetRelativeLocation()));
		Out->SetObjectField(TEXT("relativeRotation"), Vec3(NowRot.Pitch, NowRot.Yaw, NowRot.Roll));
		Out->SetObjectField(TEXT("relativeScale"), Vec3(SceneTemplate->GetRelativeScale3D()));
	}
}
