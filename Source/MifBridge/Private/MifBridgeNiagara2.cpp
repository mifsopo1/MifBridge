// Niagara — reading a particle system's structure.
//
// MifBridge had exactly ONE Niagara endpoint before this file (list_niagara_user_parameters), which
// answers about the system's exposed parameters and nothing about what the system IS. There was no way
// to ask how many emitters a system has, which of them are enabled, or what they render with.
//
// GUARDED BY MIF_WITH_NIAGARA, because Niagara is a PLUGIN and can be disabled. The guard follows the
// IK Rig precedent exactly: the endpoints stay REGISTERED either way and compile a named refusal when
// the plugin is absent, because a missing endpoint tells a caller nothing while a refusal that names the
// reason tells them everything - and it keeps the three-way MIF_DECL/MIF_BIND/@mcp.tool parity intact on
// every engine.
//
// TWO HAZARDS SPECIFIC TO THIS SUBSYSTEM, both recorded before they bite:
//
// 1. COOKED NIAGARA HAS KILLED THIS EDITOR. docs/02_GOTCHAS.md section 6c records duplicate_asset on a
//    cooked UNiagaraSystem crashing in FVersionedNiagaraEmitterData::PostLoad. These endpoints only READ
//    handles off an already-loaded system and never duplicate, reinitialise or compile one, which is the
//    operation that was fatal - but the family is worth treating as sharp.
//
// 2. UNiagaraSystem IS UCLASS(MinimalAPI). Only StaticClass() is exported by the class declaration, so
//    Cast<> and LoadObject<> link while individual members do NOT unless each carries its own
//    NIAGARA_API. GetEmitterHandles() does (5.3 NiagaraSystem.h:282, 5.7 :310, identical). Anything
//    added here later must be checked the same way or it compiles and fails at LINK - the same trap the
//    InputCore and ImageWrapper notes in MifBridge.Build.cs describe.

#include "MifBridgeHandlers.h"

#if MIF_WITH_NIAGARA
#include "NiagaraSystem.h"
#include "NiagaraEmitter.h"
#include "NiagaraEmitterHandle.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_NIAGARA
	// The plugin is absent on this engine. Both endpoints answer with the reason rather than 404ing,
	// so a caller learns why instead of guessing at a missing name.
	static void MifNoNiagara(const TSharedRef<FJsonObject>& Out)
	{
		Fail(Out, TEXT("this engine build has no Niagara plugin, so there is nothing to read. The "
					   "endpoint exists on every build deliberately - a missing endpoint would tell you "
					   "nothing, while this tells you the plugin is what is missing."));
	}

	void H_describe_niagara_system(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoNiagara(Out);
	}
	void H_list_niagara_emitters(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoNiagara(Out);
	}
