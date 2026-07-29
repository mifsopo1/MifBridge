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
				const uint8* A = (const uint8*)ValueAddr   + (SIZE_T)i * Prop->ElementSize;
				const uint8* B = (const uint8*)DefaultAddr + (SIZE_T)i * Prop->ElementSize;
				if (!Prop->Identical(A, B, PortFlags)) { return true; }
			}
			return false;
		}

		FString MifDetailsExportOne(const FProperty* Prop, const void* Addr, UObject* Owner)
		{
			FString S;
			// Data == Delta short-circuits the "skip if identical to the default" branch
			// (Property.cpp:1149), so this always emits.
			if (Prop && Addr) { Prop->ExportText_Direct(S, Addr, Addr, Owner, PPF_None); }
			return S;
		}

		// The value a freshly constructed instance of this property would hold. Used when the
		// archetype does not carry the property at all - a variable a child Blueprint added, for
		// instance - mirroring FPropertyNode::GetDefaultValueAsString's fallback
		// (PropertyNode.cpp:2432-2443). Reported as defaultSource:"constructed", never as if it had
		// come from an archetype.
		FString MifDetailsConstructedDefaultText(const FProperty* Prop, UObject* Owner)
		{
			if (!Prop) { return FString(); }
			void* Mem = FMemory::Malloc(FMath::Max(Prop->GetSize(), 1), Prop->GetMinAlignment());
			Prop->InitializeValue(Mem);
			const FString Text = MifDetailsExportOne(Prop, Mem, Owner);
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
			Row->SetNumberField(TEXT("elementSize"), Prop->ElementSize);
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
				FString ValueText = MifDetailsExportOne(Prop, ValueAddr, Owner);
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
						DefaultText   = MifDetailsExportOne(Prop, DefaultAddr, Archetype);
						DefaultSource = TEXT("archetype");
						bDiffers      = MifDetailsDiffersFromDefault(Prop, ValueAddr, DefaultAddr, bSingleElement, /*bDeep*/ true);
					}
					else
					{
						DefaultText   = MifDetailsConstructedDefaultText(Prop, Owner);
						DefaultSource = TEXT("constructed");
						bDiffers      = !DefaultText.Equals(MifDetailsExportOne(Prop, ValueAddr, Owner), ESearchCase::CaseSensitive);
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
	//          nameFilter)?, limit?, maxValueChars?, includeTransient?, deep? }
	//   out: { inspected, differing, matching, skippedTransient, truncated, properties[] }
	//
	// "What does this object actually OVERRIDE?" - the question the panel answers with a yellow
	// arrow and the bridge could not answer at all. The invariant
	// inspected == differing + matching + skippedTransient is EMITTED, not implied.
	// =============================================================================================
	void H_diff_properties_vs_default(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			  TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"),
			  TEXT("limit"), TEXT("maxValueChars"), TEXT("includeTransient"), TEXT("deep") },
			TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, nameContains (aliases filter, nameFilter), limit, maxValueChars, includeTransient, deep")))
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
			Out->SetBoolField(TEXT("truncated"), false);
			Out->SetArrayField(TEXT("properties"), TArray<TSharedPtr<FJsonValue>>());
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("'%s' has no distinct archetype (its archetype is itself), so every property matches by definition (differing:0)."),
				*Target->GetPathName()));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Rows;
		int32 Inspected = 0, Differing = 0, Matching = 0, SkippedTransient = 0;
		bool bTruncated = false;
		for (TFieldIterator<FProperty> It(Target->GetClass()); It; ++It)
		{
			FProperty* Prop = *It;
			if (!Prop) { continue; }
			if (!NameFilter.IsEmpty() && !Prop->GetName().Contains(NameFilter)) { continue; }
			++Inspected;
			if (!bIncludeTransient && Prop->HasAnyPropertyFlags(CPF_Transient))
			{
				// Transients always differ and drown the signal.
				++SkippedTransient;
				continue;
			}

			const void* ValueAddr = Prop->ContainerPtrToValuePtr<void>(Target);
			const void* DefaultAddr = nullptr;
			FString DefaultText, DefaultSource;
			bool bDiffers = false;
			if (Archetype->GetClass()->FindPropertyByName(Prop->GetFName()))
			{
				DefaultAddr   = Prop->ContainerPtrToValuePtr<void>(Archetype);
				DefaultSource = TEXT("archetype");
				bDiffers      = MifDetailsDiffersFromDefault(Prop, ValueAddr, DefaultAddr, /*bSingleElement*/ false, bDeep);
			}
			else
			{
				DefaultText   = MifDetailsConstructedDefaultText(Prop, Target);
				DefaultSource = TEXT("constructed");
				bDiffers      = !DefaultText.Equals(MifDetailsExportOne(Prop, ValueAddr, Target), ESearchCase::CaseSensitive);
			}

			if (!bDiffers) { ++Matching; continue; }
			++Differing;
			if (Rows.Num() >= Limit) { bTruncated = true; continue; }

			FString ValueText = MifDetailsExportOne(Prop, ValueAddr, Target);
			if (ValueText.Len() > MaxValueChars) { ValueText = ValueText.Left(MaxValueChars); }
			if (DefaultAddr) { DefaultText = MifDetailsExportOne(Prop, DefaultAddr, Archetype); }
			if (DefaultText.Len() > MaxValueChars) { DefaultText = DefaultText.Left(MaxValueChars); }

			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("name"), Prop->GetName());
			Row->SetStringField(TEXT("type"), Prop->GetCPPType());
			Row->SetStringField(TEXT("value"), ValueText);
			Row->SetStringField(TEXT("defaultValue"), DefaultText);
			Row->SetStringField(TEXT("defaultSource"), DefaultSource);
			Row->SetStringField(TEXT("specifier"), MifDetailsAuthoredSpecifier(Prop));
			Row->SetStringField(TEXT("persistence"), MifDetailsPersistence(Prop));
			Row->SetBoolField(TEXT("resettable"),
				!Prop->HasAnyPropertyFlags(CPF_Config) && !Prop->HasAnyPropertyFlags(CPF_EditFixedSize));
			Rows.Add(MakeShared<FJsonValueObject>(Row));
		}

		Out->SetNumberField(TEXT("inspected"), Inspected);
		Out->SetNumberField(TEXT("differing"), Differing);
		Out->SetNumberField(TEXT("matching"), Matching);
		Out->SetNumberField(TEXT("skippedTransient"), SkippedTransient);
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("properties"), Rows);
		// The checkable invariant, emitted rather than implied.
		Out->SetBoolField(TEXT("countsConsistent"), Inspected == (Differing + Matching + SkippedTransient));
	}

	// =============================================================================================
	// reset_property_to_default - TRANSACTED
	//   in:  { objectPath (actorPath), propertyPath (property), force (allowEditConst)? }
	//   out: { target, propertyPath, valueBefore, defaultValue, valueAfter, differedFromDefault,
	//          changed, defaultSource, archetype, verified, notification, ... }
	//
	// The Details panel's yellow arrow. Two refusals the panel applies and a naive reset does not:
	// CPF_Config properties have NO reset arrow and CPF_EditFixedSize containers have none either
	// (FPropertyHandleBase::CanResetToDefault, PropertyHandleImpl.cpp:3421-3433).
	// =============================================================================================
	void H_reset_property_to_default(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			  TEXT("propertyPath"), TEXT("property"), TEXT("force"), TEXT("allowEditConst") },
			TEXT("objectPath (alias actorPath), propertyPath (alias property), force (alias allowEditConst)")))
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

		// --- the panel's two refusals, BEFORE anything is touched -----------------------
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

		// --- the default value ----------------------------------------------------------
		UObject* Archetype = MifDetailsArchetypeOf(Target);
		FString DefaultText, DefaultSource;
		FPropertyPathResolution DefaultRes;
		FString DefaultError;
		const void* DefaultAddr = nullptr;
		if (Archetype && Archetype != Target && ResolvePropertyPathEx(Archetype, PropertyPath, DefaultRes, DefaultError))
		{
			DefaultAddr   = DefaultRes.LeafAddr;
			DefaultText   = MifDetailsExportOne(DefaultRes.Leaf, DefaultRes.LeafAddr, DefaultRes.LeafOwner);
			DefaultSource = TEXT("archetype");
		}
		else
		{
			DefaultText   = MifDetailsConstructedDefaultText(Leaf, LeafOwner);
			DefaultSource = TEXT("constructed");
		}

		const FString BeforeText = MifDetailsExportOne(Leaf, LeafAddr, LeafOwner);
		const bool bDiffered = DefaultAddr
			? MifDetailsDiffersFromDefault(Leaf, LeafAddr, DefaultAddr, Res.bLeafIsElement, /*bDeep*/ true)
			: !BeforeText.Equals(DefaultText, ESearchCase::CaseSensitive);

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
		void* Staging = FMemory::Malloc(FMath::Max(Leaf->GetSize(), 1), Leaf->GetMinAlignment());
		Leaf->InitializeValue(Staging);
		Leaf->CopySingleValue(Staging, LeafAddr);   // seed, so a partial default literal keeps the rest
		FString StagedText, ImportError;
		const bool bParsed = ImportPropertyTextSafely(Leaf, DefaultText, LeafAddr, Staging, LeafOwner, StagedText, ImportError);
		if (!bParsed)
		{
			Leaf->DestroyValue(Staging);
			FMemory::Free(Staging);
			// PM-003: the parser only ever saw scratch memory, so the live value is untouched.
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetBoolField(TEXT("verified"), false);
			Out->SetBoolField(TEXT("nothingModified"), true);
			Fail(Out, FString::Printf(TEXT("reset of '%s' failed while re-importing the default text '%s': %s The property is unchanged."),
				*PropertyPath, *DefaultText, *ImportError));
			return;
		}

		LeafOwner->Modify();
		if (bChainBuilt) { LeafOwner->PreEditChange(EditChain); } else { LeafOwner->PreEditChange(Leaf); }
		Leaf->CopySingleValue(LeafAddr, Staging);
		Leaf->DestroyValue(Staging);
		FMemory::Free(Staging);

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

		const FString AfterText = MifDetailsExportOne(Leaf, LeafAddr, LeafOwner);
		Out->SetStringField(TEXT("valueAfter"), AfterText);
		Out->SetBoolField(TEXT("changed"), !AfterText.Equals(BeforeText, ESearchCase::CaseSensitive));
		if (!AfterText.Equals(DefaultText, ESearchCase::CaseSensitive))
		{
			// The invariant this endpoint stakes its ok on: after a successful reset the value must
			// equal the default byte-for-byte under the SAME exporter. Reusing set_property's
			// "import said success, readback says otherwise" failure shape.
			Out->SetBoolField(TEXT("verified"), false);
			Fail(Out, FString::Printf(
				TEXT("reset of '%s' did NOT land: the default is '%s', the import produced '%s', and re-reading the property returned ")
				TEXT("'%s'. Likely a native setter or PostEditChangeProperty adjusted the value. Compare valueBefore / defaultValue / ")
				TEXT("valueAfter in this response."),
				*PropertyPath, *DefaultText, *StagedText, *AfterText));
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
	//          index (at)?, count?, key?, newKey?, value?, swapWith?, newSize? }
	//   out: { target, propertyPath, containerKind, operation, elementsBefore, elementsAfter,
	//          index?, rehashed, changed, verified, ... }
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
			  TEXT("key"), TEXT("newKey"), TEXT("value"), TEXT("swapWith"), TEXT("newSize") },
			TEXT("objectPath (alias actorPath), propertyPath (alias property), operation (alias action) = add|insert|remove|clear|swap|resize|setKey, index (alias at), count, key, newKey, value, swapWith, newSize"),
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

		// --- notify, with the ARRAY change type the panel would use ---------------------
		FPropertyChangedEvent Evt(Leaf,
			Operation == TEXT("add") || Operation == TEXT("insert") ? EPropertyChangeType::ArrayAdd :
			Operation == TEXT("remove")                             ? EPropertyChangeType::ArrayRemove :
			Operation == TEXT("clear")                              ? EPropertyChangeType::ArrayClear :
			Operation == TEXT("swap")                               ? EPropertyChangeType::ArrayMove :
			                                                          EPropertyChangeType::ValueSet);
		LeafOwner->PostEditChangeProperty(Evt);
		LeafOwner->MarkPackageDirty();

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
