// MifBridge — generic reflection property setter (set_property) + the shared typed-JSON emitter.
//
// Write path mirrors the Details panel: resolve target -> dot-walk -> ImportText_Direct into a
// SCRATCH buffer -> Modify/PreEditChange/publish/PostEditChangeProperty. Editor-only.
//
// Batch F added two things that are not "the Details panel write":
//
//  1. JSON containers as `value`. `value` used to be read with JStr, which returns the DEFAULT ("")
//     for any JSON value that is not a string. An empty string is not an import failure for every
//     property type — FArrayProperty::ImportTextInnerItem (PropertyArray.cpp:612-621) treats it as
//     "empty the array" and returns a NON-NULL buffer, i.e. SUCCESS. So a caller who passed
//     value:["A","B","C"] got ok:true and an EMPTIED array. Four CDO arrays were reported written
//     and were in fact blank. The float parser is the same shape: PropertyNumeric.cpp:125-137 has no
//     "nothing consumed" guard on the floating-point branch, so value:0.5 (a JSON number) parsed ""
//     as 0.0 and also reported success.
//     JSON arrays/objects/numbers/bools are now CONVERTED to UE export text before import, and
//     anything that cannot be converted faithfully is REFUSED with the property, its type, and the
//     export-text form it wants.
//
//  2. A universal post-write verification. After a successful import the leaf is re-exported and
//     compared with the pre-write export. If the caller asked for a change and the property is
//     byte-identical afterwards, the call FAILS. That guard is type-agnostic: it makes
//     "ok:true but nothing written" impossible for property kinds nobody has tried yet, not just
//     arrays.
//
// PM-003 is preserved and generalised: ImportText_Direct parses IN PLACE and can consume/zero the
// destination before deciding the text is bad, so nothing here ever hands a live address to a
// parser. FScratchValue is that rule as a type.
//
// SHARED, NOT LOCAL. PropertyValueToTypedJson and JsonToPropertyText are defined here (next to the
// conversion helpers they depend on) but DECLARED in MifBridgeHandlers.h, so MifBridgeNodes6.cpp's
// readers and set_variable_default's writer use THIS implementation. Nodes6 was previously calling
// PropertyValueToTypedJson with no declaration in sight at all — it compiled only because the unity
// build happened to put Nodes5 and Nodes6 in the same translation unit, which is not a guarantee.
// Do NOT copy either function into another file: see the C2084 note in MifBridgeHandlers.h.
#include "MifBridgeHandlers.h"
#include "MifBridgeVersion.h"
// FStringOutputDevice MOVED between the two engines this plugin targets:
//   5.3: declared in Containers/UnrealString.h, reached transitively through CoreMinimal
//   5.7: promoted to its own header, Misc/StringOutputDevice.h, and no longer pulled in for free
//
// So the include is REQUIRED on 5.7 and IMPOSSIBLE on 5.3 - that path does not exist there, and an
// unguarded include is a fatal C1083. The Curfew session hit the 5.7 half and could not see the 5.3
// half; building here caught it. A fifth shape for docs/02_GOTCHAS.md section 14: same type, same
// name, different HEADER.
#if MIF_ENGINE_5_7_PLUS
#include "Misc/StringOutputDevice.h"
#endif
#include "MifBridgeLog.h"

#include "UObject/UnrealType.h"        // FProperty, FStructProperty, FObjectProperty, Import/ExportText, FScript*Helper
#include "UObject/Class.h"             // UStruct::FindPropertyByName, TFieldIterator
#include "UObject/EnumProperty.h"      // FEnumProperty
#include "UObject/TextProperty.h"      // FTextProperty
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"    // StaticLoadObject
#include "Dom/JsonValue.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"

#include "WidgetBlueprint.h"
#include "Blueprint/WidgetTree.h"
#include "Components/Widget.h"
#include "Kismet2/BlueprintEditorUtils.h"   // MarkBlueprintAsModified
#include "Kismet2/KismetEditorUtilities.h"  // CompileBlueprint

namespace MifBridge
{
	// PropertyValueToTypedJson / JsonToPropertyText are declared in MifBridgeHandlers.h; this file
	// holds their single definition. (Handles ArrayDim: a C-array UPROPERTY (int Foo[4]) comes back
	// as a JSON array of its elements, not just element 0.)

	// Guard against a pathological/self-referential shape blowing the stack or the response. 12 is
	// far past anything real (FSlateBrush bottoms out around 4).
	static const int32 kMaxReflectionDepth = 12;

	// NormalizeBoolLiteral moved to MifBridgeCommon.cpp (declared in MifBridgeHandlers.h). It existed
	// here AND in MifBridgeInherited.cpp under an eviction clause with no trigger; this file's copy
	// was `static` and Inherited's was in an unnamed namespace, which under unity is the same
	// namespace scope in one TU — a redefinition the moment the two files share a blob.

	// One correctly constructed/destructed instance of a property's value. PM-003's rule as a type:
	// never let a parser that can fail touch a live address. GetSize() spans ArrayDim, so a C-array
	// UPROPERTY round-trips intact.
	struct FScratchValue
	{
		const FProperty* Prop = nullptr;
		void* Mem = nullptr;

		explicit FScratchValue(const FProperty* InProp)
			: Prop(InProp)
		{
			Mem = FMemory::Malloc(FMath::Max(Prop->GetSize(), 1), Prop->GetMinAlignment());
			Prop->InitializeValue(Mem);   // ctor: required before Copy/Import on struct/text/array
		}
		~FScratchValue()
		{
			if (Mem) { Prop->DestroyValue(Mem); FMemory::Free(Mem); }
		}
		FScratchValue(const FScratchValue&) = delete;
		FScratchValue& operator=(const FScratchValue&) = delete;
	};

	// The property's own export text. Data==Delta short-circuits FProperty::ExportText_Direct's
	// "skip if identical to the default" branch (Property.cpp:1149), so this always emits.
	static FString ExportLeafText(const FProperty* Prop, const void* ValueAddr, UObject* Owner)
	{
		FString S;
		if (Prop && ValueAddr) { Prop->ExportText_Direct(S, ValueAddr, ValueAddr, Owner, PPF_None); }
		return S;
	}

	// Element count for the three dynamic containers, INDEX_NONE for everything else. Reported on
	// every container write so "did it land" is answerable with a number, not by eyeballing a string.
	static int32 ContainerElementCount(const FProperty* Prop, const void* ValueAddr)
	{
		if (!Prop || !ValueAddr) { return INDEX_NONE; }
		if (const FArrayProperty* AP = CastField<FArrayProperty>(Prop)) { return FScriptArrayHelper(AP, ValueAddr).Num(); }
		if (const FSetProperty*   SP = CastField<FSetProperty>(Prop))   { return FScriptSetHelper(SP, ValueAddr).Num(); }
		if (const FMapProperty*   MP = CastField<FMapProperty>(Prop))   { return FScriptMapHelper(MP, ValueAddr).Num(); }
		return INDEX_NONE;
	}

	// What this property will accept, shown in EVERY refusal. A refusal that does not state the
	// correct form just moves the guesswork to the caller — which is how the reported bug survived:
	// the endpoint wanted export text and never said so.
	static FString AcceptedFormHint(const FProperty* Prop)
	{
		if (const FArrayProperty* AP = CastField<FArrayProperty>(Prop))
		{
			return FString::Printf(TEXT("a JSON array of %s (e.g. [\"A\",\"B\"]) or UE export text as a string (\"A\",\"B\")"),
				*AP->Inner->GetCPPType());
		}
		if (const FSetProperty* SP = CastField<FSetProperty>(Prop))
		{
			return FString::Printf(TEXT("a JSON array of %s (e.g. [\"A\",\"B\"]) or UE export text as a string (\"A\",\"B\")"),
				*SP->ElementProp->GetCPPType());
		}
		if (const FMapProperty* MP = CastField<FMapProperty>(Prop))
		{
			return FString::Printf(TEXT("a JSON object {%s: %s} (e.g. {\"K\":\"V\"}) or UE export text as a string ((\"K\",\"V\"))"),
				*MP->KeyProp->GetCPPType(), *MP->ValueProp->GetCPPType());
		}
		if (const FStructProperty* StP = CastField<FStructProperty>(Prop))
		{
			return FString::Printf(TEXT("a JSON object of %s members (e.g. {\"X\":1,\"Y\":2}) or UE export text as a string (X=1,Y=2)"),
				*StP->Struct->GetName());
		}
		if (CastField<FBoolProperty>(Prop))
		{
			return TEXT("JSON true/false, or the string True/False/1/0/Yes/No (lowercase true/false is normalised)");
		}
		if (const FEnumProperty* EP = CastField<FEnumProperty>(Prop))
		{
			// Defensive null check: this runs on the ERROR path, and crashing while building an error
			// message turns a bad request into a lost editor session.
			const UEnum* E = EP->GetEnum();
			return FString::Printf(TEXT("the %s entry name as a JSON string, or its integer value as a JSON number"),
				E ? *E->GetName() : TEXT("enum"));
		}
		if (const FByteProperty* ByteP = CastField<FByteProperty>(Prop))
		{
			if (ByteP->Enum)
			{
				return FString::Printf(TEXT("the %s entry name as a JSON string, or its integer value as a JSON number"), *ByteP->Enum->GetName());
			}
		}
		if (const FNumericProperty* NP = CastField<FNumericProperty>(Prop))
		{
			return NP->IsFloatingPoint()
				? TEXT("a JSON number (no exponent form — UE's float parser stops at [+-.0-9], PropertyNumeric.cpp:129) or a numeric string")
				: TEXT("a whole JSON number or a numeric string");
		}
		if (CastField<FStrProperty>(Prop) || CastField<FNameProperty>(Prop) || CastField<FTextProperty>(Prop))
		{
			return TEXT("a JSON string");
		}
		if (CastField<FObjectPropertyBase>(Prop))
		{
			return TEXT("an object path as a JSON string (e.g. \"/Game/Meshes/SM_Body.SM_Body\"), or null for None");
		}
		return TEXT("UE export text as a JSON string (the exact text get_property returns in \"value\")");
	}

	// Refusal text shared by every branch: WHERE, what the property is, and what it wants.
	static FString RefuseValue(const FString& Where, const FProperty* Prop, const TCHAR* Got)
	{
		return FString::Printf(TEXT("'%s' (%s %s): cannot convert JSON %s. Accepts %s."),
			*Where, *Prop->GetClass()->GetName(), *Prop->GetCPPType(), Got, *AcceptedFormHint(Prop));
	}

