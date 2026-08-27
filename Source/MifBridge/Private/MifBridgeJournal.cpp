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

	// Written BEFORE the handler is entered, and flushed. Everything about the ordering here is the
	// point of the file.
	void JournalCallStart(const FString& Endpoint, int32 BodyBytes)
	{
		if (!GJournal) { return; }
		GCallStartSeconds = FPlatformTime::Seconds();
		WriteRaw(FString::Printf(
			TEXT("{\"t\":\"%s\",\"ev\":\"start\",\"ep\":\"%s\",\"bytes\":%d}\n"),
			*FDateTime::UtcNow().ToIso8601(), *Esc(Endpoint), BodyBytes));
	}

	// The matching close. A `start` with no `end` is the whole diagnostic: it means the process never
	// got back here.
	void JournalCallEnd(const FString& Endpoint, bool bOk)
	{
		if (!GJournal) { return; }
		const double Ms = (FPlatformTime::Seconds() - GCallStartSeconds) * 1000.0;
		WriteRaw(FString::Printf(
			TEXT("{\"t\":\"%s\",\"ev\":\"end\",\"ep\":\"%s\",\"ok\":%s,\"ms\":%.1f}\n"),
			*FDateTime::UtcNow().ToIso8601(), *Esc(Endpoint), bOk ? TEXT("true") : TEXT("false"), Ms));
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
}
