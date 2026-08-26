// MifBridge — node creation, pin wiring, and batch endpoints (the graph-edit core).
#include "MifBridgeHandlers.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Engine/MemberReference.h"
#include "HAL/FileManager.h"
#include "K2Node_CallFunction.h"
// Engine's node-class selection chain for add_function_call (mirrors UBlueprintFunctionNodeSpawner::Create).
#include "K2Node_CallArrayFunction.h"                        // MD_ArrayParam — the whole UKismetArrayLibrary
#include "K2Node_CallDataTableFunction.h"                    // MD_DataTablePin — retypes the row struct
#include "K2Node_CallMaterialParameterCollectionFunction.h"  // MD_MaterialParameterCollectionFunction
#include "K2Node_CommutativeAssociativeBinaryOperator.h"     // pure commutative ops grow input pins
#include "K2Node_Message.h"                                  // interface calls on an external target
#include "K2Node_CallParentFunction.h"
#include "K2Node_ComponentBoundEvent.h"  // add_component_bound_event — real per-component delegate binding
#include "K2Node_DynamicCast.h"
#include "K2Node_EditablePinBase.h"   // RemoveUserDefinedPinByName / UserDefinedPins (remove_pin)
#include "K2Node_CustomEvent.h"       // custom-event parameter target (add_pin)
#include "K2Node_FunctionEntry.h"     // function inputs live here as EGPD_Output (add_pin)
#include "K2Node_FunctionResult.h"    // sibling Return-node signature sync (add_pin / remove_pin)
#include "K2Node_Event.h"
#include "K2Node_GetArrayItem.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_Knot.h"
#include "K2Node_MacroInstance.h"
#include "K2Node_Variable.h"          // SetFromProperty — the engine's own "point this node at an FProperty"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "ScopedTransaction.h"
#include "UObject/UnrealType.h"      // FProperty flags + TFieldIterator (foreign-property resolution)

namespace MifBridge
{
	namespace
	{
		bool BlueprintHasVariable(UBlueprint* Blueprint, const FString& Name)
		{
			for (const FBPVariableDescription& Var : Blueprint->NewVariables)
			{
				if (Var.VarName.ToString() == Name)
				{
					return true;
				}
			}
			const FName VarName(*Name);
			if (Blueprint->SkeletonGeneratedClass && Blueprint->SkeletonGeneratedClass->FindPropertyByName(VarName))
			{
				return true;
			}
			if (Blueprint->ParentClass && Blueprint->ParentClass->FindPropertyByName(VarName))
			{
				return true;
			}
			return false;
		}

		// --- Foreign-property variable references (Batch G) ---------------------
		//
		// Which way a variable node touches its property. The ENGINE validates the two directions
		// with different predicates, so the bridge has to as well — see CheckMemberAccessible.
		enum class EMemberAccess : uint8 { Read, Write };

		/** Prefer the SKELETON class: it is regenerated on every structural change, so a variable
		 *  added moments ago exists there even though GeneratedClass is still stale. Native classes
		 *  have no skeleton and are returned unchanged. */
		UClass* SkeletonPreferred(UClass* Class)
		{
			if (UBlueprint* OwningBP = Class ? Cast<UBlueprint>(Class->ClassGeneratedBy) : nullptr)
			{
				if (OwningBP->SkeletonGeneratedClass)
				{
					return OwningBP->SkeletonGeneratedClass;
				}
			}
			return Class;
		}

		/** Find ANY reflected FProperty on a class — native UPROPERTY or Blueprint variable alike.
		 *  FindPropertyByName walks the PropertyLink chain, which includes every inherited native
		 *  property, so UChildActorComponent::ChildActorClass is reachable exactly like a BP member.
		 *  FName comparison is case-insensitive, so "childactorclass" resolves too. */
		FProperty* FindAnyProperty(UClass* Class, const FString& PropertyName)
		{
			if (!Class || PropertyName.IsEmpty())
			{
				return nullptr;
			}
			const FName MemberName(*PropertyName);
			if (FProperty* Direct = Class->FindPropertyByName(MemberName))
			{
				return Direct;
			}
			// Fall back to the field lookup the editor uses for renamed/redirected variables.
			return FindFProperty<FProperty>(Class, MemberName);
		}

		/** Up to six Blueprint-visible property names on Class whose spelling overlaps Wanted, so
		 *  "property not found" names the near misses instead of only pointing at describe_class.
		 *  When nothing overlaps it reports how many visible properties exist, which distinguishes
		 *  "wrong name" from "wrong class" without a second round-trip. */
		FString NearMissPropertyHint(UClass* Class, const FString& Wanted)
		{
			if (!Class)
			{
				return FString();
			}
			const FString WantedLower = Wanted.ToLower();
			TArray<FString> Hits;
			int32 VisibleCount = 0;
			for (TFieldIterator<FProperty> It(Class, EFieldIteratorFlags::IncludeSuper); It; ++It)
			{
				FProperty* Property = *It;
				if (!Property->HasAnyPropertyFlags(CPF_BlueprintVisible))
				{
					continue;
				}
				++VisibleCount;
				const FString Name = Property->GetName();
				const FString NameLower = Name.ToLower();
				if (Hits.Num() < 6 && (NameLower.Contains(WantedLower) || WantedLower.Contains(NameLower)))
				{
					Hits.Add(Name);
				}
			}
			if (Hits.Num() > 0)
			{
				return FString::Printf(TEXT(" Close matches: %s."), *FString::Join(Hits, TEXT(", ")));
			}
			return FString::Printf(TEXT(" It has %d Blueprint-visible propert%s."), VisibleCount, VisibleCount == 1 ? TEXT("y") : TEXT("ies"));
		}

		/** Name a BlueprintCallable accessor that WOULD work, so a refusal points at the way forward.
		 *  UChildActorComponent::ChildActorClass is the motivating case: BlueprintReadOnly, but
		 *  SetChildActorClass is UFUNCTION(BlueprintCallable) (ChildActorComponent.h:92-95). */
		FString SuggestAccessor(UClass* Class, const FString& PropertyName, EMemberAccess Access)
		{
			if (!Class)
			{
				return FString();
			}
			TArray<FString> Candidates;
			if (Access == EMemberAccess::Write)
			{
				Candidates.Add(TEXT("Set") + PropertyName);
			}
			else
			{
				Candidates.Add(TEXT("Get") + PropertyName);
				Candidates.Add(PropertyName);
			}
			for (const FString& Candidate : Candidates)
			{
				UFunction* Function = Class->FindFunctionByName(FName(*Candidate));
				if (Function && Function->HasAnyFunctionFlags(FUNC_BlueprintCallable | FUNC_BlueprintPure))
				{
					return FString::Printf(
						TEXT(" '%s' IS BlueprintCallable though — add_function_call {class:\"%s\", function:\"%s\"} does what you want."),
						*Candidate, *Class->GetName(), *Candidate);
				}
			}
			return FString();
		}

		// The engine's OWN Blueprint-accessibility gate, applied BEFORE the node is built.
		//
		// The failure it prevents, proven live before this change: add_variable_set pointed at
		// UChildActorComponent::ChildActorClass (EditAnywhere, BlueprintReadOnly — ChildActorComponent.h:116)
		// returned ok:true with a full pin list. Compiling the blueprint then reported
		// "ChildActorComponent.ChildActorClass is not blueprint writable." Worse, that error is
		// DEFERRED: an unwired Set node is pruned as isolated before validation runs, so the blueprint
		// compiles 0 errors until the caller finally wires the exec pin — long after the ok:true that
		// caused it. A bridge response that says ok and produces a node that cannot compile is exactly
		// the "ok:true having done nothing useful" failure the house rules forbid.
		//
		// These are the exact predicates the COMPILER uses, so "accepted here" == "compiles there":
		//   UK2Node_VariableGet::ValidateNodeDuringCompilation -> FBlueprintEditorUtils::IsPropertyReadableInBlueprint
		//       (K2Node_VariableGet.cpp:425-457)
		//   UK2Node_VariableSet::ValidateNodeDuringCompilation -> FBlueprintEditorUtils::IsPropertyWritableInBlueprint
		//       (K2Node_VariableSet.cpp:421-457)
		//   implementations at BlueprintEditorUtils.cpp:8786 (writable) and :8810 (readable)
		//
		// They are deliberately NOT re-implemented as CPF_ flag arithmetic: the Private verdict depends
		// on the MD_Private *metadata* AND on whether the owning class was generated by THIS blueprint,
		// neither of which a flag test can see. Note also that the engine's palette filter
		// (UEdGraphSchema_K2::CanUserKismetAccessVariable, EdGraphSchema_K2.cpp:1228) additionally hides
		// category-hidden properties — that one is NOT used here, because a category-hidden property
		// still compiles fine and refusing it would refuse something that works.
		bool CheckMemberAccessible(UBlueprint* ContextBP, UClass* OwnerScope, const FProperty* Property,
			EMemberAccess Access, FString& OutError)
		{
			if (!Property)
			{
				OutError = TEXT("null property");
				return false;
			}
			const FString PropertyName = Property->GetName();
			const FString ClassName = OwnerScope ? OwnerScope->GetName() : TEXT("?");

			if (Access == EMemberAccess::Read)
			{
				const FBlueprintEditorUtils::EPropertyReadableState State =
					FBlueprintEditorUtils::IsPropertyReadableInBlueprint(ContextBP, Property);
				if (State == FBlueprintEditorUtils::EPropertyReadableState::Readable)
				{
					return true;
				}
				if (State == FBlueprintEditorUtils::EPropertyReadableState::NotBlueprintVisible)
				{
					OutError = FString::Printf(
						TEXT("property '%s' on '%s' is not Blueprint-visible (its UPROPERTY carries neither BlueprintReadOnly nor ")
						TEXT("BlueprintReadWrite), so no graph may read it: a Get node here compiles to the error ")
						TEXT("\"%s.%s is not blueprint visible\".%s"),
						*PropertyName, *ClassName, *ClassName, *PropertyName, *SuggestAccessor(OwnerScope, PropertyName, Access));
				}
				else
				{
					OutError = FString::Printf(
						TEXT("property '%s' on '%s' is Blueprint-private (meta=(BlueprintPrivate)) and '%s' was not generated by ")
						TEXT("this blueprint, so only that class's own graphs may read it: a Get node here compiles to the error ")
						TEXT("\"%s.%s is private and not accessible in this context\".%s"),
						*PropertyName, *ClassName, *ClassName, *ClassName, *PropertyName, *SuggestAccessor(OwnerScope, PropertyName, Access));
				}
				return false;
			}

			const FBlueprintEditorUtils::EPropertyWritableState State =
				FBlueprintEditorUtils::IsPropertyWritableInBlueprint(ContextBP, Property);
			if (State == FBlueprintEditorUtils::EPropertyWritableState::Writable)
			{
				return true;
			}
			if (State == FBlueprintEditorUtils::EPropertyWritableState::BlueprintReadOnly)
			{
				OutError = FString::Printf(
					TEXT("property '%s' on '%s' is BlueprintReadOnly — graphs may READ it but never write it: a Set node here ")
					TEXT("compiles to the error \"%s.%s is not blueprint writable\". Use add_variable_get for the read.%s"),
					*PropertyName, *ClassName, *ClassName, *PropertyName, *SuggestAccessor(OwnerScope, PropertyName, Access));
			}
			else if (State == FBlueprintEditorUtils::EPropertyWritableState::NotBlueprintVisible)
			{
				OutError = FString::Printf(
					TEXT("property '%s' on '%s' is not Blueprint-visible (its UPROPERTY carries neither BlueprintReadOnly nor ")
					TEXT("BlueprintReadWrite), so no graph may write it: a Set node here compiles to the error ")
					TEXT("\"%s.%s is not blueprint writable\".%s"),
					*PropertyName, *ClassName, *ClassName, *PropertyName, *SuggestAccessor(OwnerScope, PropertyName, Access));
			}
			else
			{
				OutError = FString::Printf(
					TEXT("property '%s' on '%s' is Blueprint-private (meta=(BlueprintPrivate)) and '%s' was not generated by this ")
					TEXT("blueprint, so only that class's own graphs may write it: a Set node here compiles to the error ")
					TEXT("\"%s.%s is private and not accessible in this context\".%s"),
					*PropertyName, *ClassName, *ClassName, *ClassName, *PropertyName, *SuggestAccessor(OwnerScope, PropertyName, Access));
			}
			return false;
		}

		// ResolveClass, plus the one retry a C++ class name needs.
		//
		// ResolveClass (MifBridgeCommon.cpp:1230) looks a bare name up with FindFirstObject<UClass>,
		// which matches the UObject name ("ChildActorComponent") — NOT the C++ spelling. So the most
		// natural guess for a native property owner, "UChildActorComponent", returned "class not found"
		// (proven live). Retrying once with the leading U/A stripped costs nothing and cannot change any
		// currently-succeeding resolution, because it only runs after the exact name has already failed.
		// Kept file-local for now; if a second file needs it, promote it into ResolveClass itself rather
		// than copying it (same eviction clause as JIntAny, MifBridgeHandlers.h:53).
		UClass* ResolveClassAllowingCppPrefix(const FString& Name, UBlueprint* ContextBP)
		{
			if (UClass* Direct = ResolveClass(Name, ContextBP))
			{
				return Direct;
			}
			FString Trimmed = Name;
			Trimmed.TrimStartAndEndInline();
			if (Trimmed.Len() > 1 && (Trimmed[0] == TCHAR('U') || Trimmed[0] == TCHAR('A'))
				&& FChar::IsUpper(Trimmed[1]) && !Trimmed.Contains(TEXT("/")) && !Trimmed.Contains(TEXT(".")))
			{
				return ResolveClass(Trimmed.RightChop(1), ContextBP);
			}
			return nullptr;
		}

		// Resolve + fully validate the property a variable node will point at on ANOTHER class — the
		// whole point of the endpoint's targetClass parameter. Deliberately does NOT touch a node, so
		// the caller can run it before any Modify()/NewObject and refuse without dirtying anything.
		//
		// Why an up-front property lookup rather than "set the name and hope":
		// UK2Node_Variable::CreatePinForVariable (K2Node_Variable.cpp:93-140) bails at :133-137 and
		// produces NO pins when FMemberReference::ResolveMember<FProperty> comes back null, which is
		// exactly what happens when the property does not exist on the class handed in. That is how the
		// bridge used to emit an "unresolved, pinless" node: silent, deferred to compile time, and the
		// returned JSON looked plausible.
		bool ResolveExternalMember(const FString& VarName, UClass* TargetClass, UBlueprint* ContextBP,
			EMemberAccess Access, UClass*& OutOwnerScope, FProperty*& OutProperty, FString& OutError)
		{
			if (!TargetClass)
			{
				OutError = TEXT("null target class");
				return false;
			}

			UClass* ResolveAgainst = SkeletonPreferred(TargetClass);
			OutOwnerScope = ResolveAgainst;

			FProperty* Property = FindAnyProperty(ResolveAgainst, VarName);
			if (!Property)
			{
				OutError = FString::Printf(
					TEXT("property '%s' not found on class '%s' — describe_class {className:\"%s\"} lists what it has.%s ")
					TEXT("(Without this check the node would be created unresolved and pinless.)"),
					*VarName, *ResolveAgainst->GetName(), *ResolveAgainst->GetName(),
					*NearMissPropertyHint(ResolveAgainst, VarName));
				return false;
			}
			if (!CheckMemberAccessible(ContextBP, ResolveAgainst, Property, Access, OutError))
			{
				return false;
			}

			OutProperty = Property;
			return true;
		}

