// MifBridge — HTTP server: routing, auth, loopback enforcement, game-thread dispatch.
#pragma once

#include "CoreMinimal.h"
#include "HttpResultCallback.h"
#include "HttpRouteHandle.h"

class IHttpRouter;
struct FHttpServerRequest;
class FJsonObject;

/**
 * Owns the FHttpServerModule listener on 127.0.0.1:<port>. Each endpoint from the
 * handler registry is bound as POST /api/<endpoint>. Requests are authenticated by a
 * shared-secret header, restricted to loopback peers, then marshalled to the game
 * thread where all UObject/graph work happens inside a transaction.
 */
class FMifBridgeServer
{
public:
	FMifBridgeServer(int32 InPort, const FString& InToken);
	~FMifBridgeServer();

	/** Bind the router + all routes and start listening. Returns false if the port is taken. */
	bool Start();
	/** Unbind routes and stop listening. */
	void Stop();

	bool IsRunning() const { return bRunning; }
	int32 GetPort() const { return Port; }

private:
	/** HTTP worker-thread entry: auth + loopback + parse, then hop to game thread. */
	bool HandleHttp(const FString& Endpoint, const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);

	int32 Port;
	FString Token;
	bool bRunning = false;

	TSharedPtr<IHttpRouter> Router;
	TArray<FHttpRouteHandle> Routes;
};
