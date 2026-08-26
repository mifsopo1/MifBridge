// MifBridge — writing properties on INHERITED components in a CHILD Blueprint.
//
// This is the Details panel's own write path: select a component that came from a PARENT
// Blueprint, change a value, and the child stores a DELTA rather than a copy. Until now the
// bridge could edit a blueprint's OWN component templates (list_components -> templatePath ->
// set_property, docs/02_GOTCHAS.md §5d) but had no route at all for an inherited one — the single
// most-wanted gap in the audit.
//
// ============================================================================================
// REGISTRY LINES THE MAIN SESSION MUST ADD (this file declares/binds nothing itself)
//
//   MifBridgeHandlers.h — beside the other component declarations (MIF_DECL(add_component) ...):
//       // INHERITED components (MifBridgeInherited.cpp) — the Details-panel write path for a
//       // component that came from a PARENT Blueprint's SCS. Delta storage via
//       // UInheritableComponentHandler; no compile, so both mutators are ordinary transacted
//       // endpoints. get_inherited_component is the read-only discovery verb: call it FIRST to
//       // learn which of the four routes (parentBlueprintSCS / native / ownSCS / notFound) applies.
//       MIF_DECL(get_inherited_component);
//       MIF_DECL(override_inherited_component);
//       MIF_DECL(revert_inherited_component);
//
//   MifBridgeCommon.cpp — in the MIF_BIND block, beside the component binds:
//       // Inherited component overrides (Batch J)
//       MIF_BIND(get_inherited_component);
//       MIF_BIND(override_inherited_component);
//       MIF_BIND(revert_inherited_component);
//
//   MifBridgeCommon.cpp — IsReadOnlyEndpoint()'s TSet, ONE new entry:
//       // Pure discovery: resolves a name across the parent chain and reports the route. Creates
//       // NOTHING — it deliberately calls GetInheritableComponentHandler(false), so it cannot
//       // mint an ICH just by being asked a question, and must not push an empty undo entry.
//       TEXT("get_inherited_component"),
//
//   IsSelfManagedEndpoint() — NO entries. See the bucket note below.
//
// BUCKETS. get_inherited_component = READ-ONLY. override_inherited_component and
// revert_inherited_component = TRANSACTED (RunEndpoint's blanket transaction; they add NOTHING to
// IsSelfManagedEndpoint). Justification, because "it edits a Blueprint asset" is not by itself a
// reason to be self-managed in this codebase — that bucket exists for handlers that run a full
// FKismetEditorUtilities::CompileBlueprint (MifBridgeCommon.cpp:374-378), because class
// reinstancing captured by an undo step restores a dead CDO and crashes:
//   * Neither endpoint compiles. The dirty path is FBlueprintEditorUtils::MarkBlueprintAsModified
//     (BlueprintEditorUtils.cpp:1831-1895) — it sets BS_Dirty, MarkPackageDirty, and
//     UpdateCustomPropertyListForPostConstruction on the BPGC and its children, and does NOT
//     compile. MarkBlueprintAsStructurallyModified (:1802-1828) would be wrong here: it runs
//     FBlueprintCompilationManager::CompileSynchronously(RegenerateSkeletonOnly). A skeleton regen
//     rebuilds the class's VARIABLE set; an ICH override creates no variable — the component
//     variable already exists, inherited from the parent — so structural buys a synchronous
//     compile for nothing. That is why add_component uses structural (it mints a variable) and
//     set_component_transform uses plain modified (it changes a template value); this is the
//     second kind.
//   * The override takes effect with no compile at all: USCS_Node::GetActualComponentTemplate
//     (SCS_Node.cpp:29-54) walks the child's ICH chain at INSTANCING time and returns the override
//     when a record matches, falling back to ComponentTemplate otherwise. So the value is live the
//     moment the record exists.
//   * Every mutation is Modify()-able (Blueprint, ICH, template), so the blanket transaction gives
//     the caller a correct Ctrl-Z for free — which self-managed would throw away.
//
// ============================================================================================
// MECHANISM. The child does not own the inherited component's template; the parent's SCS does.
// UInheritableComponentHandler keeps one FComponentOverrideRecord per overridden component
// (InheritableComponentHandler.h:169-170), each holding an FComponentKey identifying the PARENT's
// SCS node plus a fresh template object created from the parent's as its archetype
// (InheritableComponentHandler.cpp:104-198). Canonical path, read verbatim from
// Engine/Source/Editor/SubobjectDataInterface/Private/SubobjectData.cpp:145-170
// (FSubobjectData::GetObjectForBlueprint), duplicated in SSCSEditor.cpp:1544-1569:
//
//     if (IsComponent() && bCanEdit && !IsNativeComponent() && IsInheritedSCSNode())
//     {
//         FComponentKey Key(GetSCSNode());
//         const bool bBlueprintCanOverrideComponentFromKey = Key.IsValid()
//             && Blueprint && Blueprint->ParentClass
//             && Blueprint->ParentClass->IsChildOf(Key.GetComponentOwner());
//         if (bBlueprintCanOverrideComponentFromKey) {
//             UInheritableComponentHandler* ICH = Blueprint->GetInheritableComponentHandler(true);
//             OverriddenComponent = ICH->GetOverridenComponentTemplate(Key);
//             if (!OverriddenComponent) OverriddenComponent = ICH->CreateOverridenComponentTemplate(Key);
//         }
//     }
//
// THREE CORRECTIONS this file exists to encode (each was a wrong turn in an earlier proposal):
//
//  1. THERE IS NO FComponentKey(FName). The struct has exactly three constructors —
//     default, FComponentKey(const USCS_Node*) and FComponentKey(UBlueprint*, const FUCSComponentId&)
//     (InheritableComponentHandler.h:23-30). A first-time override therefore MUST be keyed off the
//     parent's real USCS_Node, found by walking Blueprint->ParentClass up the
//     UBlueprintGeneratedClass chain and asking each class's SimpleConstructionScript. That walk is
//     the engine's own (Editor.cpp:1260-1272). UInheritableComponentHandler::FindKey(FName)
//     (InheritableComponentHandler.cpp:509-519) iterates Records, so it can only ever find a key
//     for an override that ALREADY exists — it is used here strictly as a fast path, never as the
//     way to create one.
//
//  2. NEVER ImportText INTO THE LIVE PROPERTY ADDRESS. docs/01_POSTMORTEMS.md PM-003: ImportText
//     parses IN PLACE and can consume/zero the destination before deciding the text is invalid, so
//     a failed write DESTROYS the value it failed to set. Every write below imports into a scratch
//     buffer seeded from the current value (so partial struct literals keep the untouched members,
//     as the Details panel does) and publishes with CopyCompleteValue only after the parse
//     succeeded. Same approach as set_property in MifBridgeNodes5.cpp — and now literally the same
//     code: the dot-walk (MifBridge::ResolvePropertyPath) and the JSON->property-text converter
//     (MifBridge::JsonToPropertyText) are shared, so a PM-003-class fix lands on both endpoints at
//     once instead of on whichever one the next bug report happens to name.
//
//  3. NATIVE COMPONENTS ARE NOT AN ICH CASE AT ALL. The editor's own guard is `!IsNativeComponent()`
//     (SubobjectData.cpp:148; the predicate is SubobjectData.cpp:814-822 —
//     CreationMethod == Native && GetSCSNode() == nullptr). A C++ component declared on a native
//     parent (ACharacter::Mesh, ACharacter::CharacterMovement, ...) already exists as the CHILD
//     Blueprint's own CDO subobject, so the child edits it directly and there is nothing to delta.
//     Feeding one to the ICH would silently write the wrong object. This file detects that case and
//     returns the exact CDO subobject path to use with the existing set_property instead. Note the
//     path uses the SUBOBJECT's name, not the property's: on a Character child, property `Mesh`
//     lives at `...Default__<Class>_C:CharacterMesh0`, `CharacterMovement` at `:CharMoveComp`,
//     `CapsuleComponent` at `:CollisionCylinder` (verified live on the bridge, 2026-07-28). Nobody
//     guesses those, which is exactly why the path is resolved from the object and emitted rather
//     than composed from the name the caller passed.
//
//  4. VALIDATE BEFORE YOU MINT — added by Batch M after a live failure, and the reason the mutator
//     below is ordered guards -> preflight -> create -> apply rather than create -> apply.
//     `override_inherited_component {..., properties:{"SphereRadius":"not-a-float"}}` correctly
//     returned ok:false and LEFT THE OVERRIDE IT HAD ALREADY MINTED ON THE ASSET. RunEndpoint's
//     Transaction.Cancel() cannot help: the ICH and its templates are created without
//     RF_Transactional so nothing is recorded (UObjectGlobals.cpp:3131-3134 vs
//     BlueprintGeneratedClass.cpp:1202 and InheritableComponentHandler.cpp:159-160), AND
//     UTransBuffer::Cancel discards the undo entry without ever calling FTransaction::Apply
//     (EditorTransaction.cpp:1387-1437). ORDER is the only mechanism that makes a failed call leave
//     nothing behind. The full argument, with every citation, is in the block comment inside
//     H_override_inherited_component; the general rule is docs/01_POSTMORTEMS.md PM-007.
//
// CONFIG GATE. UBlueprint::GetInheritableComponentHandler returns NULL outright when
// [Kismet] bEnableInheritableComponents is false (Blueprint.cpp:2062-2068; BaseEngine.ini:1947
// ships it true), and USCS_Node::GetActualComponentTemplate checks the same helper, so with it off
// an override would be written and then ignored at instancing. Both mutators report that as a
// named failure instead of a null dereference.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Components/ActorComponent.h"                 // UActorComponent, EComponentCreationMethod
#include "Engine/Blueprint.h"                          // UBlueprint::GetInheritableComponentHandler
#include "Engine/BlueprintGeneratedClass.h"            // UBlueprintGeneratedClass::SimpleConstructionScript
#include "Engine/InheritableComponentHandler.h"        // FComponentKey, UInheritableComponentHandler
#include "Engine/SCS_Node.h"                           // USCS_Node::GetVariableName
#include "Engine/SimpleConstructionScript.h"           // FindSCSNode / GetAllNodes
#include "Kismet2/BlueprintEditorUtils.h"              // MarkBlueprintAsModified
#include "UObject/Class.h"                             // UClass::FindPropertyByName / GetDefaultObject
#include "UObject/UnrealType.h"                        // FProperty, ImportText_Direct, ExportTextItem_Direct

