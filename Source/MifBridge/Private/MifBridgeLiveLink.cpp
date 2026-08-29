// LiveLink — push synthetic transform data and read it back, no real capture hardware required. Works
// in the plain editor AND during PIE (both live-tested).
//
// Reopened 2026-08-28 as the concrete follow-up to the honest flag left in the LevelSnapshots entry
// earlier tonight ("LiveLink's 'needs external data source' reasoning should be treated as UNVERIFIED,
// not confirmed"). Traced ILiveLinkClient (Engine/Source/Runtime/LiveLinkInterface, an UNCONDITIONAL
// engine module - not the LiveLink PLUGIN) end to end: it is a plain IModularFeature with a
// PushSubjectStaticData_AnyThread/PushSubjectFrameData_AnyThread pair and a ForceTick() explicitly
// documented for driving LiveLink "outside of the normal engine tick workflow" - i.e. exactly the
// synchronous, no-PIE use this bridge needs. No Blueprint virtual subject, no real capture device, no
// message-bus connection required.
//
// WHY NO MIF_WITH_LIVELINK GUARD. Every type this file touches (ILiveLinkClient, ILiveLinkSource,
// ULiveLinkTransformRole, FLiveLinkTransformStaticData/FrameData) lives in LiveLinkInterface, an
// always-present engine RUNTIME module - confirmed present under Engine/Source/Runtime in both 5.3.2
// and 5.7, added to Build.cs unconditionally alongside GeometryFramework/GeometryCore for the same
// reason. What CAN be absent is a registered ILiveLinkClient implementation at runtime (the LiveLink
// PLUGIN supplies FLiveLinkClient and registers it as the modular feature) - so the guard here is a
// RUNTIME check (IModularFeatures::Get().IsModularFeatureAvailable), not a compile-time one. This is a
// genuinely different shape from every other optional-plugin file in this project, and deliberately so:
// the capability MifBridge needs was never behind the plugin boundary to begin with.
//
// THE SOURCE-REGISTRATION REQUIREMENT, found by reading FLiveLinkClient::PushSubjectStaticData_Internal
// (LiveLinkClient.cpp) rather than assumed: pushing under an arbitrary/unregistered source Guid is a
// silent no-op - `Collection->FindSource(...)` returns null and the push is dropped with no error
// surfaced to the pusher. So a real ILiveLinkSource must be registered via AddSource() first; this file
// implements one (FMifLiveLinkTestSource), the smallest legal implementation of the interface, held
// alive in a file-local static for the life of the editor session.
//
// SCRATCH-SAFE BY DESIGN, same invariant as every other endpoint here: this pushes into LiveLink's own
// in-memory subject collection, never persisted to any asset or disk. Pushing again under an
// already-used subjectName cleanly replaces the previous frame (LiveLinkClient's own existing behavior,
// not something this file added) rather than accumulating garbage.
//
// A SUBJECT GOES INVALID ~0.5s AFTER ITS LAST PUSH, REGARDLESS OF PIE - a genuine gotcha, first
// MISDIAGNOSED as a PIE-transition effect before being traced to its real cause. Manual testing (push,
// start PIE, check - each step several real seconds apart from typing/thinking time) showed a pushed
// subject reading invalid after entering PIE, which looked exactly like the transition itself was the
// cause. An automated test running the same sequence back-to-back, with far less real time between
// steps, did NOT reproduce it. That inconsistency was the tell: read FLiveLinkSubject::GetState()
// (LiveLinkSubject.cpp) rather than kept guessing - `bHasValidFrame = (FApp::GetCurrentTime() -
// GetLastPushTime() < ULiveLinkSettings::GetTimeWithoutFrameToBeConsiderAsInvalid())`, default 0.5
// seconds (LiveLinkSettings.cpp). LiveLink is built for CONTINUOUSLY STREAMING data (mocap, cameras)
// and marks a subject "Unresponsive" the moment it goes quiet, on ordinary wall-clock time - nothing
// about PIE enters into it at all. Practical consequence: describe_livelink_subject reading isValid:
// false shortly after a successful push is expected LiveLink behavior, not a bug in this bridge or a
// PIE artifact - push again immediately before reading if freshness matters.

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Features/IModularFeatures.h"
#include "ILiveLinkClient.h"
#include "ILiveLinkSource.h"
#include "Roles/LiveLinkTransformRole.h"
#include "Roles/LiveLinkTransformTypes.h"

