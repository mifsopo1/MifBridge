// MifBridge — GAMEPLAY ABILITY SYSTEM: GameplayEffect modifier authoring.
//
// WHY THIS EXISTS. Curfew's own decision log (DEC-064, docs/01-design-decisions.md) commits to GAS
// for "wanted/effects/cop tools" - a real, planned system, just not built yet: zero
// UGameplayAbility/UGameplayEffect/AttributeSet subclasses exist in Curfew's C++ today. Andre's call
// (2026-08-27): build the authoring endpoint now anyway, same bet as MetaHuman, so it is there when
// that work actually starts rather than discovered missing partway through it.
//
// WHY A MODIFIER NEEDS ITS OWN ENDPOINT, since set_property can already write every field on a
// GameplayEffect Blueprint's CDO - this is the IK Rig file's warning again: syntactically valid,
// semantically broken, ok:true. FGameplayModifierInfo::Attribute is an FGameplayAttribute, and its
// one real field is `TFieldPath<FProperty> Attribute` (AttributeSet.h) - PRIVATE, friend-gated to the
// details-panel customization, set programmatically only through SetUProperty(FProperty*) after
// resolving a real property off an AttributeSet class. There is no plain string a caller could hand
// set_property that reliably produces a working FieldPath here; the value has to be BUILT by asking
// the class for the property and letting the engine construct the reference; that is the actual gap.
//
// SCOPE, deliberately narrow. GAS 5.3+ has moved most of a GameplayEffect's OTHER behaviour (tags,
// immunity, conditional effects) onto UGameplayEffectComponent subclasses (GEComponents, protected),
// each its own present-but-more-involved authoring problem. Modifiers and Executions are the one part
// of the OLD direct-field model that is NOT UE_DEPRECATED in 5.7 (checked: every deprecated field
// around them says so explicitly; these two do not) - still the live way to make a GameplayEffect
// change a number, which is the concrete thing DEC-064 asks for. Components are a separate item if
// Curfew's GAS work ever needs them.
//
// UNPROVEN, HONESTLY. Neither project has an AttributeSet class yet, so nothing here has been
// exercised against real game content - only against a throwaway AttributeSet added to a probe
// project's own Source for the purpose (see tools/FEATURE_PARITY_SPEC.md for the verification this
// endpoint actually got). Treat that the same way the MetaHuman entry does: real code, run for real,
// against a fixture rather than a hand-authored asset.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "UObject/Package.h"
#include "UObject/UnrealType.h"

#if MIF_WITH_GAS
#include "GameplayEffect.h"          // UGameplayEffect, FGameplayModifierInfo
#include "AttributeSet.h"            // FGameplayAttribute, IsSupportedProperty
#include "ScalableFloat.h"           // FScalableFloat
#endif

namespace MifBridge
{
#if !MIF_WITH_GAS
	namespace
	{
		void GASUnavailable(const TSharedRef<FJsonObject>& Out, const TCHAR* What)
		{
			Fail(Out, FString::Printf(
				TEXT("%s is unavailable: this MifBridge was built against an engine with no ")
				TEXT("GameplayAbilities plugin. Rebuild against an engine that has it - it ships with ")
				TEXT("every stock UE5 install, so this refusal should not be reachable in practice."), What));
		}
	}
#endif

#if MIF_WITH_GAS
	namespace
	{
		/** "Add"/"Additive" -> Additive, "Multiply"/"MultiplyAdditive" -> MultiplyAdditive, etc. Both the
		 *  friendly name and the engine's own enum spelling are accepted, because EGameplayModOp's real
		 *  names (MultiplyAdditive, DivideAdditive) are not what a caller unfamiliar with GAS would
		 *  guess, and refusing the guess by naming the accepted set (PM-002) beats silently defaulting. */
		// PORTABLE SPELLING, checked in both trees (docs/02_GOTCHAS.md section 14) rather than assumed
		// from one. 5.7 renamed Multiplicitive/Division to MultiplyAdditive/DivideAdditive and kept the
		// old names only as UMETA(Hidden) backwards-compat aliases (GameplayEffectTypes.h); 5.3 has
		// ONLY the old names - MultiplyAdditive/DivideAdditive do not exist there at all (C2039/C2065
		// on the 5.3 probe build). Multiplicitive/Division/Additive/Override are identical values in
		// both engines, so those are what this file uses - same lesson as GetDocumentChecked() in
		// MifBridgeMetasound.cpp: the non-renamed spelling is the portable one.
		bool ParseModOp(const FString& In, EGameplayModOp::Type& Out)
		{
			const FString S = In.TrimStartAndEnd();
			if (S.Equals(TEXT("Add"), ESearchCase::IgnoreCase) || S.Equals(TEXT("Additive"), ESearchCase::IgnoreCase))
			{ Out = EGameplayModOp::Additive; return true; }
			if (S.Equals(TEXT("Multiply"), ESearchCase::IgnoreCase) || S.Equals(TEXT("Multiplicitive"), ESearchCase::IgnoreCase))
			{ Out = EGameplayModOp::Multiplicitive; return true; }
			if (S.Equals(TEXT("Divide"), ESearchCase::IgnoreCase) || S.Equals(TEXT("Division"), ESearchCase::IgnoreCase))
			{ Out = EGameplayModOp::Division; return true; }
			if (S.Equals(TEXT("Override"), ESearchCase::IgnoreCase))
			{ Out = EGameplayModOp::Override; return true; }
			return false;
		}

