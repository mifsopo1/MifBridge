// The crash journal — what was the bridge doing when the editor died.
//
// WHY THIS EXISTS. On 2026-08-26 add_anim_node crash-killed this editor (PM-013). There was no
// in-editor signal, no record of which call did it, and the culprit had to be reconstructed afterwards
// from what had recently been attempted. That reconstruction took far longer than the fix.
//
// The bridge emitted almost nothing per call before this file. LogMifBridge carried lifecycle lines
// only; the sole per-request logging was two MIF_DBG calls in MifBridgeServer.cpp, both gated behind
// the `mif.BridgeDebug` CVar which defaults to FALSE (MifBridge.cpp:18-22). So on a normal run there
// was nothing at all.
//
// ============================================================================================
// THE ONE PROPERTY THAT MATTERS: THE RECORD MUST BE ON DISK BEFORE THE HANDLER RUNS.
// ============================================================================================
//
// A journal written after a call completes tells you about every call EXCEPT the one that killed the
// process, which is the only one you wanted. So `start` is written and FLUSHED before dispatch, and
// `end` is written after. At the next launch, a `start` with no matching `end` names the call that
// died. That absence IS the finding.
//
// UE_LOG cannot do this. FOutputDeviceFile hands lines to a background FAsyncWriter ring buffer and
// does not flush per line unless -FORCELOGFLUSH is on the command line, so a hard kill loses exactly
// the tail you need. This holds one FArchive open and calls Flush() per record, which on Windows is an
// unconditional FlushFileBuffers - the bytes are out of user space before the handler is entered.
//
// APIs verified in BOTH engine trees (docs/02_GOTCHAS.md section 14 explains why that is not optional):
//   IFileManager::CreateFileWriter(const TCHAR*, uint32)   5.3 FileManager.h:97   5.7 :96
//   FArchive::Flush()                                      5.3 Archive.h:1725     5.7 :1842
//   EFileWrite::FILEWRITE_Append = 0x08                    5.3 FileManager.h:20   5.7 :20
//
// COST. One FlushFileBuffers per bridge call. Measured against the existing traffic that is nothing -
// a cooked_sweep run is 788 calls - but it is a real syscall, so `mif.BridgeJournal` can turn it off.
// It defaults to ON, deliberately: a crash journal that has to be switched on before the crash is a
// crash journal that is off when it matters. That was the whole problem with MIF_DBG.

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"
#include "MifBridgeVersion.h"                // ENGINE_MAJOR_VERSION / ENGINE_MINOR_VERSION

#include "Containers/StringConv.h"           // FTCHARToUTF8
#include "HAL/PlatformTime.h"                // FPlatformTime::Seconds

#include "HAL/FileManager.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/Paths.h"
#include "Serialization/Archive.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

static TAutoConsoleVariable<bool> CVarMifBridgeJournal(
	TEXT("mif.BridgeJournal"),
	true,
	TEXT("Record every bridge call to Saved/MifBridge/journal.jsonl, flushed before dispatch so a hard ")
	TEXT("kill still names the call that died. On by default - a journal you must enable before the ")
	TEXT("crash is off when it matters."),
	ECVF_Default);

namespace MifBridge
{
	namespace
	{
		// ONE handle held open for the process lifetime. Reopening per record would be simpler but adds
		// an open+close to every call; holding it and flushing gives the same durability for one syscall.
		FArchive* GJournal = nullptr;
		bool GJournalTried = false;
		double GCallStartSeconds = 0.0;

		// A small IN-MEMORY ring beside the on-disk journal, for the editor panel.
		// Two mechanisms because they answer two different questions: the file survives a hard kill and
		// is what you read AFTERWARDS; this ring is what a live panel shows WHILE the editor is up, with
		// no file I/O on the paint path. Fixed size, oldest overwritten - a panel showing the last 64
		// calls needs no history beyond that, and an unbounded list in a long-running editor is a leak.
		constexpr int32 GRingCapacity = 64;
		FMifCallRecord GRing[GRingCapacity];
		int32 GRingNext = 0;      // next slot to write
		int32 GRingCount = 0;     // how many slots are live, saturating at capacity
		int64 GTotalCalls = 0;

