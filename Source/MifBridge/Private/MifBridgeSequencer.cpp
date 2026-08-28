// Sequencer / LevelSequence — reading cutscenes.
//
// Nothing in the ~290 endpoints that existed before this file could enumerate a cutscene, let alone
// describe one. find_assets can be coaxed into listing them but returns generic asset rows; there was no
// way for an agent to answer "what sequences does this project have, and what is in this one" as a first
// move. Both DDS2 and Curfew want camera work.
//
// NO MIF_WITH_* GUARD HERE, deliberately. LevelSequence is an ENGINE module, not a plugin: its
// LevelSequence.Build.cs sits under Engine/Source/Runtime in both 5.3.2 and 5.7, so it ships with every
// build and cannot be disabled the way Niagara or GAS can. The guarded pattern in MifBridgeIKRig.cpp is
// for plugins; using it here would imply a failure mode that does not exist.
//
// THE DEPRECATION TRAP IN THIS FILE, recorded because it is invisible until it is fatal:
// IAssetRegistry::GetAssetsByClass has an FName overload that 5.3 marks
//     UE_DEPRECATED(5.1, "Class names are now represented by path names...")
// and that 5.7 DELETES OUTRIGHT. Passing an FName compiles with a warning on 5.3 and FAILS TO COMPILE on
// 5.7. Every call here passes GetClassPathName(). This is the same shape as IsPendingKillOrUnreachable,
// which shipped in this plugin on 2026-08-26 and would have broken the 5.7 build Curfew depends on.

#include "MifBridgeHandlers.h"

#include "LevelSequence.h"
#include "MovieScene.h"
#include "MovieSceneBinding.h"
#include "MovieSceneTrack.h"
#include "MovieSceneSection.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Misc/FrameRate.h"

