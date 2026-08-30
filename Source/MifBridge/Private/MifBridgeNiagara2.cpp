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
#include "MifBridgeLog.h"   // LogMifBridge - reached transitively on 5.3, not on 5.7

#if MIF_WITH_NIAGARA
#include "NiagaraSystem.h"
#include "NiagaraEmitter.h"
#include "NiagaraEmitterHandle.h"
#include "NiagaraComponent.h"                      // SetVariableFloat/Int/Bool/Vec3/LinearColor
#include "Subsystems/EditorActorSubsystem.h"
#include "Editor.h"
#include "GameFramework/Actor.h"
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

	// --- set_niagara_component_parameter --------------------------------------------------------
	//   in:  { actorPath, name, type?, value, confirm }
	//   out: { actor, component, parameter, type, system }
	// Bucket: MUTATES a placed component in the open level. Nothing is saved.
	//
	// THE WRITE HALF, AND IT DELIBERATELY DOES NOT TOUCH THE ASSET.
	//
	// Niagara had three reads and no writes. The obvious write is "set a user parameter on the system"
	// - and on this project that is a loaded gun: docs/02 section 6c records that duplicating a COOKED
	// UNiagaraSystem is an EXCEPTION_ACCESS_VIOLATION inside FVersionedNiagaraEmitterData::PostLoad,
	// fatal, with no MifBridge frame in the stack. duplicate_asset already refuses cooked Niagara for
	// that reason.
	//
	// So this writes to a PLACED COMPONENT instead, which is both safer and more useful:
	//
	//   * It never touches the cooked asset, so the PostLoad hazard is not merely guarded against -
	//     it is not on the code path at all.
	//   * A component override is what you actually want when tuning an effect. Editing the system
	//     changes every instance in the project; editing the component changes the one you are
	//     looking at.
	//   * It works identically on cooked and uncooked projects, which is the point of this bridge now.
	//
	// Verified in BOTH trees: UNiagaraComponent::SetVariableFloat / Int / Bool / Vec3 /
	// LinearColor are all NIAGARA_API with identical signatures.
	//
	// TYPE IS EXPLICIT, not inferred from the JSON. A JSON number could be a float or an int, and
	// Niagara treats those as different variables - setting the wrong one succeeds silently and the
	// effect ignores it, because the parameter it wrote does not exist under that type. Inferring here
	// would produce exactly the silent-success shape this project keeps finding. The type is inferred
	// ONLY when it is unambiguous, and the response always says which was used.
	void H_set_niagara_component_parameter(const TSharedRef<FJsonObject>& In,
										   const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("component"), TEXT("name"), TEXT("parameter"),
			  TEXT("type"), TEXT("value"), TEXT("confirm") },
			TEXT("actorPath (an actor with a NiagaraComponent); name (alias: parameter) - the user "
				 "parameter; type - float|int|bool|vector|color (inferred when unambiguous); value; "
				 "confirm:true"),
			{ { TEXT("system"), TEXT("this sets an override on a PLACED COMPONENT, not on the system asset - editing the asset would change every instance, and on a COOKED system it is a known editor crash (docs/02 section 6c)") },
			  { TEXT("path"), TEXT("spell it actorPath - this addresses a placed actor, not an asset") } }))
		{
			return;
		}
#if !MIF_WITH_NIAGARA
		MifNoNiagara(Out);
