// MifBridge — module boot/shutdown + Tools menu Start/Stop toggle.
#include "MifBridge.h"

#include "MifBridgeHandlers.h"   // MifBridge::Subscribe/UnsubscribeCommandListObserver (Batch O)
#include "MifBridgeLog.h"
#include "MifBridgeServer.h"

#include "Framework/Commands/UIAction.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CoreDelegates.h"
#include "Textures/SlateIcon.h"
#include "ToolMenus.h"

#define LOCTEXT_NAMESPACE "FMifBridgeModule"

DEFINE_LOG_CATEGORY(LogMifBridge);

TAutoConsoleVariable<bool> CVarMifBridgeDebug(
	TEXT("mif.BridgeDebug"),
	false,
	TEXT("When true, MifBridge logs full request/response bodies at Log level."),
	ECVF_Default);

static TAutoConsoleVariable<bool> CVarMifBridgeAutoStart(
	TEXT("mif.BridgeAutoStart"),
	true,
	TEXT("When true, MifBridge starts listening automatically on editor load."),
	ECVF_Default);

void FMifBridgeModule::StartupModule()
{
	// Token comes from the environment so the same secret can be shared with the MCP
	// server without hard-coding it. Falls back to "dev" (matches the python default).
	Token = FPlatformMisc::GetEnvironmentVariable(TEXT("MIF_BRIDGE_TOKEN"));
	if (Token.IsEmpty())
	{
		Token = TEXT("dev");
	}

	const FString PortStr = FPlatformMisc::GetEnvironmentVariable(TEXT("MIF_BRIDGE_PORT"));
	if (!PortStr.IsEmpty())
	{
		const int32 Parsed = FCString::Atoi(*PortStr);
		if (Parsed > 0 && Parsed < 65536)
		{
			Port = Parsed;
		}
		else
		{
			// Silently keeping 8791 after being ASKED for something else is how two editors end up on
			// one port. Say so.
			UE_LOG(LogMifBridge, Warning,
				TEXT("MIF_BRIDGE_PORT='%s' is not a usable port number - staying on %d."),
				*PortStr, Port);
		}
	}

	// PORT ALLOCATION CHECK. This exists because of docs/06_OPEN_ISSUES_FROM_USE.md issue 15: a second
	// editor was pointed at 8792 to dodge the first one already holding 8791, and 8792 is MifBlender's
	// reserved port. Nothing warned, because nothing knew the allocation existed - so Blender was pushed
	// onto another port and the Blender integration would have failed in a genuinely confusing way. The
	// MCP server's _blender() dials 8792 and speaks a length-prefixed binary protocol; pointed at an
	// HTTP listener, the port IS open and something DOES answer, so the two checks anyone would run both
	// pass while nothing works.
	//
	// This only warns. Refusing to start would be worse: a deliberate override is legitimate (MifBlender
	// itself honours MIF_BLENDER_PORT), and a bridge that will not boot is a bigger problem than one on
	// an awkward port. The point is that the collision is no longer SILENT.
	if (Port == 8792)
	{
		UE_LOG(LogMifBridge, Warning,
			TEXT("MifBridge is configured for port 8792, which is RESERVED for the MifBlender addon ")
			TEXT("(tools/blender-addon/MifBlender/server.py:66, README.md:178). If Blender is or will be ")
			TEXT("running on this machine, its addon cannot bind and the Blender tools will reach THIS ")
			TEXT("editor instead - which answers, so it looks connected. Prefer moving THIS editor: the ")
			TEXT("addon port lives in two places that must agree (its preference and MIF_BLENDER_PORT), ")
			TEXT("while this one is a single variable. Use 8801+ for a second editor ")
			TEXT("and leave 879x alone. See docs/06_OPEN_ISSUES_FROM_USE.md issue 15."));
	}
	else if (Port == 9876)
	{
		UE_LOG(LogMifBridge, Warning,
			TEXT("MifBridge is configured for port 9876, which belongs to the third-party 'blender-mcp' ")
			TEXT("addon, not to us. Use 8801+ instead."));
	}

	Server = MakeShared<FMifBridgeServer>(Port, Token);

	// modkit: this module loads in every editor-context process, including the headless UnrealEditor-Cmd.exe
	// commandlet UAT spins up for cooking (e.g. via the Mod Packager). Auto-starting the HTTP listener there
	// too meant every cook tried to bind the same hardcoded port as the interactive editor's own MifBridge
	// instance, failed with "HttpListener unable to bind to 127.0.0.1:8791", and got counted as the cook's
	// one fatal error - a real, working cook getting reported (and by Mod Packager, silently papered over
	// with stale leftover output) as failed. A commandlet has no interactive session for MifBridge to serve
	// anyway, so just don't start it there.
	if (CVarMifBridgeAutoStart.GetValueOnGameThread() && !IsRunningCommandlet())
	{
		StartServer();
	}

	// Batch O — start listening for command-list registrations BEFORE the editor UI is built.
	//
	// FInputBindingManager stores no command lists: RegisterCommandList only broadcasts and keeps
	// nothing (InputBindingManager.cpp:561-569). FUICommandList::TryExecuteAction needs a live list,
	// and the global ones (FLevelEditorModule::GetGlobalLevelEditorActions,
	// IMainFrameModule::GetMainFrameCommandBindings) are in modules MifBridge does not depend on. The
	// public OnRegisterCommandList multicast is the route that needs no new module dependency, and
	// FIVE engine sites broadcast onto it: LevelEditor.cpp:281, MainFrameModule.cpp:600,
	// SLevelViewport.cpp:1381, SContentBrowser.cpp:678, Sequencer.cpp:668-669.
	//
	// TIMING IS THE WHOLE POINT AND IT IS VERIFIED, NOT ASSUMED. A broadcast that happens before we
	// subscribe is lost forever, because nothing stores it. PostEngineInit plugin modules load inside
	// FEngineLoop::Init (LaunchEngineLoop.cpp:4838-4840), and EditorInit calls EngineLoop.Init() at
	// UnrealEdGlobals.cpp:111 BEFORE loading MainFrame and building the editor UI at :171. So this
	// line runs before all five broadcasts. Anything registered earlier is invisible, and
	// list_editor_commands' commandListSource block says so rather than implying completeness.
	//
	// Skipped under a commandlet for the same reason the server is: there is no interactive UI to
	// invoke, and FInputBindingManager::Get() would be constructed for nothing.
	if (!IsRunningCommandlet())
	{
		MifBridge::SubscribeCommandListObserver();
	}

	// ToolMenus may not be ready yet at PostEngineInit; register through the startup callback.
	UToolMenus::RegisterStartupCallback(
		FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FMifBridgeModule::RegisterMenus));

	UE_LOG(LogMifBridge, Log, TEXT("MifBridge module loaded (port %d, auto-start %s)."),
		Port, CVarMifBridgeAutoStart.GetValueOnGameThread() ? TEXT("on") : TEXT("off"));
}