namespace MifBridge
{
	namespace
	{
		// The smallest legal ILiveLinkSource: no real connection to manage, always valid, shuts down
		// immediately on request. Exists purely so PushSubjectStaticData_AnyThread has a registered
		// Source Guid to push under - see the file header for why an unregistered Guid silently no-ops.
		class FMifLiveLinkTestSource : public ILiveLinkSource
		{
		public:
			virtual void ReceiveClient(ILiveLinkClient* InClient, FGuid InSourceGuid) override
			{
				Client = InClient;
				SourceGuid = InSourceGuid;
			}
			virtual bool IsSourceStillValid() const override { return true; }
			virtual bool RequestSourceShutdown() override { return true; }
			virtual FText GetSourceType() const override { return NSLOCTEXT("MifBridge", "LiveLinkSourceType", "MifBridge Test Source"); }
			virtual FText GetSourceMachineName() const override { return NSLOCTEXT("MifBridge", "LiveLinkSourceMachine", "MifBridge"); }
			virtual FText GetSourceStatus() const override { return NSLOCTEXT("MifBridge", "LiveLinkSourceStatus", "Active"); }

			ILiveLinkClient* Client = nullptr;
			FGuid SourceGuid;
		};

		// File-session-lifetime, same lifetime rule as every other static registry in this codebase
		// (e.g. GMifObservedParamShapes in MifBridgeCommon.cpp) - handlers run synchronously on the game
		// thread, one at a time, so a plain static needs no locking.
		TSharedPtr<FMifLiveLinkTestSource> GMifLiveLinkSource;

		bool EnsureLiveLinkClient(ILiveLinkClient*& OutClient, FString& OutError)
		{
			if (!IModularFeatures::Get().IsModularFeatureAvailable(ILiveLinkClient::ModularFeatureName))
			{
				OutError = TEXT("no ILiveLinkClient is registered on this engine - the LiveLink plugin ")
						   TEXT("may be disabled for this project.");
				return false;
			}
			OutClient = &IModularFeatures::Get().GetModularFeature<ILiveLinkClient>(ILiveLinkClient::ModularFeatureName);
			return true;
		}

		bool EnsureMifSource(ILiveLinkClient* Client, FGuid& OutGuid, FString& OutError)
		{
			if (GMifLiveLinkSource.IsValid() && Client->HasSourceBeenAdded(GMifLiveLinkSource))
			{
				OutGuid = GMifLiveLinkSource->SourceGuid;
				return true;
			}
			GMifLiveLinkSource = MakeShared<FMifLiveLinkTestSource>();
			OutGuid = Client->AddSource(GMifLiveLinkSource);
			if (!OutGuid.IsValid())
			{
				OutError = TEXT("AddSource failed to register the scratch LiveLink source.");
				GMifLiveLinkSource.Reset();
				return false;
			}
			return true;
		}
	}

