// MifBridge — editor module. Owns the localhost HTTP server + Start/Stop menu toggle.
#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleInterface.h"

class FMifBridgeServer;

class FMifBridgeModule : public IModuleInterface
{
public:
	// IModuleInterface
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;

	/** Bring the HTTP listener up on the configured port. Safe to call when already running. */
	void StartServer();
	/** Tear the HTTP listener down. Safe to call when already stopped. */
	void StopServer();
	/** True while the listener is bound and accepting requests. */
	bool IsRunning() const;
	/** The TCP port the listener is bound to (default 8791). */
	int32 GetPort() const;

private:
	void RegisterMenus();

	TSharedPtr<FMifBridgeServer> Server;
	int32 Port = 8791;
	FString Token;
};