		const TCHAR* ModOpName(EGameplayModOp::Type Op)
		{
			switch (Op)
			{
			case EGameplayModOp::Additive:       return TEXT("Add");
			case EGameplayModOp::Multiplicitive: return TEXT("Multiply");
			case EGameplayModOp::Division:       return TEXT("Divide");
			case EGameplayModOp::Override:       return TEXT("Override");
			default:                             return TEXT("Unknown");
			}
		}
	}
#endif

	// --- add_gameplay_effect_modifier ----------------------------------------------------------------
	//   in:  { objectPath, attributeSetClass, attributeName, operation, magnitude }
	//   out: { objectPath, attribute, attributeSetClass, operation, magnitude, modifierIndex,
	//          modifierCount, note }
	// objectPath is the SAME resolver every other property endpoint uses (ResolvePropertyTarget) - for
	// a GameplayEffect Blueprint that means its CDO path, /Game/.../GE_Foo.Default__GE_Foo_C, exactly
	// like reading a Blueprint default anywhere else in this bridge.
	void H_add_gameplay_effect_modifier(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("attributeSetClass"), TEXT("attributeName"),
			  TEXT("operation"), TEXT("magnitude") },
			TEXT("objectPath (a GameplayEffect Blueprint's CDO, e.g. .../GE_Foo.Default__GE_Foo_C), ")
			TEXT("attributeSetClass, attributeName, operation (Add|Multiply|Divide|Override), magnitude ")
			TEXT("(flat float - curve-table magnitudes are not covered by this endpoint)"),
			{ { TEXT("attribute"), TEXT("split into attributeSetClass + attributeName - a "
					"FGameplayAttribute is resolved from a real class property, not a bare string") },
			  { TEXT("value"), TEXT("this endpoint's numeric key is magnitude, to match GAS's own "
					"terminology - set_property's generic 'value' key does not apply here") } }))
		{
			return;
		}
#if !MIF_WITH_GAS
		GASUnavailable(Out, TEXT("add_gameplay_effect_modifier"));