	// --- push_livelink_transform ------------------------------------------------------------------
	//   in:  { subjectName, locationX/Y/Z?, rotationPitch/Yaw/Roll?, scaleX/Y/Z? } (all default identity)
	//   out: { subjectName, isValid, role }
	// Pushes ONE synthetic transform frame under the given subject name through a scratch, session-local
	// LiveLink source. A second push under the same name cleanly replaces the frame - LiveLinkClient's
	// own existing behavior (PushSubjectStaticData_Internal detects the existing subject and either
	// clears its frames, if the role matches, or recreates it if not).
	void H_push_livelink_transform(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("subjectName"), TEXT("locationX"), TEXT("locationY"), TEXT("locationZ"),
			  TEXT("rotationPitch"), TEXT("rotationYaw"), TEXT("rotationRoll"),
			  TEXT("scaleX"), TEXT("scaleY"), TEXT("scaleZ") },
			TEXT("subjectName - the LiveLink subject to create or update; locationX/Y/Z, ")
			TEXT("rotationPitch/Yaw/Roll, scaleX/Y/Z (all optional, default identity - location 0, ")
			TEXT("rotation 0, scale 1)"),
			{}))
		{
			return;
		}

		const FString SubjectNameStr = JStr(In, TEXT("subjectName"));
		if (SubjectNameStr.IsEmpty())
		{
			Fail(Out, TEXT("subjectName is required. NOTHING was pushed."));
			return;
		}

		ILiveLinkClient* Client = nullptr;
		FString Error;
		if (!EnsureLiveLinkClient(Client, Error))
		{
			Fail(Out, Error + TEXT(" NOTHING was pushed."));
			return;
		}

		FGuid SourceGuid;
		if (!EnsureMifSource(Client, SourceGuid, Error))
		{
			Fail(Out, Error + TEXT(" NOTHING was pushed."));
			return;
		}

		const FVector Location(JNum(In, TEXT("locationX"), 0.0), JNum(In, TEXT("locationY"), 0.0), JNum(In, TEXT("locationZ"), 0.0));
		const FRotator Rotation(JNum(In, TEXT("rotationPitch"), 0.0), JNum(In, TEXT("rotationYaw"), 0.0), JNum(In, TEXT("rotationRoll"), 0.0));
		const FVector Scale(JNum(In, TEXT("scaleX"), 1.0), JNum(In, TEXT("scaleY"), 1.0), JNum(In, TEXT("scaleZ"), 1.0));
		const FTransform PushedTransform(Rotation, Location, Scale);

		const FLiveLinkSubjectKey Key(SourceGuid, FName(*SubjectNameStr));

		FLiveLinkStaticDataStruct StaticDataStruct(FLiveLinkTransformStaticData::StaticStruct());
		FLiveLinkTransformStaticData* StaticData = StaticDataStruct.Cast<FLiveLinkTransformStaticData>();
		StaticData->bIsLocationSupported = true;
		StaticData->bIsRotationSupported = true;
		StaticData->bIsScaleSupported = true;
		Client->PushSubjectStaticData_AnyThread(Key, ULiveLinkTransformRole::StaticClass(), MoveTemp(StaticDataStruct));

		FLiveLinkFrameDataStruct FrameDataStruct(FLiveLinkTransformFrameData::StaticStruct());
		FLiveLinkTransformFrameData* FrameData = FrameDataStruct.Cast<FLiveLinkTransformFrameData>();
		FrameData->Transform = PushedTransform;
		FrameData->WorldTime = FLiveLinkWorldTime(FPlatformTime::Seconds());
		Client->PushSubjectFrameData_AnyThread(Key, MoveTemp(FrameDataStruct));

		// Pushes are buffered and normally drained on the client's own tick - ForceTick() exists
		// precisely so a caller outside the normal engine loop (this bridge, mid-HTTP-request) can
		// process them synchronously rather than waiting on the next automatic tick. Documented on the
		// interface itself: "This is to be used when we want to run live link outside of the normal
		// engine tick workflow."
		Client->ForceTick();

		const bool bValid = Client->IsSubjectValid(Key);
		Out->SetStringField(TEXT("subjectName"), SubjectNameStr);
		Out->SetBoolField(TEXT("isValid"), bValid);
		Out->SetStringField(TEXT("role"), TEXT("Transform"));
		if (!bValid)
		{
			UE_LOG(LogMifBridge, Warning, TEXT("push_livelink_transform: subject '%s' pushed but reports not yet valid after ForceTick"), *SubjectNameStr);
		}
	}

	// --- describe_livelink_subject ----------------------------------------------------------------
	//   in:  { subjectName }
	//   out: { subjectName, isValid, role, transform: {locationX/Y/Z, rotationPitch/Yaw/Roll, scaleX/Y/Z} }
	// READ-ONLY: evaluates the CURRENT frame for the given subject through the same ILiveLinkClient a
	// real Blueprint/component consumer would use (EvaluateFrame_AnyThread), independent of whichever
	// source pushed it - not limited to subjects push_livelink_transform itself created.
	void H_describe_livelink_subject(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("subjectName") },
			TEXT("subjectName - the LiveLink subject to read"),
			{}))
		{
			return;
		}

		const FString SubjectNameStr = JStr(In, TEXT("subjectName"));
		if (SubjectNameStr.IsEmpty())
		{
			Fail(Out, TEXT("subjectName is required"));
			return;
		}

		ILiveLinkClient* Client = nullptr;
		FString Error;
		if (!EnsureLiveLinkClient(Client, Error))
		{
			Fail(Out, Error);
			return;
		}

		// Brace-init deliberately, not FLiveLinkSubjectName SubjectName(FName(...)) - that parses as a
		// function DECLARATION (the classic "most vexing parse"), confirmed live: MSVC's own error named
		// the type it inferred as "const FLiveLinkSubjectName (__cdecl *)(FName *)", a function pointer.
		const FLiveLinkSubjectName SubjectName{FName(*SubjectNameStr)};
		const bool bValid = Client->IsSubjectValid(SubjectName);
		Out->SetStringField(TEXT("subjectName"), SubjectNameStr);
		Out->SetBoolField(TEXT("isValid"), bValid);
		if (!bValid)
		{
			Fail(Out, FString::Printf(TEXT("no valid LiveLink subject named '%s'"), *SubjectNameStr));
			return;
		}

		FLiveLinkSubjectFrameData FrameData;
		const bool bEvaluated = Client->EvaluateFrame_AnyThread(SubjectName, ULiveLinkTransformRole::StaticClass(), FrameData);
		if (!bEvaluated)
		{
			Fail(Out, FString::Printf(
				TEXT("subject '%s' is valid but does not support the Transform role, or has no frame ")
				TEXT("data yet."), *SubjectNameStr));
			return;
		}

		Out->SetStringField(TEXT("role"), TEXT("Transform"));
		const FLiveLinkTransformFrameData* TransformFrame = FrameData.FrameData.Cast<FLiveLinkTransformFrameData>();
		if (TransformFrame)
		{
			const FVector Loc = TransformFrame->Transform.GetLocation();
			const FRotator Rot = TransformFrame->Transform.Rotator();
			const FVector Scale = TransformFrame->Transform.GetScale3D();
			TSharedRef<FJsonObject> TransformJson = MakeShared<FJsonObject>();
			TransformJson->SetNumberField(TEXT("locationX"), Loc.X);
			TransformJson->SetNumberField(TEXT("locationY"), Loc.Y);
			TransformJson->SetNumberField(TEXT("locationZ"), Loc.Z);
			TransformJson->SetNumberField(TEXT("rotationPitch"), Rot.Pitch);
			TransformJson->SetNumberField(TEXT("rotationYaw"), Rot.Yaw);
			TransformJson->SetNumberField(TEXT("rotationRoll"), Rot.Roll);
			TransformJson->SetNumberField(TEXT("scaleX"), Scale.X);
			TransformJson->SetNumberField(TEXT("scaleY"), Scale.Y);
			TransformJson->SetNumberField(TEXT("scaleZ"), Scale.Z);
			Out->SetObjectField(TEXT("transform"), TransformJson);
		}
	}
}