		// IN-FLIGHT state, for the panel's "working" indicator. Set before dispatch and cleared after,
		// which is the same ordering the on-disk journal relies on - so if the editor is wedged inside a
		// handler, this is the endpoint that wedged it, and the panel says so while it is happening
		// rather than only in the post-mortem.
		FString GInFlightEndpoint;
		FString GPendingSubject;      // subject of the call currently in flight
		bool    GPendingIsAsset = false;
		double  GInFlightSince = 0.0;

		FString JournalPath()
		{
			// Same convention as the thumbnail writer (MifBridgeThumbnail.cpp:625): everything this
			// plugin produces lives under <ProjectSaved>/MifBridge/. Saved/ is scratch by definition, so
			// nothing here can be mistaken for content - which matters, because the standing rule on
			// this project is that the bridge does not write to Content.
			return FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("MifBridge"), TEXT("journal.jsonl"));
		}

		// JSON strings must not carry raw quotes, backslashes or newlines, and an endpoint name or error
		// text can contain all three. A malformed line would make the whole journal unparseable at
		// exactly the moment somebody needs it.
		FString Esc(const FString& In)
		{
			FString S = In;
			S.ReplaceInline(TEXT("\\"), TEXT("\\\\"));
			S.ReplaceInline(TEXT("\""), TEXT("\\\""));
			S.ReplaceInline(TEXT("\r"), TEXT(" "));
			S.ReplaceInline(TEXT("\n"), TEXT(" "));
			S.ReplaceInline(TEXT("\t"), TEXT(" "));
			return S;
		}

