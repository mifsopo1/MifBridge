// MifBridge - Details-panel parity: the metadata surface, the container element lifecycle, and the
// yellow arrow (reset to default / diff vs default). Batch N, from docs/audit/work/R1_DETAILS_PANEL_PARITY.md.
//
// ============================================================================================
// REGISTRY LINES (kept in sync by the same edit that created this file)
//
//   MifBridgeHandlers.h  MIF_DECL(describe_property);
//                        MIF_DECL(diff_properties_vs_default);
//                        MIF_DECL(edit_container);
//                        MIF_DECL(reset_property_to_default);
//   MifBridgeCommon.cpp  MIF_BIND for all four, plus IsReadOnlyEndpoint entries for
//                        describe_property and diff_properties_vs_default.
//   server.py            one @mcp.tool() per endpoint.
//
// BUCKETS.
//   describe_property, diff_properties_vs_default  READ-ONLY. Pure reflection: FField metadata,
//     CPF_* flags, FProperty::Identical against the archetype. No Modify(), no object creation, and
//     no GetInheritableComponentHandler(true). Left out of IsReadOnlyEndpoint they would push one
//     empty entry onto the undo stack per call, which is exactly the pollution that bucket exists
//     to prevent.
//   edit_container, reset_property_to_default      DEFAULT (transacted). Neither runs
//     FKismetEditorUtilities::CompileBlueprint, and that is the ONLY thing IsSelfManagedEndpoint is
//     for (00_ARCHITECTURE.md, transaction policy) - so RunEndpoint's blanket transaction is both
//     sufficient and desirable: every mutation here is Modify()-able, so Ctrl-Z is correct for free.
//     The widget-template form (blueprintId + widgetName) is REFUSED on both rather than promoting
//     them to self-managed for a case that has no interesting containers and no interesting
//     archetype diff; set_property still serves it. Stated here so the choice does not read as an
//     oversight, per R1 section 2.5 field 5 ("pick one and state it in the header comment").
//
// WHAT A CANCELLED TRANSACTION DOES NOT DO. Nothing. UTransBuffer::Cancel discards the undo entry
// without ever calling FTransaction::Apply (EditorTransaction.cpp:1387-1437), so a failed call
// leaves nothing behind only if the HANDLER is ordered that way. Both mutators here are ordered
// guards -> validate -> mutate, and every element value is parsed into a SCRATCH buffer before any
// live address is touched (PM-003, via MifBridge::ImportPropertyTextSafely). See PM-007.
//
// WHAT THIS FILE DELIBERATELY DOES NOT REIMPLEMENT. The target resolver
// (MifBridge::ResolvePropertyTarget), the path walker (MifBridge::ResolvePropertyPathEx), the value
// converter (MifBridge::PropertyImportTextFromJson), the PM-003 import
// (MifBridge::ImportPropertyTextSafely), the cooked test (MifBridge::IsCookedOrContainerPackage),
// the map-key matcher (MifBridge::FindMapEntryByKeyText) and the typed-JSON emitters all live in
// MifBridgeCommon.cpp / MifBridgeNodes5.cpp and are declared in MifBridgeHandlers.h. Every one of
// them was PROMOTED rather than copied. A unity build merges unnamed namespaces across files in one
// blob, and a differently-NAMED copy is worse than a colliding one because the compiler never tells
// you (PM-005).
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Components/ActorComponent.h"      // CreationMethod / IsEditableWhenInherited reporting
#include "Dom/JsonValue.h"
#include "GameFramework/Actor.h"
#include "Misc/PackageName.h"
#include "UObject/Class.h"
#include "UObject/Field.h"                  // GetMetaDataMap
#include "UObject/Package.h"
#include "UObject/PropertyPortFlags.h"      // PPF_DeepComparison / PPF_InstanceSubobjects
#include "UObject/UnrealType.h"
#include "UObject/UObjectGlobals.h"
#include "WidgetBlueprint.h"

namespace MifBridge
{
	namespace
	{
		// ---------------------------------------------------------------------------------------
		// Archetype / default plumbing (R1 gap G6).
		// ---------------------------------------------------------------------------------------

		// "Which object is the default": the archetype, with a UClass -> CDO hop first, exactly as
		// FPropertyNode does (PropertyNode.cpp:1651-1654 then :1669).
		UObject* MifDetailsArchetypeOf(UObject* Object)
		{
			if (!Object) { return nullptr; }
			UObject* Root = Object;
			if (UClass* AsClass = Cast<UClass>(Root))
			{
				Root = AsClass->GetDefaultObject(/*bCreateIfNeeded*/ true);
			}
			return Root ? Root->GetArchetype() : nullptr;
		}

		// The panel's comparison: FProperty::Identical, with PPF_DeepComparison when the property
		// contains an instanced object, and an ArrayDim loop for C-arrays
		// (PropertyNode.cpp:2275-2308). bSingleElement is true when the caller addressed ONE element,
		// in which case looping ArrayDim would read past the allocation.
		bool MifDetailsDiffersFromDefault(const FProperty* Prop, const void* ValueAddr, const void* DefaultAddr,
			bool bSingleElement, bool bDeep)
		{
			if (!Prop || !ValueAddr || !DefaultAddr) { return false; }
			uint32 PortFlags = 0;
			if (bDeep && Prop->ContainsInstancedObjectProperty()) { PortFlags |= PPF_DeepComparison; }
			const int32 Count = bSingleElement ? 1 : FMath::Max(Prop->ArrayDim, 1);
			for (int32 i = 0; i < Count; ++i)
			{
				const uint8* A = (const uint8*)ValueAddr   + (SIZE_T)i * MifPropertyElementSize(Prop);
				const uint8* B = (const uint8*)DefaultAddr + (SIZE_T)i * MifPropertyElementSize(Prop);
				if (!Prop->Identical(A, B, PortFlags)) { return true; }
			}
			return false;
		}

		// The SAME predicate as MifDetailsDiffersFromDefault, inverted, and made to say WHY. A write
		// endpoint that claims `verified` has to distinguish three outcomes that a bool collapses into
		// one: every element matches the default, some element does NOT match, or some element could
		// not be compared at all. Only the first is a pass; the other two are both `verified:false`
		// and a caller needs to be told which it got, because "the reset did not land" and "this call
		// cannot tell you whether the reset landed" call for different next steps.
		//
		// Deliberately NOT a second copy of the compare loop: this is the one place that grew a
		// reason string, and MifDetailsDiffersFromDefault stays the read-only predicate the describe
		// and diff paths already use (PM-005 - do not grow a parallel helper, extend the family).
		bool MifDetailsEqualsDefaultVerbose(const FProperty* Prop, const void* ValueAddr, const void* DefaultAddr,
			bool bSingleElement, bool bDeep, FString& OutReason)
		{
			// Every one of these is "cannot be compared", never "equal". MifDetailsDiffersFromDefault
			// answers false (i.e. "matches") for a null address, which is the right answer for a
			// read-only differ and exactly the wrong one for a verification.
			if (!Prop)        { OutReason = TEXT("the property could not be resolved for the read-back, so no element could be compared"); return false; }
			if (!ValueAddr)   { OutReason = TEXT("the live value has no address to read back, so no element could be compared"); return false; }
			if (!DefaultAddr) { OutReason = TEXT("no default value was materialised to compare against, so no element could be compared"); return false; }

			uint32 PortFlags = 0;
			if (bDeep && Prop->ContainsInstancedObjectProperty()) { PortFlags |= PPF_DeepComparison; }
			const int32 Count = bSingleElement ? 1 : FMath::Max(Prop->ArrayDim, 1);
			for (int32 i = 0; i < Count; ++i)
			{
				const uint8* A = (const uint8*)ValueAddr   + (SIZE_T)i * MifPropertyElementSize(Prop);
				const uint8* B = (const uint8*)DefaultAddr + (SIZE_T)i * MifPropertyElementSize(Prop);
				if (!Prop->Identical(A, B, PortFlags))
				{
					OutReason = (Count > 1)
						? FString::Printf(TEXT("element [%d] of %d still differs from the default"), i, Count)
						: FString(TEXT("the value still differs from the default"));
					return false;
				}
			}
			return true;
		}

		FString MifDetailsExportOne(const FProperty* Prop, const void* Addr, UObject* Owner)
		{
			FString S;
			// Data == Delta short-circuits the "skip if identical to the default" branch
			// (Property.cpp:1149), so this always emits.
			if (Prop && Addr) { Prop->ExportText_Direct(S, Addr, Addr, Owner, PPF_None); }
			return S;
		}

		// EVERY element of a property. For a fixed-size C-array UPROPERTY (FRichCurve FloatCurves[3])
		// that is NOT what MifDetailsExportOne returns: FProperty::ExportText_Direct forwards exactly
		// ONE value to ExportText_Internal (Property.cpp:1139-1164), and the ArrayDim loop lives in
		// UStruct::ExportProperties, which emits separate `Foo(0)=`/`Foo(1)=` lines and is not reachable
		// for a single property. A caller who addressed the WHOLE property must never be handed
		// element 0 and told that is the value - which is what every text compare in this file used to
		// do, verification included.
		//
		// REPORTING TEXT ONLY, and never round-tripped: ImportText_Direct is one element too
		// (UnrealType.h:499-507), so the whole-C-array write path in reset_property_to_default copies
		// the default VALUE instead of re-importing this string. The exact spelling of the join is
		// therefore free. Byte-identical to MifDetailsExportOne whenever ArrayDim <= 1, which is why
		// switching a call site over cannot change any existing single-element output.
		FString MifDetailsExportAll(const FProperty* Prop, const void* Addr, UObject* Owner)
		{
			if (!Prop || !Addr) { return FString(); }
			if (Prop->ArrayDim <= 1) { return MifDetailsExportOne(Prop, Addr, Owner); }
			FString S = TEXT("(");
			for (int32 i = 0; i < Prop->ArrayDim; ++i)
			{
				if (i > 0) { S += TEXT(","); }
				S += MifDetailsExportOne(Prop, (const uint8*)Addr + (SIZE_T)i * MifPropertyElementSize(Prop), Owner);
			}
			return S + TEXT(")");
		}

		// One correctly constructed/destructed instance of a property's value, ArrayDim-WIDE: GetSize()
		// is ArrayDim * ElementSize (UnrealType.h:1027-1030) and InitializeValue / DestroyValue both
		// span ArrayDim (UnrealType.h:929-941, TProperty override at :1369-1375), so one of these holds
		// a WHOLE C-array UPROPERTY rather than one slot.
		//
		// MifBridgeNodes5.cpp:73 has FScratchValue, the same idea - but it is defined in that .cpp and
		// is NOT declared in MifBridgeHandlers.h, so it is reachable from here only by accident of a
		// unity blob, and re-declaring THAT name is the PM-005 collision this file's header comment
		// warns about. Hence a distinct name and a deliberately file-local type. If a third caller ever
		// needs one, PROMOTE FScratchValue to the header and delete this - do not grow a third copy.
		//
		// It exists here because reset_property_to_default now has to keep its staging buffer alive
		// across the notification and the retarget in order to verify against it, and hand-freeing that
		// at five exits is how a leak gets shipped.
		struct FMifDetailsValueScratch
		{
			const FProperty* Prop = nullptr;
			void*            Mem  = nullptr;

			FMifDetailsValueScratch() = default;
			explicit FMifDetailsValueScratch(const FProperty* InProp) { Init(InProp); }

			// ONE-SHOT. A second call would strand the first allocation, so it refuses rather than leak.
			void Init(const FProperty* InProp)
			{
				if (Mem != nullptr || InProp == nullptr) { return; }
				Prop = InProp;
				Mem  = FMemory::Malloc(FMath::Max(Prop->GetSize(), 1), Prop->GetMinAlignment());
				if (Mem) { Prop->InitializeValue(Mem); }   // required before any Copy/Import on a struct
			}
			~FMifDetailsValueScratch()
			{
				if (Prop && Mem) { Prop->DestroyValue(Mem); }
				FMemory::Free(Mem);
			}
			FMifDetailsValueScratch(const FMifDetailsValueScratch&) = delete;
			FMifDetailsValueScratch& operator=(const FMifDetailsValueScratch&) = delete;
		};

		// The value a freshly constructed instance of this property would hold. Used when the
		// archetype does not carry the property at all - a variable a child Blueprint added, for
		// instance - mirroring FPropertyNode::GetDefaultValueAsString's fallback
		// (PropertyNode.cpp:2432-2443). Reported as defaultSource:"constructed", never as if it had
		// come from an archetype.
		//
		// bWholeProperty must match how the CALLER addressed the property, because the text it is
		// compared against is produced the same way: a caller holding one element of a C-array wants
		// element 0's constructed default, and a caller holding the whole property wants all ArrayDim.
		// Comparing an ExportAll against an ExportOne would report "differs" for every C-array there is.
		FString MifDetailsConstructedDefaultText(const FProperty* Prop, UObject* Owner, bool bWholeProperty = true)
		{
			if (!Prop) { return FString(); }
			void* Mem = FMemory::Malloc(FMath::Max(Prop->GetSize(), 1), Prop->GetMinAlignment());
			Prop->InitializeValue(Mem);
			const FString Text = bWholeProperty
				? MifDetailsExportAll(Prop, Mem, Owner)
				: MifDetailsExportOne(Prop, Mem, Owner);
			Prop->DestroyValue(Mem);
			FMemory::Free(Mem);
			return Text;
		}

		// ---------------------------------------------------------------------------------------
		// The metadata surface (R1 gap G3).
		// ---------------------------------------------------------------------------------------

		struct FMifFlagRow { uint64 Flag; const TCHAR* Name; };

		const FMifFlagRow* MifDetailsFlagTable(int32& OutCount)
		{
			// The CPF_* a caller can act on. Deliberately not every flag in ObjectMacros.h: a wall of
			// 60 names is not a discovery layer.
			static const FMifFlagRow Table[] = {
				{ CPF_Edit,                     TEXT("CPF_Edit") },
				{ CPF_EditConst,                TEXT("CPF_EditConst") },
				{ CPF_EditFixedSize,            TEXT("CPF_EditFixedSize") },
				{ CPF_DisableEditOnTemplate,    TEXT("CPF_DisableEditOnTemplate") },
				{ CPF_DisableEditOnInstance,    TEXT("CPF_DisableEditOnInstance") },
				{ CPF_BlueprintVisible,         TEXT("CPF_BlueprintVisible") },
				{ CPF_BlueprintReadOnly,        TEXT("CPF_BlueprintReadOnly") },
				{ CPF_Transient,                TEXT("CPF_Transient") },
				{ CPF_DuplicateTransient,       TEXT("CPF_DuplicateTransient") },
				{ CPF_NonPIEDuplicateTransient, TEXT("CPF_NonPIEDuplicateTransient") },
				{ CPF_NonTransactional,         TEXT("CPF_NonTransactional") },
				{ CPF_SkipSerialization,        TEXT("CPF_SkipSerialization") },
				{ CPF_Config,                   TEXT("CPF_Config") },
				{ CPF_Deprecated,               TEXT("CPF_Deprecated") },
				{ CPF_Interp,                   TEXT("CPF_Interp") },
				{ CPF_SaveGame,                 TEXT("CPF_SaveGame") },
				{ CPF_Net,                      TEXT("CPF_Net") },
				{ CPF_InstancedReference,       TEXT("CPF_InstancedReference") },
				{ CPF_ExportObject,             TEXT("CPF_ExportObject") },
				{ CPF_HasGetValueTypeHash,      TEXT("CPF_HasGetValueTypeHash") },
			};
			OutCount = UE_ARRAY_COUNT(Table);
			return Table;
		}

		// UHT's specifier -> flag mapping, run backwards so the report can name the AUTHORED
		// specifier rather than raw flags (UhtPropertyMemberSpecifiers.cs:21-88). Note what falls out
		// of it: VisibleAnywhere is exactly CPF_Edit | CPF_EditConst - a property a human CANNOT edit
		// in the panel and that this bridge will happily write.
		FString MifDetailsAuthoredSpecifier(const FProperty* Prop)
		{
			const bool bEdit     = Prop->HasAnyPropertyFlags(CPF_Edit);
			if (!bEdit) { return TEXT("none"); }
			const bool bConst    = Prop->HasAnyPropertyFlags(CPF_EditConst);
			const bool bNoTmpl   = Prop->HasAnyPropertyFlags(CPF_DisableEditOnTemplate);
			const bool bNoInst   = Prop->HasAnyPropertyFlags(CPF_DisableEditOnInstance);
			if (bConst)
			{
				if (bNoTmpl) { return TEXT("VisibleInstanceOnly"); }
				if (bNoInst) { return TEXT("VisibleDefaultsOnly"); }
				return TEXT("VisibleAnywhere");
			}
			if (bNoTmpl) { return TEXT("EditInstanceOnly"); }
			if (bNoInst) { return TEXT("EditDefaultsOnly"); }
			return TEXT("EditAnywhere");
		}

		// FProperty::ShouldSerializeValue needs an FArchive, so read the flags directly instead -
		// same rule set (Property.cpp:1167-1225). Three different lies a caller must be able to tell
		// apart: gone on reload, gone on copy/paste, and not undoable.
		FString MifDetailsPersistence(const FProperty* Prop)
		{
			if (Prop->HasAnyPropertyFlags(CPF_Transient))          { return TEXT("transient"); }
			if (Prop->HasAnyPropertyFlags(CPF_SkipSerialization))  { return TEXT("notSerialized"); }
			if (Prop->HasAnyPropertyFlags(CPF_DuplicateTransient)) { return TEXT("duplicateTransient"); }
			return TEXT("saved");
		}