		// Point the node at that property.
		//
		// Why SetFromProperty rather than a hand-rolled SetExternalMember + GUID lookup:
		// UK2Node_Variable::SetFromProperty (K2Node_Variable.cpp:87-91) does BOTH halves of the job.
		//   1. FMemberReference::SetFromField<FProperty>(Property, /*bSelfContext*/ false, OwnerClass)
		//      (MemberReference.h:108-142) records the owning class as the member parent, clears
		//      bSelfContext, and looks the Blueprint member GUID up itself — so a later rename of the
		//      property on a target BLUEPRINT does not break the reference, while a NATIVE property
		//      (no GUID) correctly stays a name-only reference.
		//   2. It sets SelfContextInfo = ESelfContextInfo::NotSelfContext, which the hand-rolled
		//      SetExternalMember path left at its default. That field is half of the self-pin decision:
		//      UK2Node_Variable::CreatePinForSelf (K2Node_Variable.cpp:142-213) computes
		//      bSelfTarget = IsSelfContext() && (NotSelfContext != SelfContextInfo) at :151, then at
		//      :200-206 creates the "self" pin (friendly name "Target") and hides it ONLY when
		//      bSelfTarget. Non-self => a VISIBLE Target pin the caller wires the object into, which is
		//      the pin that makes this a foreign-property access at all. The pin's class is normalised
		//      to the property's owning class at :162-165, so passing a derived targetClass still
		//      yields a correctly-typed Target.
		void PointAtExternalMember(UK2Node_Variable* Node, FProperty* Property, UClass* OwnerScope)
		{
			Node->SetFromProperty(Property, /*bSelfContext*/ false, OwnerScope);
		}

		// Shared body for add_variable_get / add_variable_set.
		//
		// These were two near-identical copies differing only in node class, access direction and the
		// word "get"/"set" in a warning. That is precisely how a fix lands on one and not the other —
		// the read-only check below would have been trivial to add to the getter alone and leave the
		// setter emitting nodes that cannot compile. One body, one behaviour.
		//
		// ORDER MATTERS: every refusal happens BEFORE the first Modify()/NewObject, so a rejected call
		// leaves the blueprint untouched instead of recording a transaction for a node that was never
		// placed.
		void DoAddVariableNode(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out, EMemberAccess Access)
		{
			const bool bIsSet = (Access == EMemberAccess::Write);

			if (RejectUnknownParams(In, Out,
				{ TEXT("graphId"),
				  TEXT("var"), TEXT("name"), TEXT("variable"), TEXT("varName"), TEXT("property"), TEXT("propertyName"), TEXT("member"),
				  TEXT("targetClass"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("ownerClass"), TEXT("objectClass"),
				  TEXT("x"), TEXT("y") },
				TEXT("graphId, var (aliases: name, variable, varName, property, propertyName, member), ")
				TEXT("targetClass (aliases: class, cls, className, ownerClass, objectClass), x, y"),
				{ { TEXT("graph"), TEXT("spell it graphId") },
				  { TEXT("target"), TEXT("targetClass names the CLASS that owns the property; the OBJECT is wired into the node's Target pin with connect_pins, never passed here") },
				  { TEXT("value"), TEXT("a Set node takes its value on a pin — place the node, then set_pin_default or connect_pins") },
				  { TEXT("scope"), TEXT("scope is auto-detected: a variable declared on this function graph resolves as a local, anything else as a member") } }))
			{
				return;
			}

			UBlueprint* Blueprint = nullptr;
			UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
			if (!Graph)
			{
				return;
			}

			const FString Var = JStrAny(In, { TEXT("var"), TEXT("name"), TEXT("variable"), TEXT("varName"),
				TEXT("property"), TEXT("propertyName"), TEXT("member") });
			if (Var.IsEmpty())
			{
				Fail(Out, TEXT("var is required (the property name; accepted spellings: var, name, variable, varName, property, propertyName, member)"));
				return;
			}

			const FString TargetClassName = JStrAny(In, { TEXT("targetClass"), TEXT("class"), TEXT("cls"),
				TEXT("className"), TEXT("ownerClass"), TEXT("objectClass") });

			// --- Validate everything BEFORE mutating -------------------------------------------
			// Nothing below this point may Fail() once Modify() has run: a refused call must leave the
			// blueprint (and the undo stack) exactly as it found it.
			UClass* TargetClass = nullptr;
			UClass* OwnerScope = nullptr;
			FProperty* ExternalProperty = nullptr;
			bool bIsLocal = false;
			FGuid LocalGuid;
			if (!TargetClassName.IsEmpty())
			{
				TargetClass = ResolveClassAllowingCppPrefix(TargetClassName, Blueprint);
				if (!TargetClass)
				{
					Fail(Out, FString::Printf(
						TEXT("targetClass not found: '%s' — pass the UObject class name without its C++ prefix ")
						TEXT("(ChildActorComponent, not UChildActorComponent), or the full path for a Blueprint class ")
						TEXT("(/Game/BP/BP_Foo.BP_Foo_C)"), *TargetClassName));
					return;
				}
				FString RefError;
				if (!ResolveExternalMember(Var, TargetClass, Blueprint, Access, OwnerScope, ExternalProperty, RefError))
				{
					Fail(Out, RefError);
					return;
				}
			}
			else
			{
				// Auto-detect scope: a variable DECLARED on this function graph is a LOCAL and must resolve via
				// SetLocalMember (SetSelfMember would search the class for a member of that name → "Could not find
				// a variable named X" and an unresolved node). Anything else is a self member. No scope param needed.
				LocalGuid = FBlueprintEditorUtils::FindLocalVariableGuidByName(Blueprint, Graph, FName(*Var));
				bIsLocal = LocalGuid.IsValid();

				// A self MEMBER can still be an inherited NATIVE property, and those carry real Blueprint
				// accessibility rules — the same BlueprintReadOnly trap as the external path, just reached
				// without a targetClass. Gate it when (and only when) the property actually resolves: a
				// variable added moments ago may not be on the skeleton yet, and that case keeps its warning
				// rather than becoming a hard refusal.
				if (!bIsLocal)
				{
					UClass* SelfClass = Blueprint->SkeletonGeneratedClass;
					if (!SelfClass)
					{
						SelfClass = Blueprint->GeneratedClass;
					}
					if (FProperty* SelfProperty = FindAnyProperty(SelfClass, Var))
					{
						FString AccessError;
						if (!CheckMemberAccessible(Blueprint, SelfClass, SelfProperty, Access, AccessError))
						{
							Fail(Out, AccessError);
							return;
						}
					}
				}
			}

			// --- Mutate ------------------------------------------------------------------------
			Blueprint->Modify();
			Graph->Modify();

			UK2Node_Variable* Node = bIsSet
				? static_cast<UK2Node_Variable*>(NewObject<UK2Node_VariableSet>(Graph))
				: static_cast<UK2Node_Variable*>(NewObject<UK2Node_VariableGet>(Graph));

			if (ExternalProperty)
			{
				PointAtExternalMember(Node, ExternalProperty, OwnerScope);
			}
			else if (bIsLocal)
			{
				Node->VariableReference.SetLocalMember(FName(*Var), Graph->GetName(), LocalGuid);
			}
			else
			{
				Node->VariableReference.SetSelfMember(FName(*Var));
			}
			PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

			MarkStructural(Blueprint);

			// --- Structured, numerically checkable result --------------------------------------
			// scope/targetPin are what a caller tests to confirm this is a FOREIGN property read
			// rather than a self read: an external reference always exposes a visible "self" pin
			// (K2Node_Variable.cpp:200-206) and that pin is what the object reference wires into.
			const bool bExternal = (TargetClass != nullptr);
			Out->SetStringField(TEXT("scope"), bExternal ? TEXT("external") : (bIsLocal ? TEXT("local") : TEXT("self")));
			Out->SetStringField(TEXT("access"), bIsSet ? TEXT("write") : TEXT("read"));
			Out->SetStringField(TEXT("var"), Var);
			Out->SetNumberField(TEXT("pinCount"), Node->Pins.Num());
			if (bExternal && ExternalProperty)
			{
				// The class the reference actually resolved against, plus the two flags a caller most
				// often wants to know without a second describe_class round-trip.
				Out->SetStringField(TEXT("memberClass"), OwnerScope ? OwnerScope->GetPathName() : FString());
				Out->SetStringField(TEXT("memberProperty"), ExternalProperty->GetName());
				Out->SetBoolField(TEXT("native"), ExternalProperty->IsNative());
				Out->SetBoolField(TEXT("blueprintReadOnly"), ExternalProperty->HasAnyPropertyFlags(CPF_BlueprintReadOnly));
			}
			UEdGraphPin* SelfPin = Node->FindPin(UEdGraphSchema_K2::PN_Self, EGPD_Input);
			Out->SetBoolField(TEXT("hasTargetPin"), SelfPin != nullptr && !SelfPin->bHidden);
			if (SelfPin && !SelfPin->bHidden)
			{
				Out->SetStringField(TEXT("targetPin"), SelfPin->PinName.ToString());
			}

			if (!bExternal && !bIsLocal && !BlueprintHasVariable(Blueprint, Var))
			{
				Out->SetStringField(TEXT("warning"), FString::Printf(
					TEXT("variable '%s' not found on this blueprint; the %s node may be unresolved until it exists"),
					*Var, bIsSet ? TEXT("set") : TEXT("get")));
			}
			// A variable node with no pins never resolved. Say so in the response instead of returning a
			// healthy-looking node that only fails at compile time.
			if (Node->Pins.Num() == 0)
			{
				Out->SetStringField(TEXT("warning"), FString::Printf(
					TEXT("%s node for '%s' resolved to NO pins — the variable reference is dead. Check the name/targetClass, then remove_node and retry."),
					bIsSet ? TEXT("set") : TEXT("get"), *Var));
			}
			EmitNode(Out, Node);
		}

		// --- why 'path' is still accepted on connect_pins / reconnect_pin / disconnect_pin -------
		//
		// BACK-COMPAT, and nothing else. Before the strict-params guard landed those three silently
		// dropped 'path', so long-lived caller payloads still carry it; the guard then turned every one
		// of those payloads into "unrecognised parameter 'path'". A guard was added, not a parameter
		// removed, so the fix is to keep 'path' in the accepted list. It is redundant: graphId is
		// "<blueprintPath>::<graphName>" (GraphIdOf, MifBridgeCommon.cpp) and ResolveGraph resolves the
		// blueprint from its left half, so no code in this file reads 'path'.
		//
		// It is ACCEPTED AND IGNORED — plainly, with no cross-check against graphId. An earlier revision
		// failed the call when the two "disagreed", on the theory that a silently-ignored 'path' could
		// point at a different asset than the one actually edited. That check compared raw strings while
		// the resolvers normalise: ResolveBlueprint accepts "/Game/X/BP_Foo" and "/Game/X/BP_Foo.BP_Foo"
		// and follows redirectors, so two spellings of the SAME asset read as a disagreement and a valid
		// call was rejected. That is strictly worse than the bug being fixed — it turns a working payload
		// into a hard failure, which is the exact breakage 'path' was restored to undo.
		//
		// If a caller ever needs 'path' HONOURED instead of ignored, plumb it through ResolveGraph so one
		// normaliser sees both sides. Do not re-add a string-equality gate in front of the resolvers.

		// Shared connect/reconnect body. When bBreakFirst is true both pins are cleared
		// before wiring (the wildcard-reset combo). Reports CanCreateConnection's reason.
		void DoConnect(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out, bool bBreakFirst)
		{
			// The two NODE params keep their distinct names on purpose (docs/02_GOTCHAS.md:18):
			// aliasing across roles would let one key satisfy both ends. The wrong guesses get a
			// KeyNote naming the right key instead. The PIN params are per-role and are aliased.
			// 'path' — back-compat only, accepted and ignored. See the 'path' note above.
			if (RejectUnknownParams(In, Out,
				{ TEXT("srcNode"), TEXT("srcPin"), TEXT("sourcePin"), TEXT("fromPin"),
				  TEXT("dstNode"), TEXT("dstPin"), TEXT("destPin"), TEXT("toPin"),
				  TEXT("graphId"), TEXT("path") },
				TEXT("srcNode, srcPin (aliases: sourcePin, fromPin), dstNode, dstPin (aliases: destPin, toPin), ")
				TEXT("graphId, path (back-compat only — accepted and ignored; graphId already names the blueprint)"),
				{ { TEXT("from"), TEXT("spell it srcNode") },
				  { TEXT("fromNode"), TEXT("spell it srcNode") },
				  { TEXT("sourceNode"), TEXT("spell it srcNode") },
				  { TEXT("to"), TEXT("spell it dstNode") },
				  { TEXT("toNode"), TEXT("spell it dstNode") },
				  { TEXT("destNode"), TEXT("spell it dstNode") },
				  { TEXT("targetNode"), TEXT("spell it dstNode") } }))
			{
				return;
			}

			UEdGraphNode* SrcNode = ResolveNodeField(In, TEXT("srcNode"), Out);
			if (!SrcNode)
			{
				return;
			}
			UEdGraphNode* DstNode = ResolveNodeField(In, TEXT("dstNode"), Out);
			if (!DstNode)
			{
				return;
			}

			const FString SrcPinName = JStrAny(In, { TEXT("srcPin"), TEXT("sourcePin"), TEXT("fromPin") });
			const FString DstPinName = JStrAny(In, { TEXT("dstPin"), TEXT("destPin"), TEXT("toPin") });
			UEdGraphPin* OutPin = FindPin(SrcNode, SrcPinName, EGPD_Output, /*bRequireDir*/ false);
			UEdGraphPin* InPin = FindPin(DstNode, DstPinName, EGPD_Input, /*bRequireDir*/ false);
			if (!OutPin)
			{
				Fail(Out, FString::Printf(TEXT("src pin not found: '%s'"), *SrcPinName));
				return;
			}
			if (!InPin)
			{
				Fail(Out, FString::Printf(TEXT("dst pin not found: '%s'"), *DstPinName));
				return;
			}

			// Tunnel through reroute (knot) chains to the real terminal pins.
			OutPin = SkipKnots(OutPin);
			InPin = SkipKnots(InPin);

			const UEdGraphSchema_K2* Schema = K2();
			UEdGraphNode* OutOwner = OutPin->GetOwningNodeUnchecked();
			UEdGraphNode* InOwner = InPin->GetOwningNodeUnchecked();
			if (!OutOwner || !InOwner)
			{
				Fail(Out, TEXT("resolved pin has no owning node (orphaned knot chain?)"));
				return;
			}
			OutOwner->Modify();
			InOwner->Modify();

			// ASK BEFORE DESTROYING. This test used to run AFTER the bBreakFirst block, so
			// reconnect_pin on a disallowed pair broke both pins' existing wires and THEN returned
			// ok:false — the caller is told the call failed and reasonably assumes nothing changed,
			// while the graph it was rewiring has been taken apart. Same defect the three exec splices
			// had. CanCreateConnection is a pure query, so hoisting it costs nothing.
			const FPinConnectionResponse Response = Schema->CanCreateConnection(OutPin, InPin);
			if (Response.Response == CONNECT_RESPONSE_DISALLOW)
			{
				Fail(Out, FString::Printf(TEXT("%s (nothing was disconnected)"), *Response.Message.ToString()));
				return;
			}

			if (bBreakFirst)
			{
				Schema->BreakPinLinks(*OutPin, true);
				Schema->BreakPinLinks(*InPin, true);
			}

			const bool bConnected = Schema->TryCreateConnection(OutPin, InPin);
			MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(OutOwner));

			Out->SetBoolField(TEXT("connected"), bConnected);
			if (!Response.Message.IsEmpty())
			{
				Out->SetStringField(TEXT("response"), Response.Message.ToString());
			}
			Out->SetObjectField(TEXT("srcPin"), SerializePin(OutPin));
			Out->SetObjectField(TEXT("dstPin"), SerializePin(InPin));
		}
	}

