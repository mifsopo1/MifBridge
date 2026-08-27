// Unreal Insights trace control — the honest answer to "what is actually burning frame time".
//
// Andre: "if you can also add blueprint burning time, unreal insights to that performance tab also".
//
// The performance view next door reports a CENSUS - triangles, components, and which actors tick. That
// is a static property of the level and it is genuinely useful, but it cannot tell you that one
// Blueprint's Tick costs 4ms and another's costs 0.01ms. Nothing short of a profiler can.
//
// So rather than inventing a number, this drives the real profiler. FTraceAuxiliary::Start writes a
// .utrace file that Unreal Insights opens, with the channels that actually answer the question:
//
//   cpu       - the CPU timing graph, where a Blueprint's Tick shows up by name
//   frame     - frame boundaries, so timings can be read per frame
//   bookmark  - named markers
//   stats     - counters
//
// Verified in BOTH trees, identical signatures (docs/02_GOTCHAS.md section 14):
//   FTraceAuxiliary::Start(EConnectionType, const TCHAR* Target, const TCHAR* Channels,
//                          FOptions*, const FLogCategoryAlias&)   5.3 TraceAuxiliary.h:84   5.7 :140
//   FTraceAuxiliary::Stop()                                        5.3 :90                  5.7 :157
//   EConnectionType::File                                          present in both
//
// WHY THIS IS NOT ON THE SAFETY GATE'S UNSAFE LIST. It writes a file under Saved/ and changes no
// project content, so it is not in the family of save/PIE/cook operations the gate refuses. It does
// cost performance while running, which is why stopping is a separate, explicit call and the endpoint
// reports where the file went - a trace nobody can find is a trace nobody uses.

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "HAL/FileManager.h"
#include "Misc/DateTime.h"
#include "Misc/Paths.h"
#include "ProfilingDebugging/TraceAuxiliary.h"

namespace MifBridge
{
	namespace
	{
		// Tracked so stop can report WHAT it stopped. FTraceAuxiliary does not hand back the path it
		// wrote, and "stopped" without a filename is not an answer anyone can act on.
		FString GActiveTracePath;
	}

	// --- trace_start ----------------------------------------------------------
	//   in:  { channels? }
	//   out: { started, path, channels, note }
	void H_trace_start(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("channels") },
			TEXT("channels (default \"cpu,frame,bookmark,stats\" - the set that answers 'what is "
				 "burning frame time')"),
			{ { TEXT("duration"), TEXT("there is no duration - tracing runs until trace_stop, because a fixed window almost never contains the thing you were trying to catch") },
			  { TEXT("path"), TEXT("the destination is chosen for you under Saved/MifBridge/Traces and returned; a caller-supplied path is a way to write outside the project") } }))
		{
			return;
		}

		if (!GActiveTracePath.IsEmpty())
		{
			// Refusing beats silently restarting: a second Start would abandon the first file, and the
			// caller would be waiting on a trace that stopped growing.
			Fail(Out, FString::Printf(
				TEXT("a trace is already running, writing to %s. Call trace_stop first - starting a "
					 "second one would abandon the first."), *GActiveTracePath));
			return;
		}

		const FString Channels = JStr(In, TEXT("channels"), TEXT("cpu,frame,bookmark,stats"));
		const FString Dir = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("MifBridge"), TEXT("Traces"));
		IFileManager::Get().MakeDirectory(*Dir, /*Tree*/ true);
		const FString Path = FPaths::Combine(Dir,
			FString::Printf(TEXT("mif-%s.utrace"), *FDateTime::Now().ToString(TEXT("%Y%m%d-%H%M%S"))));

		const bool bOk = FTraceAuxiliary::Start(
			FTraceAuxiliary::EConnectionType::File, *Path, *Channels, nullptr, LogMifBridge);
		if (!bOk)
		{
			Fail(Out, FString::Printf(
				TEXT("FTraceAuxiliary::Start refused. The usual cause is that the editor was launched "
					 "with tracing already connected, or that the trace system was disabled at startup. "
					 "Target was %s."), *Path));
			return;
		}

		GActiveTracePath = Path;
		Out->SetBoolField(TEXT("started"), true);
		Out->SetStringField(TEXT("path"), Path);
		Out->SetStringField(TEXT("channels"), Channels);
		Out->SetStringField(TEXT("note"),
			TEXT("Tracing is RUNNING and costs performance while it does. Do the thing you want to "
				 "measure, then call trace_stop and open the .utrace in Unreal Insights - a Blueprint's "
				 "Tick appears there by name, which is the question the performance census cannot "
				 "answer."));
		UE_LOG(LogMifBridge, Log, TEXT("trace started -> %s (channels: %s)"), *Path, *Channels);
	}

	// --- trace_stop -----------------------------------------------------------
	//   in:  { }
	//   out: { stopped, path, sizeBytes, note }
	void H_trace_stop(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {}, TEXT("(no parameters)"),
			{ { TEXT("path"), TEXT("the path is remembered from trace_start and returned here") } }))
		{
			return;
		}

		if (GActiveTracePath.IsEmpty())
		{
			// Not a failure. "There was nothing to stop" is a true answer, and treating it as an error
			// would make an idempotent stop impossible.
			Out->SetBoolField(TEXT("stopped"), false);
			Out->SetStringField(TEXT("note"),
				TEXT("no trace was started by this bridge. If one is running, it was started elsewhere "
					 "- by a command-line argument or the editor's own trace controls - and stopping it "
					 "is not this endpoint's to do."));
			return;
		}

		const FString Path = GActiveTracePath;
		GActiveTracePath.Reset();
		FTraceAuxiliary::Stop();

		// The SIZE is the evidence the trace actually captured something. A zero-byte file means the
		// channels never produced data, which looks identical to success without this.
		const int64 Size = IFileManager::Get().FileSize(*Path);
		Out->SetBoolField(TEXT("stopped"), true);
		Out->SetStringField(TEXT("path"), Path);
		Out->SetNumberField(TEXT("sizeBytes"), (double)FMath::Max(Size, (int64)0));
		Out->SetStringField(TEXT("note"), Size > 0
			? TEXT("Open this .utrace in Unreal Insights (UnrealInsights.exe in Engine/Binaries/Win64). "
				   "The CPU track shows each Blueprint's Tick by name with its real cost.")
			: TEXT("The trace file is EMPTY or missing, which means the channels captured nothing - the "
				   "usual cause is stopping immediately after starting, before a frame elapsed."));
		UE_LOG(LogMifBridge, Log, TEXT("trace stopped -> %s (%lld bytes)"), *Path, (long long)Size);
	}
}