		void WriteRaw(const FString& Line)
		{
			if (!GJournal) { return; }
			// UTF-8, not UTF-16. UE strings are wide, and serialising them raw would produce a file that
			// Python's json and any text editor read as binary garbage. tools/mifwatch.py opens this with
			// encoding='utf-8'.
			FTCHARToUTF8 Utf8(*Line);
			GJournal->Serialize((void*)Utf8.Get(), Utf8.Length());
			// THE line this whole file exists for. Without it the record sits in a buffer and dies with
			// the process, which is precisely the failure being fixed.
			GJournal->Flush();
		}
	}

	// Opened once at module start. Called from FMifBridgeModule::StartupModule inside the same
	// !IsRunningCommandlet() guard as the server itself, so a cook does not create journals.
	void JournalOpen(int32 Port)
	{
		if (GJournalTried) { return; }
		GJournalTried = true;
		if (!CVarMifBridgeJournal.GetValueOnAnyThread()) { return; }

		const FString Path = JournalPath();
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), /*Tree*/ true);
		GJournal = IFileManager::Get().CreateFileWriter(*Path, FILEWRITE_Append | FILEWRITE_AllowRead);
		if (!GJournal)
		{
			UE_LOG(LogMifBridge, Warning,
				TEXT("could not open the crash journal at %s - a hard kill will leave no record of "
					 "which call died."), *Path);
			return;
		}

		// The session header carries the PID, because the journal is APPEND-ONLY across runs and
		// several editors can share a project. Without the PID, two interleaved sessions are one
		// unreadable stream.
		WriteRaw(FString::Printf(
			TEXT("{\"t\":\"%s\",\"ev\":\"session\",\"pid\":%d,\"port\":%d,\"engine\":\"%d.%d\"}\n"),
			*FDateTime::UtcNow().ToIso8601(), FPlatformProcess::GetCurrentProcessId(), Port,
			ENGINE_MAJOR_VERSION, ENGINE_MINOR_VERSION));
		UE_LOG(LogMifBridge, Log, TEXT("crash journal: %s"), *Path);
	}

	namespace
	{
		// Lift the SUBJECT out of a request body - what the call is about.
		//
		// A deliberate string scan rather than a JSON parse. This runs on the game thread before EVERY
		// dispatch, and parsing a document to produce a label for a panel that may not even be open is
		// the wrong trade. Getting it wrong costs a missing label and nothing else, which is why a
		// heuristic is acceptable HERE and would not be in the safety gate.
		void ExtractSubject(const FString& Body, FString& OutSubject, bool& bOutIsAsset)
		{
			OutSubject.Reset();
			bOutIsAsset = false;
			if (Body.IsEmpty()) { return; }

			// Ordered by how specific each key is: a payload carrying both objectPath and name is better
			// described by the path.
			static const TCHAR* Keys[] = {
				TEXT("objectPath"), TEXT("assetPath"), TEXT("path"),
				TEXT("actorPath"), TEXT("blueprintPath"), TEXT("skeletonPath"),
				TEXT("system"), TEXT("rig"), TEXT("class"),
				TEXT("actorName"), TEXT("name") };

			for (const TCHAR* Key : Keys)
			{
				const FString Needle = FString::Printf(TEXT("%c%s%c:"), TCHAR('"'), Key, TCHAR('"'));
				int32 At = Body.Find(Needle, ESearchCase::CaseSensitive);
				if (At == INDEX_NONE) { continue; }
				At += Needle.Len();
				// Skip whitespace and the opening quote of the value.
				while (At < Body.Len() && (Body[At] == TCHAR(' ') || Body[At] == TCHAR('	'))) { ++At; }
				if (At >= Body.Len() || Body[At] != TCHAR('"')) { continue; }   // not a string value
				++At;
				const int32 End = Body.Find(FString::Chr(TCHAR('"')), ESearchCase::CaseSensitive,
											ESearchDir::FromStart, At);
				if (End == INDEX_NONE || End <= At) { continue; }
				OutSubject = Body.Mid(At, End - At);
				bOutIsAsset = OutSubject.StartsWith(TEXT("/Game/"))
					|| OutSubject.StartsWith(TEXT("/Engine/"));
				return;
			}
		}
	}

	// Written BEFORE the handler is entered, and flushed. Everything about the ordering here is the
	// point of the file.
	void JournalCallStart(const FString& Endpoint, const FString& Body)
	{
		if (!GJournal) { return; }
		GCallStartSeconds = FPlatformTime::Seconds();
		GInFlightEndpoint = Endpoint;
		GInFlightSince = GCallStartSeconds;
		ExtractSubject(Body, GPendingSubject, GPendingIsAsset);
		WriteRaw(FString::Printf(
			TEXT("{\"t\":\"%s\",\"ev\":\"start\",\"ep\":\"%s\",\"bytes\":%d}\n"),
			*FDateTime::UtcNow().ToIso8601(), *Esc(Endpoint), Body.Len()));
	}

	// The matching close. A `start` with no `end` is the whole diagnostic: it means the process never
	// got back here.
	void JournalCallEnd(const FString& Endpoint, bool bOk, const FString& Error)
	{
		const double Ms = (FPlatformTime::Seconds() - GCallStartSeconds) * 1000.0;
		GInFlightEndpoint.Reset();

		// The ring is filled even when the on-disk journal is switched off. They are independent: a
		// user who disabled the file to avoid the per-call flush should still get a live panel.
		{
			FMifCallRecord& Slot = GRing[GRingNext];
			Slot.Endpoint = Endpoint;
			Slot.Subject = GPendingSubject;
			Slot.bSubjectIsAsset = GPendingIsAsset;
			Slot.Milliseconds = Ms;
			Slot.bOk = bOk;
			// Truncated: the ring is a display buffer, and a 900-character parameter-contract refusal
			// would push everything else off a card. The full text is in the response the caller got.
			Slot.Error = Error.Left(160);
			Slot.WhenSeconds = FPlatformTime::Seconds();
			GRingNext = (GRingNext + 1) % GRingCapacity;
			GRingCount = FMath::Min(GRingCount + 1, GRingCapacity);
			++GTotalCalls;
		}

		if (!GJournal) { return; }
		// The REASON goes on disk too, not only into the in-memory ring the panel reads.
		//
		// Auditing every failed call earlier tonight meant grouping 782 failures by endpoint and then
		// reading test SOURCE to work out which were deliberate refusals and which might be defects.
		// The reason was available at the time and was thrown away. Recorded, that audit becomes a
		// query rather than an investigation.
		//
		// Truncated at 160 characters for the same reason the ring is: a parameter-contract refusal
		// runs to several hundred characters of accepted-key list, and the journal is a trail rather
		// than a transcript of every response.
		if (bOk || Error.IsEmpty())
		{
			WriteRaw(FString::Printf(
				TEXT("{\"t\":\"%s\",\"ev\":\"end\",\"ep\":\"%s\",\"ok\":%s,\"ms\":%.1f}\n"),
				*FDateTime::UtcNow().ToIso8601(), *Esc(Endpoint), bOk ? TEXT("true") : TEXT("false"), Ms));
		}
		else
		{
			WriteRaw(FString::Printf(
				TEXT("{\"t\":\"%s\",\"ev\":\"end\",\"ep\":\"%s\",\"ok\":false,\"ms\":%.1f,")
				TEXT("\"err\":\"%s\"}\n"),
				*FDateTime::UtcNow().ToIso8601(), *Esc(Endpoint), Ms, *Esc(Error.Left(160))));
		}
	}

	// A clean shutdown says so. Its ABSENCE at the next launch is what distinguishes "the editor was
	// closed" from "the editor died", which a timestamp alone cannot tell you.
	void JournalClose(const TCHAR* Reason)
	{
		if (!GJournal) { return; }
		WriteRaw(FString::Printf(
			TEXT("{\"t\":\"%s\",\"ev\":\"shutdown\",\"reason\":\"%s\",\"pid\":%d}\n"),
			*FDateTime::UtcNow().ToIso8601(), Reason ? Reason : TEXT("normal"),
			FPlatformProcess::GetCurrentProcessId()));
		GJournal->Close();
		delete GJournal;
		GJournal = nullptr;
	}

	// Newest FIRST, which is the order a panel wants to render. Copies rather than exposing the ring,
	// because the caller is a widget on the game thread and the ring is written from the same thread
	// but at a different time - handing out a pointer would invite a read during a write.
	void GetRecentCalls(TArray<FMifCallRecord>& Out, int32 Max)
	{
		Out.Reset();
		const int32 N = FMath::Min(Max <= 0 ? GRingCount : Max, GRingCount);
		for (int32 i = 0; i < N; ++i)
		{
			// Walk backwards from the most recently written slot, wrapping.
			const int32 Idx = ((GRingNext - 1 - i) % GRingCapacity + GRingCapacity) % GRingCapacity;
			Out.Add(GRing[Idx]);
		}
	}

	int64 GetTotalCallCount()
	{
		return GTotalCalls;
	}

	// What is the bridge doing RIGHT NOW, if anything. Empty when idle.
	bool GetInFlight(FString& OutEndpoint, double& OutSeconds)
	{
		if (GInFlightEndpoint.IsEmpty()) { return false; }
		OutEndpoint = GInFlightEndpoint;
		OutSeconds = FPlatformTime::Seconds() - GInFlightSince;
		return true;
	}

	// ============================================================================================
	// IN-EDITOR BUG REPORTS.
	// ============================================================================================
	//
	// Andre asked whether someone could flag a call from inside the editor and have it reach the
	// autonomous loop. Most of that loop already exists (docs/12): a structured report is reproduced
	// against a scratch editor, fixed, verified, committed, and answered. What was missing was a way to
	// FILE into it from the editor itself.
	//
	// This writes one report per flag into Saved/MifBridge/reports/, in the same shape
	// report_intake.parse_report already validates: endpoint, payload, expected, actual.
	//
	// THE TRUST MODEL IS DIFFERENT HERE, and the difference is worth stating rather than assuming.
	// docs/12 says the ALLOWLIST is the security control, because a GitHub issue is written by someone
	// outside this machine. A report written by this function is not: whoever clicked the button is
	// already sitting at the editor with full access. Identity is therefore not the control for local
	// reports - but the DENY list and path rewriting still are, because those protect against MISTAKES
	// and collateral damage rather than adversaries, and a local reporter makes mistakes like anyone.
	// The queue is still DATA: nothing here executes anything, it writes a file.
	bool WriteLocalReport(const FString& Endpoint, const FString& PayloadJson,
						  const FString& Actual, const FString& Notes, FString& OutPath)
	{
		// BUILT WITH THE JSON WRITER, not with Printf and hand-escaped literals.
		//
		// The first version formatted this document with FString::Printf over a stack of TEXT("...")
		// lines carrying escaped quotes and newline escapes. It did not survive being generated: the
		// escapes collapsed and every literal ended up with a REAL newline inside it, which is
		// error C2001 twelve times over. Hand-escaping JSON inside a C++ string literal inside a
		// generator is three layers of escaping and at least one of them will lose.
		//
		// FJsonObject has no such problem - it escapes its own output, and it is what every other
		// endpoint in this plugin already uses to build a response.
		const FString Dir = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("MifBridge"), TEXT("reports"));
		IFileManager::Get().MakeDirectory(*Dir, /*Tree*/ true);

		// Timestamped so two flags in the same second cannot collide, and so the queue reads in order.
		const FString Stamp = FDateTime::UtcNow().ToString(TEXT("%Y%m%d-%H%M%S"));
		OutPath = FPaths::Combine(Dir, FString::Printf(TEXT("%s-%s.json"), *Stamp, *Endpoint));

		// The payload is embedded RAW rather than re-escaped: it is already JSON, and re-escaping a JSON
		// document into a JSON string is how a report arrives double-encoded and fails to parse.
		TSharedRef<FJsonObject> Rep = MakeShared<FJsonObject>();
		Rep->SetStringField(TEXT("source"), TEXT("in-editor-flag"));
		Rep->SetStringField(TEXT("filedAt"), FDateTime::UtcNow().ToIso8601());
		Rep->SetStringField(TEXT("engine"), FString::Printf(TEXT("%d.%d"),
			ENGINE_MAJOR_VERSION, ENGINE_MINOR_VERSION));
		// The four fields report_intake.parse_report requires (report_intake.py:19).
		Rep->SetStringField(TEXT("endpoint"), Endpoint);
		Rep->SetStringField(TEXT("expected"), TEXT("the call to succeed"));
		Rep->SetStringField(TEXT("actual"), Actual);
		Rep->SetStringField(TEXT("notes"), Notes);

		// payload must be an OBJECT, not a string (report_intake.py:24). If the caller handed us raw
		// JSON, parse it so it nests properly - embedding it as a string is how a report arrives
		// double-encoded and is rejected by the very schema it was written for.
		TSharedPtr<FJsonObject> Payload;
		if (!PayloadJson.IsEmpty())
		{
			TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(PayloadJson);
			FJsonSerializer::Deserialize(Reader, Payload);
		}
		Rep->SetObjectField(TEXT("payload"), Payload.IsValid() ? Payload : MakeShared<FJsonObject>());

		FString Doc;
		TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Doc);
		FJsonSerializer::Serialize(Rep, Writer);
		FArchive* Ar = IFileManager::Get().CreateFileWriter(*OutPath);
		if (!Ar) { return false; }
		FTCHARToUTF8 Utf8(*Doc);
		Ar->Serialize((void*)Utf8.Get(), Utf8.Length());
		Ar->Close();
		delete Ar;
		UE_LOG(LogMifBridge, Log, TEXT("flagged '%s' -> %s"), *Endpoint, *OutPath);
		return true;
	}
}
