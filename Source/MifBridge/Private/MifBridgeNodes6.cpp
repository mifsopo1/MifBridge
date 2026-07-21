// MifBridge — generic reflection property readers (get_property, list_object_properties).
// Read-only counterparts to set_property: resolve a target object the same way (objectPath,
// or blueprintId+widgetName for a widget template in a WBP's tree), then either dot-walk to
// one leaf property (get_property) or dump every top-level property (list_object_properties)
// via FProperty::ExportText — the same mechanism the Details panel and copy/paste use, so
// arrays/structs/enums all come back as readable text instead of requiring per-field support.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "UObject/UnrealType.h"        // FProperty, FStructProperty, FObjectProperty, ExportText
#include "UObject/Class.h"             // UStruct::FindPropertyByName, TFieldIterator
#include "UObject/UObjectGlobals.h"    // StaticLoadObject
#include "Misc/PackageName.h"

#include "WidgetBlueprint.h"
#include "Blueprint/WidgetTree.h"
#include "Components/Widget.h"

namespace MifBridge
{
	// Same target-resolution rules as set_property (see MifBridgeNodes5.cpp), duplicated here
	// rather than shared so this read-only file can't perturb the existing write path.
	static UObject* ResolveGenericTarget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString ObjectPath = JStr(In, TEXT("objectPath"));
		const FString WidgetName = JStr(In, TEXT("widgetName"));

