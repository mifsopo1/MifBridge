// MifBridge — EDITOR UI INVOCATION: invoke the ACTION, never the pixel.
//
// The question this file answers is "how do I reach an editor affordance that has no callable API —
// a third-party plugin's toolbar button, a custom editor window, a Details-panel row nobody exposed".
// The research (docs/audit/work/R2_UI_AUTOMATION.md) settled it: invoke the bound FUIAction / tab
// spawner / exec command. Pixel clicking through the AutomationDriver is NOT built here and the
// reasons are recorded in docs/audit/06_IMPLEMENTED.md "Batch O", not lost in a commit message.
//
// THE THREE THINGS THAT SHAPED EVERY DESIGN DECISION BELOW
//
// 1. THE AUTOMATION DRIVER CANNOT BE DRIVEN FROM A HANDLER, AT ALL. IDriverSequence::Perform()
//    blocks on TFuture::Get() (DriverSequence.cpp:1835-1838) for a promise that only the step engine
//    fulfils, and that engine re-arms itself on FTSTicker::GetCoreTicker() with a strictly positive
//    delay (StepExecutor.cpp:142/151), so step N+1 needs a LATER frame (Ticker.cpp:103 — an element
//    whose FireTime > CurrentTime is deferred). Our handlers run INSIDE
//    FTSTicker::GetCoreTicker().Tick(), because FHttpServerModule is an FTSTickerObjectBase
//    (HttpServerModule.h:23-25, and MifBridgeServer.cpp:229-265 deliberately runs the endpoint
//    inline there rather than via AsyncTask). Blocking in a handler therefore blocks the very ticker
//    that would advance the sequence. It does not even reach step 0: Execute() posts an
//    AsyncTask(ENamedThreads::GameThread, ...) (StepExecutor.cpp:57-80) that is never pumped while
//    the game thread is parked in Get(). Deadlock before the first click.
//
// 2. EVERYTHING HERE CAN OPEN A MODAL, AND A MODAL TAKES THE WHOLE BRIDGE DOWN. The HTTP server is a
//    game-thread ticker; a modal window spins its own loop, the tick stops, the socket stops being
//    read and every call times out with no response (docs/02_GOTCHAS.md §8 — this happened live, a
//    BlueprintAssist welcome popup blocked a whole build+prove cycle). For an ARBITRARY third-party
//    FUIAction there is no inventory to pre-check against, so the mitigation is different in kind:
//    every invoking endpoint here is confirm-gated, offers a dryRun that resolves everything and
//    fires nothing, checks CanExecute first, and refuses a small VERIFIED deny-list of commands whose
//    engine implementation opens a modal unconditionally. Each endpoint states its own modal
//    disposition in its comment block and in its response `modalHazard` field.
//
// 3. FSlateApplication::GetModifierKeys() READS THE REAL PLATFORM KEYBOARD (SlateApplication.cpp:
//    3034-3037 -> PlatformApplication->GetModifierKeys()). A synthesised FModifierKeysState in an
//    FKeyEvent is ignored by every consumer written the way BlueprintAssist's input processor is
//    (BlueprintAssistInputProcessor.cpp:1118-1123 builds its FInputChord from
//    FSlateApplication::Get().GetModifierKeys(), NOT from the event). So a faked Ctrl+H arrives as
//    bare H. send_editor_key therefore REFUSES a modified chord unless the human is physically
//    holding those modifiers — it never silently sends the unmodified key.
//
// HOW A COMMAND LIST IS OBTAINED — the one non-obvious mechanism in this file.
//
// FInputBindingManager enumerates COMMANDS (FUICommandInfo) but stores no command LISTS: its
// RegisterCommandList is a pure broadcast that keeps nothing (InputBindingManager.cpp:561-569).
// FUICommandList::TryExecuteAction needs a live list, and the obvious sources
// (FLevelEditorModule::GetGlobalLevelEditorActions, IMainFrameModule::GetMainFrameCommandBindings)
// are in modules MifBridge does not depend on — LevelEditor and MainFrame are PRIVATE deps /
// DynamicallyLoadedModuleNames of UnrealEd (UnrealEd.Build.cs:147, :206, :215), so they are NOT
// transitively reachable and pulling them in would be a Build.cs change this batch deliberately did
// not make.
//
// The route that needs no new module: FInputBindingManager::OnRegisterCommandList is a PUBLIC
// multicast member (InputBindingManager.h, declared above the class's `private:`), and five engine
// sites broadcast onto it:
//     Editor/LevelEditor/Private/LevelEditor.cpp:281        (FLevelEditorModule::StartupModule)
//     Editor/MainFrame/Private/MainFrameModule.cpp:600      (FMainFrameModule::StartupModule)
//     Editor/LevelEditor/Private/SLevelViewport.cpp:1381    (per viewport widget)
//     Editor/ContentBrowser/Private/SContentBrowser.cpp:678 (per content browser widget)
//     Editor/Sequencer/Private/Sequencer.cpp:668-669        (per sequencer instance)
// MifBridge loads at PostEngineInit, which LaunchEngineLoop.cpp:4838-4840 runs inside
// EngineLoop.Init() — and UnrealEdGlobals.cpp:111 calls EngineLoop.Init() BEFORE :171 loads
// MainFrame and builds the editor UI. So subscribing in FMifBridgeModule::StartupModule happens
// before all five broadcasts. This closes R2_UI_AUTOMATION.md §9 UNVERIFIED item 3.
//
// The cache is WEAK on purpose: a closed viewport's or content browser's list must not be kept alive
// by us, and a stale entry must be reported as gone rather than invoked. Anything registered BEFORE
// we subscribed is invisible, and the response says so instead of implying the list is complete.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Dom/JsonValue.h"
#include "Editor.h"                                   // GEditor
#include "Engine/Engine.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Commands/InputBindingManager.h"
#include "Framework/Commands/InputChord.h"
#include "Framework/Commands/UIAction.h"
#include "Framework/Commands/UICommandInfo.h"
#include "Framework/Commands/UICommandList.h"
#include "Framework/Docking/TabManager.h"
#include "Framework/Docking/WorkspaceItem.h"
#include "Framework/MultiBox/MultiBoxDefs.h"           // EMultiBlockType
#include "GenericPlatform/GenericApplication.h"        // FModifierKeysState
#include "HAL/IConsoleManager.h"
#include "Input/Events.h"                              // FKeyEvent
#include "InputCoreTypes.h"                            // FKey / EKeys
#include "Subsystems/AssetEditorSubsystem.h"           // UAssetEditorSubsystem / IAssetEditorInstance
#include "ToolMenu.h"
#include "ToolMenuContext.h"                           // FToolMenuContext is CONSTRUCTED here, and
                                                       // ToolMenuEntry.h only forward-declares it
#include "ToolMenuEntry.h"
#include "ToolMenuSection.h"
#include "ToolMenus.h"
#include "UObject/Object.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/SWidget.h"
#include "Widgets/SWindow.h"

namespace MifBridge
{
	namespace
	{
		// ── Command-list cache ────────────────────────────────────────────────────────────────────
		// Names are Ui-prefixed rather than generic because a unity build merges every unnamed
		// namespace in a blob into ONE namespace ([namespace.unnamed]/1) — PM-005. Nothing here is
		// duplicated anywhere else in the module; do not copy it, promote it.

		TMap<FName, TArray<TWeakPtr<FUICommandList>>>& UiCommandListCache()
		{
			static TMap<FName, TArray<TWeakPtr<FUICommandList>>> Cache;
			return Cache;
		}

		bool& UiObserverActive()      { static bool bActive = false;   return bActive; }
		FDelegateHandle& UiRegHandle()   { static FDelegateHandle H;   return H; }
		FDelegateHandle& UiUnregHandle() { static FDelegateHandle H;   return H; }

		void UiOnCommandListRegistered(const FName Context, TSharedRef<FUICommandList> List)
		{
			TArray<TWeakPtr<FUICommandList>>& Lists = UiCommandListCache().FindOrAdd(Context);
			// Drop dead entries on every touch so the cache cannot grow without bound across a long
			// session of opening and closing viewports / content browsers.
			Lists.RemoveAll([](const TWeakPtr<FUICommandList>& W) { return !W.IsValid(); });
			for (const TWeakPtr<FUICommandList>& W : Lists)
			{
				if (W.Pin() == List) { return; }
			}
			Lists.Add(List);
			UE_LOG(LogMifBridge, Verbose, TEXT("command list registered for context '%s' (%d cached)"),
				*Context.ToString(), Lists.Num());
		}

		void UiOnCommandListUnregistered(const FName Context, TSharedRef<FUICommandList> List)
		{
			if (TArray<TWeakPtr<FUICommandList>>* Lists = UiCommandListCache().Find(Context))
			{
				Lists->RemoveAll([&List](const TWeakPtr<FUICommandList>& W)
				{
					const TSharedPtr<FUICommandList> Pinned = W.Pin();
					return !Pinned.IsValid() || Pinned == List;
				});
			}
		}

		// ── Small shared shapes ───────────────────────────────────────────────────────────────────

