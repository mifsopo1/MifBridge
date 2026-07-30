// MifBridge — pipeline hooks: read_modloader_log (runtime read-back) and trigger_cook
// (plan-only). Both are read-only: the cook/deploy pipeline runs out-of-editor on live paks,
// so this endpoint returns the verified command plan rather than executing anything.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

namespace MifBridge
{
	namespace
	{
		// Live DDS2 install root (C:\SteamLibrary, NOT D:\Steam — see docs/04, docs/11).
		static const TCHAR* GameRoot = TEXT("C:/SteamLibrary/steamapps/common/Drug Dealer Simulator 2/DrugDealerSimulator2");
		static const TCHAR* RetocExe = TEXT("C:/Users/andre/.cargo/bin/retoc.exe");

		void PushLine(TArray<TSharedPtr<FJsonValue>>& Arr, const FString& Line)
		{
			Arr.Add(MakeShared<FJsonValueString>(Line));
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
			Path = FString(GameRoot) + TEXT("/Binaries/Win64/ue4ss/UE4SS.log");
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

		// Guard against pathological log sizes stalling the game thread.
		const int64 FileSize = IFileManager::Get().FileSize(*Path);
		if (FileSize > 64 * 1024 * 1024)
		{
			Out->SetBoolField(TEXT("truncatedRead"), true);
		}

		TArray<FString> AllLines;
		if (!FFileHelper::LoadFileToStringArray(AllLines, *Path))
		{
			Fail(Out, FString::Printf(TEXT("could not read log: %s"), *Path));
			return;
		}

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

		const FString Root(GameRoot);
		const FString PaksDir = Root + TEXT("/Content/Paks");
		const FString DeployMods = Root + TEXT("/Content/Paks/Mods");
		const FString DeployLogicMods = Root + TEXT("/Content/Paks/LogicMods/") + Mod;
		const FString UE4SSLog = Root + TEXT("/Binaries/Win64/ue4ss/UE4SS.log");
		const FString Retoc(RetocExe);

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