	// --- Node creation ------------------------------------------------------

	void H_add_function_call(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// 'cls' is the documented trap: it was silently ignored, 'class' defaulted to "self", and the
		// caller got "function 'X' not found on class 'SKEL_<TheirOwnBlueprint>_C'" — an error that
		// names the wrong subsystem entirely (proven live before this change). Aliased AND guarded.
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"),
			  TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("targetClass"), TEXT("ownerClass"),
			  TEXT("function"), TEXT("functionName"), TEXT("func"), TEXT("method"),
			  TEXT("asMessage"), TEXT("message"),
			  TEXT("x"), TEXT("y") },
			TEXT("graphId, class (aliases: cls, className, targetClass, ownerClass; default \"self\"), ")
			TEXT("function (aliases: functionName, func, method), asMessage (alias: message), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("target"), TEXT("the target OBJECT is wired into the node's self/Target pin with connect_pins; 'class' names the class that declares the function") },
			  { TEXT("args"), TEXT("arguments are pins — place the node, then set_pin_default or connect_pins") },
			  { TEXT("pure"), TEXT("purity comes from the UFUNCTION itself (BlueprintPure); it is not selectable here") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}

		FString ClassName = JStrAny(In, { TEXT("class"), TEXT("cls"), TEXT("className"),
			TEXT("targetClass"), TEXT("ownerClass") });
		const bool bClassDefaulted = ClassName.IsEmpty();
		if (bClassDefaulted)
		{
			ClassName = TEXT("self");
		}
		const FString FunctionName = JStrAny(In, { TEXT("function"), TEXT("functionName"), TEXT("func"), TEXT("method") });
		if (FunctionName.IsEmpty())
		{
			Fail(Out, TEXT("function is required (accepted spellings: function, functionName, func, method)"));
			return;
		}

		UClass* TargetClass = ResolveClassAllowingCppPrefix(ClassName, Blueprint);
		if (!TargetClass)
		{
			Fail(Out, FString::Printf(
				TEXT("class not found: '%s' — pass the UObject class name without its C++ prefix ")
				TEXT("(KismetSystemLibrary, not UKismetSystemLibrary), or the full path for a Blueprint class ")
				TEXT("(/Game/BP/BP_Foo.BP_Foo_C)"), *ClassName));
			return;
		}
		UFunction* Function = TargetClass->FindFunctionByName(FName(*FunctionName));
		if (!Function)
		{
			// Say WHICH class was searched and, when it was only searched because no class key was
			// given, say that too — otherwise a defaulted self-search reads as "the engine lost my function".
			Fail(Out, FString::Printf(TEXT("function '%s' not found on class '%s'%s"),
				*FunctionName, *TargetClass->GetName(),
				bClassDefaulted
					? TEXT(" — no class key was supplied (class/cls/className/targetClass/ownerClass), so the search defaulted to this blueprint's own class")
					: TEXT("")));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		// Pick the SUBCLASS of UK2Node_CallFunction the engine would pick. Spawning a plain
		// UK2Node_CallFunction for every function is wrong for whole families of nodes:
		//
		//   - Functions tagged MD_ArrayParam (the entire UKismetArrayLibrary: Array_Add, Array_Remove,
		//     Array_Contains, Array_Length, Array_Find, Array_Insert, Array_Append, Array_Sort, ...)
		//     need UK2Node_CallArrayFunction. It is the class that OWNS the wildcard-propagation logic
		//     tying TargetArray's element type to the neighbouring pins. On a plain CallFunction the
		//     wildcards can be forced to a type and will compile 0/0 — then silently revert to wildcard
		//     on save+reload, because nothing re-resolves them on reconstruct. That reversion is the
		//     long-standing "Array_Find won't stay typed, use a ForEachLoop macro instead" gotcha in
		//     the README; it was never an Array_Find quirk, it was this line.
		//   - MD_DataTablePin functions need UK2Node_CallDataTableFunction to retype the row struct.
		//   - Commutative+pure operators need the class that grows extra input pins.
		//
		// Order mirrors UBlueprintFunctionNodeSpawner::Create exactly — the branches are not mutually
		// exclusive in practice, so the sequence is the specification.
		UClass* NodeClass = UK2Node_CallFunction::StaticClass();
		{
			const bool bIsPure = Function->HasAllFunctionFlags(FUNC_BlueprintPure);
			const bool bHasArrayPointerParms       = Function->HasMetaData(FBlueprintMetadata::MD_ArrayParam);
			const bool bIsCommutativeAssociative   = Function->HasMetaData(FBlueprintMetadata::MD_CommutativeAssociativeBinaryOperator);
			const bool bIsMaterialParamCollection  = Function->HasMetaData(FBlueprintMetadata::MD_MaterialParameterCollectionFunction);
			const bool bIsDataTableFunc            = Function->HasMetaData(FBlueprintMetadata::MD_DataTablePin);

			if (bIsCommutativeAssociative && bIsPure)      { NodeClass = UK2Node_CommutativeAssociativeBinaryOperator::StaticClass(); }
			else if (bIsMaterialParamCollection)           { NodeClass = UK2Node_CallMaterialParameterCollectionFunction::StaticClass(); }
			else if (bIsDataTableFunc)                     { NodeClass = UK2Node_CallDataTableFunction::StaticClass(); }
			else if (bHasArrayPointerParms)                { NodeClass = UK2Node_CallArrayFunction::StaticClass(); }
			// UK2Node_PromotableOperator is deliberately NOT selected here: the engine gates it on
			// FTypePromotion state that only exists once the editor's type-promotion registry is
			// primed, and a promotable node spawned outside that path comes up with unresolved
			// wildcard pins. The plain CallFunction it falls back to is correct and stable.
		}

		// Interface functions dispatch through a Message node, which tolerates a null/!Implements
		// target at runtime instead of hard-failing. Only when calling on an EXTERNAL target.
		//
		// An EXPLICIT asMessage:false used to be overridden by the interface auto-path, so there was no
		// way to author a non-Message interface call — and the guard on this endpoint ADVERTISES
		// asMessage as honoured, which is the "accepted but ignored" shape 01_POSTMORTEMS.md says must
		// never ship. JHasAny distinguishes "omitted" from "explicitly false"; an explicit false now
		// suppresses the auto-path, and the response records which way it went (see `message` below).
		const bool bMessageSpecified = JHasAny(In, { TEXT("asMessage"), TEXT("message") });
		const bool bMessageRequested = JBoolAny(In, { TEXT("asMessage"), TEXT("message") }, false);
		const bool bInterfaceOnExternal =
			TargetClass->HasAnyClassFlags(CLASS_Interface) && !ClassName.Equals(TEXT("self"), ESearchCase::IgnoreCase);
		const bool bWantMessage = bMessageSpecified ? bMessageRequested : bInterfaceOnExternal;

		UK2Node_CallFunction* Node = bWantMessage
			? NewObject<UK2Node_CallFunction>(Graph, UK2Node_Message::StaticClass())
			: NewObject<UK2Node_CallFunction>(Graph, NodeClass);
		Node->SetFromFunction(Function); // derives purity, self/target, param pins, containers
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		Out->SetBoolField(TEXT("message"), bWantMessage);
		if (bMessageSpecified && !bMessageRequested && bInterfaceOnExternal)
		{
			// A deliberate non-Message interface call is legal but is NOT what the engine's UI would
			// produce, and it hard-fails at runtime on a target that does not implement the interface.
			Out->SetStringField(TEXT("note"),
				TEXT("asMessage:false was honoured on an interface call against an external target: this is a direct call, "
				     "so it will fail at runtime if the target does not implement the interface (a Message node would "
				     "silently do nothing instead)"));
		}
		// Surface which class was chosen — otherwise "why is my array pin still a wildcard" is
		// invisible from the response.
		Out->SetStringField(TEXT("nodeClass"), Node->GetClass()->GetName());
		EmitNode(Out, Node);
	}

	// --- add_variable_get / add_variable_set --------------------------------
	//   in:  { graphId, var, targetClass?, x?, y? }   (see DoAddVariableNode for the alias set)
	//   out: { node, scope: self|local|external, access: read|write, hasTargetPin, targetPin?,
	//          memberClass?, native?, blueprintReadOnly?, pinCount }
	//
	// With targetClass these read/write a property on ANOTHER object — a spawned actor's variable, or
	// a NATIVE UPROPERTY such as UChildActorComponent::ChildActorClass. The node gets a visible Target
	// pin (K2Node_Variable.cpp:200-206) that the object reference wires into; without targetClass the
	// scope is auto-detected as a graph local or a self member.
	void H_add_variable_get(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		DoAddVariableNode(In, Out, EMemberAccess::Read);
	}

	void H_add_variable_set(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		DoAddVariableNode(In, Out, EMemberAccess::Write);
	}

	void H_add_branch(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("graphId"), TEXT("x"), TEXT("y") }, TEXT("graphId, x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("condition"), TEXT("the Condition input is a pin — place the node, then set_pin_default or connect_pins") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_IfThenElse* Node = NewObject<UK2Node_IfThenElse>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_macro_instance(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"),
			  TEXT("macroGraph"), TEXT("macro"), TEXT("macroName"), TEXT("name"),
			  TEXT("macroPath"), TEXT("macroLibrary"), TEXT("library"), TEXT("path"),
			  TEXT("x"), TEXT("y") },
			TEXT("graphId, macroGraph (aliases: macro, macroName, name), ")
			TEXT("macroPath (aliases: macroLibrary, library, path), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}

		FString MacroPath = JStrAny(In, { TEXT("macroPath"), TEXT("macroLibrary"), TEXT("library"), TEXT("path") });
		if (MacroPath.IsEmpty())
		{
			MacroPath = TEXT("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros");
		}
		const FString MacroName = JStrAny(In, { TEXT("macroGraph"), TEXT("macro"), TEXT("macroName"), TEXT("name") });
		if (MacroName.IsEmpty())
		{
			Fail(Out, TEXT("macroGraph is required (e.g. 'ForEachLoop'; accepted spellings: macroGraph, macro, macroName, name)"));
			return;
		}

		UObject* MacroObject = StaticLoadObject(UBlueprint::StaticClass(), nullptr, *MacroPath, nullptr, LOAD_NoWarn);
		UBlueprint* MacroLibrary = Cast<UBlueprint>(MacroObject);
		if (!MacroLibrary)
		{
			Fail(Out, FString::Printf(TEXT("macro library not found: %s"), *MacroPath));
			return;
		}

		// Match the internal graph name, but forgive the two ways a caller reasonably gets it wrong:
		// case, and the spaces the EDITOR shows. Unreal displays "Switch Has Authority" for a graph
		// named SwitchHasAuthority, and an agent that only ever sees the display name has nothing else
		// to try. An exact-only match rejected both spellings, and the bare "not found" that followed
		// was read as evidence the node was not a macro at all.
		auto Squash = [](const FString& In2)
		{
			FString S = In2;
			S.ReplaceInline(TEXT(" "), TEXT(""));
			S.ReplaceInline(TEXT("_"), TEXT(""));
			return S.ToLower();
		};
		const FString WantSquashed = Squash(MacroName);

		UEdGraph* MacroGraph = nullptr;
		FString MatchedBy;
		for (UEdGraph* Candidate : MacroLibrary->MacroGraphs)
		{
			if (Candidate && Candidate->GetName() == MacroName)
			{
				MacroGraph = Candidate;
				MatchedBy = TEXT("exact");
				break;
			}
		}
		if (!MacroGraph)
		{
			for (UEdGraph* Candidate : MacroLibrary->MacroGraphs)
			{
				if (Candidate && Squash(Candidate->GetName()) == WantSquashed)
				{
					MacroGraph = Candidate;
					MatchedBy = TEXT("normalized (case and spaces ignored)");
					break;
				}
			}
		}
		if (!MacroGraph)
		{
			// Say what IS there. "not found" alone forces the caller to guess again, and a run of
			// failed guesses is what produced a confident, wrong conclusion about the node's type.
			TArray<FString> Names;
			for (UEdGraph* Candidate : MacroLibrary->MacroGraphs)
			{
				if (Candidate) { Names.Add(Candidate->GetName()); }
			}
			Names.Sort();

			TArray<FString> Near;
			for (const FString& Nm : Names)
			{
				const FString Sq = Squash(Nm);
				if (Sq.Contains(WantSquashed) || WantSquashed.Contains(Sq)) { Near.Add(Nm); }
			}

			TArray<TSharedPtr<FJsonValue>> Arr;
			for (const FString& Nm : Names) { Arr.Add(MakeShared<FJsonValueString>(Nm)); }
			Out->SetArrayField(TEXT("availableMacroGraphs"), Arr);
			Out->SetNumberField(TEXT("availableCount"), Names.Num());
			if (Near.Num() > 0)
			{
				TArray<TSharedPtr<FJsonValue>> NearArr;
				for (const FString& Nm : Near) { NearArr.Add(MakeShared<FJsonValueString>(Nm)); }
				Out->SetArrayField(TEXT("didYouMean"), NearArr);
			}
			// SEARCH EVERY OTHER MACRO LIBRARY. This is the reported case, and neither the caller nor
			// I guessed it: "Switch Has Authority" is not in StandardMacros at all - it lives in
			// ActorMacros, and its graph name really does contain spaces. The user's second guess was
			// the RIGHT NAME in the WRONG LIBRARY, and the old error mentioned only the name, so a run
			// of failures read as "this is not a macro" rather than "look somewhere else".
			// macroPath defaults to StandardMacros, so everything else was effectively invisible.
			//
			// ASK THE REGISTRY, do not hardcode. The first version of this listed the three libraries
			// under /Engine/EditorBlueprintResources; an engine-wide search then found a fourth
			// (ArtTools/RenderToTexture/Macros/RenderToTextureMacros) that the list could never reach.
			// It rotted inside the session it was written in. The registry also covers macro libraries
			// the PROJECT defines - which is what a user is most likely to be reaching for.
			//
			// The BlueprintType tag is read straight off FAssetData, so nothing is loaded to decide
			// what is a macro library; only the few that are get loaded, and only on this failure path.
			TArray<TSharedPtr<FJsonValue>> Elsewhere;
			{
				IAssetRegistry& Registry =
					FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
				TArray<FAssetData> BlueprintAssets;
				Registry.GetAssetsByClass(UBlueprint::StaticClass()->GetClassPathName(),
					BlueprintAssets, /*bSearchSubClasses*/ true);

				int32 Searched = 0;
				for (const FAssetData& Data : BlueprintAssets)
				{
					if (Searched > 64) { break; }   // sanity bound; there are only ever a handful
					FString BpType;
					if (!Data.GetTagValue(FBlueprintTags::BlueprintType, BpType)) { continue; }
					if (BpType != TEXT("BPTYPE_MacroLibrary")) { continue; }

					const FString LibPath = Data.GetObjectPathString();
					if (LibPath == MacroPath) { continue; }   // already searched, above

					UBlueprint* OtherLib = Cast<UBlueprint>(
						StaticLoadObject(UBlueprint::StaticClass(), nullptr, *LibPath, nullptr, LOAD_NoWarn));
					if (!OtherLib) { continue; }
					++Searched;

					for (UEdGraph* Candidate : OtherLib->MacroGraphs)
					{
						if (!Candidate) { continue; }
						const FString CName = Candidate->GetName();
						if (CName == MacroName || Squash(CName) == WantSquashed)
						{
							TSharedRef<FJsonObject> Hit = MakeShared<FJsonObject>();
							Hit->SetStringField(TEXT("macroGraph"), CName);
							Hit->SetStringField(TEXT("macroPath"), LibPath);
							Elsewhere.Add(MakeShared<FJsonValueObject>(Hit));
						}
					}
				}
				Out->SetNumberField(TEXT("otherLibrariesSearched"), Searched);
			}
			if (Elsewhere.Num() > 0)
			{
				Out->SetArrayField(TEXT("foundInOtherLibrary"), Elsewhere);
			}

			Out->SetStringField(TEXT("hint"),
				TEXT("macroGraph is the INTERNAL graph name, which differs from the title the editor "
					 "shows (\"Switch Has Authority\" is a graph named SwitchHasAuthority). To copy the "
					 "exact value off an existing node, read it with get_node/list_nodes - a "
					 "K2Node_MacroInstance now reports macro.addMacroInstanceArgs."));
			if (Elsewhere.Num() > 0)
			{
				// Name the exact macroPath to retry with. One more call, not another guess.
				// A macro name can exist in SEVERAL libraries - "Switch Has Authority" is in both
				// ActorMacros and ActorComponentMacros, and they are not interchangeable (one targets
				// an Actor, the other a component). Naming just the first would send the caller to an
				// arbitrary one, so list them all and let them choose.
				TArray<FString> Paths;
				for (const TSharedPtr<FJsonValue>& V : Elsewhere)
				{
					const TSharedPtr<FJsonObject>* Obj = nullptr;
					if (V.IsValid() && V->TryGetObject(Obj) && Obj)
					{
						Paths.Add((*Obj)->GetStringField(TEXT("macroPath")));
					}
				}
				Fail(Out, FString::Printf(
					TEXT("macro graph '%s' is not in %s, but it EXISTS in %d other librar%s: %s. Retry with one of those as macroPath. (macroPath defaults to StandardMacros, so everything else is easy to miss%s.) Every match is listed in foundInOtherLibrary."),
					*MacroName, *MacroPath, Paths.Num(), Paths.Num() == 1 ? TEXT("y") : TEXT("ies"),
					*FString::Join(Paths, TEXT(", ")),
					Paths.Num() > 1 ? TEXT("; these are NOT interchangeable - pick the one matching your target type") : TEXT("")));
				return;
			}
			Fail(Out, FString::Printf(
				TEXT("macro graph '%s' not found in %s (%d macro graphs available%s). This library's graph names are listed in availableMacroGraphs, and the other engine macro libraries were searched too."),
				*MacroName, *MacroPath, Names.Num(),
				Near.Num() ? *FString::Printf(TEXT("; closest: %s"), *FString::Join(Near, TEXT(", "))) : TEXT("")));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		// Spawn fresh + AllocateDefaultPins — never paste. This is the fix for the
		// ForEachLoop wildcard that stayed 'undetermined' via the clipboard path.
		UK2Node_MacroInstance* Node = NewObject<UK2Node_MacroInstance>(Graph);
		Node->SetMacroGraph(MacroGraph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		// SetMacroGraph is void. A macro instance whose graph reference did not take is a node that
		// exists and does nothing - and it would be reported here as successfully created. The
		// reference is what the whole node IS, so check it rather than assume.
		if (!Node->GetMacroGraph())
		{
			Fail(Out, FString::Printf(
				TEXT("the node was created but its macro reference did not take, so it points at no "
					 "macro ('%s' in %s). It is in the graph and does nothing - remove it with "
					 "delete_node."),
				*MacroGraph->GetName(), *MacroPath));
			return;
		}

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
		Out->SetStringField(TEXT("macroGraphResolved"), MacroGraph->GetName());
		Out->SetStringField(TEXT("matchedBy"), MatchedBy);
	}

	void H_add_get_array_item(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("graphId"), TEXT("x"), TEXT("y") }, TEXT("graphId, x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("index"), TEXT("the index is a pin — the response names it as indexPin; use set_pin_default or connect_pins") },
			  { TEXT("array"), TEXT("the array is a pin — the response names it as arrayPin; use connect_pins") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		Blueprint->Modify();
		Graph->Modify();

		UK2Node_GetArrayItem* Node = NewObject<UK2Node_GetArrayItem>(Graph);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);

		// Surface the real (quirky) pin names so callers use array/index/out semantics.
		if (UEdGraphPin* ArrayPin = Node->GetTargetArrayPin())
		{
			Out->SetStringField(TEXT("arrayPin"), ArrayPin->PinName.ToString());
		}
		if (Node->Pins.IsValidIndex(1))
		{
			if (UEdGraphPin* IndexPin = Node->GetIndexPin())
			{
				Out->SetStringField(TEXT("indexPin"), IndexPin->PinName.ToString());
			}
		}
		if (Node->Pins.IsValidIndex(2))
		{
			if (UEdGraphPin* ResultPin = Node->GetResultPin())
			{
				Out->SetStringField(TEXT("outPin"), ResultPin->PinName.ToString());
			}
		}
		EmitNode(Out, Node);
	}

	void H_add_override_event(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"),
			  TEXT("event"), TEXT("eventName"), TEXT("name"), TEXT("function"), TEXT("functionName"),
			  TEXT("interfaceOrParent"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("parentClass"),
			  TEXT("interface"), TEXT("ownerClass"), TEXT("targetClass"),
			  TEXT("callParent"), TEXT("addParentCall"), TEXT("withParentCall"),
			  TEXT("x"), TEXT("y") },
			TEXT("blueprintId (alias: path), event (aliases: eventName, name, function, functionName), ")
			TEXT("interfaceOrParent (aliases: class, cls, className, parentClass, interface, ownerClass, targetClass), ")
			TEXT("callParent (aliases: addParentCall, withParentCall), x, y"),
			{ { TEXT("graphId"), TEXT("an override always lands in the blueprint's event graph — pass blueprintId instead") } }))
		{
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}

		UEdGraph* EventGraph = FBlueprintEditorUtils::FindEventGraph(Blueprint);
		if (!EventGraph && Blueprint->UbergraphPages.Num() > 0)
		{
			EventGraph = Blueprint->UbergraphPages[0];
		}
		if (!EventGraph)
		{
			Fail(Out, TEXT("blueprint has no event graph to host the override"));
			return;
		}

		const FString InterfaceOrParent = JStrAny(In, { TEXT("interfaceOrParent"), TEXT("class"), TEXT("cls"),
			TEXT("className"), TEXT("parentClass"), TEXT("interface"), TEXT("ownerClass"), TEXT("targetClass") });
		const FString EventName = JStrAny(In, { TEXT("event"), TEXT("eventName"), TEXT("name"),
			TEXT("function"), TEXT("functionName") });
		if (EventName.IsEmpty())
		{
			Fail(Out, TEXT("event is required (accepted spellings: event, eventName, name, function, functionName)"));
			return;
		}

		UClass* HostClass = InterfaceOrParent.IsEmpty() ? Blueprint->ParentClass : ResolveClassAllowingCppPrefix(InterfaceOrParent, Blueprint);
		if (!HostClass)
		{
			Fail(Out, FString::Printf(TEXT("interfaceOrParent class not found: '%s'"), *InterfaceOrParent));
			return;
		}
		UFunction* EventFunction = HostClass->FindFunctionByName(FName(*EventName));
		if (!EventFunction)
		{
			Fail(Out, FString::Printf(TEXT("event '%s' not found on '%s'"), *EventName, *HostClass->GetName()));
			return;
		}

		for (UEdGraphNode* Existing : EventGraph->Nodes)
		{
			UK2Node_Event* AsEvent = Cast<UK2Node_Event>(Existing);
			if (AsEvent && AsEvent->EventReference.GetMemberName() == FName(*EventName))
			{
				Fail(Out, FString::Printf(TEXT("event '%s' is already present in the graph"), *EventName));
				return;
			}
		}

		const int32 X = JInt(In, TEXT("x"));
		const int32 Y = JInt(In, TEXT("y"));

		Blueprint->Modify();
		EventGraph->Modify();

		UK2Node_Event* Node = NewObject<UK2Node_Event>(EventGraph);
		Node->EventReference.SetExternalMember(FName(*EventName), HostClass);
		Node->bOverrideFunction = true;
		PlaceAndInit(EventGraph, Node, X, Y);

		MarkStructural(Blueprint);
		EmitNode(Out, Node);

		if (JBoolAny(In, { TEXT("callParent"), TEXT("addParentCall"), TEXT("withParentCall") }, false))
		{
			UK2Node_CallParentFunction* ParentNode = NewObject<UK2Node_CallParentFunction>(EventGraph);
			ParentNode->SetFromFunction(EventFunction);
			PlaceAndInit(EventGraph, ParentNode, X + 320, Y);

			UEdGraphPin* ThenPin = FindPin(Node, TEXT("then"), EGPD_Output, /*bRequireDir*/ true);
			UEdGraphPin* ParentExec = FindPin(ParentNode, TEXT("execute"), EGPD_Input, /*bRequireDir*/ true);
			if (ThenPin && ParentExec)
			{
				K2()->TryCreateConnection(ThenPin, ParentExec);
			}
			MarkStructural(Blueprint);

			Out->SetStringField(TEXT("parentNodeGuid"), ParentNode->NodeGuid.ToString());
			Out->SetObjectField(TEXT("parentNode"), SerializeNode(ParentNode, /*bIncludePins*/ true));
		}
	}