		void MifDetailsEmitContainerShape(const FProperty* Prop, const void* ValueAddr, const TSharedRef<FJsonObject>& Row)
		{
			if (const FArrayProperty* AP = CastField<FArrayProperty>(Prop))
			{
				Row->SetStringField(TEXT("container"), TEXT("array"));
				Row->SetStringField(TEXT("innerType"), AP->Inner->GetCPPType());
				if (ValueAddr) { Row->SetNumberField(TEXT("elementCount"), FScriptArrayHelper(AP, ValueAddr).Num()); }
			}
			else if (const FSetProperty* SP = CastField<FSetProperty>(Prop))
			{
				Row->SetStringField(TEXT("container"), TEXT("set"));
				Row->SetStringField(TEXT("innerType"), SP->ElementProp->GetCPPType());
				Row->SetBoolField(TEXT("elementHashable"), SP->ElementProp->HasAnyPropertyFlags(CPF_HasGetValueTypeHash));
				if (ValueAddr) { Row->SetNumberField(TEXT("elementCount"), FScriptSetHelper(SP, ValueAddr).Num()); }
			}
			else if (const FMapProperty* MP = CastField<FMapProperty>(Prop))
			{
				Row->SetStringField(TEXT("container"), TEXT("map"));
				Row->SetStringField(TEXT("keyType"), MP->KeyProp->GetCPPType());
				Row->SetStringField(TEXT("valueType"), MP->ValueProp->GetCPPType());
				Row->SetBoolField(TEXT("keyHashable"), MP->KeyProp->HasAnyPropertyFlags(CPF_HasGetValueTypeHash));
				if (ValueAddr) { Row->SetNumberField(TEXT("elementCount"), FScriptMapHelper(MP, ValueAddr).Num()); }
			}
			else if (Prop->ArrayDim > 1)
			{
				// A fixed-size C-array UPROPERTY is NOT a TArray, which is why UCurveVector::FloatCurves
				// (FRichCurve[3]) needed its own accessor branch in the walker.
				Row->SetStringField(TEXT("container"), TEXT("cArray"));
				Row->SetNumberField(TEXT("elementCount"), Prop->ArrayDim);
			}
			else
			{
				Row->SetStringField(TEXT("container"), TEXT("none"));
			}
		}

		// One property, fully described. ValueAddr / ContainerAddr / Owner may be null (the
		// class-only form), in which case value-dependent fields are simply absent rather than
		// invented.
		TSharedRef<FJsonObject> MifDetailsDescribeProperty(const FProperty* Prop, const void* ValueAddr,
			const void* ContainerAddr, UObject* Owner, UObject* Archetype, const void* DefaultAddr,
			bool bSingleElement, bool bIncludeMetadata, bool bIncludeDefault, int32 MaxValueChars)
		{
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("name"), Prop->GetName());
			Row->SetStringField(TEXT("authoredName"), Prop->GetAuthoredName());
			Row->SetStringField(TEXT("type"), Prop->GetCPPType());
			Row->SetStringField(TEXT("propertyClass"), Prop->GetClass()->GetName());
			Row->SetNumberField(TEXT("arrayDim"), Prop->ArrayDim);
			Row->SetNumberField(TEXT("elementSize"), MifPropertyElementSize(Prop));
			if (UStruct* OwnerStruct = Prop->GetOwnerStruct())
			{
				Row->SetStringField(TEXT("owner"), OwnerStruct->GetName());
			}
			MifDetailsEmitContainerShape(Prop, ValueAddr, Row);

			// --- flags + authored specifier ---
			int32 FlagCount = 0;
			const FMifFlagRow* Table = MifDetailsFlagTable(FlagCount);
			TArray<TSharedPtr<FJsonValue>> Flags;
			for (int32 i = 0; i < FlagCount; ++i)
			{
				if (Prop->HasAnyPropertyFlags((EPropertyFlags)Table[i].Flag))
				{
					Flags.Add(MakeShared<FJsonValueString>(Table[i].Name));
				}
			}
			Row->SetArrayField(TEXT("flags"), Flags);
			Row->SetNumberField(TEXT("flagCount"), Flags.Num());
			Row->SetStringField(TEXT("specifier"), MifDetailsAuthoredSpecifier(Prop));
			Row->SetStringField(TEXT("persistence"), MifDetailsPersistence(Prop));
			Row->SetBoolField(TEXT("editFixedSize"), Prop->HasAnyPropertyFlags(CPF_EditFixedSize));
			Row->SetBoolField(TEXT("editConst"), Prop->HasAnyPropertyFlags(CPF_EditConst));

			// --- EditCondition, the whole point of the discovery layer ---
			FEditConditionInfo EC;
			InspectEditCondition(Prop, ContainerAddr, EC);
			if (EC.bHasMeta) { Row->SetStringField(TEXT("editCondition"), EC.MetaText); }
			else             { Row->SetField(TEXT("editCondition"), MakeShared<FJsonValueNull>()); }
			Row->SetStringField(TEXT("editConditionKind"), EC.Kind);
			if (EC.bEvaluated) { Row->SetBoolField(TEXT("editConditionMet"), EC.bMet); }
			else               { Row->SetField(TEXT("editConditionMet"), MakeShared<FJsonValueNull>()); }
			if (!EC.FlagName.IsEmpty())  { Row->SetStringField(TEXT("editConditionFlag"), EC.FlagName); }
			if (EC.bHides)               { Row->SetBoolField(TEXT("editConditionHides"), true); }
			if (EC.bInlineToggle)        { Row->SetBoolField(TEXT("inlineEditConditionToggle"), true); }
			if (!EC.Note.IsEmpty())      { Row->SetStringField(TEXT("editConditionNote"), EC.Note); }

			// --- clamps: reported, and (for UIMin/UIMax) never acted on by anything ---
			FPropertyClampInfo Clamps;
			InspectClamps(Prop, Clamps);
			if (!Clamps.ClampMinText.IsEmpty())   { Row->SetStringField(TEXT("clampMin"), Clamps.ClampMinText); }
			if (!Clamps.ClampMaxText.IsEmpty())   { Row->SetStringField(TEXT("clampMax"), Clamps.ClampMaxText); }
			if (!Clamps.UIMinText.IsEmpty())      { Row->SetStringField(TEXT("uiMin"), Clamps.UIMinText); }
			if (!Clamps.UIMaxText.IsEmpty())      { Row->SetStringField(TEXT("uiMax"), Clamps.UIMaxText); }
			if (!Clamps.MultipleText.IsEmpty())   { Row->SetStringField(TEXT("multiple"), Clamps.MultipleText); }
			if (!Clamps.ArrayClampText.IsEmpty()) { Row->SetStringField(TEXT("arrayClamp"), Clamps.ArrayClampText); }

			// --- metadata ---
			bool bMetadataAvailable = false;
			int32 MetadataKeyCount = 0;
#if WITH_EDITORONLY_DATA
			if (const TMap<FName, FString>* MetaMap = Prop->GetMetaDataMap())
			{
				bMetadataAvailable = true;
				MetadataKeyCount = MetaMap->Num();
				if (bIncludeMetadata)
				{
					TSharedRef<FJsonObject> Meta = MakeShared<FJsonObject>();
					for (const TPair<FName, FString>& KV : *MetaMap)
					{
						Meta->SetStringField(KV.Key.ToString(), KV.Value);
					}
					Row->SetObjectField(TEXT("metadata"), Meta);
				}
				if (const FString* Cat = MetaMap->Find(FName(TEXT("Category"))))    { Row->SetStringField(TEXT("category"), *Cat); }
				if (const FString* Disp = MetaMap->Find(FName(TEXT("DisplayName")))) { Row->SetStringField(TEXT("displayName"), *Disp); }
				if (const FString* Tip = MetaMap->Find(FName(TEXT("ToolTip"))))      { Row->SetStringField(TEXT("tooltip"), *Tip); }
				if (const FString* Allowed = MetaMap->Find(FName(TEXT("AllowedClasses"))))       { Row->SetStringField(TEXT("allowedClasses"), *Allowed); }
				if (const FString* Disallowed = MetaMap->Find(FName(TEXT("DisallowedClasses")))) { Row->SetStringField(TEXT("disallowedClasses"), *Disallowed); }
				if (const FString* Options = MetaMap->Find(FName(TEXT("GetOptions"))))           { Row->SetStringField(TEXT("getOptions"), *Options); }
				if (const FString* Units = MetaMap->Find(FName(TEXT("Units"))))                  { Row->SetStringField(TEXT("units"), *Units); }
				if (const FString* Force = MetaMap->Find(FName(TEXT("ForceUnits"))))             { Row->SetStringField(TEXT("forceUnits"), *Force); }
				if (const FString* Bitmask = MetaMap->Find(FName(TEXT("BitmaskEnum"))))          { Row->SetStringField(TEXT("bitmaskEnum"), *Bitmask); }
			}
#endif
			// On a cooked package GetMetaDataMap() is null. Every meta field is then ABSENT and
			// metadataAvailable is false - not emitted as empty strings, which would read as "no
			// clamp, no gate" when the truth is "unknown". CPF_* flags are cooked and stay accurate.
			Row->SetBoolField(TEXT("metadataAvailable"), bMetadataAvailable);
			Row->SetNumberField(TEXT("metadataKeyCount"), MetadataKeyCount);
			Row->SetBoolField(TEXT("instanced"),
				Prop->HasAnyPropertyFlags(CPF_InstancedReference) || Prop->ContainsInstancedObjectProperty());

			// --- the panel's own "can a human edit this row" predicate, recomputed ---
			// PropertyEditorHelpers.cpp:374-390 (shown at all) + PropertyNode.cpp:1137-1246 (greyed).
			bool bEditableByHuman = Prop->HasAnyPropertyFlags(CPF_Edit)
				&& !Prop->HasAnyPropertyFlags(CPF_EditConst)
				&& !EC.bInlineToggle;
			FString NotEditableReason;
			if (!Prop->HasAnyPropertyFlags(CPF_Edit))          { NotEditableReason = TEXT("no CPF_Edit: the property has no EditAnywhere/EditDefaultsOnly/VisibleAnywhere specifier, so the panel never shows a row for it"); }
			else if (Prop->HasAnyPropertyFlags(CPF_EditConst)) { NotEditableReason = TEXT("CPF_EditConst (VisibleAnywhere / VisibleDefaultsOnly / VisibleInstanceOnly): the panel shows it greyed out. This bridge can still write it."); }
			else if (EC.bInlineToggle)                         { NotEditableReason = TEXT("meta InlineEditConditionToggle: the panel draws this as the little checkbox on the GATED property's own row rather than as a row of its own"); }
			if (bEditableByHuman && Owner)
			{
				// The virtual every UObject may override; UObject's own is
				// `return !InProperty->HasAnyPropertyFlags(CPF_EditConst);` (Obj.cpp:507-511).
				if (!Owner->CanEditChange(Prop))
				{
					bEditableByHuman = false;
					NotEditableReason = FString::Printf(TEXT("%s::CanEditChange() returned false for this property"), *Owner->GetClass()->GetName());
				}
			}
			if (bEditableByHuman && EC.bEvaluated && !EC.bMet)
			{
				bEditableByHuman = false;
				NotEditableReason = FString::Printf(
					TEXT("EditCondition \"%s\" is not met (flag '%s'); the panel greys the row and the engine ignores the value"),
					*EC.MetaText, *EC.FlagName);
			}
			Row->SetBoolField(TEXT("editableByHuman"), bEditableByHuman);
			if (!NotEditableReason.IsEmpty()) { Row->SetStringField(TEXT("notEditableReason"), NotEditableReason); }

			// --- current value + default ---
			if (ValueAddr)
			{
				// ExportAll when the caller addressed the WHOLE property, ExportOne when it addressed one
				// element: bSingleElement is exactly that distinction, and getting it backwards either
				// reports element 0 as if it were a 3-element C-array's value or reads past the end of
				// the one element the caller resolved. ExportAll is byte-identical to ExportOne for
				// ArrayDim <= 1, so only C-array properties see any change here.
				FString ValueText = bSingleElement
					? MifDetailsExportOne(Prop, ValueAddr, Owner)
					: MifDetailsExportAll(Prop, ValueAddr, Owner);
				if (MaxValueChars > 0 && ValueText.Len() > MaxValueChars)
				{
					ValueText = ValueText.Left(MaxValueChars);
					Row->SetBoolField(TEXT("valueClipped"), true);
				}
				Row->SetStringField(TEXT("value"), ValueText);
				Row->SetField(TEXT("typed"), bSingleElement
					? PropertyValueToTypedJsonElement(Prop, ValueAddr, Owner)
					: PropertyValueToTypedJson(Prop, ValueAddr, Owner));

				if (bIncludeDefault)
				{
					FString DefaultText;
					FString DefaultSource;
					bool bDiffers = false;
					if (DefaultAddr)
					{
						DefaultText   = bSingleElement
							? MifDetailsExportOne(Prop, DefaultAddr, Archetype)
							: MifDetailsExportAll(Prop, DefaultAddr, Archetype);
						DefaultSource = TEXT("archetype");
						bDiffers      = MifDetailsDiffersFromDefault(Prop, ValueAddr, DefaultAddr, bSingleElement, /*bDeep*/ true);
					}
					else
					{
						// Both sides of this text compare must be produced the same way - see the
						// bWholeProperty note on MifDetailsConstructedDefaultText.
						DefaultText   = MifDetailsConstructedDefaultText(Prop, Owner, /*bWholeProperty*/ !bSingleElement);
						DefaultSource = TEXT("constructed");
						bDiffers      = !DefaultText.Equals(bSingleElement
							? MifDetailsExportOne(Prop, ValueAddr, Owner)
							: MifDetailsExportAll(Prop, ValueAddr, Owner), ESearchCase::CaseSensitive);
					}
					if (MaxValueChars > 0 && DefaultText.Len() > MaxValueChars) { DefaultText = DefaultText.Left(MaxValueChars); }
					Row->SetStringField(TEXT("defaultValue"), DefaultText);
					Row->SetStringField(TEXT("defaultSource"), DefaultSource);
					Row->SetBoolField(TEXT("differsFromDefault"), bDiffers);
					if (Archetype) { Row->SetStringField(TEXT("archetype"), Archetype->GetPathName()); }
				}
			}
			return Row;
		}

		// The G5 facts a caller needs about the OBJECT it is about to write, reported by every verb
		// in this file so "which of the two did I edit" is never a guess.
		void MifDetailsEmitTargetKind(UObject* Target, const TSharedRef<FJsonObject>& Out)
		{
			if (!Target) { return; }
			Out->SetStringField(TEXT("target"), Target->GetPathName());
			Out->SetStringField(TEXT("targetClass"), Target->GetClass()->GetName());
			Out->SetBoolField(TEXT("isTemplate"), Target->IsTemplate());
			if (UObject* Arch = Target->GetArchetype()) { Out->SetStringField(TEXT("archetype"), Arch->GetPathName()); }
			if (UActorComponent* Comp = Cast<UActorComponent>(Target))
			{
				Out->SetStringField(TEXT("creationMethod"), ComponentCreationMethodString(Comp));
				Out->SetBoolField(TEXT("editableWhenInherited"), Comp->IsEditableWhenInherited());
				if (AActor* OwnerActor = Comp->GetOwner())
				{
					Out->SetStringField(TEXT("owningActor"), OwnerActor->GetPathName());
				}
			}
			Out->SetBoolField(TEXT("cooked"), IsCookedOrContainerPackage(Target->GetOutermost()));
		}