void FMifBridgeModule::ShutdownModule()
{
	// A CLEAN shutdown says so. Its ABSENCE at the next launch is what separates "the editor was
	// closed" from "the editor died" - a timestamp alone cannot tell you which. Written first,
	// before anything else can fail during teardown.
	// Before the journal closes: the spawner holds a lambda inside this DLL, and leaving it registered
	// past unload is a dangling call the next tab invocation would make.
	MifBridge::UnregisterPanel();

	MifBridge::JournalClose(TEXT("module-shutdown"));

	UToolMenus::UnRegisterStartupCallback(this);
	UToolMenus::UnregisterOwner(this);

	// Drop the OnRegisterCommandList subscription and the weak command-list cache. Leaving a delegate
	// bound to a free function in an unloading DLL is a dangling call the next broadcast would make.
	MifBridge::UnsubscribeCommandListObserver();

	StopServer();
	Server.Reset();

	UE_LOG(LogMifBridge, Log, TEXT("MifBridge module unloaded."));
}

void FMifBridgeModule::StartServer()
{
	if (!Server.IsValid())
	{
		Server = MakeShared<FMifBridgeServer>(Port, Token);
	}
	if (Server->IsRunning())
	{
		return;
	}
	if (Server->Start())
	{
		UE_LOG(LogMifBridge, Log, TEXT("MifBridge listening on http://127.0.0.1:%d/api"), Server->GetPort());
		// Opened here rather than in StartupModule so the journal records the port it is actually
		// serving on, and so a build that never starts a server never creates one.
		MifBridge::JournalOpen(Server->GetPort());
	}
	else
	{
		UE_LOG(LogMifBridge, Warning, TEXT("MifBridge failed to bind port %d (already in use?)."), Port);
	}
}

