// MifBridge — module boot/shutdown + Tools menu Start/Stop toggle.
#include "MifBridge.h"

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
	}

	Server = MakeShared<FMifBridgeServer>(Port, Token);

	if (CVarMifBridgeAutoStart.GetValueOnGameThread())
	{
		StartServer();
	}

	// ToolMenus may not be ready yet at PostEngineInit; register through the startup callback.
	UToolMenus::RegisterStartupCallback(
		FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FMifBridgeModule::RegisterMenus));

	UE_LOG(LogMifBridge, Log, TEXT("MifBridge module loaded (port %d, auto-start %s)."),
		Port, CVarMifBridgeAutoStart.GetValueOnGameThread() ? TEXT("on") : TEXT("off"));
}

void FMifBridgeModule::ShutdownModule()
{
	UToolMenus::UnRegisterStartupCallback(this);
	UToolMenus::UnregisterOwner(this);

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

	UToolMenu* Menu = UToolMenus::Get()->ExtendMenu("LevelEditor.MainMenu.Tools");
	if (!Menu)
	{
		return;
	}

	FToolMenuSection& Section = Menu->FindOrAddSection("MifBridge");

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
