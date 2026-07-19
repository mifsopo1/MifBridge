// MifBridge — generic reflection property setter (set_property).
// Mirrors the Details-panel write path: resolve target -> dot-walk -> ImportText_Direct
// -> Modify/PreEditChange/PostEditChangeProperty. Editor-only (PostEditChange is WITH_EDITOR).
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "UObject/UnrealType.h"        // FProperty, FStructProperty, FObjectProperty, ImportText_Direct
#include "UObject/Class.h"             // UStruct::FindPropertyByName
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"    // StaticLoadObject
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"

#include "WidgetBlueprint.h"
#include "Blueprint/WidgetTree.h"
#include "Components/Widget.h"
#include "Kismet2/BlueprintEditorUtils.h"   // MarkBlueprintAsModified
#include "Kismet2/KismetEditorUtilities.h"  // CompileBlueprint

namespace MifBridge
{
	// FBoolProperty::ImportText is CASE-SENSITIVE and word-based (PropertyBool.cpp):
	// 1/True/Yes => true, 0/False/No => false. Lowercase true/false FAIL. Normalize.
	static FString NormalizeBoolLiteral(const FString& In)
	{
		const FString T = In.TrimStartAndEnd();
		if (T.Equals(TEXT("true"),  ESearchCase::IgnoreCase)) return TEXT("True");
		if (T.Equals(TEXT("false"), ESearchCase::IgnoreCase)) return TEXT("False");
		return In;
	}

	// Walk "A.B.C" from Object, descending FStructProperty in-place and hopping FObjectProperty
	// to the pointed-to UObject. Returns the leaf FProperty + its VALUE address + the object that
	// PreEdit/PostEdit must fire on. Dynamic containers (TArray/TMap/TSet) are NOT supported.
	static bool ResolvePropertyPath(UObject* Object, const FString& Path,
		FProperty*& OutLeaf, void*& OutLeafAddr, UObject*& OutLeafOwner, FString& OutError)
	{
		if (!Object) { OutError = TEXT("null target object"); return false; }

		TArray<FString> Segs;
		Path.ParseIntoArray(Segs, TEXT("."), true);
		if (Segs.Num() == 0) { OutError = TEXT("empty propertyPath"); return false; }

		UStruct* CurStruct = Object->GetClass();  // container type
		void*    Container = Object;              // container base ptr (UObject* at top level)
		UObject* LeafOwner = Object;              // object PostEditChange fires on

		for (int32 i = 0; i < Segs.Num(); ++i)
		{
			FProperty* Prop = CurStruct->FindPropertyByName(FName(*Segs[i]));   // direct members only
			if (!Prop)
			{
				OutError = FString::Printf(TEXT("property '%s' not found on '%s'"), *Segs[i], *CurStruct->GetName());
				return false;
			}
			// VALUE address, not container — ImportText_Direct requires this.
			void* ValuePtr = Prop->ContainerPtrToValuePtr<void>(Container);

			if (i == Segs.Num() - 1)
			{
				OutLeaf = Prop; OutLeafAddr = ValuePtr; OutLeafOwner = LeafOwner;
				return true;
			}

			if (FStructProperty* SP = CastField<FStructProperty>(Prop))
			{
				// Descend struct in-place — same memory, new struct type.
				CurStruct = SP->Struct;
				Container = ValuePtr;     // struct memory becomes the next container
			}
			else if (FObjectPropertyBase* OP = CastField<FObjectPropertyBase>(Prop))
			{
				// Cross an object boundary — READ the inner UObject, then continue on its class
				// with it as the new container + new edit owner.
				UObject* Inner = OP->GetObjectPropertyValue(ValuePtr);
				if (!Inner)
				{
					OutError = FString::Printf(TEXT("object property '%s' is null; cannot descend"), *Segs[i]);
					return false;
				}
				CurStruct = Inner->GetClass();
				Container = Inner;
				LeafOwner = Inner;
			}
			else
			{
				OutError = FString::Printf(TEXT("segment '%s' is not a struct or object ref (arrays/maps/sets unsupported)"), *Segs[i]);
				return false;
			}
		}
		OutError = TEXT("path traversal fell through");
		return false;
	}

