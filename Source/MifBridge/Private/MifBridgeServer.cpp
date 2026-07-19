// MifBridge — HTTP server implementation.
#include "MifBridgeServer.h"

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Async/Async.h"
#include "Dom/JsonObject.h"
#include "HttpPath.h"
#include "HttpServerConstants.h"
#include "HttpServerModule.h"
#include "HttpServerRequest.h"
#include "HttpServerResponse.h"
#include "IHttpRouter.h"
#include "IPAddress.h"
#include "Policies/CondensedJsonPrintPolicy.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace
{
	FString HeaderValue(const FHttpServerRequest& Request, const FString& Key)
	{
		if (const TArray<FString>* Values = Request.Headers.Find(Key))
		{
			if (Values->Num() > 0)
			{
				return (*Values)[0];
			}
		}
		return FString();
	}

	bool IsLoopbackPeer(const TSharedPtr<FInternetAddr>& Peer)
	{
		if (!Peer.IsValid())
		{
			// Can't determine — the token gate is the fallback. Do not block.
			return true;
		}
		const FString Addr = Peer->ToString(false /*bAppendPort*/);
		return Addr.StartsWith(TEXT("127.")) || Addr == TEXT("::1") || Addr.StartsWith(TEXT("0:0:0:0:0:0:0:1"));
	}

	FString JsonToString(const TSharedRef<FJsonObject>& Obj)
	{
		FString Out;
		TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Out);
		FJsonSerializer::Serialize(Obj, Writer);
		return Out;
	}

	TUniquePtr<FHttpServerResponse> MakeJsonResponse(const TSharedRef<FJsonObject>& Obj, EHttpServerResponseCodes Code)
	{
		TUniquePtr<FHttpServerResponse> Response = FHttpServerResponse::Create(JsonToString(Obj), TEXT("application/json"));
		Response->Code = Code;
		return Response;
	}
}

FMifBridgeServer::FMifBridgeServer(int32 InPort, const FString& InToken)
	: Port(InPort)
	, Token(InToken)
{
}

FMifBridgeServer::~FMifBridgeServer()
{
	Stop();
}

bool FMifBridgeServer::Start()
{
	if (bRunning)
	{
		return true;
	}

	FHttpServerModule& Http = FHttpServerModule::Get();
	Router = Http.GetHttpRouter(Port, /*bFailOnBindFailure*/ true);
	if (!Router.IsValid())
	{
		return false;
	}

	const TArray<FString> Endpoints = MifBridge::GetEndpointNames();
	for (const FString& Name : Endpoints)
	{
		const FString PathStr = FString::Printf(TEXT("/api/%s"), *Name);

		FHttpRequestHandler Handler =
			[this, Name](const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete) -> bool
			{
				return this->HandleHttp(Name, Request, OnComplete);
			};

		FHttpRouteHandle Route = Router->BindRoute(FHttpPath(PathStr), EHttpServerRequestVerbs::VERB_POST, Handler);
		if (Route.IsValid())
		{
			Routes.Add(Route);
		}
		else
		{
			UE_LOG(LogMifBridge, Warning, TEXT("Failed to bind route %s"), *PathStr);
		}
	}

	Http.StartAllListeners();
	bRunning = true;
	UE_LOG(LogMifBridge, Log, TEXT("Bound %d routes on port %d."), Routes.Num(), Port);
	return true;
}

void FMifBridgeServer::Stop()
{
	if (!bRunning)
	{
		return;
	}

	if (Router.IsValid())
	{
		for (FHttpRouteHandle& Route : Routes)
		{
			if (Route.IsValid())
			{
				Router->UnbindRoute(Route);
			}
		}
	}
	Routes.Reset();
	Router.Reset();

	// Note: FHttpServerModule::StopAllListeners() would stop every listener process-wide;
	// unbinding our routes is sufficient to make the bridge inert without disturbing others.
	bRunning = false;
}

bool FMifBridgeServer::HandleHttp(const FString& Endpoint, const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
	// --- Shared-secret gate -------------------------------------------------
	if (!Token.IsEmpty())
	{
		const FString Provided = HeaderValue(Request, TEXT("X-Mif-Token"));
		if (Provided != Token)
		{
			TSharedRef<FJsonObject> Err = MakeShared<FJsonObject>();
			Err->SetBoolField(TEXT("ok"), false);
			Err->SetStringField(TEXT("error"), TEXT("invalid or missing X-Mif-Token header"));
			OnComplete(MakeJsonResponse(Err, EHttpServerResponseCodes::Forbidden));
			return true;
		}
	}

	// --- Loopback-only enforcement -----------------------------------------
	if (!IsLoopbackPeer(Request.PeerAddress))
	{
		TSharedRef<FJsonObject> Err = MakeShared<FJsonObject>();
		Err->SetBoolField(TEXT("ok"), false);
		Err->SetStringField(TEXT("error"), TEXT("bridge only accepts loopback connections"));
		OnComplete(MakeJsonResponse(Err, EHttpServerResponseCodes::Forbidden));
		return true;
	}

	// --- Parse body ---------------------------------------------------------
	FString BodyStr;
	if (Request.Body.Num() > 0)
	{
		// UTF-8 decode of the raw byte payload.
		FUTF8ToTCHAR Converter(reinterpret_cast<const ANSICHAR*>(Request.Body.GetData()), Request.Body.Num());
		BodyStr = FString(Converter.Length(), Converter.Get());
	}

	TSharedPtr<FJsonObject> InObj;
	if (!BodyStr.IsEmpty())
	{
		TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(BodyStr);
		if (!FJsonSerializer::Deserialize(Reader, InObj) || !InObj.IsValid())
		{
			TSharedRef<FJsonObject> Err = MakeShared<FJsonObject>();
			Err->SetBoolField(TEXT("ok"), false);
			Err->SetStringField(TEXT("error"), TEXT("request body is not valid JSON"));
			OnComplete(MakeJsonResponse(Err, EHttpServerResponseCodes::BadRequest));
			return true;
		}
	}
	if (!InObj.IsValid())
	{
		InObj = MakeShared<FJsonObject>();
	}
	const TSharedRef<FJsonObject> InRef = InObj.ToSharedRef();

	MIF_DBG("-> %s %s", *Endpoint, *BodyStr);

	// --- Hop to game thread: ALL UObject work happens there -----------------
	FHttpResultCallback Callback = OnComplete;
	AsyncTask(ENamedThreads::GameThread, [Endpoint, InRef, Callback]()
	{
		TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
		MifBridge::RunEndpoint(Endpoint, InRef, Out);

		const FString OutStr = JsonToString(Out);
		MIF_DBG("<- %s %s", *Endpoint, *OutStr);

		TUniquePtr<FHttpServerResponse> Response = FHttpServerResponse::Create(OutStr, TEXT("application/json"));
		Response->Code = EHttpServerResponseCodes::Ok;
		Callback(MoveTemp(Response));
	});

	return true; // response delivered asynchronously
}
