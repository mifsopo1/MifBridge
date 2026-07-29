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
	// The target resolver and the dot-walk BOTH live in MifBridgeCommon.cpp now
	// (MifBridge::ResolvePropertyTarget / MifBridge::ResolvePropertyPathEx, declared in
	// MifBridgeHandlers.h).
	//
	// This file used to carry a local ResolveGenericTarget whose own comment said the copy was
	// deliberate - "duplicated here rather than shared so this read-only file can't perturb the
	// existing write path". That reasoning does not survive contact with PM-005: the two copies were
	// already free to drift about what "objectPath" accepts, and Batch N added four more endpoints
	// that need the same resolution, which would have made six. A read that resolves its target
	// differently from the write that follows it is a worse failure than the one the fence was
	// guarding against.
	//
	// (The read-only callers below take the walker's non-const address and simply bind it to a const
	// pointer; there is no second const overload to drift.)

	//   in:  { objectPath: "/Game/..." } OR { blueprintId: "...", widgetName: "MyText" }
	//        propertyPath: "A.B.C" (dot path)
	//   out: { target, propertyPath, leafProperty, type, value }
	// value is the property's ExportText representation (same text you'd see copy-pasted from
	// the Details panel) — readable for scalars, structs, arrays, enums, object refs alike.
	void H_get_property(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"),
			  TEXT("widgetName"), TEXT("propertyPath"), TEXT("property") },
			TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, propertyPath (alias property)")))
		{
			return;
		}

		const FString PropertyPath = JStrAny(In, { TEXT("propertyPath"), TEXT("property") });
		if (PropertyPath.IsEmpty()) { Fail(Out, TEXT("propertyPath required (dot path, e.g. Font.Size; element accessors: Keys[2].Value, SomeMap{Alpha})")); return; }

		UObject* Target = ResolvePropertyTarget(In, Out);
		if (!Target) return;

		FPropertyPathResolution Res;
		FString Error;
		if (!ResolvePropertyPathEx(Target, PropertyPath, Res, Error))
		{
			Fail(Out, Error);
			return;
		}
		FProperty* Leaf = Res.Leaf; UObject* LeafOwner = Res.LeafOwner;
		const void* LeafAddr = Res.LeafAddr;   // read-only from here down

		FString ValueStr;
		Leaf->ExportText_Direct(ValueStr, LeafAddr, LeafAddr, LeafOwner, PPF_None);

		Out->SetStringField(TEXT("target"), Target->GetPathName());
		Out->SetStringField(TEXT("propertyPath"), PropertyPath);
		Out->SetStringField(TEXT("leafProperty"), Leaf->GetName());
		Out->SetStringField(TEXT("type"), Leaf->GetCPPType());
		Out->SetStringField(TEXT("value"), ValueStr);
		// "value" stays the engine's lossless export text (round-trip-safe, and existing callers
		// read it), but export text is a hostile shape for a caller doing arithmetic or a filter:
		// a bool arrives as the STRING "False" and an array as one "(\"A\",\"B\")" blob. That cost a
		// 63-blueprint audit its correctness once — the strings are truthy in most languages, so
		// every "is this flag set" test silently passed. "typed" is the same value as real JSON
		// (bool/number/array/object). Shared writer with set_property (MifBridgeNodes5.cpp) so the
		// two can never disagree about what a property means.
		Out->SetField(TEXT("typed"), Res.bLeafIsElement
			? PropertyValueToTypedJsonElement(Leaf, LeafAddr, LeafOwner)
			: PropertyValueToTypedJson(Leaf, LeafAddr, LeafOwner));
		// Batch N: element addressing. Stated rather than implied, because "OverrideMaterials[1]" and
		// "OverrideMaterials" are different questions with different answers.
		Out->SetBoolField(TEXT("isElement"), Res.bLeafIsElement);
		if (Res.bLeafIsElement)
		{
			Out->SetStringField(TEXT("elementPath"), Res.ElementDescription);
			Out->SetNumberField(TEXT("elementIndex"), Res.ElementIndex);
			if (!Res.ElementOrdering.IsEmpty())
			{
				// A set/map index is a POSITION IN ITERATION ORDER, and that order is not stable across
				// a rehash. Saying so is the difference between a usable index and a trap.
				Out->SetStringField(TEXT("elementOrdering"), Res.ElementOrdering);
			}
		}
	}

	//   in:  { objectPath: "/Game/..." } OR { blueprintId: "...", widgetName: "MyText" }
	//   out: { target, class, properties: [{name, type, value}] }
	// Dumps every top-level reflected property so an unfamiliar asset (DataAsset, InputAction,
	// InputMappingContext, Actor, ...) can be surveyed without knowing field names up front.
	// Use get_property afterwards to descend into a specific struct/object field.
	void H_list_object_properties(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			  TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"), TEXT("limit"), TEXT("maxValueChars") },
			TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, nameContains (aliases filter, nameFilter), limit, maxValueChars"),
			{{ TEXT("propertyPath"),
			   TEXT("list_object_properties dumps ALL top-level properties; get_property reads ONE by dot path, and describe_property reports its flags/metadata/EditCondition") }}))
		{
			return;
		}

		UObject* Target = ResolvePropertyTarget(In, Out);
		if (!Target) return;

		// Filtering and caps are NOT optional niceties here. Exporting every property of a large
		// Blueprint actor is pathological: Ultra_Dynamic_Sky has ~545 properties, several of which
		// are volumetric-cloud/curve structs whose ExportText runs to tens of kilobytes EACH. The
		// unbounded version produced a response so large the request came back empty — the endpoint
		// looked broken on exactly the objects you most want to inspect.
		const FString NameFilter = JStrAny(In, { TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter") });
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 200), 1, 5000);
		// Long values are near-useless in a listing anyway — get_property returns the full value for
		// a single named property, which is the right tool once you know what you want.
		const int32 MaxValueChars = FMath::Clamp(JInt(In, TEXT("maxValueChars"), 200), 16, 100000);

		TArray<TSharedPtr<FJsonValue>> Props;
		int32 Matched = 0;
		bool bTruncated = false;
		for (TFieldIterator<FProperty> It(Target->GetClass()); It; ++It)
		{
			FProperty* Prop = *It;
			if (!Prop) continue;

			const FString PropName = Prop->GetName();
			if (!NameFilter.IsEmpty() && !PropName.Contains(NameFilter))
			{
				continue;
			}
			++Matched;
			if (Props.Num() >= Limit)
			{
				bTruncated = true;
				continue;   // keep counting so the caller learns the real total
			}

			FString ValueStr;
			Prop->ExportText_InContainer(0, ValueStr, Target, Target, Target, PPF_None);
			bool bValueClipped = false;
			if (ValueStr.Len() > MaxValueChars)
			{
				ValueStr = ValueStr.Left(MaxValueChars);
				bValueClipped = true;
			}

			TSharedRef<FJsonObject> PropJson = MakeShared<FJsonObject>();
			PropJson->SetStringField(TEXT("name"), PropName);
			PropJson->SetStringField(TEXT("type"), Prop->GetCPPType());
			PropJson->SetStringField(TEXT("value"), ValueStr);
			if (bValueClipped)
			{
				// Say so rather than hand back a silently-cut value that looks complete.
				PropJson->SetBoolField(TEXT("valueClipped"), true);
			}
			Props.Add(MakeShared<FJsonValueObject>(PropJson));
		}

		Out->SetStringField(TEXT("target"), Target->GetPathName());
		Out->SetStringField(TEXT("class"), Target->GetClass()->GetName());
		Out->SetNumberField(TEXT("count"), Props.Num());
		Out->SetNumberField(TEXT("matched"), Matched);
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("properties"), Props);
	}
}
