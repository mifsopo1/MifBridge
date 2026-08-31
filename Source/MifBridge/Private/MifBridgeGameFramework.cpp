// ModularGameplay — UGameFrameworkComponentManager: register actors as component receivers, then
// request a component class be attached to every receiver of a given actor class, live.
//
// Reopened 2026-08-28, the same night ModularGameplay was re-examined and found blocked - correctly,
// at the time: UGameFrameworkComponentManager is a UGameInstanceSubsystem, which does not exist in the
// bare editor world this project's own EditorWorld() helper returns, only once a UGameInstance has
// initialized (PIE or a packaged game). That was a real, specific technical wall, not a "nobody needs
// it" excuse - and it stayed a wall right up until Andre lifted the standing no-PIE rule later the same
// night ("use pie for anything you need... do whatever is needed", see [[feedback-pie-authorized]]).
// With PIE actually usable, the wall is gone: this file targets ActiveWorld() (PIE when running,
// correctly refuses otherwise), the same helper MifBridgeSpatial.cpp and others already use for
// anything that can only answer during play.
//
// NO MIF_WITH_MODULARGAMEPLAY GUARD NEEDED for what THIS file touches: UGameFrameworkComponentManager
// itself lives in the ModularGameplay plugin (already linked, MIF_WITH_MODULARGAMEPLAY exists), but
// nothing here references it by class name at compile time in a way a stock engine would reject -
// wait, that is wrong and worth being honest about: the class IS compiled against directly
// (#include "Components/GameFrameworkComponentManager.h"), so this DOES need the plugin present. Kept
// under the existing MIF_WITH_MODULARGAMEPLAY guard for that reason - the plugin dependency was linked
// back on 2026-08-26 and never used until now.
//
// ACTORS MUST OPT IN, PER THE ENGINE'S OWN DESIGN, NOT SOMETHING THIS FILE ADDED. Checked before
// assuming DDS2/Curfew actors would just work: grepped Engine/Source/Runtime/Engine for any base
// Pawn/Character/Controller class calling AddGameFrameworkComponentReceiver on itself - zero hits. The
// request/receiver system is not an ambient engine feature; it is a pattern a PROJECT'S OWN classes opt
// into deliberately (Lyra is the canonical example). Since neither DDS2 nor Curfew has adopted it,
// add_game_framework_receiver is exposed as its own endpoint rather than assumed automatic, so a caller
// can register a specific actor explicitly instead of the request silently matching nothing.
//
// THE REQUEST HANDLE MUST STAY ALIVE, per the class's own documented contract: "when this handle is
// destroyed, it will remove the associated request from the system" and any receiver actors currently
// in memory "will lose the components immediately." A file-local static registry holds it for the
// editor session's lifetime - same shape as MifBridgeLiveLink.cpp's GMifLiveLinkSource, and the
// LevelSnapshots/GeometryScript files before it. A handle whose PIE session has since ended is safe to
// hold (FComponentRequestHandle's destructor checks its OwningManager weak pointer before touching it)
// even though it no longer does anything - not cleaned up automatically on PIE end, a minor known
// untidiness rather than a correctness risk.

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#if MIF_WITH_MODULARGAMEPLAY
#include "Components/GameFrameworkComponentManager.h"
#include "GameFramework/Actor.h"
#include "Engine/GameInstance.h"
#include "Misc/Guid.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_MODULARGAMEPLAY
	static void MifNoModularGameplay(const TSharedRef<FJsonObject>& Out)
	{
		Fail(Out, TEXT("this engine build has no ModularGameplay plugin, so there is no component ")
					  TEXT("manager to use. The endpoint exists on every build deliberately - a missing ")
					  TEXT("endpoint would tell you nothing, while this tells you the plugin is what is ")
					  TEXT("missing."));
	}
	void H_add_game_framework_receiver(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoModularGameplay(Out);
	}
	void H_add_game_framework_component_request(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoModularGameplay(Out);
	}
	void H_remove_game_framework_component_request(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoModularGameplay(Out);
	}
	void H_list_game_framework_component_requests(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoModularGameplay(Out);
	}