	// ---- validate BEFORE importing (Batch L, defect 2) ----------------------------------------
	//
	// LIVE EVIDENCE. override_inherited_component {component:"Influence",
	// properties:{"SphereRadius":"not-a-float"}} answered ok:true, applied:true, wanted:"0.000000".
	// Nothing in the write bracket was broken: ImportText_Direct really did report success, the
	// publish really did happen, and the read-back really did equal what was staged. The value was
	// simply never UNDERSTOOD — UE's float parser (PropertyNumeric.cpp:125-137) accepts only
	// [+-.0-9], stops at the first character it dislikes, and has no "nothing consumed" guard, so
	// "not-a-float" parsed as 0.0.
	//
	// VERIFYING THAT THE WRITE LANDED DOES NOT VERIFY THAT THE VALUE WAS UNDERSTOOD. The anti-silence
	// guard cannot catch this class by construction: `wanted` and `after` are both derived from the
	// same misparse, so they agree. The only place to catch it is here, before the import, against
	// the DESTINATION property's type.
	//
	// Everything this function knows how to check, it checks; everything else it declares unchecked
	// via bOutValidated instead of implying a guarantee it did not make.
	static FString ListEnumEntries(const UEnum* Enum, int32 MaxToList = 24)
	{
		if (!Enum) { return FString(); }
		int32 Count = Enum->NumEnums();
		// Most UENUMs carry a synthesised _MAX sentinel, which is not a value a caller should send —
		// but not all do, so drop it only when it is actually there rather than losing a real entry.
		if (Count > 0 && Enum->GetNameStringByIndex(Count - 1).EndsWith(TEXT("_MAX"))) { --Count; }
		TArray<FString> Names;
		for (int32 i = 0; i < Count && Names.Num() < MaxToList; ++i)
		{
			// AUTHORED name, not the reflected one: a Blueprint user enum stores mangled entries
			// ("NewEnumerator0") and shows the authored display name everywhere a caller can see it,
			// so listing the reflected names would answer a question nobody asked.
			Names.Add(Enum->GetAuthoredNameStringByIndex(i));
		}
		FString Joined = FString::Join(Names, TEXT(", "));
		if (Count > Names.Num()) { Joined += FString::Printf(TEXT(", … (%d more)"), Count - Names.Num()); }
		return Joined;
	}

	bool ValidatePropertyText(const FProperty* Prop, const FString& Text, const FString& Where,
		FString& OutError, bool* bOutValidated)
	{
		if (bOutValidated) { *bOutValidated = false; }
		if (!Prop) { OutError = FString::Printf(TEXT("'%s': null property"), *Where); return false; }

		const FString T = Text.TrimStartAndEnd();

		// Bools first: FBoolProperty is not an FNumericProperty, but "1"/"0" are legal for it.
		if (CastField<FBoolProperty>(Prop))
		{
			if (bOutValidated) { *bOutValidated = true; }
			const FString N = NormalizeBoolLiteral(T);
			if (N.Equals(TEXT("True")) || N.Equals(TEXT("False")) || N == TEXT("1") || N == TEXT("0")
				|| N.Equals(TEXT("Yes"), ESearchCase::IgnoreCase) || N.Equals(TEXT("No"), ESearchCase::IgnoreCase)
				|| N.Equals(TEXT("On"), ESearchCase::IgnoreCase) || N.Equals(TEXT("Off"), ESearchCase::IgnoreCase))
			{
				return true;
			}
			OutError = FString::Printf(
				TEXT("'%s' (%s): '%s' is not a boolean. FBoolProperty::ImportText (PropertyBool.cpp:384-397) is ")
				TEXT("word-based and would take an unrecognised word as FALSE without reporting anything. Accepts %s."),
				*Where, *Prop->GetCPPType(), *T, *AcceptedFormHint(Prop));
			return false;
		}

		// Enums BEFORE numerics: an FByteProperty with an Enum IS numeric, but its text form is the
		// entry name, and a wrong name imports as 0 — i.e. the FIRST entry, a plausible-looking value.
		const FEnumProperty* EnumP = CastField<FEnumProperty>(Prop);
		const FByteProperty* ByteP = CastField<FByteProperty>(Prop);
		// ToRawPtr on the inner branch: 5.7 cannot reconcile TObjectPtr<UEnum> with nullptr in a
		// ternary. Same C2445 family as the two in MifBridgeNodes.cpp.
		const UEnum* Enum = EnumP ? EnumP->GetEnum()
							  : (ByteP ? ToRawPtr(ByteP->Enum) : nullptr);
		if (Enum != nullptr)
		{
			if (bOutValidated) { *bOutValidated = true; }
			double AsNumber = 0.0;
			if (ParseWholeNumber(T, AsNumber) && AsNumber == FMath::TruncToDouble(AsNumber))
			{
				return true;   // the documented "or its integer value" form
			}
			// CheckAuthoredName covers Blueprint user enums, whose entries are addressed by display
			// name; GetIndexByNameString already tries both the bare and Enum::-qualified spellings
			// (Enum.cpp:638-646), so "Movable" and "EComponentMobility::Movable" both resolve.
			if (Enum->GetIndexByNameString(T, EGetByNameFlags::CheckAuthoredName) != INDEX_NONE)
			{
				return true;
			}
			OutError = FString::Printf(
				TEXT("'%s' (%s): '%s' is not an entry of enum %s. An unrecognised entry name imports as 0 — the FIRST ")
				TEXT("entry — which looks like a deliberate value. Valid entries: %s."),
				*Where, *Prop->GetCPPType(), *T, *Enum->GetName(), *ListEnumEntries(Enum));
			return false;
		}

		if (const FNumericProperty* NP = CastField<FNumericProperty>(Prop))
		{
			if (bOutValidated) { *bOutValidated = true; }
			// UE's float importer tolerates one trailing f/F ("0.5f"), so strip it before the check
			// rather than refusing a form the engine itself accepts.
			FString Numeric = T;
			if (NP->IsFloatingPoint() && Numeric.Len() > 1 && (Numeric.EndsWith(TEXT("f")) || Numeric.EndsWith(TEXT("F"))))
			{
				Numeric.LeftChopInline(1);
			}
			// Exponent form parses as a number but UE's float importer cannot READ one: it accepts
			// only [+-.0-9] (PropertyNumeric.cpp:129), so "1e5" would import as 1 — a plausible value,
			// five orders of magnitude wrong, reported as success. AcceptedFormHint already says this;
			// now it is enforced instead of merely documented.
			if (Numeric.Contains(TEXT("e"), ESearchCase::IgnoreCase))
			{
				OutError = FString::Printf(
					TEXT("'%s' (%s): '%s' uses exponent notation, which UE's numeric importer cannot read — it accepts ")
					TEXT("only [+-.0-9] (PropertyNumeric.cpp:129) and would store just the mantissa. Write the number out ")
					TEXT("in full (e.g. 100000 rather than 1e5)."),
					*Where, *Prop->GetCPPType(), *T);
				return false;
			}
			double Parsed = 0.0;
			if (!ParseWholeNumber(Numeric, Parsed))
			{
				OutError = FString::Printf(
					TEXT("'%s' (%s): '%s' is not a number. UE's numeric importer stops at the first character it cannot ")
					TEXT("read and has no \"nothing consumed\" guard (PropertyNumeric.cpp:125-137), so it would import ")
					TEXT("this as 0 and report SUCCESS — and a post-write check comparing 0 against 0 would then pass. ")
					TEXT("The whole value must parse: a prefix like \"12abc\" is refused for the same reason. Accepts %s."),
					*Where, *Prop->GetCPPType(), *T, *AcceptedFormHint(Prop));
				return false;
			}
			if (NP->IsInteger() && Parsed != FMath::TruncToDouble(Parsed))
			{
				OutError = FString::Printf(
					TEXT("'%s' (%s): '%s' has a fractional part and this is an integer property. UE's integer importer ")
					TEXT("stops at the '.', so it would silently store %lld. Pass a whole number."),
					*Where, *Prop->GetCPPType(), *T, (int64)FMath::TruncToDouble(Parsed));
				return false;
			}
			return true;
		}

		// Strings, names and text take anything.
		if (CastField<FStrProperty>(Prop) || CastField<FNameProperty>(Prop) || CastField<FTextProperty>(Prop))
		{
			if (bOutValidated) { *bOutValidated = true; }
			return true;
		}

		// Hard object/class refs are validated AFTER the import, in CanonicaliseLeaf: the importer is
		// the only thing that knows how to read every reference spelling (bare path, Class'/Path.Name',
		// None), and it stores null for an unresolvable path while reporting success
		// (PropertyBaseObject.cpp:388/422) — which that check turns into a refusal. Soft refs are
		// deliberately NOT checked: a soft reference legitimately names an unloaded asset.
		if (CastField<FObjectPropertyBase>(Prop))
		{
			if (bOutValidated) { *bOutValidated = CastField<FObjectProperty>(Prop) != nullptr; }
			return true;
		}

		// Structs and containers arrive as export text whose grammar only the engine's own parser
		// knows; there is no reliable pre-check, and guessing one would be the very habit this
		// function exists to remove. Say so instead. (JSON objects/arrays ARE checked member by
		// member: JsonToPropertyText recurses and each leaf lands back here.)
		OutError = FString::Printf(
			TEXT("'%s' (%s): the bridge cannot pre-validate export text for this property kind, so the value below was ")
			TEXT("imported unchecked — a partially-parsed literal can still land as a plausible value. Send the value as ")
			TEXT("typed JSON instead (%s) and every leaf is checked individually."),
			*Where, *Prop->GetCPPType(), *AcceptedFormHint(Prop));
		return true;
	}

	// JsonTypeName moved to MifBridgeCommon.cpp (declared in MifBridgeHandlers.h). CALLER-VISIBLE
	// CHANGE: this file's copy spelled EJson::Boolean "bool" while MifBridgeAuthoring.cpp's copy
	// spelled it "boolean", so set_property and set_material_parameter refused the same JSON type
	// with two different words. The shared one says "boolean" (the JSON spec's own noun), so
	// set_property refusals now read "cannot convert JSON boolean" where they said "JSON bool".

	// Hand RAW (undelimited) text to the property's OWN importer, then ask the ENGINE to export it
	// back in the form the enclosing container parser expects.
	//
	// Why not hand-roll the quoting: every leaf type has its own rule (FStrProperty wraps in quotes
	// and escapes via ReplaceCharWithEscapedChar, PropertyStr.cpp:59; FNameProperty the same,
	// PropertyName.cpp:36; an object exports Class'/Game/Path.Name'; an enum exports its authored
	// entry name). Round-tripping through the engine gets all of them right by construction AND
	// validates the element — a bad enum name fails HERE, naming the element, instead of making the
	// whole container string fail with an unusable message.
	static bool CanonicaliseLeaf(const FProperty* Prop, const FString& RawText, bool bDelimited,
		UObject* Owner, const FString& Where, FString& OutText, FString& OutError)
	{
		// Batch L, defect 2: the type check happens BEFORE the importer sees the text. Handing
		// "not-a-float" to a float property here used to produce 0.0 and a clean success, and every
		// downstream check then agreed with that 0.0 because it was the only value in play.
		FString ValidationNote;
		if (!ValidatePropertyText(Prop, RawText, Where, ValidationNote))
		{
			OutError = ValidationNote;
			return false;
		}

		FScratchValue Scratch(Prop);
		FStringOutputDevice ErrText;
		const TCHAR* R = Prop->ImportText_Direct(*RawText, Scratch.Mem, Owner, PPF_None, &ErrText);
		if (R == nullptr)
		{
			FString Detail = ErrText.TrimStartAndEnd();
			if (!Detail.IsEmpty()) { Detail += TEXT(". "); }
			OutError = FString::Printf(TEXT("'%s' (%s): '%s' is not a valid value. %sAccepts %s."),
				*Where, *Prop->GetCPPType(), *RawText, *Detail, *AcceptedFormHint(Prop));
			return false;
		}

		// FObjectPropertyBase::ImportText_Internal computes bOk and then DROPS it — it returns the
		// advanced buffer even when the path resolved to nothing (PropertyBaseObject.cpp:388/422).
		// So an unresolvable asset path used to "import successfully" as null. Only hard object refs
		// are checked: a soft ref legitimately holds a path for an unloaded asset.
		if (const FObjectProperty* HardOP = CastField<FObjectProperty>(Prop))
		{
			const FString T = RawText.TrimStartAndEnd();
			if (!T.IsEmpty() && !T.Equals(TEXT("None"), ESearchCase::IgnoreCase)
				&& HardOP->GetObjectPropertyValue(Scratch.Mem) == nullptr)
			{
				OutError = FString::Printf(TEXT("'%s' (%s): object path '%s' did not resolve to a loaded object. ")
					TEXT("UE's object importer reports success and stores null for an unresolvable path, so this is refused rather than written as None. ")
					TEXT("Pass a full path like /Game/Meshes/SM_Body.SM_Body, or null to clear it deliberately."),
					*Where, *Prop->GetCPPType(), *T);
				return false;
			}
		}

		OutText.Reset();
		Prop->ExportTextItem_Direct(OutText, Scratch.Mem, nullptr, Owner, bDelimited ? PPF_Delimited : PPF_None);
		return true;
	}