namespace MifBridge
{
	namespace
	{
		// The four origin words used to be four literals HERE. Batch N gave list_components the same
		// four, and two files spelling the same state is the PM-005 shape even when the copies are
		// only string literals - a rename in one place and the other silently keeps answering the old
		// word. They now have exactly ONE definition, MifBridge::kComponentOrigin* in
		// MifBridgeCommon.cpp (declared in MifBridgeHandlers.h), and every use below names those
		// directly. Do NOT re-introduce a local alias either: a namespace-scope alias initialised from
		// another translation unit's constant is a static-initialisation-order bet, and it buys
		// nothing but a shorter identifier.

		// Everything the three endpoints need to know about one component name, resolved ONCE.
		// Filled by ResolveComponentOrigin; nothing in here creates anything.
		struct FInheritedResolution
		{
			FString                    Origin          = kComponentOriginNotFound;
			USCS_Node*                 OwnNode         = nullptr;  // ownSCS: this blueprint's own node
			USCS_Node*                 ParentNode      = nullptr;  // parentBlueprintSCS: the parent's node
			UBlueprintGeneratedClass*  ParentOwnerBPGC = nullptr;  // class whose SCS holds ParentNode
			UActorComponent*           NativeTemplate  = nullptr;  // native: the CHILD CDO's subobject
			FString                    NativeMatchedBy;            // "property" | "subobject"
			FComponentKey              Key;                        // valid only for parentBlueprintSCS
		};

		// FindNativeComponentOnCDO moved to MifBridgeCommon.cpp (declared in MifBridgeHandlers.h) in
		// Batch N. list_components needs the SAME property-name-then-subobject-name resolution to report
		// a native component's real CDO subobject path, and a second copy under a second name is the
		// PM-005 failure the compiler never reports. Do NOT re-add a local copy.

		// Resolve one component NAME against a child blueprint, in the order that decides the route.
		// Own SCS is tested FIRST: a name that exists in this blueprint's own tree is not inherited
		// at all, and routing it through the ICH would create a bogus record keyed off a parent node
		// that happens to share the name.
		void ResolveComponentOrigin(UBlueprint* Blueprint, const FName Name, FInheritedResolution& Out)
		{
			if (USimpleConstructionScript* OwnSCS = Blueprint->SimpleConstructionScript)
			{
				if (USCS_Node* Node = OwnSCS->FindSCSNode(Name))
				{
					Out.Origin = kComponentOriginOwnSCS;
					Out.OwnNode = Node;
					return;
				}
			}

			// Parent-chain walk — the engine's own (Editor.cpp:1260-1272). Starting at ParentClass
			// (not GeneratedClass) is what keeps the child's own SCS out of the search; each level is
			// a UBlueprintGeneratedClass, which still carries its SimpleConstructionScript even when
			// the class is COOKED and has no UBlueprint asset behind it — the common case in this
			// project, where mod blueprints derive from cooked game blueprints.
			for (UBlueprintGeneratedClass* BPGC = Cast<UBlueprintGeneratedClass>(Blueprint->ParentClass);
				 BPGC != nullptr;
				 BPGC = Cast<UBlueprintGeneratedClass>(BPGC->GetSuperClass()))
			{
				if (!BPGC->SimpleConstructionScript)
				{
					continue;
				}
				if (USCS_Node* Node = BPGC->SimpleConstructionScript->FindSCSNode(Name))
				{
					Out.Origin = kComponentOriginParentSCS;
					Out.ParentNode = Node;
					Out.ParentOwnerBPGC = BPGC;
					Out.Key = FComponentKey(Node);   // the ONLY constructor that can mint a new key
					return;
				}
			}

			FString MatchedBy;
			if (UActorComponent* Native = FindNativeComponentOnCDO(Blueprint, Name, MatchedBy))
			{
				Out.Origin = kComponentOriginNative;
				Out.NativeTemplate = Native;
				Out.NativeMatchedBy = MatchedBy;
				return;
			}

			Out.Origin = kComponentOriginNotFound;
		}

		// Every component name reachable from this blueprint, with where it came from. Emitted only
		// on the notFound path: "component 'X' not found" with no list is the error that costs an
		// agent three more round trips guessing spellings.
		//
		// Batch N: this walked the three origins itself, which made it the SECOND enumerator in the
		// module the moment list_components grew one - and the two would have disagreed immediately,
		// because this one reported a native component under its SUBOBJECT name (CharacterMesh0) while
		// the Details panel and describe_class show the PROPERTY name (Mesh). It now calls the shared
		// MifBridge::EnumerateBlueprintComponents, so the names this error lists are exactly the names
		// list_components reports and exactly the names ResolveComponentOrigin accepts.
		void GatherAvailableComponents(UBlueprint* Blueprint, const TSharedRef<FJsonObject>& Out, int32 Cap)
		{
			TArray<FComponentOriginRow> Rows;
			EnumerateBlueprintComponents(Blueprint, Rows, Cap);

			TArray<TSharedPtr<FJsonValue>> Json;
			for (const FComponentOriginRow& Row : Rows)
			{
				TSharedRef<FJsonObject> RowJson = MakeShared<FJsonObject>();
				RowJson->SetStringField(TEXT("name"), Row.Name.ToString());
				RowJson->SetStringField(TEXT("origin"), Row.Origin);
				if (Row.ComponentClass) { RowJson->SetStringField(TEXT("class"), Row.ComponentClass->GetName()); }
				if (Row.SubobjectName != NAME_None)
				{
					// Native only, and only when it DIFFERS from the property name - which is the whole
					// point: Mesh -> CharacterMesh0, CharacterMovement -> CharMoveComp.
					RowJson->SetStringField(TEXT("subobjectName"), Row.SubobjectName.ToString());
				}
				Json.Add(MakeShared<FJsonValueObject>(RowJson));
			}

			Out->SetArrayField(TEXT("availableComponents"), Json);
			Out->SetNumberField(TEXT("availableComponentCount"), Json.Num());

			// THE CAP IS ORDER-BIASED, so saying "the same set" is false once it bites.
			// EnumerateBlueprintComponents fills in three sections - own SCS, then the parent's SCS,
			// then the native CDO - and its HasRoom() gate is checked in every one. Section 1 spends
			// the whole budget first, so a blueprint with Cap-or-more of its OWN components yields a
			// list that structurally CANNOT contain an inherited or native row. The list looked
			// complete, said so, and was the very thing added to stop a caller guessing at what
			// exists.
			//
			// list_components passes Cap 0 and really does return everything, which is what makes the
			// old note wrong rather than merely incomplete.
			//
			// Truncation is inferred from having filled to the cap. That is a slight over-report - a
			// blueprint with exactly Cap components and nothing inherited is flagged when nothing was
			// lost - and that is the right direction: claiming completeness wrongly is the failure
			// worth avoiding.
			const bool bCapped = (Cap > 0 && Json.Num() >= Cap);
			Out->SetBoolField(TEXT("availableComponentsTruncated"), bCapped);
			Out->SetStringField(TEXT("availableComponentsNote"), bCapped
				? FString::Printf(TEXT("this list is CAPPED at %d and is not the whole set - it is filled from this "
					"blueprint's own components first, so inherited and native ones may be missing entirely. "
					"Call list_components, which is uncapped, for the real set."), Cap)
				: FString(TEXT("list_components on this blueprint returns the same set, plus the template path and the exact endpoint to call for each row")));
		}