		if (!ObjectPath.IsEmpty())
		{
			FString P = ObjectPath; P.TrimStartAndEndInline();
			UObject* Target = StaticLoadObject(UObject::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Target && !P.Contains(TEXT(".")))
			{
				// Accept a bare package path like /Game/Foo/Bar -> /Game/Foo/Bar.Bar
				const FString Full = P + TEXT(".") + FPackageName::GetShortName(P);
				Target = StaticLoadObject(UObject::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
			if (!Target) { Fail(Out, FString::Printf(TEXT("object not found: %s"), *ObjectPath)); return nullptr; }
			return Target;
		}
		if (!WidgetName.IsEmpty())
		{
			UBlueprint* BP = ResolveBlueprintField(In, Out);   // reads blueprintId/path; writes Fail on miss
			if (!BP) return nullptr;
			UWidgetBlueprint* WidgetBP = Cast<UWidgetBlueprint>(BP);
			if (!WidgetBP) { Fail(Out, FString::Printf(TEXT("'%s' is not a Widget Blueprint"), *BP->GetName())); return nullptr; }
			if (!WidgetBP->WidgetTree) { Fail(Out, TEXT("widget blueprint has no WidgetTree")); return nullptr; }
			UObject* Target = WidgetBP->WidgetTree->FindWidget(FName(*WidgetName));
			if (!Target) { Fail(Out, FString::Printf(TEXT("widget '%s' not found in %s"), *WidgetName, *BP->GetName())); return nullptr; }
			return Target;
		}
		Fail(Out, TEXT("supply either objectPath or (blueprintId + widgetName)"));
		return nullptr;
	}

	// Walk "A.B.C" from Object, descending FStructProperty in-place and hopping FObjectProperty
	// to the pointed-to UObject. Read-only mirror of set_property's ResolvePropertyPath.
	static bool ResolveReadPropertyPath(UObject* Object, const FString& Path,
		FProperty*& OutLeaf, const void*& OutLeafAddr, UObject*& OutLeafOwner, FString& OutError)
	{
		if (!Object) { OutError = TEXT("null target object"); return false; }

		TArray<FString> Segs;
		Path.ParseIntoArray(Segs, TEXT("."), true);
		if (Segs.Num() == 0) { OutError = TEXT("empty propertyPath"); return false; }

		UStruct* CurStruct = Object->GetClass();
		const void* Container = Object;
		UObject* LeafOwner = Object;

		for (int32 i = 0; i < Segs.Num(); ++i)
		{
			FProperty* Prop = CurStruct->FindPropertyByName(FName(*Segs[i]));
			if (!Prop)
			{
				OutError = FString::Printf(TEXT("property '%s' not found on '%s'"), *Segs[i], *CurStruct->GetName());
				return false;
			}
			const void* ValuePtr = Prop->ContainerPtrToValuePtr<void>(const_cast<void*>(Container));

			if (i == Segs.Num() - 1)
			{
				OutLeaf = Prop; OutLeafAddr = ValuePtr; OutLeafOwner = LeafOwner;
				return true;
			}

			if (FStructProperty* SP = CastField<FStructProperty>(Prop))
			{
				CurStruct = SP->Struct;
				Container = ValuePtr;
			}
			else if (FObjectPropertyBase* OP = CastField<FObjectPropertyBase>(Prop))
			{
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
				OutError = FString::Printf(TEXT("segment '%s' is not a struct or object ref (arrays/maps/sets unsupported mid-path)"), *Segs[i]);
				return false;
			}
		}
		OutError = TEXT("path traversal fell through");
		return false;
	}

	//   in:  { objectPath: "/Game/..." } OR { blueprintId: "...", widgetName: "MyText" }
	//        propertyPath: "A.B.C" (dot path)
	//   out: { target, propertyPath, leafProperty, type, value }
	// value is the property's ExportText representation (same text you'd see copy-pasted from
	// the Details panel) — readable for scalars, structs, arrays, enums, object refs alike.
	void H_get_property(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString PropertyPath = JStr(In, TEXT("propertyPath"));
		if (PropertyPath.IsEmpty()) { Fail(Out, TEXT("propertyPath required (dot path, e.g. Font.Size)")); return; }

		UObject* Target = ResolveGenericTarget(In, Out);
		if (!Target) return;

		FProperty* Leaf = nullptr; const void* LeafAddr = nullptr; UObject* LeafOwner = nullptr; FString Error;
		if (!ResolveReadPropertyPath(Target, PropertyPath, Leaf, LeafAddr, LeafOwner, Error))
		{
			Fail(Out, Error);
			return;
		}

		FString ValueStr;
		Leaf->ExportText_Direct(ValueStr, LeafAddr, LeafAddr, LeafOwner, PPF_None);

		Out->SetStringField(TEXT("target"), Target->GetPathName());
		Out->SetStringField(TEXT("propertyPath"), PropertyPath);
		Out->SetStringField(TEXT("leafProperty"), Leaf->GetName());
		Out->SetStringField(TEXT("type"), Leaf->GetCPPType());
		Out->SetStringField(TEXT("value"), ValueStr);
	}

	//   in:  { objectPath: "/Game/..." } OR { blueprintId: "...", widgetName: "MyText" }
	//   out: { target, class, properties: [{name, type, value}] }
	// Dumps every top-level reflected property so an unfamiliar asset (DataAsset, InputAction,
	// InputMappingContext, Actor, ...) can be surveyed without knowing field names up front.
	// Use get_property afterwards to descend into a specific struct/object field.
	void H_list_object_properties(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UObject* Target = ResolveGenericTarget(In, Out);
		if (!Target) return;

		TArray<TSharedPtr<FJsonValue>> Props;
		for (TFieldIterator<FProperty> It(Target->GetClass()); It; ++It)
		{
			FProperty* Prop = *It;
			if (!Prop) continue;

			FString ValueStr;
			Prop->ExportText_InContainer(0, ValueStr, Target, Target, Target, PPF_None);

			TSharedRef<FJsonObject> PropJson = MakeShared<FJsonObject>();
			PropJson->SetStringField(TEXT("name"), Prop->GetName());
			PropJson->SetStringField(TEXT("type"), Prop->GetCPPType());
			PropJson->SetStringField(TEXT("value"), ValueStr);
			Props.Add(MakeShared<FJsonValueObject>(PropJson));
		}

		Out->SetStringField(TEXT("target"), Target->GetPathName());
		Out->SetStringField(TEXT("class"), Target->GetClass()->GetName());
		Out->SetArrayField(TEXT("properties"), Props);
	}
}
