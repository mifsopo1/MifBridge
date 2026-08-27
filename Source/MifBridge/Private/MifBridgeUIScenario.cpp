// MifBridge — UI SCENARIO RUNNER: the interaction-faithful half of the UMG proposal, Phase C.
//
// WHY THIS IS ITS OWN FILE AND A NEW PATTERN. Every other endpoint in this bridge (including its own
// siblings preview_widget/preview_composite_widget) is ONE synchronous handler call: do the work,
// return. This cannot be, because "position a pawn, wait for the game's own focus/UI to react, wait
// for layout to settle" spans MULTIPLE FRAMES, and a handler blocking the game thread across frames
// would freeze the very tick that is supposed to advance the wait - the same class of self-inflicted
// hang the modal-dialog trap already describes. So this is a STATE MACHINE ticked by FTSTicker
// (the same off-thread-safe ticker MifBridgeServer.cpp itself is built on), advanced a little each
// frame, polled across MULTIPLE HTTP calls exactly the way start_pie/pie_status already established
// for PIE's own deferred startup.
//
// THE NEW HAZARD CLASS, stated plainly rather than undersold. Activation calls
// UGameViewportClient::InputKey directly - the actual entry point real input takes into a game's own
// PlayerController/EnhancedInput stack, not Slate's generic focused-widget routing send_editor_key
// uses (which depends on OS/window focus and can just as easily land on an editor widget). That is
// what makes this INTERACTION-FAITHFUL rather than a guess. It is also gameplay Blueprint code
// (OnClicked, Construct, Tick, whatever the mod's own graphs do) running SYNCHRONOUSLY on the same
// handler thread as every other endpoint. An uncaught exception is one thing; a genuine infinite
// loop in mod code is not something ANY timeout on THIS thread can interrupt, because the timeout
// check and the hung code share the same thread - a soft deadline can only catch "the condition
// never became true", never "the game thread stopped ticking at all". That second case is the
// existing modal-hang trap (bridge manual §0) with a mod-authored cause instead of an engine one,
// and it has the same fix: go look, do not wait it out.
//
// SCOPE CUT FROM THE PROPOSAL'S OWN 12-STATE ILLUSTRATION, on purpose, matching every other Phase
// this spec closed: fewer states, same safety properties (explicit steps, pollable status, hard
// deadline). No automatic actor-focus-radius calculation - playerTransform is explicit, because
// "how close is close enough to interact" is game-specific logic this bridge cannot know generically.
// No PIE lifecycle management - start_pie/pie_status already do that; this assumes PIE is already
// running and refuses cleanly if it is not.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Blueprint/UserWidget.h"
#include "Blueprint/WidgetTree.h"
#include "Blueprint/WidgetBlueprintLibrary.h"
#include "Components/Widget.h"
#include "Components/PanelWidget.h"
#include "Components/PanelSlot.h"
#include "Components/CanvasPanelSlot.h"
#include "Kismet/GameplayStatics.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/Pawn.h"
#include "GenericPlatform/IInputInterface.h"
#include "GenericPlatform/GenericPlatformInputDeviceMapper.h"
#include "InputKeyEventArgs.h"
#include "Containers/Ticker.h"
#include "HAL/PlatformTime.h"
#include "HAL/PlatformFileManager.h"
#include "GenericPlatform/GenericPlatformFile.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"
#include "ImageUtils.h"

namespace MifBridge
{
	namespace
	{
		enum class EUIScenarioState : uint8
		{
			Idle, Positioned, WaitingForStableUI, Ready, TimedOut, Failed, Stopped
		};

		const TCHAR* StateName(EUIScenarioState S)
		{
			switch (S)
			{
			case EUIScenarioState::Idle:              return TEXT("IDLE");
			case EUIScenarioState::Positioned:        return TEXT("POSITIONED");
			case EUIScenarioState::WaitingForStableUI:return TEXT("WAITING_FOR_STABLE_UI");
			case EUIScenarioState::Ready:              return TEXT("READY");
			case EUIScenarioState::TimedOut:           return TEXT("TIMED_OUT");
			case EUIScenarioState::Failed:              return TEXT("FAILED");
			case EUIScenarioState::Stopped:             return TEXT("STOPPED");
			default:                                    return TEXT("UNKNOWN");
			}
		}