		TSharedRef<FJsonObject> UiChordJson(const TSharedRef<const FInputChord>& Chord)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("key"), Chord->Key.IsValid() ? Chord->Key.ToString() : FString());
			J->SetBoolField(TEXT("ctrl"),  Chord->bCtrl != 0);
			J->SetBoolField(TEXT("alt"),   Chord->bAlt != 0);
			J->SetBoolField(TEXT("shift"), Chord->bShift != 0);
			J->SetBoolField(TEXT("cmd"),   Chord->bCmd != 0);
			J->SetStringField(TEXT("text"), Chord->GetInputText().ToString());
			J->SetBoolField(TEXT("valid"), Chord->IsValidChord());
			return J;
		}

		// Deliberately a switch, not StaticEnum<EMultiBlockType>(): the reflection lookup would add a
		// failure mode (a null UEnum) for a nine-value enum whose spelling is fixed in
		// MultiBoxDefs.h:50-61.
		const TCHAR* UiBlockTypeName(EMultiBlockType Type)
		{
			switch (Type)
			{
			case EMultiBlockType::None:               return TEXT("none");
			case EMultiBlockType::ButtonRow:          return TEXT("buttonRow");
			case EMultiBlockType::EditableText:       return TEXT("editableText");
			case EMultiBlockType::Heading:            return TEXT("heading");
			case EMultiBlockType::MenuEntry:          return TEXT("menuEntry");
			case EMultiBlockType::Separator:          return TEXT("separator");
			case EMultiBlockType::ToolBarButton:      return TEXT("toolBarButton");
			case EMultiBlockType::ToolBarComboButton: return TEXT("toolBarComboButton");
			case EMultiBlockType::Widget:             return TEXT("widget");
			default:                                  return TEXT("unknown");
			}
		}

		// ── The modal deny-list ───────────────────────────────────────────────────────────────────
		// A SEED, not a guarantee, and the docs say so in those words. Every entry below was read out
		// of D:/UE532 and cites the line that opens the dialog; the first three are UNCONDITIONAL
		// (the modal opens the moment the action runs), the fourth is conditional but its failure
		// branch is the likely one for an unattended agent. There is no general way to know whether
		// an arbitrary third-party FUIAction opens a modal — R2_UI_AUTOMATION.md §6 item 1 — which is
		// why confirm/dryRun/CanExecute carry the real weight and this list is only the part that can
		// be made exact. Extend it as instances are found; an entry costs one line.
		struct FUiModalDenyEntry
		{
			const TCHAR* Context;
			const TCHAR* Command;
			const TCHAR* Reason;
		};

		const FUiModalDenyEntry* UiModalDenyList(int32& OutNum)
		{
			static const FUiModalDenyEntry Entries[] =
			{
				{ TEXT("MainFrame"), TEXT("AboutUnrealEd"),
				  TEXT("FSlateApplication::AddModalWindow (MainFrameActions.cpp:725) — unconditional modal; ")
				  TEXT("the bridge's game-thread ticker stops until a human closes it") },
				{ TEXT("MainFrame"), TEXT("CreditsUnrealEd"),
				  TEXT("FSlateApplication::AddModalWindow (MainFrameActions.cpp:753) — unconditional modal") },
				{ TEXT("MainFrame"), TEXT("ZipUpProject"),
				  TEXT("IDesktopPlatform::SaveFileDialog (MainFrameActions.cpp:470) — an OS-modal file ")
				  TEXT("browser, and the zip itself blocks the game thread for minutes") },
				{ TEXT("MainFrame"), TEXT("OpenIDE"),
				  TEXT("FMessageDialog::Open on failure (MainFrameActions.cpp:443) — reached whenever the ")
				  TEXT("solution cannot be opened, which is the normal case in a headless/agent session") },
			};
			OutNum = static_cast<int32>(UE_ARRAY_COUNT(Entries));
			return Entries;
		}

		const TCHAR* UiFindModalDenyReason(const FString& Context, const FString& Command)
		{
			int32 Num = 0;
			const FUiModalDenyEntry* Entries = UiModalDenyList(Num);
			for (int32 i = 0; i < Num; ++i)
			{
				if (Context.Equals(Entries[i].Context, ESearchCase::IgnoreCase)
					&& Command.Equals(Entries[i].Command, ESearchCase::IgnoreCase))
				{
					return Entries[i].Reason;
				}
			}
			return nullptr;
		}

		// ── Known tab ids ─────────────────────────────────────────────────────────────────────────
		// NOT a claim that these exist — every one is PROBED live with FTabManager::HasTabSpawner
		// (TabManager.h:981, public) and the response reports what actually answered true. They are a
		// seed for the probe, because the registry itself cannot be enumerated: FTabSpawner and
		// HasTabSpawnerFor are both under `protected:` at TabManager.h:1113-1117 despite carrying
		// SLATE_API. BlueprintAssist reached the same conclusion independently and hardcodes its own
		// list with the comment "Nomad unlisted tabs - search for '->RegisterNomadTabSpawner('"
		// (BlueprintAssist/Private/BlueprintAssistWidgets/BAOpenWindowMenu.cpp:529) — this seed is
		// drawn from that file plus FLevelEditorTabIds (LevelEditor.h:40-62) plus the three tabs
		// BlueprintAssist itself registers.
		const TCHAR* const* UiKnownTabIds(int32& OutNum)
		{
			static const TCHAR* const Ids[] =
			{
				// Global / nomad tabs — registered on FGlobalTabmanager, so manager:"global" reaches them.
				TEXT("OutputLog"), TEXT("MessageLog"), TEXT("ReferenceViewer"), TEXT("UndoHistory"),
				TEXT("PluginsEditor"), TEXT("ProjectLauncher"), TEXT("MaterialAnalyzer"),
				TEXT("VisualLogger"), TEXT("ConfigEditor"), TEXT("DebuggerApp"), TEXT("Search"),
				TEXT("LocalizationDashboard"), TEXT("AutomationWindow"), TEXT("DeviceManager"),
				TEXT("SessionFrontend"), TEXT("ContentBrowserTab1"), TEXT("LevelEditor"),
				TEXT("WidgetReflector"), TEXT("StatsViewer"), TEXT("ClassViewerApp"),
				// BlueprintAssist's own windows — the "custom editor window" case, verified in-tree at
				// BlueprintAssistModule.cpp:109/120 and BADebugMenu.cpp:289.
				TEXT("BADebugMenu"), TEXT("BAWelcomeScreen"), TEXT("BASettingChanges"),
				// Level-editor minor tabs. These live on the LEVEL EDITOR's tab manager, not the global
				// one, so they answer HasTabSpawner only via manager:"majorTab", majorTab:"LevelEditor".
				TEXT("LevelEditorSelectionDetails"), TEXT("LevelEditorSelectionDetails2"),
				TEXT("LevelEditorSceneOutliner"), TEXT("LevelEditorToolBox"), TEXT("PlacementBrowser"),
				TEXT("LevelEditorLayerBrowser"), TEXT("WorldSettingsTab"), TEXT("LevelEditorViewport"),
				TEXT("LevelEditorStatsViewer"), TEXT("Sequencer"),
				// Blueprint-editor tabs. manager:"assetEditor" + asset:<path> reaches these — this is the
				// route to the DETAILS PANEL of an open Blueprint (FBlueprintEditorTabs, cited in
				// BAOpenWindowMenu.cpp).
				TEXT("Inspector"), TEXT("MyBlueprint"), TEXT("Palette"), TEXT("BlueprintDefaults"),
				TEXT("CompilerResults"), TEXT("FindResults"), TEXT("Components"), TEXT("Bookmarks"),
				TEXT("Debug"), TEXT("ReplaceNodeReferences"), TEXT("SCSViewport"), TEXT("Toolbar"),
			};
			OutNum = static_cast<int32>(UE_ARRAY_COUNT(Ids));
			return Ids;
		}

		// ── Tab-manager resolution ────────────────────────────────────────────────────────────────
		// Three managers, all reachable with no module dependency MifBridge does not already have:
		//   global      FGlobalTabmanager::Get()                                  (TabManager.h:1203)
		//   majorTab    FindExistingLiveTab -> GetTabManagerForMajorTab           (:920, :1257)
		//   assetEditor UAssetEditorSubsystem -> IAssetEditorInstance::GetAssociatedTabManager()
		//                                       (AssetEditorSubsystem.h:138, :77)
		// GetTabManagerForMajorTab is the escape hatch that makes the LEVEL EDITOR's own tab manager
		// reachable without the LevelEditor module: the major tab is a global nomad tab, and the
		// global manager knows which child manager was created for it.
		TSharedPtr<FTabManager> UiResolveTabManager(const FString& Kind, const FString& MajorTab,
			const FString& AssetQuery, const TSharedRef<FJsonObject>& Out, FString& OutError)
		{
			FString K = Kind;
			if (K.IsEmpty()) { K = TEXT("global"); }
			K = K.ToLower();

			if (K == TEXT("global"))
			{
				Out->SetStringField(TEXT("managerResolved"), TEXT("FGlobalTabmanager"));
				return FGlobalTabmanager::Get();
			}

			if (K == TEXT("majortab"))
			{
				if (MajorTab.IsEmpty())
				{
					OutError = TEXT("majorTab is required when manager=\"majorTab\" — the id of an OPEN major tab whose child tab manager you want (e.g. majorTab:\"LevelEditor\")");
					return nullptr;
				}
				const TSharedPtr<SDockTab> Major = FGlobalTabmanager::Get()->FindExistingLiveTab(FTabId(FName(*MajorTab)));
				if (!Major.IsValid())
				{
					OutError = FString::Printf(
						TEXT("majorTab '%s' is not OPEN — GetTabManagerForMajorTab needs the live SDockTab, so the major tab must already exist. Open it first (invoke_editor_tab {manager:\"global\", tabId:\"%s\"}) and retry."),
						*MajorTab, *MajorTab);
					return nullptr;
				}
				const TSharedPtr<FTabManager> Child = FGlobalTabmanager::Get()->GetTabManagerForMajorTab(Major);
				if (!Child.IsValid())
				{
					OutError = FString::Printf(
						TEXT("majorTab '%s' is open but owns no child tab manager — it is a leaf tab, not a major tab hosting minor tabs. Use manager:\"global\"."),
						*MajorTab);
					return nullptr;
				}
				Out->SetStringField(TEXT("managerResolved"), FString::Printf(TEXT("child tab manager of major tab '%s'"), *MajorTab));
				return Child;
			}

			if (K == TEXT("asseteditor"))
			{
				if (!GEditor)
				{
					OutError = TEXT("no GEditor — manager=\"assetEditor\" needs a running editor");
					return nullptr;
				}
				UAssetEditorSubsystem* Sub = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
				if (!Sub)
				{
					OutError = TEXT("UAssetEditorSubsystem unavailable");
					return nullptr;
				}
				// Always report what IS open: on failure this is the difference between "no editor for
				// that asset" and "nothing is open at all", and it is the discovery half for `asset`.
				TArray<UObject*> Edited = Sub->GetAllEditedAssets();
				TArray<TSharedPtr<FJsonValue>> OpenArr;
				for (UObject* Obj : Edited)
				{
					if (Obj) { OpenArr.Add(MakeShared<FJsonValueString>(Obj->GetPathName())); }
				}
				Out->SetArrayField(TEXT("openAssetEditors"), OpenArr);

				if (AssetQuery.IsEmpty())
				{
					OutError = TEXT("asset is required when manager=\"assetEditor\" — the object path (or a unique substring of it) of an asset whose editor is OPEN. openAssetEditors[] in this response lists what is open right now.");
					return nullptr;
				}
				UObject* Match = nullptr;
				int32 Matches = 0;
				for (UObject* Obj : Edited)
				{
					if (!Obj) { continue; }
					const FString Path = Obj->GetPathName();
					if (Path.Equals(AssetQuery, ESearchCase::IgnoreCase)
						|| Obj->GetName().Equals(AssetQuery, ESearchCase::IgnoreCase))
					{
						Match = Obj; Matches = 1; break;   // exact wins outright
					}
					if (Path.Contains(AssetQuery))
					{
						++Matches;
						if (!Match) { Match = Obj; }
					}
				}
				if (!Match)
				{
					OutError = FString::Printf(
						TEXT("no OPEN asset editor matches asset '%s' — openAssetEditors[] in this response lists what is open. Open the asset first (open_blueprint) and retry."),
						*AssetQuery);
					return nullptr;
				}
				if (Matches > 1)
				{
					OutError = FString::Printf(
						TEXT("asset '%s' matches %d open asset editors — pass the full object path. openAssetEditors[] lists them."),
						*AssetQuery, Matches);
					return nullptr;
				}
				IAssetEditorInstance* Instance = Sub->FindEditorForAsset(Match, /*bFocusIfOpen*/ false);
				if (!Instance)
				{
					OutError = FString::Printf(TEXT("asset '%s' is listed as edited but FindEditorForAsset returned null"), *Match->GetPathName());
					return nullptr;
				}
				const TSharedPtr<FTabManager> Mgr = Instance->GetAssociatedTabManager();
				if (!Mgr.IsValid())
				{
					OutError = FString::Printf(
						TEXT("the editor for '%s' (%s) exposes no tab manager — GetAssociatedTabManager returned null, which some toolkits legitimately do"),
						*Match->GetPathName(), *Instance->GetEditorName().ToString());
					return nullptr;
				}
				Out->SetStringField(TEXT("managerResolved"),
					FString::Printf(TEXT("tab manager of %s editing %s"), *Instance->GetEditorName().ToString(), *Match->GetPathName()));
				Out->SetStringField(TEXT("assetResolved"), Match->GetPathName());
				return Mgr;
			}

			OutError = FString::Printf(
				TEXT("manager '%s' is not one of: global (FGlobalTabmanager — nomad/global tabs), majorTab (the child manager of an OPEN major tab, e.g. majorTab:\"LevelEditor\"), assetEditor (the tab manager of an OPEN asset editor, with asset:<path>)"),
				*Kind);
			return nullptr;
		}

		// Walk the workspace-menu tree a tab manager exposes and collect every FTabSpawnerEntry in it.
		// PARTIAL BY CONSTRUCTION and reported as such: a spawner only appears here if it was given a
		// group with FTabSpawnerEntry::SetGroup, and the engine's nomad spawners are grouped into the
		// WorkspaceMenuStructure module's own root, not into this manager's local root.
		void UiCollectWorkspaceTabIds(const TSharedRef<FWorkspaceItem>& Item, TArray<FString>& OutIds, int32 Depth)
		{
			if (Depth > 8) { return; }                    // the tree is a tree; the guard is for safety, not need
			for (const TSharedRef<FWorkspaceItem>& Child : Item->GetChildItems())
			{
				if (const TSharedPtr<FTabSpawnerEntry> Spawner = Child->AsSpawnerEntry())
				{
					OutIds.AddUnique(Spawner->GetTabType().ToString());
				}
				UiCollectWorkspaceTabIds(Child, OutIds, Depth + 1);
			}
		}

		// The one place that turns "the caller asked for modifiers" into "the platform actually has
		// them down". See fact 3 in the file header: the FModifierKeysState carried by the event is
		// NOT what BlueprintAssist-shaped consumers read.
		struct FUiModifierRequest
		{
			bool bAny = false;
			bool bCtrl = false, bAlt = false, bShift = false, bCmd = false;
		};

		bool UiReadModifiers(const TSharedRef<FJsonObject>& In, FUiModifierRequest& Out, FString& OutError)
		{
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (!In->TryGetObjectField(TEXT("modifiers"), Obj) || !Obj || !Obj->IsValid())
			{
				return true;                              // absent is not an error
			}
			static const TCHAR* Accepted[] = { TEXT("ctrl"), TEXT("alt"), TEXT("shift"), TEXT("cmd") };
			for (const auto& Pair : (*Obj)->Values)
			{
				bool bKnown = false;
				for (const TCHAR* A : Accepted) { if (Pair.Key.Equals(A, ESearchCase::IgnoreCase)) { bKnown = true; break; } }
				if (!bKnown)
				{
					OutError = FString::Printf(
						TEXT("modifiers.%s is not a recognised modifier — accepted keys are ctrl, alt, shift, cmd (booleans). A typo here would otherwise be silently ignored."),
						*Pair.Key);
					return false;
				}
				if (Pair.Value.IsValid() && Pair.Value->Type != EJson::Boolean)
				{
					OutError = FString::Printf(TEXT("modifiers.%s must be a boolean, not %s"),
						*Pair.Key, JsonTypeName(Pair.Value->Type));
					return false;
				}
			}
			Out.bCtrl  = JBool(Obj->ToSharedRef(), TEXT("ctrl"),  false);
			Out.bAlt   = JBool(Obj->ToSharedRef(), TEXT("alt"),   false);
			Out.bShift = JBool(Obj->ToSharedRef(), TEXT("shift"), false);
			Out.bCmd   = JBool(Obj->ToSharedRef(), TEXT("cmd"),   false);
			Out.bAny   = Out.bCtrl || Out.bAlt || Out.bShift || Out.bCmd;
			return true;
		}

		// Read a JSON array of strings STRICTLY: a non-string element is an error naming the index,
		// never a silently skipped entry.
		bool UiReadStringArray(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
			TArray<FString>& Out, FString& OutError)
		{
			const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
			// JArray records a present-but-wrong-typed field; absent still returns quietly, which is
			// what this helper's "true means no error" contract wants.
			if (!JArray(In, Field, Arr) || !Arr) { return true; }
			for (int32 i = 0; i < Arr->Num(); ++i)
			{
				const TSharedPtr<FJsonValue>& V = (*Arr)[i];
				if (!V.IsValid() || V->Type != EJson::String)
				{
					OutError = FString::Printf(TEXT("%s[%d] must be a string, not %s"),
						Field, i, V.IsValid() ? JsonTypeName(V->Type) : TEXT("null"));
					return false;
				}
				Out.AddUnique(V->AsString());
			}
			return true;
		}
	}

	// ── Observer lifecycle — called from FMifBridgeModule::Startup/ShutdownModule ─────────────────

	void SubscribeCommandListObserver()
	{
		if (UiObserverActive()) { return; }
		FInputBindingManager& Mgr = FInputBindingManager::Get();
		UiRegHandle()   = Mgr.OnRegisterCommandList.AddStatic(&UiOnCommandListRegistered);
		UiUnregHandle() = Mgr.OnUnregisterCommandList.AddStatic(&UiOnCommandListUnregistered);
		UiObserverActive() = true;
		UE_LOG(LogMifBridge, Log, TEXT("subscribed to FInputBindingManager::OnRegisterCommandList — invoke_editor_command can reach command lists registered from now on"));
	}

	void UnsubscribeCommandListObserver()
	{
		if (!UiObserverActive()) { return; }
		FInputBindingManager& Mgr = FInputBindingManager::Get();
		Mgr.OnRegisterCommandList.Remove(UiRegHandle());
		Mgr.OnUnregisterCommandList.Remove(UiUnregHandle());
		UiRegHandle().Reset();
		UiUnregHandle().Reset();
		UiCommandListCache().Empty();
		UiObserverActive() = false;
	}

	bool AreCommandListsObserved() { return UiObserverActive(); }

	void GetCachedCommandListContexts(TArray<FName>& OutContexts)
	{
		OutContexts.Reset();
		for (const auto& Pair : UiCommandListCache())
		{
			for (const TWeakPtr<FUICommandList>& W : Pair.Value)
			{
				if (W.IsValid()) { OutContexts.AddUnique(Pair.Key); break; }
			}
		}
	}

	void GetCachedCommandLists(FName Context, TArray<TSharedPtr<const FUICommandList>>& OutLists)
	{
		OutLists.Reset();
		if (const TArray<TWeakPtr<FUICommandList>>* Lists = UiCommandListCache().Find(Context))
		{
			for (const TWeakPtr<FUICommandList>& W : *Lists)
			{
				if (const TSharedPtr<FUICommandList> Pinned = W.Pin()) { OutLists.Add(Pinned); }
			}
		}
	}

	// ══ list_editor_commands ═════════════════════════════════════════════════════════════════════
	//   in : { context?, command?, filter?, includeUnbound? = true, includeCanExecute? = false,
	//          includeConsole? = false, consolePrefix?, menu?, section?, limit? = 400 }
	//   out: { ok, contexts:[{context, description, commandCount, cachedCommandLists,
	//                         commands:[{name, label, description, chord, altChord, bound,
	//                                    mappedInLists, canExecute, canExecuteKnown, modalDenied}]}],
	//          contextCount, matchedCommands, emittedCommands, truncated,
	//          commandListSource:{...}, console?:{...}, menu?:{...} }
	//
	// THREE HALVES, each labelled with what it can and cannot see:
	//   a) BINDING CONTEXTS — genuinely ENUMERABLE. FInputBindingManager::GetKnownInputContexts
	//      (InputBindingManager.h:45) + GetCommandInfosFromContext (:126). This reaches every
	//      third-party plugin's TCommands<> with ZERO coupling: BlueprintAssist registers
	//      "BlueprintAssistCommands" (BlueprintAssistCommands.h:13-21) and every one of its ~150
	//      commands lists here without MifBridge linking against it.
	//   b) CONSOLE OBJECTS — enumerable, opt-in, prefix-filtered.
	//      IConsoleManager::ForEachConsoleObjectThatStartsWith (IConsoleManager.h:984).
	//   c) ONE NAMED MENU — probe-only, opt-in. UToolMenus::IsMenuRegistered (ToolMenus.h:140) then
	//      CollectHierarchy (:216). The full menu SET is not enumerated: UToolMenus::Menus is a
	//      private member (:390-391) and reading it would need reflection into another module's
	//      private state for a listing. CollectHierarchy is used rather than GenerateMenu because
	//      GenerateMenu allocates a UToolMenu and runs third-party dynamic-section construct
	//      delegates (ToolMenus.cpp:1881-1901) — listing must not have side effects.
	//
	// MODAL DISPOSITION: this endpoint invokes NOTHING. The only third-party code it can reach is a
	// command's FCanExecuteAction predicate, and only when includeCanExecute:true — which is why that
	// is opt-in and off by default. UToolMenu::FindEntry is PRIVATE (ToolMenu.h:102-106, exported and
	// unusable); this walks the public Sections array and FToolMenuSection::FindEntry instead.
	void H_list_editor_commands(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("context"), TEXT("command"), TEXT("filter"), TEXT("includeUnbound"),
			  TEXT("includeCanExecute"), TEXT("includeConsole"), TEXT("consolePrefix"),
			  TEXT("menu"), TEXT("section"), TEXT("limit") },
			TEXT("context, command, filter, includeUnbound (default true), includeCanExecute (default false), ")
			TEXT("includeConsole (default false), consolePrefix, menu, section, limit (default 400)"),
			{ { TEXT("tabId"), TEXT("tabs are a different registry — use invoke_editor_tab {probe:true}") },
			  { TEXT("entry"), TEXT("pass menu (and optionally section); every entry in it is listed") } }))
		{
			return;
		}

		const FString WantContext = JStr(In, TEXT("context"));
		const FString WantCommand = JStr(In, TEXT("command"));
		const FString Filter      = JStr(In, TEXT("filter"));
		const bool bIncludeUnbound = JBool(In, TEXT("includeUnbound"), true);
		const bool bIncludeCanExec = JBool(In, TEXT("includeCanExecute"), false);
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 400), 1, 20000);

		// (a) binding contexts
		TArray<TSharedPtr<FBindingContext>> Contexts;
		FInputBindingManager::Get().GetKnownInputContexts(Contexts);

		int32 Matched = 0, Emitted = 0;
		TArray<TSharedPtr<FJsonValue>> ContextArr;
		for (const TSharedPtr<FBindingContext>& Ctx : Contexts)
		{
			if (!Ctx.IsValid()) { continue; }
			const FName ContextName = Ctx->GetContextName();
			if (!WantContext.IsEmpty() && !ContextName.ToString().Equals(WantContext, ESearchCase::IgnoreCase))
			{
				continue;
			}

			TArray<TSharedPtr<FUICommandInfo>> Infos;
			FInputBindingManager::Get().GetCommandInfosFromContext(ContextName, Infos);

			TArray<TSharedPtr<const FUICommandList>> Lists;
			GetCachedCommandLists(ContextName, Lists);

			TArray<TSharedPtr<FJsonValue>> CmdArr;
			for (const TSharedPtr<FUICommandInfo>& Info : Infos)
			{
				if (!Info.IsValid()) { continue; }
				const FString Name  = Info->GetCommandName().ToString();
				const FString Label = Info->GetLabel().ToString();
				if (!WantCommand.IsEmpty() && !Name.Equals(WantCommand, ESearchCase::IgnoreCase)) { continue; }
				if (!Filter.IsEmpty()
					&& !Name.Contains(Filter, ESearchCase::IgnoreCase)
					&& !Label.Contains(Filter, ESearchCase::IgnoreCase))
				{
					continue;
				}
				const TSharedRef<const FInputChord> Primary = Info->GetActiveChord(EMultipleKeyBindingIndex::Primary);
				const bool bBound = Primary->IsValidChord();
				if (!bIncludeUnbound && !bBound) { continue; }

				++Matched;
				if (Emitted >= Limit) { continue; }

				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("name"), Name);
				J->SetStringField(TEXT("label"), Label);
				J->SetStringField(TEXT("description"), Info->GetDescription().ToString());
				J->SetObjectField(TEXT("chord"), UiChordJson(Primary));
				J->SetObjectField(TEXT("altChord"), UiChordJson(Info->GetActiveChord(EMultipleKeyBindingIndex::Secondary)));
				J->SetBoolField(TEXT("bound"), bBound);
				J->SetStringField(TEXT("inputText"), Info->GetInputText().ToString());

				int32 MappedIn = 0;
				bool bCanExecute = false, bCanExecuteKnown = false;
				for (const TSharedPtr<const FUICommandList>& List : Lists)
				{
					if (List.IsValid() && List->IsActionMapped(Info))
					{
						++MappedIn;
						if (bIncludeCanExec && !bCanExecuteKnown)
						{
							// Runs the command's FCanExecuteAction — third-party code. Opt-in for
							// exactly that reason; see the endpoint's modal disposition above.
							bCanExecute = List->CanExecuteAction(Info.ToSharedRef());
							bCanExecuteKnown = true;
						}
					}
				}
				J->SetNumberField(TEXT("mappedInLists"), MappedIn);
				J->SetBoolField(TEXT("canExecuteKnown"), bCanExecuteKnown);
				if (bCanExecuteKnown) { J->SetBoolField(TEXT("canExecute"), bCanExecute); }
				else                  { J->SetField(TEXT("canExecute"), MakeShared<FJsonValueNull>()); }
				if (const TCHAR* Deny = UiFindModalDenyReason(ContextName.ToString(), Name))
				{
					J->SetBoolField(TEXT("modalDenied"), true);
					J->SetStringField(TEXT("modalDeniedReason"), Deny);
				}
				CmdArr.Add(MakeShared<FJsonValueObject>(J));
				++Emitted;
			}

			TSharedRef<FJsonObject> CtxJson = MakeShared<FJsonObject>();
			CtxJson->SetStringField(TEXT("context"), ContextName.ToString());
			CtxJson->SetStringField(TEXT("description"), Ctx->GetContextDesc().ToString());
			CtxJson->SetNumberField(TEXT("commandCount"), Infos.Num());
			CtxJson->SetNumberField(TEXT("cachedCommandLists"), Lists.Num());
			CtxJson->SetArrayField(TEXT("commands"), CmdArr);
			ContextArr.Add(MakeShared<FJsonValueObject>(CtxJson));
		}

		if (!WantContext.IsEmpty() && ContextArr.Num() == 0)
		{
			TArray<FString> Known;
			for (const TSharedPtr<FBindingContext>& C : Contexts)
			{
				if (C.IsValid()) { Known.Add(C->GetContextName().ToString()); }
			}
			Known.Sort();
			Fail(Out, FString::Printf(
				TEXT("context '%s' is not a known binding context%s. Call list_editor_commands with no context to enumerate all %d of them."),
				*WantContext, *NearMissSuggestion(Known, WantContext), Known.Num()));
			return;
		}

		Out->SetArrayField(TEXT("contexts"), ContextArr);
		Out->SetNumberField(TEXT("contextCount"), ContextArr.Num());
		Out->SetNumberField(TEXT("knownContextCount"), Contexts.Num());
		Out->SetNumberField(TEXT("matchedCommands"), Matched);
		Out->SetNumberField(TEXT("emittedCommands"), Emitted);
		Out->SetBoolField(TEXT("truncated"), Emitted < Matched);

		// Where an invokable command list can come from, stated honestly.
		{
			TArray<FName> CachedContexts;
			GetCachedCommandListContexts(CachedContexts);
			TArray<TSharedPtr<FJsonValue>> Arr;
			for (const FName& N : CachedContexts) { Arr.Add(MakeShared<FJsonValueString>(N.ToString())); }
			TSharedRef<FJsonObject> Src = MakeShared<FJsonObject>();
			Src->SetBoolField(TEXT("observed"), AreCommandListsObserved());
			Src->SetArrayField(TEXT("contextsWithLists"), Arr);
			Src->SetStringField(TEXT("mechanism"),
				TEXT("FInputBindingManager::OnRegisterCommandList, subscribed in FMifBridgeModule::StartupModule (PostEngineInit, before the editor UI is built)"));
			Src->SetStringField(TEXT("limitation"),
				TEXT("FInputBindingManager stores no command lists (InputBindingManager.cpp:561-569 only broadcasts), so a list registered BEFORE MifBridge subscribed is invisible and a context absent from contextsWithLists cannot be invoked through invoke_editor_command's default route — pass menu/section/entry to take the ToolMenus route instead."));
			Out->SetObjectField(TEXT("commandListSource"), Src);
		}

		// (b) console objects — opt-in
		if (JBool(In, TEXT("includeConsole"), false))
		{
			const FString Prefix = JStr(In, TEXT("consolePrefix"));
			TArray<TSharedPtr<FJsonValue>> Arr;
			int32 Total = 0;
			IConsoleManager::Get().ForEachConsoleObjectThatStartsWith(
				FConsoleObjectVisitor::CreateLambda([&Arr, &Total, Limit](const TCHAR* Name, IConsoleObject* Obj)
				{
					++Total;
					if (Arr.Num() >= Limit || !Obj) { return; }
					TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
					J->SetStringField(TEXT("name"), Name);
					J->SetStringField(TEXT("help"), Obj->GetHelp() ? Obj->GetHelp() : TEXT(""));
					J->SetStringField(TEXT("kind"), Obj->AsCommand() ? TEXT("command") : TEXT("variable"));
					Arr.Add(MakeShared<FJsonValueObject>(J));
				}), Prefix.IsEmpty() ? TEXT("") : *Prefix);

			TSharedRef<FJsonObject> Console = MakeShared<FJsonObject>();
			Console->SetStringField(TEXT("prefix"), Prefix);
			Console->SetNumberField(TEXT("matched"), Total);
			Console->SetNumberField(TEXT("emitted"), Arr.Num());
			Console->SetBoolField(TEXT("truncated"), Arr.Num() < Total);
			Console->SetArrayField(TEXT("objects"), Arr);
			Console->SetStringField(TEXT("runWith"), TEXT("run_console {command:\"<name> <args>\"} — same process, structured result in execOutput"));
			Out->SetObjectField(TEXT("console"), Console);
		}

		// (c) one named menu — opt-in, probe-only
		const FString MenuName = JStr(In, TEXT("menu"));
		if (!MenuName.IsEmpty())
		{
			UToolMenus* Menus = UToolMenus::Get();
			TSharedRef<FJsonObject> MenuJson = MakeShared<FJsonObject>();
			MenuJson->SetStringField(TEXT("name"), MenuName);
			MenuJson->SetBoolField(TEXT("enumerable"), false);
			MenuJson->SetStringField(TEXT("enumerationNote"),
				TEXT("menu NAMES cannot be listed: UToolMenus keeps its registry in the private member Menus (ToolMenus.h:390-391) and exposes no enumerator. This half is PROBE-ONLY — name a menu and its sections/entries are reported."));
			if (!Menus)
			{
				MenuJson->SetBoolField(TEXT("registered"), false);
				MenuJson->SetStringField(TEXT("error"), TEXT("UToolMenus::Get() returned null"));
				Out->SetObjectField(TEXT("menu"), MenuJson);
			}
			else if (!Menus->IsMenuRegistered(FName(*MenuName)))
			{
				MenuJson->SetBoolField(TEXT("registered"), false);
				MenuJson->SetStringField(TEXT("hint"),
					TEXT("try a full menu path — 'LevelEditor.MainMenu', 'LevelEditor.MainMenu.Tools', 'LevelEditor.LevelEditorToolBar', 'AssetEditor.BlueprintEditor.ToolBar', 'ContentBrowser.AssetContextMenu'"));
				Out->SetObjectField(TEXT("menu"), MenuJson);
			}
			else
			{
				const FString WantSection = JStr(In, TEXT("section"));
				const FToolMenuContext EmptyContext;
				TArray<TSharedPtr<FJsonValue>> SectionArr;
				int32 EntryTotal = 0;
				for (UToolMenu* Menu : Menus->CollectHierarchy(FName(*MenuName)))
				{
					if (!Menu) { continue; }
					for (const FToolMenuSection& Section : Menu->Sections)
					{
						if (!WantSection.IsEmpty() && !Section.Name.ToString().Equals(WantSection, ESearchCase::IgnoreCase))
						{
							continue;
						}
						TArray<TSharedPtr<FJsonValue>> EntryArr;
						for (const FToolMenuEntry& Entry : Section.Blocks)
						{
							++EntryTotal;
							TSharedRef<FJsonObject> E = MakeShared<FJsonObject>();
							E->SetStringField(TEXT("name"), Entry.Name.ToString());
							E->SetStringField(TEXT("label"), Entry.Label.IsSet() ? Entry.Label.Get().ToString() : FString());
							E->SetStringField(TEXT("type"), UiBlockTypeName(Entry.Type));
							E->SetBoolField(TEXT("isSubMenu"), Entry.IsSubMenu());
							// GetActionForCommand with an EMPTY context is the only public probe: it
							// returns non-null exactly when the entry is command-backed AND its command
							// list is reachable. Entries built from a raw FUIAction or an
							// FToolMenuStringCommand keep both in private, non-reflected members
							// (ToolMenuEntry.h:214, :216) and are honestly labelled unreachable rather
							// than reported as invokable and then failing.
							TSharedPtr<const FUICommandList> EntryList;
							const FUIAction* Action = Entry.GetActionForCommand(EmptyContext, EntryList);
							E->SetStringField(TEXT("invokeKind"),
								Action ? TEXT("command")
								       : (Entry.IsSubMenu() ? TEXT("submenu")
								                            : (Entry.Type == EMultiBlockType::Separator || Entry.Type == EMultiBlockType::Heading
								                               ? TEXT("decoration") : TEXT("unreachableOrToolUIAction"))));
							E->SetBoolField(TEXT("hasCommandList"), EntryList.IsValid());
							EntryArr.Add(MakeShared<FJsonValueObject>(E));
						}
						TSharedRef<FJsonObject> S = MakeShared<FJsonObject>();
						S->SetStringField(TEXT("name"), Section.Name.ToString());
						S->SetStringField(TEXT("ownerMenu"), Menu->GetMenuName().ToString());
						S->SetNumberField(TEXT("entryCount"), EntryArr.Num());
						S->SetArrayField(TEXT("entries"), EntryArr);
						SectionArr.Add(MakeShared<FJsonValueObject>(S));
					}
				}
				MenuJson->SetBoolField(TEXT("registered"), true);
				MenuJson->SetNumberField(TEXT("entryCount"), EntryTotal);
				MenuJson->SetArrayField(TEXT("sections"), SectionArr);
				MenuJson->SetStringField(TEXT("listedVia"),
					TEXT("UToolMenus::CollectHierarchy (ToolMenus.h:216) + the public Sections/Blocks arrays. GenerateMenu is deliberately NOT used: it allocates a UToolMenu and runs third-party dynamic-section construct delegates (ToolMenus.cpp:1881-1901), i.e. listing would have side effects."));
				Out->SetObjectField(TEXT("menu"), MenuJson);
			}
		}

		Out->SetStringField(TEXT("invokeWith"),
			TEXT("invoke_editor_command {context, command, dryRun:true} to check, then {confirm:true} to fire"));
	}

	// ══ invoke_editor_command ════════════════════════════════════════════════════════════════════
	//   in : { context, command, menu?, section?, entry?, dryRun? = false, confirm? = false,
	//          allowKnownModal? = false }
	//   out: { ok, context, command, label, resolvedVia, listSource, actionFound, canExecute,
	//          invoked, dryRun, modalHazard, note }
	//
	// Executes the FUIAction a menu entry or toolbar button is bound to — the same delegate a mouse
	// click ends in, minus hit-testing, minus the focus change, minus the cursor
	// (UIAction.h:124/133, public and inline). TryExecuteAction (UICommandList.h:148) is used rather
	// than ExecuteAction (:133) because it checks CanExecute first; ExecuteAction's own comment at
	// :129 says "It is assumed at this point that CanExecuteAction was already checked".
	//
	// RESOLUTION ORDER, each step failing closed with a distinct error:
	//   1. FInputBindingManager::FindCommandInContext(context, command)  (InputBindingManager.h:101)
	//   2. a live FUICommandList:
	//        menu/section/entry given -> UToolMenus::CollectHierarchy -> the PUBLIC Sections array ->
	//          FToolMenuSection::FindEntry (ToolMenuSection.h:59) -> FToolMenuEntry::GetActionForCommand
	//          (ToolMenuEntry.h:138).  UToolMenu::FindEntry is PRIVATE (ToolMenu.h:102-106) — exported
	//          and unusable, one of the traps this project has paid for before.
	//        otherwise -> the OnRegisterCommandList cache (file header)
	//   3. CanExecuteAction, then TryExecuteAction
	//   4. no command but the entry carries an FToolUIAction -> TryExecuteToolUIAction (:149)
	//
	// MODAL DISPOSITION — THE WHOLE RISK. The action is arbitrary third-party code. If it opens a
	// modal the game-thread ticker stops, this HTTP server stops reading its socket, and THIS CALL
	// NEVER RETURNS (docs/02_GOTCHAS.md §8). An invoke_editor_command that hangs is the signature.
	// Diagnose from OUTSIDE the process:
	//     powershell -NoProfile -Command "Get-Process UnrealEditor | Select-Object Id,MainWindowTitle"
	// a MainWindowTitle that is not the normal editor title names the dialog. Mitigations, in order
	// of how much they actually buy:
	//   * confirm:true is REQUIRED to fire. Without it (and without dryRun) the call FAILS naming the
	//     parameter — it never answers ok:true having done nothing.
	//   * dryRun:true resolves the command, the list and CanExecute and fires nothing.
	//   * CanExecute is checked and a disabled command is refused rather than invoked.
	//   * a small VERIFIED deny-list of commands whose engine implementation opens a modal
	//     unconditionally is refused unless allowKnownModal:true.
	// There is no way to make this safe in general from inside the process, and the tool description
	// says so.
	//
	// BUCKET: SELF-MANAGED (no outer transaction). The invoked action is arbitrary editor code that
	// may open its own FScopedTransaction, run a full Blueprint compile, or BE undo/redo — and
	// beginning an undo inside an open transaction trips the engine's own ensure(!GIsTransacting)
	// (TransBuffer.h:74), while a compile captured by an undo step restores a dead CDO and crashes.
	// Self-managed means RunEndpoint opens nothing and the action behaves exactly as it does when a
	// human clicks it. It also makes the endpoint compile-heavy, so `batch` refuses it, which is
	// correct for the same reason.
	void H_invoke_editor_command(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("context"), TEXT("command"), TEXT("menu"), TEXT("section"), TEXT("entry"),
			  TEXT("dryRun"), TEXT("confirm"), TEXT("allowKnownModal") },
			TEXT("context, command, menu, section, entry, dryRun, confirm, allowKnownModal"),
			{ { TEXT("commandList"), TEXT("not a parameter — the list is found automatically (cache), or via menu/section/entry") },
			  { TEXT("key"), TEXT("sending a keystroke is send_editor_key, not this endpoint") } }))
		{
			return;
		}

		const FString ContextStr = JStr(In, TEXT("context"));
		const FString CommandStr = JStr(In, TEXT("command"));
		if (ContextStr.IsEmpty())
		{
			Fail(Out, TEXT("context is required — the binding-context name (e.g. \"BlueprintAssistCommands\", \"LevelEditor\", \"MainFrame\"). list_editor_commands with no arguments enumerates every one."));
			return;
		}
		if (CommandStr.IsEmpty())
		{
			Fail(Out, FString::Printf(TEXT("command is required — the FUICommandInfo name inside context '%s'. list_editor_commands {context:\"%s\"} lists them."), *ContextStr, *ContextStr));
			return;
		}

		const bool bDryRun = JBool(In, TEXT("dryRun"), false);
		const bool bConfirm = JBool(In, TEXT("confirm"), false);
		const bool bAllowKnownModal = JBool(In, TEXT("allowKnownModal"), false);

		Out->SetStringField(TEXT("context"), ContextStr);
		Out->SetStringField(TEXT("command"), CommandStr);
		Out->SetBoolField(TEXT("dryRun"), bDryRun);
		Out->SetBoolField(TEXT("invoked"), false);
		Out->SetStringField(TEXT("modalHazard"),
			TEXT("an invoked action is arbitrary third-party code and MAY open a modal, which stops the game-thread ticker this HTTP server runs on — a call that never returns IS the symptom (docs/02_GOTCHAS.md §8). Check from outside: Get-Process UnrealEditor | Select-Object Id,MainWindowTitle"));

		// 1. the command
		const TSharedPtr<FUICommandInfo> Command =
			FInputBindingManager::Get().FindCommandInContext(FName(*ContextStr), FName(*CommandStr));
		if (!Command.IsValid())
		{
			TArray<TSharedPtr<FUICommandInfo>> Infos;
			FInputBindingManager::Get().GetCommandInfosFromContext(FName(*ContextStr), Infos);
			if (Infos.Num() == 0)
			{
				TArray<TSharedPtr<FBindingContext>> Contexts;
				FInputBindingManager::Get().GetKnownInputContexts(Contexts);
				TArray<FString> Known;
				for (const TSharedPtr<FBindingContext>& C : Contexts)
				{
					if (C.IsValid()) { Known.Add(C->GetContextName().ToString()); }
				}
				Known.Sort();
				Fail(Out, FString::Printf(
					TEXT("binding context '%s' has no commands — it is probably not a registered context%s. list_editor_commands enumerates all %d."),
					*ContextStr, *NearMissSuggestion(Known, ContextStr), Known.Num()));
				return;
			}
			TArray<FString> Names;
			for (const TSharedPtr<FUICommandInfo>& I : Infos) { if (I.IsValid()) { Names.Add(I->GetCommandName().ToString()); } }
			Names.Sort();
			Fail(Out, FString::Printf(
				TEXT("command '%s' does not exist in context '%s'%s. list_editor_commands {context:\"%s\"} lists all %d."),
				*CommandStr, *ContextStr, *NearMissSuggestion(Names, CommandStr), *ContextStr, Names.Num()));
			return;
		}
		Out->SetStringField(TEXT("label"), Command->GetLabel().ToString());
		Out->SetStringField(TEXT("description"), Command->GetDescription().ToString());
		Out->SetObjectField(TEXT("chord"), UiChordJson(Command->GetActiveChord(EMultipleKeyBindingIndex::Primary)));

		// 2. the deny-list, checked BEFORE anything is executed and before confirm is even consulted,
		//    so a caller who passed confirm:true by reflex still cannot take the bridge down with one
		//    of the four known-unconditional cases.
		if (const TCHAR* DenyReason = UiFindModalDenyReason(ContextStr, CommandStr))
		{
			Out->SetBoolField(TEXT("modalDenied"), true);
			Out->SetStringField(TEXT("modalDeniedReason"), DenyReason);
			if (!bAllowKnownModal)
			{
				Fail(Out, FString::Printf(
					TEXT("%s.%s is on MifBridge's verified modal deny-list and was NOT invoked: %s. Pass allowKnownModal:true ONLY if a human is watching the editor and can dismiss the dialog — otherwise the bridge stops answering until someone clicks."),
					*ContextStr, *CommandStr, DenyReason));
				return;
			}
		}

		// 3. a live command list
		const FString MenuName    = JStr(In, TEXT("menu"));
		const FString SectionName = JStr(In, TEXT("section"));
		const FString EntryName   = JStr(In, TEXT("entry"));
		TSharedPtr<const FUICommandList> List;
		FToolMenuEntry* FoundEntry = nullptr;
		FString ResolvedVia;

		if (!MenuName.IsEmpty() || !EntryName.IsEmpty())
		{
			if (MenuName.IsEmpty())
			{
				Fail(Out, TEXT("menu is required when entry is given — there is no way to search every menu, because UToolMenus::Menus is private (ToolMenus.h:390-391). Name the menu that owns the entry."));
				return;
			}
			if (EntryName.IsEmpty())
			{
				Fail(Out, FString::Printf(TEXT("entry is required when menu is given — list_editor_commands {menu:\"%s\"} lists every entry name in it."), *MenuName));
				return;
			}
			UToolMenus* Menus = UToolMenus::Get();
			if (!Menus) { Fail(Out, TEXT("UToolMenus::Get() returned null")); return; }
			if (!Menus->IsMenuRegistered(FName(*MenuName)))
			{
				Fail(Out, FString::Printf(TEXT("menu '%s' is not registered (UToolMenus::IsMenuRegistered, ToolMenus.h:140). Menu NAMES cannot be enumerated; try a full path such as 'LevelEditor.MainMenu.Tools'."), *MenuName));
				return;
			}
			const FToolMenuContext EmptyContext;
			for (UToolMenu* Menu : Menus->CollectHierarchy(FName(*MenuName)))
			{
				if (!Menu) { continue; }
				for (FToolMenuSection& Section : Menu->Sections)
				{
					if (!SectionName.IsEmpty() && !Section.Name.ToString().Equals(SectionName, ESearchCase::IgnoreCase))
					{
						continue;
					}
					if (FToolMenuEntry* Entry = Section.FindEntry(FName(*EntryName)))
					{
						FoundEntry = Entry;
						TSharedPtr<const FUICommandList> EntryList;
						if (Entry->GetActionForCommand(EmptyContext, EntryList) && EntryList.IsValid())
						{
							List = EntryList;
						}
						break;
					}
				}
				if (FoundEntry) { break; }
			}
			if (!FoundEntry)
			{
				const FString SectionClause = SectionName.IsEmpty()
					? FString()
					: FString::Printf(TEXT(" section '%s'"), *SectionName);
				Fail(Out, FString::Printf(
					TEXT("entry '%s' not found in menu '%s'%s. list_editor_commands {menu:\"%s\"} lists every section and entry."),
					*EntryName, *MenuName, *SectionClause, *MenuName));
				return;
			}
			ResolvedVia = TEXT("toolMenuEntry");
		}

		if (!List.IsValid())
		{
			TArray<TSharedPtr<const FUICommandList>> Cached;
			GetCachedCommandLists(FName(*ContextStr), Cached);
			for (const TSharedPtr<const FUICommandList>& Candidate : Cached)
			{
				if (Candidate.IsValid() && Candidate->IsActionMapped(Command))
				{
					List = Candidate;
					if (ResolvedVia.IsEmpty()) { ResolvedVia = TEXT("registeredCommandListCache"); }
					break;
				}
			}
			Out->SetNumberField(TEXT("cachedListsForContext"), Cached.Num());
		}
		Out->SetStringField(TEXT("resolvedVia"), ResolvedVia.IsEmpty() ? TEXT("none") : ResolvedVia);
		Out->SetBoolField(TEXT("actionFound"), List.IsValid());

		if (!List.IsValid())
		{
			// The FToolUIAction fallback: an entry with no command can still carry one.
			if (FoundEntry)
			{
				if (bDryRun)
				{
					Out->SetStringField(TEXT("note"),
						TEXT("no FUICommandList reaches this command; the named menu entry exists and would be tried via FToolMenuEntry::TryExecuteToolUIAction, which succeeds only for FToolUIAction entries. Entries built from a raw FUIAction or an FToolMenuStringCommand keep both in private non-reflected members (ToolMenuEntry.h:214,:216) and are not invokable through any public API."));
					Out->SetStringField(TEXT("resolvedVia"), TEXT("toolUIActionFallback"));
					return;
				}
				if (!bConfirm)
				{
					Fail(Out, TEXT("invoke_editor_command requires confirm=true to execute anything (or dryRun=true to resolve without executing). Firing an editor action can open a modal that stops the bridge, so it is never the default."));
					return;
				}
				const FToolMenuContext EmptyContext;
				const bool bToolUiOk = FoundEntry->TryExecuteToolUIAction(EmptyContext);
				Out->SetStringField(TEXT("resolvedVia"), TEXT("toolUIActionFallback"));
				Out->SetBoolField(TEXT("invoked"), bToolUiOk);
				if (!bToolUiOk)
				{
					Fail(Out, FString::Printf(
						TEXT("entry '%s' has no reachable action: it is not command-backed, and TryExecuteToolUIAction reported nothing bound — i.e. it was built from a raw FUIAction or an FToolMenuStringCommand, both of which are private, non-reflected members (ToolMenuEntry.h:214, :216) with no public accessor. This entry cannot be invoked through the ToolMenus surface at all."),
						*EntryName));
				}
				return;
			}

			TArray<FName> CachedContexts;
			GetCachedCommandListContexts(CachedContexts);
			FString ContextList;
			for (const FName& N : CachedContexts)
			{
				if (!ContextList.IsEmpty()) { ContextList += TEXT(", "); }
				ContextList += N.ToString();
			}
			Fail(Out, FString::Printf(
				TEXT("command '%s.%s' exists but no live FUICommandList maps it, so there is nothing to execute. %s Contexts with a cached list right now: [%s]. Two routes forward: (1) pass menu/section/entry to take the action off a ToolMenus entry instead; (2) send_editor_key with the command's chord, which reaches IInputProcessor-driven plugin commands (this is how BlueprintAssist's ~150 commands actually run). Why this happens: FInputBindingManager stores no command lists (InputBindingManager.cpp:561-569 only broadcasts), MifBridge caches the broadcasts it hears from PostEngineInit onward, and a list registered earlier — or never broadcast at all — is invisible."),
				*ContextStr, *CommandStr,
				AreCommandListsObserved() ? TEXT("The command-list observer IS active.") : TEXT("The command-list observer is NOT active (module startup did not subscribe)."),
				*ContextList));
			return;
		}

		// 4. CanExecute, then fire
		const bool bCanExecute = List->CanExecuteAction(Command.ToSharedRef());
		Out->SetBoolField(TEXT("canExecute"), bCanExecute);
		Out->SetBoolField(TEXT("canExecuteChecked"), true);

		if (bDryRun)
		{
			Out->SetStringField(TEXT("note"),
				bCanExecute
					? TEXT("resolved and executable — re-send with confirm:true (and drop dryRun) to invoke")
					: TEXT("resolved but CanExecute is FALSE — the editor would render this entry greyed out. Invoking is refused; fix the precondition (usually a selection or an open asset) first."));
			return;
		}

		if (!bCanExecute)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s.%s' resolved, but its FCanExecuteAction says NO — the editor draws this entry greyed out right now, and TryExecuteAction would do nothing while reporting nothing. Satisfy the precondition (a selection, an open asset, a mode) and retry. Use dryRun:true to re-check without invoking."),
				*ContextStr, *CommandStr));
			return;
		}

		if (!bConfirm)
		{
			Fail(Out, FString::Printf(
				TEXT("invoke_editor_command requires confirm=true to execute '%s.%s' (or dryRun=true to resolve without executing). Everything else about the call checked out: the command resolved, a live command list maps it, and CanExecute is true. Firing an editor action can open a modal that stops the bridge answering, so it is never the default."),
				*ContextStr, *CommandStr));
			return;
		}

		UE_LOG(LogMifBridge, Log, TEXT("invoke_editor_command: executing %s.%s (%s)"),
			*ContextStr, *CommandStr, *Out->GetStringField(TEXT("resolvedVia")));
		const bool bInvoked = List->TryExecuteAction(Command.ToSharedRef());
		Out->SetBoolField(TEXT("invoked"), bInvoked);
		if (!bInvoked)
		{
			Fail(Out, FString::Printf(
				TEXT("FUICommandList::TryExecuteAction returned false for '%s.%s' after CanExecute reported true — the action was not run. This means the mapping was removed between the two calls, or the bound FExecuteAction is unbound (FUIAction::IsBound, UIAction.h:165). Nothing was executed."),
				*ContextStr, *CommandStr));
			return;
		}
		Out->SetStringField(TEXT("note"),
			TEXT("the FUIAction ran. What it DID is the action's own business — this endpoint reports that the delegate executed, not what it changed. Verify with the endpoint that reads the thing you expected to change."));
	}

	// ══ invoke_editor_tab ════════════════════════════════════════════════════════════════════════
	//   in : { tabId?, manager? = "global", majorTab?, asset?, probe? = false, probeIds?[],
	//          includeKnownIds? = true, asInactive? = false }
	//   out: { ok, manager, managerResolved, tabId, hasSpawner, alreadyOpen, invoked, tabLabel,
	//          probes:[{tabId, hasSpawner, open}], workspaceMenuTabIds:[], enumerable:false,
	//          enumerationNote }
	//
	// FTabManager::TryInvokeTab (TabManager.h:912, SLATE_API, public) — the route BlueprintAssist
	// itself uses to open its own three windows (BlueprintAssistGlobalActions.cpp:147,
	// BlueprintAssistModule.cpp:117, BlueprintAssistToolbar.cpp:533/546/557). "Open a custom editor
	// window" is one public call, no pixels.
	//
	// THE DISCOVERY HALF, DESIGNED HONESTLY AROUND THE TRAP. Tab ids CANNOT be enumerated from a
	// plugin: the registry FTabSpawner and the lookup HasTabSpawnerFor are both under `protected:`
	// (TabManager.h:1113-1117) despite carrying SLATE_API — the same export-macro-but-inaccessible
	// shape this project has hit before. What IS public is HasTabSpawner (:981), which PROBES one id.
	// So this endpoint probes:
	//   * a curated seed list of ~45 well-known ids (see UiKnownTabIds — drawn from the engine's own
	//     tab-id constants and from BlueprintAssist, which hardcodes the same kind of list for the
	//     same reason, BAOpenWindowMenu.cpp:529),
	//   * plus anything the caller passes in probeIds[],
	//   * plus a walk of the manager's workspace-menu tree (FTabManager::GetLocalWorkspaceMenuRoot,
	//     :969 -> FWorkspaceItem::GetChildItems -> FTabSpawnerEntry::GetTabType, all public), which is
	//     a real but PARTIAL enumeration: a spawner appears there only if it was given a group.
	// Every reported hasSpawner is a LIVE answer from this editor, not a claim from the seed list.
	//
	// MODAL DISPOSITION: a tab spawner is third-party code and could in principle show a dialog while
	// constructing its widget — this is exactly how BlueprintAssist's welcome screen took the bridge
	// down once (docs/02_GOTCHAS.md §8). What can be pre-validated IS: HasTabSpawner refuses an
	// unknown id before anything is constructed, and probe:true / a bare probe call constructs
	// nothing at all. Opening a tab is reversible by closing it, which is why this endpoint is not
	// confirm-gated the way invoke_editor_command is.
	//
	// BUCKET: SELF-MANAGED — see the note on invoke_editor_command. A spawner may load assets and
	// open its own transactions; wrapping that in ours buys nothing (Ctrl-Z does not close tabs) and
	// risks capturing a compile.
	void H_invoke_editor_tab(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("tabId"), TEXT("tab"), TEXT("manager"), TEXT("majorTab"), TEXT("asset"),
			  TEXT("probe"), TEXT("probeIds"), TEXT("includeKnownIds"), TEXT("asInactive") },
			TEXT("tabId (alias: tab), manager (global|majorTab|assetEditor; default global), majorTab, ")
			TEXT("asset, probe, probeIds[], includeKnownIds (default true), asInactive"),
			{ { TEXT("command"), TEXT("invoking a bound command is invoke_editor_command") },
			  { TEXT("close"), TEXT("closing a tab is not implemented — SDockTab::RequestCloseTab can run a third-party OnCanCloseTab that shows a dialog") } }))
		{
			return;
		}

		const FString TabIdStr  = JStrAny(In, { TEXT("tabId"), TEXT("tab") });
		const FString ManagerIn = JStr(In, TEXT("manager"), TEXT("global"));
		const bool bProbeOnly   = JBool(In, TEXT("probe"), false) || TabIdStr.IsEmpty();
		const bool bAsInactive  = JBool(In, TEXT("asInactive"), false);
		const bool bIncludeKnown = JBool(In, TEXT("includeKnownIds"), true);

		TArray<FString> ProbeIds;
		{
			FString ArrError;
			if (!UiReadStringArray(In, TEXT("probeIds"), ProbeIds, ArrError)) { Fail(Out, ArrError); return; }
		}

		Out->SetStringField(TEXT("manager"), ManagerIn);
		Out->SetBoolField(TEXT("enumerable"), false);
		Out->SetStringField(TEXT("enumerationNote"),
			TEXT("tab ids cannot be ENUMERATED from a plugin: FTabManager::TabSpawner and HasTabSpawnerFor are both protected (TabManager.h:1113-1117) despite carrying SLATE_API. They can only be PROBED one at a time with the public HasTabSpawner (:981). probes[] below is a live probe of a curated seed plus anything you passed in probeIds[]; workspaceMenuTabIds[] is a real but PARTIAL enumeration (only spawners that were given a workspace-menu group appear)."));

		// 'asset' IS ONLY READ WHEN manager IS "assetEditor".
		//
		// UiResolveTabManager returns early for manager:"global" and never looks at the asset, so
		// passing one with the DEFAULT manager did nothing and said nothing. A caller who meant an
		// asset-editor tab and forgot to set manager got a global tab operation under ok:true.
		//
		// RejectUnknownParams cannot catch this: 'asset' is a perfectly valid declared parameter. It
		// is ignored by MODE, which is a blind spot in that guard - worth remembering, because any
		// endpoint whose parameters mean different things in different modes has the same hole.
		// Found by the endpoint sweep's ghost probe.
		const FString AssetIn = JStr(In, TEXT("asset"));
		if (!AssetIn.IsEmpty() && !ManagerIn.Equals(TEXT("assetEditor"), ESearchCase::IgnoreCase))
		{
			Fail(Out, FString::Printf(
				TEXT("'asset' is only used when manager is \"assetEditor\", and manager here is "
					 "\"%s\" — so the asset would have been ignored. Pass "
					 "manager:\"assetEditor\" to target that asset's tab manager, or drop 'asset' "
					 "to operate on the %s tab manager deliberately. NOTHING was done."),
				*ManagerIn, *ManagerIn));
			return;
		}

		FString MgrError;
		const TSharedPtr<FTabManager> Manager = UiResolveTabManager(
			ManagerIn, JStr(In, TEXT("majorTab")), AssetIn, Out, MgrError);
		if (!Manager.IsValid()) { Fail(Out, MgrError); return; }

		// Partial enumeration from the workspace menu.
		{
			TArray<FString> WorkspaceIds;
			UiCollectWorkspaceTabIds(Manager->GetLocalWorkspaceMenuRoot(), WorkspaceIds, 0);
			WorkspaceIds.Sort();
			TArray<TSharedPtr<FJsonValue>> Arr;
			for (const FString& S : WorkspaceIds) { Arr.Add(MakeShared<FJsonValueString>(S)); }
			Out->SetArrayField(TEXT("workspaceMenuTabIds"), Arr);
			for (const FString& S : WorkspaceIds) { ProbeIds.AddUnique(S); }
		}

		if (bIncludeKnown)
		{
			int32 KnownNum = 0;
			const TCHAR* const* Known = UiKnownTabIds(KnownNum);
			for (int32 i = 0; i < KnownNum; ++i) { ProbeIds.AddUnique(Known[i]); }
		}
		if (!TabIdStr.IsEmpty()) { ProbeIds.AddUnique(TabIdStr); }

		TArray<FString> Available;
		{
			TArray<TSharedPtr<FJsonValue>> ProbeArr;
			ProbeIds.Sort();
			for (const FString& Id : ProbeIds)
			{
				const bool bHas = Manager->HasTabSpawner(FName(*Id));
				const bool bOpen = Manager->FindExistingLiveTab(FTabId(FName(*Id))).IsValid();
				if (!bHas && !bOpen) { continue; }            // report what EXISTS, not the whole seed
				if (bHas) { Available.Add(Id); }
				TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
				P->SetStringField(TEXT("tabId"), Id);
				P->SetBoolField(TEXT("hasSpawner"), bHas);
				P->SetBoolField(TEXT("open"), bOpen);
				ProbeArr.Add(MakeShared<FJsonValueObject>(P));
			}
			Out->SetArrayField(TEXT("probes"), ProbeArr);
			Out->SetNumberField(TEXT("probed"), ProbeIds.Num());
			Out->SetNumberField(TEXT("availableCount"), Available.Num());
		}

		if (bProbeOnly)
		{
			Out->SetBoolField(TEXT("invoked"), false);
			Out->SetStringField(TEXT("note"), TabIdStr.IsEmpty()
				? TEXT("discovery only — no tabId was given, so nothing was invoked. Pass tabId to open one; probes[] lists every seed/probeIds candidate that this manager can actually spawn or already has open.")
				: TEXT("probe:true — resolved and probed, invoked nothing. Drop probe to open the tab."));
			return;
		}

		// Two statements, not `const FTabId TabId(FName(*TabIdStr));` — that form is a most-vexing-parse
		// (FName(*TabIdStr) reads as a parameter "FName* TabIdStr", making TabId a function declaration).
		const FName TabIdName(*TabIdStr);
		const FTabId TabId = FTabId(TabIdName);
		const bool bHasSpawner = Manager->HasTabSpawner(TabId.TabType);
		Out->SetStringField(TEXT("tabId"), TabIdStr);
		Out->SetBoolField(TEXT("hasSpawner"), bHasSpawner);

		const TSharedPtr<SDockTab> Existing = Manager->FindExistingLiveTab(TabId);
		Out->SetBoolField(TEXT("alreadyOpen"), Existing.IsValid());

		if (!bHasSpawner && !Existing.IsValid())
		{
			Available.Sort();
			Fail(Out, FString::Printf(
				TEXT("no tab spawner registered for '%s' on this tab manager%s. %d of the probed ids ARE spawnable here — see probes[] (hasSpawner:true). If the tab belongs to another manager, try manager:\"majorTab\" with majorTab:\"LevelEditor\" (level-editor minor tabs such as LevelEditorSelectionDetails) or manager:\"assetEditor\" with asset:<path> (Blueprint-editor tabs such as Inspector / MyBlueprint / Palette). Nothing was invoked."),
				*TabIdStr, *NearMissSuggestion(Available, TabIdStr), Available.Num()));
			return;
		}

		UE_LOG(LogMifBridge, Log, TEXT("invoke_editor_tab: TryInvokeTab('%s') on %s"),
			*TabIdStr, *JStr(Out, TEXT("managerResolved")));
		const TSharedPtr<SDockTab> Tab = Manager->TryInvokeTab(TabId, bAsInactive);
		Out->SetBoolField(TEXT("invoked"), Tab.IsValid());
		if (!Tab.IsValid())
		{
			Fail(Out, FString::Printf(
				TEXT("TryInvokeTab('%s') returned no tab. The spawner exists but declined — the commonest causes are a FCanSpawnTab that says no right now, a tab whose menu type is Hidden, or a tab permission list that denies it (FTabManager::GetTabPermissionList). Nothing was opened."),
				*TabIdStr));
			return;
		}
		Out->SetStringField(TEXT("tabLabel"), Tab->GetTabLabel().ToString());
		Out->SetBoolField(TEXT("tabActive"), Tab->IsActive());
		Out->SetBoolField(TEXT("tabForeground"), Tab->IsForeground());
	}

	// ══ send_editor_key ══════════════════════════════════════════════════════════════════════════
	//   in : { key, confirm? = false, dryRun? = false, modifiers?{ctrl,alt,shift,cmd},
	//          userIndex? = 0, isRepeat? = false, characterCode? = 0, keyCode? = 0,
	//          sendKeyUp? = true }
	//   out: { ok, key, keyValid, sent, downHandled, upHandled, modifiersRequested,
	//          modifiersReal, focusedWidget, activeWindow, note }
	//
	// FSlateApplication::ProcessKeyDownEvent (SlateApplication.h:1219, SLATE_API, public) gives the
	// registered IInputProcessors first refusal (SlateApplication.cpp:4645,
	// InputPreProcessors.HandleKeyDownEvent before anything else) — which is the ONLY route to
	// commands a plugin dispatches from its own input processor rather than from a reachable
	// FUICommandList. BlueprintAssist is exactly that shape: every one of its commands runs through
	// FBAInputProcessor::ProcessCommandBindings against command lists that are private members of BA
	// singletons (BlueprintAssistInputProcessor.cpp:1111, :145-359), so invoke_editor_command cannot
	// reach them and this can.
	//
	// THE MODIFIER REFUSAL, WHICH IS THE POINT OF THIS ENDPOINT'S DESIGN. The FModifierKeysState you
	// put in an FKeyEvent is NOT what consumers read. FSlateApplication::GetModifierKeys() goes
	// straight to the platform (SlateApplication.cpp:3034-3037), and BlueprintAssist builds its
	// FInputChord from that live state, not from the event (BlueprintAssistInputProcessor.cpp:
	// 1118-1123). A synthetic Ctrl+H is therefore evaluated as bare H — which would fire the WRONG
	// command silently. So: if modifiers are requested and the real platform keyboard does not
	// already have them down, this REFUSES. It never downgrades a chord to its unmodified key.
	// (The AutomationDriver does not fix this either: its fake modifier state is only consulted while
	// pass-through is OFF, and Enable() turns pass-through ON — AutomatedApplication.cpp:278-286
	// against AutomationDriverModule.cpp:66.)
	//
	// MODAL DISPOSITION: the key reaches whatever is bound to it, which is arbitrary code — the same
	// hazard as invoke_editor_command, with less warning, because the binding is not named in the
	// request. Hence confirm:true is required and dryRun:true validates everything (key validity,
	// modifier reality, focus) without sending. What can be pre-validated IS validated: an unknown
	// key name is refused with near misses rather than silently sending nothing.
	//
	// WHY DOWN AND UP IN THE SAME CALL: leaving a key logically down until some later frame risks a
	// stuck modifier/keystate if the bridge or the editor stops between the two halves. Command
	// dispatch happens on key DOWN for both Slate's binding path and BlueprintAssist's processor, so
	// pairing them immediately costs nothing and cannot strand the editor. sendKeyUp:false exists for
	// the rare consumer that wants the down alone, and says in the response that it left it down.
	//
	// BUCKET: SELF-MANAGED — same reasoning as invoke_editor_command.
	void H_send_editor_key(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("key"), TEXT("confirm"), TEXT("dryRun"), TEXT("modifiers"), TEXT("userIndex"),
			  TEXT("isRepeat"), TEXT("characterCode"), TEXT("keyCode"), TEXT("sendKeyUp") },
			TEXT("key, confirm, dryRun, modifiers{ctrl,alt,shift,cmd}, userIndex (default 0), ")
			TEXT("isRepeat, characterCode, keyCode, sendKeyUp (default true)"),
			{ { TEXT("text"), TEXT("typing a string is not implemented — ProcessKeyCharEvent per character goes into whatever currently has focus, which is unbounded; see the Batch O notes in docs/audit/06_IMPLEMENTED.md") },
			  { TEXT("ctrl"), TEXT("modifiers go in the modifiers object: modifiers:{ctrl:true}") } }))
		{
			return;
		}

		if (!FSlateApplication::IsInitialized())
		{
			Fail(Out, TEXT("Slate is not initialized in this process — send_editor_key needs a real editor UI (it is unavailable in a commandlet)"));
			return;
		}

		const FString KeyName = JStr(In, TEXT("key"));
		if (KeyName.IsEmpty())
		{
			Fail(Out, TEXT("key is required — an FKey name such as \"Tab\", \"F5\", \"H\", \"SpaceBar\", \"LeftMouseButton\". list_editor_commands reports each command's chord.key, which is exactly this spelling."));
			return;
		}
		const FKey Key(*KeyName);
		Out->SetStringField(TEXT("key"), KeyName);
		Out->SetBoolField(TEXT("keyValid"), Key.IsValid());
		Out->SetBoolField(TEXT("sent"), false);
		if (!Key.IsValid())
		{
			TArray<FKey> AllKeys;
			EKeys::GetAllKeys(AllKeys);
			TArray<FString> Names;
			Names.Reserve(AllKeys.Num());
			for (const FKey& K : AllKeys) { Names.Add(K.ToString()); }
			Fail(Out, FString::Printf(
				TEXT("key '%s' is not a registered FKey%s. FKey names are case-sensitive-ish engine identifiers, not display text — \"SpaceBar\" not \"Space\", \"LeftShift\" not \"Shift\". Nothing was sent."),
				*KeyName, *NearMissSuggestion(Names, KeyName)));
			return;
		}

		FUiModifierRequest Wanted;
		{
			FString ModError;
			if (!UiReadModifiers(In, Wanted, ModError)) { Fail(Out, ModError); return; }
		}

		const FModifierKeysState RealMods = FSlateApplication::Get().GetModifierKeys();
		{
			TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
			R->SetBoolField(TEXT("ctrl"),  RealMods.IsControlDown());
			R->SetBoolField(TEXT("alt"),   RealMods.IsAltDown());
			R->SetBoolField(TEXT("shift"), RealMods.IsShiftDown());
			R->SetBoolField(TEXT("cmd"),   RealMods.IsCommandDown());
			Out->SetObjectField(TEXT("modifiersReal"), R);
			TSharedRef<FJsonObject> W = MakeShared<FJsonObject>();
			W->SetBoolField(TEXT("ctrl"),  Wanted.bCtrl);
			W->SetBoolField(TEXT("alt"),   Wanted.bAlt);
			W->SetBoolField(TEXT("shift"), Wanted.bShift);
			W->SetBoolField(TEXT("cmd"),   Wanted.bCmd);
			W->SetBoolField(TEXT("any"),   Wanted.bAny);
			Out->SetObjectField(TEXT("modifiersRequested"), W);
		}

		if (Wanted.bAny)
		{
			const bool bSatisfied =
				   (!Wanted.bCtrl  || RealMods.IsControlDown())
				&& (!Wanted.bAlt   || RealMods.IsAltDown())
				&& (!Wanted.bShift || RealMods.IsShiftDown())
				&& (!Wanted.bCmd   || RealMods.IsCommandDown());
			Out->SetBoolField(TEXT("modifiersSatisfiedByRealKeyboard"), bSatisfied);
			if (!bSatisfied)
			{
				Fail(Out, FString::Printf(
					TEXT("REFUSED: a modified chord cannot be faked. You asked for %s%s%s%s'%s', but FSlateApplication::GetModifierKeys() reads the REAL platform keyboard (SlateApplication.cpp:3034-3037) and reports ctrl=%s alt=%s shift=%s cmd=%s right now. Consumers that build their FInputChord from that live state — which is how BlueprintAssist's input processor does it (BlueprintAssistInputProcessor.cpp:1118-1123) — would see BARE '%s' and fire whatever THAT is bound to. Sending it anyway would be a silently wrong action, so nothing was sent. Options: (1) invoke the command directly with invoke_editor_command, which needs no chord at all; (2) have a human hold the modifiers and retry; (3) drop the modifiers and send the unmodified key deliberately."),
					Wanted.bCtrl ? TEXT("Ctrl+") : TEXT(""),
					Wanted.bAlt ? TEXT("Alt+") : TEXT(""),
					Wanted.bShift ? TEXT("Shift+") : TEXT(""),
					Wanted.bCmd ? TEXT("Cmd+") : TEXT(""),
					*KeyName,
					RealMods.IsControlDown() ? TEXT("true") : TEXT("false"),
					RealMods.IsAltDown() ? TEXT("true") : TEXT("false"),
					RealMods.IsShiftDown() ? TEXT("true") : TEXT("false"),
					RealMods.IsCommandDown() ? TEXT("true") : TEXT("false"),
					*KeyName));
				return;
			}
		}

		// Context the caller needs to interpret the result: a key event that no pre-processor claims
		// goes to the focused widget, so "which widget" is the difference between "it worked" and
		// "it went into a text box".
		if (const TSharedPtr<SWidget> Focused = FSlateApplication::Get().GetKeyboardFocusedWidget())
		{
			TSharedRef<FJsonObject> F = MakeShared<FJsonObject>();
			F->SetStringField(TEXT("type"), Focused->GetTypeAsString());
			F->SetStringField(TEXT("readableLocation"), Focused->GetReadableLocation());
			Out->SetObjectField(TEXT("focusedWidget"), F);
		}
		if (const TSharedPtr<SWindow> Active = FSlateApplication::Get().GetActiveTopLevelWindow())
		{
			Out->SetStringField(TEXT("activeWindow"), Active->GetTitle().ToString());
			Out->SetBoolField(TEXT("activeWindowMinimized"), Active->IsWindowMinimized());
		}

		const bool bDryRun = JBool(In, TEXT("dryRun"), false);
		const bool bConfirm = JBool(In, TEXT("confirm"), false);
		if (bDryRun)
		{
			Out->SetBoolField(TEXT("dryRun"), true);
			Out->SetStringField(TEXT("note"),
				TEXT("validated only: the key name resolves, the modifier request (if any) matches the real keyboard, and the focus/window context is reported. Nothing was sent. Re-send with confirm:true to deliver it."));
			return;
		}
		if (!bConfirm)
		{
			Fail(Out, FString::Printf(
				TEXT("send_editor_key requires confirm=true to deliver '%s' (or dryRun=true to validate without sending). A synthetic key runs whatever is bound to it — which the request does not name — so it is never the default."),
				*KeyName));
			return;
		}

		const uint32 UserIndex = static_cast<uint32>(FMath::Max(0, JInt(In, TEXT("userIndex"), 0)));
		const uint32 CharCode  = static_cast<uint32>(FMath::Max(0, JInt(In, TEXT("characterCode"), 0)));
		const uint32 KeyCode   = static_cast<uint32>(FMath::Max(0, JInt(In, TEXT("keyCode"), 0)));
		const bool bIsRepeat   = JBool(In, TEXT("isRepeat"), false);
		const bool bSendUp     = JBool(In, TEXT("sendKeyUp"), true);

		// The event carries the REAL modifier state, not a fabricated one: it is what every consumer
		// reads anyway (see the header note), so anything else would put two different answers in
		// front of the same consumer.
		const FKeyEvent DownEvent(Key, RealMods, UserIndex, bIsRepeat, CharCode, KeyCode);
		UE_LOG(LogMifBridge, Log, TEXT("send_editor_key: ProcessKeyDownEvent('%s')"), *KeyName);
		const bool bDownHandled = FSlateApplication::Get().ProcessKeyDownEvent(DownEvent);
		Out->SetBoolField(TEXT("downHandled"), bDownHandled);
		Out->SetBoolField(TEXT("sent"), true);

		if (bSendUp)
		{
			const FKeyEvent UpEvent(Key, RealMods, UserIndex, false, CharCode, KeyCode);
			const bool bUpHandled = FSlateApplication::Get().ProcessKeyUpEvent(UpEvent);
			Out->SetBoolField(TEXT("upHandled"), bUpHandled);
			Out->SetBoolField(TEXT("keyLeftDown"), false);
		}
		else
		{
			Out->SetBoolField(TEXT("keyLeftDown"), true);
			Out->SetStringField(TEXT("warning"),
				TEXT("sendKeyUp:false — the key was left logically DOWN. Send the same key again with sendKeyUp:true (or press it physically) before anything else relies on keyboard state."));
		}

		Out->SetStringField(TEXT("note"),
			TEXT("downHandled is what Slate reported: true means a pre-processor or the focused widget consumed the event, false means nothing claimed it. Neither value says which command ran — verify the effect with the endpoint that reads the thing you expected to change."));
	}

	//   in:  { path: "/Game/..." }
	//   out: { assetPath, assetClass, alreadyOpen, opened, contextsBefore[], contextsAfter[],
	//          newContexts[], openAssetEditors[] }
	//
	// WHAT THIS DOES *NOT* DO — read this before reaching for it. It does NOT make an asset
	// editor's commands reachable by invoke_editor_command. That was the intent (a user's
	// suggestion, 2026-08-15) and it was MEASURED FALSE the day it was built:
	// SM_Barrel's StaticMeshEditor was opened, openAssetEditors[] confirmed it, and the cached
	// contexts stayed [LevelViewport, ContentBrowser] with newContexts[] empty across repeated
	// calls. StaticMeshEditor.RemoveCollision still failed with cachedListsForContext:0.
	//
	// ROOT CAUSE (verified in D:/UE532, not inferred). The original diagnosis — "an asset editor
	// only broadcasts its command list when opened" — is wrong. Those toolkits NEVER register a
	// command list at all: in the whole of Engine/Source/Editor only FIVE call sites reach
	// FInputBindingManager::RegisterCommandList — SContentBrowser.cpp, LevelEditor.cpp,
	// SLevelViewport.cpp, MainFrameModule.cpp and Sequencer.cpp. StaticMeshEditor.cpp has none.
	// An asset editor toolkit builds its own FUICommandList locally and never hands it over, so
	// there is no broadcast for MifBridge to hear no matter when, or how often, it is opened.
	// The permanently short cached-context list is a property of the engine, not a timing race.
	//
	// SO: for an asset-editor command, the answer is a DIRECT endpoint that calls the same engine
	// function the button does — remove_collision / add_simplified_collision are exactly that for
	// the static-mesh collision toolbar. Do not add "open the editor first" to any workflow
	// expecting it to help; it will not.
	//
	// WHAT IT IS STILL GOOD FOR: opening an asset editor programmatically (getting a viewport up
	// for a human to look at, or driving one of the five contexts that DO register). newContexts[]
	// is kept because it is the live evidence for the paragraph above — if a future engine version
	// starts registering asset-editor lists, this field is where that will show up first.
	//
	// This opens real editor UI, so unlike the rest of the plugin it is NOT dialog-free: an asset
	// that prompts on open (missing source file, upgrade notice) can raise a modal, and a modal
	// stalls the game-thread ticker this HTTP server runs on. Prefer a direct endpoint where one
	// exists — remove_collision / add_simplified_collision cover the static-mesh collision toolbar
	// without opening anything.
	void H_open_asset_editor(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path") },
			TEXT("path - the asset whose default editor to open (warms its FUICommandList so invoke_editor_command can reach that editor's commands)"),
			{ { TEXT("blueprintId"), TEXT("spell it path") },
			  { TEXT("asset"), TEXT("spell it path") },
			  { TEXT("focus"), TEXT("there is no focus - OpenEditorForAsset already brings the editor forward; alreadyOpen in the response says whether it was open before this call") } }))
		{
			return;
		}

		const FString RawPath = JStr(In, TEXT("path"));
		if (RawPath.IsEmpty())
		{
			Fail(Out, TEXT("open_asset_editor requires path"));
			return;
		}
		if (!GEditor)
		{
			Fail(Out, TEXT("no GEditor - open_asset_editor needs a running editor"));
			return;
		}
		UAssetEditorSubsystem* Sub = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
		if (!Sub)
		{
			Fail(Out, TEXT("UAssetEditorSubsystem unavailable"));
			return;
		}
		UObject* Asset = LoadAssetLenient(RawPath);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: %s"), *RawPath));
			return;
		}

		TArray<FName> Before;
		GetCachedCommandListContexts(Before);
		const bool bAlreadyOpen = (Sub->FindEditorForAsset(Asset, /*bFocusIfOpen*/ false) != nullptr);

		const bool bOpened = Sub->OpenEditorForAsset(Asset);

		TArray<FName> After;
		GetCachedCommandListContexts(After);

		auto EmitNames = [](const TArray<FName>& Names)
		{
			TArray<TSharedPtr<FJsonValue>> Arr;
			for (const FName& N : Names) { Arr.Add(MakeShared<FJsonValueString>(N.ToString())); }
			return Arr;
		};
		TArray<FName> NewOnes;
		for (const FName& N : After)
		{
			if (!Before.Contains(N)) { NewOnes.Add(N); }
		}

		Out->SetStringField(TEXT("assetPath"), NormalizePackagePath(RawPath));
		Out->SetStringField(TEXT("assetClass"), Asset->GetClass()->GetName());
		Out->SetBoolField(TEXT("alreadyOpen"), bAlreadyOpen);
		Out->SetBoolField(TEXT("opened"), bOpened);
		Out->SetArrayField(TEXT("contextsBefore"), EmitNames(Before));
		Out->SetArrayField(TEXT("contextsAfter"), EmitNames(After));
		Out->SetArrayField(TEXT("newContexts"), EmitNames(NewOnes));

		TArray<UObject*> Edited = Sub->GetAllEditedAssets();
		TArray<TSharedPtr<FJsonValue>> OpenArr;
		for (UObject* Obj : Edited)
		{
			if (Obj) { OpenArr.Add(MakeShared<FJsonValueString>(Obj->GetPathName())); }
		}
		Out->SetArrayField(TEXT("openAssetEditors"), OpenArr);

		if (NewOnes.Num() == 0)
		{
			// Do NOT soften this into "try again" - that was the original wording and it was
			// wrong. Measured 2026-08-15: repeated opens never add an asset-editor context.
			Out->SetStringField(TEXT("note"),
				TEXT("No new command contexts - and for an asset editor that is EXPECTED, not a "
				     "timing problem. Asset editor toolkits never call "
				     "FInputBindingManager::RegisterCommandList at all (in the whole engine only "
				     "SContentBrowser, LevelEditor, SLevelViewport, MainFrame and Sequencer do), "
				     "so there is no broadcast to cache and invoke_editor_command will NOT reach "
				     "this editor's commands however many times you open it. Use a direct endpoint "
				     "that calls the same engine function as the button - e.g. remove_collision / "
				     "add_simplified_collision for the static-mesh collision toolbar."));
		}
		UE_LOG(LogMifBridge, Log, TEXT("open_asset_editor: %s (%s) opened=%d alreadyOpen=%d newContexts=%d"),
			*RawPath, *Asset->GetClass()->GetName(), bOpened ? 1 : 0, bAlreadyOpen ? 1 : 0, NewOnes.Num());
	}
}