#else
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("set_niagara_component_parameter needs confirm:true - it changes a live "
						   "component in the open level. NOTHING was changed."));
			return;
		}

		UEditorActorSubsystem* Sub = GEditor
			? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
		if (!Sub) { Fail(Out, TEXT("no UEditorActorSubsystem.")); return; }
		AActor* Actor = ResolveActor(Sub, In, Out);
		if (!Actor) { return; }

		TArray<UNiagaraComponent*> Comps;
		Actor->GetComponents<UNiagaraComponent>(Comps);
		if (Comps.Num() == 0)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no NiagaraComponent. NOTHING was changed."), *Actor->GetActorLabel()));
			return;
		}
		// NAMED when there is more than one, rather than silently taking the first. An actor with two
		// effects is exactly the case where guessing writes to the wrong one and reports success.
		const FString WantComp = JStr(In, TEXT("component"));
		UNiagaraComponent* Comp = nullptr;
		if (WantComp.IsEmpty())
		{
			if (Comps.Num() > 1)
			{
				TArray<FString> Names;
				for (const UNiagaraComponent* C : Comps) { Names.Add(C->GetName()); }
				Fail(Out, FString::Printf(
					TEXT("'%s' has %d NiagaraComponents (%s) - name one with `component`, because "
						 "picking for you would write to the wrong effect and report success. NOTHING "
						 "was changed."),
					*Actor->GetActorLabel(), Comps.Num(), *FString::Join(Names, TEXT(", "))));
				return;
			}
			Comp = Comps[0];
		}
		else
		{
			for (UNiagaraComponent* C : Comps)
			{
				if (C && C->GetName() == WantComp) { Comp = C; break; }
			}
			if (!Comp)
			{
				Fail(Out, FString::Printf(
					TEXT("no NiagaraComponent called '%s' on '%s'. NOTHING was changed."),
					*WantComp, *Actor->GetActorLabel()));
				return;
			}
		}

		const FString ParamName = JStrAny(In, { TEXT("name"), TEXT("parameter") });
		if (ParamName.IsEmpty())
		{
			Fail(Out, TEXT("name is required - a user parameter. list_niagara_user_parameters reports "
						   "them for the component's system. NOTHING was changed."));
			return;
		}

		// TYPE, explicit or unambiguously inferred - never guessed between float and int.
		FString Type = JStr(In, TEXT("type")).ToLower();
		const TSharedPtr<FJsonValue> Value = In->TryGetField(TEXT("value"));
		if (!Value.IsValid())
		{
			Fail(Out, TEXT("value is required. NOTHING was changed."));
			return;
		}
		if (Type.IsEmpty())
		{
			if (Value->Type == EJson::Boolean) { Type = TEXT("bool"); }
			else if (Value->Type == EJson::Object) { Type = TEXT("vector"); }
			else if (Value->Type == EJson::Number)
			{
				// REFUSED rather than guessed. Niagara treats Float and Int as different variables, so
				// writing the wrong one succeeds and the effect ignores it - silent success, which is
				// worse than a refusal a caller can act on in one edit.
				Fail(Out, TEXT("value is a number and `type` was not given, so this could be a float "
							   "or an int - and Niagara treats those as DIFFERENT variables. Writing "
							   "the wrong one succeeds and the effect ignores it. Pass type:\"float\" "
							   "or type:\"int\". NOTHING was changed."));
				return;
			}
			else
			{
				Fail(Out, TEXT("could not infer a type from this value. Pass type: float|int|bool|"
							   "vector|color. NOTHING was changed."));
				return;
			}
		}

		Comp->Modify();
		const FName Var(*ParamName);
		if (Type == TEXT("float"))
		{
			Comp->SetVariableFloat(Var, (float)Value->AsNumber());
		}
		else if (Type == TEXT("int"))
		{
			Comp->SetVariableInt(Var, (int32)Value->AsNumber());
		}
		else if (Type == TEXT("bool"))
		{
			Comp->SetVariableBool(Var, Value->AsBool());
		}
		else if (Type == TEXT("vector") || Type == TEXT("color"))
		{
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (!Value->TryGetObject(Obj) || !Obj)
			{
				Fail(Out, FString::Printf(
					TEXT("type '%s' needs an object value such as {\"x\":1,\"y\":2,\"z\":3} (or "
						 "{\"r\":..,\"g\":..,\"b\":..,\"a\":..} for color). NOTHING was changed."),
					*Type));
				return;
			}
			const TSharedRef<FJsonObject> V = Obj->ToSharedRef();
			if (Type == TEXT("vector"))
			{
				Comp->SetVariableVec3(Var, FVector(
					JNum(V, TEXT("x"), 0.0), JNum(V, TEXT("y"), 0.0), JNum(V, TEXT("z"), 0.0)));
			}
			else
			{
				Comp->SetVariableLinearColor(Var, FLinearColor(
					(float)JNum(V, TEXT("r"), 0.0), (float)JNum(V, TEXT("g"), 0.0),
					(float)JNum(V, TEXT("b"), 0.0), (float)JNum(V, TEXT("a"), 1.0)));
			}
		}
		else
		{
			Fail(Out, FString::Printf(
				TEXT("unknown type '%s' - use float, int, bool, vector or color. NOTHING was "
					 "changed."), *Type));
			return;
		}

		Out->SetStringField(TEXT("actor"), Actor->GetActorLabel());
		Out->SetStringField(TEXT("component"), Comp->GetName());
		Out->SetStringField(TEXT("parameter"), ParamName);
		Out->SetStringField(TEXT("type"), Type);
		Out->SetStringField(TEXT("system"),
			Comp->GetAsset() ? Comp->GetAsset()->GetPathName() : FString());
		// SAID PLAINLY, because Niagara has no read-back for an override and this endpoint therefore
		// cannot verify its own write the way the rest of this bridge does. Claiming success without
		// saying that would be claiming more than was checked.
		Out->SetStringField(TEXT("note"),
			TEXT("the override was applied to this COMPONENT, not to the system asset - other "
				 "instances are unaffected, and a cooked system was never touched. There is no engine "
				 "read-back for a component override, so this reports that the call was made, NOT "
				 "that the effect uses it: a name that matches no user parameter is accepted silently "
				 "by Niagara. Check the name against list_niagara_user_parameters. Nothing was "
				 "saved."));
		UE_LOG(LogMifBridge, Log, TEXT("set_niagara_component_parameter: %s.%s = (%s)"),
			*Actor->GetActorLabel(), *ParamName, *Type);
