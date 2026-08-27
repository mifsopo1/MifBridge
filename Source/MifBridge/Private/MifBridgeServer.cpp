// MifBridge — HTTP server implementation.
#include "MifBridgeServer.h"

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Containers/Ticker.h"
#include "Dom/JsonObject.h"
#include "HAL/Event.h"
#include "HAL/PlatformProcess.h"
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

	/**
	 * State shared between a non-game-thread caller and the game-thread ticker that runs its
	 * endpoint. Both sides hold a thread-safe shared ref; whichever releases last returns the
	 * event to the pool. That matters on the timeout path: the waiter gives up while the
	 * ticker may still be about to write Out and Trigger(), so neither side may unilaterally
	 * free the event or the payload.
	 */
	struct FMifPendingCall
	{
		TSharedRef<FJsonObject> Out;
		FEvent* Event;

		FMifPendingCall()
			: Out(MakeShared<FJsonObject>())
			, Event(FPlatformProcess::GetSynchEventFromPool())
		{
		}

		~FMifPendingCall()
		{
			FPlatformProcess::ReturnSynchEventToPool(Event);
		}
	};

	/** Upper bound on how long an off-game-thread request will wait for the game thread. */
	constexpr float MifOffThreadTimeoutSeconds = 120.0f;
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

	// A 404 THAT TEACHES NOTHING COSTS ROUND TRIPS. UE's router answers an unbound path with
	//   {"errorCode":"...route_handler_not_found","errorMessage":""}
	// - an EMPTY message - and the handler never runs, so MifBridge cannot say anything. A real session
	// building a city burned three calls guessing delete_actor, destroy_actor and remove_actor before
	// finding delete_level_actor, and separately guessed list_endpoints without learning that self_audit
	// already enumerates everything.
	//
	// A preprocessor runs BEFORE routing, so an unknown /api/ path can be answered properly. It returns
	// false for everything it does not handle, which leaves normal routing untouched.
	Router->RegisterRequestPreprocessor(
		[](const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete) -> bool
		{
			const FString Path = Request.RelativePath.GetPath();
			if (!Path.StartsWith(TEXT("/api/")))
			{
				return false;                       // not ours - let the router deal with it
			}
			const FString Wanted = Path.RightChop(5);
			const TArray<FString> Known = MifBridge::GetEndpointNames();
			if (Wanted.IsEmpty() || Known.Contains(Wanted))
			{
				return false;                       // a real endpoint - its own route handles it
			}

			// RANKED, NOT FIRST-EIGHT. The first version took the first eight names containing any shared
			// word, which put delete_datatable_rows and add_spawn_actor ahead of delete_level_actor - and
			// delete_level_actor IS the answer that guess was looking for. A did-you-mean that omits the
			// right answer is barely better than none, so score by how much of the guess a name actually
			// shares and take the BEST eight.
			TArray<FString> Parts;
			Wanted.ParseIntoArray(Parts, TEXT("_"), true);
			TArray<TPair<int32, FString>> Scored;
			for (const FString& Name : Known)
			{
				int32 Score = 0;
				for (const FString& Part : Parts)
				{
					// FOUR, not three. A three-letter fragment is noise: 'all' from a nonsense guess matches
					// 'call', so zzzz_not_a_thing_at_all suggested every call-related endpoint. Four costs
					// nothing on the real cases - get_spline is carried by 'spline', not by 'get'.
					if (Part.Len() >= 4 && Name.Contains(Part))
					{
						// A whole word is worth more than an incidental substring: 'actor' in
						// 'delete_level_actor' beats 'actor' inside 'actorClass'.
						Score += (Name.EndsWith(Part) || Name.StartsWith(Part) || Name.Contains(Part + TEXT("_"))) ? 3 : 1;
					}
				}
				if (Name.Contains(Wanted) || Wanted.Contains(Name))
				{
					Score += 5;                     // a containment match is the strongest signal there is
				}
				if (Score > 0)
				{
					Scored.Add(TPair<int32, FString>(Score, Name));
				}
			}
			Scored.Sort([](const TPair<int32, FString>& A, const TPair<int32, FString>& B)
				{ return A.Key != B.Key ? A.Key > B.Key : A.Value < B.Value; });
			TArray<FString> Suggestions;
			for (const TPair<int32, FString>& Pair : Scored)
			{
				Suggestions.Add(Pair.Value);
				if (Suggestions.Num() >= 8) { break; }
			}
			TSharedRef<FJsonObject> Err = MakeShared<FJsonObject>();
			Err->SetBoolField(TEXT("ok"), false);
			Err->SetStringField(TEXT("error"), FString::Printf(
				TEXT("'%s' is not an endpoint on this build (%d are registered). Call self_audit to list ")
				TEXT("every endpoint, or describe_endpoint {name} for one of them."),
				*Wanted, Known.Num()));
			if (Suggestions.Num() > 0)
			{
				TArray<TSharedPtr<FJsonValue>> Arr;
				for (const FString& Sug : Suggestions)
				{
					Arr.Add(MakeShared<FJsonValueString>(Sug));
				}
				Err->SetArrayField(TEXT("didYouMean"), Arr);
			}
			OnComplete(MakeJsonResponse(Err, EHttpServerResponseCodes::NotFound));
			return true;
		});

	Http.StartAllListeners();
	// Routes are now bound, once per name. Any later RegisterExternalEndpoint call would produce a
	// dispatchable-but-unrouted endpoint, so the registry refuses from here on.
	MifBridge::MarkRouteTableLive();
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
	// BEFORE dispatch, and flushed. MIF_DBG above is gated behind a CVar that defaults to false
	// and goes to UE_LOG, which buffers - so on a normal run neither of those survives a hard
	// kill. This does. See MifBridgeJournal.cpp for why the ordering is the whole point.
	MifBridge::JournalCallStart(Endpoint, BodyStr);

	// --- Run the endpoint on the game thread, at a tick-safe point ----------
	//
	// Do NOT reach for AsyncTask(ENamedThreads::GameThread, ...) here. That enqueues onto the
	// game thread's NAMED-THREAD task queue, which is pumped not only between frames but also
	// from inside FTickTaskSequencer::ReleaseTickGroup() -> WaitUntilTasksComplete(): while a
	// tick group waits on its tasks, the named thread happily services anything else queued to
	// it. An endpoint that recompiles a Blueprint therefore reinstances actors in the MIDDLE of
	// a tick group, and FTickTaskManager is left iterating FTickFunctions whose owning objects
	// have just been trashed. The next one to execute lands on
	//
	//     EngineBaseTypes.h:409   check(!"Pure virtual not implemented")
	//
	// inside FTickFunctionTask::DoTask() - a hard crash whose stack contains no MifBridge frame
	// at all, so it reads as a spontaneous editor failure. It reproduced on every compile-heavy
	// request and was misread for a long time as a project-side teardown bug.
	//
	// FHttpServerModule derives from FTSTickerObjectBase, so this handler is ALREADY on the game
	// thread, called from FTSTicker::GetCoreTicker().Tick() - which FEngineLoop::Tick() runs
	// after GEngine->Tick() has completed the entire world tick, outside every tick group. That
	// is precisely the safe point we want, so the real fix is to stop deferring and just run.
	const auto RunAndReply = [&Endpoint, &InRef](const FHttpResultCallback& Reply, const TSharedRef<FJsonObject>& Out)
	{
		const FString OutStr = JsonToString(Out);
		MIF_DBG("<- %s %s", *Endpoint, *OutStr);
		// The matching close. Reached only if the handler returned at all - which is exactly what
		// makes its absence meaningful.
		MifBridge::JournalCallEnd(Endpoint, MifBridge::IsOk(Out));

		TUniquePtr<FHttpServerResponse> Response = FHttpServerResponse::Create(OutStr, TEXT("application/json"));
		Response->Code = EHttpServerResponseCodes::Ok;
		Reply(MoveTemp(Response));
	};

	if (IsInGameThread())
	{
		TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
		MifBridge::RunEndpoint(Endpoint, InRef, Out);
		RunAndReply(OnComplete, Out);
		return true;
	}

	// Off the game thread: only reachable if the HTTP server is ever driven by another
	// transport. Hand the work to the core ticker (FTSTicker is safe to add to from any
	// thread) so it lands on the same post-world-tick safe point, and block here until it has
	// run.
	//
	// The reply is issued from THIS thread on purpose. FHttpResultCallback is only valid for
	// the duration of the handler call; capturing it and invoking it a frame later from the
	// game thread dereferences freed state and faults. That was tried, and it turned a
	// crash-on-compile into a crash-on-every-request.
	TSharedRef<FMifPendingCall, ESPMode::ThreadSafe> Pending = MakeShared<FMifPendingCall, ESPMode::ThreadSafe>();

	FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateLambda(
		[Endpoint, InRef, Pending](float) -> bool
		{
			MifBridge::RunEndpoint(Endpoint, InRef, Pending->Out);
			Pending->Event->Trigger();
			return false; // one-shot
		}), 0.0f);

	const uint32 TimeoutMs = static_cast<uint32>(MifOffThreadTimeoutSeconds * 1000.0f);
	if (!Pending->Event->Wait(TimeoutMs))
	{
		// The game thread never got to us. Do not touch Pending->Out - the ticker may still be
		// about to write it. Pending's other reference keeps the payload and the event alive
		// until that lambda is destroyed, so abandoning it here is safe.
		TSharedRef<FJsonObject> Err = MakeShared<FJsonObject>();
		Err->SetBoolField(TEXT("ok"), false);
		Err->SetStringField(TEXT("error"),
			FString::Printf(TEXT("timed out after %.0fs waiting for the game thread"), MifOffThreadTimeoutSeconds));
		OnComplete(MakeJsonResponse(Err, EHttpServerResponseCodes::ServerError));
		return true;
	}

	RunAndReply(OnComplete, Pending->Out);
	return true;
}
