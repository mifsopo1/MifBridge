// Game Features / Modular Gameplay — reading which feature plugins exist and what state they are in.
//
// This is the one subsystem on docs/13_COMPETITOR_GAP_MAP.md that is ABOUT MODDING, which is DDS2's
// whole case: a Game Feature plugin is how content gets added to a shipped game without patching the
// base game. DDS2 already ships one - DDS2Casino, the Casino DLC - so this is not speculative surface.
//
// GUARDED BY MIF_WITH_GAMEFEATURES, same IK Rig pattern as Niagara: registered on every engine,
// compiling a named refusal where the plugin is absent.
//
// ============================================================================================
// THE VERSION SPLIT IN THIS FILE, and why the state is DERIVED rather than read.
// ============================================================================================
//
// UGameFeaturesSubsystem differs materially between 5.3 and 5.7, and it differs in the OPPOSITE
// direction from the usual trap in this codebase. The usual shape is 5.3 having something that 5.7
// deleted (GetAssetsByClass(FName), IsPendingKillOrUnreachable). Here it is 5.7 that has grown members
// 5.3 never had - so the danger is writing against the NEWER engine and breaking the older one, which
// is the build the SDK actually runs on.
//
// 5.7-ONLY, DELIBERATELY NOT USED HERE:
//     EGameFeaturePluginState GetPluginState(const FString& PluginURL) const;   // 5.7:686
//     void ForEachGameFeature(TFunctionRef<void(FGameFeatureInfo&&)>) const;    // 5.7:467
//     bool GetPluginURLByName(FStringView, FString&) const;                     // 5.7:637
//     bool IsGameFeaturePluginMounted(const FString&) const;                    // 5.7:556
//
// GetPluginState is the tempting one: it returns the exact state enum in a single call, which is
// precisely what this endpoint wants to report. It does not exist in 5.3 at all. So `state` here is
// DERIVED from the four predicates that DO exist in both, and is reported as a derived ladder rather
// than dressed up as the engine's own answer:
//     bool IsGameFeaturePluginInstalled  (const FString&)        5.3:413  5.7:553
//     bool IsGameFeaturePluginRegistered (const FString&, bool)  5.3:416  5.7:559
//     bool IsGameFeaturePluginLoaded     (const FString&)        5.3:419  5.7:562
//     bool IsGameFeaturePluginActive     (const FString&, bool)  5.3:438  5.7:595
//     static FString GetPluginURL_FileProtocol(const FString&)   5.3:387  5.7:509
// All five verified present in both trees. The four booleans are reported RAW alongside the derived
// name, so a caller who disagrees with the derivation can compute their own.
//
// Two more differences worth recording because they look alarming and are harmless:
//   * 5.3 declares `class GAMEFEATURES_API UGameFeaturesSubsystem` (whole-class export); 5.7 declares
//     `class UGameFeaturesSubsystem` with per-member UE_API. That is a declaration-side change - the
//     calling code is identical either way.
//   * The plugin MOVED: 5.3 has it under Plugins/Experimental/, 5.7 under Plugins/Runtime/. It was
//     promoted out of experimental. The module name is unchanged, so Build.cs does not care.
//
// Enumeration goes through IPluginManager, not the subsystem, because GetDiscoveredPlugins() and
// GetDescriptor() are identical in both trees while the subsystem's enumeration APIs are not.

#include "MifBridgeHandlers.h"

#if MIF_WITH_GAMEFEATURES
#include "GameFeaturesSubsystem.h"
#include "Engine/Engine.h"          // GEngine - see MifGetGameFeaturesSubsystem below
#include "Interfaces/IPluginManager.h"
#include "PluginDescriptor.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_GAMEFEATURES
	static void MifNoGameFeatures(const TSharedRef<FJsonObject>& Out)
	{
		Fail(Out, TEXT("this engine build has no GameFeatures plugin, so there is nothing to read. The "
					   "endpoint exists on every build deliberately - a missing endpoint would tell you "
					   "nothing, while this tells you the plugin is what is missing."));
	}
	void H_list_game_feature_plugins(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoGameFeatures(Out);
	}
	void H_describe_game_feature_plugin(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoGameFeatures(Out);
	}