		// Shared entry for the two MUTATING verbs: resolve the target, refuse the widget form, refuse
		// a cooked package. Writes the reason into Out and returns null.
		UObject* MifDetailsResolveWritableTarget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
			const TCHAR* EndpointName)
		{
			UWidgetBlueprint* WidgetBP = nullptr;
			UObject* Target = ResolvePropertyTarget(In, Out, &WidgetBP);
			if (!Target) { return nullptr; }
			if (WidgetBP)
			{
				Fail(Out, FString::Printf(
					TEXT("%s does not accept the widget-template form (blueprintId + widgetName): that branch has to recompile the ")
					TEXT("Widget Blueprint, which would make this endpoint compile-heavy and unbatchable for a case with no ")
					TEXT("containers and no interesting archetype diff. Use set_property for widget templates, or pass the widget's ")
					TEXT("objectPath directly. Nothing was changed."),
					EndpointName));
				return nullptr;
			}
			if (IsCookedOrContainerPackage(Target->GetOutermost()))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' lives in a COOKED / container-only package, which this bridge treats as read-only: a write there ")
					TEXT("cannot be saved. Mint an editable copy first (create_editable_child) and address that. Nothing was changed."),
					*Target->GetPathName()));
				return nullptr;
			}
			return Target;
		}

		// ---------------------------------------------------------------------------------------
		// diff_properties_vs_default plumbing - the OPTIONAL `recursive` walk.
		// ---------------------------------------------------------------------------------------

		// Everything the walk carries, so the recursive signature stays readable and every counter has
		// exactly one home.
		//
		// THE COUNTING RULE, which the emitted `countsConsistent` actually checks: every INSPECTED node
		// ends up in exactly one of differing / matching / skippedTransient / expanded. `expanded` is a
		// struct that was OPENED instead of reported, so it is not also counted as differing - and it is
		// always 0 when recursive is false, which is why the shipped invariant
		// inspected == differing + matching + skippedTransient still holds unchanged for every caller
		// that does not ask for recursion.
		struct FMifDetailsDiffWalk
		{
			UObject* ValueOwner   = nullptr;    // the object, for ExportText_Direct's Owner argument
			UObject* DefaultOwner = nullptr;    // the archetype, same
			int32 Limit           = 200;
			int32 MaxValueChars   = 200;
			bool  bIncludeTransient = false;
			bool  bDeep             = true;
			int32 MaxDepth          = 4;

			int32 Inspected = 0, Differing = 0, Matching = 0, SkippedTransient = 0, Expanded = 0;
			bool  bTruncated = false;
			bool  bBudgetExhausted = false;
			TArray<TSharedPtr<FJsonValue>> Rows;

			// A ceiling on NODES VISITED, not on rows kept. A UScriptStruct cannot contain itself by
			// value, so the graph is finite and this is not what makes the recursion terminate (MaxDepth
			// and the finite struct graph are). It is what keeps a SYNCHRONOUS handler - these run inside
			// the HTTP server's ticker - from walking every member of every FPostProcessSettings on a
			// heavily-overridden actor before it answers.
			// constexpr, not `static const`: it is passed to FString::Printf below, and a variadic call
			// ODR-uses it - a plain in-class `static const int32` would then need an out-of-line
			// definition and fail to link.
			static constexpr int32 kNodeBudget = 20000;
		};

		// One row of the diff. Path is the DOTTED path (equal to the property name at the top level),
		// which is what reset_property_to_default and set_property accept - a diff whose rows cannot be
		// fed back to the verb that acts on them is a report, not a tool.
		TSharedRef<FJsonObject> MifDetailsMakeDiffRow(const FProperty* Prop, const FString& Path,
			const void* ValueAddr, const void* DefaultAddr, const FString& ConstructedDefaultText,
			UObject* ValueOwner, UObject* DefaultOwner, int32 MaxValueChars)
		{
			// ExportAll, not ExportOne: `bDiffers` for these rows is FProperty::Identical over ArrayDim
			// (bSingleElement is false at every call site here), so reporting element 0 as the value
			// would state a difference the printed value cannot account for. Identical for ArrayDim <= 1.
			FString ValueText = MifDetailsExportAll(Prop, ValueAddr, ValueOwner);
			if (ValueText.Len() > MaxValueChars) { ValueText = ValueText.Left(MaxValueChars); }
			FString DefaultText = DefaultAddr
				? MifDetailsExportAll(Prop, DefaultAddr, DefaultOwner)
				: ConstructedDefaultText;
			if (DefaultText.Len() > MaxValueChars) { DefaultText = DefaultText.Left(MaxValueChars); }

			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("name"), Prop->GetName());
			Row->SetStringField(TEXT("path"), Path);
			Row->SetStringField(TEXT("type"), Prop->GetCPPType());
			Row->SetStringField(TEXT("value"), ValueText);
			Row->SetStringField(TEXT("defaultValue"), DefaultText);
			Row->SetStringField(TEXT("defaultSource"), DefaultAddr ? TEXT("archetype") : TEXT("constructed"));
			Row->SetStringField(TEXT("specifier"), MifDetailsAuthoredSpecifier(Prop));
			Row->SetStringField(TEXT("persistence"), MifDetailsPersistence(Prop));
			Row->SetBoolField(TEXT("resettable"),
				!Prop->HasAnyPropertyFlags(CPF_Config) && !Prop->HasAnyPropertyFlags(CPF_EditFixedSize));
			return Row;
		}

		// Recurse into the members of ONE struct, comparing the object's copy against the archetype's.
		// Deliberately narrow, and each exclusion is a correctness requirement rather than caution:
		//   - only into an FStructProperty, where ValueBase and DefaultBase are the SAME UStruct, so one
		//     member offset addresses both. That is the entire safety argument, which is why nothing
		//     else is descended into.
		//   - NEVER into a TArray/TSet/TMap element: the object and its archetype hold different element
		//     counts and different allocations, so there is no parallel address to compare against.
		//     Containers stay leaves and are compared WHOLE by FProperty::Identical, which is correct.
		//   - NEVER into a C-array member (ArrayDim > 1): every member address would need a per-element
		//     path, and quietly using element 0 is exactly the blindness that made
		//     reset_property_to_default report verified:true over an untouched override. Reported as a
		//     leaf instead, where the ArrayDim-wide Identical already gives the right answer.
		//   - NEVER through an object POINTER: that is a different object with a different archetype and
		//     its own diff, not a child row of this one.
		void MifDetailsWalkDiff(FMifDetailsDiffWalk& W, UStruct* Struct, const void* ValueBase,
			const void* DefaultBase, const FString& PathPrefix, int32 Depth)
		{
			if (!Struct || !ValueBase || !DefaultBase) { return; }
			for (TFieldIterator<FProperty> It(Struct); It; ++It)
			{
				FProperty* Prop = *It;
				if (!Prop) { continue; }
				if (W.Inspected >= FMifDetailsDiffWalk::kNodeBudget) { W.bBudgetExhausted = true; return; }
				++W.Inspected;
				if (!W.bIncludeTransient && Prop->HasAnyPropertyFlags(CPF_Transient))
				{
					++W.SkippedTransient;
					continue;
				}

				const void* ValueAddr   = Prop->ContainerPtrToValuePtr<void>(ValueBase);
				const void* DefaultAddr = Prop->ContainerPtrToValuePtr<void>(DefaultBase);
				if (!MifDetailsDiffersFromDefault(Prop, ValueAddr, DefaultAddr, /*bSingleElement*/ false, W.bDeep))
				{
					++W.Matching;
					continue;
				}

				const FString Path = PathPrefix.IsEmpty()
					? Prop->GetName()
					: (PathPrefix + TEXT(".") + Prop->GetName());
				const FStructProperty* SP = CastField<FStructProperty>(Prop);
				if (SP && SP->Struct && Prop->ArrayDim == 1 && Depth < W.MaxDepth)
				{
					const int32 DifferingBefore = W.Differing;
					++W.Expanded;
					MifDetailsWalkDiff(W, SP->Struct, ValueAddr, DefaultAddr, Path, Depth + 1);
					if (W.Differing != DifferingBefore) { continue; }
					// The struct compared NON-identical but no member of it did. That is a real answer,
					// not a contradiction: FProperty::Identical on a UScriptStruct can route through a
					// custom Identical op (TStructOpsTypeTraits) that is not a member-wise compare. Fall
					// back to reporting the struct itself rather than count a difference with no row to
					// explain it.
					--W.Expanded;
				}
				++W.Differing;
				if (W.Rows.Num() >= W.Limit) { W.bTruncated = true; continue; }
				W.Rows.Add(MakeShared<FJsonValueObject>(MifDetailsMakeDiffRow(
					Prop, Path, ValueAddr, DefaultAddr, FString(), W.ValueOwner, W.DefaultOwner, W.MaxValueChars)));
			}
		}

		// ---------------------------------------------------------------------------------------
		// edit_container plumbing.
		// ---------------------------------------------------------------------------------------

		// Every size-changing operation. CPF_EditFixedSize hides the panel's add/remove buttons
		// (PropertyEditorHelpers.cpp:679) and is keyed off the FLAG, never the metadata string - the
		// flag survives a cook, the `EditFixedSize` meta does not.
		bool MifDetailsIsSizeChanging(const FString& Operation)
		{
			return Operation == TEXT("add") || Operation == TEXT("insert") || Operation == TEXT("remove")
				|| Operation == TEXT("clear") || Operation == TEXT("resize");
		}
	}

	// =============================================================================================
	// describe_property - READ-ONLY discovery
	//   in:  { objectPath (actorPath) | blueprintId (path) + widgetName | class (className),
	//          propertyPath (property) | nameContains (filter, nameFilter),
	//          limit?, maxValueChars?, includeMetadata?, includeDefault? }
	//   out: one property in full detail, or a filtered survey.
	//
	// Nothing in the bridge reported property FLAGS or METADATA before this. list_object_properties
	// emits {name,type,value}; describe_class enumerates functions and dispatchers and no properties
	// at all. Without this an agent cannot tell EditAnywhere from VisibleAnywhere, cannot see a
	// gate, cannot see a clamp, cannot see Transient, and cannot see which Category a property is
	// under - which makes every other Details-panel capability un-actionable, because you have to
	// know a gate applies before you can care that it does.
	// =============================================================================================
	void H_describe_property(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			  TEXT("class"), TEXT("className"),
			  TEXT("propertyPath"), TEXT("property"),
			  TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"),
			  TEXT("limit"), TEXT("maxValueChars"), TEXT("includeMetadata"), TEXT("includeDefault") },
			TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName | class (alias className); then propertyPath (alias property) OR nameContains (aliases filter, nameFilter); limit, maxValueChars, includeMetadata, includeDefault")))
		{
			return;
		}

		const FString PropertyPath   = JStrAny(In, { TEXT("propertyPath"), TEXT("property") });
		const FString NameFilter     = JStrAny(In, { TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter") });
		const FString ClassName      = JStrAny(In, { TEXT("class"), TEXT("className") });
		const bool  bIncludeMetadata = JBool(In, TEXT("includeMetadata"), true);
		const bool  bIncludeDefault  = JBool(In, TEXT("includeDefault"), true);
		const int32 Limit            = FMath::Clamp(JInt(In, TEXT("limit"), 200), 1, 5000);
		const int32 MaxValueChars    = FMath::Clamp(JInt(In, TEXT("maxValueChars"), 200), 16, 100000);

		// --- class-only form: describe a TYPE with no instance -------------------------
		if (!ClassName.IsEmpty())
		{
			UClass* Class = ResolveClass(ClassName, /*ContextBP*/ nullptr);
			if (!Class)
			{
				Fail(Out, FString::Printf(TEXT("class '%s' not found (accepts a short name or a full path such as /Script/Engine.StaticMeshComponent)"), *ClassName));
				return;
			}
			// The CDO is the instance every "what does this class default to" question is really
			// about, and it is what makes the value/EditCondition columns answerable at all.
			UObject* CDO = Class->GetDefaultObject(/*bCreateIfNeeded*/ true);
			TArray<TSharedPtr<FJsonValue>> Rows;
			int32 Matched = 0;
			bool bTruncated = false;
			for (TFieldIterator<FProperty> It(Class); It; ++It)
			{
				FProperty* Prop = *It;
				if (!Prop) { continue; }
				if (!PropertyPath.IsEmpty() && !Prop->GetName().Equals(PropertyPath, ESearchCase::IgnoreCase)) { continue; }
				if (!NameFilter.IsEmpty() && !Prop->GetName().Contains(NameFilter)) { continue; }
				++Matched;
				if (Rows.Num() >= Limit) { bTruncated = true; continue; }
				const void* ValueAddr = CDO ? Prop->ContainerPtrToValuePtr<void>(CDO) : nullptr;
				Rows.Add(MakeShared<FJsonValueObject>(MifDetailsDescribeProperty(
					Prop, ValueAddr, CDO, CDO, nullptr, nullptr, /*bSingleElement*/ false,
					bIncludeMetadata, /*bIncludeDefault*/ false, MaxValueChars)));
			}
			Out->SetStringField(TEXT("class"), Class->GetPathName());
			Out->SetStringField(TEXT("form"), TEXT("class"));
			Out->SetNumberField(TEXT("count"), Rows.Num());
			Out->SetNumberField(TEXT("matched"), Matched);
			Out->SetBoolField(TEXT("truncated"), bTruncated);
			Out->SetArrayField(TEXT("properties"), Rows);
			if (Rows.Num() == 0)
			{
				Out->SetStringField(TEXT("note"), TEXT("no property matched; values shown for the class form come from the CDO, and differsFromDefault is not computed (a CDO IS the default)"));
			}
			return;
		}

		UObject* Target = ResolvePropertyTarget(In, Out);
		if (!Target) { return; }
		MifDetailsEmitTargetKind(Target, Out);
		UObject* Archetype = MifDetailsArchetypeOf(Target);

		// --- one property, in full detail ----------------------------------------------
		if (!PropertyPath.IsEmpty())
		{
			FPropertyPathResolution Res;
			FString Error;
			if (!ResolvePropertyPathEx(Target, PropertyPath, Res, Error))
			{
				Fail(Out, Error);
				return;
			}
			// The default address: the SAME path resolved on the archetype. When the archetype does
			// not carry it - a variable a child Blueprint added, for instance - there is no archetype
			// default, and the row falls back to a freshly constructed one and SAYS which it used
			// (PropertyNode.cpp:1834-1862 is the engine's own version of this fork).
			const void* DefaultAddr = nullptr;
			FPropertyPathResolution DefaultRes;
			FString DefaultError;
			if (Archetype && Archetype != Target && ResolvePropertyPathEx(Archetype, PropertyPath, DefaultRes, DefaultError))
			{
				DefaultAddr = DefaultRes.LeafAddr;
			}
			Out->SetStringField(TEXT("form"), TEXT("property"));
			Out->SetStringField(TEXT("propertyPath"), PropertyPath);
			Out->SetBoolField(TEXT("isElement"), Res.bLeafIsElement);
			if (Res.bLeafIsElement)
			{
				Out->SetStringField(TEXT("elementPath"), Res.ElementDescription);
				Out->SetNumberField(TEXT("elementIndex"), Res.ElementIndex);
				if (!Res.ElementOrdering.IsEmpty()) { Out->SetStringField(TEXT("elementOrdering"), Res.ElementOrdering); }
			}
			Out->SetObjectField(TEXT("property"), MifDetailsDescribeProperty(
				Res.Leaf, Res.LeafAddr, Res.LeafContainerAddr, Res.LeafOwner, Archetype, DefaultAddr,
				Res.bLeafIsElement, bIncludeMetadata, bIncludeDefault, /*MaxValueChars*/ 0));
			Out->SetStringField(TEXT("leafOwner"), Res.LeafOwner->GetPathName());
			return;
		}

		// --- survey form ----------------------------------------------------------------
		TArray<TSharedPtr<FJsonValue>> Rows;
		int32 Matched = 0;
		bool bTruncated = false;
		for (TFieldIterator<FProperty> It(Target->GetClass()); It; ++It)
		{
			FProperty* Prop = *It;
			if (!Prop) { continue; }
			if (!NameFilter.IsEmpty() && !Prop->GetName().Contains(NameFilter)) { continue; }
			++Matched;
			if (Rows.Num() >= Limit) { bTruncated = true; continue; }   // keep counting: a cap must never read as completeness
			const void* ValueAddr = Prop->ContainerPtrToValuePtr<void>(Target);
			const void* DefaultAddr = nullptr;
			if (Archetype && Archetype != Target && Archetype->GetClass()->FindPropertyByName(Prop->GetFName()))
			{
				DefaultAddr = Prop->ContainerPtrToValuePtr<void>(Archetype);
			}
			Rows.Add(MakeShared<FJsonValueObject>(MifDetailsDescribeProperty(
				Prop, ValueAddr, Target, Target, Archetype, DefaultAddr, /*bSingleElement*/ false,
				bIncludeMetadata, bIncludeDefault, MaxValueChars)));
		}
		Out->SetStringField(TEXT("form"), TEXT("survey"));
		Out->SetNumberField(TEXT("count"), Rows.Num());
		Out->SetNumberField(TEXT("matched"), Matched);
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("properties"), Rows);
	}

	// =============================================================================================
	// diff_properties_vs_default - READ-ONLY
	//   in:  { objectPath (actorPath) | blueprintId (path) + widgetName, nameContains (filter,
	//          nameFilter)?, limit?, maxValueChars?, includeTransient?, deep?,
	//          recursive (includeChildren)? }
	//   out: { inspected, differing, matching, skippedTransient, expanded, truncated, recursive,
	//          properties[] }
	//
	// "What does this object actually OVERRIDE?" - the question the panel answers with a yellow
	// arrow and the bridge could not answer at all. The invariant
	// inspected == differing + matching + skippedTransient + expanded is EMITTED, not implied;
	// `expanded` is 0 unless recursion was asked for, so the three-term form every existing caller
	// checks still holds for them.
	//
	// `recursive` DEFAULTS TO FALSE and the top-level walk is untouched by it. Turned on, a struct
	// property that differs is OPENED instead of reported and its differing members are reported in
	// its place, each with a dotted `path` that reset_property_to_default accepts. It descends into
	// STRUCT MEMBERS ONLY - see MifDetailsWalkDiff for why containers, C-arrays and object pointers
	// are leaves and not an oversight.
	// =============================================================================================
	void H_diff_properties_vs_default(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			  TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"),
			  TEXT("limit"), TEXT("maxValueChars"), TEXT("includeTransient"), TEXT("deep"),
			  TEXT("recursive"), TEXT("includeChildren") },
			TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, nameContains (aliases filter, nameFilter), limit, maxValueChars, includeTransient, deep, recursive (alias includeChildren)")))
		{
			return;
		}

		UObject* Target = ResolvePropertyTarget(In, Out);
		if (!Target) { return; }

		const FString NameFilter    = JStrAny(In, { TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter") });
		const int32 Limit           = FMath::Clamp(JInt(In, TEXT("limit"), 200), 1, 5000);
		const int32 MaxValueChars   = FMath::Clamp(JInt(In, TEXT("maxValueChars"), 200), 16, 100000);
		const bool bIncludeTransient = JBool(In, TEXT("includeTransient"), false);
		// PPF_DeepComparison on an instanced-object property is the expensive case; bounded rather
		// than merely hoped about, and the response says when it was disabled.
		const bool bDeep            = JBool(In, TEXT("deep"), true);
		// OPTIONAL and defaulting to FALSE, so a caller who does not ask for it gets exactly the
		// top-level-only walk this endpoint has always done. When true, a struct property that differs
		// is OPENED rather than reported, and its differing members are reported instead - which is what
		// makes "Settings.BloomIntensity" appear as a row you can hand straight to
		// reset_property_to_default, instead of one "Settings" row whose value is a 4KB struct literal.
		const bool bRecursive       = JBoolAny(In, { TEXT("recursive"), TEXT("includeChildren") }, false);

		MifDetailsEmitTargetKind(Target, Out);
		UObject* Archetype = MifDetailsArchetypeOf(Target);
		Out->SetStringField(TEXT("archetype"), Archetype ? Archetype->GetPathName() : FString());
		Out->SetBoolField(TEXT("deep"), bDeep);

		if (!Archetype || Archetype == Target)
		{
			// A stated RESULT, not an error: the root CDO's archetype is itself, so nothing can differ.
			Out->SetNumberField(TEXT("inspected"), 0);
			Out->SetNumberField(TEXT("differing"), 0);
			Out->SetNumberField(TEXT("matching"), 0);
			Out->SetNumberField(TEXT("skippedTransient"), 0);
			Out->SetNumberField(TEXT("expanded"), 0);
			Out->SetBoolField(TEXT("recursive"), bRecursive);
			Out->SetBoolField(TEXT("truncated"), false);
			Out->SetArrayField(TEXT("properties"), TArray<TSharedPtr<FJsonValue>>());
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("'%s' has no distinct archetype (its archetype is itself), so every property matches by definition (differing:0)."),
				*Target->GetPathName()));
			return;
		}

		FMifDetailsDiffWalk Walk;
		Walk.ValueOwner       = Target;
		Walk.DefaultOwner     = Archetype;
		Walk.Limit            = Limit;
		Walk.MaxValueChars    = MaxValueChars;
		Walk.bIncludeTransient = bIncludeTransient;
		Walk.bDeep            = bDeep;
		for (TFieldIterator<FProperty> It(Target->GetClass()); It; ++It)
		{
			FProperty* Prop = *It;
			if (!Prop) { continue; }
			// The name filter selects TOP-LEVEL properties, as it always has. It is deliberately NOT
			// re-applied to nested members: a caller filtering on "Bloom" wants the members of the
			// struct it selected, not only the members that happen to repeat the word.
			if (!NameFilter.IsEmpty() && !Prop->GetName().Contains(NameFilter)) { continue; }
			if (Walk.Inspected >= FMifDetailsDiffWalk::kNodeBudget) { Walk.bBudgetExhausted = true; break; }
			++Walk.Inspected;
			if (!bIncludeTransient && Prop->HasAnyPropertyFlags(CPF_Transient))
			{
				// Transients always differ and drown the signal.
				++Walk.SkippedTransient;
				continue;
			}

			const void* ValueAddr = Prop->ContainerPtrToValuePtr<void>(Target);
			const void* DefaultAddr = nullptr;
			FString ConstructedText;
			bool bDiffers = false;
			if (Archetype->GetClass()->FindPropertyByName(Prop->GetFName()))
			{
				DefaultAddr   = Prop->ContainerPtrToValuePtr<void>(Archetype);
				bDiffers      = MifDetailsDiffersFromDefault(Prop, ValueAddr, DefaultAddr, /*bSingleElement*/ false, bDeep);
			}
			else
			{
				// No archetype ADDRESS at all, so there is nothing to recurse into: a constructed
				// default is a text answer only. Both sides of this compare are produced by the same
				// exporter (ExportAll), which is what makes it valid for a C-array UPROPERTY.
				ConstructedText = MifDetailsConstructedDefaultText(Prop, Target, /*bWholeProperty*/ true);
				bDiffers        = !ConstructedText.Equals(MifDetailsExportAll(Prop, ValueAddr, Target), ESearchCase::CaseSensitive);
			}

			if (!bDiffers) { ++Walk.Matching; continue; }

			const FStructProperty* SP = CastField<FStructProperty>(Prop);
			if (bRecursive && DefaultAddr && SP && SP->Struct && Prop->ArrayDim == 1)
			{
				const int32 DifferingBefore = Walk.Differing;
				++Walk.Expanded;
				MifDetailsWalkDiff(Walk, SP->Struct, ValueAddr, DefaultAddr, Prop->GetName(), /*Depth*/ 1);
				if (Walk.Differing != DifferingBefore) { continue; }
				--Walk.Expanded;   // opened it and found nothing inside; report the struct itself
			}
			++Walk.Differing;
			if (Walk.Rows.Num() >= Limit) { Walk.bTruncated = true; continue; }
			Walk.Rows.Add(MakeShared<FJsonValueObject>(MifDetailsMakeDiffRow(
				Prop, Prop->GetName(), ValueAddr, DefaultAddr, ConstructedText, Target, Archetype, MaxValueChars)));
		}

		Out->SetNumberField(TEXT("inspected"), Walk.Inspected);
		Out->SetNumberField(TEXT("differing"), Walk.Differing);
		Out->SetNumberField(TEXT("matching"), Walk.Matching);
		Out->SetNumberField(TEXT("skippedTransient"), Walk.SkippedTransient);
		Out->SetNumberField(TEXT("expanded"), Walk.Expanded);
		Out->SetBoolField(TEXT("recursive"), bRecursive);
		if (bRecursive) { Out->SetNumberField(TEXT("maxDepth"), Walk.MaxDepth); }
		Out->SetBoolField(TEXT("truncated"), Walk.bTruncated);
		Out->SetArrayField(TEXT("properties"), Walk.Rows);
		if (Walk.bBudgetExhausted)
		{
			AddWarning(Out, FString::Printf(
				TEXT("the walk stopped after visiting %d properties (the node budget) and did NOT finish, so `inspected` and `matching` ")
				TEXT("under-report and an override past that point is not in this response. Narrow it with nameContains, or turn recursive off."),
				FMifDetailsDiffWalk::kNodeBudget));
		}
		// The checkable invariant, emitted rather than implied. `expanded` (a struct that was opened
		// instead of reported) is always 0 when recursion is off, so this reduces to the three-term
		// form for every caller that does not pass recursive:true.
		Out->SetBoolField(TEXT("countsConsistent"),
			Walk.Inspected == (Walk.Differing + Walk.Matching + Walk.SkippedTransient + Walk.Expanded));
	}

	// =============================================================================================
	// reset_property_to_default - TRANSACTED
	//   in:  { objectPath (actorPath), propertyPath (property), force (allowEditConst)?,
	//          overrideFlag (editCondition, override): set | refuse | ignore = "ignore" }
	//   out: { target, propertyPath, valueBefore, defaultValue, valueAfter, differedFromDefault,
	//          changed, defaultSource, archetype, verified, notification, editConditionKind,
	//          editCondition?, editConditionMet?, editConditionFlag?, overrideFlagUnmet?, arrayDim?,
	//          archetypeShapeMismatch?, verifyFailure?, ... }
	//
	// `force` waives exactly ONE refusal, the one it has always waived: CPF_EditConst. It does NOT
	// touch the meta EditCondition gate. A previous revision of this handler overloaded it with that
	// second meaning and refused a gated reset unless force:true - a new unconditional refusal on a
	// shipped write endpoint, which broke every caller that had ever reset a gated property. That is
	// the same breaking change this wave already reversed for edit_container (:1747-1780), and it is
	// reversed here the same way.
	//
	// A closed meta EditCondition is answered by `overrideFlag`, spelled exactly as edit_container and
	// set_property spell it (set | refuse | ignore, aliases editCondition / override) so ONE vocabulary
	// covers all three write endpoints. It defaults to "ignore" - the pre-wave behaviour, minus the
	// silence: the editCondition* fields are always reported and a closed gate always raises a warning.
	// "set" is the one word this endpoint refuses rather than honours, because writing the companion
	// flag would make a RESET turn a feature ON; see the gate block below. server.py's docstring still
	// claims "force=True waives TWO refusals" (server.py:897) and does not pass `overrideFlag` at all -
	// that file is owned elsewhere and both corrections are REPORTED rather than made. Nothing is broken
	// meanwhile, precisely because the default is the shipped behaviour.
	//
	// ARCHETYPE SHAPE MISMATCH - a REFUSAL this endpoint can return that server.py does not yet
	// document. When the path resolves on the object and on the archetype to properties with a
	// different FField class, ArrayDim or ElementSize, the call fails with archetypeShapeMismatch:true
	// and nothingModified:true. It is not a caller mistake and not a transient: everything downstream
	// indexes the ARCHETYPE's memory with the LIVE leaf's ArrayDim/ElementSize, so comparing - and
	// then copying - across mismatched declarations would read past the archetype's allocation. The
	// realistic cause is a class that was reinstanced after a live C++/Blueprint change while a stale
	// archetype is still referenced, or a child class redeclaring an inherited name with a different
	// type. The actionable answers are in the error text: reopen/recompile the asset so the archetype
	// is rebuilt, or write the intended value explicitly with set_property, which never touches the
	// archetype's memory. server.py's docstring should name this refusal.
	//
	// VERIFICATION compares against the DEFAULT, never against the staged buffer. Those are the same
	// bytes only on the whole-C-array branch, which copies the default wholesale; the other branch
	// SEEDS staging from the live value and imports the default TEXT over it, so any member the
	// default literal did not mention survives - and verifying against staging would then pass while
	// the live value still differs from the actual default. That is a false `verified:true`, which is
	// the one answer this endpoint must never give.
	//
	// The Details panel's yellow arrow. Refusals the panel applies and a naive reset does not:
	// CPF_Config properties have NO reset arrow, CPF_EditFixedSize containers have none either
	// (FPropertyHandleBase::CanResetToDefault, PropertyHandleImpl.cpp:3421-3433), and a row whose meta
	// EditCondition is not met is greyed along with its arrow.
	//
	// C-ARRAYS. A leaf with ArrayDim > 1 addressed WHOLE ("FloatCurves", not "FloatCurves[2]") is reset
	// across EVERY element, and verified across every element. Both used to collapse to element 0 while
	// the difference DETECTION was already ArrayDim-wide, so a `float Foo[4]` whose only override was
	// Foo[3] reported ok / verified:true with the override untouched. See the staging block below.
	// =============================================================================================
	void H_reset_property_to_default(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			  TEXT("propertyPath"), TEXT("property"), TEXT("force"), TEXT("allowEditConst"),
			  TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override") },
			TEXT("objectPath (alias actorPath), propertyPath (alias property), force (alias allowEditConst), overrideFlag (set|refuse|ignore)")))
		{
			return;
		}

		const FString PropertyPath = JStrAny(In, { TEXT("propertyPath"), TEXT("property") });
		if (PropertyPath.IsEmpty())
		{
			Fail(Out, TEXT("propertyPath required (alias property) - a dot path, e.g. Settings.BloomIntensity, or an element such as OverrideMaterials[1]"));
			return;
		}
		const bool bForce = JBoolAny(In, { TEXT("force"), TEXT("allowEditConst") }, false);

		// The meta-EditCondition escape, spelled and validated exactly as edit_container spells it
		// (:1692-1704) and set_property before it (MifBridgeNodes5.cpp:1000-1015): same key, same three
		// words, same aliases, same PM-002 rule that a string-to-enum dispatch never has a silent
		// default. The DEFAULT is "ignore", which is what this endpoint did before the gate was added, so
		// no shipped caller changes behaviour. "set" is part of the shared vocabulary and is validated
		// here, then REFUSED at the gate below rather than silently downgraded - the reason is there.
		FString OverrideFlagMode = JStrAny(In, { TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override") }, TEXT("ignore"));
		OverrideFlagMode = OverrideFlagMode.TrimStartAndEnd().ToLower();
		if (OverrideFlagMode != TEXT("set") && OverrideFlagMode != TEXT("refuse") && OverrideFlagMode != TEXT("ignore"))
		{
			Fail(Out, FString::Printf(
				TEXT("overrideFlag '%s' is not one of set | refuse | ignore. 'ignore' (the default) resets the property behind a closed ")
				TEXT("meta EditCondition and WARNS that the engine will not read it; 'refuse' fails naming the flag; 'set' is refused by ")
				TEXT("THIS endpoint, because a reset must never turn a feature on. Nothing was changed."),
				*JStrAny(In, { TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override") })));
			return;
		}

		UObject* Target = MifDetailsResolveWritableTarget(In, Out, TEXT("reset_property_to_default"));
		if (!Target) { return; }

		FPropertyPathResolution Res;
		FString Error;
		if (!ResolvePropertyPathEx(Target, PropertyPath, Res, Error)) { Fail(Out, Error); return; }
		FProperty* Leaf      = Res.Leaf;
		void*      LeafAddr  = Res.LeafAddr;
		UObject*   LeafOwner = Res.LeafOwner;
		// Captured BEFORE the write: a construction-script rerun renames the object we are holding to
		// TRASH_<Class>_N, so its path is only readable now.
		const FString TargetPathAtWrite = Target->GetPathName();

		// --- the panel's FLAG refusals, BEFORE anything is touched ----------------------
		// (the metadata one, EditCondition, follows immediately after and is answered by `overrideFlag`
		//  rather than refused outright)
		if (Leaf->HasAnyPropertyFlags(CPF_Config))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is CPF_Config: the Details panel shows NO reset arrow for config properties ")
				TEXT("(FPropertyHandleBase::CanResetToDefault, PropertyHandleImpl.cpp:3421-3433), because its value comes from an ")
				TEXT(".ini rather than from the archetype. Edit the .ini, or use set_property to write the archetype's value ")
				TEXT("explicitly. Nothing was changed."),
				*PropertyPath));
			return;
		}
		if (Leaf->HasAnyPropertyFlags(CPF_EditFixedSize))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is CPF_EditFixedSize: its elements can be edited but the container cannot be resized, and the panel ")
				TEXT("therefore offers no reset arrow either (PropertyHandleImpl.cpp:3421-3433). Reset the individual elements ")
				TEXT("instead. Nothing was changed."),
				*PropertyPath));
			return;
		}
		if (Leaf->HasAnyPropertyFlags(CPF_EditConst) && !bForce)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is CPF_EditConst (VisibleAnywhere / VisibleDefaultsOnly / VisibleInstanceOnly): a human cannot reset it ")
				TEXT("in the panel, which greys the row. Pass force:true to reset it anyway. Nothing was changed."),
				*PropertyPath));
			return;
		}

		// --- the panel's THIRD gate: is the row's GATE open? -----------------------------
		// Decided here, before any archetype work and long before any write, because a refusal must
		// leave nothing behind and ORDER is the only mechanism for that (PM-007: a cancelled
		// transaction reverts nothing at all). Same position set_property gates at
		// (MifBridgeNodes5.cpp:1058-1082).
		//
		// The panel greys a gated row and its reset arrow TOGETHER, and the engine branches on the
		// companion FLAG rather than on this member. So a reset behind a closed gate really does write
		// memory - the old response was not lying about `verified` - and really does change nothing
		// else. It is the implied EFFECT that was dishonest, and SILENCE is the whole of the defect.
		//
		// THREE ANSWERS, and the caller picks - the same key, the same three words and the same default
		// as edit_container (:1747-1780), so the two endpoints are one mental model rather than two. The
		// previous revision of this block refused UNCONDITIONALLY unless force:true, which is the
		// identical breaking change this same wave identified and reversed for edit_container: a shipped
		// write endpoint that had always reset gated properties began hard-failing, so every caller that
		// reset one broke. The rule that settles it is not a preference - new behaviour is opt-in and
		// the default is today's behaviour.
		//
		//   ignore (default) - perform the reset, report the gate, and WARN that the engine reads the
		//                      companion FLAG and will not read this member until that flag is set.
		//                      Pre-wave behaviour, minus the silence.
		//   refuse           - fail, naming the flag and the value it needs. The strict behaviour, now
		//                      opt-in rather than imposed.
		//   set              - REFUSED here, where set_property and edit_container honour it. A caller
		//                      handing set_property a VALUE for a gated member plausibly means "and turn
		//                      the feature on"; "reset this to its default" carries no such intent, and
		//                      writing the flag would make a RESET turn a feature ON, which no reset
		//                      should ever do. The companion action for a reset is to RESET the flag as
		//                      well - a second call to this same endpoint. Refused rather than silently
		//                      downgraded to "ignore" (PM-002: no silent default).
		//
		// `force` is deliberately NOT consulted below. It means "waive CPF_EditConst", the one thing it
		// has always meant; overloading it with this second gate is what made server.py's docstring
		// wrong. That file is not this agent's to edit and the correction is REPORTED instead.
		FEditConditionInfo EC;
		InspectEditCondition(Leaf, Res.LeafContainerAddr, EC);
		if (EC.bHasMeta)            { Out->SetStringField(TEXT("editCondition"), EC.MetaText); }
		Out->SetStringField(TEXT("editConditionKind"), EC.Kind);
		if (EC.bEvaluated)          { Out->SetBoolField(TEXT("editConditionMet"), EC.bMet); }
		if (!EC.FlagName.IsEmpty()) { Out->SetStringField(TEXT("editConditionFlag"), EC.FlagName); }
		// bEvaluated, NEVER bMet alone. An expression this bridge cannot parse comes back
		// Kind:"unevaluated" with bMet DEFAULTED to true (MifBridgeHandlers.h:491,
		// MifBridgeCommon.cpp:2319-2329), and so does a gated property addressed as an element of a
		// dynamic container, which has no declaring container to find the sibling flag in
		// (MifBridgeCommon.cpp:2351-2359). Both must pass through ungated rather than be refused on a
		// guess.
		const bool bGateClosed = EC.bEvaluated && !EC.bMet;
		if (bGateClosed && OverrideFlagMode == TEXT("set"))
		{
			Out->SetStringField(TEXT("propertyPath"), PropertyPath);
			Out->SetBoolField(TEXT("nothingModified"), true);
			Fail(Out, FString::Printf(
				TEXT("overrideFlag:\"set\" is not available on reset_property_to_default. '%s' is gated by meta EditCondition=\"%s\", and ")
				TEXT("writing the companion flag '%s' = %s would make a RESET turn a feature ON - a change to a property you did not ")
				TEXT("address, which no reset should ever make (set_property and edit_container accept \"set\" because a caller handing ")
				TEXT("them a value plausibly means it; a reset does not). Reset '%s' itself with a second call to this endpoint - that is ")
				TEXT("a reset's companion action - or pass overrideFlag:\"ignore\" (the default) to reset only '%s' behind the closed ")
				TEXT("gate. Nothing was changed."),
				*PropertyPath, *EC.MetaText, *EC.FlagName, EC.bRequiredFlagValue ? TEXT("True") : TEXT("False"),
				*EC.FlagName, *PropertyPath));
			return;
		}
		if (bGateClosed && OverrideFlagMode == TEXT("refuse"))
		{
			Out->SetStringField(TEXT("propertyPath"), PropertyPath);
			Out->SetBoolField(TEXT("nothingModified"), true);
			Fail(Out, FString::Printf(
				TEXT("'%s' is gated by meta EditCondition=\"%s\" and the companion flag '%s' is currently %s, so the Details panel greys ")
				TEXT("the row and its reset arrow with it. The engine reads the FLAG, not this member, so resetting '%s' now would change ")
				TEXT("memory and change nothing else. You asked for overrideFlag:\"refuse\". Reset or set '%s' instead - that is the edit ")
				TEXT("the panel would have you make - or pass overrideFlag:\"ignore\" (the default) to reset behind the closed gate on ")
				TEXT("purpose. Nothing was changed."),
				*PropertyPath, *EC.MetaText, *EC.FlagName, EC.bRequiredFlagValue ? TEXT("False") : TEXT("True"),
				*PropertyPath, *EC.FlagName));
			return;
		}
		if (bGateClosed)
		{
			// Raised HERE rather than after the write, so it survives every later exit: a caller stopped
			// by the archetype-shape refusal, or told the property already equals its default, still
			// needs to know the row it addressed is one the engine is not reading. Phrased without
			// tense for the same reason - whether a write lands is decided further down.
			Out->SetBoolField(TEXT("overrideFlagUnmet"), true);
			AddWarning(Out, FString::Printf(
				TEXT("RESET BEHIND A CLOSED GATE: '%s' is gated by meta EditCondition=\"%s\" and the flag '%s' is %s, so the Details panel ")
				TEXT("greys this row and its reset arrow and the engine reads the FLAG rather than this member. Resetting '%s' changes ")
				TEXT("memory and changes nothing else. Reset or set '%s' as well to make it take effect; pass overrideFlag:\"refuse\" if ")
				TEXT("you would rather be stopped than warned."),
				*PropertyPath, *EC.MetaText, *EC.FlagName, EC.bRequiredFlagValue ? TEXT("False") : TEXT("True"),
				*PropertyPath, *EC.FlagName));
		}

		// --- the default value ----------------------------------------------------------
		// ALWAYS materialised as an ADDRESS, never as text alone. The constructed fallback used to be
		// text-only, which forced the differ/verify compares down a text path - and text cannot see
		// past element 0 of a C-array UPROPERTY (ExportText_Direct emits ONE element,
		// Property.cpp:1139-1164). One address means ONE predicate below: FProperty::Identical over
		// every element the caller addressed, for both values of defaultSource.
		UObject* Archetype = MifDetailsArchetypeOf(Target);
		FString DefaultText, DefaultSource;
		FPropertyPathResolution DefaultRes;
		FString DefaultError;
		const void* DefaultAddr = nullptr;
		FMifDetailsValueScratch ConstructedDefault;   // populated on the fallback branch only
		if (Archetype && Archetype != Target && ResolvePropertyPathEx(Archetype, PropertyPath, DefaultRes, DefaultError))
		{
			// SHAPE GUARD, before anything reads THROUGH DefaultAddr. Everything below indexes the
			// archetype's memory with the LIVE leaf's ArrayDim/ElementSize, so a differently-declared
			// archetype property would compare - and then copy - past the end of the archetype's
			// allocation. Normally the two are the very same FProperty; a reinstanced or child class is
			// the case that makes this checked rather than assumed.
			if (!DefaultRes.Leaf
				|| DefaultRes.Leaf->GetClass()  != Leaf->GetClass()
				|| DefaultRes.Leaf->ArrayDim    != Leaf->ArrayDim
				|| MifPropertyElementSize(DefaultRes.Leaf) != MifPropertyElementSize(Leaf))
			{
				Out->SetBoolField(TEXT("changed"), false);
				Out->SetBoolField(TEXT("verified"), false);
				Out->SetBoolField(TEXT("nothingModified"), true);
				// Machine-readable, because "the archetype disagrees about the declaration" is a
				// DIFFERENT refusal from every other failure this endpoint returns and a caller should
				// not have to string-match to tell. Additive field; nothing existing changes shape.
				Out->SetBoolField(TEXT("archetypeShapeMismatch"), true);
				// Report every dimension that was checked, not just the one that happened to differ:
				// a caller cannot act on "they are different" without being told HOW.
				Fail(Out, FString::Printf(
					TEXT("'%s' resolves on the object as %s [ArrayDim %d, ElementSize %d], but on the archetype '%s' as %s ")
					TEXT("[ArrayDim %d, ElementSize %d]. Refusing to compare or copy across mismatched declarations: every read below ")
					TEXT("indexes the ARCHETYPE's memory with the LIVE property's ArrayDim/ElementSize, so proceeding would read - and ")
					TEXT("then write - past the end of the archetype's allocation. This is not a bad propertyPath; the path resolved on ")
					TEXT("BOTH objects. It means the two declarations have drifted apart, which happens when a class was reinstanced ")
					TEXT("after a live C++ or Blueprint change while a stale archetype is still referenced, or when a child class ")
					TEXT("redeclares an inherited name with a different type. What to do: reopen or recompile the asset so the archetype ")
					TEXT("is rebuilt and call this again, or write the intended value explicitly with set_property, which never touches ")
					TEXT("the archetype's memory. Nothing was changed."),
					*PropertyPath, *Leaf->GetClass()->GetName(), Leaf->ArrayDim, MifPropertyElementSize(Leaf),
					*Archetype->GetPathName(),
					DefaultRes.Leaf ? *DefaultRes.Leaf->GetClass()->GetName() : TEXT("<unresolved>"),
					DefaultRes.Leaf ? DefaultRes.Leaf->ArrayDim : 0,
					DefaultRes.Leaf ? MifPropertyElementSize(DefaultRes.Leaf) : 0));
				return;
			}
			DefaultAddr   = DefaultRes.LeafAddr;
			DefaultText   = Res.bLeafIsElement
				? MifDetailsExportOne(DefaultRes.Leaf, DefaultRes.LeafAddr, DefaultRes.LeafOwner)
				: MifDetailsExportAll(DefaultRes.Leaf, DefaultRes.LeafAddr, DefaultRes.LeafOwner);
			DefaultSource = TEXT("archetype");
		}
		else
		{
			// The same buffer MifDetailsConstructedDefaultText builds, kept ALIVE instead of exported
			// and dropped, so the compares below have an address to work with. For an element leaf only
			// slot 0 is used, which is harmless: InitializeValue/DestroyValue span ArrayDim either way.
			// For ArrayDim <= 1 the text this produces is byte-identical to what the old call returned.
			ConstructedDefault.Init(Leaf);
			DefaultAddr   = ConstructedDefault.Mem;
			DefaultText   = Res.bLeafIsElement
				? MifDetailsExportOne(Leaf, DefaultAddr, LeafOwner)
				: MifDetailsExportAll(Leaf, DefaultAddr, LeafOwner);
			DefaultSource = TEXT("constructed");
		}
		if (!DefaultAddr)
		{
			// Only reachable if the scratch allocation failed. Refuse rather than fall through: a null
			// default makes MifDetailsDiffersFromDefault answer "false", which would report
			// differedFromDefault:false / verified:true for a property nobody ever looked at.
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetBoolField(TEXT("verified"), false);
			Out->SetBoolField(TEXT("nothingModified"), true);
			Fail(Out, FString::Printf(
				TEXT("could not materialise a default value for '%s' to compare against, so this call cannot say whether a reset is ")
				TEXT("needed or whether one landed. Nothing was changed."),
				*PropertyPath));
			return;
		}

		// OUR OWN COPY OF THE DEFAULT, taken BEFORE the write, because the verification at the bottom
		// compares against the DEFAULT and DefaultAddr cannot be trusted to survive the write. On the
		// archetype branch it points INTO the archetype's memory, and a construction-script rerun or a
		// reinstance can free that out from under us; the constructed branch is already our memory but
		// is snapshotted the same way so there is ONE comparand and ONE lifetime rule below rather
		// than a branch nobody will remember to keep in step.
		//
		// This is what used to be missing. The verification compared against Staging.Mem - which the
		// non-C-array branch builds by SEEDING from the live value and importing the default TEXT over
		// it, so every member the default literal did not mention is carried over from the live value.
		// Comparing the written value against that buffer asks "did the copy land", never "is this the
		// default", and passes while the property still differs from its default. A false
		// `verified:true` is the one answer this endpoint must never give.
		//
		// Element convention matches ConstructedDefault above: an element leaf uses slot 0 only, so
		// CopySingleValue, and the compare below runs with bSingleElement=true. A whole property is
		// ArrayDim-wide on both branches, so CopyCompleteValue.
		FMifDetailsValueScratch DefaultSnapshot(Leaf);
		if (!DefaultSnapshot.Mem)
		{
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetBoolField(TEXT("verified"), false);
			Out->SetBoolField(TEXT("nothingModified"), true);
			Fail(Out, FString::Printf(
				TEXT("could not allocate a snapshot of the default for '%s', and without one this call could not verify a reset ")
				TEXT("against the DEFAULT afterwards. Refusing rather than writing something it cannot check. Nothing was changed."),
				*PropertyPath));
			return;
		}
		if (Res.bLeafIsElement) { Leaf->CopySingleValue(DefaultSnapshot.Mem, DefaultAddr); }
		else                    { Leaf->CopyCompleteValue(DefaultSnapshot.Mem, DefaultAddr); }

		const FString BeforeText = Res.bLeafIsElement
			? MifDetailsExportOne(Leaf, LeafAddr, LeafOwner)
			: MifDetailsExportAll(Leaf, LeafAddr, LeafOwner);
		// ONE predicate, for both values of defaultSource: FProperty::Identical over every element the
		// caller addressed (MifDetailsDiffersFromDefault loops ArrayDim unless bSingleElement). The
		// text fallback that used to sit on the constructed branch had exactly the element-0 blindness
		// the write path had.
		const bool bDiffered = MifDetailsDiffersFromDefault(Leaf, LeafAddr, DefaultAddr, Res.bLeafIsElement, /*bDeep*/ true);

		Out->SetStringField(TEXT("propertyPath"), PropertyPath);
		Out->SetStringField(TEXT("leafProperty"), Leaf->GetName());
		Out->SetStringField(TEXT("leafType"), Leaf->GetCPPType());
		Out->SetStringField(TEXT("valueBefore"), BeforeText);
		Out->SetStringField(TEXT("defaultValue"), DefaultText);
		Out->SetStringField(TEXT("defaultSource"), DefaultSource);
		Out->SetStringField(TEXT("archetype"), Archetype ? Archetype->GetPathName() : FString());
		Out->SetBoolField(TEXT("differedFromDefault"), bDiffered);
		MifDetailsEmitTargetKind(Target, Out);
		if (DefaultSource == TEXT("constructed"))
		{
			AddWarning(Out, FString::Printf(
				TEXT("'%s' does not exist on the archetype ('%s'), so the reset used a FRESHLY CONSTRUCTED default instead of an ")
				TEXT("inherited one (defaultSource:\"constructed\"). This is the case for a variable a child Blueprint added."),
				*PropertyPath, Archetype ? *Archetype->GetPathName() : TEXT("<none>")));
		}

		if (!bDiffered)
		{
			// Reported, not failed - the same shape as set_property's idempotent-write note.
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetBoolField(TEXT("verified"), true);
			Out->SetStringField(TEXT("valueAfter"), BeforeText);
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("'%s' already equals its default (%s); nothing to reset (changed:false)."), *PropertyPath, *DefaultText));
			return;
		}

		// --- write, with the same bracket set_property uses ------------------------------
		// PPF_InstanceSubobjects mirrors the panel's own reset
		// (FPropertyValueImpl::ResetToDefault, PropertyHandleImpl.cpp:992-1008, whose
		// EPropertyValueSetFlags::InstanceObjects maps to PPF_InstanceSubobjects at :490-492).
		// PM-003: the panel imports into the LIVE address (PropertyTextUtilities.cpp:34). We do not.
		FEditPropertyChain EditChain;
		bool bChainBuilt = Res.Chain.Num() > 0;
		for (FProperty* Segment : Res.Chain)
		{
			if (!Segment) { bChainBuilt = false; break; }
			EditChain.AddTail(Segment);
		}
		if (bChainBuilt)
		{
			bChainBuilt = EditChain.SetActivePropertyNode(Res.Chain.Last())
				&& EditChain.SetActiveMemberPropertyNode(Res.Chain[0]);
		}

		// PARSE FIRST, into a staging buffer, and only then open the notification bracket. Calling
		// PreEditChange and then failing would leave UActorComponent::PreEditChange's
		// FComponentReregisterContext un-consumed (ActorComponent.cpp:806-822 is matched only by
		// ConsolidatedPostEditChange at :927-941) - a dangling registration on a live component.
		//
		// A WHOLE fixed-size C-array UPROPERTY has no text form to re-import. ExportText_Direct and
		// ImportText_Direct are ONE element each (Property.cpp:1139-1164, UnrealType.h:499-507), so
		// DefaultText describes element 0 and re-importing it could only ever restore element 0 - which
		// is exactly what this handler used to do: differedFromDefault was already ArrayDim-correct
		// (MifDetailsDiffersFromDefault with bSingleElement=false loops ArrayDim), so it PROVED that
		// elements differed, fixed one, and then verified with a compare that could not see the others.
		// Copy the default VALUE across all ArrayDim elements instead.
		//
		// THIS IS ONE CHANGE WITH THE PUBLISH BELOW, not two. CopyCompleteValue publishing a Staging
		// that had only been SEEDED at element 0 would overwrite elements 1..N-1 with CONSTRUCTED
		// defaults rather than the archetype's - a silent WRONG WRITE in place of a silent no-op, which
		// is worse than the bug. Neither half is correct alone.
		const bool bWholeCArray = (!Res.bLeafIsElement && Leaf->ArrayDim > 1);

		FMifDetailsValueScratch Staging(Leaf);
		if (!Staging.Mem)
		{
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetBoolField(TEXT("verified"), false);
			Out->SetBoolField(TEXT("nothingModified"), true);
			Fail(Out, FString::Printf(TEXT("could not allocate a staging buffer for '%s'. Nothing was changed."), *PropertyPath));
			return;
		}
		FString StagedText, ImportError;
		bool bParsed = true;
		if (bWholeCArray)
		{
			// DefaultAddr is shape-checked against Leaf above. No LeafArrayBase arithmetic is needed
			// here: when bLeafIsElement is false, Res.LeafCArrayIndex is provably 0 - the walker assigns
			// the C-array index only inside the branch that also sets bSegIsElement
			// (MifBridgeCommon.cpp:1848-1874, SegCArrayIndex and bSegIsElement set together at :1868 and
			// :1871) and copies the pair onto the leaf together (:2034-2035) - so LeafAddr IS the base of
			// the array. set_property needs that arithmetic only because it runs the ELEMENT case through
			// an ArrayDim-wide scratch, which this handler does not.
			Leaf->CopyCompleteValue(Staging.Mem, DefaultAddr);   // all ArrayDim elements
			StagedText = MifDetailsExportAll(Leaf, Staging.Mem, LeafOwner);
		}
		else
		{
			Leaf->CopySingleValue(Staging.Mem, LeafAddr);   // seed, so a partial default literal keeps the rest
			bParsed = ImportPropertyTextSafely(Leaf, DefaultText, LeafAddr, Staging.Mem, LeafOwner, StagedText, ImportError);
		}
		if (!bParsed)
		{
			// PM-003: the parser only ever saw scratch memory, so the live value is untouched.
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetBoolField(TEXT("verified"), false);
			Out->SetBoolField(TEXT("nothingModified"), true);
			Fail(Out, FString::Printf(TEXT("reset of '%s' failed while re-importing the default text '%s': %s The property is unchanged."),
				*PropertyPath, *DefaultText, *ImportError));
			return;
		}

		// The FProperty the staging buffer was built and TYPED against. If a construction-script rerun
		// retargets us onto a different declaration below, comparing live memory through that one
		// against this buffer is not a comparison this endpoint can stand behind.
		FProperty* const StagedLeaf = Leaf;

		LeafOwner->Modify();
		if (bChainBuilt) { LeafOwner->PreEditChange(EditChain); } else { LeafOwner->PreEditChange(Leaf); }
		// CopySingleValue publishes ONE element - a container row, or one slot of a C-array;
		// CopyCompleteValue publishes all ArrayDim. set_property has branched exactly this way all
		// along (MifBridgeNodes5.cpp:1247-1250) and this handler did not, which is how a reset of a
		// `float Foo[4]` wrote element 0 and left elements 1..3 overridden. LeafAddr is the correct base
		// for BOTH branches, and for ArrayDim == 1 the two calls are bit-identical, so no existing path
		// changes behaviour.
		if (Res.bLeafIsElement) { Leaf->CopySingleValue(LeafAddr, Staging.Mem); }
		else                    { Leaf->CopyCompleteValue(LeafAddr, Staging.Mem); }
		// Staging is NOT the comparand. It is kept alive only so StagedText's provenance is intact and
		// so the failure message below can report what was published alongside what was expected;
		// FMifDetailsValueScratch releases it at every exit from here on. The comparand is
		// DefaultSnapshot - our own copy of the DEFAULT, taken before the write. Verifying against
		// Staging was the false-pass: on this branch Staging is the live value with the default's TEXT
		// imported over it, so it agrees with what was written by construction and can agree while the
		// property still differs from its default. DefaultAddr itself is NOT read again from here on -
		// reinstancing can invalidate the archetype's address, which is exactly why the snapshot exists.

		FPropertyChangedEvent Evt(Leaf, EPropertyChangeType::ValueSet);
		if (bChainBuilt)
		{
			Evt.SetActiveMemberProperty(Res.Chain[0]);
			FPropertyChangedChainEvent ChainEvt(EditChain, Evt);
			ChainEvt.ChangeType = EPropertyChangeType::ValueSet;
			LeafOwner->PostEditChangeChainProperty(ChainEvt);
		}
		else
		{
			LeafOwner->PostEditChangeProperty(Evt);
		}
		LeafOwner->MarkPackageDirty();
		Out->SetStringField(TEXT("notification"), bChainBuilt ? TEXT("chain") : TEXT("plain"));

		// --- verify by re-reading, re-resolving first if a rerun replaced the object -----
		auto IsTrashed = [](const UObject* Obj)
		{
			return Obj == nullptr || !IsValid(Obj) || Obj->GetName().StartsWith(TEXT("TRASH_"));
		};
		bool bReconstructed = false;
		if (IsTrashed(LeafOwner) || IsTrashed(Target))
		{
			bReconstructed = true;
			// StaticFindObject, never StaticLoadObject: the package is already loaded and nothing here
			// may resurrect anything.
			UObject* NewTarget = StaticFindObject(UObject::StaticClass(), nullptr, *TargetPathAtWrite);
			FPropertyPathResolution NewRes;
			FString RetargetError;
			if (NewTarget && !IsTrashed(NewTarget) && ResolvePropertyPathEx(NewTarget, PropertyPath, NewRes, RetargetError))
			{
				Leaf = NewRes.Leaf; LeafAddr = NewRes.LeafAddr; LeafOwner = NewRes.LeafOwner;
				Out->SetStringField(TEXT("retargetedTo"), LeafOwner->GetPathName());
			}
			else
			{
				Out->SetBoolField(TEXT("verified"), false);
				Out->SetBoolField(TEXT("reconstructed"), true);
				Fail(Out, FString::Printf(
					TEXT("the reset of '%s' triggered a construction-script rerun that destroyed the component (renamed TRASH_*) and ")
					TEXT("the replacement could not be re-resolved. The reset is UNVERIFIED. Never read a trashed pointer back."),
					*PropertyPath));
				return;
			}
		}
		Out->SetBoolField(TEXT("reconstructed"), bReconstructed);
		Out->SetStringField(TEXT("verifiedOn"), LeafOwner->GetPathName());

		const FString AfterText = Res.bLeafIsElement
			? MifDetailsExportOne(Leaf, LeafAddr, LeafOwner)
			: MifDetailsExportAll(Leaf, LeafAddr, LeafOwner);
		Out->SetStringField(TEXT("valueAfter"), AfterText);
		Out->SetBoolField(TEXT("changed"), !AfterText.Equals(BeforeText, ESearchCase::CaseSensitive));
		if (Leaf->ArrayDim > 1) { Out->SetNumberField(TEXT("arrayDim"), Leaf->ArrayDim); }

		// A retarget after a construction-script rerun can hand back a DIFFERENT FProperty. Staging AND
		// DefaultSnapshot were both built and typed against the one that existed before the write, so
		// there is no comparison left that this endpoint can stand behind - say so rather than pass.
		// Never a false ok. This is also what keeps the element loop below in bounds: a different
		// declaration can carry a different ArrayDim/ElementSize, and the snapshot is sized for the old
		// one.
		if (Leaf != StagedLeaf)
		{
			Out->SetBoolField(TEXT("verified"), false);
			Fail(Out, FString::Printf(
				TEXT("the reset of '%s' was written, but the object was reconstructed and the path now resolves to a DIFFERENT FProperty ")
				TEXT("declaration, so the live value cannot be compared against what was staged. The reset is UNVERIFIED - re-read the ")
				TEXT("property with describe_property to confirm it."),
				*PropertyPath));
			return;
		}

		// THE INVARIANT THIS ENDPOINT STAKES ITS `ok` ON: after a successful reset the live value equals
		// THE DEFAULT - FProperty::Identical, every element the caller addressed, against the snapshot
		// of the default taken before the write.
		//
		// It is compared against the DEFAULT and not against Staging, and that is the whole point.
		// Staging and the default are the same bytes ONLY on the bWholeCArray branch, which copies the
		// default wholesale. The other branch seeds Staging from the LIVE value and imports the default
		// TEXT over it, and ImportPropertyTextSafely preserves every member the default literal did not
		// mention - so a Staging compare asks "did my copy land" (it always did, we performed it) while
		// differedFromDefault above asked the real question against DefaultAddr. The two halves
		// disagreed, and the half that decided `verified` was the one that could not fail. That is the
		// false pass this lane was opened to remove.
		//
		// A text compare cannot carry the invariant either: ExportText_Direct emits ONE element
		// (Property.cpp:1139-1164), so for a C-array UPROPERTY it puts element 0 against element 0 and
		// returns equal no matter what elements 1..ArrayDim-1 hold.
		FString VerifyFailure;
		if (!MifDetailsEqualsDefaultVerbose(Leaf, LeafAddr, DefaultSnapshot.Mem, Res.bLeafIsElement, /*bDeep*/ true, VerifyFailure))
		{
			const FString CArrayNote = Leaf->ArrayDim > 1
				? FString::Printf(TEXT(" (a fixed-size C-array of %d elements)"), Leaf->ArrayDim)
				: FString();
			Out->SetBoolField(TEXT("verified"), false);
			// The reason, machine-readable beside the prose, because "it differs" and "it could not be
			// compared" are different outcomes and only one of them means the write failed.
			Out->SetStringField(TEXT("verifyFailure"), VerifyFailure);
			Fail(Out, FString::Printf(
				TEXT("reset of '%s'%s is NOT verified: %s. The default is '%s', the staged value was '%s', and re-reading the property ")
				TEXT("returned '%s'. The comparison is FProperty::Identical against the DEFAULT over every element addressed - not ")
				TEXT("against the staged buffer, and not a text compare of element 0. Either a native setter or PostEditChangeProperty ")
				TEXT("adjusted the value after the write, or the default's exported text does not describe the whole default (a partial ")
				TEXT("struct literal leaves the members it omits at their previous values). The write DID happen; only the claim that it ")
				TEXT("produced the default is withheld. Compare valueBefore / defaultValue / valueAfter in this response, and re-read ")
				TEXT("with describe_property."),
				*PropertyPath, *CArrayNote, *VerifyFailure, *DefaultText, *StagedText, *AfterText));
			return;
		}
		Out->SetBoolField(TEXT("verified"), true);
		Out->SetField(TEXT("typed"), Res.bLeafIsElement
			? PropertyValueToTypedJsonElement(Leaf, LeafAddr, LeafOwner)
			: PropertyValueToTypedJson(Leaf, LeafAddr, LeafOwner));

		UE_LOG(LogMifBridge, Log, TEXT("reset_property_to_default: %s.%s -> %s (%s)"),
			*Target->GetName(), *PropertyPath, *AfterText, *DefaultSource);
	}

	// =============================================================================================
	// edit_container - TRANSACTED
	//   in:  { objectPath (actorPath), propertyPath (property),
	//          operation (action): add | insert | remove | clear | swap | resize | setKey,
	//          index (at)?, count?, key?, newKey?, value?, swapWith?, newSize?,
	//          overrideFlag (editCondition, override): set | refuse | ignore = "ignore" }
	//   out: { target, propertyPath, containerKind, operation, elementsBefore, elementsAfter,
	//          index?, rehashed, changed, verified, editConditionKind?, editConditionMet?,
	//          editConditionFlag?, overrideFlagWritten?, overrideFlagUnmet?, ... }
	//
	// GATES. CPF_EditFixedSize refuses the size-changing operations. A container whose meta
	// EditCondition is not met is the panel's other gate - it greys the container and its +/x buttons
	// together, and the engine reads the companion FLAG rather than the container, so an edit made
	// behind a closed gate changes memory and changes nothing else.
	//
	// `overrideFlag` is how that gate is answered, spelled exactly as set_property spells it
	// (set | refuse | ignore, aliases editCondition / override) so one vocabulary covers both write
	// endpoints. It DEFAULTS TO "ignore" here where set_property defaults to "set", and the asymmetry
	// is deliberate twice over: (1) back-compat - edit_container shipped performing the operation, so
	// today's behaviour has to remain the default and every new behaviour has to be opt-in; (2) intent
	// - set_property is handed a VALUE for the gated member, so "and turn the feature on" is a fair
	// reading, whereas "append one element to this array" is not consent to enable the feature that
	// owns the array. What changed is that "ignore" is no longer SILENT: the editCondition fields are
	// always reported and a closed gate always raises a warning saying the edit will not be read.
	// server.py does not expose `overrideFlag` on this tool yet, so "set" and "refuse" are unreachable
	// over MCP until it does - that is a REPORTED change, not one made from this file. Nothing is
	// broken meanwhile, precisely because the default is the shipped behaviour.
	//
	// NOTE ON THE PARAMETER NAME. The obvious name for the verb is `op` - and `op` is batch's own
	// routing key, which RejectUnknownParams tolerates centrally (MifBridgeHandlers.h). An endpoint
	// whose real parameter collides with the dispatcher's would be un-diagnosable inside batch, so
	// this one is `operation` (alias `action`) and `op` is refused BY NAME with that explanation.
	// =============================================================================================
	void H_edit_container(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			  TEXT("propertyPath"), TEXT("property"),
			  TEXT("operation"), TEXT("action"),
			  TEXT("index"), TEXT("at"), TEXT("count"),
			  TEXT("key"), TEXT("newKey"), TEXT("value"), TEXT("swapWith"), TEXT("newSize"),
			  TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override") },
			TEXT("objectPath (alias actorPath), propertyPath (alias property), operation (alias action) = add|insert|remove|clear|swap|resize|setKey, index (alias at), count, key, newKey, value, swapWith, newSize, overrideFlag (set|refuse|ignore)"),
			{{ TEXT("op"),
			   TEXT("this endpoint's verb is 'operation' (alias 'action'), NOT 'op' - 'op' is batch's routing key and is tolerated centrally, so an endpoint that used it would be un-diagnosable inside batch") }}))
		{
			return;
		}

		const FString PropertyPath = JStrAny(In, { TEXT("propertyPath"), TEXT("property") });
		if (PropertyPath.IsEmpty())
		{
			Fail(Out, TEXT("propertyPath required (alias property) - the CONTAINER, e.g. OverrideMaterials or Settings.WeightedBlendables.Array"));
			return;
		}
		FString Operation = JStrAny(In, { TEXT("operation"), TEXT("action") }).TrimStartAndEnd().ToLower();
		static const TCHAR* kOps = TEXT("add | insert | remove | clear | swap | resize | setKey");
		if (Operation.IsEmpty())
		{
			Fail(Out, FString::Printf(TEXT("operation required (alias action) - one of %s"), kOps));
			return;
		}
		if (Operation == TEXT("setkey")) { Operation = TEXT("setKey"); }
		if (Operation != TEXT("add") && Operation != TEXT("insert") && Operation != TEXT("remove")
			&& Operation != TEXT("clear") && Operation != TEXT("swap") && Operation != TEXT("resize")
			&& Operation != TEXT("setKey"))
		{
			// A string-to-enum dispatch must never have a silent default (PM-002).
			Fail(Out, FString::Printf(TEXT("operation '%s' is not one of %s. Nothing was changed."),
				*JStrAny(In, { TEXT("operation"), TEXT("action") }), kOps));
			return;
		}

		// The meta-EditCondition escape, spelled and validated exactly as set_property spells it
		// (MifBridgeNodes5.cpp:1000-1015): same three words, same aliases, same PM-002 rule that a
		// string-to-enum dispatch never has a silent default. The DEFAULT differs - "ignore" here,
		// "set" there - and the doc comment above says why.
		FString OverrideFlagMode = JStrAny(In, { TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override") }, TEXT("ignore"));
		OverrideFlagMode = OverrideFlagMode.TrimStartAndEnd().ToLower();
		if (OverrideFlagMode != TEXT("set") && OverrideFlagMode != TEXT("refuse") && OverrideFlagMode != TEXT("ignore"))
		{
			Fail(Out, FString::Printf(
				TEXT("overrideFlag '%s' is not one of set | refuse | ignore. 'ignore' (the default) performs the operation behind a ")
				TEXT("closed meta EditCondition and WARNS that the engine will not read it; 'set' writes the companion flag in the same ")
				TEXT("transaction and REPORTS it; 'refuse' fails naming the flag. Nothing was changed."),
				*JStrAny(In, { TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override") })));
			return;
		}

		UObject* Target = MifDetailsResolveWritableTarget(In, Out, TEXT("edit_container"));
		if (!Target) { return; }

		FPropertyPathResolution Res;
		FString Error;
		if (!ResolvePropertyPathEx(Target, PropertyPath, Res, Error)) { Fail(Out, Error); return; }
		FProperty* Leaf      = Res.Leaf;
		void*      LeafAddr  = Res.LeafAddr;
		UObject*   LeafOwner = Res.LeafOwner;

		FArrayProperty* AP = CastField<FArrayProperty>(Leaf);
		FSetProperty*   SP = CastField<FSetProperty>(Leaf);
		FMapProperty*   MP = CastField<FMapProperty>(Leaf);
		if (!AP && !SP && !MP)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s (%s), not a container; edit_container operates on TArray/TMap/TSet only.%s Nothing was changed."),
				*PropertyPath, *Leaf->GetClass()->GetName(), *Leaf->GetCPPType(),
				Leaf->ArrayDim > 1
					? TEXT(" It IS a fixed-size C-array UPROPERTY of ArrayDim elements, whose size is part of the C++ declaration and cannot be changed - address its elements with set_property 'Name[N]'.")
					: TEXT("")));
			return;
		}
		const TCHAR* ContainerKind = AP ? TEXT("array") : (SP ? TEXT("set") : TEXT("map"));

		// CPF_EditFixedSize is a FLAG (ObjectMacros.h:403) and survives a cook; the EditFixedSize
		// META string does not. Key the guard off the flag, always.
		if (Leaf->HasAnyPropertyFlags(CPF_EditFixedSize) && MifDetailsIsSizeChanging(Operation))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is CPF_EditFixedSize (UPROPERTY meta EditFixedSize): elements can be edited but the container cannot be ")
				TEXT("resized, so operation '%s' is refused. The Details panel hides its add/remove buttons for the same reason ")
				TEXT("(PropertyEditorHelpers.cpp:679). Use set_property on '%s[N]' to edit an element in place. Nothing was changed."),
				*PropertyPath, *Operation, *PropertyPath));
			return;
		}

		// EditCondition, the panel's other per-row gate, checked beside its sibling CPF_EditFixedSize
		// refusal above and well before the "VALIDATE EVERYTHING BEFORE THE FIRST MUTATION" block and
		// the first Modify() - a refusal must leave nothing behind, and order is the only mechanism
		// (PM-007).
		//
		// THREE ANSWERS, and the caller picks. The previous revision of this block refused
		// UNCONDITIONALLY, which was a breaking change on a shipped write endpoint: edit_container has
		// always performed the operation regardless of this gate, and a caller editing a gated
		// container suddenly got a hard failure with no way through from inside the endpoint. The rule
		// that settles it is not a preference - new behaviour is opt-in and defaults to today's
		// behaviour - so the refusal became `overrideFlag:"refuse"` and the DEFAULT is "ignore", which
		// is what this endpoint has always done.
		//
		// What is NOT preserved is the silence. The old behaviour performed the edit and said nothing
		// about the gate, so a caller could not tell an edit the engine reads from one it does not.
		// "ignore" now always reports editConditionKind / editConditionMet / editConditionFlag and
		// raises a warning naming the flag. The defect this lane was opened for was the SILENT no-op,
		// and that is fixed without breaking anyone.
		//
		//   ignore (default) - perform the operation, report the gate, warn that the engine reads the
		//                      FLAG and will not read this container until the flag is set.
		//   set              - write the companion flag in the same transaction and the same
		//                      Modify/PreEditChange..PostEditChange bracket the operation uses, then
		//                      report it, exactly as set_property's overrideFlag:"set" does
		//                      (MifBridgeNodes5.cpp:1240-1245). Written only AFTER the operation has
		//                      actually mutated, never at this point: everything between here and the
		//                      mutation can still refuse (index range, duplicate key, value parse), and
		//                      PM-007 says a cancelled transaction reverts nothing at all, so a flag
		//                      written here would survive a refusal that claims "nothing was changed".
		//   refuse           - fail, naming the flag and the value it needs. This is the strict
		//                      behaviour, now opt-in.
		//
		// `overrideFlag` rather than `force`: reset_property_to_default's `force` means "waive
		// CPF_EditConst", one refusal and nothing else, while "set" writes a second property. One word
		// cannot mean both, and inventing a third spelling for the same idea is how a vocabulary rots -
		// so reset_property_to_default answers THIS gate with THIS key too (:1163-1243), differing only
		// in that it refuses "set": a reset must never turn a feature on. server.py does not pass this parameter
		// yet, so over MCP the default is the only reachable mode today - which is the shipped
		// behaviour, so nothing is broken while that catches up. REPORTED, not edited from here.
		//
		// Leaf here is always the container MEMBER (anything else was refused above), so it is a
		// declared member and Res.LeafContainerAddr is non-null except when the container is itself an
		// element of another dynamic container - which InspectEditCondition reports as unevaluated
		// (MifBridgeCommon.cpp:2351-2359). Hence bEvaluated && !bMet, never !bMet alone
		// (MifBridgeHandlers.h:491): an unparseable expression defaults bMet to TRUE and must pass
		// through ungated rather than be refused on a guess.
		FEditConditionInfo EC;
		InspectEditCondition(Leaf, Res.LeafContainerAddr, EC);
		const bool bGateClosed = EC.bEvaluated && !EC.bMet;
		if (bGateClosed && OverrideFlagMode == TEXT("refuse"))
		{
			Out->SetStringField(TEXT("propertyPath"), PropertyPath);
			Out->SetStringField(TEXT("containerKind"), ContainerKind);
			Out->SetStringField(TEXT("operation"), Operation);
			Out->SetStringField(TEXT("editCondition"), EC.MetaText);
			Out->SetStringField(TEXT("editConditionKind"), EC.Kind);
			Out->SetStringField(TEXT("editConditionFlag"), EC.FlagName);
			Out->SetBoolField(TEXT("editConditionMet"), false);
			Out->SetBoolField(TEXT("nothingModified"), true);
			Fail(Out, FString::Printf(
				TEXT("'%s' is gated by meta EditCondition=\"%s\" and the companion flag '%s' is currently %s, so the Details panel greys ")
				TEXT("the container and its +/x buttons. The engine reads the FLAG, not this container, so operation '%s' would change ")
				TEXT("memory and change nothing else. You asked for overrideFlag:\"refuse\". Pass overrideFlag:\"set\" to write '%s' = %s in ")
				TEXT("the same transaction and then perform the operation, overrideFlag:\"ignore\" (the default) to perform it behind the ")
				TEXT("closed gate on purpose, or set '%s' yourself with set_property first. Nothing was changed."),
				*PropertyPath, *EC.MetaText, *EC.FlagName, EC.bRequiredFlagValue ? TEXT("False") : TEXT("True"),
				*Operation, *EC.FlagName, EC.bRequiredFlagValue ? TEXT("True") : TEXT("False"), *EC.FlagName));
			return;
		}
		// Always stated from here on, whichever mode is in force and whether or not the gate is closed -
		// the silence is the half of the old behaviour that was genuinely wrong.
		if (EC.bHasMeta)            { Out->SetStringField(TEXT("editCondition"), EC.MetaText); }
		Out->SetStringField(TEXT("editConditionKind"), EC.Kind);
		if (EC.bEvaluated)          { Out->SetBoolField(TEXT("editConditionMet"), EC.bMet); }
		if (!EC.FlagName.IsEmpty()) { Out->SetStringField(TEXT("editConditionFlag"), EC.FlagName); }
		// "set" needs a resolved FBoolProperty AND the address of the struct/object that declares it.
		// Without either there is nothing to write, and that is reported at the end rather than
		// silently downgraded to "ignore".
		const bool bSetFlagAfterMutation = bGateClosed && OverrideFlagMode == TEXT("set")
			&& EC.FlagProp != nullptr && Res.LeafContainerAddr != nullptr;

		FProperty* ElementProp = AP ? AP->Inner : (SP ? SP->ElementProp : MP->ValueProp);
		FProperty* KeyProp     = MP ? MP->KeyProp : nullptr;

		// Hashability, checked BY NAME rather than crashed on: FScriptMapHelper::AddPair passes
		// KeyProp->GetValueTypeHash to Map->Add (UnrealType.h:4910).
		if ((SP || MP) && (Operation == TEXT("add") || Operation == TEXT("setKey")))
		{
			const FProperty* Hashed = SP ? SP->ElementProp : MP->KeyProp;
			if (!Hashed->HasAnyPropertyFlags(CPF_HasGetValueTypeHash))
			{
				Fail(Out, FString::Printf(
					TEXT("the %s element/key type %s has no GetTypeHash, so UE cannot add to this container through reflection ")
					TEXT("(CPF_HasGetValueTypeHash is unset). Reading it works; adding does not. Nothing was changed."),
					ContainerKind, *Hashed->GetCPPType()));
				return;
			}
		}

		// --- counts BEFORE, so every claim below is checkable ---------------------------
		auto CountNow = [&]() -> int32
		{
			if (AP) { return FScriptArrayHelper(AP, LeafAddr).Num(); }
			if (SP) { return FScriptSetHelper(SP, LeafAddr).Num(); }
			return FScriptMapHelper(MP, LeafAddr).Num();
		};
		const int32 Before = CountNow();

		const bool  bHasIndex = JHasAny(In, { TEXT("index"), TEXT("at") });
		const int32 Index     = JIntAny(In, { TEXT("index"), TEXT("at") }, INDEX_NONE);
		const int32 Count     = FMath::Max(JInt(In, TEXT("count"), 1), 1);
		const FString KeyText    = JStr(In, TEXT("key"));
		const FString NewKeyText = JStr(In, TEXT("newKey"));
		const TSharedPtr<FJsonValue> ValueJson = In->TryGetField(TEXT("value"));

		// An IGNORED parameter is worse than a rejected one: RejectUnknownParams only proves a key is
		// SPELLED right, not that THIS operation can act on it. `count` on a swap, `newSize` on an add
		// and `key` on an array would all have been silently dropped, which is the defect class this
		// codebase keeps paying for (01_POSTMORTEMS.md, spawn_actor_in_level's dropped `mesh`).
		{
			struct FMifOpParam { const TCHAR* Key; const TCHAR* Alias; const TCHAR* UsedBy; };
			static const FMifOpParam OpParams[] = {
				{ TEXT("count"),    nullptr,        TEXT("remove (arrays)") },
				{ TEXT("swapWith"), nullptr,        TEXT("swap (arrays)") },
				{ TEXT("newSize"),  nullptr,        TEXT("resize (arrays)") },
				{ TEXT("newKey"),   nullptr,        TEXT("setKey (maps)") },
				{ TEXT("index"),    TEXT("at"),     TEXT("insert / remove / swap") },
				{ TEXT("key"),      nullptr,        TEXT("add / remove / setKey on a MAP") },
				{ TEXT("value"),    nullptr,        TEXT("add / insert") },
			};
			auto OperationUses = [&](const FString& Key) -> bool
			{
				if (Key == TEXT("count"))    { return Operation == TEXT("remove") && AP != nullptr; }
				if (Key == TEXT("swapWith")) { return Operation == TEXT("swap"); }
				if (Key == TEXT("newSize"))  { return Operation == TEXT("resize"); }
				if (Key == TEXT("newKey"))   { return Operation == TEXT("setKey"); }
				if (Key == TEXT("index"))    { return Operation == TEXT("insert") || Operation == TEXT("remove") || Operation == TEXT("swap"); }
				if (Key == TEXT("key"))      { return MP != nullptr && (Operation == TEXT("add") || Operation == TEXT("remove") || Operation == TEXT("setKey")); }
				if (Key == TEXT("value"))    { return Operation == TEXT("add") || Operation == TEXT("insert"); }
				return true;
			};
			for (const FMifOpParam& P : OpParams)
			{
				const bool bSupplied = P.Alias ? JHasAny(In, { P.Key, P.Alias }) : In->HasField(P.Key);
				if (bSupplied && !OperationUses(P.Key))
				{
					Fail(Out, FString::Printf(
						TEXT("'%s' was supplied but operation '%s' on a %s does not use it (it is for %s). ")
						TEXT("Rather than drop it silently, this call is refused. Nothing was changed."),
						P.Key, *Operation, ContainerKind, P.UsedBy));
					return;
				}
			}
		}

		Out->SetStringField(TEXT("propertyPath"), PropertyPath);
		Out->SetStringField(TEXT("containerKind"), ContainerKind);
		Out->SetStringField(TEXT("operation"), Operation);
		Out->SetNumberField(TEXT("elementsBefore"), Before);
		MifDetailsEmitTargetKind(Target, Out);

		// --- VALIDATE EVERYTHING BEFORE THE FIRST MUTATION ------------------------------
		// PM-007: a cancelled transaction reverts nothing at all, so ORDER is the only mechanism that
		// makes a failed call leave nothing behind. Every range check, every duplicate check and every
		// value parse happens here, before Modify() is called.
		FString ValueImportText, ValueForm, ValueTypeNote, ConvError;
		bool bValueTypeValidated = false;
		const bool bWantsValue = ValueJson.IsValid();
		if (bWantsValue)
		{
			if (!PropertyImportTextFromJson(ValueJson, ElementProp, LeafOwner,
				FString::Printf(TEXT("%s.value"), *PropertyPath),
				ValueImportText, ValueForm, bValueTypeValidated, ValueTypeNote, ConvError))
			{
				Out->SetBoolField(TEXT("nothingModified"), true);
				Fail(Out, ConvError);
				return;
			}
		}
		FString KeyImportText, KeyForm, KeyTypeNote, KeyConvError;
		bool bKeyTypeValidated = false;
		if (MP && (Operation == TEXT("add") || Operation == TEXT("remove") || Operation == TEXT("setKey")))
		{
			if (KeyText.IsEmpty() && !In->HasField(TEXT("key")))
			{
				Fail(Out, FString::Printf(TEXT("operation '%s' on a TMap<%s,%s> requires 'key'. Nothing was changed."),
					*Operation, *MP->KeyProp->GetCPPType(), *MP->ValueProp->GetCPPType()));
				return;
			}
			// Only 'add' actually IMPORTS the key into the map. 'remove' and 'setKey' LOOK IT UP by
			// exported text, using the same matcher the '{Key}' path accessor uses - type-checking
			// there could refuse a key the matcher would have found, which is a worse answer than not
			// checking.
			if (Operation == TEXT("add")
				&& !PropertyImportTextFromJson(MakeShared<FJsonValueString>(KeyText), KeyProp, LeafOwner,
					FString::Printf(TEXT("%s.key"), *PropertyPath),
					KeyImportText, KeyForm, bKeyTypeValidated, KeyTypeNote, KeyConvError))
			{
				Out->SetBoolField(TEXT("nothingModified"), true);
				Fail(Out, KeyConvError);
				return;
			}
		}

		int32 ResultIndex = INDEX_NONE;
		bool  bRehashed = false;
		bool  bDidMutate = false;

		// ---------------------------------------------------------------------------- ARRAY
		if (AP)
		{
			if (Operation == TEXT("insert") || Operation == TEXT("remove") || Operation == TEXT("swap"))
			{
				if (!bHasIndex)
				{
					Fail(Out, FString::Printf(TEXT("operation '%s' on an array requires 'index' (alias 'at'). The array has %d element%s. Nothing was changed."),
						*Operation, Before, Before == 1 ? TEXT("") : TEXT("s")));
					return;
				}
				const int32 MaxValid = (Operation == TEXT("insert")) ? Before : Before - 1;
				if (Index < 0 || Index > MaxValid)
				{
					Fail(Out, FString::Printf(
						TEXT("'%s[%d]': index %d is out of range - the array has %d element%s (valid %s). Nothing was changed."),
						*PropertyPath, Index, Index, Before, Before == 1 ? TEXT("") : TEXT("s"),
						MaxValid < 0 ? TEXT("none: the array is empty") : *FString::Printf(TEXT("0..%d"), MaxValid)));
					return;
				}
			}
			if (Operation == TEXT("swap"))
			{
				if (!In->HasField(TEXT("swapWith")))
				{
					Fail(Out, TEXT("operation 'swap' requires 'swapWith' (the other index). Nothing was changed."));
					return;
				}
				const int32 Other = JInt(In, TEXT("swapWith"), INDEX_NONE);
				if (Other < 0 || Other >= Before)
				{
					Fail(Out, FString::Printf(TEXT("swapWith %d is out of range - the array has %d element%s (valid 0..%d). Nothing was changed."),
						Other, Before, Before == 1 ? TEXT("") : TEXT("s"), Before - 1));
					return;
				}
				// SWAPPING AN ELEMENT WITH ITSELF IS A NO-OP, AND SAYING SO IS THE POINT. Both range
				// checks above accept index == swapWith, SwapValues(3,3) does nothing, and `changed`
				// below is hardcoded true for swap because a structural op cannot be verified by
				// COUNTING - the count is identical either way. So this reported changed:true for a
				// call that changed nothing, and dirtied the package doing it: Modify(),
				// PreEditChange and a PostEditChange carrying ArrayMove all fire for the no-op.
				//
				// Reported rather than refused, matching set_variable_type, which answers a same-type
				// request with changed:false and a note instead of failing. A caller that computed
				// two indices which happened to coincide has not made an error worth stopping for -
				// they just need to be told nothing moved.
				if (Index == Other)
				{
					Out->SetNumberField(TEXT("swapWith"), Other);
					Out->SetNumberField(TEXT("elementsAfter"), Before);
					Out->SetNumberField(TEXT("index"), Index);
					Out->SetBoolField(TEXT("rehashed"), false);
					Out->SetBoolField(TEXT("changed"), false);
					Out->SetStringField(TEXT("note"), FString::Printf(
						TEXT("index and swapWith are both %d, so nothing moved. The array is untouched and the ")
						TEXT("package was not dirtied."), Index));
					return;
				}

				LeafOwner->Modify();
				LeafOwner->PreEditChange(Leaf);
				FScriptArrayHelper Helper(AP, LeafAddr);
				Helper.SwapValues(Index, Other);
				ResultIndex = Index;
				bDidMutate = true;
				Out->SetNumberField(TEXT("swapWith"), Other);
			}
			else if (Operation == TEXT("resize"))
			{
				if (!In->HasField(TEXT("newSize")))
				{
					Fail(Out, TEXT("operation 'resize' requires 'newSize'. Nothing was changed."));
					return;
				}
				const int32 NewSize = JInt(In, TEXT("newSize"), -1);
				if (NewSize < 0)
				{
					Fail(Out, FString::Printf(TEXT("newSize must be >= 0 (got %d). Nothing was changed."), NewSize));
					return;
				}
				LeafOwner->Modify();
				LeafOwner->PreEditChange(Leaf);
				FScriptArrayHelper(AP, LeafAddr).Resize(NewSize);
				bDidMutate = true;
			}
			else if (Operation == TEXT("clear"))
			{
				LeafOwner->Modify();
				LeafOwner->PreEditChange(Leaf);
				FScriptArrayHelper(AP, LeafAddr).EmptyValues();
				bDidMutate = true;
			}
			else if (Operation == TEXT("remove"))
			{
				if (Index + Count > Before)
				{
					Fail(Out, FString::Printf(TEXT("removing %d element%s from index %d would run past the end - the array has %d. Nothing was changed."),
						Count, Count == 1 ? TEXT("") : TEXT("s"), Index, Before));
					return;
				}
				LeafOwner->Modify();
				LeafOwner->PreEditChange(Leaf);
				FScriptArrayHelper(AP, LeafAddr).RemoveValues(Index, Count);
				ResultIndex = Index;
				bDidMutate = true;
			}
			else if (Operation == TEXT("add") || Operation == TEXT("insert"))
			{
				// PARSE FIRST, into a staging buffer, and only then open the notification bracket.
				// Two reasons, and either one is sufficient. (1) PM-007: a cancelled transaction
				// reverts nothing, so a call that creates an element and THEN discovers the value is
				// garbage has to undo its own creation - and not creating it is strictly better.
				// (2) Calling PreEditChange and then returning early would leave
				// UActorComponent::PreEditChange's FComponentReregisterContext un-consumed
				// (ActorComponent.cpp:806-822 is matched only by ConsolidatedPostEditChange at
				// :927-941) - a dangling registration on a live component.
				void* Staging = nullptr;
				FString Staged, ImportError;
				if (bWantsValue)
				{
					Staging = FMemory::Malloc(FMath::Max(AP->Inner->GetSize(), 1), AP->Inner->GetMinAlignment());
					AP->Inner->InitializeValue(Staging);
					if (!ImportPropertyTextSafely(AP->Inner, ValueImportText, nullptr, Staging, LeafOwner, Staged, ImportError))
					{
						AP->Inner->DestroyValue(Staging);
						FMemory::Free(Staging);
						Out->SetBoolField(TEXT("nothingModified"), true);
						Out->SetNumberField(TEXT("elementsAfter"), Before);
						Fail(Out, FString::Printf(TEXT("%s No element was added, so the array is exactly as it was."), *ImportError));
						return;
					}
				}
				LeafOwner->Modify();
				LeafOwner->PreEditChange(Leaf);
				FScriptArrayHelper Helper(AP, LeafAddr);
				if (Operation == TEXT("add")) { ResultIndex = Helper.AddValue(); }
				else                          { Helper.InsertValues(Index, 1); ResultIndex = Index; }
				if (Staging)
				{
					// RE-RESOLVE after the structural op: AddValues/InsertValues reallocate, so any
					// pointer taken before this line is dangling (UnrealType.h:4099-4110).
					FScriptArrayHelper After(AP, LeafAddr);
					AP->Inner->CopySingleValue(After.GetElementPtr(ResultIndex), Staging);
					AP->Inner->DestroyValue(Staging);
					FMemory::Free(Staging);
					Out->SetStringField(TEXT("valueWritten"), Staged);
				}
				bDidMutate = true;
			}
			else
			{
				Fail(Out, FString::Printf(TEXT("operation '%s' does not apply to a TArray (setKey is TMap-only). Nothing was changed."), *Operation));
				return;
			}
		}
		// ------------------------------------------------------------------------------ SET
		else if (SP)
		{
			if (Operation == TEXT("clear"))
			{
				LeafOwner->Modify();
				LeafOwner->PreEditChange(Leaf);
				FScriptSetHelper(SP, LeafAddr).EmptyElements();
				bDidMutate = true;
			}
			else if (Operation == TEXT("remove"))
			{
				if (!bHasIndex)
				{
					Fail(Out, FString::Printf(TEXT("operation 'remove' on a TSet requires 'index' - the POSITION in iteration order, which get_property/describe_property report. The set has %d element%s. Nothing was changed."),
						Before, Before == 1 ? TEXT("") : TEXT("s")));
					return;
				}
				if (Index < 0 || Index >= Before)
				{
					Fail(Out, FString::Printf(TEXT("'%s[%d]': index %d is out of range - the set has %d element%s%s. Nothing was changed."),
						*PropertyPath, Index, Index, Before, Before == 1 ? TEXT("") : TEXT("s"),
						Before > 0 ? *FString::Printf(TEXT(" (valid 0..%d)"), Before - 1) : TEXT("")));
					return;
				}
				LeafOwner->Modify();
				LeafOwner->PreEditChange(Leaf);
				FScriptSetHelper Helper(SP, LeafAddr);
				Helper.RemoveAt(Helper.FindInternalIndex(Index), 1);
				Helper.Rehash();
				bRehashed = true;
				ResultIndex = Index;
				bDidMutate = true;
			}
			else if (Operation == TEXT("add"))
			{
				if (!bWantsValue)
				{
					Fail(Out, FString::Printf(TEXT("operation 'add' on a TSet<%s> requires 'value' - a set element IS its value, so there is no default to add. Nothing was changed."),
						*SP->ElementProp->GetCPPType()));
					return;
				}
				// Parse into a scratch and check for a duplicate BEFORE touching the set: AddElement
				// silently swallows a duplicate, and the panel refuses it outright
				// (PropertyHandleImpl.cpp:389).
				void* Scratch = FMemory::Malloc(FMath::Max(SP->ElementProp->GetSize(), 1), SP->ElementProp->GetMinAlignment());
				SP->ElementProp->InitializeValue(Scratch);
				FString Staged, ImportError;
				const bool bParsed = ImportPropertyTextSafely(SP->ElementProp, ValueImportText, nullptr, Scratch, LeafOwner, Staged, ImportError);
				bool bDuplicate = false;
				if (bParsed)
				{
					bDuplicate = FScriptSetHelper(SP, LeafAddr).FindElementIndex(Scratch) != INDEX_NONE;
				}
				if (bParsed && !bDuplicate)
				{
					LeafOwner->Modify();
					LeafOwner->PreEditChange(Leaf);
					FScriptSetHelper Helper(SP, LeafAddr);
					Helper.AddElement(Scratch);   // hashes as it inserts
					// Deliberately NO index in the response: a set's iteration order is not a position
					// the caller can rely on, and reporting Num()-1 would look like one.
					bDidMutate = true;
				}
				SP->ElementProp->DestroyValue(Scratch);
				FMemory::Free(Scratch);
				if (!bParsed) { Out->SetBoolField(TEXT("nothingModified"), true); Fail(Out, ImportError); return; }
				if (bDuplicate)
				{
					Out->SetBoolField(TEXT("nothingModified"), true);
					Fail(Out, FString::Printf(
						TEXT("'%s' already contains an element equal to %s. TSet rejects duplicates and the Details panel refuses the ")
						TEXT("same edit (PropertyHandleImpl.cpp:389); AddElement would have swallowed it silently. Nothing was changed."),
						*PropertyPath, *Staged));
					return;
				}
				Out->SetStringField(TEXT("valueWritten"), Staged);
			}
			else
			{
				Fail(Out, FString::Printf(TEXT("operation '%s' does not apply to a TSet (insert/swap/resize are array-only, setKey is map-only; a set has no positions to insert at). Nothing was changed."), *Operation));
				return;
			}
		}
		// ------------------------------------------------------------------------------ MAP
		else
		{
			if (Operation == TEXT("clear"))
			{
				LeafOwner->Modify();
				LeafOwner->PreEditChange(Leaf);
				FScriptMapHelper(MP, LeafAddr).EmptyValues();
				bDidMutate = true;
			}
			else if (Operation == TEXT("remove"))
			{
				const int32 Found = FindMapEntryByKeyText(MP, LeafAddr, KeyText, LeafOwner);
				if (Found == INDEX_NONE)
				{
					Fail(Out, FString::Printf(TEXT("no entry with key '%s' in '%s' (%d entries). Existing keys: %s. Nothing was changed."),
						*KeyText, *PropertyPath, Before, *SampleMapKeyText(MP, LeafAddr, LeafOwner, 12)));
					return;
				}
				LeafOwner->Modify();
				LeafOwner->PreEditChange(Leaf);
				FScriptMapHelper Helper(MP, LeafAddr);
				Helper.RemoveAt(Found, 1);
				Helper.Rehash();
				bRehashed = true;
				bDidMutate = true;
			}
			else if (Operation == TEXT("add"))
			{
				if (FindMapEntryByKeyText(MP, LeafAddr, KeyText, LeafOwner) != INDEX_NONE)
				{
					// AddPair OVERWRITES silently, so without this check "add" quietly becomes
					// "replace" - the panel refuses instead (PropertyHandleImpl.cpp:446).
					Fail(Out, FString::Printf(
						TEXT("key '%s' already exists in '%s'. Maps reject duplicate keys, and FScriptMapHelper::AddPair would have ")
						TEXT("OVERWRITTEN the existing value without saying so. Use set_property {propertyPath:\"%s{%s}\"} to change its ")
						TEXT("value, or operation:\"setKey\" to rename it. Nothing was changed."),
						*KeyText, *PropertyPath, *PropertyPath, *KeyText));
					return;
				}
				void* KeyScratch = FMemory::Malloc(FMath::Max(KeyProp->GetSize(), 1), KeyProp->GetMinAlignment());
				void* ValScratch = FMemory::Malloc(FMath::Max(MP->ValueProp->GetSize(), 1), MP->ValueProp->GetMinAlignment());
				KeyProp->InitializeValue(KeyScratch);
				MP->ValueProp->InitializeValue(ValScratch);
				FString KeyStaged, ValStaged, ImportError;
				bool bOk = ImportPropertyTextSafely(KeyProp, KeyImportText, nullptr, KeyScratch, LeafOwner, KeyStaged, ImportError);
				if (bOk && bWantsValue)
				{
					bOk = ImportPropertyTextSafely(MP->ValueProp, ValueImportText, nullptr, ValScratch, LeafOwner, ValStaged, ImportError);
				}
				if (bOk)
				{
					LeafOwner->Modify();
					LeafOwner->PreEditChange(Leaf);
					FScriptMapHelper Helper(MP, LeafAddr);
					Helper.AddPair(KeyScratch, ValScratch);
					Helper.Rehash();
					bRehashed = true;
					bDidMutate = true;
				}
				KeyProp->DestroyValue(KeyScratch);       FMemory::Free(KeyScratch);
				MP->ValueProp->DestroyValue(ValScratch); FMemory::Free(ValScratch);
				if (!bOk) { Out->SetBoolField(TEXT("nothingModified"), true); Fail(Out, ImportError); return; }
				Out->SetStringField(TEXT("keyWritten"), KeyStaged);
				if (bWantsValue) { Out->SetStringField(TEXT("valueWritten"), ValStaged); }
			}
			else if (Operation == TEXT("setKey"))
			{
				if (NewKeyText.IsEmpty() && !In->HasField(TEXT("newKey")))
				{
					Fail(Out, TEXT("operation 'setKey' requires both 'key' (the entry to rename) and 'newKey'. Nothing was changed."));
					return;
				}
				const int32 Found = FindMapEntryByKeyText(MP, LeafAddr, KeyText, LeafOwner);
				if (Found == INDEX_NONE)
				{
					Fail(Out, FString::Printf(TEXT("no entry with key '%s' in '%s'. Existing keys: %s. Nothing was changed."),
						*KeyText, *PropertyPath, *SampleMapKeyText(MP, LeafAddr, LeafOwner, 12)));
					return;
				}
				if (FindMapEntryByKeyText(MP, LeafAddr, NewKeyText, LeafOwner) != INDEX_NONE)
				{
					Fail(Out, FString::Printf(TEXT("key '%s' already exists in '%s'; renaming '%s' onto it would destroy an entry. Nothing was changed."),
						*NewKeyText, *PropertyPath, *KeyText));
					return;
				}
				FString NewKeyImport, NewKeyForm, NewKeyNote, NewKeyError;
				bool bNewKeyValidated = false;
				if (!PropertyImportTextFromJson(MakeShared<FJsonValueString>(NewKeyText), KeyProp, LeafOwner,
					FString::Printf(TEXT("%s.newKey"), *PropertyPath), NewKeyImport, NewKeyForm, bNewKeyValidated, NewKeyNote, NewKeyError))
				{
					Out->SetBoolField(TEXT("nothingModified"), true);
					Fail(Out, NewKeyError);
					return;
				}
				// Copy the key AND the value out first: RemovePair invalidates both addresses.
				void* NewKeyScratch = FMemory::Malloc(FMath::Max(KeyProp->GetSize(), 1), KeyProp->GetMinAlignment());
				void* ValScratch    = FMemory::Malloc(FMath::Max(MP->ValueProp->GetSize(), 1), MP->ValueProp->GetMinAlignment());
				KeyProp->InitializeValue(NewKeyScratch);
				MP->ValueProp->InitializeValue(ValScratch);
				FString NewKeyStaged, ImportError;
				bool bOk = ImportPropertyTextSafely(KeyProp, NewKeyImport, nullptr, NewKeyScratch, LeafOwner, NewKeyStaged, ImportError);
				if (bOk)
				{
					FScriptMapHelper Helper(MP, LeafAddr);
					MP->ValueProp->CopySingleValue(ValScratch, Helper.GetValuePtr(Found));
					LeafOwner->Modify();
					LeafOwner->PreEditChange(Leaf);
					Helper.RemoveAt(Found, 1);
					Helper.AddPair(NewKeyScratch, ValScratch);
					Helper.Rehash();
					bRehashed = true;
					bDidMutate = true;
				}
				KeyProp->DestroyValue(NewKeyScratch);    FMemory::Free(NewKeyScratch);
				MP->ValueProp->DestroyValue(ValScratch); FMemory::Free(ValScratch);
				if (!bOk) { Out->SetBoolField(TEXT("nothingModified"), true); Fail(Out, ImportError); return; }
				Out->SetStringField(TEXT("keyWritten"), NewKeyStaged);
			}
			else
			{
				Fail(Out, FString::Printf(TEXT("operation '%s' does not apply to a TMap (insert/swap/resize are array-only). Use add/remove/clear/setKey. Nothing was changed."), *Operation));
				return;
			}
		}

		if (!bDidMutate)
		{
			Fail(Out, FString::Printf(TEXT("edit_container '%s' on '%s' reached the end without mutating anything. Nothing was changed."),
				*Operation, *PropertyPath));
			return;
		}

		// --- overrideFlag:"set" - the companion EditCondition flag -----------------------
		// HERE, and not at the gate. Every branch above calls Modify() and PreEditChange() only once
		// its own last possible refusal has passed, so this point is the first at which the operation
		// is known to have happened: bDidMutate is true, the notification bracket is open, and the
		// transaction is already dirty. Writing the flag at the gate instead would leave it set behind
		// any of the later refusals - index out of range, duplicate key, a value that would not parse -
		// each of which promises "Nothing was changed", and PM-007 is explicit that a cancelled
		// transaction reverts nothing at all.
		//
		// Inside the bracket and firing NO notification of its own, for set_property's reason
		// (MifBridgeNodes5.cpp:1236-1245): a second PostEditChange on a placed actor's component reruns
		// the construction scripts mid-write and leaves LeafAddr dangling before the count below is
		// read back. The container's own PostEditChangeProperty announces the object once, for both.
		//
		// LeafContainerAddr is the base of the struct/object DECLARING the container, which a container
		// operation does not move - only the container's own element storage is reallocated.
		bool bFlagWritten = false, bFlagBefore = false, bFlagAfter = false;
		if (bSetFlagAfterMutation)
		{
			void* FlagAddr = EC.FlagProp->ContainerPtrToValuePtr<void>(Res.LeafContainerAddr);
			bFlagBefore = EC.FlagProp->GetPropertyValue(FlagAddr);
			EC.FlagProp->SetPropertyValue(FlagAddr, EC.bRequiredFlagValue);
			bFlagAfter  = EC.FlagProp->GetPropertyValue(FlagAddr);   // MEASURED, not echoed
			bFlagWritten = true;
		}

		// --- notify, with the ARRAY change type the panel would use ---------------------
		FPropertyChangedEvent Evt(Leaf,
			Operation == TEXT("add") || Operation == TEXT("insert") ? EPropertyChangeType::ArrayAdd :
			Operation == TEXT("remove")                             ? EPropertyChangeType::ArrayRemove :
			Operation == TEXT("clear")                              ? EPropertyChangeType::ArrayClear :
			Operation == TEXT("swap")                               ? EPropertyChangeType::ArrayMove :
			                                                          EPropertyChangeType::ValueSet);
		LeafOwner->PostEditChangeProperty(Evt);
		LeafOwner->MarkPackageDirty();

		// --- what happened to the gate, said out loud ------------------------------------
		// Emitted BEFORE the verification block below, so it survives on that block's failure path too:
		// a caller told "the count did not change" still needs to know the container it edited is one
		// the engine is not reading.
		if (bFlagWritten)
		{
			TSharedRef<FJsonObject> FlagJson = MakeShared<FJsonObject>();
			FlagJson->SetStringField(TEXT("name"), EC.FlagName);
			FlagJson->SetBoolField(TEXT("valueBefore"), bFlagBefore);
			FlagJson->SetBoolField(TEXT("valueAfter"), bFlagAfter);
			Out->SetObjectField(TEXT("overrideFlagWritten"), FlagJson);
			AddWarning(Out, FString::Printf(
				TEXT("'%s' is gated by meta EditCondition=\"%s\"; you passed overrideFlag:\"set\", so the companion flag '%s' was %s and has ")
				TEXT("been SET to %s in the SAME transaction as operation '%s' - one Ctrl-Z undoes both. This ENABLED the feature that owns ")
				TEXT("the container, which is a change to a property other than the one you addressed. Pass overrideFlag:\"ignore\" (the ")
				TEXT("default) if you wanted only the container touched."),
				*PropertyPath, *EC.MetaText, *EC.FlagName,
				bFlagBefore ? TEXT("True") : TEXT("False"),
				EC.bRequiredFlagValue ? TEXT("True") : TEXT("False"), *Operation));
		}
		else if (bGateClosed)
		{
			// Covers "ignore" AND a "set" that had nothing to write (no resolved FBoolProperty, or the
			// container is itself an element of a dynamic container so there is no declaring container
			// to find the sibling in - MifBridgeCommon.cpp:2351-2359). Downgrading either to silence is
			// what made this endpoint dishonest in the first place.
			Out->SetBoolField(TEXT("overrideFlagUnmet"), true);
			const FString CouldNotWrite = (OverrideFlagMode == TEXT("set"))
				? FString::Printf(TEXT("You asked for overrideFlag:\"set\", but the flag could not be written: %s. "),
					!EC.FlagProp ? TEXT("the companion property could not be resolved as a bool")
					             : TEXT("the container has no declaring container address to find the sibling flag in"))
				: FString();
			AddWarning(Out, FString::Printf(
				TEXT("EDITED BUT NOT READ BY THE ENGINE: '%s' is gated by meta EditCondition=\"%s\" and the flag '%s' is still %s, so the ")
				TEXT("Details panel greys this container and its +/x buttons and the engine reads the FLAG rather than the container. ")
				TEXT("Operation '%s' DID change memory and changed nothing else. %sSet '%s' to %s - with set_property, or by passing ")
				TEXT("overrideFlag:\"set\" here - to make this edit take effect; pass overrideFlag:\"refuse\" if you would rather be ")
				TEXT("stopped than warned."),
				*PropertyPath, *EC.MetaText, *EC.FlagName, EC.bRequiredFlagValue ? TEXT("False") : TEXT("True"),
				*Operation, *CouldNotWrite, *EC.FlagName, EC.bRequiredFlagValue ? TEXT("True") : TEXT("False")));
		}
		else if (EC.bHasMeta && !EC.bEvaluated && !EC.Note.IsEmpty())
		{
			// A gate this bridge could not evaluate passes through ungated rather than being refused on
			// a guess (bEvaluated, never bMet alone - MifBridgeHandlers.h:491). Saying so is the whole
			// difference between that and degrading silently.
			AddWarning(Out, FString::Printf(
				TEXT("'%s': %s The operation WAS performed; verify by hand that the condition holds."), *PropertyPath, *EC.Note));
		}

		// --- verify by COUNTING, which is the only claim a structural op can make -------
		const int32 After = CountNow();
		Out->SetNumberField(TEXT("elementsAfter"), After);
		if (ResultIndex != INDEX_NONE) { Out->SetNumberField(TEXT("index"), ResultIndex); }
		Out->SetBoolField(TEXT("rehashed"), bRehashed);
		Out->SetBoolField(TEXT("changed"), After != Before || Operation == TEXT("swap") || Operation == TEXT("setKey"));

		const bool bExpectSameCount = (Operation == TEXT("swap") || Operation == TEXT("setKey"))
			|| (Operation == TEXT("clear") && Before == 0)
			|| (Operation == TEXT("resize") && JInt(In, TEXT("newSize"), Before) == Before);
		if (!bExpectSameCount && After == Before)
		{
			// A structural op that left the count alone is a FAILURE, not a success - the same rule as
			// set_property's "the import said success and the readback says otherwise" guard.
			Out->SetBoolField(TEXT("verified"), false);
			Fail(Out, FString::Printf(
				TEXT("edit_container '%s' on '%s' reported success but the element count is unchanged (%d before, %d after). ")
				TEXT("Treat this as a failed edit: a native setter or PostEditChangeProperty may have rejected it."),
				*Operation, *PropertyPath, Before, After));
			return;
		}
		Out->SetBoolField(TEXT("verified"), true);
		if (Operation == TEXT("clear") && Before == 0)
		{
			Out->SetStringField(TEXT("note"), TEXT("the container was already empty; nothing needed to change (changed:false)"));
		}

		UE_LOG(LogMifBridge, Log, TEXT("edit_container: %s.%s %s (%d -> %d)"),
			*Target->GetName(), *PropertyPath, *Operation, Before, After);
	}
}