	// A JSON object key is always a string, but a map's KEY property may be an int/name/enum. Route
	// the key text through the key property so TMap<int32,...> and TMap<FName,...> both work.
	static bool MapKeyToPropertyText(const FString& Key, const FProperty* KeyProp, UObject* Owner,
		const FString& Where, FString& OutText, FString& OutError)
	{
		// Numeric/bool/enum keys arrive as their decimal/word spelling inside the JSON key, which is
		// exactly what the property's own undelimited importer wants.
		return CanonicaliseLeaf(KeyProp, Key, /*bDelimited*/true, Owner, Where, OutText, OutError);
	}

	// Convert one JSON value into the UE export text that Prop's importer accepts.
	// bDelimited is true for anything INSIDE a container/struct literal (elements, map keys/values,
	// struct members) — that is the flag the engine's own container parsers pass down
	// (PropertyArray.cpp:656, PropertyMap.cpp:850/867, Class.cpp:2873).
	bool JsonToPropertyText(const TSharedPtr<FJsonValue>& Value, const FProperty* Prop,
		bool bDelimited, UObject* Owner, int32 Depth, const FString& Where,
		FString& OutText, FString& OutError)
	{
		if (!Prop) { OutError = FString::Printf(TEXT("'%s': null property"), *Where); return false; }
		if (!Value.IsValid() || Value->Type == EJson::None)
		{
			OutError = FString::Printf(TEXT("'%s': missing JSON value. Accepts %s."), *Where, *AcceptedFormHint(Prop));
			return false;
		}
		if (Depth > kMaxReflectionDepth)
		{
			OutError = FString::Printf(TEXT("'%s': JSON nested deeper than %d levels"), *Where, kMaxReflectionDepth);
			return false;
		}

		const EJson T = Value->Type;

		// A C-array UPROPERTY inside a struct literal needs Member[0]=..,Member[1]=.. syntax, which
		// this converter does not emit. Refuse rather than write element 0 and call it done.
		if (Prop->ArrayDim > 1 && bDelimited)
		{
			OutError = FString::Printf(TEXT("'%s' (%s[%d]): fixed-size C-array members inside a struct literal are not convertible from JSON. ")
				TEXT("Send the whole struct as UE export text instead."), *Where, *Prop->GetCPPType(), Prop->ArrayDim);
			return false;
		}

		// ---- dynamic containers -------------------------------------------------------------
		if (const FArrayProperty* AP = CastField<FArrayProperty>(Prop))
		{
			if (T == EJson::String) { OutText = Value->AsString(); return true; }   // already export text
			if (T != EJson::Array)  { OutError = RefuseValue(Where, Prop, JsonTypeName(T)); return false; }

			const TArray<TSharedPtr<FJsonValue>>& Elems = Value->AsArray();
			TArray<FString> Parts;
			Parts.Reserve(Elems.Num());
			for (int32 i = 0; i < Elems.Num(); ++i)
			{
				FString Part;
				if (!JsonToPropertyText(Elems[i], AP->Inner, true, Owner, Depth + 1,
					FString::Printf(TEXT("%s[%d]"), *Where, i), Part, OutError))
				{
					return false;
				}
				Parts.Add(Part);
			}
			// "()" is the engine's empty-array literal and imports cleanly (PropertyArray.cpp:636-644).
			OutText = TEXT("(") + FString::Join(Parts, TEXT(",")) + TEXT(")");
			return true;
		}

		if (const FSetProperty* SP = CastField<FSetProperty>(Prop))
		{
			if (T == EJson::String) { OutText = Value->AsString(); return true; }
			if (T != EJson::Array)  { OutError = RefuseValue(Where, Prop, JsonTypeName(T)); return false; }

			const TArray<TSharedPtr<FJsonValue>>& Elems = Value->AsArray();
			TArray<FString> Parts;
			Parts.Reserve(Elems.Num());
			for (int32 i = 0; i < Elems.Num(); ++i)
			{
				FString Part;
				if (!JsonToPropertyText(Elems[i], SP->ElementProp, true, Owner, Depth + 1,
					FString::Printf(TEXT("%s[%d]"), *Where, i), Part, OutError))
				{
					return false;
				}
				Parts.Add(Part);
			}
			OutText = TEXT("(") + FString::Join(Parts, TEXT(",")) + TEXT(")");
			return true;
		}

		if (const FMapProperty* MP = CastField<FMapProperty>(Prop))
		{
			if (T == EJson::String) { OutText = Value->AsString(); return true; }
			if (T != EJson::Object) { OutError = RefuseValue(Where, Prop, JsonTypeName(T)); return false; }

			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (!Value->TryGetObject(Obj) || Obj == nullptr || !Obj->IsValid())
			{
				OutError = RefuseValue(Where, Prop, JsonTypeName(T));
				return false;
			}

			// Engine map literal is ((Key,Value),(Key,Value)) — PropertyMap.cpp:843-877.
			TArray<FString> Parts;
			Parts.Reserve((*Obj)->Values.Num());
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*Obj)->Values)
			{
				FString KeyText;
				if (!MapKeyToPropertyText(Pair.Key, MP->KeyProp, Owner,
					FString::Printf(TEXT("%s{%s} key"), *Where, *Pair.Key), KeyText, OutError))
				{
					return false;
				}
				FString ValText;
				if (!JsonToPropertyText(Pair.Value, MP->ValueProp, true, Owner, Depth + 1,
					FString::Printf(TEXT("%s{%s}"), *Where, *Pair.Key), ValText, OutError))
				{
					return false;
				}
				Parts.Add(FString::Printf(TEXT("(%s,%s)"), *KeyText, *ValText));
			}
			OutText = TEXT("(") + FString::Join(Parts, TEXT(",")) + TEXT(")");
			return true;
		}

		// ---- struct literal -------------------------------------------------------------------
		if (const FStructProperty* StP = CastField<FStructProperty>(Prop))
		{
			if (T == EJson::String) { OutText = Value->AsString(); return true; }
			if (T != EJson::Object) { OutError = RefuseValue(Where, Prop, JsonTypeName(T)); return false; }

			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (!Value->TryGetObject(Obj) || Obj == nullptr || !Obj->IsValid())
			{
				OutError = RefuseValue(Where, Prop, JsonTypeName(T));
				return false;
			}

			TArray<FString> Parts;
			Parts.Reserve((*Obj)->Values.Num());
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*Obj)->Values)
			{
				FProperty* Member = StP->Struct->FindPropertyByName(FName(*Pair.Key));
				if (!Member)
				{
					// Blueprint user-struct members are stored mangled ("Speed_2_A1B2..."), so accept
					// the authored name too rather than telling the caller their correct field is wrong.
					for (TFieldIterator<FProperty> It(StP->Struct); It; ++It)
					{
						if (It->GetAuthoredName().Equals(Pair.Key, ESearchCase::IgnoreCase)) { Member = *It; break; }
					}
				}
				if (!Member)
				{
					// House rule: an unknown key names itself AND lists what is accepted.
					TArray<FString> Names;
					for (TFieldIterator<FProperty> It(StP->Struct); It; ++It)
					{
						const FString Authored = It->GetAuthoredName();
						Names.Add(Authored.Equals(It->GetName()) ? It->GetName()
							: FString::Printf(TEXT("%s (%s)"), *Authored, *It->GetName()));
					}
					OutError = FString::Printf(TEXT("'%s': struct %s has no member '%s'. Members: %s"),
						*Where, *StP->Struct->GetName(), *Pair.Key, *FString::Join(Names, TEXT(", ")));
					return false;
				}

				FString MemberText;
				if (!JsonToPropertyText(Pair.Value, Member, true, Owner, Depth + 1,
					FString::Printf(TEXT("%s.%s"), *Where, *Member->GetName()), MemberText, OutError))
				{
					return false;
				}
				Parts.Add(FString::Printf(TEXT("%s=%s"), *Member->GetName(), *MemberText));
			}
			// A partial struct literal leaves the untouched members alone, exactly like the Details
			// panel — the scratch buffer is seeded from the current value (PM-003).
			OutText = TEXT("(") + FString::Join(Parts, TEXT(",")) + TEXT(")");
			return true;
		}

		// ---- scalars: build RAW text, then let the engine canonicalise/validate it -------------
		FString Raw;

		if (CastField<FBoolProperty>(Prop))
		{
			switch (T)
			{
			case EJson::Boolean: Raw = Value->AsBool() ? TEXT("True") : TEXT("False"); break;
			case EJson::Number:  Raw = (Value->AsNumber() != 0.0) ? TEXT("True") : TEXT("False"); break;
			case EJson::String:  Raw = NormalizeBoolLiteral(Value->AsString()); break;
			default: OutError = RefuseValue(Where, Prop, JsonTypeName(T)); return false;
			}
			return CanonicaliseLeaf(Prop, Raw, bDelimited, Owner, Where, OutText, OutError);
		}

		// Enums BEFORE numerics: FByteProperty with an Enum IS a numeric property, but its text form
		// is the entry name. Emitting the raw byte would be the same class of loss as "True".
		const FEnumProperty* EnumP = CastField<FEnumProperty>(Prop);
		const FByteProperty* ByteP = CastField<FByteProperty>(Prop);
		if (EnumP != nullptr || (ByteP != nullptr && ByteP->Enum != nullptr))
		{
			switch (T)
			{
			case EJson::String: Raw = Value->AsString().TrimStartAndEnd(); break;
			case EJson::Number:
			{
				const double D = Value->AsNumber();
				if (D != FMath::TruncToDouble(D))
				{
					OutError = FString::Printf(TEXT("'%s' (%s): enum value must be a whole number, got %s. Accepts %s."),
						*Where, *Prop->GetCPPType(), *FString::SanitizeFloat(D), *AcceptedFormHint(Prop));
					return false;
				}
				Raw = FString::Printf(TEXT("%lld"), (int64)D);
				break;
			}
			default: OutError = RefuseValue(Where, Prop, JsonTypeName(T)); return false;
			}
			return CanonicaliseLeaf(Prop, Raw, bDelimited, Owner, Where, OutText, OutError);
		}

		if (const FNumericProperty* NP = CastField<FNumericProperty>(Prop))
		{
			switch (T)
			{
			case EJson::String: Raw = Value->AsString().TrimStartAndEnd(); break;
			case EJson::Number:
			{
				const double D = Value->AsNumber();
				if (NP->IsInteger())
				{
					if (D != FMath::TruncToDouble(D))
					{
						// Truncating silently is the bug class this whole batch exists to remove.
						OutError = FString::Printf(TEXT("'%s' (%s): integer property received the fractional number %s. ")
							TEXT("Pass a whole number, or a string if you meant the text form."),
							*Where, *Prop->GetCPPType(), *FString::SanitizeFloat(D));
						return false;
					}
					Raw = FString::Printf(TEXT("%lld"), (int64)D);
				}
				else
				{
					// SanitizeFloat is %f-based (String.cpp:1172) so it never emits an exponent, which
					// UE's float parser cannot read (PropertyNumeric.cpp:129 accepts only [+-.0-9] and
					// a trailing f).
					Raw = FString::SanitizeFloat(D, 1);
				}
				break;
			}
			default: OutError = RefuseValue(Where, Prop, JsonTypeName(T)); return false;
			}
			return CanonicaliseLeaf(Prop, Raw, bDelimited, Owner, Where, OutText, OutError);
		}

		if (CastField<FStrProperty>(Prop) || CastField<FNameProperty>(Prop) || CastField<FTextProperty>(Prop))
		{
			// Only strings. Coercing 5 into "5" is the silent-conversion habit that produced the
			// "True"/"False" audit failure in the first place.
			if (T != EJson::String) { OutError = RefuseValue(Where, Prop, JsonTypeName(T)); return false; }
			return CanonicaliseLeaf(Prop, Value->AsString(), bDelimited, Owner, Where, OutText, OutError);
		}

		if (CastField<FObjectPropertyBase>(Prop))
		{
			if (T == EJson::Null) { OutText = TEXT("None"); return true; }
			if (T != EJson::String) { OutError = RefuseValue(Where, Prop, JsonTypeName(T)); return false; }
			const FString Path = Value->AsString().TrimStartAndEnd();
			if (Path.IsEmpty()) { OutText = TEXT("None"); return true; }
			return CanonicaliseLeaf(Prop, Path, bDelimited, Owner, Where, OutText, OutError);
		}

		// Delegates, interfaces, anything unmodelled: a string is taken as export text; a JSON
		// container is refused, never silently dropped.
		if (T == EJson::String)
		{
			return CanonicaliseLeaf(Prop, Value->AsString(), bDelimited, Owner, Where, OutText, OutError);
		}
		OutError = RefuseValue(Where, Prop, JsonTypeName(T));
		return false;
	}

	// ---- typed JSON emitter (GAP 3) -----------------------------------------------------------
	// Recursive worker. Emits JSON that MATCHES the value's type instead of stringifying everything:
	// bools as true/false (the string "False" is truthy in every scripting language, which is how a
	// 63-blueprint audit silently read every disabled flag as enabled), numbers as numbers,
	// arrays/sets as arrays, maps/structs as objects.
	static TSharedPtr<FJsonValue> TypedJsonOne(const FProperty* Prop, const void* ValueAddr, UObject* Owner, int32 Depth);

	// TypedJsonOne + ArrayDim. Everything recurses through THIS one, carrying Depth, so a deeply
	// nested struct still hits the depth guard instead of restarting the count at every level.
	static TSharedPtr<FJsonValue> TypedJsonWithDim(const FProperty* Prop, const void* ValueAddr, UObject* Owner, int32 Depth)
	{
		if (!Prop || !ValueAddr) { return MakeShared<FJsonValueNull>(); }
		if (Prop->ArrayDim <= 1) { return TypedJsonOne(Prop, ValueAddr, Owner, Depth); }

		// C-array UPROPERTY (int Foo[4]): every reader below reads ONE element, so report the whole
		// static array instead of silently reporting element 0 as if it were the property.
		TArray<TSharedPtr<FJsonValue>> Items;
		Items.Reserve(Prop->ArrayDim);
		for (int32 i = 0; i < Prop->ArrayDim; ++i)
		{
			Items.Add(TypedJsonOne(Prop, (const uint8*)ValueAddr + (SIZE_T)i * Prop->ElementSize, Owner, Depth));
		}
		return MakeShared<FJsonValueArray>(Items);
	}

	static TSharedPtr<FJsonValue> TypedJsonOne(const FProperty* Prop, const void* ValueAddr, UObject* Owner, int32 Depth)
	{
		if (!Prop || !ValueAddr) { return MakeShared<FJsonValueNull>(); }
		if (Depth > kMaxReflectionDepth) { return MakeShared<FJsonValueString>(ExportLeafText(Prop, ValueAddr, Owner)); }

		if (const FBoolProperty* BP = CastField<FBoolProperty>(Prop))
		{
			return MakeShared<FJsonValueBoolean>(BP->GetPropertyValue(ValueAddr));
		}

		// Enums before numerics, same reason as the writer above.
		if (CastField<FEnumProperty>(Prop))
		{
			return MakeShared<FJsonValueString>(ExportLeafText(Prop, ValueAddr, Owner));
		}
		if (const FByteProperty* ByteP = CastField<FByteProperty>(Prop))
		{
			if (ByteP->Enum) { return MakeShared<FJsonValueString>(ExportLeafText(Prop, ValueAddr, Owner)); }
			return MakeShared<FJsonValueNumber>((double)ByteP->GetPropertyValue(ValueAddr));
		}
		if (const FNumericProperty* NP = CastField<FNumericProperty>(Prop))
		{
			// int64 beyond 2^53 loses precision as a JSON number; "value" (export text) stays exact,
			// which is why it is kept alongside "typed" rather than replaced by it.
			return NP->IsFloatingPoint()
				? MakeShared<FJsonValueNumber>(NP->GetFloatingPointPropertyValue(ValueAddr))
				: MakeShared<FJsonValueNumber>((double)NP->GetSignedIntPropertyValue(ValueAddr));
		}

		if (const FStrProperty* SP = CastField<FStrProperty>(Prop))
		{
			return MakeShared<FJsonValueString>(SP->GetPropertyValue(ValueAddr));
		}
		if (const FNameProperty* NmP = CastField<FNameProperty>(Prop))
		{
			return MakeShared<FJsonValueString>(NmP->GetPropertyValue(ValueAddr).ToString());
		}
		if (const FTextProperty* TP = CastField<FTextProperty>(Prop))
		{
			// Display string. The lossless NSLOCTEXT form stays in "value" — same trade-off, and the
			// same reasoning, as the DataTable textFormat work (docs/02_GOTCHAS.md §5e).
			return MakeShared<FJsonValueString>(TP->GetPropertyValue(ValueAddr).ToString());
		}

		if (const FObjectPropertyBase* OP = CastField<FObjectPropertyBase>(Prop))
		{
			if (UObject* O = OP->GetObjectPropertyValue(ValueAddr))
			{
				return MakeShared<FJsonValueString>(O->GetPathName());
			}
			// A soft reference to an UNLOADED asset resolves to null but still holds a path. Reporting
			// null there would be a lie, so fall back to the export text and only call it null when the
			// engine itself says "None".
			const FString Exported = ExportLeafText(Prop, ValueAddr, Owner);
			if (Exported.IsEmpty() || Exported.Equals(TEXT("None"), ESearchCase::IgnoreCase))
			{
				return MakeShared<FJsonValueNull>();
			}
			return MakeShared<FJsonValueString>(Exported);
		}

		if (const FArrayProperty* AP = CastField<FArrayProperty>(Prop))
		{
			FScriptArrayHelper H(AP, ValueAddr);
			TArray<TSharedPtr<FJsonValue>> Items;
			Items.Reserve(H.Num());
			for (int32 i = 0; i < H.Num(); ++i)
			{
				Items.Add(TypedJsonOne(AP->Inner, H.GetRawPtr(i), Owner, Depth + 1));
			}
			return MakeShared<FJsonValueArray>(Items);
		}
		if (const FSetProperty* SetP = CastField<FSetProperty>(Prop))
		{
			FScriptSetHelper H(SetP, ValueAddr);
			TArray<TSharedPtr<FJsonValue>> Items;
			Items.Reserve(H.Num());
			for (int32 i = 0, Max = H.GetMaxIndex(); i < Max; ++i)   // sparse: holes must be skipped
			{
				if (!H.IsValidIndex(i)) { continue; }
				Items.Add(TypedJsonOne(SetP->ElementProp, H.GetElementPtr(i), Owner, Depth + 1));
			}
			return MakeShared<FJsonValueArray>(Items);
		}
		if (const FMapProperty* MP = CastField<FMapProperty>(Prop))
		{
			FScriptMapHelper H(MP, ValueAddr);
			TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
			for (int32 i = 0, Max = H.GetMaxIndex(); i < Max; ++i)
			{
				if (!H.IsValidIndex(i)) { continue; }
				FString Key;
				// Undelimited so an FName key reads Foo, not "Foo" — that is the spelling
				// set_property feeds back through the key property.
				MP->KeyProp->ExportTextItem_Direct(Key, H.GetKeyPtr(i), nullptr, Owner, PPF_None);
				Obj->SetField(Key, TypedJsonOne(MP->ValueProp, H.GetValuePtr(i), Owner, Depth + 1));
			}
			return MakeShared<FJsonValueObject>(Obj);
		}

		if (const FStructProperty* StP = CastField<FStructProperty>(Prop))
		{
			TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
			for (TFieldIterator<FProperty> It(StP->Struct); It; ++It)
			{
				FProperty* M = *It;
				if (!M) { continue; }
				// Reflected (mangled) name, because that is the name the struct literal writer emits.
				Obj->SetField(M->GetName(), TypedJsonWithDim(M, M->ContainerPtrToValuePtr<void>(ValueAddr), Owner, Depth + 1));
			}
			return MakeShared<FJsonValueObject>(Obj);
		}

		// Delegates, interfaces, anything unmodelled: export text, which is never wrong.
		return MakeShared<FJsonValueString>(ExportLeafText(Prop, ValueAddr, Owner));
	}

	TSharedPtr<FJsonValue> PropertyValueToTypedJson(const FProperty* Prop, const void* ValueAddr, UObject* Owner)
	{
		return TypedJsonWithDim(Prop, ValueAddr, Owner, 0);
	}

	TSharedPtr<FJsonValue> PropertyValueToTypedJsonElement(const FProperty* Prop, const void* ValueAddr, UObject* Owner)
	{
		// ONE element. TypedJsonWithDim would loop ArrayDim from ValueAddr, which reads off the end of
		// the allocation when ValueAddr is already element N of a C-array UPROPERTY - the exact shape
		// element-level addressing introduced.
		return TypedJsonOne(Prop, ValueAddr, Owner, 0);
	}

	// ResolvePropertyPath moved to MifBridgeCommon.cpp (declared in MifBridgeHandlers.h). It existed
	// THREE times — here, in MifBridgeNodes6.cpp as ResolveReadPropertyPath, and in
	// MifBridgeInherited.cpp as ResolvePropertyPathLocal — so the PM-003 write bracket's own resolver
	// was in triplicate and a fix to one left two exposed. Do NOT re-add a local copy.

	// --- THE caller-value converter and THE PM-003 import, shared -----------------------------
	// Both are declared in MifBridgeHandlers.h and defined here, next to FScratchValue and the
	// conversion helpers they stand on. set_property used to inline the two-branch dispatch below;
	// Batch N gave edit_container and reset_property_to_default the same job, and three copies of
	// "which forms does `value` accept" is how two endpoints end up disagreeing about it (PM-005).

	bool PropertyImportTextFromJson(const TSharedPtr<FJsonValue>& Value, const FProperty* Prop,
		UObject* Owner, const FString& Where, FString& OutText, FString& OutForm,
		bool& bOutTypeValidated, FString& OutTypeNote, FString& OutError)
	{
		OutText.Reset(); OutForm.Reset(); OutTypeNote.Reset(); OutError.Reset();
		bOutTypeValidated = false;
		if (!Value.IsValid() || !Prop)
		{
			OutError = TEXT("value required (UE export text as a string, or typed JSON: array / object / number / bool / null)");
			return false;
		}
		if (Value->Type == EJson::String)
		{
			// A string reaches ImportText_Direct byte-for-byte, so every caller sending UE export text
			// is unaffected - but it is TYPE-CHECKED first (PM-006). Without this,
			// value:"not-a-float" on a float property imports 0.0, reports success, and passes the
			// post-write check by comparing 0 against 0.
			OutText = Value->AsString();
			if (CastField<FBoolProperty>(Prop)) { OutText = NormalizeBoolLiteral(OutText); }
			OutForm = TEXT("string");
			if (!ValidatePropertyText(Prop, OutText, Where, OutTypeNote, &bOutTypeValidated))
			{
				// A refusal IS a validation: say so, so the response cannot read as "unchecked".
				bOutTypeValidated = true;
				OutError = OutTypeNote;
				return false;
			}
			return true;
		}
		// The array-wipe bug lived here: JStr returned "" for a JSON array and FArrayProperty accepted
		// "" as "empty the array" WITH SUCCESS.
		if (!JsonToPropertyText(Value, Prop, /*bDelimited*/ false, Owner, 0, Where, OutText, OutError))
		{
			bOutTypeValidated = true;
			return false;
		}
		OutForm = TEXT("json");
		// CanonicaliseLeaf calls ValidatePropertyText for every leaf it converts, so a container or
		// struct given as typed JSON is checked member by member.
		bOutTypeValidated = true;
		return true;
	}

	bool ImportPropertyTextSafely(const FProperty* Prop, const FString& Text, const void* Seed,
		void* Dest, UObject* Owner, FString& OutStagedText, FString& OutError)
	{
		OutStagedText.Reset(); OutError.Reset();
		if (!Prop || !Dest) { OutError = TEXT("null property or destination"); return false; }

		FScratchValue Scratch(Prop);
		if (Seed)
		{
			// Seeding from the current value is what preserves partial-struct-literal semantics -
			// "(X=5)" leaves Y and Z alone, exactly as the Details panel behaves.
			Prop->CopySingleValue(Scratch.Mem, Seed);
		}
		FStringOutputDevice ErrText;
		const TCHAR* R = Prop->ImportText_Direct(*Text, Scratch.Mem, Owner, PPF_None, &ErrText);
		if (R == nullptr)
		{
			OutError = FString::Printf(TEXT("ImportText_Direct failed for '%s': %s (nothing was written). Accepts %s."),
				*Text, *ErrText, *AcceptedFormHint(Prop));
			return false;
		}
		OutStagedText = ExportLeafText(Prop, Scratch.Mem, Owner);
		// ONE element. The scratch is the only thing the parser ever touched (PM-003).
		Prop->CopySingleValue(Dest, Scratch.Mem);
		return true;
	}

	//   in:  { objectPath: "/Game/..." }  OR  { blueprintId: "...", widgetName: "MyText" }
	//        propertyPath: "A.B.C" (dot path), now with ELEMENT accessors:
	//                      "OverrideMaterials[1]", "FloatCurves[1].Keys[0].Value",
	//                      "ScalarParameterValues[ParameterInfo.Name=Roughness].ParameterValue",
	//                      "SomeMap{Alpha}.Threshold"
	//        value: UE export text as a STRING (unchanged), OR typed JSON - array/object/number/bool/null
	//        overrideFlag: set | refuse | ignore   (aliases editCondition, override; default "set")
	//        enforceClamps: bool                   (aliases clamp, respectClamps; default false)
	//   out: { target, propertyPath, leafProperty, leafType, applied, verified, changed,
	//          valueForm, importText, valueBefore, valueAfter, typed,
	//          elementsBefore?, elementsAfter?, coerced?, valueStaged?, note?, recompiled?,
	//          notification, memberProperty, chainDepth, reconstructed, retargetedTo?, verifiedOn,
	//          isElement, elementPath?, elementIndex?, elementOrdering?, rehashed?,
	//          editCondition, editConditionKind, editConditionMet, editConditionHides,
	//          overrideFlagWritten?, overrideFlagUnmet?, clampViolation?, clampApplied?, warnings? }
	//
	// Registered as self-managed (RunEndpoint opens NO transaction) because the widget-BP branch
	// calls CompileBlueprint, which must not run inside a transaction. We open a tight inner
	// transaction around ONLY the reflection write; the compile happens after it closes.
	//
	// BATCH N adds two things to this handler and neither is optional behaviour:
	//
	//  1. EDITCONDITION. Many engine properties are GATED: writing UStaticMeshComponent::MinLOD
	//     without bOverrideMinLOD is read by nothing - StaticMeshRender.cpp:248 is
	//     `bOverrideMinLOD ? MinLOD : SMCurrentMinLOD` - and FPostProcessSettings has 423 more of
	//     them. The write lands in memory and the CAPABILITY does not, so the existing verification
	//     bracket cannot see it: the value genuinely changed. This handler now detects the companion
	//     flag and either SETS it and says so, or REFUSES naming it. It never writes a value the
	//     engine will ignore while reporting success.
	//
	//  2. CLAMPS. ClampMin/ClampMax are applied ONLY by the panel's typed numeric setters
	//     (PropertyHandleImpl.cpp:870-931); ImportText never reads the metadata, and a grep of
	//     Runtime/CoreUObject for ClampMin finds only UPROPERTY declarations, no consuming code. So
	//     this endpoint mirrors the panel's copy/paste path, which is genuinely unclamped. It reports
	//     the violation by default and will coerce on request - it does not silently exceed a bound
	//     the panel would have refused, and it does not silently clamp either, which would be the
	//     same bug class pointing the other way.
	void H_set_property(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// An IGNORED parameter is worse than a rejected one - the caller gets ok:true and then debugs
		// the wrong subsystem (01_POSTMORTEMS.md, spawn_actor_in_level's dropped `mesh`: four ground
		// rebuilds). RejectUnknownParams already tolerates the batch dispatcher's "op" key centrally.
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			  TEXT("propertyPath"), TEXT("value"),
			  TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override"),
			  TEXT("enforceClamps"), TEXT("clamp"), TEXT("respectClamps") },
			TEXT("objectPath | (blueprintId or path) + widgetName, propertyPath, value, overrideFlag (set|refuse|ignore), enforceClamps. "
				 "objectPath also reaches a blueprint's COMPONENTS: take the component's templatePath "
				 "from list_components (the ..._GEN_VARIABLE path) and pass it as objectPath. "
				 "propertyPath may be NESTED - 'BodyInstance.bSimulatePhysics' works."),
			{{ TEXT("actorPath"),
			   TEXT("use objectPath - a placed actor's path IS an objectPath") },
			 { TEXT("componentName"),
			   TEXT("components ARE supported, just not by name here: call list_components, take the component's templatePath (the ..._GEN_VARIABLE one) and pass it as objectPath. That is how you set an AudioComponent's Sound, a CharacterMovement's MaxWalkSpeed, or BodyInstance.bSimulatePhysics on a mesh") },
			 { TEXT("component"),
			   TEXT("same as componentName - pass the component's templatePath from list_components as objectPath") },
			 { TEXT("format"),
			   TEXT("no output format switch here; the response always carries BOTH valueAfter (export text) and typed (typed JSON)") },
			 { TEXT("verify"),
			   TEXT("not optional - every write is verified by re-export, which is what makes ok:true mean written") },
			 { TEXT("operation"),
			   TEXT("set_property writes a VALUE; add/insert/remove/clear/swap/resize/setKey on a container are edit_container") }}))
		{
			return;
		}

		const FString PropertyPath = JStr(In, TEXT("propertyPath"));
		if (PropertyPath.IsEmpty()) { Fail(Out, TEXT("propertyPath required (dot path, e.g. Font.Size)")); return; }
		if (!In->HasField(TEXT("value")))
		{
			Fail(Out, TEXT("value required (UE export text as a string, or typed JSON: array / object / number / bool / null)"));
			return;
		}
		const TSharedPtr<FJsonValue> ValueJson = In->TryGetField(TEXT("value"));
		if (!ValueJson.IsValid())
		{
			Fail(Out, TEXT("value required (UE export text as a string, or typed JSON: array / object / number / bool / null)"));
			return;
		}

		// A string-to-enum dispatch must never have a silent default (PM-002). An unrecognised
		// overrideFlag is refused naming the accepted set, not quietly treated as "set".
		FString OverrideFlagMode = JStrAny(In, { TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override") }, TEXT("set"));
		OverrideFlagMode = OverrideFlagMode.TrimStartAndEnd().ToLower();
		if (OverrideFlagMode != TEXT("set") && OverrideFlagMode != TEXT("refuse") && OverrideFlagMode != TEXT("ignore"))
		{
			Fail(Out, FString::Printf(
				TEXT("overrideFlag '%s' is not one of set | refuse | ignore. 'set' (the default) writes the companion ")
				TEXT("EditCondition flag alongside the value and REPORTS it; 'refuse' fails naming the flag; 'ignore' ")
				TEXT("writes anyway and warns. Nothing was changed."),
				*JStrAny(In, { TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override") })));
			return;
		}
		const bool bEnforceClamps = JBoolAny(In, { TEXT("enforceClamps"), TEXT("clamp"), TEXT("respectClamps") }, false);

		// --- Resolve the target object ------------------------------------------------
		// ONE resolver, shared with get_property / list_object_properties / describe_property /
		// edit_container / reset_property_to_default / diff_properties_vs_default (PM-005).
		UWidgetBlueprint* OwningWidgetBP = nullptr;
		UObject* Target = ResolvePropertyTarget(In, Out, &OwningWidgetBP);
		if (!Target) { return; }
		if (OwningWidgetBP)
		{
			// THIS branch is the compile-heavy one (FKismetEditorUtilities::CompileBlueprint at the
			// tail of this handler), and reinstancing captured by batch's open transaction restores a
			// dead CDO on the next Ctrl-Z. It used to be fenced by banning set_property from batch
			// ENTIRELY, which also blocked the objectPath branch - CDO edits, component templates,
			// node properties, placed actors - that compiles nothing, while docs/02_GOTCHAS.md 5d
			// tells callers to batch exactly those. The refusal sits on the branch with the hazard.
			if (IsBatchTransactionOpen())
			{
				Fail(Out, FString::Printf(
					TEXT("set_property's widget-Blueprint branch (widgetName '%s') recompiles '%s', which must not happen ")
					TEXT("inside batch's open transaction - call it standalone. The objectPath branch of set_property IS ")
					TEXT("batchable; only widgetName is refused here."),
					*JStr(In, TEXT("widgetName")), *OwningWidgetBP->GetName()));
				return;
			}
		}

		// --- Walk the dot path --------------------------------------------------------
		// The accessor-aware walker: every segment's FProperty (what FEditPropertyChain needs), the
		// element address when an accessor was used, and the DECLARING container address (what an
		// EditCondition sibling lookup needs).
		FPropertyPathResolution Res;
		FString Error;
		if (!ResolvePropertyPathEx(Target, PropertyPath, Res, Error))
		{
			Fail(Out, Error);
			return;
		}
		FProperty* Leaf      = Res.Leaf;
		void*      LeafAddr  = Res.LeafAddr;
		UObject*   LeafOwner = Res.LeafOwner;
		TArray<FProperty*> PropertyChainSegments = Res.Chain;
		// Captured BEFORE the write: a construction-script rerun renames the object we are holding to
		// TRASH_<Class>_N, so its path is only readable now (see the retarget block below).
		const FString TargetPathAtWrite = Target->GetPathName();
		const FString LeafOwnerPathAtWrite = LeafOwner->GetPathName();

		// --- EDITCONDITION: is this property GATED, and is the gate open? ---------------
		// Decided BEFORE anything is written, because a refusal must leave nothing behind (PM-007:
		// a cancelled transaction reverts nothing at all, so order is the only mechanism).
		FEditConditionInfo EC;
		InspectEditCondition(Leaf, Res.LeafContainerAddr, EC);
		const bool bGateClosed = EC.bEvaluated && !EC.bMet;
		if (bGateClosed && OverrideFlagMode == TEXT("refuse"))
		{
			Out->SetStringField(TEXT("propertyPath"), PropertyPath);
			Out->SetStringField(TEXT("leafProperty"), Leaf->GetName());
			Out->SetStringField(TEXT("leafType"), Leaf->GetCPPType());
			Out->SetBoolField(TEXT("applied"), false);
			Out->SetStringField(TEXT("editCondition"), EC.MetaText);
			Out->SetStringField(TEXT("editConditionKind"), EC.Kind);
			Out->SetBoolField(TEXT("editConditionMet"), false);
			Fail(Out, FString::Printf(
				TEXT("'%s' (%s) is gated by meta EditCondition=\"%s\" and the companion flag '%s' is currently %s. ")
				TEXT("The engine reads the FLAG, not the value, so writing '%s' now would change memory and change nothing else. ")
				TEXT("Pass overrideFlag:\"set\" to write the flag with the value (the default), overrideFlag:\"ignore\" to stage ")
				TEXT("the value behind a closed gate on purpose, or set '%s' yourself first. Nothing was changed."),
				*PropertyPath, *Leaf->GetCPPType(), *EC.MetaText, *EC.FlagName,
				EC.bRequiredFlagValue ? TEXT("False") : TEXT("True"),
				*PropertyPath, *EC.FlagName));
			return;
		}

		// --- Turn `value` into the export text ImportText_Direct wants ----------------
		// ONE converter, shared with edit_container and reset_property_to_default.
		FString ImportStr, ValueForm, TypeValidationNote, ConvError;
		bool bTypeValidated = false;
		if (!PropertyImportTextFromJson(ValueJson, Leaf, LeafOwner, PropertyPath,
			ImportStr, ValueForm, bTypeValidated, TypeValidationNote, ConvError))
		{
			Out->SetStringField(TEXT("propertyPath"), PropertyPath);
			Out->SetStringField(TEXT("leafProperty"), Leaf->GetName());
			Out->SetStringField(TEXT("leafType"), Leaf->GetCPPType());
			Out->SetBoolField(TEXT("applied"), false);
			Out->SetBoolField(TEXT("typeValidated"), bTypeValidated);
			Fail(Out, ConvError);
			return;
		}

		// --- CLAMPS, before the import, and only when explicitly asked for -------------
		FPropertyClampInfo ClampInfo;
		InspectClamps(Leaf, ClampInfo);
		bool bClampApplied = false;
		FString ClampAppliedMeta, ClampAppliedFrom, ClampAppliedTo;
		if (bEnforceClamps && ClampInfo.bNumeric && (ClampInfo.bHasClampMin || ClampInfo.bHasClampMax))
		{
			double Numeric = 0.0;
			if (ParseWholeNumber(ImportStr, Numeric))
			{
				double Clamped = Numeric;
				FString Meta;
				if (ClampInfo.bHasClampMin && Clamped < ClampInfo.ClampMin) { Clamped = ClampInfo.ClampMin; Meta = TEXT("ClampMin"); }
				if (ClampInfo.bHasClampMax && Clamped > ClampInfo.ClampMax) { Clamped = ClampInfo.ClampMax; Meta = TEXT("ClampMax"); }
				if (Clamped != Numeric)
				{
					const FNumericProperty* NP = CastField<FNumericProperty>(Leaf);
					const FString NewText = (NP && NP->IsFloatingPoint())
						? FString::SanitizeFloat(Clamped)
						: FString::Printf(TEXT("%lld"), (int64)Clamped);
					ClampAppliedMeta = Meta;
					ClampAppliedFrom = ImportStr;
					ClampAppliedTo   = NewText;
					ImportStr        = NewText;
					bClampApplied    = true;
				}
			}
		}

		// --- Snapshot BEFORE, so "did the write land" is answerable ---------------------
		const FString BeforeText  = ExportLeafText(Leaf, LeafAddr, LeafOwner);
		const int32   BeforeCount = ContainerElementCount(Leaf, LeafAddr);
		// Dirty-flag snapshot: MarkPackageDirty() below runs before the write is verified, so a write
		// that provably did NOT land used to leave the package dirty anyway - and list_dirty_packages /
		// save_dirty_packages would then report and SAVE a package whose only "change" was a failed
		// write. Restored on the verification-failure path.
		UPackage* LeafPackage = LeafOwner ? LeafOwner->GetOutermost() : nullptr;   // re-pointed after a rerun
		const bool bPackageWasDirty = LeafPackage && LeafPackage->IsDirty();

		// --- Details-panel write bracket, scoped to a TIGHT inner transaction ---------
		// CompileBlueprint (below) reinstances and must NOT be inside a transaction, so the
		// transaction closes at the end of this block. ErrText is declared outside so the
		// parse result survives for the Fail message.
		//
		// PM-003: ImportText_Direct parses IN PLACE and can consume/clear the destination before it
		// decides the text is bad, so a rejected value used to WIPE the property it failed to set.
		// Import into a scratch copy first and only publish it on success. The scratch spans the
		// leaf's whole ArrayDim and the import targets ELEMENT LeafCArrayIndex inside it, so
		// addressing FloatCurves[2] neither reads nor writes past the allocation.
		FStringOutputDevice ErrText;
		bool bApplied = false;
		FString StagedText;      // export of the PARSED value, before publishing
		FString NotificationForm = TEXT("plain");
		FString MemberPropertyName;
		int32   ChainDepth = 0;
		bool    bFlagWritten = false, bFlagBefore = false, bFlagAfter = false;
		bool    bRehashed = false;
		FString DuplicateRefusal;
		const bool bSetFlagNow = bGateClosed && OverrideFlagMode == TEXT("set") && EC.FlagProp && Res.LeafContainerAddr;
		{
			FScratchValue Scratch(Leaf);
			uint8* LeafArrayBase = (uint8*)LeafAddr - (SIZE_T)Res.LeafCArrayIndex * Leaf->ElementSize;
			uint8* ScratchElem   = (uint8*)Scratch.Mem + (SIZE_T)Res.LeafCArrayIndex * Leaf->ElementSize;
			Leaf->CopyCompleteValue(Scratch.Mem, LeafArrayBase);   // start from the CURRENT value, so a partial
			                                                       // import that only sets some struct members
			                                                       // behaves like the Details panel does.

			const TCHAR* R = Leaf->ImportText_Direct(*ImportStr, ScratchElem, LeafOwner, PPF_None, &ErrText);

			if (R != nullptr)
			{
				// A SET element is hashed BY ITS VALUE, so editing one in place can collide with an
				// element that already exists. The panel refuses outright rather than silently
				// swallowing one ("Duplicate elements are not allowed in Set properties",
				// PropertyHandleImpl.cpp:389); FindElementIndex is a linear compare, so this costs one
				// pass and needs no GetTypeHash.
				if (FSetProperty* SetProp = CastField<FSetProperty>(Res.ElementContainerProp))
				{
					if (Res.ElementContainerAddr)
					{
						FScriptSetHelper DupHelper(SetProp, Res.ElementContainerAddr);
						const int32 DupIndex = DupHelper.FindElementIndex(ScratchElem);
						if (DupIndex != INDEX_NONE && DupHelper.GetElementPtr(DupIndex) != LeafAddr)
						{
							DuplicateRefusal = FString::Printf(
								TEXT("writing '%s' would make it equal to an element the set already contains. TSet rejects duplicates ")
								TEXT("(the Details panel refuses the same edit: PropertyHandleImpl.cpp:389). Nothing was changed - use ")
								TEXT("edit_container {operation:\"remove\"} then {operation:\"add\"} if you meant to replace it."),
								*PropertyPath);
						}
					}
				}

				if (DuplicateRefusal.IsEmpty())
				{
					// Canonical text of what we are about to write, produced by the SAME exporter as
					// BeforeText - so the two are directly comparable and "did the caller ask for a
					// change" is a string compare, not a guess about the caller's input spelling.
					StagedText = ExportLeafText(Leaf, ScratchElem, LeafOwner);

					// Batch L, defect 3. The comment that used to sit on PostEditChangeProperty said it
					// "propagates to instances/archetype". IT DOES NOT. UObject::PostEditChangeProperty is
					// a delegate broadcast plus an interactive-snapshot and nothing else
					// (Obj.cpp:433-444). PostEditChangeChainProperty is the one that walks
					// GetArchetypeInstances (Obj.cpp:501-509) - and it ENDS by calling
					// PostEditChangeProperty anyway (Obj.cpp:541), so the chain form is a strict superset
					// of what this used to do. Switching also reaches the 40 PostEditChangeChainProperty
					// overrides in Runtime/Engine/Private that never fired before, among them
					// UMeshComponent's CleanUpOverrideMaterials (MeshComponent.cpp:155-166).
					//
					// PropagatePostEditChange check()s the active member node (Obj.cpp:660), so a chain is
					// either built in full or not used at all - never handed over half-built.
					FEditPropertyChain EditChain;
					bool bChainBuilt = PropertyChainSegments.Num() > 0;
					for (FProperty* Segment : PropertyChainSegments)
					{
						if (!Segment) { bChainBuilt = false; break; }
						EditChain.AddTail(Segment);
					}
					if (bChainBuilt)
					{
						// The chain's tail is the DECLARED member, which for an element write is the
						// container itself rather than the element's inner property - SetActivePropertyNode
						// must therefore be given a property that is actually in the chain.
						FProperty* ActiveLeaf = PropertyChainSegments.Last();
						bChainBuilt = EditChain.SetActivePropertyNode(ActiveLeaf)
							&& EditChain.SetActiveMemberPropertyNode(PropertyChainSegments[0]);
					}

					FScopedTransaction Tx(NSLOCTEXT("MifBridge", "SetProperty", "Mif Bridge: set_property"));
					LeafOwner->Modify();
					if (bChainBuilt) { LeafOwner->PreEditChange(EditChain); } else { LeafOwner->PreEditChange(Leaf); }

					// The companion EditCondition flag, written INSIDE the same Modify/PreEditChange..
					// PostEditChange bracket and the same transaction, so one Ctrl-Z undoes both and the
					// pair is announced once. It deliberately does NOT fire its own notification: on a
					// placed actor's component that would rerun the construction scripts mid-write and
					// leave LeafAddr dangling before the value is even published.
					if (bSetFlagNow)
					{
						void* FlagAddr = EC.FlagProp->ContainerPtrToValuePtr<void>(Res.LeafContainerAddr);
						bFlagBefore = EC.FlagProp->GetPropertyValue(FlagAddr);
						EC.FlagProp->SetPropertyValue(FlagAddr, EC.bRequiredFlagValue);
						bFlagAfter  = EC.FlagProp->GetPropertyValue(FlagAddr);   // MEASURED, not echoed
						bFlagWritten = true;
					}

					// Publish. CopySingleValue for an element (one row of a container / one slot of a
					// C-array); CopyCompleteValue for a whole property, which is what ArrayDim needs.
					if (Res.bLeafIsElement) { Leaf->CopySingleValue(LeafAddr, ScratchElem); }
					else                    { Leaf->CopyCompleteValue(LeafAddr, Scratch.Mem); }

					// A set's hash table is keyed on element VALUES, so an in-place element edit leaves it
					// stale and Find stops seeing entries the set still holds. The panel rehashes for
					// exactly this reason (PropertyHandleImpl.cpp:522-534). Element storage is not moved by
					// a rehash, so LeafAddr stays valid for the verification read below.
					if (Res.ElementContainerProp && Res.ElementContainerAddr)
					{
						if (FSetProperty* SetProp = CastField<FSetProperty>(Res.ElementContainerProp))
						{
							FScriptSetHelper(SetProp, Res.ElementContainerAddr).Rehash();
							bRehashed = true;
						}
					}

					// Only fire the edit notification for a write that actually happened. It used to run
					// unconditionally, so a failed import still told listeners/instances the value changed.
					FPropertyChangedEvent Evt(Leaf, EPropertyChangeType::ValueSet);
					// MemberProperty = the OUTERMOST member, as the Details panel reports it
					// (PropertyNode.cpp:3081-3083). FPropertyChangedEvent's constructor sets it to the leaf
					// (UnrealType.h:6349-6350), and AActor::PostEditChangeProperty switches on
					// MemberProperty (ActorEditor.cpp:134-135) - so member-keyed handlers never fired for
					// a dotted path like "Settings.BloomIntensity".
					if (bChainBuilt) { Evt.SetActiveMemberProperty(PropertyChainSegments[0]); }
					if (bChainBuilt)
					{
						FPropertyChangedChainEvent ChainEvt(EditChain, Evt);
						ChainEvt.ChangeType = EPropertyChangeType::ValueSet;
						LeafOwner->PostEditChangeChainProperty(ChainEvt);
					}
					else
					{
						LeafOwner->PostEditChangeProperty(Evt);
					}
					NotificationForm = bChainBuilt ? TEXT("chain") : TEXT("plain");
					MemberPropertyName = bChainBuilt ? PropertyChainSegments[0]->GetName() : Leaf->GetName();
					ChainDepth = PropertyChainSegments.Num();
					LeafOwner->MarkPackageDirty();
					bApplied = true;
				}
			}
		}   // transaction (if any) commits here - BEFORE any compile

		if (!DuplicateRefusal.IsEmpty())
		{
			// Refused after the scratch import and before ANY publish, so the object is byte-identical
			// to what it was when the call arrived.
			Out->SetStringField(TEXT("propertyPath"), PropertyPath);
			Out->SetStringField(TEXT("leafProperty"), Leaf->GetName());
			Out->SetBoolField(TEXT("applied"), false);
			Out->SetBoolField(TEXT("nothingModified"), true);
			Fail(Out, DuplicateRefusal);
			return;
		}

		if (!bApplied)
		{
			Fail(Out, FString::Printf(TEXT("ImportText_Direct failed for '%s' = '%s': %s (property left unchanged). Accepts %s."),
				*PropertyPath, *ImportStr, *ErrText, *AcceptedFormHint(Leaf)));
			return;
		}

		// --- VERIFY THE WRITE LANDED --------------------------------------------------
		// Read the property back through the same exporter AFTER PostEditChangeProperty has run.
		// This is the guard that makes "ok:true and nothing written" impossible for EVERY property
		// kind, including ones nobody has exercised yet - the array bug is only the one we found.
		// It runs AFTER the transaction closes on purpose: PostEditChangeProperty is itself a place a
		// value can be silently rejected, so it must be inside what we verify. If the guard trips, the
		// asset holds its ORIGINAL value - nothing was written, so there is nothing to roll back - and
		// the dirty flag this call raised is put back the way it was found, so list_dirty_packages /
		// save_dirty_packages do not report and then SAVE a package whose only "change" failed.
		// --- RE-RESOLVE BEFORE READING BACK (Batch L, defect 3) -------------------------
		// On a PLACED ACTOR's component the bracket above triggers the actor's construction scripts:
		// UActorComponent::PreEditChange registers an FComponentReregisterContext
		// (ActorComponent.cpp:806-822) and ConsolidatedPostEditChange consumes it and calls
		// MyOwner->RerunConstructionScripts() for any non-template owner on a non-Interactive change
		// (ActorComponent.cpp:927-941). The rerun DestroyComponent()s every CS-created component and
		// renames it TRASH_<Class>_N (ActorConstruction.cpp:167-210), replacing it with a NEW object of
		// the same name. LeafOwner/LeafAddr are dangling at that point, and the verification below used
		// to read the TRASHED object, find the value it had just written there, and report
		// verified:true about an object that is no longer part of the actor - a use-after-free after
		// the next GC. Re-resolve by PATH (StaticFindObject, never StaticLoadObject: the package is
		// already loaded and nothing here may resurrect anything), then verify the object the caller
		// actually addressed.
		bool bReconstructed = false;
		FString RetargetedTo;
		auto IsTrashed = [](const UObject* Obj)
		{
			return Obj == nullptr || !IsValid(Obj) || Obj->GetName().StartsWith(TEXT("TRASH_"));
		};
		if (IsTrashed(LeafOwner) || IsTrashed(Target))
		{
			bReconstructed = true;
			UObject* NewTarget = StaticFindObject(UObject::StaticClass(), nullptr, *TargetPathAtWrite);
			FPropertyPathResolution NewRes;
			FString RetargetError;
			if (NewTarget && !IsTrashed(NewTarget)
				&& ResolvePropertyPathEx(NewTarget, PropertyPath, NewRes, RetargetError))
			{
				Target = NewTarget;
				Leaf = NewRes.Leaf; LeafAddr = NewRes.LeafAddr; LeafOwner = NewRes.LeafOwner;
				PropertyChainSegments = NewRes.Chain;
				Res = NewRes;
				RetargetedTo = LeafOwner->GetPathName();
				LeafPackage = LeafOwner->GetOutermost();
			}
			else
			{
				// NEVER fall back to reading the trashed pointer. An unverifiable write is reported as
				// unverifiable; the alternative is the confident wrong answer this defect was made of.
				Out->SetStringField(TEXT("target"), TargetPathAtWrite);
				Out->SetStringField(TEXT("propertyPath"), PropertyPath);
				Out->SetBoolField(TEXT("applied"), true);
				Out->SetBoolField(TEXT("verified"), false);
				Out->SetBoolField(TEXT("reconstructed"), true);
				Out->SetStringField(TEXT("valueStaged"), StagedText);
				Fail(Out, FString::Printf(
					TEXT("the write to '%s' triggered a construction-script rerun that destroyed '%s' (renamed TRASH_*), ")
					TEXT("and the replacement could not be re-resolved at '%s'%s. The write is UNVERIFIED - it may or may ")
					TEXT("not have survived the rerun. Re-read the actor with list_level_actors / get_property before ")
					TEXT("retrying, and prefer editing the component TEMPLATE (...._GEN_VARIABLE) if you want the change to ")
					TEXT("apply to every instance."),
					*PropertyPath, *LeafOwnerPathAtWrite, *TargetPathAtWrite,
					RetargetError.IsEmpty() ? TEXT("") : *(TEXT(" (") + RetargetError + TEXT(")"))));
				return;
			}
		}

		const FString AfterText  = ExportLeafText(Leaf, LeafAddr, LeafOwner);
		const int32   AfterCount = ContainerElementCount(Leaf, LeafAddr);
		const bool bChanged         = !AfterText.Equals(BeforeText,  ESearchCase::CaseSensitive);
		const bool bRequestedChange = !StagedText.Equals(BeforeText, ESearchCase::CaseSensitive);
		const bool bCoerced         = !AfterText.Equals(StagedText,  ESearchCase::CaseSensitive) || bClampApplied;

		// Numbers first so a failure response is still numerically checkable.
		Out->SetStringField(TEXT("target"), Target->GetPathName());
		Out->SetStringField(TEXT("propertyPath"), PropertyPath);
		Out->SetStringField(TEXT("leafProperty"), Leaf->GetName());
		Out->SetStringField(TEXT("leafType"), Leaf->GetCPPType());
		Out->SetStringField(TEXT("valueForm"), ValueForm);
		Out->SetStringField(TEXT("importText"), ImportStr);
		Out->SetStringField(TEXT("valueBefore"), BeforeText);
		Out->SetStringField(TEXT("valueAfter"), AfterText);
		Out->SetBoolField(TEXT("changed"), bChanged);
		// Defect 2: whether the VALUE was type-checked against the destination property, as opposed to
		// merely written. These are different questions and used to have one answer.
		Out->SetBoolField(TEXT("typeValidated"), bTypeValidated);
		if (!bTypeValidated && !TypeValidationNote.IsEmpty())
		{
			Out->SetStringField(TEXT("typeValidationNote"), TypeValidationNote);
		}
		// Defect 3: which notification fired, on which member, and which OBJECT the read-back came
		// from. verifiedOn is the checkable one - it must equal retargetedTo when a rerun happened.
		Out->SetStringField(TEXT("notification"), NotificationForm);
		Out->SetNumberField(TEXT("chainDepth"), ChainDepth);
		if (!MemberPropertyName.IsEmpty()) { Out->SetStringField(TEXT("memberProperty"), MemberPropertyName); }
		Out->SetBoolField(TEXT("reconstructed"), bReconstructed);
		if (!RetargetedTo.IsEmpty()) { Out->SetStringField(TEXT("retargetedTo"), RetargetedTo); }
		Out->SetStringField(TEXT("verifiedOn"), LeafOwner->GetPathName());
		// Batch N: element addressing, stated so a caller can tell "I wrote the array" from "I wrote
		// row 1 of the array" without re-parsing its own propertyPath.
		Out->SetBoolField(TEXT("isElement"), Res.bLeafIsElement);
		if (Res.bLeafIsElement)
		{
			Out->SetStringField(TEXT("elementPath"), Res.ElementDescription);
			Out->SetNumberField(TEXT("elementIndex"), Res.ElementIndex);
			if (!Res.ElementOrdering.IsEmpty()) { Out->SetStringField(TEXT("elementOrdering"), Res.ElementOrdering); }
		}
		if (bRehashed) { Out->SetBoolField(TEXT("rehashed"), true); }
		if (BeforeCount != INDEX_NONE)
		{
			Out->SetNumberField(TEXT("elementsBefore"), BeforeCount);
			Out->SetNumberField(TEXT("elementsAfter"), AfterCount);
		}

		// --- EditCondition, always stated - including its ABSENCE ----------------------
		if (EC.bHasMeta) { Out->SetStringField(TEXT("editCondition"), EC.MetaText); }
		else             { Out->SetField(TEXT("editCondition"), MakeShared<FJsonValueNull>()); }
		Out->SetStringField(TEXT("editConditionKind"), EC.Kind);
		if (EC.bEvaluated) { Out->SetBoolField(TEXT("editConditionMet"), EC.bMet); }
		else               { Out->SetField(TEXT("editConditionMet"), MakeShared<FJsonValueNull>()); }
		if (EC.bHides) { Out->SetBoolField(TEXT("editConditionHides"), true); }
		if (bFlagWritten)
		{
			TSharedRef<FJsonObject> FlagJson = MakeShared<FJsonObject>();
			FlagJson->SetStringField(TEXT("name"), EC.FlagName);
			FlagJson->SetBoolField(TEXT("valueBefore"), bFlagBefore);
			FlagJson->SetBoolField(TEXT("valueAfter"), bFlagAfter);
			Out->SetObjectField(TEXT("overrideFlagWritten"), FlagJson);
			AddWarning(Out, FString::Printf(
				TEXT("'%s' is gated by meta EditCondition=\"%s\"; the companion flag '%s' was False and has been SET to %s in the ")
				TEXT("same transaction, because the engine reads the flag rather than the value. Pass overrideFlag:\"refuse\" if you ")
				TEXT("would rather be told than have the flag written for you."),
				*PropertyPath, *EC.MetaText, *EC.FlagName, EC.bRequiredFlagValue ? TEXT("True") : TEXT("False")));
		}
		else if (bGateClosed && OverrideFlagMode == TEXT("ignore"))
		{
			Out->SetBoolField(TEXT("overrideFlagUnmet"), true);
			AddWarning(Out, FString::Printf(
				TEXT("WRITTEN BUT IGNORED BY THE ENGINE: '%s' is gated by meta EditCondition=\"%s\" and the flag '%s' is still %s, ")
				TEXT("so the value is stored and never read. You asked for overrideFlag:\"ignore\"."),
				*PropertyPath, *EC.MetaText, *EC.FlagName, EC.bRequiredFlagValue ? TEXT("False") : TEXT("True")));
		}
		else if (bGateClosed && !EC.FlagProp)
		{
			Out->SetBoolField(TEXT("overrideFlagUnmet"), true);
		}
		else if (bGateClosed && !Res.LeafContainerAddr)
		{
			Out->SetBoolField(TEXT("overrideFlagUnmet"), true);
			AddWarning(Out, EC.Note);
		}
		if (EC.bHasMeta && !EC.bEvaluated && !EC.Note.IsEmpty())
		{
			// A warning on a SUCCESSFUL write, not a failure: the value really was written, and the
			// gate's state is genuinely unknown to this bridge. Degrading silently is the failure mode
			// being removed.
			AddWarning(Out, FString::Printf(TEXT("'%s': %s The value WAS written; verify by hand that the condition holds."),
				*PropertyPath, *EC.Note));
		}

		// --- CLAMPS, reported on every write -------------------------------------------
		if (bClampApplied)
		{
			TSharedRef<FJsonObject> ClampJson = MakeShared<FJsonObject>();
			ClampJson->SetStringField(TEXT("meta"), ClampAppliedMeta);
			ClampJson->SetStringField(TEXT("requested"), ClampAppliedFrom);
			ClampJson->SetStringField(TEXT("written"), ClampAppliedTo);
			Out->SetObjectField(TEXT("clampApplied"), ClampJson);
			AddWarning(Out, FString::Printf(
				TEXT("enforceClamps:true coerced '%s' from %s to %s to satisfy meta %s. The Details panel's TYPED numeric entry ")
				TEXT("does the same (PropertyHandleImpl.cpp:870-931); its copy/paste path does not."),
				*PropertyPath, *ClampAppliedFrom, *ClampAppliedTo, *ClampAppliedMeta));
		}
		else
		{
			FString ViolMeta, ViolLimit;
			if (DescribeClampViolation(Leaf, AfterText, ViolMeta, ViolLimit))
			{
				TSharedRef<FJsonObject> ClampJson = MakeShared<FJsonObject>();
				ClampJson->SetStringField(TEXT("meta"), ViolMeta);
				ClampJson->SetStringField(TEXT("limit"), ViolLimit);
				ClampJson->SetStringField(TEXT("written"), AfterText);
				Out->SetObjectField(TEXT("clampViolation"), ClampJson);
				AddWarning(Out, FString::Printf(
					TEXT("'%s' now holds %s, outside its authored meta %s=%s. ImportText does NOT enforce clamps - only the panel's ")
					TEXT("typed numeric setters do - so this write was accepted where the panel's spinbox would have coerced it. ")
					TEXT("Pass enforceClamps:true to get the panel's behaviour."),
					*PropertyPath, *AfterText, *ViolMeta, *ViolLimit));
			}
		}
		if (ClampInfo.bHasUIMin || ClampInfo.bHasUIMax)
		{
			// Reported, never acted on: UIMin/UIMax are slider bounds and are not enforced by anything,
			// including the panel.
			TSharedRef<FJsonObject> UIJson = MakeShared<FJsonObject>();
			if (ClampInfo.bHasUIMin) { UIJson->SetStringField(TEXT("UIMin"), ClampInfo.UIMinText); }
			if (ClampInfo.bHasUIMax) { UIJson->SetStringField(TEXT("UIMax"), ClampInfo.UIMaxText); }
			Out->SetObjectField(TEXT("uiRange"), UIJson);
		}

		if (bRequestedChange && !bChanged)
		{
			// Nothing was written, so nothing should look written. Only ever CLEARS a flag this call
			// set itself - a package that was already dirty when we arrived stays dirty.
			if (LeafPackage && !bPackageWasDirty)
			{
				LeafPackage->SetDirtyFlag(false);
				Out->SetBoolField(TEXT("packageDirtyRestored"), true);
			}
			Out->SetBoolField(TEXT("applied"), false);
			Out->SetBoolField(TEXT("verified"), false);
			Out->SetStringField(TEXT("valueStaged"), StagedText);
			Fail(Out, FString::Printf(
				TEXT("set_property did NOT write '%s' (%s): the import reported success and produced '%s', ")
				TEXT("but re-reading the property afterwards returned '%s' - byte-identical to before the write. ")
				TEXT("Nothing was changed. Likely causes: a native setter or PostEditChangeProperty rejected the value, ")
				TEXT("or the leaf is a computed/transient field.%s Compare valueBefore / valueStaged / valueAfter in this response."),
				*PropertyPath, *Leaf->GetCPPType(), *StagedText, *AfterText,
				bReconstructed
					? TEXT(" This write also triggered a construction-script rerun, which REPLACED the component: ")
					  TEXT("FComponentInstanceDataCache does not carry transient properties, properties without ")
					  TEXT("EditAnywhere/Interp, multicast delegates, or anything the construction script itself writes ")
					  TEXT("(ComponentInstanceDataCache.cpp:54-66,171) - so an instance edit of such a property cannot ")
					  TEXT("survive. Edit the component TEMPLATE (...._GEN_VARIABLE) instead.")
					: TEXT("")));
			return;
		}

		Out->SetBoolField(TEXT("applied"), true);
		Out->SetBoolField(TEXT("verified"), true);
		if (bCoerced)
		{
			// Something between the parse and the readback adjusted the value (a ClampMin, a
			// normalising PostEditChangeProperty, a native setter). The write DID land, but not as
			// literally as asked - say so instead of echoing the request back as if it were the result.
			Out->SetBoolField(TEXT("coerced"), true);
			Out->SetStringField(TEXT("valueStaged"), StagedText);
		}
		if (!bChanged)
		{
			// Legitimate idempotent write: the parsed value equals what was already there. Reported
			// rather than failed, but never as a bare applied:true - changed:false is the number that
			// tells a caller its edit was a no-op.
			Out->SetStringField(TEXT("note"),
				TEXT("property already held this value; nothing needed to change (changed:false)"));
		}
		// Typed readback: exactly what get_property's "typed" field will return for this leaf next,
		// and exactly what can be fed straight back into value to reproduce this state. The
		// single-element form when the caller addressed an element, because the whole-property form
		// loops ArrayDim from the address it is given.
		Out->SetField(TEXT("typed"), Res.bLeafIsElement
			? PropertyValueToTypedJsonElement(Leaf, LeafAddr, LeafOwner)
			: PropertyValueToTypedJson(Leaf, LeafAddr, LeafOwner));

		// --- Widget-BP persistence: mark + recompile so the generated class bakes the edit -----
		// After verification: a write that did not land must never be baked into a generated class.
		bool bRecompiled = false;
		if (OwningWidgetBP)
		{
			FBlueprintEditorUtils::MarkBlueprintAsModified(OwningWidgetBP);
			FKismetEditorUtilities::CompileBlueprint(OwningWidgetBP);   // outside the transaction above
			bRecompiled = true;
			Out->SetBoolField(TEXT("recompiled"), bRecompiled);
		}

		UE_LOG(LogMifBridge, Log, TEXT("set_property: %s.%s = %s (form=%s, changed=%s)"),
			*Target->GetName(), *PropertyPath, *ImportStr, *ValueForm, bChanged ? TEXT("true") : TEXT("false"));
	}
}