#else

	// The four predicates, gathered once. Every one of these exists in both 5.3 and 5.7 - see the file
	// header for line numbers. Nothing in here may reach for GetPluginState.
	struct FMifGameFeatureState
	{
		bool bInstalled = false;
		bool bRegistered = false;
		bool bLoaded = false;
		bool bActive = false;
		bool bKnownToSubsystem = false;
	};

	// DO NOT call UGameFeaturesSubsystem::Get() here. In 5.3 it is declared inline as
	//     static UGameFeaturesSubsystem& Get() { return *GEngine->GetEngineSubsystem<...>(); }
	// which dereferences the result UNCHECKED. If GEngine is null or the subsystem was never created,
	// that is a null dereference inside a handler - and a handler runs synchronously on the game thread
	// inside the HTTP ticker, so it takes the whole editor down rather than returning an error. That is
	// the same shape as PM-013, where a CastChecked failure terminated the process instead of failing
	// the call. Fetching the pointer ourselves costs nothing and cannot crash.
	static UGameFeaturesSubsystem* MifGetGameFeaturesSubsystem()
	{
		return GEngine ? GEngine->GetEngineSubsystem<UGameFeaturesSubsystem>() : nullptr;
	}

	static FMifGameFeatureState MifReadGameFeatureState(const FString& PluginURL)
	{
		FMifGameFeatureState S;
		UGameFeaturesSubsystem* Sub = MifGetGameFeaturesSubsystem();
		// An absent subsystem leaves every predicate false, which reads as NotLoaded. That is the
		// truthful answer - nothing is loaded if there is nothing to load it - and the caller can tell
		// it apart from a real NotLoaded via subsystemAvailable on the response.
		if (!Sub) { return S; }
		S.bInstalled = Sub->IsGameFeaturePluginInstalled(PluginURL);
		S.bRegistered = Sub->IsGameFeaturePluginRegistered(PluginURL);
		S.bLoaded = Sub->IsGameFeaturePluginLoaded(PluginURL);
		S.bActive = Sub->IsGameFeaturePluginActive(PluginURL);
		S.bKnownToSubsystem = S.bInstalled || S.bRegistered || S.bLoaded || S.bActive;
		return S;
	}

	// The DERIVED state name. Reported as derived, never as the engine's own answer, because on 5.3
	// there IS no engine answer to report - GetPluginState does not exist there.
	static const TCHAR* MifDeriveGameFeatureState(const FMifGameFeatureState& S)
	{
		// Highest rung first: the states are a ladder, and a plugin that is Active is also Loaded,
		// Registered and Installed.
		if (S.bActive)     { return TEXT("Active"); }
		if (S.bLoaded)     { return TEXT("Loaded"); }
		if (S.bRegistered) { return TEXT("Registered"); }
		if (S.bInstalled)  { return TEXT("Installed"); }
		return TEXT("NotLoaded");
	}

	static void MifWriteGameFeatureState(const TSharedRef<FJsonObject>& Row,
										 const FMifGameFeatureState& S)
	{
		Row->SetStringField(TEXT("state"), MifDeriveGameFeatureState(S));
		// The RAW predicates alongside the derived name. A caller who disagrees with the ladder can
		// compute their own answer instead of being stuck with this one.
		TSharedRef<FJsonObject> Flags = MakeShared<FJsonObject>();
		Flags->SetBoolField(TEXT("installed"), S.bInstalled);
		Flags->SetBoolField(TEXT("registered"), S.bRegistered);
		Flags->SetBoolField(TEXT("loaded"), S.bLoaded);
		Flags->SetBoolField(TEXT("active"), S.bActive);
		Row->SetObjectField(TEXT("stateFlags"), Flags);
		Row->SetBoolField(TEXT("knownToSubsystem"), S.bKnownToSubsystem);
		// Without this, an absent subsystem and a genuinely-not-loaded plugin produce IDENTICAL output:
		// all four predicates false, state NotLoaded. They mean completely different things, so the
		// difference is reported rather than left for the caller to fail to notice.
		Row->SetBoolField(TEXT("subsystemAvailable"), MifGetGameFeaturesSubsystem() != nullptr);
	}

	// A plugin counts as a game feature if the SUBSYSTEM knows it, or if its descriptor is marked
	// ExplicitlyLoaded - the flag that makes a plugin loadable on demand rather than at startup, which
	// is what a game feature is. Both are reported via `detectedBy` so the caller can see WHICH test
	// matched rather than trusting a bare yes.
	static bool MifIsGameFeaturePlugin(const TSharedRef<IPlugin>& Plugin,
									   const FMifGameFeatureState& State, FString& OutDetectedBy)
	{
		const bool bExplicit = Plugin->GetDescriptor().bExplicitlyLoaded;
		if (State.bKnownToSubsystem && bExplicit) { OutDetectedBy = TEXT("subsystem+descriptor"); return true; }
		if (State.bKnownToSubsystem)             { OutDetectedBy = TEXT("subsystem"); return true; }
		if (bExplicit)                           { OutDetectedBy = TEXT("descriptor"); return true; }
		return false;
	}

	// --- list_game_feature_plugins -------------------------------------------
	//   in:  { nameContains?, activeOnly? }
	//   out: { count, totalDiscoveredPlugins, gameFeaturePluginCount, plugins:[...] }
	void H_list_game_feature_plugins(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("nameContains"), TEXT("activeOnly") },
			TEXT("nameContains (substring filter on the plugin name); activeOnly (default false)"),
			{ { TEXT("name"), TEXT("this lists them all - describe_game_feature_plugin is the one that takes a single name") },
			  { TEXT("path"), TEXT("game feature plugins are addressed by NAME, not asset path") },
			  { TEXT("activate"), TEXT("this endpoint is read-only; activating a game feature changes what is loaded in the running editor and the bridge does not do that") } }))
		{
			return;
		}

		const FString NameContains = JStr(In, TEXT("nameContains"));
		const bool bActiveOnly = JBool(In, TEXT("activeOnly"), false);

		TArray<TSharedRef<IPlugin>> All = IPluginManager::Get().GetDiscoveredPlugins();
		TArray<TSharedPtr<FJsonValue>> Rows;
		int32 GameFeatureCount = 0;

		for (const TSharedRef<IPlugin>& Plugin : All)
		{
			const FString PluginName = Plugin->GetName();
			// GetPluginURL_FileProtocol is the portable way to name a plugin to the subsystem: the
			// by-name lookup (GetPluginURLByName) is 5.7-only.
			const FString URL = UGameFeaturesSubsystem::GetPluginURL_FileProtocol(
				Plugin->GetDescriptorFileName());
			const FMifGameFeatureState State = MifReadGameFeatureState(URL);

			FString DetectedBy;
			if (!MifIsGameFeaturePlugin(Plugin, State, DetectedBy)) { continue; }
			++GameFeatureCount;

			if (!NameContains.IsEmpty() && !PluginName.Contains(NameContains)) { continue; }
			if (bActiveOnly && !State.bActive) { continue; }

			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("name"), PluginName);
			Row->SetStringField(TEXT("url"), URL);
			Row->SetStringField(TEXT("detectedBy"), DetectedBy);
			Row->SetBoolField(TEXT("enabled"), Plugin->IsEnabled());
			Row->SetStringField(TEXT("friendlyName"), Plugin->GetDescriptor().FriendlyName);
			Row->SetStringField(TEXT("category"), Plugin->GetDescriptor().Category);
			MifWriteGameFeatureState(Row, State);
			Rows.Add(MakeShared<FJsonValueObject>(Row));
		}

		Out->SetNumberField(TEXT("count"), Rows.Num());
		// Both totals, so neither a filter nor the game-feature test can look like completeness.
		Out->SetNumberField(TEXT("gameFeaturePluginCount"), GameFeatureCount);
		Out->SetNumberField(TEXT("totalDiscoveredPlugins"), All.Num());
		Out->SetArrayField(TEXT("plugins"), Rows);
		// Said once, here, rather than repeated on every row: the state name is ours, not the engine's.
		Out->SetStringField(TEXT("stateNote"),
			TEXT("`state` is DERIVED from the four installed/registered/loaded/active predicates, which "
				 "are the only ones present in both UE 5.3 and 5.7 - the engine's own GetPluginState is "
				 "5.7-only. stateFlags carries the raw predicates if you want to derive it differently."));
		if (!MifGetGameFeaturesSubsystem())
		{
			Out->SetStringField(TEXT("note"),
				TEXT("the GameFeatures module is linked but its engine subsystem is NOT available, so "
					 "every state below was read as false. Any plugin listed here was detected from its "
					 "descriptor alone. This is not the same as 'nothing is loaded'."));
		}
		else if (GameFeatureCount == 0)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("no game feature plugins found. That is a real answer, not an error - most projects "
					 "have none. A plugin counts here if the GameFeatures subsystem knows it or its "
					 "descriptor is marked ExplicitlyLoaded."));
		}
		else if (Rows.Num() == 0)
		{
			// The project HAS game features; the filter just excluded them all. Without this, an empty
			// `plugins` array reads the same as "this project has none" - and the two are opposite
			// answers. gameFeaturePluginCount already carries the truth, but a caller has to notice it.
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("the project has %d game feature plugin(s) but none matched the filter%s - "
					 "gameFeaturePluginCount is the real number, not count."),
				GameFeatureCount,
				bActiveOnly ? TEXT(" (activeOnly was set, so anything not currently Active was excluded)")
							: TEXT("")));
		}
	}

	// --- describe_game_feature_plugin ----------------------------------------
	//   in:  { name (aliases: plugin, pluginName) }
	//   out: { name, url, state, stateFlags, enabled, descriptor{...}, modules:[...] }
	void H_describe_game_feature_plugin(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("name"), TEXT("plugin"), TEXT("pluginName") },
			TEXT("name (aliases: plugin, pluginName) - a plugin name like 'MyGameFeature'"),
			{ { TEXT("nameContains"), TEXT("describe takes one exact name - list_game_feature_plugins is the one that filters") },
			  { TEXT("url"), TEXT("this takes the plugin NAME; the file-protocol URL is derived for you and returned") } }))
		{
			return;
		}

		const FString Name = JStrAny(In, { TEXT("name"), TEXT("plugin"), TEXT("pluginName") });
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required - a plugin name. list_game_feature_plugins enumerates them."));
			return;
		}

		TSharedPtr<IPlugin> Found = IPluginManager::Get().FindPlugin(Name);
		if (!Found.IsValid())
		{
			Fail(Out, FString::Printf(
				TEXT("no plugin named '%s' was discovered. list_game_feature_plugins enumerates the game "
					 "feature ones; note this takes a PLUGIN NAME (like 'MyGameFeature'), not a path."), *Name));
			return;
		}

		const TSharedRef<IPlugin> Plugin = Found.ToSharedRef();
		const FPluginDescriptor& Desc = Plugin->GetDescriptor();
		const FString URL = UGameFeaturesSubsystem::GetPluginURL_FileProtocol(
			Plugin->GetDescriptorFileName());
		const FMifGameFeatureState State = MifReadGameFeatureState(URL);

		FString DetectedBy;
		const bool bIsGameFeature = MifIsGameFeaturePlugin(Plugin, State, DetectedBy);

		Out->SetStringField(TEXT("name"), Plugin->GetName());
		Out->SetStringField(TEXT("url"), URL);
		Out->SetBoolField(TEXT("isGameFeature"), bIsGameFeature);
		// A plugin that exists but is NOT a game feature is answered, not refused - "this is not a game
		// feature plugin" is the useful answer to that question.
		Out->SetStringField(TEXT("detectedBy"), bIsGameFeature ? DetectedBy : TEXT("none"));
		Out->SetBoolField(TEXT("enabled"), Plugin->IsEnabled());
		Out->SetStringField(TEXT("baseDir"), Plugin->GetBaseDir());
		Out->SetStringField(TEXT("descriptorFile"), Plugin->GetDescriptorFileName());
		MifWriteGameFeatureState(Out, State);

		TSharedRef<FJsonObject> D = MakeShared<FJsonObject>();
		D->SetStringField(TEXT("friendlyName"), Desc.FriendlyName);
		D->SetStringField(TEXT("description"), Desc.Description);
		D->SetStringField(TEXT("category"), Desc.Category);
		D->SetStringField(TEXT("createdBy"), Desc.CreatedBy);
		D->SetStringField(TEXT("versionName"), Desc.VersionName);
		D->SetNumberField(TEXT("version"), Desc.Version);
		// bExplicitlyLoaded is THE descriptor flag that makes a plugin a game feature candidate - it is
		// what lets it be loaded on demand instead of at startup. Verified 5.3:132, 5.7:148.
		D->SetBoolField(TEXT("explicitlyLoaded"), Desc.bExplicitlyLoaded);
		// NOT a bool, despite the .uplugin JSON key reading like one. FPluginDescriptor declares
		//     EPluginEnabledByDefault EnabledByDefault;   // 5.3:102, 5.7:118, enum identical in both
		// with three states - Unspecified, Enabled, Disabled - and Unspecified is a REAL state meaning
		// the descriptor did not say, which a bool cannot express. Reported as a string for that reason.
		// (Assuming the bool from the JSON key name is what broke this build once - read the struct.)
		D->SetStringField(TEXT("enabledByDefault"),
			Desc.EnabledByDefault == EPluginEnabledByDefault::Enabled  ? TEXT("Enabled")  :
			Desc.EnabledByDefault == EPluginEnabledByDefault::Disabled ? TEXT("Disabled") :
																			 TEXT("Unspecified"));
		D->SetBoolField(TEXT("canContainContent"), Desc.bCanContainContent);
		D->SetBoolField(TEXT("isBetaVersion"), Desc.bIsBetaVersion);
		Out->SetObjectField(TEXT("descriptor"), D);

		TArray<TSharedPtr<FJsonValue>> Modules;
		for (const FModuleDescriptor& M : Desc.Modules)
		{
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("name"), M.Name.ToString());
			Modules.Add(MakeShared<FJsonValueObject>(Row));
		}
		Out->SetNumberField(TEXT("moduleCount"), Modules.Num());
		Out->SetArrayField(TEXT("modules"), Modules);

		Out->SetStringField(TEXT("stateNote"),
			TEXT("`state` is DERIVED from the installed/registered/loaded/active predicates - the "
				 "engine's own GetPluginState is 5.7-only and absent on 5.3."));
		if (!bIsGameFeature)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("this plugin exists but is not a game feature: the GameFeatures subsystem does not "
					 "know it and its descriptor is not marked ExplicitlyLoaded."));
		}
	}
#endif   // MIF_WITH_GAMEFEATURES
}