		// ONE scenario at a time, deliberately - the proposal's own safety list says "refuse a second
		// conflicting session unless explicitly stopped", and everything here runs on the game thread
		// anyway, so a second concurrent scenario could only interleave in ways nobody could reason about.
		struct FUIScenario
		{
			bool bActive = false;
			EUIScenarioState State = EUIScenarioState::Idle;
			FString ScenarioId;
			FString FailReason;

			TWeakObjectPtr<UWorld> World;
			TWeakObjectPtr<APawn> Pawn;
			TWeakObjectPtr<AActor> TargetActor;
			TWeakObjectPtr<UGameViewportClient> GameViewport;

			TArray<FString> ExpectedWidgetClasses;
			int32 StableFramesRequired = 3;
			int32 StableFramesObserved = 0;
			int32 LastWidgetCount = -1;

			double StartedSeconds = 0.0;
			double DeadlineSeconds = 0.0;
			bool bActivated = false;

			FTSTicker::FDelegateHandle TickerHandle;
		};
		static FUIScenario GScenario;

		void UnregisterTicker()
		{
			if (GScenario.TickerHandle.IsValid())
			{
				FTSTicker::GetCoreTicker().RemoveTicker(GScenario.TickerHandle);
				GScenario.TickerHandle.Reset();
			}
		}

		void FailScenario(EUIScenarioState NewState, const FString& Reason)
		{
			GScenario.State = NewState;
			GScenario.FailReason = Reason;
			UnregisterTicker();
			UE_LOG(LogMifBridge, Warning, TEXT("ui_scenario %s -> %s: %s"),
				*GScenario.ScenarioId, StateName(NewState), *Reason);
		}

		// Ticked every frame while State == WaitingForStableUI. Returning true keeps it registered;
		// false unregisters (FTSTicker's own contract) - used here for every terminal transition so a
		// finished scenario cannot keep ticking forever by accident.
		bool TickScenario(float /*DeltaTime*/)
		{
			if (!GScenario.bActive || GScenario.State != EUIScenarioState::WaitingForStableUI)
			{
				return false;   // state moved on without us (capture/stop already ran) - stop ticking
			}
			if (!GScenario.World.IsValid())
			{
				FailScenario(EUIScenarioState::Failed, TEXT("PIE world no longer valid - PIE was stopped externally while this scenario was waiting."));
				return false;
			}

			const double Now = FPlatformTime::Seconds();
			if (Now > GScenario.DeadlineSeconds)
			{
				FailScenario(EUIScenarioState::TimedOut, FString::Printf(
					TEXT("timed out waiting for stable UI after %.1fs. lastWidgetCount=%d, ")
					TEXT("stableFramesObserved=%d/%d. This means the condition never became true - it ")
					TEXT("does NOT rule out the game thread having hung on something this ticker could ")
					TEXT("never observe; if the editor itself looks frozen, that is the modal-hang trap, ")
					TEXT("not this timeout."),
					(Now - GScenario.StartedSeconds), GScenario.LastWidgetCount,
					GScenario.StableFramesObserved, GScenario.StableFramesRequired));
				return false;
			}

			UWorld* World = GScenario.World.Get();
			TArray<UUserWidget*> Found;
			UWidgetBlueprintLibrary::GetAllWidgetsOfClass(World, Found, UUserWidget::StaticClass(), /*TopLevelOnly*/ false);

			// Expected classes, if named, all need at least one live instance before anything counts
			// as stable - a widget COUNT that happens to match by coincidence is not the same as the
			// widgets the caller actually asked to see.
			if (GScenario.ExpectedWidgetClasses.Num() > 0)
			{
				bool bAllPresent = true;
				for (const FString& ExpectedClass : GScenario.ExpectedWidgetClasses)
				{
					bool bThisOnePresent = false;
					for (UUserWidget* W : Found)
					{
						if (W && IsValid(W) && W->GetClass()->GetPathName() == ExpectedClass)
						{
							bThisOnePresent = true;
							break;
						}
					}
					if (!bThisOnePresent) { bAllPresent = false; break; }
				}
				if (!bAllPresent)
				{
					GScenario.LastWidgetCount = Found.Num();
					GScenario.StableFramesObserved = 0;   // not even present yet, so definitely not stable
					return true;
				}
			}

			const int32 CurrentCount = Found.Num();
			if (CurrentCount == GScenario.LastWidgetCount)
			{
				++GScenario.StableFramesObserved;
			}
			else
			{
				GScenario.StableFramesObserved = 0;
				GScenario.LastWidgetCount = CurrentCount;
			}

			if (GScenario.StableFramesObserved >= GScenario.StableFramesRequired)
			{
				GScenario.State = EUIScenarioState::Ready;
				UnregisterTicker();
				UE_LOG(LogMifBridge, Log, TEXT("ui_scenario %s -> READY (%d widgets, stable %d frames)"),
					*GScenario.ScenarioId, CurrentCount, GScenario.StableFramesObserved);
				return false;
			}
			return true;
		}