		// --- property write plumbing -------------------------------------------------------
		//
		// OWNERSHIP NOTE — HONOURED IN FULL, and deleted as a standing clause so it cannot read as a
		// standing invitation. The eviction clause that used to sit here ("promote once the ownership
		// fence lifts") had no trigger, which is how a "temporary" duplicate becomes a permanent one.
		// Everything it covered now has exactly ONE definition, in MifBridgeCommon.cpp, declared in
		// MifBridgeHandlers.h:
		//   NormalizeBoolLiteral      — was here AND `static` in MifBridgeNodes5.cpp. Under a unity
		//                               build an unnamed namespace and a file-scope `static` are the
		//                               same namespace scope in one TU, so those two were a C2084 the
		//                               moment file sizes put the files in one blob.
		//   ResolvePropertyPathLocal  — was the third copy of the Details-panel dot-walk
		//                               (MifBridgeNodes5.cpp ResolvePropertyPath, MifBridgeNodes6.cpp
		//                               ResolveReadPropertyPath). A PM-003-class fix to any one of the
		//                               three left the other two exposed. Now MifBridge::ResolvePropertyPath.
		//   JsonValueToPropertyText   — was a WEAKER sibling of MifBridgeNodes5.cpp's
		//                               JsonToPropertyText: it refused every JSON array and object
		//                               ("not UE property text"), and it decided int-vs-float
		//                               formatting from the JSON value's shape rather than from the
		//                               destination property, so 1.5 into an int32 produced "1.5" and
		//                               failed inside the importer with a message that named neither.
		//                               Now MifBridge::JsonToPropertyText, which converts against the
		//                               resolved FProperty — so override_inherited_component gained
		//                               array/map/struct values and set_property's exact refusals.
		// Do NOT re-add a local copy of any of them.

		FString ExportLeaf(FProperty* Leaf, const void* Addr, UObject* Owner)
		{
			FString S;
			// nullptr DefaultValue, not Addr: ExportText_Direct short-circuits when Data == Delta and
			// would emit nothing for an unchanged value — useless for a before/after comparison.
			Leaf->ExportTextItem_Direct(S, Addr, nullptr, Owner, PPF_None);
			return S;
		}

		// One property write, PM-003-safe, with the result VERIFIED by re-export rather than assumed
		// from ImportText's return value.
		//
		// The anti-silence rule this implements: `applied` is true only when the object, read back
		// after the publish, holds exactly what the import produced. `changed` is reported
		// SEPARATELY, so writing a value the property already had is visible as applied+unchanged
		// instead of being either mislabelled a failure or hidden as a success.
		struct FWriteOutcome
		{
			FString Name;
			bool    bApplied = false;
			bool    bChanged = false;
			// Whether the VALUE was type-checked against the destination property, as opposed to
			// merely written. Two different questions that used to have one answer — see defect 2 in
			// ApplyOneProperty below.
			bool    bTypeValidated = false;
			FString TypeNote;
			FString Before;
			FString After;
			FString Wanted;
			FString Reason;
		};

		// EVERYTHING THE WRITE DOES BEFORE IT TOUCHES THE OBJECT: resolve the dot-path, refuse an
		// EditConst or value-less entry, and TYPE-CHECK the caller's value against the destination
		// property. Split out of ApplyOneProperty by Batch M so the IDENTICAL code can run as a DRY
		// RUN against the parent's archetype before anything is created, and again against the real
		// override when the write happens. ONE implementation on purpose: a preflight that can drift
		// from the writer it is predicting is worse than no preflight at all, because it turns a hard
		// failure into a wrong promise. (PM-005 is the same rule for a different reason.)
		//
		// Nothing in here writes: ResolvePropertyPath only walks (MifBridgeCommon.cpp:1340-1423) and
		// both validators work on a scratch buffer, so running this against the PARENT's template
		// cannot modify the parent asset.
		//
		// Takes the raw JSON value, not pre-flattened text: the conversion has to happen AFTER the path
		// resolve because the destination FProperty is what decides whether 1 means "1" or "1.000000",
		// whether ["A","B"] is legal at all, and what a bad value's refusal should say. The previous
		// shape (convert first, blind to the property) is why a JSON array was refused outright here
		// while set_property accepted it.
		bool PrepareOneProperty(UObject* Target, const FString& PropPath, const TSharedPtr<FJsonValue>& ValueJson,
			FProperty*& OutLeaf, void*& OutLeafAddr, UObject*& OutLeafOwner, FString& OutImportStr, FWriteOutcome& R)
		{
			R.Name = PropPath;
			OutLeaf = nullptr; OutLeafAddr = nullptr; OutLeafOwner = nullptr;
			OutImportStr.Reset();

			FString Error;
			if (!ResolvePropertyPath(Target, PropPath, OutLeaf, OutLeafAddr, OutLeafOwner, Error))
			{
				R.Reason = Error;
				return false;
			}
			if (OutLeaf->HasAnyPropertyFlags(CPF_EditConst))
			{
				// Named up front rather than after a write that silently does nothing.
				R.Reason = FString::Printf(TEXT("property '%s' is EditConst (read-only in the Details panel too)"), *PropPath);
				return false;
			}
			if (!ValueJson.IsValid() || ValueJson->Type == EJson::None)
			{
				R.Reason = FString::Printf(TEXT("properties['%s'] has no value"), *PropPath);
				return false;
			}

			// Same two-branch policy as set_property: a STRING reaches the importer byte-for-byte (so
			// every existing caller sending UE export text is unaffected), anything else is converted
			// against this property by the shared converter and REFUSED if it cannot convert faithfully.
			if (ValueJson->Type == EJson::String)
			{
				OutImportStr = ValueJson->AsString();
				if (CastField<FBoolProperty>(OutLeaf)) { OutImportStr = NormalizeBoolLiteral(OutImportStr); }
				// THE DEFECT THIS ENDPOINT WAS CAUGHT WITH (Batch L, defect 2).
				// override_inherited_component {component:"Influence",
				// properties:{"SphereRadius":"not-a-float"}} answered ok:true, applied:true,
				// wanted:"0.000000": UE's float importer parsed "not-a-float" as 0.0 and reported
				// success, and the verification below then compared after(0) against wanted(0) and
				// passed. Verifying that the write LANDED does not verify that the value was
				// UNDERSTOOD, and no post-write check can — both sides come from the same misparse.
				// Shared validator, so set_property, set_variable_default and this endpoint agree.
				FString TypeError;
				if (!ValidatePropertyText(OutLeaf, OutImportStr, PropPath, TypeError, &R.bTypeValidated))
				{
					R.Reason = TypeError;
					return false;
				}
				if (!R.bTypeValidated && !TypeError.IsEmpty()) { R.TypeNote = TypeError; }
			}
			else
			{
				FString ConvError;
				if (!JsonToPropertyText(ValueJson, OutLeaf, /*bDelimited*/ false, OutLeafOwner, /*Depth*/ 0, PropPath, OutImportStr, ConvError))
				{
					R.Reason = ConvError;
					return false;
				}
				// Every leaf JsonToPropertyText converted went through CanonicaliseLeaf, which calls
				// the same validator.
				R.bTypeValidated = true;
			}
			return true;
		}

		void ApplyOneProperty(UObject* Target, const FString& PropPath, const TSharedPtr<FJsonValue>& ValueJson, FWriteOutcome& R)
		{
			FProperty* Leaf = nullptr; void* LeafAddr = nullptr; UObject* LeafOwner = nullptr;
			FString ImportStr;
			if (!PrepareOneProperty(Target, PropPath, ValueJson, Leaf, LeafAddr, LeafOwner, ImportStr, R))
			{
				return;   // R.Reason already names the property, the value and the expected form
			}
			R.Before = ExportLeaf(Leaf, LeafAddr, LeafOwner);

			// PM-003. Scratch buffer, seeded from the CURRENT value so a partial struct literal
			// leaves the untouched members alone exactly as the Details panel does; the live address
			// is never handed to a parse that can fail.
			const int32 ValueSize = Leaf->GetSize();   // spans ArrayDim, so C-array UPROPERTYs round-trip
			void* Scratch = FMemory::Malloc(FMath::Max(ValueSize, 1), Leaf->GetMinAlignment());
			Leaf->InitializeValue(Scratch);
			Leaf->CopyCompleteValue(Scratch, LeafAddr);

			FStringOutputDevice ErrText;
			const TCHAR* Parsed = Leaf->ImportText_Direct(*ImportStr, Scratch, LeafOwner, PPF_None, &ErrText);
			if (Parsed != nullptr)
			{
				R.Wanted = ExportLeaf(Leaf, Scratch, LeafOwner);

				LeafOwner->Modify();
				LeafOwner->PreEditChange(Leaf);
				Leaf->CopyCompleteValue(LeafAddr, Scratch);      // publish
				// Only notify for a write that actually happened (the second half of PM-003).
				FPropertyChangedEvent Evt(Leaf, EPropertyChangeType::ValueSet);
				LeafOwner->PostEditChangeProperty(Evt);

				// VERIFY. PostEditChangeProperty can clamp, snap or outright reject a value
				// (component clamps, UPROPERTY meta ClampMin, custom PostEditChangeProperty
				// overrides). Read the object back instead of trusting the import.
				R.After = ExportLeaf(Leaf, LeafAddr, LeafOwner);
				R.bApplied = R.After.Equals(R.Wanted, ESearchCase::CaseSensitive);
				R.bChanged = !R.After.Equals(R.Before, ESearchCase::CaseSensitive);
				if (!R.bApplied)
				{
					R.Reason = FString::Printf(
						TEXT("write did not land: asked for '%s', object holds '%s' after PostEditChangeProperty (clamped or rejected by the component)"),
						*R.Wanted, *R.After);
				}
			}
			else
			{
				R.After = R.Before;   // untouched, by construction
				R.Reason = FString::Printf(TEXT("could not parse '%s' as %s: %s (property left unchanged)"),
					*ImportStr, *Leaf->GetClass()->GetName(), *ErrText);
			}

			Leaf->DestroyValue(Scratch);
			FMemory::Free(Scratch);
		}