void FMifBridgeModule::StopServer()
{
	if (Server.IsValid() && Server->IsRunning())
	{
		Server->Stop();
		UE_LOG(LogMifBridge, Log, TEXT("MifBridge stopped."));
	}
}

bool FMifBridgeModule::IsRunning() const
{
	return Server.IsValid() && Server->IsRunning();
}

int32 FMifBridgeModule::GetPort() const
{
	return Server.IsValid() ? Server->GetPort() : Port;
}

void FMifBridgeModule::RegisterMenus()
{
	FToolMenuOwnerScoped OwnerScoped(this);

	// The panel's tab spawner registers HERE rather than in StartupModule, because RegisterMenus runs
	// from UToolMenus::RegisterStartupCallback - i.e. after the editor UI machinery is up, and never at
	// all in a process without one. RegisterPanel checks FSlateApplication::IsInitialized() as well,
	// because EHostType::Editor DOES load in commandlets.
	MifBridge::RegisterPanel();

	UToolMenu* Menu = UToolMenus::Get()->ExtendMenu("LevelEditor.MainMenu.Tools");
	if (!Menu)
	{
		return;
	}

	FToolMenuSection& Section = Menu->FindOrAddSection("MifBridge");

	Section.AddMenuEntry(
		"MifBridgePanel",
		LOCTEXT("MifPanel", "Mif Bridge: Live Panel"),
		LOCTEXT("MifPanelTip",
			"Open the MifBridge panel - port, safety-gate mode and recent calls, updating live. "
			"Read-only; the bridge does not depend on it and runs headless without it."),
		FSlateIcon(),
		FUIAction(FExecuteAction::CreateLambda([]() { MifBridge::OpenPanel(); })));

	Section.AddMenuEntry(
		"MifBridgeToggle",
		TAttribute<FText>::CreateLambda([this]()
		{
			return IsRunning()
				? FText::Format(LOCTEXT("MifStop", "Mif Bridge: Stop (port {0})"), FText::AsNumber(GetPort(), &FNumberFormattingOptions::DefaultNoGrouping()))
				: LOCTEXT("MifStart", "Mif Bridge: Start");
		}),
		TAttribute<FText>::CreateLambda([this]()
		{
			return IsRunning()
				? FText::Format(LOCTEXT("MifStopTip", "Stop the localhost HTTP bridge (currently listening on 127.0.0.1:{0})."), FText::AsNumber(GetPort(), &FNumberFormattingOptions::DefaultNoGrouping()))
				: LOCTEXT("MifStartTip", "Start the localhost HTTP bridge for programmatic Blueprint edits.");
		}),
		FSlateIcon(),
		FUIAction(FExecuteAction::CreateLambda([this]()
		{
			if (IsRunning())
			{
				StopServer();
			}
			else
			{
				StartServer();
			}
		}))
	);
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FMifBridgeModule, MifBridge)