		UWorld* ResolveScenarioWorld(const FString& WantRole, FString& OutError)
		{
			TArray<UWorld*> PIEWorlds;
			CollectPIEWorlds(PIEWorlds);
			if (PIEWorlds.Num() == 0)
			{
				OutError = TEXT("no PIE world is running. This scenario runner does not manage PIE ")
					TEXT("lifecycle - start_pie, poll pie_status until state=='running', then retry.");
				return nullptr;
			}
			for (UWorld* W : PIEWorlds)
			{
				const bool bIsServer = (W->GetNetMode() != NM_Client);
				if (WantRole == TEXT("any")
					|| (WantRole == TEXT("server") && bIsServer)
					|| (WantRole == TEXT("client") && !bIsServer))
				{
					return W;
				}
			}
			OutError = FString::Printf(TEXT("no PIE world matching netMode '%s'."), *WantRole);
			return nullptr;
		}

		TSharedRef<FJsonObject> StatusJson()
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetBoolField(TEXT("active"), GScenario.bActive);
			J->SetStringField(TEXT("scenarioId"), GScenario.ScenarioId);
			J->SetStringField(TEXT("state"), StateName(GScenario.State));
			if (!GScenario.FailReason.IsEmpty())
			{
				J->SetStringField(TEXT("reason"), GScenario.FailReason);
			}
			if (GScenario.bActive)
			{
				J->SetNumberField(TEXT("elapsedSeconds"), FPlatformTime::Seconds() - GScenario.StartedSeconds);
				J->SetBoolField(TEXT("activated"), GScenario.bActivated);
				J->SetNumberField(TEXT("lastWidgetCount"), GScenario.LastWidgetCount);
				J->SetNumberField(TEXT("stableFramesObserved"), GScenario.StableFramesObserved);
				J->SetNumberField(TEXT("stableFramesRequired"), GScenario.StableFramesRequired);
				J->SetBoolField(TEXT("worldValid"), GScenario.World.IsValid());
				J->SetBoolField(TEXT("pawnValid"), GScenario.Pawn.IsValid());
				J->SetBoolField(TEXT("targetActorValid"), GScenario.TargetActor.IsValid());
			}
			return J;
		}
	}

	// --- ui_scenario_start -----------------------------------------------------------------------
	//   in:  { targetActorPath, netMode? (server|client|any, default server), playerLocation
	//          {x,y,z}, playerRotation? {pitch,yaw,roll}, playerIndex? (default 0), confirm }
	//   out: StatusJson() - state will be POSITIONED on success.
	// Moves the LOCAL PLAYER PAWN with confirm:true required - this is a real gameplay-state mutation
	// (position, and anything the pawn's own movement/collision does in response), not a read.
	void H_ui_scenario_start(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("targetActorPath"), TEXT("netMode"), TEXT("playerLocation"), TEXT("playerRotation"),
			  TEXT("playerIndex"), TEXT("confirm") },
			TEXT("targetActorPath (a live PIE actor's path, from list_pie_actors), netMode? ")
			TEXT("(server|client|any, default server), playerLocation {x,y,z} (required - explicit, ")
			TEXT("no automatic interaction-radius calculation), playerRotation? {pitch,yaw,roll}, ")
			TEXT("playerIndex? (default 0), confirm (required true - this moves the player pawn)"),
			{ { TEXT("activationKey"), TEXT("belongs to ui_scenario_activate, not start") },
			  { TEXT("expectedWidgetClasses"), TEXT("belongs to ui_scenario_activate") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("ui_scenario_start requires confirm=true - it moves the local player pawn, "
						   "a real gameplay-state mutation. Nothing was done."));
			return;
		}
		if (GScenario.bActive)
		{
			Fail(Out, FString::Printf(
				TEXT("a scenario (%s, state %s) is already active - call ui_scenario_stop first."),
				*GScenario.ScenarioId, StateName(GScenario.State)));
			return;
		}

		const FString TargetPath = JStr(In, TEXT("targetActorPath"));
		if (TargetPath.IsEmpty()) { Fail(Out, TEXT("targetActorPath is required.")); return; }
		AActor* Target = FindObject<AActor>(nullptr, *TargetPath);
		if (!Target || !IsValid(Target))
		{
			Fail(Out, FString::Printf(
				TEXT("no live actor at '%s' - list_pie_actors reports the correct path for each one."),
				*TargetPath));
			return;
		}

		const FString WantRole = JStr(In, TEXT("netMode"), TEXT("server")).ToLower();
		FString WorldError;
		UWorld* World = ResolveScenarioWorld(WantRole, WorldError);
		if (!World) { Fail(Out, WorldError); return; }
		if (Target->GetWorld() != World)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' belongs to a different world than the resolved netMode '%s' PIE world - ")
				TEXT("pass the matching netMode, or omit it (default server)."), *TargetPath, *WantRole));
			return;
		}

		const int32 PlayerIndex = JInt(In, TEXT("playerIndex"), 0);
		APawn* Pawn = UGameplayStatics::GetPlayerPawn(World, PlayerIndex);
		if (!Pawn)
		{
			Fail(Out, FString::Printf(TEXT("no player pawn at playerIndex %d in this PIE world."), PlayerIndex));
			return;
		}
		UGameViewportClient* GameViewport = World->GetGameViewport();
		if (!GameViewport)
		{
			Fail(Out, TEXT("this PIE world has no GameViewportClient - cannot deliver input or capture ")
						   TEXT("its viewport. (A dedicated-server PIE client has no viewport at all.)"));
			return;
		}

		const TSharedPtr<FJsonObject>* LocObj = nullptr;
		if (!In->TryGetObjectField(TEXT("playerLocation"), LocObj) || !LocObj)
		{
			Fail(Out, TEXT("playerLocation {x,y,z} is required - explicit, no automatic interaction-radius calculation."));
			return;
		}
		const FVector NewLoc(
			(*LocObj)->GetNumberField(TEXT("x")), (*LocObj)->GetNumberField(TEXT("y")), (*LocObj)->GetNumberField(TEXT("z")));
		FRotator NewRot = Pawn->GetActorRotation();
		const TSharedPtr<FJsonObject>* RotObj = nullptr;
		if (In->TryGetObjectField(TEXT("playerRotation"), RotObj) && RotObj)
		{
			NewRot = FRotator((*RotObj)->GetNumberField(TEXT("pitch")), (*RotObj)->GetNumberField(TEXT("yaw")),
				(*RotObj)->GetNumberField(TEXT("roll")));
		}

		Pawn->SetActorLocationAndRotation(NewLoc, NewRot, /*bSweep*/ false, nullptr, ETeleportType::TeleportPhysics);

		GScenario = FUIScenario();
		GScenario.bActive = true;
		GScenario.State = EUIScenarioState::Positioned;
		GScenario.ScenarioId = FGuid::NewGuid().ToString(EGuidFormats::Short);
		GScenario.World = World;
		GScenario.Pawn = Pawn;
		GScenario.TargetActor = Target;
		GScenario.GameViewport = GameViewport;
		GScenario.StartedSeconds = FPlatformTime::Seconds();

		const FVector ActualLoc = Pawn->GetActorLocation();
		Out->SetObjectField(TEXT("status"), StatusJson());
		Out->SetObjectField(TEXT("playerLocationActual"), Vec3(ActualLoc));
		Out->SetStringField(TEXT("note"),
			TEXT("player pawn positioned. Call ui_scenario_activate next to deliver the actual input - ")
			TEXT("that is the step which runs mod gameplay code synchronously and is the one this ")
			TEXT("bridge cannot fully protect against hanging."));
		UE_LOG(LogMifBridge, Log, TEXT("ui_scenario_start: %s @ %s, target=%s"),
			*GScenario.ScenarioId, *ActualLoc.ToString(), *TargetPath);
	}

	// --- ui_scenario_activate ---------------------------------------------------------------------
	//   in:  { activationKey? (default "F"), expectedWidgetClasses? [class paths], timeoutSeconds?
	//          (default 10), stableFrames? (default 3), confirm }
	//   out: StatusJson() - state moves to WAITING_FOR_STABLE_UI on success (poll ui_scenario_status).
	// THE hazardous step. Delivers a real key through UGameViewportClient::InputKey - the actual entry
	// point gameplay input takes, not Slate's generically-focused-widget routing send_editor_key uses.
	void H_ui_scenario_activate(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("activationKey"), TEXT("expectedWidgetClasses"), TEXT("timeoutSeconds"),
			  TEXT("stableFrames"), TEXT("confirm") },
			TEXT("activationKey? (default F), expectedWidgetClasses? [class paths to wait for], ")
			TEXT("timeoutSeconds? (default 10), stableFrames? (default 3), confirm (required true - ")
			TEXT("this delivers real input and runs gameplay code synchronously)")))
		{
			return;
		}
		if (!GScenario.bActive || GScenario.State != EUIScenarioState::Positioned)
		{
			Fail(Out, FString::Printf(
				TEXT("no scenario is in POSITIONED state (current: %s) - call ui_scenario_start first, ")
				TEXT("or this scenario was already activated."),
				GScenario.bActive ? StateName(GScenario.State) : TEXT("no active scenario")));
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("ui_scenario_activate requires confirm=true - it delivers real input and runs "
						   "whatever gameplay code responds to it, synchronously, on this handler thread. "
						   "Nothing was sent."));
			return;
		}
		if (!GScenario.World.IsValid() || !GScenario.GameViewport.IsValid())
		{
			FailScenario(EUIScenarioState::Failed, TEXT("PIE world or game viewport no longer valid."));
			Out->SetObjectField(TEXT("status"), StatusJson());
			Fail(Out, GScenario.FailReason);
			return;
		}

		const FString KeyName = JStr(In, TEXT("activationKey"), TEXT("F"));
		const FKey Key(*KeyName);
		if (!Key.IsValid())
		{
			Fail(Out, FString::Printf(TEXT("activationKey '%s' is not a registered FKey."), *KeyName));
			return;
		}

		const TArray<TSharedPtr<FJsonValue>>* ExpectedArr = nullptr;
		JArray(In, TEXT("expectedWidgetClasses"), ExpectedArr);
		if (ExpectedArr)
		{
			for (const TSharedPtr<FJsonValue>& V : *ExpectedArr)
			{
				FString S;
				if (V.IsValid() && V->TryGetString(S) && !S.IsEmpty()) { GScenario.ExpectedWidgetClasses.Add(S); }
			}
		}
		GScenario.StableFramesRequired = FMath::Clamp(JInt(In, TEXT("stableFrames"), 3), 1, 60);
		const double TimeoutSeconds = FMath::Clamp(JNum(In, TEXT("timeoutSeconds"), 10.0), 0.5, 120.0);

		UGameViewportClient* GameViewport = GScenario.GameViewport.Get();
		FViewport* Viewport = GameViewport->Viewport;
		if (!Viewport)
		{
			FailScenario(EUIScenarioState::Failed, TEXT("game viewport has no FViewport - cannot deliver input."));
			Out->SetObjectField(TEXT("status"), StatusJson());
			Fail(Out, GScenario.FailReason);
			return;
		}

		// PORTABLE SPELLING, checked in both trees rather than assumed: 5.7 added a 7th (timestamp)
		// param to FInputKeyEventArgs, keeping the 6-arg form only as UE_DEPRECATED(5.6). 5.3 has
		// ONLY the 6-arg form - the timestamp overload does not exist there at all (C2440 on the 5.3
		// probe build, "no constructor could take the source type"). The 6-arg form is therefore the
		// one spelling that compiles on both, same lesson as GAS's EGameplayModOp names.
		const FInputDeviceId DeviceId = IPlatformInputDeviceMapper::Get().GetDefaultInputDevice();
		UE_LOG(LogMifBridge, Log, TEXT("ui_scenario_activate: %s InputKey('%s') via UGameViewportClient"),
			*GScenario.ScenarioId, *KeyName);
		GameViewport->InputKey(FInputKeyEventArgs(Viewport, DeviceId, Key, IE_Pressed, 1.0f, false));
		GameViewport->InputKey(FInputKeyEventArgs(Viewport, DeviceId, Key, IE_Released, 0.0f, false));

		GScenario.bActivated = true;
		GScenario.State = EUIScenarioState::WaitingForStableUI;
		GScenario.StartedSeconds = FPlatformTime::Seconds();
		GScenario.DeadlineSeconds = GScenario.StartedSeconds + TimeoutSeconds;
		GScenario.StableFramesObserved = 0;
		GScenario.LastWidgetCount = -1;
		GScenario.TickerHandle = FTSTicker::GetCoreTicker().AddTicker(
			FTickerDelegate::CreateStatic(&TickScenario), 0.0f);

		Out->SetObjectField(TEXT("status"), StatusJson());
		Out->SetStringField(TEXT("note"),
			TEXT("input sent. Poll ui_scenario_status until state is READY, TIMED_OUT or FAILED, then ")
			TEXT("call ui_scenario_capture. A hard deadline is running - it will move to TIMED_OUT on ")
			TEXT("its own, but only if the game thread is still ticking at all (see this file's header ")
			TEXT("comment on what this timeout cannot protect against)."));
	}

	// --- ui_scenario_status ------------------------------------------------------------------------
	void H_ui_scenario_status(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {}, TEXT("no parameters"))) { return; }
		const TSharedRef<FJsonObject> Status = StatusJson();
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Field : Status->Values)
		{
			Out->SetField(Field.Key, Field.Value);
		}
	}

	// --- ui_scenario_capture -----------------------------------------------------------------------
	//   out: { path, exists, wroteFile, width, height, tree }
	// Only valid once state==READY. Captures the GAME viewport (not the editor's active viewport -
	// capture_viewport reads a different FViewport entirely) plus a geometry tree of every top-level
	// widget found, same shape as describe_live_widget.
	void H_ui_scenario_capture(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("name") }, TEXT("name? (output filename)"))) { return; }
		if (!GScenario.bActive || GScenario.State != EUIScenarioState::Ready)
		{
			Fail(Out, FString::Printf(
				TEXT("scenario is not READY (current: %s) - poll ui_scenario_status until it is."),
				GScenario.bActive ? StateName(GScenario.State) : TEXT("no active scenario")));
			return;
		}
		if (!GScenario.World.IsValid() || !GScenario.GameViewport.IsValid())
		{
			FailScenario(EUIScenarioState::Failed, TEXT("PIE world or game viewport no longer valid before capture."));
			Fail(Out, GScenario.FailReason);
			return;
		}

		UGameViewportClient* GameViewport = GScenario.GameViewport.Get();
		FViewport* Viewport = GameViewport->Viewport;
		if (!Viewport) { Fail(Out, TEXT("game viewport has no FViewport to capture.")); return; }

		const FIntPoint Size = Viewport->GetSizeXY();
		if (Size.X <= 0 || Size.Y <= 0)
		{
			Fail(Out, FString::Printf(TEXT("game viewport reports %dx%d - nothing to capture."), Size.X, Size.Y));
			return;
		}

		// Same force-redraw discipline capture_viewport uses (T194) - without it a non-realtime
		// viewport's backbuffer can be stale.
		Viewport->Invalidate();
		Viewport->Draw();
		FlushRenderingCommands();

		TArray<FColor> Pixels;
		if (!Viewport->ReadPixels(Pixels) || Pixels.Num() == 0)
		{
			Fail(Out, TEXT("reading the game viewport's pixels failed."));
			return;
		}
		for (FColor& C : Pixels) { C.A = 255; }

		// TArray64/TArrayView64, not TArray - PNGCompressImageArray's modern signature takes 64-bit
		// containers on this engine; the same pattern capture_viewport already uses.
		TArray64<uint8> PNGData;
		FImageUtils::PNGCompressImageArray(Size.X, Size.Y,
			TArrayView64<const FColor>(Pixels.GetData(), Pixels.Num()), PNGData);

		FString Name = JStr(In, TEXT("name"), TEXT("MifUIScenario"));
		Name = FPaths::MakeValidFileName(Name);
		const FString Dir = FPaths::ProjectSavedDir() / TEXT("MifBridge");
		IPlatformFile& PF = FPlatformFileManager::Get().GetPlatformFile();
		PF.CreateDirectoryTree(*Dir);
		const FString FullPath = FPaths::ConvertRelativePathToFull(Dir / (Name + TEXT(".png")));
		const bool bWrote = FFileHelper::SaveArrayToFile(PNGData, *FullPath);

		TArray<TSharedPtr<FJsonValue>> WidgetTrees;
		TArray<UUserWidget*> Found;
		UWidgetBlueprintLibrary::GetAllWidgetsOfClass(GScenario.World.Get(), Found, UUserWidget::StaticClass(), /*TopLevelOnly*/ true);
		for (UUserWidget* W : Found)
		{
			if (!W || !IsValid(W)) { continue; }
			TSharedRef<FJsonObject> Node = MakeShared<FJsonObject>();
			Node->SetStringField(TEXT("path"), W->GetPathName());
			Node->SetStringField(TEXT("class"), W->GetClass()->GetPathName());
			const FGeometry& Geo = W->GetCachedGeometry();
			TSharedRef<FJsonObject> Pos = MakeShared<FJsonObject>();
			const FVector2D AbsPos(Geo.GetAbsolutePosition());
			Pos->SetNumberField(TEXT("x"), AbsPos.X); Pos->SetNumberField(TEXT("y"), AbsPos.Y);
			Node->SetObjectField(TEXT("absolutePosition"), Pos);
			TSharedRef<FJsonObject> Sz = MakeShared<FJsonObject>();
			const FVector2D AbsSize(Geo.GetAbsoluteSize());
			Sz->SetNumberField(TEXT("x"), AbsSize.X); Sz->SetNumberField(TEXT("y"), AbsSize.Y);
			Node->SetObjectField(TEXT("absoluteSize"), Sz);
			WidgetTrees.Add(MakeShared<FJsonValueObject>(Node));
		}

		Out->SetStringField(TEXT("path"), FullPath);
		Out->SetBoolField(TEXT("exists"), PF.FileExists(*FullPath));
		Out->SetBoolField(TEXT("wroteFile"), bWrote);
		Out->SetNumberField(TEXT("width"), Size.X);
		Out->SetNumberField(TEXT("height"), Size.Y);
		Out->SetArrayField(TEXT("topLevelWidgets"), WidgetTrees);
		Out->SetStringField(TEXT("fidelity"), TEXT("pieActualInput"));
		Out->SetStringField(TEXT("note"),
			TEXT("PIE evidence, not packaged-runtime evidence - MifBridge is Editor-only. Call ")
			TEXT("describe_live_widget on any path in topLevelWidgets[] for its full nested tree ")
			TEXT("(the shape list_live_widgets/describe_live_widget already give). Call ui_scenario_stop ")
			TEXT("when done to restore this bridge to idle."));
		UE_LOG(LogMifBridge, Log, TEXT("ui_scenario_capture: %s -> %s (%d top-level widgets)"),
			*GScenario.ScenarioId, *FullPath, WidgetTrees.Num());
	}

	// --- ui_scenario_stop --------------------------------------------------------------------------
	void H_ui_scenario_stop(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {}, TEXT("no parameters"))) { return; }
		if (!GScenario.bActive)
		{
			Out->SetBoolField(TEXT("wasActive"), false);
			Out->SetStringField(TEXT("note"), TEXT("no scenario was active - nothing to stop."));
			return;
		}
		Out->SetBoolField(TEXT("wasActive"), true);
		Out->SetStringField(TEXT("finalState"), StateName(GScenario.State));
		UnregisterTicker();
		GScenario.bActive = false;
		GScenario.State = EUIScenarioState::Stopped;
		UE_LOG(LogMifBridge, Log, TEXT("ui_scenario_stop: %s (was %s)"),
			*GScenario.ScenarioId, StateName(GScenario.State));
		Out->SetObjectField(TEXT("status"), StatusJson());
	}
}