#else

	namespace
	{
		// Keyed by a caller-given or auto-generated requestId so remove_game_framework_component_request
		// can find it again in a LATER, separate HTTP call - the handle itself is never serialisable.
		//
		// THE CLASSES ARE KEPT ALONGSIDE THE HANDLE, and that is the whole reason this is a struct.
		// A live request injects componentClass into every current AND future actor of receiverClass
		// and stays live until its handle is released. Storing only the handle meant a listing could
		// report ids and nothing about what they DO, which is barely better than not listing them -
		// a caller looking at three unfamiliar ids still cannot tell which one to release.
		struct FMifComponentRequest
		{
			TSharedPtr<FComponentRequestHandle> Handle;
			FString ReceiverClass;
			FString ComponentClass;
		};
		TMap<FString, FMifComponentRequest> GMifComponentRequests;

		bool EnsureGameInstance(UGameInstance*& OutInstance, FString& OutError)
		{
			UWorld* World = ActiveWorld();
			OutInstance = World ? World->GetGameInstance() : nullptr;
			if (!OutInstance)
			{
				OutError = TEXT("no UGameInstance is active - UGameFrameworkComponentManager only exists ")
						   TEXT("during PIE or a packaged game, never the plain editor world. Start PIE first.");
				return false;
			}
			return true;
		}
	}

	// --- add_game_framework_receiver -------------------------------------------------------------
	//   in:  { actorPath }
	//   out: { actorPath, added: true }
	// Opts ONE actor into the component-request system. Required before any request targeting its
	// class will do anything to it - see the file header for why nothing does this automatically.
	void H_add_game_framework_receiver(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("actorPath"), TEXT("actor") },
			TEXT("actorPath (alias: actor) - the actor to register as a component-request receiver"), {}))
		{
			return;
		}

		UGameInstance* GameInstance = nullptr;
		FString Error;
		if (!EnsureGameInstance(GameInstance, Error))
		{
			Fail(Out, Error);
			return;
		}

		const FString ActorQuery = JStrAny(In, { TEXT("actorPath"), TEXT("actor") });
		AActor* Actor = FindActorInWorld(ActiveWorld(), ActorQuery);
		if (!Actor)
		{
			Fail(Out, FString::Printf(TEXT("no actor matching '%s' in the active world"), *ActorQuery));
			return;
		}

		UGameFrameworkComponentManager* Manager = GameInstance->GetSubsystem<UGameFrameworkComponentManager>();
		if (!Manager)
		{
			Fail(Out, TEXT("UGameFrameworkComponentManager subsystem is not available on this GameInstance"));
			return;
		}

		Manager->AddReceiver(Actor);
		Out->SetStringField(TEXT("actorPath"), Actor->GetPathName());
		Out->SetBoolField(TEXT("added"), true);
	}

	// --- add_game_framework_component_request ----------------------------------------------------
	//   in:  { receiverClass, componentClass, requestId? }
	//   out: { requestId, receiverClass, componentClass }
	// Any CURRENT receiver actor of receiverClass gets an instance of componentClass immediately;
	// any FUTURE receiver of that class gets one the moment it registers. The request stays live
	// until remove_game_framework_component_request releases it - see the file header for why.
	void H_add_game_framework_component_request(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("receiverClass"), TEXT("componentClass"), TEXT("requestId") },
			TEXT("receiverClass - an Actor subclass; componentClass - an ActorComponent subclass; ")
			TEXT("requestId (optional, auto-generated if omitted) - use it with ")
			TEXT("remove_game_framework_component_request later"),
			{}))
		{
			return;
		}

		UGameInstance* GameInstance = nullptr;
		FString Error;
		if (!EnsureGameInstance(GameInstance, Error))
		{
			Fail(Out, Error);
			return;
		}

		UClass* ReceiverClass = ResolveClassStrictField(In, { TEXT("receiverClass") }, nullptr, Out);
		if (!ReceiverClass) { return; }
		if (!ReceiverClass->IsChildOf(AActor::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not an Actor class: '%s'"), *ReceiverClass->GetName()));
			return;
		}

		UClass* ComponentClass = ResolveClassStrictField(In, { TEXT("componentClass") }, nullptr, Out);
		if (!ComponentClass) { return; }
		if (!ComponentClass->IsChildOf(UActorComponent::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not an ActorComponent class: '%s'"), *ComponentClass->GetName()));
			return;
		}

		FString RequestId = JStr(In, TEXT("requestId"));
		if (RequestId.IsEmpty())
		{
			RequestId = FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphens);
		}
		else if (GMifComponentRequests.Contains(RequestId))
		{
			Fail(Out, FString::Printf(
				TEXT("requestId '%s' is already in use - remove it first or pick another. NOTHING was added."),
				*RequestId));
			return;
		}

		UGameFrameworkComponentManager* Manager = GameInstance->GetSubsystem<UGameFrameworkComponentManager>();
		if (!Manager)
		{
			Fail(Out, TEXT("UGameFrameworkComponentManager subsystem is not available on this GameInstance"));
			return;
		}

		TSharedPtr<FComponentRequestHandle> Handle = Manager->AddComponentRequest(
			TSoftClassPtr<AActor>(ReceiverClass), TSubclassOf<UActorComponent>(ComponentClass));
		if (!Handle.IsValid())
		{
			Fail(Out, TEXT("AddComponentRequest returned no handle. NOTHING was added."));
			return;
		}

		GMifComponentRequests.Add(RequestId,
			FMifComponentRequest{ Handle, ReceiverClass->GetPathName(), ComponentClass->GetPathName() });
		Out->SetStringField(TEXT("requestId"), RequestId);
		Out->SetStringField(TEXT("receiverClass"), ReceiverClass->GetPathName());
		Out->SetStringField(TEXT("componentClass"), ComponentClass->GetPathName());
		UE_LOG(LogMifBridge, Log, TEXT("add_game_framework_component_request: %s -> %s (id %s)"),
			*ReceiverClass->GetName(), *ComponentClass->GetName(), *RequestId);
	}

	// --- remove_game_framework_component_request -------------------------------------------------
	//   in:  { requestId }
	//   out: { requestId, removed: true }
	// Destroying the handle removes the component from every CURRENT receiver of that class
	// immediately - the manager's own documented behavior, not something this endpoint does by hand.
	void H_remove_game_framework_component_request(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("requestId") },
			TEXT("requestId - the id returned by add_game_framework_component_request"), {}))
		{
			return;
		}

		const FString RequestId = JStr(In, TEXT("requestId"));
		if (RequestId.IsEmpty())
		{
			Fail(Out, TEXT("requestId is required"));
			return;
		}
		if (!GMifComponentRequests.Contains(RequestId))
		{
			Fail(Out, FString::Printf(TEXT("no request with id '%s' - it may already have been removed, ")
										   TEXT("or never existed this editor session. ")
										   TEXT("list_game_framework_component_requests names every one that IS live."),
									  *RequestId));
			return;
		}

		GMifComponentRequests.Remove(RequestId);
		Out->SetStringField(TEXT("requestId"), RequestId);
		Out->SetBoolField(TEXT("removed"), true);
	}

	// --- list_game_framework_component_requests ---------------------------------------------------
	//   in:  { }
	//   out: { requests: [{ requestId, receiverClass, componentClass, handleValid }], count }
	//
	// WHY THIS EXISTS. add_ hands back a requestId and the request stays LIVE until remove_ releases
	// it - injecting componentClass into every current and future actor of receiverClass. Nothing
	// could enumerate them, so a lost id was a leaked request: still running, still adding components,
	// with no way to name it and nothing that would even tell you it was there. Found 2026-08-31 by
	// sweeping every endpoint family for writers with no reader; of 435 endpoints and 18 families that
	// LOOKED like this shape, it was the only one that really was.
	//
	// SESSION-SCOPED, and it says so. The map lives for the editor session; a request made before a
	// restart is gone with its handle, which is why remove_'s refusal says "never existed this editor
	// session" rather than "never existed".
	void H_list_game_framework_component_requests(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {},
			TEXT("no parameters - it reports every live component request this editor session made"),
			{ { TEXT("requestId"), TEXT("not a filter - the list is small by construction and a filter that returned one row would just be remove_game_framework_component_request's error message") } }))
		{
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Rows;
		int32 Stale = 0;
		for (const TPair<FString, FMifComponentRequest>& Pair : GMifComponentRequests)
		{
			// handleValid separates "this request is doing something" from "the map still has a row
			// for it". The handle holds a weak pointer to its owning manager, so a world teardown can
			// leave the entry behind with nothing behind it.
			const bool bValid = Pair.Value.Handle.IsValid();
			if (!bValid)
			{
				++Stale;
			}
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("requestId"), Pair.Key);
			Row->SetStringField(TEXT("receiverClass"), Pair.Value.ReceiverClass);
			Row->SetStringField(TEXT("componentClass"), Pair.Value.ComponentClass);
			Row->SetBoolField(TEXT("handleValid"), bValid);
			Rows.Add(MakeShared<FJsonValueObject>(Row));
		}
		Out->SetArrayField(TEXT("requests"), Rows);
		Out->SetNumberField(TEXT("count"), Rows.Num());
		Out->SetNumberField(TEXT("staleHandles"), Stale);
		Out->SetStringField(TEXT("scopeNote"),
			TEXT("these are the requests THIS editor session made. A request from a previous session "
				 "is gone with its handle and is not listed - and cannot be removed, because the "
				 "handle that would release it no longer exists."));
		if (Stale > 0)
		{
			Out->SetStringField(TEXT("staleNote"), FString::Printf(
				TEXT("%d entr(y/ies) have an invalid handle - the owning manager went away, most "
					 "likely with its world. They inject nothing; remove_game_framework_component_request "
					 "still clears the row."),
				Stale));
		}
	}
#endif
}