	//   in:  { objectPath: "/Game/..." }  OR  { blueprintId: "...", widgetName: "MyText" }
	//        propertyPath: "A.B.C" (dot path),  value: "..." (string; bools => True/False/1/0)
	//   out: { target, propertyPath, applied:true, leafProperty, recompiled? }
	// Registered as self-managed (RunEndpoint opens NO transaction) because the widget-BP branch
	// calls CompileBlueprint, which must not run inside a transaction. We open a tight inner
	// transaction around ONLY the reflection write; the compile happens after it closes.
	void H_set_property(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString PropertyPath = JStr(In, TEXT("propertyPath"));
		if (PropertyPath.IsEmpty()) { Fail(Out, TEXT("propertyPath required (dot path, e.g. Font.Size)")); return; }
		if (!In->HasField(TEXT("value"))) { Fail(Out, TEXT("value required (string)")); return; }
		const FString Value = JStr(In, TEXT("value"));

		// --- Resolve the target object ------------------------------------------------
		UObject*          Target        = nullptr;
		UWidgetBlueprint* OwningWidgetBP = nullptr;   // set only for the widget-in-BP path

		const FString ObjectPath = JStr(In, TEXT("objectPath"));
		const FString WidgetName = JStr(In, TEXT("widgetName"));

		if (!ObjectPath.IsEmpty())
		{
			FString P = ObjectPath; P.TrimStartAndEndInline();
			Target = StaticLoadObject(UObject::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Target && !P.Contains(TEXT(".")))
			{
				// Accept a bare package path like /Game/Foo/Bar -> /Game/Foo/Bar.Bar
				const FString Full = P + TEXT(".") + FPackageName::GetShortName(P);
				Target = StaticLoadObject(UObject::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
			if (!Target) { Fail(Out, FString::Printf(TEXT("object not found: %s"), *ObjectPath)); return; }
		}
		else if (!WidgetName.IsEmpty())
		{
			UBlueprint* BP = ResolveBlueprintField(In, Out);   // reads blueprintId/path; writes Fail on miss
			if (!BP) return;
			OwningWidgetBP = Cast<UWidgetBlueprint>(BP);
			if (!OwningWidgetBP) { Fail(Out, FString::Printf(TEXT("'%s' is not a Widget Blueprint"), *BP->GetName())); return; }
			if (!OwningWidgetBP->WidgetTree) { Fail(Out, TEXT("widget blueprint has no WidgetTree")); return; }
			// The edit target is the UWidget TEMPLATE in the tree (an archetype).
			Target = OwningWidgetBP->WidgetTree->FindWidget(FName(*WidgetName));
			if (!Target) { Fail(Out, FString::Printf(TEXT("widget '%s' not found in %s"), *WidgetName, *BP->GetName())); return; }
		}
		else
		{
			Fail(Out, TEXT("supply either objectPath or (blueprintId + widgetName)"));
			return;
		}

		// --- Walk the dot path --------------------------------------------------------
		FProperty* Leaf = nullptr; void* LeafAddr = nullptr; UObject* LeafOwner = nullptr;
		FString Error;
		if (!ResolvePropertyPath(Target, PropertyPath, Leaf, LeafAddr, LeafOwner, Error))
		{
			Fail(Out, Error);
			return;
		}

		// Bool sanitize (only when the LEAF is a bool — struct sub-fields like a leaf .R are floats).
		FString ImportStr = Value;
		if (CastField<FBoolProperty>(Leaf)) { ImportStr = NormalizeBoolLiteral(Value); }

		// --- Details-panel write bracket, scoped to a TIGHT inner transaction ---------
		// CompileBlueprint (below) reinstances and must NOT be inside a transaction, so the
		// transaction closes at the end of this block. ErrText is declared outside so the
		// parse result survives for the Fail message.
		FStringOutputDevice ErrText;
		const TCHAR* R = nullptr;
		{
			FScopedTransaction Tx(NSLOCTEXT("MifBridge", "SetProperty", "Mif Bridge: set_property"));
			LeafOwner->Modify();
			LeafOwner->PreEditChange(Leaf);

			R = Leaf->ImportText_Direct(*ImportStr, LeafAddr, LeafOwner, PPF_None, &ErrText);

			FPropertyChangedEvent Evt(Leaf, EPropertyChangeType::ValueSet);
			LeafOwner->PostEditChangeProperty(Evt);   // propagates to instances/archetype
			LeafOwner->MarkPackageDirty();
		}   // transaction commits here — BEFORE any compile

		if (R == nullptr)
		{
			Fail(Out, FString::Printf(TEXT("ImportText_Direct failed for '%s' = '%s': %s"),
				*PropertyPath, *ImportStr, *ErrText));
			return;
		}

		// --- Widget-BP persistence: mark + recompile so the generated class bakes the edit -----
		bool bRecompiled = false;
		if (OwningWidgetBP)
		{
			FBlueprintEditorUtils::MarkBlueprintAsModified(OwningWidgetBP);
			FKismetEditorUtilities::CompileBlueprint(OwningWidgetBP);   // outside the transaction above
			bRecompiled = true;
		}

		Out->SetStringField(TEXT("target"), Target->GetPathName());
		Out->SetStringField(TEXT("propertyPath"), PropertyPath);
		Out->SetStringField(TEXT("leafProperty"), Leaf->GetName());
		Out->SetBoolField(TEXT("applied"), true);
		if (OwningWidgetBP) { Out->SetBoolField(TEXT("recompiled"), bRecompiled); }
		UE_LOG(LogMifBridge, Log, TEXT("set_property: %s.%s = %s"), *Target->GetName(), *PropertyPath, *ImportStr);
	}
}