		TSharedRef<FJsonObject> SerializeWriteOutcome(const FWriteOutcome& R)
		{
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("name"), R.Name);
			Row->SetBoolField(TEXT("applied"), R.bApplied);
			Row->SetBoolField(TEXT("changed"), R.bChanged);
			Row->SetBoolField(TEXT("typeValidated"), R.bTypeValidated);
			if (!R.TypeNote.IsEmpty()) { Row->SetStringField(TEXT("typeValidationNote"), R.TypeNote); }
			Row->SetStringField(TEXT("before"), R.Before);
			Row->SetStringField(TEXT("after"), R.After);
			if (!R.Wanted.IsEmpty()) { Row->SetStringField(TEXT("wanted"), R.Wanted); }
			if (!R.Reason.IsEmpty()) { Row->SetStringField(TEXT("reason"), R.Reason); }
			if (R.bApplied && !R.bChanged)
			{
				Row->SetStringField(TEXT("note"), TEXT("value was already this; write is a no-op, not a failure"));
			}
			return Row;
		}

		// The preflight's row shape. Deliberately NOT SerializeWriteOutcome's: that one reports
		// before/after/wanted, and emitting those from a run that wrote nothing would invite the
		// reader to believe a write was attempted and lost. `applied:false` here means NOT ATTEMPTED.
		TSharedRef<FJsonObject> SerializePreflightOutcome(const FWriteOutcome& R, bool bValidated)
		{
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("name"), R.Name);
			Row->SetBoolField(TEXT("validated"), bValidated);
			Row->SetBoolField(TEXT("applied"), false);
			Row->SetBoolField(TEXT("changed"), false);
			Row->SetBoolField(TEXT("typeValidated"), R.bTypeValidated);
			if (!R.TypeNote.IsEmpty()) { Row->SetStringField(TEXT("typeValidationNote"), R.TypeNote); }
			if (!R.Reason.IsEmpty())   { Row->SetStringField(TEXT("reason"), R.Reason); }
			Row->SetStringField(TEXT("stage"), TEXT("preflight"));
			return Row;
		}

		// THE DRY RUN (Batch M). Type-checks every requested property against Probe — an object of the
		// destination CLASS that already exists — and returns how many were refused, with a row for
		// each so a refusal carries the same per-property diagnostics an applied call does.
		// Writes nothing, creates nothing, and is the only thing standing between a bad value and a
		// permanent override on the caller's asset.
		int32 PreflightProperties(UObject* Probe, const TSharedPtr<FJsonObject>& PropsObj, const TArray<FString>& Keys,
			TArray<TSharedPtr<FJsonValue>>& OutRows, TArray<FString>& OutFailedNames)
		{
			int32 Failed = 0;
			for (const FString& Key : Keys)
			{
				FWriteOutcome R;
				R.Name = Key;
				FProperty* Leaf = nullptr; void* Addr = nullptr; UObject* Owner = nullptr; FString ImportStr;
				const bool bValidated = PrepareOneProperty(Probe, Key, PropsObj->Values[Key], Leaf, Addr, Owner, ImportStr, R);
				if (!bValidated)
				{
					++Failed;
					OutFailedNames.Add(Key);
				}
				OutRows.Add(MakeShared<FJsonValueObject>(SerializePreflightOutcome(R, bValidated)));
			}
			return Failed;
		}

		// --- shared resolution front-end ---------------------------------------------------

		// blueprint / blueprintId / path / asset. Not ResolveBlueprintField: that helper reads only
		// "blueprintId" and "path" (MifBridgeCommon.cpp:837-851), so accepting the other two
		// spellings in RejectUnknownParams while routing through it would produce the exact
		// silent-ignore failure the strict-params guard exists to prevent.
		UBlueprint* ResolveBlueprintAliased(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
		{
			const FString Path = JStrAny(In, { TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset") });
			if (Path.TrimStartAndEnd().IsEmpty())
			{
				Fail(Out, TEXT("'blueprint' required (aliases: blueprintId, path, asset) - the CHILD blueprint whose inherited component you are editing"));
				return nullptr;
			}
			FString Error;
			UBlueprint* Blueprint = ResolveBlueprint(Path, Error);
			if (!Blueprint)
			{
				Fail(Out, Error);   // already graded: cooked vs wrong type vs no such package
			}
			return Blueprint;
		}

		FString ReadComponentName(const TSharedRef<FJsonObject>& In)
		{
			return JStrAny(In, { TEXT("component"), TEXT("componentName"), TEXT("name") }).TrimStartAndEnd();
		}

		// Both mutators need the same four things before they may touch the ICH; failing here writes
		// the reason into Out and returns null.
		UInheritableComponentHandler* GetHandlerForOverride(UBlueprint* Blueprint, const FInheritedResolution& Res,
			const TSharedRef<FJsonObject>& Out, bool bCreateIfNecessary)
		{
			// The editor's guard, verbatim (SubobjectData.cpp:152-155). Key.IsValid() needs a valid
			// AssociatedGuid, which is USCS_Node::VariableGuid — a plain UPROPERTY, so it survives
			// cooking, but a malformed parent would otherwise fail deep inside CreateOverriden*.
			if (!Res.Key.IsValid())
			{
				Fail(Out, FString::Printf(
					TEXT("cannot build a component key for '%s': the parent SCS node has no valid VariableGuid (owner '%s'). The parent asset needs re-saving in an editor that runs USCS_Node::ValidateGuid."),
					*Res.ParentNode->GetVariableName().ToString(),
					*GetPathNameSafe(Res.ParentOwnerBPGC)));
				return nullptr;
			}
			if (!Blueprint->ParentClass || !Blueprint->ParentClass->IsChildOf(Res.Key.GetComponentOwner()))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' does not inherit from '%s', so it cannot override that class's component (the editor applies the same test: Blueprint->ParentClass->IsChildOf(Key.GetComponentOwner()))"),
					*Blueprint->GetName(), *GetPathNameSafe(Res.Key.GetComponentOwner())));
				return nullptr;
			}
			if (!Cast<UBlueprintGeneratedClass>(Blueprint->GeneratedClass))
			{
				// GetInheritableComponentHandler(true) does CastChecked on GeneratedClass
				// (Blueprint.cpp:2072) — an uncompiled blueprint would assert inside the engine.
				Fail(Out, FString::Printf(TEXT("'%s' has no UBlueprintGeneratedClass yet; compile it before overriding an inherited component"),
					*Blueprint->GetName()));
				return nullptr;
			}

			UInheritableComponentHandler* ICH = Blueprint->GetInheritableComponentHandler(bCreateIfNecessary);
			if (!ICH && bCreateIfNecessary)
			{
				// The only documented null on the create path (Blueprint.cpp:2062-2068).
				Fail(Out, TEXT("inheritable components are disabled: [Kismet] bEnableInheritableComponents=false in the engine config. With it off, UBlueprint::GetInheritableComponentHandler returns null and USCS_Node::GetActualComponentTemplate ignores overrides at instancing, so the write would be silently discarded."));
			}
			return ICH;
		}

		int32 CountOverrides(UInheritableComponentHandler* ICH)
		{
			if (!ICH) { return 0; }
			TArray<UActorComponent*> Templates;
			ICH->GetAllTemplates(Templates, /*bIncludeTransientTemplates*/ false);
			return Templates.Num();
		}

		// The single sentence that turns the native refusal into an actionable next call.
		FString NativeAlternativeHint(const FInheritedResolution& Res)
		{
			return FString::Printf(
				TEXT("set_property {objectPath:\"%s\", propertyPath:\"<Prop>\", value:\"<v>\"} - a NATIVE component is not an ICH case (the editor's own guard is !IsNativeComponent(), SubobjectData.cpp:148); the child blueprint's CDO already owns its own subobject instance, so you edit it directly. Note the path carries the SUBOBJECT name ('%s'), not the property name."),
				*Res.NativeTemplate->GetPathName(),
				*Res.NativeTemplate->GetName());
		}
	}

	// --- get_inherited_component --------------------------------------------------
	//   in:  { blueprint (blueprintId|path|asset), component (componentName|name) }
	//   out: { blueprint, component, origin: parentBlueprintSCS|native|ownSCS|notFound,
	//          parentClass, componentClass?, ownerClass?, canOverride, canOverrideReason?,
	//          overrideExists, existingOverrideCount, overrideTemplatePath?, parentTemplatePath?,
	//          nativeCdoPath?, nativeMatchedBy?, creationMethod?, ownTemplatePath?,
	//          inheritableComponentHandlerPath?, route, hint,
	//          availableComponents?[], availableComponentCount? }
	//
	// READ-ONLY, and read-only in the strong sense: it asks for the handler with
	// bCreateIfNecessary=FALSE, so merely asking "is this overridden?" never mints an ICH on the
	// asset. That is the whole reason this verb exists separately from the mutator — the engine's
	// own accessor is get-or-CREATE, so any "just checking" call written against the raw API dirties
	// the blueprint. Call this first; it tells you which of four routes applies.
	void H_get_inherited_component(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"),
			  TEXT("component"), TEXT("componentName"), TEXT("name") },
			TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name)")))
		{
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintAliased(In, Out);
		if (!Blueprint) { return; }

		const FString CompName = ReadComponentName(In);
		if (CompName.IsEmpty())
		{
			Fail(Out, TEXT("'component' required (aliases: componentName, name) - the component's variable name as list_components / the Details panel shows it"));
			return;
		}

		FInheritedResolution Res;
		ResolveComponentOrigin(Blueprint, FName(*CompName), Res);

		Out->SetStringField(TEXT("blueprint"), Blueprint->GetPathName());
		Out->SetStringField(TEXT("component"), CompName);
		Out->SetStringField(TEXT("origin"), Res.Origin);
		Out->SetStringField(TEXT("parentClass"), GetPathNameSafe(Blueprint->ParentClass));

		// Non-creating handler read. Reported even when the component is native/own so the caller can
		// see whether this asset carries overrides at all.
		UInheritableComponentHandler* ICH = Blueprint->GetInheritableComponentHandler(/*bCreateIfNecessary*/ false);
		Out->SetStringField(TEXT("inheritableComponentHandlerPath"), ICH ? ICH->GetPathName() : FString());
		Out->SetNumberField(TEXT("existingOverrideCount"), CountOverrides(ICH));

		if (Res.Origin == kComponentOriginParentSCS)
		{
			UActorComponent* ParentTemplate = Res.ParentNode->ComponentTemplate;
			Out->SetStringField(TEXT("ownerClass"), GetPathNameSafe(Res.Key.GetComponentOwner()));
			Out->SetStringField(TEXT("parentTemplatePath"), GetPathNameSafe(ParentTemplate));
			// R1 section 6: none of the inherited-component verbs reported this, so a caller could not
			// tell "this component is not overridable" from "the override failed".
			// UActorComponent::IsEditableWhenInherited() (ActorComponent.h:356) is the engine's own
			// answer and returns false for anything a User Construction Script created
			// (ActorComponent.cpp:2243-2246). Reported BESIDE canOverride rather than folded into it:
			// the bridge's write path does not test it, and a read that predicts a refusal which never
			// happens is its own kind of wrong answer.
			Out->SetBoolField(TEXT("editableWhenInherited"),
				ParentTemplate ? ParentTemplate->IsEditableWhenInherited() : true);
			if (Res.ParentNode->ComponentClass)
			{
				Out->SetStringField(TEXT("componentClass"), Res.ParentNode->ComponentClass->GetPathName());
			}

			const bool bKeyOk = Res.Key.IsValid();
			const bool bChildOk = Blueprint->ParentClass && Blueprint->ParentClass->IsChildOf(Res.Key.GetComponentOwner());
			const bool bCanOverride = bKeyOk && bChildOk;
			Out->SetBoolField(TEXT("canOverride"), bCanOverride);
			if (!bCanOverride)
			{
				Out->SetStringField(TEXT("canOverrideReason"), !bKeyOk
					? TEXT("component key invalid (parent SCS node has no valid VariableGuid)")
					: TEXT("this blueprint's ParentClass is not a child of the class that owns the component"));
			}

			// Fast path per correction 1: FindKey(FName) only ever finds a key for a record that
			// ALREADY exists (InheritableComponentHandler.cpp:509-519), so it is safe here and
			// useless for creation. The authoritative lookup is still the key built from the node.
			UActorComponent* Existing = nullptr;
			if (ICH && bCanOverride)
			{
				const FComponentKey FastKey = ICH->FindKey(FName(*CompName));
				Existing = FastKey.IsValid() ? ICH->GetOverridenComponentTemplate(FastKey) : nullptr;
				if (!Existing) { Existing = ICH->GetOverridenComponentTemplate(Res.Key); }
			}
			Out->SetBoolField(TEXT("overrideExists"), Existing != nullptr);
			if (Existing)
			{
				Out->SetStringField(TEXT("overrideTemplatePath"), Existing->GetPathName());
				Out->SetStringField(TEXT("route"), TEXT("set_property"));
				Out->SetStringField(TEXT("hint"), FString::Printf(
					TEXT("override already exists - write to it directly with set_property {objectPath:\"%s\", ...}, or use override_inherited_component to apply several properties at once. revert_inherited_component discards it and falls back to the parent's '%s'."),
					*Existing->GetPathName(), *GetPathNameSafe(ParentTemplate)));
			}
			else
			{
				Out->SetStringField(TEXT("route"), TEXT("override_inherited_component"));
				Out->SetStringField(TEXT("hint"), bCanOverride
					? FString::Printf(TEXT("no override yet - call override_inherited_component {blueprint, component:\"%s\", properties:{...}} to mint one and write it in a single transacted call. Reads still come from the parent's '%s' until then."),
						*CompName, *GetPathNameSafe(ParentTemplate))
					: TEXT("no override, and this blueprint cannot create one - see canOverrideReason."));
			}
		}
		else if (Res.Origin == kComponentOriginNative)
		{
			Out->SetBoolField(TEXT("canOverride"), false);
			Out->SetStringField(TEXT("canOverrideReason"),
				TEXT("native component: inherited from a C++ parent class, not from a parent Blueprint's SCS. UInheritableComponentHandler does not apply (SubobjectData.cpp:148 excludes it) and this blueprint's CDO already owns its own instance."));
			Out->SetBoolField(TEXT("overrideExists"), false);
			Out->SetStringField(TEXT("nativeCdoPath"), Res.NativeTemplate->GetPathName());
			Out->SetStringField(TEXT("nativeMatchedBy"), Res.NativeMatchedBy);
			Out->SetStringField(TEXT("componentClass"), Res.NativeTemplate->GetClass()->GetPathName());
			Out->SetStringField(TEXT("creationMethod"), ComponentCreationMethodString(Res.NativeTemplate));
			Out->SetStringField(TEXT("route"), TEXT("set_property"));
			Out->SetStringField(TEXT("hint"), NativeAlternativeHint(Res));
		}
		else if (Res.Origin == kComponentOriginOwnSCS)
		{
			UActorComponent* OwnTemplate = Res.OwnNode->ComponentTemplate;
			Out->SetBoolField(TEXT("canOverride"), false);
			Out->SetStringField(TEXT("canOverrideReason"),
				TEXT("component is declared in THIS blueprint's own SimpleConstructionScript - it is not inherited, so there is nothing to delta against."));
			Out->SetBoolField(TEXT("overrideExists"), false);
			Out->SetStringField(TEXT("ownTemplatePath"), GetPathNameSafe(OwnTemplate));
			if (Res.OwnNode->ComponentClass)
			{
				Out->SetStringField(TEXT("componentClass"), Res.OwnNode->ComponentClass->GetPathName());
			}
			Out->SetStringField(TEXT("route"), TEXT("set_property"));
			Out->SetStringField(TEXT("hint"), FString::Printf(
				TEXT("edit the template directly: set_property {objectPath:\"%s\", propertyPath:\"<Prop>\", value:\"<v>\"} (docs/02_GOTCHAS.md §5d). list_components reports the same path as templatePath."),
				*GetPathNameSafe(OwnTemplate)));
		}
		else
		{
			Out->SetBoolField(TEXT("canOverride"), false);
			Out->SetBoolField(TEXT("overrideExists"), false);
			Out->SetStringField(TEXT("route"), TEXT("none"));
			Out->SetStringField(TEXT("hint"), FString::Printf(
				TEXT("no component named '%s' in this blueprint's SCS, in any parent blueprint's SCS, or among the CDO's native subobjects - see availableComponents."),
				*CompName));
			GatherAvailableComponents(Blueprint, Out, /*Cap*/ 80);
		}
	}

	// --- override_inherited_component ----------------------------------------------
	//   in:  { blueprint (blueprintId|path|asset), component (componentName|name),
	//          properties? (props) : { "<PropOrDotPath>": <string|number|bool> }, confirm? }
	//   out: { blueprint, component, origin, created, overrideTemplatePath, parentTemplatePath,
	//          componentClass, existingOverrideCount, propertiesRequested, propertiesApplied,
	//          propertiesFailed, propertiesUnchanged, properties: [...], dirtyPath, warning? }
	//
	// TRANSACTED (see the bucket note at the top of this file): it modifies a Blueprint asset but
	// runs no compile, and every step is Modify()-able, so RunEndpoint's blanket transaction is both
	// sufficient and desirable. Get-or-creates the override template via the canonical editor path,
	// then applies each property with the PM-003 scratch bracket and VERIFIES the result by
	// re-export. A property that did not land is reported failed with the reason, never as success.
	//
	// `confirm` is accepted but NOT required: minting an override is reversible
	// (revert_inherited_component). It is honoured rather than ignored - passing confirm:false is
	// treated as an explicit refusal, because a parameter the handler silently drops is the #1 bug
	// class in this codebase (docs/audit/03_GAPS_AND_RISKS.md §7.1).
	void H_override_inherited_component(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"),
			  TEXT("component"), TEXT("componentName"), TEXT("name"),
			  TEXT("properties"), TEXT("props"), TEXT("confirm") },
			TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name), properties (alias: props), confirm"),
			{ { TEXT("propertyPath"), TEXT("this endpoint takes a 'properties' OBJECT (name -> value); use set_property for a single dot-path write against the returned overrideTemplatePath") },
			  { TEXT("value"),        TEXT("this endpoint takes a 'properties' OBJECT (name -> value); use set_property for a single named write") } }))
		{
			return;
		}

		if (JHasAny(In, { TEXT("confirm") }) && !JBool(In, TEXT("confirm"), true))
		{
			Fail(Out, TEXT("confirm=false - refusing. (confirm is optional here: minting an override is reversible with revert_inherited_component. It is honoured rather than ignored.)"));
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintAliased(In, Out);
		if (!Blueprint) { return; }

		const FString CompName = ReadComponentName(In);
		if (CompName.IsEmpty())
		{
			Fail(Out, TEXT("'component' required (aliases: componentName, name)"));
			return;
		}

		FInheritedResolution Res;
		ResolveComponentOrigin(Blueprint, FName(*CompName), Res);
		Out->SetStringField(TEXT("blueprint"), Blueprint->GetPathName());
		Out->SetStringField(TEXT("component"), CompName);
		Out->SetStringField(TEXT("origin"), Res.Origin);

		// Correction 3: refuse the native case with the route that actually works, resolved from the
		// real object. Writing the ICH here would create a record against a node that does not exist.
		if (Res.Origin == kComponentOriginNative)
		{
			Out->SetStringField(TEXT("nativeCdoPath"), Res.NativeTemplate->GetPathName());
			Out->SetStringField(TEXT("nativeMatchedBy"), Res.NativeMatchedBy);
			Out->SetStringField(TEXT("creationMethod"), ComponentCreationMethodString(Res.NativeTemplate));
			Fail(Out, FString::Printf(TEXT("'%s' is a NATIVE inherited component (C++ parent), not a parent-Blueprint SCS component - there is no ICH override for it. Use %s"),
				*CompName, *NativeAlternativeHint(Res)));
			return;
		}
		if (Res.Origin == kComponentOriginOwnSCS)
		{
			Out->SetStringField(TEXT("ownTemplatePath"), GetPathNameSafe(Res.OwnNode->ComponentTemplate));
			Fail(Out, FString::Printf(TEXT("'%s' is declared in THIS blueprint's own SCS, not inherited - edit its template directly: set_property {objectPath:\"%s\", propertyPath:\"<Prop>\", value:\"<v>\"}"),
				*CompName, *GetPathNameSafe(Res.OwnNode->ComponentTemplate)));
			return;
		}
		if (Res.Origin != kComponentOriginParentSCS)
		{
			GatherAvailableComponents(Blueprint, Out, /*Cap*/ 80);
			Fail(Out, FString::Printf(TEXT("no component named '%s' in this blueprint, any parent blueprint's SCS, or the CDO's native subobjects - see availableComponents"), *CompName));
			return;
		}

		// ============================================================================================
		// BATCH M — VALIDATE, THEN CREATE. The ORDER below is the fix; it is not a style preference.
		//
		// LIVE EVIDENCE (2026-07-29). With Batch L's validator in place,
		//   override_inherited_component {component:"Influence", properties:{"SphereRadius":"not-a-float"}}
		// correctly returned ok:false — and the ICH override template it had minted BEFORE validating
		// was STILL THERE afterwards: a follow-up get_inherited_component reported overrideExists:true.
		// A FAILED call had permanently added an override to the user's Blueprint. The child now
		// shadowed the parent for that component: a silent behaviour change to their asset, from a
		// call that told them it had failed.
		//
		// WHY RunEndpoint's Transaction.Cancel() DID NOT SAVE IT. Two independent reasons, either one
		// sufficient on its own. Both read out of the engine at D:/UE532:
		//
		//  1. NOTHING WAS EVER RECORDED. SaveToTransactionBuffer stores an object only when it has
		//     RF_Transactional (UObjectGlobals.cpp:3131-3134), and NEITHER object here does: the
		//     handler is NewObject<UInheritableComponentHandler>(this, ...) with default flags
		//     (BlueprintGeneratedClass.cpp:1202), and the override template is
		//     NewObject<UActorComponent>(..., RF_ArchetypeObject|RF_Public|RF_InheritableComponentTemplate, ...)
		//     (InheritableComponentHandler.cpp:159-160). ICH->Modify() below therefore dirties the
		//     package and broadcasts OnObjectModified and stores NOTHING for undo.
		//
		//  2. Cancel DOES NOT REVERT ANYTHING — for any object, transactional or not.
		//     UTransBuffer::Cancel (EditorTransaction.cpp:1387-1437) broadcasts TransactionCanceled,
		//     calls GUndo->EndOperation(), nulls GUndo and POPS the entry off UndoBuffer. It never
		//     calls FTransaction::Apply(); the only two callers of Apply in the whole transaction
		//     system are UTransBuffer::Undo (:1624) and ::Redo (:1688). The engine's own doc says the
		//     same thing: "Cancels the current transaction, no longer capture actions to be placed in
		//     the undo buffer" (Editor/Transactor.h:514-519). Cancel means DISCARD THE UNDO ENTRY.
		//     It has never meant ROLL BACK.
		//
		// So the central "a failed call is atomic" guarantee was never true — it had simply never been
		// exercised, which docs/audit/06_IMPLEMENTED.md said in as many words ("STILL UNPROVEN ... no
		// call had been found that MUTATES and then GENUINELY FAILS"). This endpoint became the first
		// such call, and it disproved the guarantee on its first run.
		//
		// THE ONLY MECHANISM THAT ACTUALLY WORKS IS ORDER. Everything needed to refuse a bad value is
		// available WITHOUT minting anything: the destination class is the parent template's class, so
		// every property name resolves and every value type-checks against the archetype the override
		// would have been duplicated from. Validate all of them; mint only if all of them pass.
		// ============================================================================================

		// --- 1. GUARDS, WITH NOTHING CREATED ----------------------------------------------
		// The same helper the real fetch uses, called with bCreateIfNecessary=FALSE so a refusal here
		// mints neither a handler nor a template. It writes its reason into Out and returns null on
		// key/parentage/generated-class failure — and ALSO returns null, with Out still ok, when the
		// blueprint simply has no handler yet. IsOk separates the two, exactly as
		// revert_inherited_component does it.
		UInheritableComponentHandler* ExistingICH = GetHandlerForOverride(Blueprint, Res, Out, /*bCreateIfNecessary*/ false);
		if (!IsOk(Out)) { return; }

		UActorComponent* ParentTemplate = Res.ParentNode->ComponentTemplate;
		Out->SetStringField(TEXT("ownerClass"), GetPathNameSafe(Res.Key.GetComponentOwner()));
		Out->SetStringField(TEXT("parentTemplatePath"), GetPathNameSafe(ParentTemplate));

		UActorComponent* ExistingOverride = ExistingICH ? ExistingICH->GetOverridenComponentTemplate(Res.Key) : nullptr;
		Out->SetBoolField(TEXT("overrideExisted"), ExistingOverride != nullptr);

		// TMap iteration order is not insertion order; sort so the response rows and the log are
		// reproducible across calls with the same input.
		const TSharedPtr<FJsonObject>* PropsObj = nullptr;
		if (!In->TryGetObjectField(TEXT("properties"), PropsObj))
		{
			In->TryGetObjectField(TEXT("props"), PropsObj);
		}
		TArray<FString> Keys;
		if (PropsObj && PropsObj->IsValid())
		{
			(*PropsObj)->Values.GetKeys(Keys);
			Keys.Sort();
		}

		// --- 2. THE PREFLIGHT -------------------------------------------------------------
		if (Keys.Num() > 0)
		{
			// WHICH OBJECT TO CHECK AGAINST. When an override already exists it IS the write target,
			// so use it. Otherwise the parent's template: CreateOverridenComponentTemplate builds the
			// new one with NewObject<UActorComponent>(GetOuter(), BestArchetype->GetClass(), ...,
			// BestArchetype) (InheritableComponentHandler.cpp:159-160), i.e. a copy of that archetype
			// — same class, same struct layout, same array lengths, same non-null inner objects. Every
			// question the preflight asks (does this name resolve? is it EditConst? is this text a
			// legal value for that FProperty?) has the same answer on both.
			UActorComponent* Probe = ExistingOverride ? ExistingOverride : ParentTemplate;
			if (!Probe)
			{
				Out->SetBoolField(TEXT("created"), false);
				Out->SetBoolField(TEXT("nothingModified"), true);
				Out->SetStringField(TEXT("outcome"), TEXT("preflight-rejected-nothing-created"));
				Fail(Out, FString::Printf(
					TEXT("the parent's SCS node for '%s' (owner '%s') has no ComponentTemplate, so the values cannot be type-checked - and CreateOverridenComponentTemplate would find no archetype either. NOTHING WAS CREATED OR MODIFIED. Re-save the parent asset."),
					*CompName, *GetPathNameSafe(Res.Key.GetComponentOwner())));
				return;
			}

			TArray<TSharedPtr<FJsonValue>> PreRows;
			TArray<FString> BadNames;
			const int32 BadCount = PreflightProperties(Probe, *PropsObj, Keys, PreRows, BadNames);
			if (BadCount > 0)
			{
				Out->SetArrayField(TEXT("properties"), PreRows);
				Out->SetNumberField(TEXT("propertiesRequested"), Keys.Num());
				Out->SetNumberField(TEXT("propertiesApplied"), 0);
				Out->SetNumberField(TEXT("propertiesFailed"), BadCount);
				Out->SetNumberField(TEXT("propertiesUnchanged"), 0);
				Out->SetNumberField(TEXT("existingOverrideCount"), CountOverrides(ExistingICH));
				Out->SetStringField(TEXT("validatedAgainst"), Probe->GetPathName());
				Out->SetBoolField(TEXT("created"), false);
				Out->SetBoolField(TEXT("nothingModified"), true);
				Out->SetStringField(TEXT("outcome"), TEXT("preflight-rejected-nothing-created"));
				Fail(Out, FString::Printf(
					TEXT("%d of %d properties are invalid (%s) - see properties[] for the per-property reason. NOTHING WAS CREATED OR MODIFIED: every value is type-checked against %s BEFORE any override is minted, so this blueprint is exactly as it was before the call%s. Fix the values and call again."),
					BadCount, Keys.Num(), *FString::Join(BadNames, TEXT(", ")), *Probe->GetPathName(),
					ExistingOverride
						? TEXT(", and the override that already existed is untouched")
						: TEXT("")));
				return;
			}
		}

		// --- 3. MUTATE ---------------------------------------------------------------------
		// Modify BEFORE the handler is fetched, not after: GetInheritableComponentHandler(true)
		// ASSIGNS Blueprint->InheritableComponentHandler on a blueprint that had none
		// (Blueprint.cpp:2070-2074), so a Modify() taken afterwards would snapshot the pointer as
		// already-set. (UBlueprint IS RF_Transactional, so unlike the ICH this one really is recorded
		// — but see the header block above: recorded is not the same as reverted-on-cancel.)
		Blueprint->Modify();

		const bool bMintedHandler = (ExistingICH == nullptr);
		UInheritableComponentHandler* ICH = GetHandlerForOverride(Blueprint, Res, Out, /*bCreateIfNecessary*/ true);
		if (!ICH) { return; }   // config gate; reason already written, and nothing was created
		ICH->Modify();          // the Records array is about to change

		UActorComponent* Override = ICH->GetOverridenComponentTemplate(Res.Key);
		const bool bCreated = (Override == nullptr);
		if (!Override)
		{
			Override = ICH->CreateOverridenComponentTemplate(Res.Key);
		}
		if (!Override)
		{
			// The engine's own failure mode: FindBestArchetype could not resolve a template
			// (InheritableComponentHandler.cpp:120-125 logs the same condition). Note the one thing
			// this path CAN leave behind, rather than letting the caller discover it: the engine's
			// loop over Records drops a record whose ComponentTemplate is null before it gives up
			// (:107-117), and that drop is not transaction-recorded either.
			Out->SetBoolField(TEXT("created"), false);
			Out->SetStringField(TEXT("outcome"), TEXT("create-returned-null"));
			Fail(Out, FString::Printf(
				TEXT("CreateOverridenComponentTemplate returned null for '%s' (owner '%s') - no archetype could be found for the component. The parent's SCS node exists but its ComponentTemplate is missing; re-save the parent asset. No override template was created%s. If a record for this key existed with a null template, the engine removed it on the way through (InheritableComponentHandler.cpp:107-117) - re-check with get_inherited_component."),
				*CompName, *GetPathNameSafe(Res.Key.GetComponentOwner()),
				bMintedHandler
					? TEXT(", but an empty UInheritableComponentHandler was assigned to this blueprint on the way in - zero records, no behavioural effect; it is the same object the editor creates the first time you override anything, and Ctrl-Z does not remove it")
					: TEXT("")));
			return;
		}

		// Captured before any cleanup: RemoveOverridenComponentTemplate MarkAsGarbage()es the object.
		const FString OverridePath = Override->GetPathName();
		Out->SetBoolField(TEXT("created"), bCreated);
		Out->SetStringField(TEXT("overrideTemplatePath"), OverridePath);
		Out->SetStringField(TEXT("componentClass"), Override->GetClass()->GetPathName());

		// --- 4. APPLY --------------------------------------------------------------------
		TArray<TSharedPtr<FJsonValue>> Rows;
		int32 Requested = 0, Applied = 0, Failed = 0, Unchanged = 0;
		TArray<FString> FailedNames;
		bool bTouchedEditableWhenInherited = false;

		if (Keys.Num() > 0)
		{
			Override->Modify();

			for (const FString& Key : Keys)
			{
				++Requested;
				FWriteOutcome R;
				R.Name = Key;

				ApplyOneProperty(Override, Key, (*PropsObj)->Values[Key], R);

				if (R.bApplied) { ++Applied; if (!R.bChanged) { ++Unchanged; } }
				else            { ++Failed; FailedNames.Add(Key); }

				if (Key.Equals(TEXT("bEditableWhenInherited"), ESearchCase::IgnoreCase))
				{
					bTouchedEditableWhenInherited = true;
				}
				Rows.Add(MakeShared<FJsonValueObject>(SerializeWriteOutcome(R)));
			}
		}

		// Value change on a component TEMPLATE, not a class-layout change: MarkBlueprintAsModified,
		// never MarkBlueprintAsStructurallyModified. Structural runs
		// FBlueprintCompilationManager::CompileSynchronously(RegenerateSkeletonOnly)
		// (BlueprintEditorUtils.cpp:1814-1816) to rebuild the class's VARIABLE set; an ICH override
		// adds no variable - the component variable is already inherited - so the skeleton regen
		// would be pure cost. MarkBlueprintAsModified (:1831-1895) does what this change needs:
		// BS_Dirty + MarkPackageDirty + UpdateCustomPropertyListForPostConstruction on the BPGC and
		// its children, which is the cached list consulted when instancing components. And the
		// override needs no compile to take effect at all - USCS_Node::GetActualComponentTemplate
		// (SCS_Node.cpp:29-54) reads the ICH chain live at instancing time.
		FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
		Out->SetStringField(TEXT("dirtyPath"), TEXT("FBlueprintEditorUtils::MarkBlueprintAsModified"));

		Out->SetArrayField(TEXT("properties"), Rows);
		Out->SetNumberField(TEXT("propertiesRequested"), Requested);
		Out->SetNumberField(TEXT("propertiesApplied"), Applied);
		Out->SetNumberField(TEXT("propertiesFailed"), Failed);
		Out->SetNumberField(TEXT("propertiesUnchanged"), Unchanged);
		Out->SetNumberField(TEXT("existingOverrideCount"), CountOverrides(ICH));

		if (bTouchedEditableWhenInherited)
		{
			// UActorComponent::CanEditChange gates every property on the ARCHETYPE's
			// bEditableWhenInherited (ActorComponent.cpp:2203-2207), and
			// FBlueprintEditorUtils::HandleDisableEditableWhenInherited
			// (BlueprintEditorUtils.cpp:9917-9933) REMOVES ICH override records on archetype
			// instances when it is turned off. Writing it can therefore delete overrides in classes
			// derived from this one. Surfaced, not blocked - it is a legitimate thing to set.
			Out->SetStringField(TEXT("warning"),
				TEXT("bEditableWhenInherited was written: FBlueprintEditorUtils::HandleDisableEditableWhenInherited removes ICH override records on derived classes when it is turned off, and UActorComponent::CanEditChange then locks the component in every child. Re-check any derived blueprint that overrides this component."));
		}

		if (Requested == 0)
		{
			Out->SetStringField(TEXT("hint"), FString::Printf(
				TEXT("override minted, no properties requested - write to it with set_property {objectPath:\"%s\", propertyPath:\"<Prop>\", value:\"<v>\"}"),
				*OverridePath));
		}

		UE_LOG(LogMifBridge, Log, TEXT("override_inherited_component: %s.%s -> %s (created=%d, applied=%d/%d)"),
			*Blueprint->GetName(), *CompName, *OverridePath, bCreated ? 1 : 0, Applied, Requested);

		// Anti-silence: a partially-failed batch must not return ok:true. The per-property rows stay
		// in Out (Fail only sets ok/error), so the caller sees exactly which ones landed.
		if (Failed > 0)
		{
			// BELT AND BRACES (Batch M). The preflight refuses everything it can predict from the
			// destination type. This is the residue it CANNOT predict: a UPROPERTY meta ClampMin, a
			// component's own PostEditChangeProperty that snaps or rejects the value, a CanEditChange
			// that quietly refuses. Since Cancel() will not undo the creation (header block above),
			// the handler undoes it itself.
			//
			// THE RULE IS NARROW ON PURPOSE: remove the override ONLY when THIS call created it.
			// Removing one the caller already had would discard overrides they never asked us to
			// touch — which is exactly why revert_inherited_component is confirm-gated.
			FString Tail;
			if (bCreated)
			{
				ICH->RemoveOverridenComponentTemplate(Res.Key);
				// The field would now name a MarkAsGarbage'd object; renaming it is the honest report.
				Out->RemoveField(TEXT("overrideTemplatePath"));
				Out->SetStringField(TEXT("removedTemplatePath"), OverridePath);
				Out->SetBoolField(TEXT("overrideRemovedOnFailure"), true);
				Out->SetNumberField(TEXT("existingOverrideCount"), CountOverrides(ICH));
				Out->SetStringField(TEXT("outcome"), TEXT("created-then-removed-on-failure"));
				Tail = FString::Printf(
					TEXT(" The override this call minted (%s) has been REMOVED again, so '%s' reads from the parent's template %s exactly as it did before the call.%s"),
					*OverridePath, *CompName, *GetPathNameSafe(ParentTemplate),
					bMintedHandler
						? TEXT(" ONE residue is left and there is no engine API to remove it: the empty UInheritableComponentHandler this call assigned to the blueprint. It holds zero records and has no behavioural effect - it is the same object the editor creates the first time you override anything.")
						: TEXT(""));
			}
			else
			{
				Out->SetBoolField(TEXT("overrideRemovedOnFailure"), false);
				Out->SetStringField(TEXT("outcome"), TEXT("pre-existing-override-kept"));
				Tail = FString::Printf(
					TEXT(" The override at %s ALREADY EXISTED before this call and has deliberately NOT been removed - deleting it would discard overrides you did not ask to change. Any property row above with applied:true DID land on it, so re-read it; revert_inherited_component {confirm:true} discards the whole record if that is what you want."),
					*OverridePath);
			}

			Fail(Out, FString::Printf(
				TEXT("%d of %d properties did not apply (%s) - see properties[] for the per-property reason. Every value passed the pre-flight type check, so these were refused by the engine itself (clamp, PostEditChangeProperty, CanEditChange).%s"),
				Failed, Requested, *FString::Join(FailedNames, TEXT(", ")), *Tail));
		}
		else
		{
			Out->SetStringField(TEXT("outcome"), bCreated ? TEXT("created") : TEXT("updated-existing"));
		}
	}

	// --- revert_inherited_component ------------------------------------------------
	//   in:  { blueprint (blueprintId|path|asset), component (componentName|name), confirm:true }
	//   out: { blueprint, component, origin, reverted, removedTemplatePath, fallsBackTo,
	//          remainingOverrideCount, dirtyPath, note }
	//
	// TRANSACTED, same reasoning as the override endpoint (no compile). confirm-gated because it
	// DISCARDS every property the child had overridden on that component in one step - there is no
	// per-property undo inside the record.
	//
	// KNOWN CAVEAT, stated rather than hidden: UInheritableComponentHandler::RemoveOverridenComponentTemplate
	// (InheritableComponentHandler.cpp:200-211) calls MarkAsGarbage() on the template, and
	// MarkAsGarbage is an object-flag operation (UObjectBaseUtility.h:263) that the transaction
	// buffer does not record. A Ctrl-Z restores the Records array but the restored record points at
	// a garbage-flagged template. The engine anticipates exactly this - CreateOverridenComponentTemplate's
	// `if (!::IsValid(NewComponentTemplate))` branch (InheritableComponentHandler.cpp:161-169,
	// commented "HACK ... we mark them pending kill so we can identify that situation here") clears
	// the flag and re-copies from the archetype - so the reliable recovery is to call
	// override_inherited_component again rather than to rely on undo alone.
	void H_revert_inherited_component(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"),
			  TEXT("component"), TEXT("componentName"), TEXT("name"), TEXT("confirm") },
			TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name), confirm")))
		{
			return;
		}

		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("revert_inherited_component requires confirm=true - it discards EVERY property overridden on that component in one step"));
			return;
		}

		UBlueprint* Blueprint = ResolveBlueprintAliased(In, Out);
		if (!Blueprint) { return; }

		const FString CompName = ReadComponentName(In);
		if (CompName.IsEmpty())
		{
			Fail(Out, TEXT("'component' required (aliases: componentName, name)"));
			return;
		}

		FInheritedResolution Res;
		ResolveComponentOrigin(Blueprint, FName(*CompName), Res);
		Out->SetStringField(TEXT("blueprint"), Blueprint->GetPathName());
		Out->SetStringField(TEXT("component"), CompName);
		Out->SetStringField(TEXT("origin"), Res.Origin);

		if (Res.Origin == kComponentOriginNative)
		{
			Out->SetStringField(TEXT("nativeCdoPath"), Res.NativeTemplate->GetPathName());
			Fail(Out, FString::Printf(TEXT("'%s' is a NATIVE inherited component - it has no ICH override to revert. Its values live on this blueprint's CDO subobject (%s); reset them with set_property."),
				*CompName, *Res.NativeTemplate->GetPathName()));
			return;
		}
		if (Res.Origin == kComponentOriginOwnSCS)
		{
			Fail(Out, FString::Printf(TEXT("'%s' is declared in THIS blueprint's own SCS - there is no inherited value to fall back to. Use remove_component to delete it, or set_property on %s to change it."),
				*CompName, *GetPathNameSafe(Res.OwnNode->ComponentTemplate)));
			return;
		}
		if (Res.Origin != kComponentOriginParentSCS)
		{
			GatherAvailableComponents(Blueprint, Out, /*Cap*/ 80);
			Fail(Out, FString::Printf(TEXT("no component named '%s' in this blueprint, any parent blueprint's SCS, or the CDO's native subobjects - see availableComponents"), *CompName));
			return;
		}

		// bCreateIfNecessary=FALSE: reverting when nothing is overridden must not mint a handler.
		UInheritableComponentHandler* ICH = GetHandlerForOverride(Blueprint, Res, Out, /*bCreateIfNecessary*/ false);
		if (!IsOk(Out)) { return; }   // key/parentage/generated-class failure already reported

		UActorComponent* Existing = ICH ? ICH->GetOverridenComponentTemplate(Res.Key) : nullptr;
		Out->SetStringField(TEXT("fallsBackTo"), GetPathNameSafe(Res.ParentNode->ComponentTemplate));
		if (!Existing)
		{
			Out->SetBoolField(TEXT("reverted"), false);
			Out->SetNumberField(TEXT("remainingOverrideCount"), CountOverrides(ICH));
			Fail(Out, FString::Printf(TEXT("'%s' has no override in '%s' - nothing to revert (it already reads from the parent's template %s)"),
				*CompName, *Blueprint->GetName(), *GetPathNameSafe(Res.ParentNode->ComponentTemplate)));
			return;
		}

		const FString RemovedPath = Existing->GetPathName();

		Blueprint->Modify();
		ICH->Modify();
		ICH->RemoveOverridenComponentTemplate(Res.Key);

		// Same dirty path as the override endpoint, same reason: the record set changed, the class
		// layout did not. See the block comment in H_override_inherited_component.
		FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);

		Out->SetBoolField(TEXT("reverted"), true);
		Out->SetStringField(TEXT("removedTemplatePath"), RemovedPath);
		Out->SetNumberField(TEXT("remainingOverrideCount"), CountOverrides(ICH));
		Out->SetStringField(TEXT("dirtyPath"), TEXT("FBlueprintEditorUtils::MarkBlueprintAsModified"));
		Out->SetStringField(TEXT("note"),
			TEXT("the removed template is MarkAsGarbage'd by the engine and that flag is NOT transaction-recorded; to restore the override after an undo, call override_inherited_component again rather than relying on Ctrl-Z alone"));

		UE_LOG(LogMifBridge, Log, TEXT("revert_inherited_component: %s.%s removed %s"),
			*Blueprint->GetName(), *CompName, *RemovedPath);
	}
}
