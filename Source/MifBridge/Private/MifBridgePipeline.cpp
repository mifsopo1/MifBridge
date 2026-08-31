// MifBridge — pipeline hooks: read_modloader_log (runtime read-back) and trigger_cook
// (plan-only). Both are read-only: the cook/deploy pipeline runs out-of-editor on live paks,
// so this endpoint returns the verified command plan rather than executing anything.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"      // GetEnvironmentVariable - MifBridge.cpp includes it for the same reason
#include "Misc/App.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

namespace MifBridge
{
	namespace
	{
		// Live DDS2 install root (C:\SteamLibrary, NOT D:\Steam). The reference here used to be
		// "see docs/04, docs/11"; docs/04 has never existed in this repository and docs/11 here is
		// about UE4 port feasibility. Those notes are the author's separate DDS2 modding
		// documentation, not anything shipped with this plugin.
		// NEITHER OF THESE IS A LITERAL ANY MORE, for two different reasons.
		//
		// The retoc path used to read C:/Users/<author>/.cargo/bin/retoc.exe, which put the
		// author's Windows account name into every published binary and every clone of this source,
		// and was wrong for everyone else besides. Derived from %USERPROFILE% it resolves to the
		// same file on the machine it was written for - cargo installs there by definition - so
		// nothing changes here and nothing personal ships.
		//
		// Both also take an environment override so these three endpoints are usable by someone
		// whose game is not in this exact Steam folder. Unset, the defaults behave exactly as they
		// did before, so this is additive rather than a workflow change.
		FString MifGameRoot()
		{
			const FString Env = FPlatformMisc::GetEnvironmentVariable(TEXT("MIF_GAME_ROOT"));
			if (!Env.IsEmpty()) { return Env.Replace(TEXT("\\"), TEXT("/")); }
			return TEXT("C:/SteamLibrary/steamapps/common/Drug Dealer Simulator 2/DrugDealerSimulator2");
		}

		FString MifRetocExe()
		{
			const FString Env = FPlatformMisc::GetEnvironmentVariable(TEXT("MIF_RETOC_EXE"));
			if (!Env.IsEmpty()) { return Env.Replace(TEXT("\\"), TEXT("/")); }
			const FString Home = FPlatformMisc::GetEnvironmentVariable(TEXT("USERPROFILE"));
			if (!Home.IsEmpty())
			{
				return Home.Replace(TEXT("\\"), TEXT("/")) / TEXT(".cargo/bin/retoc.exe");
			}
			// No USERPROFILE at all is a broken environment rather than a supported one; say so
			// with a path that names the variable instead of silently pointing at nothing.
			return TEXT("%USERPROFILE%/.cargo/bin/retoc.exe");
		}

		void PushLine(TArray<TSharedPtr<FJsonValue>>& Arr, const FString& Line)
		{
			Arr.Add(MakeShared<FJsonValueString>(Line));
		}

		/** Read at most MaxBytes from the END of a file, as text.
		 *
		 *  WHY THIS EXISTS. Both log endpoints carried a "guard against pathological log sizes
		 *  stalling the game thread" whose entire body was `Out->SetBoolField("truncatedRead", true)`.
		 *  It read the whole file anyway - the size was computed, compared, and then never used
		 *  again - so on a 2 GB log it allocated 2 GB on the game thread and reported the truncation
		 *  it had not performed. docs/audit/work/J_dds2_project.md:342 found exactly this on
		 *  2026-07-26 and said the revision "should make the guard real (refuse or tail-read past the
		 *  cap), not inherit the flag-only behaviour". It was then copied verbatim into read_engine_log
		 *  when that endpoint was added. This is the tail-read that archive asked for.
		 *
		 *  ReadFlags matters: read_engine_log reads a file THIS process holds open for write, which
		 *  needs FILEREAD_AllowWrite or the open fails with a sharing violation.
		 *
		 *  When truncated, everything up to and including the first newline in the window is dropped,
		 *  because seeking to a byte offset lands mid-line and half a line is worse than no line. */
		bool LoadFileTail(const FString& Path, int64 MaxBytes, uint32 ReadFlags,
		                  FString& OutText, bool& bOutTruncated)
		{
			bOutTruncated = false;
			TUniquePtr<FArchive> Reader(IFileManager::Get().CreateFileReader(*Path, ReadFlags));
			if (!Reader)
			{
				return false;
			}

			const int64 Size = Reader->TotalSize();
			int64 Offset = 0;
			if (Size > MaxBytes)
			{
				Offset = Size - MaxBytes;
				bOutTruncated = true;
				Reader->Seek(Offset);
			}

			const int64 Count = Size - Offset;
			TArray<uint8> Bytes;
			Bytes.SetNumUninitialized(static_cast<int32>(Count) + 1);
			Reader->Serialize(Bytes.GetData(), Count);
			Bytes[static_cast<int32>(Count)] = 0;
			if (!Reader->Close())
			{
				return false;
			}

			OutText = FString(UTF8_TO_TCHAR(reinterpret_cast<const char*>(Bytes.GetData())));

			if (bOutTruncated)
			{
				int32 NL = INDEX_NONE;
				if (OutText.FindChar(TEXT('\n'), NL))
				{
					OutText.MidInline(NL + 1);
				}
			}
			return true;
		}
	}