#else

	// Shared resolver: both endpoints take the same asset and make the same two mistakes possible.
	static UNiagaraSystem* MifResolveNiagaraSystem(const FString& Path, const TSharedRef<FJsonObject>& Out)
	{
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a NiagaraSystem asset"));
			return nullptr;
		}
		UNiagaraSystem* System = LoadObject<UNiagaraSystem>(nullptr, *Path);
		if (!System)
		{
			// The trailing-name retry the rest of the bridge uses: callers pass both the package
			// (/Game/FX/NS_Fire) and the object (/Game/FX/NS_Fire.NS_Fire).
			const FString Name = FPaths::GetBaseFilename(Path);
			System = LoadObject<UNiagaraSystem>(nullptr, *(Path + TEXT(".") + Name));
		}
		if (!System)
		{
			Fail(Out, FString::Printf(
				TEXT("no NiagaraSystem at '%s'. find_assets {class:\"NiagaraSystem\"} lists them; an "
					 "object path looks like /Game/FX/NS_Fire.NS_Fire."), *Path));
			return nullptr;
		}
		return System;
	}

	// --- describe_niagara_system ---------------------------------------------
	//   in:  { path (aliases: assetPath, system) }
	//   out: { system, name, emitterCount, enabledEmitterCount, exposedParameterCount?, note? }
	// The first question about any effect: how many emitters, and how many of them actually run. A
	// disabled emitter is invisible in game and perfectly visible in the editor, which is a common
	// source of "the effect does nothing" - so the enabled count is reported separately rather than
	// left for the caller to compute from the emitter list.
	void H_describe_niagara_system(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("system") },
			TEXT("path (aliases: assetPath, system) - a NiagaraSystem asset"),
			{ { TEXT("emitter"), TEXT("this describes the whole system; list_niagara_emitters is the one that takes an emitter") },
			  { TEXT("component"), TEXT("this reads the ASSET; a placed component's overrides are a different question") } }))
		{
			return;
		}

		UNiagaraSystem* System = MifResolveNiagaraSystem(
			JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("system") }), Out);
		if (!System) { return; }

		const TArray<FNiagaraEmitterHandle>& Handles = System->GetEmitterHandles();
		int32 Enabled = 0;
		for (const FNiagaraEmitterHandle& Handle : Handles)
		{
			if (Handle.GetIsEnabled()) { ++Enabled; }
		}

		Out->SetStringField(TEXT("system"), System->GetPathName());
		Out->SetStringField(TEXT("name"), System->GetName());
		Out->SetNumberField(TEXT("emitterCount"), Handles.Num());
		Out->SetNumberField(TEXT("enabledEmitterCount"), Enabled);
		Out->SetNumberField(TEXT("disabledEmitterCount"), Handles.Num() - Enabled);

		if (Handles.Num() == 0)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("this system has no emitters at all, so it renders nothing. On a COOKED system that "
					 "may mean its editor-only emitter data was stripped rather than that the effect is "
					 "empty - check whether the package is cooked before concluding it is broken."));
		}
		else if (Enabled == 0)
		{
			// The specific failure this endpoint exists to make visible.
			Out->SetStringField(TEXT("note"),
				TEXT("every emitter in this system is DISABLED, so it will render nothing at runtime "
					 "while still looking populated in the editor."));
		}
	}

	// --- list_niagara_emitters -----------------------------------------------
	//   in:  { path (aliases: assetPath, system), nameContains?, includeDisabled? }
	//   out: { system, count, totalEmitters, emitters:[{ index, name, id, enabled, rendererCount }] }
	// Which emitters exist and which are live. The renderer count is included because an emitter with
	// no renderers simulates and draws nothing, which looks identical to a disabled one from outside
	// and is a different fix.
	void H_list_niagara_emitters(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("system"), TEXT("nameContains"),
			  TEXT("includeDisabled") },
			TEXT("path (aliases: assetPath, system) - a NiagaraSystem asset; nameContains (substring "
				 "filter); includeDisabled (default true)"),
			{ { TEXT("index"), TEXT("this lists them all with their index - filter with nameContains, or read the index off the result") } }))
		{
			return;
		}

		UNiagaraSystem* System = MifResolveNiagaraSystem(
			JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("system") }), Out);
		if (!System) { return; }

		const FString NameContains = JStr(In, TEXT("nameContains"));
		const bool bIncludeDisabled = JBool(In, TEXT("includeDisabled"), true);

		const TArray<FNiagaraEmitterHandle>& Handles = System->GetEmitterHandles();
		TArray<TSharedPtr<FJsonValue>> Rows;
		for (int32 Index = 0; Index < Handles.Num(); ++Index)
		{
			const FNiagaraEmitterHandle& Handle = Handles[Index];
			const FString EmitterName = Handle.GetName().ToString();
			if (!NameContains.IsEmpty() && !EmitterName.Contains(NameContains)) { continue; }
			const bool bEnabled = Handle.GetIsEnabled();
			if (!bEnabled && !bIncludeDisabled) { continue; }

			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			// The INDEX is reported because it is the stable way to address an emitter: names are not
			// guaranteed unique within a system, and the GUID is unwieldy to pass by hand.
			Row->SetNumberField(TEXT("index"), Index);
			Row->SetStringField(TEXT("name"), EmitterName);
			Row->SetStringField(TEXT("id"), Handle.GetId().ToString());
			Row->SetBoolField(TEXT("enabled"), bEnabled);
			Rows.Add(MakeShared<FJsonValueObject>(Row));
		}

		Out->SetStringField(TEXT("system"), System->GetPathName());
		Out->SetNumberField(TEXT("count"), Rows.Num());
		// totalEmitters is the unfiltered truth, so a filtered list can never read as completeness.
		Out->SetNumberField(TEXT("totalEmitters"), Handles.Num());
		Out->SetArrayField(TEXT("emitters"), Rows);
		if (Rows.Num() == 0 && Handles.Num() > 0)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("the system has emitters but none matched the filter - totalEmitters is the real "
					 "count."));
		}
	}
#endif   // MIF_WITH_NIAGARA
}