	// Creates a genuine UK2Node_ComponentBoundEvent - the exact node type the Blueprint editor
	// produces from "Add Event > On <X> (<ComponentName>)" in the Components/My Blueprint panel
	// (e.g. ClosePriximity's "On Component Begin Overlap"). This is NOT the same thing
	// add_bind_dispatcher builds: that endpoint creates a generic K2Node_AddDelegate plus a
	// separate CustomEvent, which only works for delegates whose every parameter is a plain
	// value/object - it cannot bind a delegate like OnComponentBeginOverlap whose SweepResult
	// parameter is passed by const-ref, because a hand-built CustomEvent can't be declared with
	// that calling convention. UK2Node_ComponentBoundEvent sidesteps the problem entirely: it
	// derives its pins directly from the delegate's own SignatureFunction (see
	// InitializeComponentBoundEventParams in K2Node_ComponentBoundEvent.cpp), so by-ref struct
	// params are handled correctly with no manual signature reconstruction at all.
	//   in:  { blueprintId (alias: path), component, dispatcher (aliases: delegate, event), x, y }
	//   out: { nodeGuid, node }
	void H_add_component_bound_event(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("component"),
			  TEXT("dispatcher"), TEXT("delegate"), TEXT("event"), TEXT("x"), TEXT("y") },
			TEXT("blueprintId (alias: path), component (the SCS/native component variable name), ")
			TEXT("dispatcher (aliases: delegate, event), x, y"),
			{ { TEXT("targetClass"), TEXT("not needed here - the delegate's owner class is found automatically from the component's own type") },
			  { TEXT("graphId"), TEXT("this always lands in the blueprint's event graph - pass blueprintId instead") } }))
		{
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);
		if (!Blueprint)
		{
			return;
		}

		UEdGraph* EventGraph = FBlueprintEditorUtils::FindEventGraph(Blueprint);
		if (!EventGraph && Blueprint->UbergraphPages.Num() > 0)
		{
			EventGraph = Blueprint->UbergraphPages[0];
		}
		if (!EventGraph)
		{
			Fail(Out, TEXT("blueprint has no event graph to host the bound event"));
			return;
		}

		const FString ComponentName = JStr(In, TEXT("component"));
		if (ComponentName.IsEmpty())
		{
			Fail(Out, TEXT("component is required (the SCS/native component's variable name, e.g. \"ClosePriximity\")"));
			return;
		}
		const FString DispatcherName = JStrAny(In, { TEXT("dispatcher"), TEXT("delegate"), TEXT("event") });
		if (DispatcherName.IsEmpty())
		{
			Fail(Out, TEXT("dispatcher is required (aliases: delegate, event) - the multicast delegate property on the component's class, e.g. \"OnComponentBeginOverlap\""));
			return;
		}

		UClass* SkeletonClass = Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass : Blueprint->GeneratedClass;
		if (!SkeletonClass)
		{
			Fail(Out, TEXT("blueprint has no generated/skeleton class yet - compile it at least once first"));
			return;
		}

		FObjectProperty* ComponentProp = FindFProperty<FObjectProperty>(SkeletonClass, FName(*ComponentName));
		if (!ComponentProp)
		{
			Fail(Out, FString::Printf(
				TEXT("component '%s' not found as a property on '%s' - it must be an SCS component on this blueprint or an inherited native component exposed as a UPROPERTY (check list_components)"),
				*ComponentName, *SkeletonClass->GetName()));
			return;
		}
		UClass* ComponentClass = ComponentProp->PropertyClass;
		if (!ComponentClass)
		{
			Fail(Out, FString::Printf(TEXT("'%s' is not an object-reference property"), *ComponentName));
			return;
		}

		FMulticastDelegateProperty* DelegateProp = FindFProperty<FMulticastDelegateProperty>(ComponentClass, FName(*DispatcherName));
		if (!DelegateProp)
		{
			Fail(Out, FString::Printf(
				TEXT("dispatcher '%s' not found on '%s' (the class of component '%s') - check describe_class's dispatchers list for that component's type"),
				*DispatcherName, *ComponentClass->GetName(), *ComponentName));
			return;
		}

		// Refuse a duplicate binding of the same component+delegate pair, mirroring the
		// "already present" guard in H_add_override_event.
		for (UEdGraphNode* Existing : EventGraph->Nodes)
		{
			if (UK2Node_ComponentBoundEvent* AsBound = Cast<UK2Node_ComponentBoundEvent>(Existing))
			{
				if (AsBound->ComponentPropertyName == ComponentProp->GetFName() && AsBound->DelegatePropertyName == DelegateProp->GetFName())
				{
					Fail(Out, FString::Printf(TEXT("a bound event for %s's %s already exists in this graph"), *ComponentName, *DispatcherName));
					return;
				}
			}
		}

		Blueprint->Modify();
		EventGraph->Modify();

		UK2Node_ComponentBoundEvent* Node = NewObject<UK2Node_ComponentBoundEvent>(EventGraph);
		// Sets ComponentPropertyName/DelegatePropertyName/DelegateOwnerClass/EventReference/
		// CustomFunctionName - MUST run before PlaceAndInit's AllocateDefaultPins call below, since
		// pin generation reads EventReference (set here from the delegate's own SignatureFunction).
		Node->InitializeComponentBoundEventParams(ComponentProp, DelegateProp);
		PlaceAndInit(EventGraph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
		UE_LOG(LogMifBridge, Log, TEXT("add_component_bound_event: %s.%s on %s"), *ComponentName, *DispatcherName, *Blueprint->GetPathName());
	}

	void H_add_parent_call(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"),
			  TEXT("parentClass"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("parent"), TEXT("ownerClass"), TEXT("targetClass"),
			  TEXT("function"), TEXT("functionName"), TEXT("func"), TEXT("method"), TEXT("name"),
			  TEXT("x"), TEXT("y") },
			TEXT("graphId, parentClass (aliases: class, cls, className, parent, ownerClass, targetClass; ")
			TEXT("default = this blueprint's parent), function (aliases: functionName, func, method, name), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}

		const FString ParentName = JStrAny(In, { TEXT("parentClass"), TEXT("class"), TEXT("cls"),
			TEXT("className"), TEXT("parent"), TEXT("ownerClass"), TEXT("targetClass") });
		const FString FunctionName = JStrAny(In, { TEXT("function"), TEXT("functionName"), TEXT("func"),
			TEXT("method"), TEXT("name") });
		if (FunctionName.IsEmpty())
		{
			Fail(Out, TEXT("function is required (accepted spellings: function, functionName, func, method, name)"));
			return;
		}

		UClass* ParentClass = ParentName.IsEmpty() ? Blueprint->ParentClass : ResolveClassAllowingCppPrefix(ParentName, Blueprint);
		if (!ParentClass)
		{
			Fail(Out, FString::Printf(TEXT("parent class not found: '%s'"), *ParentName));
			return;
		}
		UFunction* Function = ParentClass->FindFunctionByName(FName(*FunctionName));
		if (!Function)
		{
			Fail(Out, FString::Printf(TEXT("function '%s' not found on parent '%s'"), *FunctionName, *ParentClass->GetName()));
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_CallParentFunction* Node = NewObject<UK2Node_CallParentFunction>(Graph);
		Node->SetFromFunction(Function);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
	}

	void H_add_cast(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"),
			  TEXT("targetClass"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("castTo"), TEXT("to"), TEXT("targetType"),
			  TEXT("pure"), TEXT("x"), TEXT("y") },
			TEXT("graphId, targetClass (aliases: class, cls, className, castTo, to, targetType), pure? (default false), x, y"),
			{ { TEXT("graph"), TEXT("spell it graphId") },
			  { TEXT("object"), TEXT("the object to cast is a pin — place the node, then connect_pins into its Object pin") } }))
		{
			return;
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = ResolveGraphField(In, Out, Blueprint);
		if (!Graph)
		{
			return;
		}
		// STRICT: an empty/absent class must not fall through to ResolveClass's "self" behaviour.
		// It used to, so passing the wrong key (class / to / castTo / targetType instead of
		// targetClass) produced a cast of the blueprint to ITSELF — which always succeeds, compiles
		// clean, and is nearly invisible. Accept the common spellings; refuse the empty case.
		UClass* TargetClass = ResolveClassStrictField(
			In, { TEXT("targetClass"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("castTo"), TEXT("to"), TEXT("targetType") },
			Blueprint, Out);
		if (!TargetClass)
		{
			return;
		}

		Blueprint->Modify();
		Graph->Modify();

		UK2Node_DynamicCast* Node = NewObject<UK2Node_DynamicCast>(Graph);
		Node->TargetType = TargetClass;
		// pure:false (default) exposes exec + Cast Failed pins; pure:true is a data-only cast.
		// This MUST run before PlaceAndInit, because AllocateDefaultPins reads bIsPureCast to decide
		// whether to create the exec pins at all — and once they exist they are engine-allocated and
		// cannot be removed afterwards (remove_pin refuses them by design).
		//
		// The absence of this option cost a real repair: replacing a graph's PURE casts with add_cast's
		// impure ones left five nodes with unwired exec pins, which the compiler purges as
		// "not connected to the execution chain" — taking every downstream variable node's Target with
		// them and producing 13 errors whose text never mentions purity.
		const bool bPure = JBool(In, TEXT("pure"), false);
		Node->SetPurity(bPure);
		PlaceAndInit(Graph, Node, JInt(In, TEXT("x")), JInt(In, TEXT("y")));

		MarkStructural(Blueprint);
		EmitNode(Out, Node);
		Out->SetBoolField(TEXT("pure"), Node->IsNodePure());
	}

	// --- set_cast_purity --------------------------------------------------------
	//   in:  { graphId?, node (aliases: nodeGuid/guid/nodeId), pure }
	//   out: { node, pure, execPinsBefore, execPinsAfter, changed }
	//
	// Converts an EXISTING cast between pure and impure. UK2Node_DynamicCast::SetPurity is the only
	// correct route: the exec pins are engine-allocated by AllocateDefaultPins, so they can neither be
	// removed (remove_pin refuses) nor added by hand, and writing bIsPureCast with set_property changes
	// the flag WITHOUT reallocating the pins — leaving a node whose flag and pins disagree.
	void H_set_cast_purity(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("graphId"), TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("pure") },
			TEXT("graphId?, node (aliases: nodeGuid, guid, nodeId), pure"),
			{ { TEXT("bIsPureCast"), TEXT("pass pure:true|false — writing bIsPureCast directly with set_property changes the flag but does NOT reallocate the exec pins") },
			  { TEXT("impure"), TEXT("spell it pure:false") } }))
		{
			return;
		}
		if (!In->HasField(TEXT("pure")))
		{
			Fail(Out, TEXT("pure is required (true = data-only cast, false = exec + Cast Failed pins)"));
			return;
		}
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node) { return; }

		UK2Node_DynamicCast* CastNode = Cast<UK2Node_DynamicCast>(Node);
		if (!CastNode)
		{
			Fail(Out, FString::Printf(TEXT("node is a %s, not a cast node — there is no purity to set"), *Node->GetClass()->GetName()));
			return;
		}

		auto CountExecPins = [](UK2Node_DynamicCast* N)
		{
			int32 Count = 0;
			for (UEdGraphPin* P : N->Pins)
			{
				if (P && P->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec) { ++Count; }
			}
			return Count;
		};

		const bool bPure = JBool(In, TEXT("pure"), false);
		const int32 Before = CountExecPins(CastNode);

		Node->Modify();
		if (UEdGraph* Graph = Node->GetGraph()) { Graph->Modify(); }

		// SetPurity no-ops when the flag already matches, which is exactly the stuck state produced by
		// a prior set_property write. Reconstruct unconditionally so the pins are rebuilt from the flag
		// either way, rather than trusting a call that may legitimately do nothing.
		CastNode->SetPurity(bPure);
		CastNode->ReconstructNode();

		if (UBlueprint* OwningBP = FBlueprintEditorUtils::FindBlueprintForNode(Node))
		{
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(OwningBP);
		}

		// READ BACK: a pure cast must have ZERO exec pins. Anything else means the reallocation did
		// not happen, and reporting success would hide a node whose flag and pins disagree.
		const int32 After = CountExecPins(CastNode);
		const bool bWantZero = bPure;
		if (bWantZero && After != 0)
		{
			Fail(Out, FString::Printf(
				TEXT("purity was set to pure but the node still has %d exec pin(s) — the reallocation did not take. ")
				TEXT("The node's flag and pins now disagree; do not rely on it."), After));
			return;
		}
		if (!bWantZero && After == 0)
		{
			Fail(Out, TEXT("purity was set to impure but the node has no exec pins — the reallocation did not take."));
			return;
		}

		Out->SetStringField(TEXT("node"), Node->NodeGuid.ToString(EGuidFormats::Digits));
		Out->SetBoolField(TEXT("pure"), CastNode->IsNodePure());
		Out->SetNumberField(TEXT("execPinsBefore"), Before);
		Out->SetNumberField(TEXT("execPinsAfter"), After);
		Out->SetBoolField(TEXT("changed"), Before != After);
	}

	void H_move_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("x"), TEXT("y") },
			TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), x, y")))
		{
			return;
		}

		UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
		if (!Node)
		{
			return;
		}
		if (UEdGraph* Graph = Cast<UEdGraph>(Node->GetOuter()))
		{
			Graph->Modify();
		}
		Node->Modify();
		Node->NodePosX = JInt(In, TEXT("x"), Node->NodePosX);
		Node->NodePosY = JInt(In, TEXT("y"), Node->NodePosY);
		Out->SetObjectField(TEXT("node"), SerializeNode(Node, /*bIncludePins*/ false));
	}

	void H_remove_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("confirm") },
			TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), confirm (required, must be true)")))
		{
			return;
		}

		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_node requires confirm=true"));
			return;
		}
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
		if (!Node)
		{
			return;
		}
		const FString Guid = Node->NodeGuid.ToString();
		UBlueprint* Blueprint = FBlueprintEditorUtils::FindBlueprintForNode(Node);
		if (Blueprint)
		{
			Blueprint->Modify();
			FBlueprintEditorUtils::RemoveNode(Blueprint, Node, /*bDontRecompile*/ true);
			MarkStructural(Blueprint);
		}
		else if (UEdGraph* Graph = Cast<UEdGraph>(Node->GetOuter()))
		{
			Graph->Modify();
			Graph->RemoveNode(Node);
		}
		Out->SetStringField(TEXT("removed"), Guid);
	}

	// --- add_pin ------------------------------------------------------------
	//   in:  { graphId? | blueprintId+function? | nodeGuid? ,
	//          name, type, container?, direction?: "input"|"output", default?, confirm? }
	//   out: { pin, direction, nodeGuid, kind, createdResultNode?, siblingResultNodesUpdated }
	//
	// Adds a parameter to an EXISTING function or custom event. Without this, a signature was frozen
	// at creation: adding one input meant remove_function + create_function (body destroyed) or
	// remove_node + add_custom_event (every wire destroyed).
	//
	// The direction inversion is the thing to get right. A function's INPUTS live on the ENTRY node
	// as EGPD_Output (the entry emits arguments into the graph); its OUTPUTS live on the RESULT node
	// as EGPD_Input (the return consumes them). A custom event has inputs only, on the event node as
	// EGPD_Output. Callers say "input"/"output" in function terms; this maps it.
	//
	// Mirrors FBlueprintGraphActionDetails::OnAddNewInputClicked / OnAddNewOutputClicked.
	void H_add_pin(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("name"), TEXT("pin"), TEXT("pinName"),
			  TEXT("type"), TEXT("pinType"), TEXT("container"), TEXT("valueType"),
			  TEXT("direction"), TEXT("dir"),
			  TEXT("default"), TEXT("defaultValue"), TEXT("value"),
			  TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"),
			  TEXT("graphId"), TEXT("blueprintId"), TEXT("path"), TEXT("function"), TEXT("functionName") },
			TEXT("name (aliases: pin, pinName), type (alias: pinType), container, valueType, ")
			TEXT("direction (alias: dir; input|output), default (aliases: defaultValue, value), ")
			TEXT("and ONE target: nodeGuid (aliases: node, guid, nodeId) | graphId | blueprintId + function"),
			{ { TEXT("confirm"), TEXT("add_pin is additive and needs no confirm; remove_pin is the one that does") } }))
		{
			return;
		}

		const FString RawName = JStrAny(In, { TEXT("name"), TEXT("pin"), TEXT("pinName") });
		FString PinName = RawName;
		PinName.TrimStartAndEndInline();
		if (!IsValidIdentifier(PinName))
		{
			Fail(Out, FString::Printf(TEXT("invalid pin name '%s' (must match ^[A-Za-z_][A-Za-z0-9_]*$)"), *RawName));
			return;
		}

		FEdGraphPinType PinType;
		FString TypeError;
		if (!MakePinType(JStrAny(In, { TEXT("type"), TEXT("pinType") }), JStr(In, TEXT("container")), PinType, TypeError, JStr(In, TEXT("valueType"))))
		{
			Fail(Out, TypeError);
			return;
		}

		FString DirStr = JStrAny(In, { TEXT("direction"), TEXT("dir") }).ToLower();
		if (DirStr.IsEmpty())
		{
			DirStr = TEXT("input");
		}
		const bool bWantOutput = DirStr.StartsWith(TEXT("out"));
		if (!bWantOutput && !DirStr.StartsWith(TEXT("in")))
		{
			Fail(Out, FString::Printf(TEXT("unknown direction '%s' (expected: input | output)"), *DirStr));
			return;
		}

		// --- Resolve the target -------------------------------------------------------
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		UK2Node_EditablePinBase* EntryLike = nullptr;   // function entry OR custom event
		FString Kind;

		const FString NodeGuid = JStrAny(In, { TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId") });
		if (!NodeGuid.IsEmpty())
		{
			UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
			if (!Node)
			{
				return;
			}
			if (!Node->IsA<UK2Node_CustomEvent>() && !Node->IsA<UK2Node_FunctionEntry>())
			{
				Fail(Out, FString::Printf(
					TEXT("node %s is a %s — add_pin targets a Custom Event or a function ENTRY node ")
					TEXT("(for a function graph pass graphId or blueprintId+function instead)"),
					*NodeGuid, *Node->GetClass()->GetName()));
				return;
			}
			EntryLike = CastChecked<UK2Node_EditablePinBase>(Node);
			Blueprint = FBlueprintEditorUtils::FindBlueprintForNode(Node);
			Graph = Cast<UEdGraph>(Node->GetOuter());
			Kind = Node->IsA<UK2Node_CustomEvent>() ? TEXT("customEvent") : TEXT("function");
		}
		else
		{
			const FString GraphId = JStr(In, TEXT("graphId"));
			if (!GraphId.IsEmpty())
			{
				Graph = ResolveGraphField(In, Out, Blueprint);
				if (!Graph) { return; }
			}
			else
			{
				Blueprint = ResolveBlueprintField(In, Out);
				if (!Blueprint) { return; }
				const FString FunctionName = JStrAny(In, { TEXT("function"), TEXT("functionName") });
				if (FunctionName.IsEmpty())
				{
					Fail(Out, TEXT("supply one of: nodeGuid (a custom event), graphId, or blueprintId + function"));
					return;
				}
				for (UEdGraph* G : Blueprint->FunctionGraphs)
				{
					if (G && G->GetName() == FunctionName) { Graph = G; break; }
				}
				if (!Graph)
				{
					Fail(Out, FString::Printf(TEXT("function graph '%s' not found in %s"), *FunctionName, *Blueprint->GetName()));
					return;
				}
			}
			TArray<UK2Node_FunctionEntry*> Entries;
			Graph->GetNodesOfClass(Entries);
			if (Entries.Num() == 0)
			{
				Fail(Out, FString::Printf(TEXT("graph '%s' has no function entry node — address a custom event by nodeGuid instead"), *Graph->GetName()));
				return;
			}
			EntryLike = Entries[0];
			Kind = TEXT("function");
		}

		if (!Blueprint || !Graph)
		{
			Fail(Out, TEXT("could not resolve the owning blueprint/graph"));
			return;
		}

		const bool bIsCustomEvent = EntryLike->IsA<UK2Node_CustomEvent>();
		if (bWantOutput && bIsCustomEvent)
		{
			Fail(Out, TEXT("a custom event has no outputs — events are fire-and-forget. Use a function if you need a return value."));
			return;
		}

		// The node itself decides whether this type/direction is legal (exec pins on a node that
		// can't modify execution wires, container restrictions, ...). Ask before mutating.
		const EEdGraphPinDirection Desired = bWantOutput ? EGPD_Input : EGPD_Output;
		{
			FText PinError;
			UK2Node_EditablePinBase* Validator = EntryLike;
			EEdGraphPinDirection ValidateDir = Desired;
			if (bWantOutput)
			{
				TArray<UK2Node_FunctionResult*> Results;
				Graph->GetNodesOfClass(Results);
				if (Results.Num() > 0)
				{
					Validator = Results[0];
				}
				else
				{
					// NO RETURN NODE YET. One is created further down — that is the documented
					// behaviour for adding an output to a function that has none. But this preflight
					// then asked the ENTRY node whether it would accept an EGPD_Input, and
					// UK2Node_FunctionEntry refuses any input outright ("Cannot add input pins to
					// function entry node!"). So add_pin direction=output could NEVER succeed on a
					// fresh function: it failed here, before reaching the code that would have made
					// the Return node. The error even named the wrong direction, because it was
					// answering a question about a node we were not going to put the pin on.
					//
					// A CDO is not a stand-in: UK2Node_FunctionTerminator's check consults
					// IsEditable() and CanModifyExecutionWires(), which are instance state.
					//
					// Both terminators share every check except a single direction rule, so asking
					// the entry about the direction IT accepts answers the identical type / exec /
					// editable question. The direction rule is satisfied by construction — the pin
					// goes onto a Result node as an input.
					ValidateDir = EGPD_Output;
				}
			}
			if (!Validator->CanCreateUserDefinedPin(PinType, ValidateDir, PinError))
			{
				Fail(Out, FString::Printf(TEXT("cannot add that pin: %s"), *PinError.ToString()));
				return;
			}
		}

		Blueprint->Modify();
		Graph->Modify();

		int32 SiblingsUpdated = 0;
		bool bCreatedResultNode = false;
		UEdGraphPin* NewPin = nullptr;
		// The node that owns the pin we will report/default. Held instead of a pin pointer because a
		// node survives ReconstructNode() and a pin does not.
		UK2Node_EditablePinBase* PinHost = nullptr;
		FName FinalName(*PinName);

		// Reconstruct with orphan-pin saving off, then let the schema propagate the signature change —
		// exactly what OnParamsChanged does. Skipping HandleParameterDefaultValueChanged leaves callers
		// of the function stale.
		// ReconstructNode() DESTROYS AND REALLOCATES EVERY UEdGraphPin ON THE NODE. Any UEdGraphPin*
		// captured before this call is dangling the instant it returns, and touching one is an
		// access violation, not a recoverable error - MifBridge 0.4.0 shipped exactly that crash:
		// add_pin captured the new pin, called FinishNode, then read the pin to apply `default`, and
		// took the editor out with EXCEPTION_ACCESS_VIOLATION reading 0xffffffffffffffff.
		//
		// So this lambda takes the caller's pin pointer BY REFERENCE and nulls it. A stale pointer can
		// then only be re-acquired deliberately (by name, below) - it cannot be used by accident,
		// and the compiler no longer lets a future edit forget.
		auto FinishNode = [](UK2Node_EditablePinBase* Node, UEdGraphPin*& InOutPinToInvalidate)
		{
			const bool bPrev = Node->bDisableOrphanPinSaving;
			Node->bDisableOrphanPinSaving = true;
			Node->ReconstructNode();
			Node->bDisableOrphanPinSaving = bPrev;
			K2()->HandleParameterDefaultValueChanged(Node);
			InOutPinToInvalidate = nullptr;   // see above: it is freed memory now
		};

		// Re-acquire a user-defined pin by NAME after a reconstruct. Name survives the round trip;
		// the pointer does not.
		auto ReacquirePin = [](UK2Node_EditablePinBase* Node, const FName& Name) -> UEdGraphPin*
		{
			for (UEdGraphPin* P : Node->Pins)
			{
				if (P && P->PinName == Name) { return P; }
			}
			return nullptr;
		};

		if (!bWantOutput)
		{
			EntryLike->Modify();
			NewPin = EntryLike->CreateUserDefinedPin(FinalName, PinType, EGPD_Output, /*bUseUniqueName*/ true);
			if (!NewPin)
			{
				Fail(Out, FString::Printf(TEXT("CreateUserDefinedPin failed for '%s'"), *PinName));
				return;
			}
			FinalName = NewPin->PinName;   // read BEFORE the reconstruct - the name is what survives
			FinishNode(EntryLike, NewPin);            // NewPin is null after this, deliberately
			NewPin = ReacquirePin(EntryLike, FinalName);
		}
		else
		{
			// Outputs live on the Result node(s). A void function has none — mint one, wired from the
			// entry's exec, or the Return is unreachable and the out-param is never written.
			TArray<UK2Node_FunctionResult*> Results;
			Graph->GetNodesOfClass(Results);
			if (Results.Num() == 0)
			{
				UK2Node_FunctionResult* Result = NewObject<UK2Node_FunctionResult>(Graph);
				PlaceAndInit(Graph, Result, EntryLike->NodePosX + 800, EntryLike->NodePosY);
				UEdGraphPin* EntryThen = FindPin(EntryLike, TEXT("then"), EGPD_Output, /*bRequireDir*/ true);
				UEdGraphPin* ResultExec = FindPin(Result, TEXT("execute"), EGPD_Input, /*bRequireDir*/ true);
				if (EntryThen && ResultExec && ResultExec->LinkedTo.Num() == 0)
				{
					K2()->TryCreateConnection(EntryThen, ResultExec);
				}
				bCreatedResultNode = true;
				Graph->GetNodesOfClass(Results);
			}
			if (Results.Num() == 0)
			{
				// Batch M, option (c): a cancelled transaction discards the undo entry rather than
				// rolling a node creation back (PM-007), so say what may be sitting in the graph.
				Fail(Out, TEXT("could not create a function Result node for the new output. WHAT MAY BE LEFT BEHIND: a bare UK2Node_FunctionResult may already have been placed in this graph and is NOT removed by this failure - check with list_nodes and remove it with delete_node."));
				return;
			}

			// Uniquify ONCE against the primary, then apply that exact name to every sibling —
			// letting each uniquify independently would give the same parameter different names on
			// different Return nodes, which does not compile.
			FinalName = Results[0]->CreateUniquePinName(FName(*PinName));
			PinHost = Results[0];
			for (UK2Node_FunctionResult* Result : Results)
			{
				Result->Modify();
				UEdGraphPin* Pin = Result->CreateUserDefinedPin(FinalName, PinType, EGPD_Input, /*bUseUniqueName*/ false);
				if (!Pin)
				{
					// Batch M, option (c). Partial state is possible here and is NOT rolled back: a
					// cancelled transaction discards the undo entry, it does not undo the pins already
					// created on earlier Return nodes (PM-007). Unwinding would mean deleting user pins
					// that may already be wired, which is remove_pin's confirm-gated job, not this
					// handler's.
					Fail(Out, FString::Printf(
						TEXT("CreateUserDefinedPin failed for '%s' on a Return node (%d of %d sibling Return nodes had already been given the pin). WHAT IS LEFT BEHIND: those %d pins, and a Result node if this call created one. They are NOT removed - use remove_pin {confirm:true} on '%s' to undo them."),
						*FinalName.ToString(), SiblingsUpdated, Results.Num(), SiblingsUpdated, *FinalName.ToString()));
					return;
				}
				// Do NOT keep `Pin` across the reconstruct: it is freed by it. PinHost (Results[0])
				// was captured above, and the pin is re-acquired from it by name after the loop.
				FinishNode(Result, Pin);
				++SiblingsUpdated;
			}
		}

		// Optional default for the new pin (inputs only — a return value has no literal default).
		// The schema silently refuses a literal that does not parse for the pin type, and add_pin's
		// out: block never mentioned the default at all, so a rejected default was invisible in both
		// directions. Report it; do not fail the whole call, because the PIN was created successfully
		// and failing here would report failure over a pin that stays (a cancelled transaction
		// discards the undo entry, it does not roll the pin back — PM-007).
		const FString Default = JStrAny(In, { TEXT("default"), TEXT("defaultValue"), TEXT("value") });

		// Last line of defence. If anything above left NewPin null (or a future edit reintroduces a
		// reconstruct between here and the creation), re-acquire by name rather than dereferencing
		// whatever the pointer happens to hold.
		if (NewPin == nullptr && PinHost != nullptr && !FinalName.IsNone())
		{
			for (UEdGraphPin* P : PinHost->Pins)
			{
				if (P && P->PinName == FinalName) { NewPin = P; break; }
			}
		}
		if (!Default.IsEmpty() && !bWantOutput && NewPin == nullptr)
		{
			// The pin was created - only the handle to it was lost. Say that, instead of crashing or
			// silently dropping the default.
			Out->SetStringField(TEXT("defaultError"), FString::Printf(
				TEXT("pin '%s' was created but could not be re-acquired after the node reconstruct, so the default was not applied. The pin exists - set it with set_pin_default."),
				*FinalName.ToString()));
		}
		if (!Default.IsEmpty() && NewPin && !bWantOutput)
		{
			FString DefaultBefore, DefaultAfter, DefaultError;
			bool bDefaultChanged = false;
			const bool bDefaultOk = SetPinDefaultChecked(NewPin, Default, DefaultBefore, DefaultAfter, bDefaultChanged, DefaultError);
			Out->SetStringField(TEXT("defaultAfter"), DefaultAfter);
			Out->SetBoolField(TEXT("defaultApplied"), bDefaultOk && bDefaultChanged);
			if (!bDefaultOk)
			{
				Out->SetStringField(TEXT("defaultError"), DefaultError);
			}
		}
		else if (!Default.IsEmpty() && bWantOutput)
		{
			Out->SetStringField(TEXT("defaultError"),
				TEXT("a default was supplied for an OUTPUT pin and was ignored — only input pins carry a literal default"));
		}

		MarkStructural(Blueprint);

		Out->SetStringField(TEXT("pin"), FinalName.ToString());
		Out->SetStringField(TEXT("direction"), bWantOutput ? TEXT("output") : TEXT("input"));
		Out->SetStringField(TEXT("kind"), Kind);
		Out->SetStringField(TEXT("nodeGuid"), EntryLike->NodeGuid.ToString());
		Out->SetObjectField(TEXT("type"), SerializePinType(PinType));
		Out->SetNumberField(TEXT("resultNodesUpdated"), SiblingsUpdated);
		if (bCreatedResultNode) { Out->SetBoolField(TEXT("createdResultNode"), true); }
		if (FinalName.ToString() != PinName)
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("'%s' was already taken; the pin was named '%s'"), *PinName, *FinalName.ToString()));
		}
		UE_LOG(LogMifBridge, Log, TEXT("add_pin: %s %s on %s"), *FinalName.ToString(),
			bWantOutput ? TEXT("(out)") : TEXT("(in)"), *Graph->GetName());
	}

	// --- remove_pin ---------------------------------------------------------
	//   in:  { node|nodeGuid, pin, graphId?, direction?: "input"|"output", confirm: true }
	//   out: { removed, pin, kind: "userDefined"|"duplicate", node }
	//
	// Two jobs:
	//  1. Delete a user-defined pin (function input/output, custom-event param, tunnel pin) — the
	//     Details-panel X button. UK2Node_EditablePinBase::RemoveUserDefinedPinByName drops both the
	//     live UEdGraphPin and its FUserPinInfo record; skipping the record would leave the node
	//     "out-of-date" at compile because reconstruct re-derives pins FROM that record.
	//  2. Delete a DUPLICATE pin — two pins sharing a name+direction where only one can be real.
	//     This is the escape hatch for assets already carrying the spurious second "execute" pin that
	//     create_function used to mint (see PlaceAndInit in MifBridgeCommon.cpp). We keep whichever
	//     copy is wired and drop an unwired twin, so removing it can never break existing exec flow.
	//
	// A pin that is neither user-defined nor duplicated is REFUSED: engine-allocated pins are
	// re-created by AllocateDefaultPins on the next reconstruct, so "removing" one is a lie that
	// silently reverts. Say that instead of pretending.
	void H_remove_pin(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"),
			  TEXT("pin"), TEXT("pinName"), TEXT("name"), TEXT("direction"), TEXT("dir"), TEXT("confirm") },
			TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), ")
			TEXT("direction (alias: dir; input|output), confirm (required, must be true)")))
		{
			return;
		}

		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_pin requires confirm=true"));
			return;
		}
		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node)
		{
			return;
		}
		const FString PinName = JStrAny(In, { TEXT("pin"), TEXT("pinName"), TEXT("name") });
		if (PinName.IsEmpty())
		{
			Fail(Out, TEXT("pin is required (the pin name to remove)"));
			return;
		}

		// Optional direction filter — needed when a node has same-named pins on both sides.
		const FString DirStr = JStrAny(In, { TEXT("direction"), TEXT("dir") });
		const bool bHasDir = !DirStr.IsEmpty();
		const EEdGraphPinDirection WantDir = DirStr.StartsWith(TEXT("out")) ? EGPD_Output : EGPD_Input;

		TArray<UEdGraphPin*> Matches;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin && Pin->PinName.ToString().Equals(PinName, ESearchCase::IgnoreCase)
				&& (!bHasDir || Pin->Direction == WantDir))
			{
				Matches.Add(Pin);
			}
		}
		if (Matches.Num() == 0)
		{
			Fail(Out, FString::Printf(TEXT("pin not found on node: '%s'%s"), *PinName,
				bHasDir ? *FString::Printf(TEXT(" (direction=%s)"), *DirStr) : TEXT("")));
			return;
		}

		UBlueprint* Blueprint = FBlueprintEditorUtils::FindBlueprintForNode(Node);
		UEdGraph* Graph = Cast<UEdGraph>(Node->GetOuter());
		UK2Node_EditablePinBase* Editable = Cast<UK2Node_EditablePinBase>(Node);

		const bool bUserDefined = Editable && Editable->UserDefinedPins.ContainsByPredicate(
			[&PinName](const TSharedPtr<FUserPinInfo>& Info)
			{
				return Info.IsValid() && Info->PinName.ToString().Equals(PinName, ESearchCase::IgnoreCase);
			});

		if (Graph) { Graph->Modify(); }
		Node->Modify();

		FString Kind;
		if (bUserDefined)
		{
			// Break links first so nothing holds a stale pointer, then drop pin + record.
			//
			// The comment above was the intent; the loop did not achieve it. Matches holds SEVERAL pins
			// on the SAME node, and BreakPinLinks with notification can rebuild that node
			// (PinConnectionListChanged / NodeConnectionListChanged -> ReconstructNode), which frees
			// every other pin in the array. Iteration two then dereferenced freed memory - the add_pin
			// crash class. Capture identities first and re-resolve each time.
			for (const FMifPinRef& Ref : CapturePins(Matches))
			{
				if (UEdGraphPin* Live = ResolvePin(Ref))
				{
					K2()->BreakPinLinks(*Live, /*bSendsNodeNotification*/ true);
				}
			}
			// Matches may now be entirely stale; nothing below this point may dereference it.
			Matches.Reset();
			Editable->RemoveUserDefinedPinByName(FName(*PinName));

			// A function graph may have SEVERAL Return nodes; they all share one signature, so an
			// output removed from one must be removed from the rest or the graph won't compile.
			int32 SiblingsUpdated = 0;
			if (Graph && Node->IsA<UK2Node_FunctionResult>())
			{
				TArray<UK2Node_FunctionResult*> Results;
				Graph->GetNodesOfClass(Results);
				for (UK2Node_FunctionResult* Sibling : Results)
				{
					if (Sibling && Sibling != Node)
					{
						Sibling->Modify();
						Sibling->RemoveUserDefinedPinByName(FName(*PinName));
						Sibling->ReconstructNode();
						++SiblingsUpdated;
					}
				}
			}
			Editable->ReconstructNode();
			Kind = TEXT("userDefined");
			Out->SetNumberField(TEXT("siblingResultNodesUpdated"), SiblingsUpdated);
		}
		else if (Matches.Num() > 1)
		{
			// Duplicate cleanup. Keep a linked copy if there is exactly one; otherwise keep the first.
			UEdGraphPin* Keep = nullptr;
			for (UEdGraphPin* Pin : Matches)
			{
				if (Pin->LinkedTo.Num() > 0) { Keep = Pin; break; }
			}
			if (!Keep) { Keep = Matches[0]; }

			// bSendsNodeNotification is false here, but that only suppresses NodeConnectionListChanged -
			// PinConnectionListChanged still runs on both ends and can RemovePin an orphan. Resolve
			// through identities rather than trusting the snapshot.
			const FMifPinRef KeepRef = CapturePin(Keep);
			int32 Removed = 0;
			for (const FMifPinRef& Ref : CapturePins(Matches))
			{
				UEdGraphPin* Pin = ResolvePin(Ref);
				if (!Pin) { continue; }
				if (Pin == ResolvePin(KeepRef)) { continue; }
				K2()->BreakPinLinks(*Pin, /*bSendsNodeNotification*/ false);
				Node->Pins.Remove(Pin);
				Pin->MarkAsGarbage();
				++Removed;
			}
			Kind = TEXT("duplicate");
			Out->SetNumberField(TEXT("duplicatesRemoved"), Removed);
			Out->SetBoolField(TEXT("keptLinkedCopy"), Keep->LinkedTo.Num() > 0);
		}
		else
		{
			Fail(Out, FString::Printf(
				TEXT("pin '%s' on %s is engine-allocated, not user-defined, and is not duplicated — it cannot be removed. ")
				TEXT("AllocateDefaultPins would recreate it on the next reconstruct. Only user-defined pins ")
				TEXT("(function/event/tunnel parameters) and duplicate pins can be deleted."),
				*PinName, *Node->GetClass()->GetName()));
			return;
		}

		MarkStructural(Blueprint);
		Out->SetBoolField(TEXT("removed"), true);
		Out->SetStringField(TEXT("pin"), PinName);
		Out->SetStringField(TEXT("kind"), Kind);
		Out->SetObjectField(TEXT("node"), SerializeNode(Node, /*bIncludePins*/ true));
		UE_LOG(LogMifBridge, Log, TEXT("remove_pin: %s.%s (%s)"), *Node->GetName(), *PinName, *Kind);
	}

	void H_refresh_node(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId") },
			TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid)")))
		{
			return;
		}

		UEdGraphNode* Node = ResolveNodeField(In, TEXT("nodeGuid"), Out);
		if (!Node)
		{
			return;
		}
		Node->Modify();
		Node->ReconstructNode();
		MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(Node));
		Out->SetObjectField(TEXT("node"), SerializeNode(Node, /*bIncludePins*/ true));
	}

	// --- Pins / wiring ------------------------------------------------------

	void H_connect_pins(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		DoConnect(In, Out, /*bBreakFirst*/ false);
	}

	void H_reconnect_pin(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		DoConnect(In, Out, /*bBreakFirst*/ true);
	}

	void H_disconnect_pin(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// 'path' — back-compat only, accepted and ignored. See the 'path' note above.
		if (RejectUnknownParams(In, Out,
			{ TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"),
			  TEXT("pin"), TEXT("pinName"), TEXT("name"), TEXT("path") },
			TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), ")
			TEXT("path (back-compat only — accepted and ignored; graphId already names the blueprint)")))
		{
			return;
		}

		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node)
		{
			return;
		}
		const FString PinName = JStrAny(In, { TEXT("pin"), TEXT("pinName"), TEXT("name") });
		UEdGraphPin* Pin = FindPin(Node, PinName, EGPD_Input, /*bRequireDir*/ false);
		if (!Pin)
		{
			Fail(Out, FString::Printf(TEXT("pin not found: '%s'"), *PinName));
			return;
		}
		Node->Modify();
		K2()->BreakPinLinks(*Pin, /*bSendsNodeNotification*/ true);
		MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(Node));
		Out->SetObjectField(TEXT("pin"), SerializePin(Pin));
	}

	void H_set_pin_default(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// 'value' here vs 'default' on add_pin was a real in-file inconsistency; both now work on both.
		if (RejectUnknownParams(In, Out,
			{ TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"),
			  TEXT("pin"), TEXT("pinName"), TEXT("name"),
			  TEXT("value"), TEXT("default"), TEXT("defaultValue") },
			TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), ")
			TEXT("value (aliases: default, defaultValue)")))
		{
			return;
		}

		UEdGraphNode* Node = ResolveNodeField(In, TEXT("node"), Out);
		if (!Node)
		{
			return;
		}
		const FString PinName = JStrAny(In, { TEXT("pin"), TEXT("pinName"), TEXT("name") });
		const FString Value = JStrAny(In, { TEXT("value"), TEXT("default"), TEXT("defaultValue") });
		UEdGraphPin* Pin = FindPin(Node, PinName, EGPD_Input, /*bRequireDir*/ false);
		if (!Pin)
		{
			Fail(Out, FString::Printf(TEXT("pin not found: '%s'"), *PinName));
			return;
		}
		Node->Modify();
		// TrySetDefaultValue is void and the schema silently refuses a literal it cannot parse for the
		// pin type, so set_pin_default {value:"banana"} on an int pin used to answer ok:true. The pin
		// was re-serialised into the response, so the truth was IN the payload — but nothing said the
		// write had not landed, and no caller diffs a serialised pin against its own request.
		FString DefaultBefore, DefaultAfter, DefaultError;
		bool bDefaultChanged = false;
		if (!SetPinDefaultChecked(Pin, Value, DefaultBefore, DefaultAfter, bDefaultChanged, DefaultError))
		{
			Fail(Out, DefaultError);
			return;
		}
		Out->SetStringField(TEXT("defaultBefore"), DefaultBefore);
		Out->SetStringField(TEXT("defaultAfter"), DefaultAfter);
		Out->SetBoolField(TEXT("changed"), bDefaultChanged);
		Out->SetObjectField(TEXT("pin"), SerializePin(Pin));
	}

	void H_splice_into_exec(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// Two NODE params, so they keep distinct names (docs/02_GOTCHAS.md:18) — the pin params are
		// per-role and take the obvious short spellings.
		if (RejectUnknownParams(In, Out,
			{ TEXT("afterNode"), TEXT("insertNode"), TEXT("graphId"),
			  TEXT("afterPin"), TEXT("afterExecOut"),
			  TEXT("insertExecIn"), TEXT("insertIn"), TEXT("execIn"),
			  TEXT("insertExecOut"), TEXT("insertOut"), TEXT("execOut") },
			TEXT("afterNode, insertNode, graphId (optional), afterPin (alias: afterExecOut; default \"then\"), ")
			TEXT("insertExecIn (aliases: insertIn, execIn; default \"execute\"), ")
			TEXT("insertExecOut (aliases: insertOut, execOut; default \"then\")"),
			{ { TEXT("beforeNode"), TEXT("splice_into_exec inserts AFTER a node — pass afterNode") },
			  { TEXT("node"), TEXT("this endpoint needs BOTH afterNode and insertNode; there is no single 'node'") } }))
		{
			return;
		}

		UEdGraphNode* AfterNode = ResolveNodeField(In, TEXT("afterNode"), Out);
		if (!AfterNode)
		{
			return;
		}
		UEdGraphNode* InsertNode = ResolveNodeField(In, TEXT("insertNode"), Out);
		if (!InsertNode)
		{
			return;
		}

		// A MACRO INSTANCE as the ANCHOR takes the editor out. Not "fails" - the process is gone, with
		// no crash dialog and the HTTP connection reset mid-call, so the caller cannot even see what
		// happened. Reproduced twice in a row by a user against a DoOnce.
		//
		// Why: UK2Node_MacroInstance is a tunnel. Its exec pins are BOUNDARY pins whose links resolve
		// into the macro's own inner graph, so the far side of AfterOut->LinkedTo can be a pin owned by
		// a node in a DIFFERENT UEdGraph. SpliceExecAfter walks those links to re-home them and trips
		// `Assertion failed: OwningNode`, which is a check(), not a recoverable error.
		//
		// This has been a documented "never do this" in docs/02_GOTCHAS.md for a long time and was
		// enforced NOWHERE. A rule that costs an editor when broken belongs in the code: prose does not
		// stop a caller who has not read it, and this endpoint is reachable by agents that never will.
		// INSERTING a macro is fine and stays allowed - the user's own repro spliced a DoOnce IN
		// successfully, compiled and saved; it was the next call, anchoring off that macro's Completed
		// pin, that died.
		if (const UK2Node_MacroInstance* AnchorMacro = Cast<UK2Node_MacroInstance>(AfterNode))
		{
			const UEdGraph* MacroGraph = AnchorMacro->GetMacroGraph();
			Fail(Out, FString::Printf(
				TEXT("refusing to splice AFTER the macro instance '%s'%s%s: a macro's exec pins are tunnel BOUNDARY pins, ")
				TEXT("and re-homing their links crashes the editor outright (Assertion failed: OwningNode) - no error, no dialog, process gone. ")
				TEXT("Wire past a macro instead: disconnect_pin on the macro's '%s' output, then connect_pins macro->newNode ")
				TEXT("and connect_pins newNode->oldTarget. Inserting a macro (as insertNode) is fine and unaffected."),
				*AnchorMacro->GetName(),
				MacroGraph ? TEXT(" (") : TEXT(""),
				MacroGraph ? *MacroGraph->GetName() : TEXT(""),
				*JStrAny(In, { TEXT("afterPin"), TEXT("afterExecOut") }, TEXT("then"))));
			return;
		}

		const FString AfterPinName = JStrAny(In, { TEXT("afterPin"), TEXT("afterExecOut") }, TEXT("then"));
		const FString InsertInName = JStrAny(In, { TEXT("insertExecIn"), TEXT("insertIn"), TEXT("execIn") }, TEXT("execute"));
		const FString InsertOutName = JStrAny(In, { TEXT("insertExecOut"), TEXT("insertOut"), TEXT("execOut") }, TEXT("then"));

		UEdGraphPin* AfterOut = FindPin(AfterNode, AfterPinName, EGPD_Output, /*bRequireDir*/ true);
		UEdGraphPin* InsertIn = FindPin(InsertNode, InsertInName, EGPD_Input, /*bRequireDir*/ true);
		UEdGraphPin* InsertOut = FindPin(InsertNode, InsertOutName, EGPD_Output, /*bRequireDir*/ true);
		if (!AfterOut)
		{
			Fail(Out, FString::Printf(TEXT("afterPin (exec out) not found: '%s'"), *AfterPinName));
			return;
		}
		if (!InsertIn)
		{
			Fail(Out, FString::Printf(TEXT("insertExecIn not found: '%s'"), *InsertInName));
			return;
		}
		if (!InsertOut)
		{
			Fail(Out, FString::Printf(TEXT("insertExecOut not found: '%s'"), *InsertOutName));
			return;
		}

		// Same refusal keyed on the PIN's owner rather than the node parameter, so an anchor reached by
		// any other route cannot slip past the check above.
		if (AfterOut->GetOwningNode() && AfterOut->GetOwningNode()->IsA<UK2Node_MacroInstance>())
		{
			Fail(Out, TEXT("afterPin belongs to a macro instance - see the refusal above; wire past it with disconnect_pin + two connect_pins"));
			return;
		}

		AfterNode->Modify();
		InsertNode->Modify();

		// Was: BreakPinLinks first, then two TryCreateConnection calls whose bools were discarded, then
		// reconnectedTargets = OldTargets.Num() — the number of links we MEANT to move, reported as if
		// it were the number moved. A wrong pin type or an already-occupied single-link exec left the
		// chain severed and still answered ok:true. SpliceExecAfter (MifBridgeCommon.cpp) validates the
		// whole new shape with CanCreateConnection before breaking anything, and counts real successes.
		int32 Reconnected = 0;
		FString SpliceError;
		if (!SpliceExecAfter(AfterOut, InsertIn, InsertOut, Reconnected, SpliceError))
		{
			Fail(Out, SpliceError);
			return;
		}

		MarkStructural(FBlueprintEditorUtils::FindBlueprintForNode(AfterNode));
		Out->SetNumberField(TEXT("reconnectedTargets"), Reconnected);
		Out->SetObjectField(TEXT("afterPin"), SerializePin(AfterOut));
	}

	// --- Batch --------------------------------------------------------------

	void H_batch(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// Guards only batch's OWN envelope. Each op object is validated by the handler it dispatches to;
		// RejectUnknownParams tolerates the routing key 'op' centrally (MifBridgeCommon.cpp:669).
		if (RejectUnknownParams(In, Out,
			{ TEXT("ops"), TEXT("blueprintId"), TEXT("path"), TEXT("backup"), TEXT("compileAtEnd") },
			TEXT("ops (array), blueprintId (alias: path), backup, compileAtEnd (default true)"),
			{ { TEXT("operations"), TEXT("spell it ops") },
			  { TEXT("graphId"), TEXT("graphId belongs on each op inside ops, not on the batch envelope") } }))
		{
			return;
		}

		const TArray<TSharedPtr<FJsonValue>>* Ops = nullptr;
		if (!JArray(In, TEXT("ops"), Ops) || Ops == nullptr)
		{
			Fail(Out, TEXT("batch requires an 'ops' array"));
			return;
		}
		// An empty ops[] answered ok:true, opCount:0 — indistinguishable from a batch whose entries
		// were all silently dropped (see the per-entry handling below). batch's response IS the audit
		// trail, so "nothing to do" is a caller error worth naming.
		if (Ops->Num() == 0)
		{
			Fail(Out, TEXT("'ops' is empty — batch has nothing to run. Pass at least one {op: ...} object."));
			return;
		}

		// Optional backup of the top-level blueprintId before mutating.
		//
		// This block used to be a degraded copy of H_backup_blueprint and could claim a backup that did
		// not exist. It hardcoded FPackageName::GetAssetPackageExtension(), so for a World package the
		// .uasset path never existed, FileExists was false, and backup:true produced NO backup at all
		// while the batch proceeded to mutate; it discarded IFileManager::Copy's return value, so
		// Out["backup"] could name a .bak that was never written; and an unresolvable or absent
		// blueprintId skipped the whole thing in silence. A caller passes backup:true precisely because
		// what follows is destructive, so every one of those is now a hard Fail BEFORE any op runs —
		// the shared BackupPackage (MifBridgeCommon.cpp) is the single implementation.
		const FString TopBlueprintId = JStrAny(In, { TEXT("blueprintId"), TEXT("path") });
		if (JBool(In, TEXT("backup"), false))
		{
			if (TopBlueprintId.IsEmpty())
			{
				Fail(Out, TEXT("backup:true needs blueprintId (alias: path) on the batch envelope — there is nothing to back up otherwise. Nothing was run."));
				return;
			}
			FString ResolveError;
			UBlueprint* BackupBP = ResolveBlueprint(TopBlueprintId, ResolveError);
			if (!BackupBP)
			{
				Fail(Out, FString::Printf(TEXT("backup:true was requested but blueprintId '%s' did not resolve: %s. Nothing was run."),
					*TopBlueprintId, *ResolveError));
				return;
			}
			FString BackupPath, BackupError;
			if (!BackupPackage(BackupBP->GetOutermost(), BackupPath, BackupError))
			{
				Fail(Out, FString::Printf(TEXT("backup:true was requested but the backup failed: %s. Nothing was run."), *BackupError));
				return;
			}
			Out->SetStringField(TEXT("backup"), BackupPath);
		}

		TArray<TSharedPtr<FJsonValue>> Results;
		TSet<UBlueprint*> Touched;
		bool bAllOk = true;

		const TMap<FString, FHandlerFn>& Registry = Handlers();

		// All op mutations are captured in ONE transaction (one Ctrl-Z). It closes BEFORE
		// the compileAtEnd step so reinstancing is never captured as an undo step. Ops that
		// themselves compile (create_function, recipe_add_debug_print, nested batch) are
		// disallowed here — call them standalone.
		{
			FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "Batch", "Mif Bridge: batch"));
			// Marks "a batch transaction is open" for the duration, so a handler with ONE
			// compile-heavy branch can refuse just that branch (set_property's widget-Blueprint path)
			// instead of the whole endpoint being banned from batch, and so RejectUnknownParams
			// tolerates the routing key 'op' only where batch actually injects it.
			FBatchTransactionScope BatchScope;

			int32 OpIndex = INDEX_NONE;
			for (const TSharedPtr<FJsonValue>& OpValue : *Ops)
			{
				++OpIndex;
				const TSharedPtr<FJsonObject>* OpObjectPtr = nullptr;
				if (!OpValue.IsValid() || !OpValue->TryGetObject(OpObjectPtr) || OpObjectPtr == nullptr)
				{
					// A non-object entry (e.g. ops:["add_branch"] — strings instead of objects) used to
					// `continue`, so it never appeared in results[] and opCount under-counted it:
					// ok:true, opCount:0, results:[]. batch's response is the audit trail, so a dropped
					// op has to be visible IN it, at its own index.
					TSharedRef<FJsonObject> BadOut = MakeShared<FJsonObject>();
					BadOut->SetBoolField(TEXT("ok"), true);
					BadOut->SetNumberField(TEXT("index"), OpIndex);
					Fail(BadOut, FString::Printf(
						TEXT("ops[%d] is not an object — each entry must be {\"op\":\"<endpoint>\", ...}"), OpIndex));
					bAllOk = false;
					Results.Add(MakeShared<FJsonValueObject>(BadOut));
					continue;
				}
				const TSharedRef<FJsonObject> OpIn = OpObjectPtr->ToSharedRef();
				const FString OpName = JStr(OpIn, TEXT("op"));

				TSharedRef<FJsonObject> OpOut = MakeShared<FJsonObject>();
				OpOut->SetBoolField(TEXT("ok"), true);
				OpOut->SetNumberField(TEXT("index"), OpIndex);
				OpOut->SetStringField(TEXT("op"), OpName);
				// The silent-ignore record (MifBridgeHandlers.h) is reset once per REQUEST by
				// RunEndpoint, and batch is one request however many ops it runs — so attribute by
				// delta, or op[7]'s bad parameter would be reported against op[0] as well.
				const int32 ViolationsBeforeOp = NumParamTypeViolations();

				if (OpName.IsEmpty())
				{
					Fail(OpOut, FString::Printf(TEXT("ops[%d] has no 'op' — name the endpoint to call"), OpIndex));
				}
				else if (OpName == TEXT("batch") || IsCompileHeavyEndpoint(OpName))
				{
					Fail(OpOut, FString::Printf(TEXT("op '%s' is not allowed inside batch (it runs a full compile, which must not happen inside batch's open transaction); call it standalone — batch already compiles once at the end via compileAtEnd"), *OpName));
				}
				else if (const FHandlerFn* Fn = Registry.Find(OpName))
				{
					// ATTRIBUTE THE OP'S GUARD TO THE OP, NOT TO batch. RejectUnknownParams files each
					// accepted-key list it sees under GMifCurrentEndpoint, and the only writer of that
					// global is RunEndpoint - which batch deliberately does not recurse through. So
					// every op's key list was recorded against "batch", and TMap::Add REPLACES, so
					// batch's own five-key entry was destroyed by whichever op ran last. self_audit
					// then reported batch's observedParamCount as some other endpoint's count.
					//
					// The op lost out too: an endpoint only ever exercised through batch never got an
					// entry of its own, so describe_endpoint kept answering params_not_declared for a
					// guard that had demonstrably just run - which is precisely what the
					// runtime-observed branch was written to fix.
					//
					// Restored to "batch" rather than cleared, so batch's OWN guard (which already ran
					// before this loop) keeps its attribution and anything later in this dispatch is
					// still charged to batch.
					MifSetCurrentEndpoint(OpName);
					(*Fn)(OpIn, OpOut); // runs inside the batch's single transaction
					MifSetCurrentEndpoint(TEXT("batch"));
				}
				// Mirror RunEndpoint's resolution order: built-ins, THEN provider-registered endpoints.
				// Without this second lookup every kr_* op answered "unknown op: 'kr_list_events'" for
				// an endpoint self_audit lists as live — a confidently wrong statement about the
				// bridge's own surface, and it made the reconstructor's batch-specific 'op' handling
				// dead code. The compile-heavy ban above already consults the external registry (it
				// derives from IsSelfManagedEndpoint), so external SelfManaged endpoints stay fenced
				// out for free and no new policy is introduced here.
				else if (const FHandlerFn* ExtFn = FindExternalHandler(OpName))
				{
					MifSetCurrentEndpoint(OpName);      // same reasoning as the built-in branch above
					(*ExtFn)(OpIn, OpOut);
					MifSetCurrentEndpoint(TEXT("batch"));
				}
				else
				{
					Fail(OpOut, FString::Printf(TEXT("unknown op: '%s'"), *OpName));
				}

				if (NumParamTypeViolations() > ViolationsBeforeOp)
				{
					Fail(OpOut, FString::Printf(
						TEXT("ops[%d] ('%s') supplied a parameter of the wrong JSON type, which was IGNORED: %s. ")
						TEXT("The op is reported failed rather than left looking successful; note that batch's single ")
						TEXT("transaction still commits, so re-read anything this batch touched."),
						OpIndex, *OpName, *DescribeParamTypeViolations()));
				}
				if (!IsOk(OpOut))
				{
					bAllOk = false;
				}

				// Track which blueprint each op touched so we can compile them once at the end.
				// 'path' is checked as well as 'blueprintId' because the handlers' own guards now
				// ADVERTISE it as an alias (add_pin, add_override_event); consulting only blueprintId
				// meant an op addressed through `path` mutated the blueprint, left Touched empty, and
				// returned compiles:[] — structurally modified and uncompiled, reported as ok:true.
				FString ResolveError;
				if (OpIn->HasField(TEXT("graphId")))
				{
					UBlueprint* OpBlueprint = nullptr;
					if (ResolveGraph(JStr(OpIn, TEXT("graphId")), OpBlueprint, ResolveError) && OpBlueprint)
					{
						Touched.Add(OpBlueprint);
					}
				}
				else if (JHasAny(OpIn, { TEXT("blueprintId"), TEXT("path") }))
				{
					if (UBlueprint* OpBlueprint = ResolveBlueprint(JStrAny(OpIn, { TEXT("blueprintId"), TEXT("path") }), ResolveError))
					{
						Touched.Add(OpBlueprint);
					}
				}
				// A THIRD ADDRESSING FORM: by NODE alone. rename_event and set_function_flags both take
				// nodeGuid (aliases node/guid/nodeId) with no graphId and no blueprintId, mutate the
				// blueprint that owns the node, and used to leave Touched empty - so compileAtEnd
				// skipped them and the response reported ok with compiles:[] over a blueprint left
				// structurally modified and uncompiled.
				//
				// This is the SAME bug the comment above records being fixed for `path`: an addressing
				// form was added to the handlers and the tracking here was not revisited. It is masked
				// whenever the caller passes a top-level blueprintId to batch, which is why it survived.
				else if (JHasAny(OpIn, { TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId") }))
				{
					const FString NodeGuid = JStrAny(OpIn, { TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId") });
					if (UEdGraphNode* OpNode = ResolveNode(NodeGuid, ResolveError))
					{
						if (UBlueprint* OpBlueprint = FBlueprintEditorUtils::FindBlueprintForNode(OpNode))
						{
							Touched.Add(OpBlueprint);
						}
					}
				}

				Results.Add(MakeShared<FJsonValueObject>(OpOut));
			}
		}

		Out->SetBoolField(TEXT("ok"), bAllOk);
		Out->SetNumberField(TEXT("opCount"), Results.Num());
		Out->SetArrayField(TEXT("results"), Results);

		if (JBool(In, TEXT("compileAtEnd"), true))
		{
			if (!TopBlueprintId.IsEmpty())
			{
				FString ResolveError;
				if (UBlueprint* TopBP = ResolveBlueprint(TopBlueprintId, ResolveError))
				{
					Touched.Add(TopBP);
				}
			}

			TArray<TSharedPtr<FJsonValue>> Compiles;
			for (UBlueprint* Blueprint : Touched)
			{
				TSharedRef<FJsonObject> CompileOut = MakeShared<FJsonObject>();
				CompileBlueprintInto(Blueprint, CompileOut);
				CompileOut->SetStringField(TEXT("blueprintId"), Blueprint->GetPathName());
				if (!IsOk(CompileOut))
				{
					Out->SetBoolField(TEXT("ok"), false);
				}
				Compiles.Add(MakeShared<FJsonValueObject>(CompileOut));
			}
			Out->SetArrayField(TEXT("compile"), Compiles);
		}
	}
}