#else
		UObject* Target = ResolvePropertyTarget(In, Out, nullptr);
		if (!Target) { return; }
		UGameplayEffect* Effect = Cast<UGameplayEffect>(Target);
		if (!Effect)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' resolved to a %s, not a UGameplayEffect. Pass a GameplayEffect Blueprint's ")
				TEXT("CDO path (.../GE_Foo.Default__GE_Foo_C)."),
				*JStr(In, TEXT("objectPath")), *Target->GetClass()->GetName()));
			return;
		}

		UClass* AttrSetClass = ResolveClassStrictField(In, { TEXT("attributeSetClass") }, nullptr, Out);
		if (!AttrSetClass) { return; }
		if (!AttrSetClass->IsChildOf(UAttributeSet::StaticClass()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a UAttributeSet subclass - attributeSetClass must be the class that ")
				TEXT("DECLARES the attribute property, not the ability system's owner."),
				*AttrSetClass->GetPathName()));
			return;
		}

		const FString AttributeName = JStr(In, TEXT("attributeName")).TrimStartAndEnd();
		if (AttributeName.IsEmpty()) { Fail(Out, TEXT("attributeName is required")); return; }
		FProperty* Prop = AttrSetClass->FindPropertyByName(FName(*AttributeName));
		if (!Prop)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no property named '%s'. GAS attributes are FGameplayAttributeData ")
				TEXT("UPROPERTYs declared directly on the AttributeSet class."),
				*AttrSetClass->GetName(), *AttributeName));
			return;
		}
		// IsGameplayAttributeDataProperty, not IsSupportedProperty: the latter is a 5.6+ addition to
		// FGameplayAttribute (a DIFFERENT, non-static, UAttributeSet-member overload exists on 5.3
		// under the same name, which is not the same check and does not portably resolve here).
		// IsGameplayAttributeDataProperty is static on FGameplayAttribute in BOTH trees and is also the
		// stricter, more-correct check for a NEW attribute: GAS's current convention is
		// FGameplayAttributeData, not a bare float, so steering toward it here is a feature.
		if (!FGameplayAttribute::IsGameplayAttributeDataProperty(Prop))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s.%s' exists but is not an FGameplayAttributeData property - GAS attributes ")
				TEXT("must be declared as FGameplayAttributeData, not a bare float, to be modifiable ")
				TEXT("here. NOTHING was added."),
				*AttrSetClass->GetName(), *AttributeName));
			return;
		}

		const FString OpStr = JStr(In, TEXT("operation"));
		EGameplayModOp::Type Op;
		if (!ParseModOp(OpStr, Op))
		{
			Fail(Out, FString::Printf(
				TEXT("operation '%s' is not one of Add | Multiply | Divide | Override. NOTHING was added."),
				*OpStr));
			return;
		}

		const double Magnitude = JNum(In, TEXT("magnitude"), 0.0);

		// Built the engine's way: SetUProperty resolves AttributeOwner/AttributeName from the real
		// FProperty rather than a caller-supplied FieldPath string, which is the entire reason this
		// endpoint exists rather than a generic set_property call.
		FGameplayAttribute Attr;
		Attr.SetUProperty(Prop);

		FGameplayModifierInfo Info;
		Info.Attribute = Attr;
		Info.ModifierOp = Op;
		Info.ModifierMagnitude = FGameplayEffectModifierMagnitude(FScalableFloat(static_cast<float>(Magnitude)));

		// Modify() BEFORE the mutation, not just MarkPackageDirty() after it. Two things depend on
		// it and both were broken without it: the undo transaction has nothing to restore (so Ctrl+Z
		// - the escape hatch this whole bridge promises - silently did nothing for this endpoint),
		// and FMifScratchWatch listens on FCoreUObjectDelegates::OnObjectModified, which only
		// Modify() raises. So appending a modifier to a real /Game asset was reported as
		// scratchClean:true. MarkPackageDirty sets the dirty flag; it announces nothing.
		Effect->Modify();
		const int32 Index = Effect->Modifiers.Add(Info);
		Effect->GetOutermost()->MarkPackageDirty();

		// Read back from the array we just wrote, not from the local Info - proof the append landed
		// where the caller will actually find it, matching "read back every write" everywhere else.
		const FGameplayModifierInfo& Written = Effect->Modifiers[Index];
		Out->SetStringField(TEXT("objectPath"), Effect->GetPathName());
		Out->SetStringField(TEXT("attribute"), Written.Attribute.GetName());
		Out->SetStringField(TEXT("attributeSetClass"), AttrSetClass->GetPathName());
		Out->SetStringField(TEXT("operation"), ModOpName(Written.ModifierOp));
		Out->SetNumberField(TEXT("magnitude"), Magnitude);
		Out->SetNumberField(TEXT("modifierIndex"), Index);
		Out->SetNumberField(TEXT("modifierCount"), Effect->Modifiers.Num());
		Out->SetStringField(TEXT("note"),
			TEXT("magnitude is a flat ScalableFloat with no curve table - pass a curve-driven magnitude ")
			TEXT("via set_property on Modifiers[N].ModifierMagnitude if a level curve is needed later. ")
			TEXT("Not saved: save_blueprint/save_package to persist."));
		UE_LOG(LogMifBridge, Log, TEXT("add_gameplay_effect_modifier: %s += %s.%s (%s %.3f)"),
			*Effect->GetName(), *AttrSetClass->GetName(), *AttributeName, ModOpName(Op), Magnitude);
#endif
	}
}