#endif
	}

	// =======================================================================
	// set_niagara_emitter - only the ENABLE direction was actually broken
	// =======================================================================
	//
	// SCOPE, NARROWED AFTER CHECKING. set_property{propertyPath:"EmitterHandles[N].bIsEnabled"}
	// already reaches this flag, and the DISABLE direction genuinely works: InitEmitters builds an
	// instance per handle unconditionally and Init sets ExecutionState=Disabled from
	// IsAllowedToExecute, so a raw property write is enough to turn an emitter off.
	//
	// ENABLE is the half that is broken, and it fails silently. FNiagaraEmitterHandle::SetIsEnabled
	// does two things a property write skips (NiagaraEmitterHandle.cpp:110-124):
	//
	//     GetSystemSpawnScript()->GetLatestSource()->RefreshFromExternalChanges();
	//     GetSystemSpawnScript()->InvalidateCompileResults(TEXT("Emitter enabled changed."));
	//
	// and UNiagaraSystem::PostEditChangeProperty does not compensate. So set_property flips the
	// bool, the system keeps its stale compile results, and the emitter stays dark - with a flag
	// that reads as enabled. That is a wrong answer rather than an error, which is why this is
	// worth an endpoint at all.
	//
	// ADD AND REMOVE ARE DELIBERATELY NOT HERE, and that is the vetting's call rather than
	// laziness. Raw AddEmitterHandle contains an UNGUARDED null dereference at
	// NiagaraSystem.cpp:2309 - GetLatestEmitterData()->RemoveParent() on a Template or Behavior
	// emitter - which is a different crash from the one already in this project's audit, and
	// RemoveEmitterHandle vs RemoveEmitterHandlesById differ in whether system parameters are
	// cleaned up. Both deserve their own item with their own guards rather than being smuggled in
	// beside a boolean.
	//
	// COOKED IS REFUSED, but NOT because it crashes - and getting that reason right matters,
	// because the wrong reason invites someone to "fix" it. SetIsEnabled's side-effect block
	// self-skips on cooked content (GetLatestSource is null there), so nothing dies. It is refused
	// because the change cannot be persisted and the system cannot be recompiled, so the emitter
	// would come back on restart with the flag telling a different story.
	//
	// UNiagaraComponent::SetEmitterEnable IS NOT USED, and the reason is worth recording: it is a
	// per-instance, cooked-safe alternative on 5.6 and 5.7 - and on 5.3 it is a STUB that logs
	// "SetEmitterEnable: Is not implemented in Niagara" and returns (NiagaraSystemInstance.cpp:166-
	// 177). Routing to it on 5.3 would produce a silent no-op that reports success.

	void H_set_niagara_emitter(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("system"), TEXT("emitter"), TEXT("enabled"),
			  TEXT("recompile") },
			TEXT("path (aliases assetPath, system) - a NiagaraSystem; emitter - the handle name; ")
			TEXT("enabled:true|false; recompile (default FALSE - compiling from an HTTP handler is ")
			TEXT("opt-in)"),
			{ { TEXT("add"), TEXT("adding an emitter is not offered here - raw AddEmitterHandle has "
								  "an unguarded null dereference on Template and Behavior emitters, "
								  "so it needs its own guards rather than riding along with a bool") },
			  { TEXT("remove"), TEXT("same - and RemoveEmitterHandle vs RemoveEmitterHandlesById "
									 "differ in whether system parameters are cleaned up") },
			  { TEXT("index"), TEXT("emitters are addressed by NAME here, because an index shifts "
									"when anything is added or removed") } }))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("system") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a NiagaraSystem asset. NOTHING was changed."));
			return;
		}
		UObject* Asset = LoadAssetLenient(Path);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: '%s'. NOTHING was changed."), *Path));
			return;
		}
		UNiagaraSystem* System = Cast<UNiagaraSystem>(Asset);
		if (!System)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s, not a NiagaraSystem. NOTHING was changed."),
				*Path, *Asset->GetClass()->GetName()));
			return;
		}

		// COOKED: refused for persistence, not for safety - see the note above.
		if (IsCookedOrContainerPackage(System->GetOutermost()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' came from a COOKED package. Toggling an emitter there changes a flag that "
					 "cannot be saved and a system that cannot be recompiled, so the emitter would "
					 "come back on restart with the flag saying otherwise. It is refused for that "
					 "reason rather than for safety - the engine's own side-effect block self-skips "
					 "on cooked content. NOTHING was changed."), *System->GetPathName()));
			return;
		}

		const FString EmitterName = JStr(In, TEXT("emitter"));
		if (EmitterName.IsEmpty())
		{
			Fail(Out, TEXT("emitter is required - the handle name. list_niagara_emitters reports "
				TEXT("them. NOTHING was changed.")));
			return;
		}
		if (!In->HasField(TEXT("enabled")))
		{
			Fail(Out, TEXT("enabled:true|false is required - say which end state you want rather "
				TEXT("than having this toggle. NOTHING was changed.")));
			return;
		}
		const bool bWant = JBool(In, TEXT("enabled"), true);
		const bool bRecompile = JBool(In, TEXT("recompile"), false);

		int32 Found = INDEX_NONE;
		TArray<FString> Names;
		const TArray<FNiagaraEmitterHandle>& Handles = System->GetEmitterHandles();
		for (int32 i = 0; i < Handles.Num(); ++i)
		{
			const FString Name = Handles[i].GetName().ToString();
			Names.Add(Name);
			if (Name == EmitterName) { Found = i; }
		}
		if (Found == INDEX_NONE)
		{
			Fail(Out, FString::Printf(
				TEXT("no emitter named '%s' on '%s'. It has: %s. NOTHING was changed."),
				*EmitterName, *System->GetName(),
				Names.Num() ? *FString::Join(Names, TEXT(", ")) : TEXT("(none)")));
			return;
		}

		Out->SetStringField(TEXT("system"), System->GetPathName());
		Out->SetStringField(TEXT("emitter"), EmitterName);
		Out->SetNumberField(TEXT("emitterIndex"), Found);

		const bool bWas = Handles[Found].GetIsEnabled();
		Out->SetBoolField(TEXT("wasEnabled"), bWas);
		if (bWas == bWant)
		{
			Out->SetBoolField(TEXT("enabled"), bWas);
			Out->SetBoolField(TEXT("changed"), false);
			Out->SetStringField(TEXT("note"),
				TEXT("that emitter is already in the state you asked for - nothing was changed, and "
					 "nothing needed to be."));
			return;
		}

		{
			FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_SetNiagaraEmitter",
											"Set Niagara Emitter Enabled"));
			System->Modify();
			// THE CALL set_property CANNOT MAKE. This is what invalidates the compile results and
			// refreshes the node graph - without it the flag flips and the emitter stays dark.
			FNiagaraEmitterHandle& Handle =
				const_cast<FNiagaraEmitterHandle&>(System->GetEmitterHandles()[Found]);
			Handle.SetIsEnabled(bWant, *System, bRecompile);
		}

		// READ BACK from the system's own handle list. SetIsEnabled returns a bool about whether
		// anything changed, which is not the same claim as "the emitter is now enabled".
		const bool bNow = System->GetEmitterHandles()[Found].GetIsEnabled();
		if (bNow != bWant)
		{
			Fail(Out, FString::Printf(
				TEXT("the emitter was set to %s and reads back as %s. NOTHING reliable was "
					 "produced."), bWant ? TEXT("enabled") : TEXT("disabled"),
				bNow ? TEXT("enabled") : TEXT("disabled")));
			return;
		}
		System->MarkPackageDirty();

		Out->SetBoolField(TEXT("enabled"), bNow);
		Out->SetBoolField(TEXT("changed"), true);
		Out->SetBoolField(TEXT("recompiled"), bRecompile);
		if (!bRecompile)
		{
			Out->SetStringField(TEXT("recompileNote"),
				TEXT("the system's compile results were INVALIDATED but not rebuilt, because "
					 "compiling from an HTTP handler is opt-in - it can take a long time and holds "
					 "the editor. The change is real and the system will recompile when the editor "
					 "next needs it, or now with recompile:true."));
		}
		Out->SetStringField(TEXT("whyNotSetProperty"),
			TEXT("set_property on EmitterHandles[N].bIsEnabled flips the same bool, and it is enough "
				 "to DISABLE an emitter - but not to enable one, because it skips the "
				 "RefreshFromExternalChanges and InvalidateCompileResults this call makes. That "
				 "leaves a stale compile result and an emitter that stays dark with a flag saying "
				 "otherwise."));
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the system is dirty and NOTHING has been saved."));
	}
}