namespace MifBridge
{
	// --- list_level_sequences ------------------------------------------------
	//   in:  { filter? (aliases: search, name), limit? }
	//   out: { count, truncated?, registryStillScanning, sequences:[{ objectPath, packageName, name,
	//          loaded }] }
	// The cheapest possible entry point into this family: pure Asset Registry, LOADS NOTHING. That
	// matters more here than convenience — every other read in this file has to load the asset, and
	// docs/02_GOTCHAS.md section 6c records what loading cooked editor data can do. This one cannot
	// trip any of it, and it passes audit_read_purity unmodified.
	//
	// Field names deliberately match list_datatables key for key: objectPath is what
	// describe_level_sequence takes, packageName is what get_referencers takes. A caller who has used
	// one already knows this one.
	void H_list_level_sequences(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("filter"), TEXT("search"), TEXT("name"), TEXT("limit") },
			TEXT("filter (aliases: search, name) - substring matched against the full object path; "
				 "limit (default 0 = uncapped)"),
			{ { TEXT("path"), TEXT("list_level_sequences takes filter, a substring of the object path - describe_level_sequence is the one that takes path") },
			  { TEXT("class"), TEXT("this endpoint is ULevelSequence-only; find_assets is the one that takes a class") } }))
		{
			return;
		}

		const FString Filter = JStrAny(In, { TEXT("filter"), TEXT("search"), TEXT("name") });
		const int32 Limit = FMath::Max(0, JInt(In, TEXT("limit"), 0));

		FAssetRegistryModule& Module =
			FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
		IAssetRegistry& Registry = Module.Get();

		TArray<FAssetData> Assets;
		// GetClassPathName(), never an FName - see the file header. And the bool IS checked: this
		// project has a whole audit tool (tools/audit_postconditions.py) because discarded engine
		// answers were found here before.
		if (!Registry.GetAssetsByClass(ULevelSequence::StaticClass()->GetClassPathName(), Assets,
									   /*bSearchSubClasses*/ true))
		{
			Fail(Out, TEXT("the Asset Registry refused the query for ULevelSequence. This is not "
						   "'no sequences exist' - it is the registry declining to answer."));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Rows;
		bool bTruncated = false;
		int32 Matched = 0;
		for (const FAssetData& Asset : Assets)
		{
			const FString ObjectPath = Asset.GetObjectPathString();
			if (!Filter.IsEmpty() && !ObjectPath.Contains(Filter))
			{
				continue;
			}
			++Matched;
			if (Limit > 0 && Rows.Num() >= Limit)
			{
				bTruncated = true;
				continue;      // keep counting, so `matched` stays honest
			}
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("objectPath"), ObjectPath);
			Row->SetStringField(TEXT("packageName"), Asset.PackageName.ToString());
			Row->SetStringField(TEXT("name"), Asset.AssetName.ToString());
			Row->SetBoolField(TEXT("loaded"), Asset.IsAssetLoaded());
			Rows.Add(MakeShared<FJsonValueObject>(Row));
		}

		Out->SetNumberField(TEXT("count"), Rows.Num());
		Out->SetNumberField(TEXT("matched"), Matched);   // never let a cap look like completeness
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("sequences"), Rows);

		// A ZERO COUNT DURING A SCAN IS NOT AN ANSWER. At editor startup the registry is still
		// discovering assets and GetAssetsByClass returns a PARTIAL set while returning true, so
		// "no sequences" and "not finished looking" are indistinguishable unless this is reported.
		const bool bScanning = Registry.IsLoadingAssets();
		Out->SetBoolField(TEXT("registryStillScanning"), bScanning);
		if (bScanning)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("the Asset Registry is STILL SCANNING, so this list may be incomplete - a low or "
					 "zero count here does not mean the sequences are absent. Ask again once it settles."));
		}
	}

	// --- describe_level_sequence ---------------------------------------------
	//   in:  { path (aliases: assetPath, objectPath, sequencePath) }
	//   out: { objectPath, name, displayRate, displayRateFps, tickResolution, playbackStart/End (ticks
	//          and seconds), durationSeconds, playbackRangeLocked, counts{...}, hasCameraCutTrack }
	// What is actually IN a cutscene: how long, at what rate, how many things it possesses or spawns,
	// and whether it drives a camera. Enough to decide whether a sequence is the one you are looking
	// for without opening it.
	//
	// Time in Sequencer is two rates, and conflating them is the classic mistake: TICK RESOLUTION is
	// the internal integer frame space (24000/1 by default), DISPLAY RATE is what the UI shows (30/1).
	// A frame number is meaningless without saying which. Both are reported, and every tick value is
	// also given in seconds so a caller never has to do the conversion itself.
	void H_describe_level_sequence(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), TEXT("sequencePath") },
			TEXT("path (aliases: assetPath, objectPath, sequencePath) - a LevelSequence asset"),
			{ { TEXT("filter"), TEXT("describe_level_sequence takes one path - list_level_sequences is the one that takes filter") },
			  { TEXT("time"), TEXT("this reports the whole playback range; evaluating a sequence at a time needs a live player, which the bridge does not drive") } }))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("objectPath"),
										   TEXT("sequencePath") });
		if (Path.IsEmpty()) { Fail(Out, TEXT("path is required - a LevelSequence asset")); return; }

		ULevelSequence* Sequence = LoadObject<ULevelSequence>(nullptr, *Path);
		if (!Sequence)
		{
			// Same trailing-name retry the rest of the bridge uses: callers pass both the package
			// (/Game/Cine/LS_Intro) and the object (/Game/Cine/LS_Intro.LS_Intro).
			const FString Name = FPaths::GetBaseFilename(Path);
			Sequence = LoadObject<ULevelSequence>(nullptr, *(Path + TEXT(".") + Name));
		}
		if (!Sequence)
		{
			Fail(Out, FString::Printf(
				TEXT("no LevelSequence at '%s'. list_level_sequences enumerates them; an object path "
					 "looks like /Game/Cinematics/LS_Intro.LS_Intro."), *Path));
			return;
		}

		UMovieScene* Scene = Sequence->GetMovieScene();
		if (!Scene)
		{
			// Not a crash and not impossible: a cooked or partially-loaded sequence can answer for the
			// asset and have no MovieScene behind it. Refusing beats dereferencing.
			Fail(Out, FString::Printf(
				TEXT("'%s' has no MovieScene. The asset loaded but its contents did not - it may be "
					 "cooked, in which case its editor-only data was stripped."), *Sequence->GetName()));
			return;
		}

		const FFrameRate Tick = Scene->GetTickResolution();
		const FFrameRate Display = Scene->GetDisplayRate();
		Out->SetStringField(TEXT("objectPath"), Sequence->GetPathName());
		Out->SetStringField(TEXT("name"), Sequence->GetName());
		Out->SetStringField(TEXT("tickResolution"),
			FString::Printf(TEXT("%d/%d"), Tick.Numerator, Tick.Denominator));
		Out->SetStringField(TEXT("displayRate"),
			FString::Printf(TEXT("%d/%d"), Display.Numerator, Display.Denominator));
		Out->SetNumberField(TEXT("displayRateFps"), Display.AsDecimal());

		const TRange<FFrameNumber> Range = Scene->GetPlaybackRange();
		if (Range.GetLowerBound().IsClosed() && Range.GetUpperBound().IsClosed())
		{
			const FFrameNumber Start = Range.GetLowerBoundValue();
			const FFrameNumber End = Range.GetUpperBoundValue();
			Out->SetNumberField(TEXT("playbackStartTick"), Start.Value);
			Out->SetNumberField(TEXT("playbackEndTick"), End.Value);
			// Ticks are meaningless outside Sequencer, so give seconds too rather than making every
			// caller carry the tick resolution around to divide by.
			Out->SetNumberField(TEXT("playbackStartTime"), Tick.AsSeconds(Start));
			Out->SetNumberField(TEXT("playbackEndTime"), Tick.AsSeconds(End));
			Out->SetNumberField(TEXT("durationSeconds"), Tick.AsSeconds(End) - Tick.AsSeconds(Start));
		}
		else
		{
			Out->SetStringField(TEXT("playbackRangeNote"),
				TEXT("the playback range is unbounded on at least one end, so it has no duration."));
		}
		Out->SetBoolField(TEXT("playbackRangeLocked"), Scene->IsPlaybackRangeLocked());

		int32 TotalSections = 0;
		for (UMovieSceneSection* Section : Scene->GetAllSections())
		{
			if (Section) { ++TotalSections; }
		}

		TSharedRef<FJsonObject> Counts = MakeShared<FJsonObject>();
		// CONST pointer, deliberately: forces the const GetBindings() overload. The non-const one is
		// UE_DEPRECATED(5.7, "Getting non-const access ... is no longer allowed. Please use const
		// GetBindings()") - same reasoning already applied in MifBridgeSequencerWrite.cpp, this call
		// site was just missed since it is read-only and never needed Scene non-const for anything else.
		Counts->SetNumberField(TEXT("bindings"), const_cast<const UMovieScene*>(Scene)->GetBindings().Num());
		Counts->SetNumberField(TEXT("possessables"), Scene->GetPossessableCount());
		Counts->SetNumberField(TEXT("spawnables"), Scene->GetSpawnableCount());
		Counts->SetNumberField(TEXT("rootTracks"), Scene->GetTracks().Num());
		Counts->SetNumberField(TEXT("sections"), TotalSections);
		Out->SetObjectField(TEXT("counts"), Counts);

		// Possessables reference actors that must already exist in the level; spawnables carry their
		// own template and are created by the sequence. Which one a binding is decides whether a
		// missing actor is a broken reference or expected, so the split is worth surfacing.
		Out->SetBoolField(TEXT("hasCameraCutTrack"), Scene->GetCameraCutTrack() != nullptr);
	}
}