	// --- read_modloader_log -------------------------------------------------
	// Tails the UE4SS.log where both Lua print() and Blueprint PrintToModLoader output land.
	// This closes the RUNTIME loop: after a cook, read what actually happened in-game.

	void H_read_modloader_log(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("lines"), TEXT("filter") },
			TEXT("path (optional - defaults to the live DDS2 UE4SS.log), lines (tail size, 1-5000, default 80), filter (plain substring)"),
			{ { TEXT("logPath"), TEXT("spell it path - or omit it entirely to tail the live DDS2 UE4SS.log") },
			  { TEXT("file"), TEXT("spell it path") },
			  { TEXT("maxLines"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") },
			  { TEXT("limit"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") },
			  { TEXT("tail"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") },
			  { TEXT("contains"), TEXT("spell it filter - a plain substring match, not a regex") },
			  { TEXT("search"), TEXT("spell it filter - a plain substring match, not a regex") } }))
		{
			return;
		}

		FString Path = JStr(In, TEXT("path"));
		if (Path.IsEmpty())
		{
			Path = MifGameRoot() + TEXT("/Binaries/Win64/ue4ss/UE4SS.log");
		}
		const int32 Lines = FMath::Clamp(JInt(In, TEXT("lines"), 80), 1, 5000);
		const FString Filter = JStr(In, TEXT("filter"));

		Out->SetStringField(TEXT("path"), Path);

		if (!FPaths::FileExists(Path))
		{
			Out->SetBoolField(TEXT("found"), false);
			Fail(Out, FString::Printf(TEXT("log file not found: %s"), *Path));
			return;
		}

		// Guard against pathological log sizes stalling the game thread. This one is REAL - it
		// tail-reads rather than setting a flag and reading the whole file anyway. See LoadFileTail.
		FString FullText;
		bool bTruncated = false;
		if (!LoadFileTail(Path, 64 * 1024 * 1024, 0, FullText, bTruncated))
		{
			Fail(Out, FString::Printf(TEXT("could not read log: %s"), *Path));
			return;
		}
		Out->SetBoolField(TEXT("truncatedRead"), bTruncated);
		if (bTruncated)
		{
			Out->SetStringField(TEXT("truncatedReadNote"),
				TEXT("log exceeded 64 MB - only the last 64 MB was read, so the oldest entries are "
					 "absent and line numbers do not correspond to the file's own"));
		}
		TArray<FString> AllLines;
		FullText.ParseIntoArrayLines(AllLines, /*InCullEmpty*/ false);

		TArray<FString> Kept;
		if (Filter.IsEmpty())
		{
			Kept = MoveTemp(AllLines);
		}
		else
		{
			for (const FString& Line : AllLines)
			{
				if (Line.Contains(Filter))
				{
					Kept.Add(Line);
				}
			}
		}

		const int32 Start = FMath::Max(0, Kept.Num() - Lines);
		TArray<TSharedPtr<FJsonValue>> Tail;
		for (int32 Index = Start; Index < Kept.Num(); ++Index)
		{
			PushLine(Tail, Kept[Index]);
		}

		Out->SetBoolField(TEXT("found"), true);
		Out->SetNumberField(TEXT("matched"), Kept.Num());
		Out->SetNumberField(TEXT("returned"), Tail.Num());
		Out->SetArrayField(TEXT("lines"), Tail);
	}

	// --- read_engine_log -----------------------------------------------------
	// Tails THIS EDITOR PROCESS'S OWN Output Log (Saved/Logs/<Project>.log) - everything any
	// UE_LOG call anywhere in the engine or project writes, including FMessageLog entries (which
	// mirror to the regular log by default). read_modloader_log tails a DIFFERENT, external log
	// (UE4SS, a packaged-game runtime); this one is the editor's own log, always live no matter
	// what plugin or subsystem is doing the logging.
	//
	// Reopened 2026-08-28/29 after a concrete, live need: diagnosing why move_actor_to's target
	// pawn never moved required triangulating the cause from list_pie_actors and engine source,
	// because there was no way to just read the actual FMessageLog("PIE") warning
	// (UAIBlueprintHelperLibrary::SimpleMoveToLocation calls it directly) that would have named the
	// cause outright. This endpoint exists so that investigation is a single call next time.
	//
	// SAME file-tailing shape as read_modloader_log on purpose - one already-proven pattern, not a
	// new one: same lines/filter contract, same size guard, same alias-rejection notes.
	void H_read_engine_log(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("lines"), TEXT("filter") },
			TEXT("lines (tail size, 1-5000, default 200), filter (plain substring) - always reads THIS ")
			TEXT("editor process's own Output Log (Saved/Logs/<Project>.log); there is no path override, ")
			TEXT("unlike read_modloader_log, because there is only ever one such log for a running process"),
			{ { TEXT("path"), TEXT("not accepted here - this always reads the current process's own ")
				TEXT("Output Log. Use read_modloader_log if you need to read a DIFFERENT log file by path") },
			  { TEXT("maxLines"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") },
			  { TEXT("limit"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") },
			  { TEXT("tail"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") },
			  { TEXT("contains"), TEXT("spell it filter - a plain substring match, not a regex") },
			  { TEXT("search"), TEXT("spell it filter - a plain substring match, not a regex") } }))
		{
			return;
		}

		const FString Path = FPaths::ConvertRelativePathToFull(
			FPaths::ProjectLogDir() / (FString(FApp::GetProjectName()) + TEXT(".log")));
		const int32 Lines = FMath::Clamp(JInt(In, TEXT("lines"), 200), 1, 5000);
		const FString Filter = JStr(In, TEXT("filter"));

		Out->SetStringField(TEXT("path"), Path);

		if (!FPaths::FileExists(Path))
		{
			Out->SetBoolField(TEXT("found"), false);
			Fail(Out, FString::Printf(TEXT("log file not found: %s (unusual - this process should be ")
				TEXT("writing to it right now)"), *Path));
			return;
		}


		// The log file is OPEN FOR WRITE by THIS SAME PROCESS the whole time, which
		// LoadFileToStringArray cannot read - live-caught, not assumed: it opens its read handle via
		// plain FILEREAD_Silent (FileHelper.cpp), which WindowsPlatformFile.cpp's OpenRead() turns
		// into a CreateFileW sharing request of FILE_SHARE_READ only, no FILE_SHARE_WRITE - a
		// sharing violation against the writer's own open handle, so it failed every time on the
		// live editor. FILEREAD_AllowWrite (FileManager.h) is the flag for exactly this case, but
		// only LoadFileToString(..., ReadFlags) exposes it - LoadFileToStringArray does not, so this
		// reads the whole file as one string with that flag and splits it into lines itself,
		// InCullEmpty=false so line numbers still line up with what a human would see in the file.
		// Same real pathological-size guard read_modloader_log uses - this log grows for the entire
		// editor session, so it can be much larger than a fresh modloader log, which is exactly why
		// the cap has to actually cap. FILEREAD_AllowWrite for the sharing reason described above.
		FString FullText;
		bool bTruncated = false;
		if (!LoadFileTail(Path, 64 * 1024 * 1024, FILEREAD_AllowWrite, FullText, bTruncated))
		{
			Fail(Out, FString::Printf(TEXT("could not read log: %s"), *Path));
			return;
		}
		Out->SetBoolField(TEXT("truncatedRead"), bTruncated);
		if (bTruncated)
		{
			Out->SetStringField(TEXT("truncatedReadNote"),
				TEXT("log exceeded 64 MB - only the last 64 MB was read, so the oldest entries are "
					 "absent and the line numbers below do not correspond to the file's own"));
		}
		TArray<FString> AllLines;
		FullText.ParseIntoArrayLines(AllLines, /*InCullEmpty*/ false);

		TArray<FString> Kept;
		if (Filter.IsEmpty())
		{
			Kept = MoveTemp(AllLines);
		}
		else
		{
			for (const FString& Line : AllLines)
			{
				if (Line.Contains(Filter))
				{
					Kept.Add(Line);
				}
			}
		}

		const int32 Start = FMath::Max(0, Kept.Num() - Lines);
		TArray<TSharedPtr<FJsonValue>> Tail;
		for (int32 Index = Start; Index < Kept.Num(); ++Index)
		{
			PushLine(Tail, Kept[Index]);
		}

		Out->SetBoolField(TEXT("found"), true);
		Out->SetNumberField(TEXT("matched"), Kept.Num());
		Out->SetNumberField(TEXT("returned"), Tail.Num());
		Out->SetArrayField(TEXT("lines"), Tail);
	}

	// --- trigger_cook -------------------------------------------------------
	// PLAN-ONLY. The DDS2 cook/deploy pipeline runs out-of-editor against the live game
	// paks; running it from inside the editor process would be wrong and unsafe. This
	// returns the verified retoc command sequence with paths pinned, executing nothing.

	void H_trigger_cook(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("mod"), TEXT("asset") },
			TEXT("mod, asset - both optional, and both only fill placeholders in the returned command plan (this endpoint executes nothing)"),
			{ { TEXT("modName"), TEXT("spell it mod") },
			  { TEXT("assetPath"), TEXT("spell it asset - it is substituted into the retoc --filter argument") },
			  { TEXT("path"), TEXT("spell it asset - it is substituted into the retoc --filter argument") },
			  { TEXT("confirm"), TEXT("trigger_cook is plan-only and runs nothing, so there is nothing to confirm") },
			  { TEXT("execute"), TEXT("trigger_cook is plan-only by design - run the returned plan yourself, out-of-editor") } }))
		{
			return;
		}

		const FString Mod = JStr(In, TEXT("mod"), TEXT("<ModName>"));
		const FString Asset = JStr(In, TEXT("asset"), TEXT("<AssetName>"));

		const FString Root = MifGameRoot();
		const FString PaksDir = Root + TEXT("/Content/Paks");
		const FString DeployMods = Root + TEXT("/Content/Paks/Mods");
		const FString DeployLogicMods = Root + TEXT("/Content/Paks/LogicMods/") + Mod;
		const FString UE4SSLog = Root + TEXT("/Binaries/Win64/ue4ss/UE4SS.log");
		const FString Retoc = MifRetocExe();

		Out->SetBoolField(TEXT("executed"), false);
		Out->SetStringField(TEXT("note"),
			TEXT("Plan only — MifBridge does not run cook/deploy from inside the editor (it operates on live game paks, out-of-editor). ")
			TEXT("Cook itself has no documented one-liner; use Brando's DDS2 SDK for content mods. The preferred DDS2 lane SKIPS cook: ")
			TEXT("retoc to-legacy (extract) -> byte-patch the .uexp (same-size swaps only) -> retoc to-zen (repack) -> deploy."));

		TArray<TSharedPtr<FJsonValue>> Plan;
		PushLine(Plan, TEXT("# 1. Extract the target asset from the live paks (INPUT must be the Paks DIRECTORY):"));
		PushLine(Plan, FString::Printf(TEXT("\"%s\" to-legacy \"%s\" <outLegacyDir> --filter %s --version UE5_3"), *Retoc, *PaksDir, *Asset));
		PushLine(Plan, TEXT("# 2. Byte-patch the .uexp in <outLegacyDir> — SAME-SIZE literal swaps only (offsets must not shift)."));
		PushLine(Plan, TEXT("# 3. Repack to a _P pak (mounts at the ORIGINAL package path):"));
		PushLine(Plan, FString::Printf(TEXT("\"%s\" to-zen <outLegacyDir> <out.utoc> --version UE5_3"), *Retoc));
		PushLine(Plan, TEXT("# 4. Parity check (do NOT trust exit codes; UnrealPak exits 255 on success):"));
		PushLine(Plan, FString::Printf(TEXT("\"%s\" list --path <out.utoc>   # ExportBundleData row must match the base-game package path"), *Retoc));
		PushLine(Plan, TEXT("# 5. Deploy the .pak/.ucas/.utoc trio to the FLAT override folder:"));
		PushLine(Plan, FString::Printf(TEXT("copy <out.pak> <out.ucas> <out.utoc> \"%s\""), *DeployMods));
		PushLine(Plan, FString::Printf(TEXT("#    (ModActor-style _P instead deploys to: \"%s\")"), *DeployLogicMods));
		PushLine(Plan, FString::Printf(TEXT("# 6. Read runtime output (Lua print + Blueprint PrintToModLoader): %s"), *UE4SSLog));
		Out->SetArrayField(TEXT("plan"), Plan);

		TSharedRef<FJsonObject> Paths = MakeShared<FJsonObject>();
		Paths->SetStringField(TEXT("retoc"), Retoc);
		Paths->SetStringField(TEXT("gameRoot"), Root);
		Paths->SetStringField(TEXT("paksDir"), PaksDir);
		Paths->SetStringField(TEXT("deployMods"), DeployMods);
		Paths->SetStringField(TEXT("deployLogicMods"), DeployLogicMods);
		Paths->SetStringField(TEXT("ue4ssLog"), UE4SSLog);
		Out->SetObjectField(TEXT("paths"), Paths);

		TArray<TSharedPtr<FJsonValue>> Caveats;
		PushLine(Caveats, TEXT("The retoc to-zen lane runs WHILE the game is open; the ModKit UnrealPak lane requires the game CLOSED (else it locks the .ucas)."));
		PushLine(Caveats, TEXT("A plain override _P pak goes to Content/Paks/Mods/ (flat), NOT Content/Paks/LogicMods/ (that folder is only for UE4SS BPModLoaderMod ModActor mods)."));
		PushLine(Caveats, TEXT("Live DDS2 install is on C:/SteamLibrary, NOT the D:/Steam path used for DDS1."));
		Out->SetArrayField(TEXT("caveats"), Caveats);
	}
}
