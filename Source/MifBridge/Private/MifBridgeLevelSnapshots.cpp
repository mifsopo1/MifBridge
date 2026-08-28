// Level Snapshots — capture and restore editor world state.
//
// Reopened 2026-08-28 after being wrongly declined earlier the same night. The decline reasoning was
// "zero plan or presence in either project" - the exact shape of mistake this project's own autopilot
// hook (autopilot-continue.js) explicitly warns against: MifBridge is a GENERAL UE5 tool, and neither
// DDS2 nor Curfew needing something yet is not the same as it being worthless to every UE5 user.
// Capture/restore of level state is useful to ANY UE5 developer doing iterative editing - "try this,
// and if it's wrong, go back" - which is exactly the gap this project's own docs/01_POSTMORTEMS.md
// keeps returning to (FTransaction::Cancel() has no rollback story; this plugin IS one).
//
// Built and verified the same way GAS/MVVM/MetaHuman were when DDS2 had no real content for them yet:
// against a FIXTURE (a scratch actor moved, snapshotted, then restored) rather than declined outright.
//
// GUARDED BY MIF_WITH_LEVELSNAPSHOTS, same pattern as every other optional-plugin file in this project.
// The module dependency was already linked (2026-08-26 breadth batch); this is the first file to use it.
//
// WHY NewObject DIRECTLY RATHER THAN TakeLevelSnapshot_Internal. That helper (LevelSnapshotsFunctionLibrary.cpp)
// creates its ULevelSnapshot with RF_NoFlags - fine for a Blueprint call where the caller holds the
// pointer for the rest of the same session, wrong here because every create_* endpoint in this codebase
// needs the object to survive as a REAL, LATER-FINDABLE asset (RF_Public | RF_Standalone |
// RF_Transactional), the same requirement create_procedural_mesh's StaticMesh has. So this file calls
// ULevelSnapshot's own public SetSnapshotName/SetSnapshotDescription/SnapshotWorld directly on a
// NewObject built with the correct flags, matching H_create_datatable's established template instead.
//
// SCRATCH-SAFE BY DESIGN, same invariant as every other create_* endpoint: registered via
// FAssetRegistryModule::AssetCreated, never Package->Save()'d, gone on editor restart.
//
// THE SAFETY CHECK THE ENGINE API DOES NOT HAVE. ApplySnapshotToWorld's own implementation
// (LevelSnapshotsFunctionLibrary.cpp) only asserts TargetWorld and Snapshot are non-null - it does NOT
// check the snapshot was taken in the world it is being applied to ("we assume the world matches",
// LevelSnapshot.h's own comment on the lower-level overload). Applying a snapshot of one level to a
// DIFFERENT open level is exactly the kind of caller mistake this project's own standing rule keeps
// finding uncaught by the engine, so apply_level_snapshot compares MapPath itself and refuses on
// mismatch rather than silently doing something undefined.

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#if MIF_WITH_LEVELSNAPSHOTS
#include "LevelSnapshotsFunctionLibrary.h"
#include "Data/LevelSnapshot.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Misc/PackageName.h"
#include "Engine/World.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_LEVELSNAPSHOTS
	static void MifNoLevelSnapshots(const TSharedRef<FJsonObject>& Out)
	{
		Fail(Out, TEXT("this engine build has no LevelSnapshots plugin, so there is nothing to capture "
					   "or restore. The endpoint exists on every build deliberately - a missing endpoint "
					   "would tell you nothing, while this tells you the plugin is what is missing."));
	}
	void H_create_level_snapshot(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoLevelSnapshots(Out);
	}
	void H_describe_level_snapshot(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoLevelSnapshots(Out);
	}
	void H_apply_level_snapshot(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoLevelSnapshots(Out);
	}
#else

	namespace
	{
		// Same shape as GeometryScript's ValidateNewMeshAssetPath - each domain in this codebase keeps
		// its own local validator rather than a shared one nobody owns the contract of. The check
		// itself (disk file OR an object already loaded in memory) is the same fix that closed the
		// create_procedural_mesh overwrite bug earlier tonight - not re-derived, applied from the start.
		bool ValidateNewSnapshotAssetPath(const FString& Path, FString& OutAssetName, FString& OutError)
		{
			if (Path.IsEmpty())
			{
				OutError = TEXT("path is required (must start with /Game/), e.g. \"/Game/Snapshots/LS_BeforeTest\"");
				return false;
			}
			if (!Path.StartsWith(TEXT("/Game/")))
			{
				OutError = FString::Printf(
					TEXT("path '%s' must start with /Game/ - this creates a new project asset, not an engine one"), *Path);
				return false;
			}
			OutAssetName = FPackageName::GetLongPackageAssetName(Path);
			if (OutAssetName.IsEmpty())
			{
				OutError = FString::Printf(TEXT("path '%s' has no asset name after the last '/'"), *Path);
				return false;
			}
			const bool bOnDisk = FPackageName::DoesPackageExistEx(
				FPackagePath::FromPackageNameChecked(Path),
				FPackageName::EPackageLocationFilter::FileSystem) != FPackageName::EPackageLocationFilter::None;
			UObject* Existing = FindObject<UObject>(nullptr, *(Path + TEXT(".") + OutAssetName));
			if (bOnDisk || Existing)
			{
				OutError = FString::Printf(
					TEXT("'%s' is already taken (%s) - create_level_snapshot never overwrites. ")
					TEXT("delete_asset the existing one first or pick another path. NOTHING was created."),
					*Path, bOnDisk ? TEXT("a package file exists on disk")
								   : TEXT("an object is already loaded there"));
				return false;
			}
			return true;
		}

		void EmitSnapshotSummary(const TSharedRef<FJsonObject>& Out, ULevelSnapshot* Snapshot)
		{
			Out->SetStringField(TEXT("assetPath"), Snapshot->GetPathName());
			Out->SetNumberField(TEXT("numSavedActors"), Snapshot->GetNumSavedActors());
			Out->SetStringField(TEXT("mapPath"), Snapshot->GetMapPath().ToString());
			Out->SetStringField(TEXT("captureTime"), Snapshot->GetCaptureTime().ToIso8601());
			Out->SetStringField(TEXT("snapshotName"), Snapshot->GetSnapshotName().ToString());
			Out->SetStringField(TEXT("description"), Snapshot->GetSnapshotDescription());
		}
	}

	// --- create_level_snapshot ------------------------------------------------------------------
	//   in:  { path, name?, description? }
	//   out: { assetPath, numSavedActors, mapPath, captureTime, snapshotName, description }
	void H_create_level_snapshot(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("name"), TEXT("description") },
			TEXT("path (alias: assetPath) - where to create the snapshot asset; name (optional, ")
			TEXT("defaults to the asset name); description (optional)"),
			{}))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath") });
		FString AssetName, PathError;
		if (!ValidateNewSnapshotAssetPath(Path, AssetName, PathError))
		{
			Fail(Out, PathError);
			return;
		}

		UWorld* World = EditorWorld();
		if (!World)
		{
			Fail(Out, TEXT("no editor world is open. NOTHING was created."));
			return;
		}

		const FString SnapshotName = JStr(In, TEXT("name"), AssetName);
		const FString Description = JStr(In, TEXT("description"), FString());

		UPackage* Package = CreatePackage(*Path);
		if (!Package)
		{
			Fail(Out, FString::Printf(TEXT("failed to create package '%s'"), *Path));
			return;
		}
		ULevelSnapshot* Snapshot = NewObject<ULevelSnapshot>(
			Package, FName(*AssetName), RF_Public | RF_Standalone | RF_Transactional);
		if (!Snapshot)
		{
			Fail(Out, TEXT("failed to allocate the new LevelSnapshot"));
			return;
		}

		Snapshot->SetSnapshotName(FName(*SnapshotName));
		Snapshot->SetSnapshotDescription(Description);
		if (!Snapshot->SnapshotWorld(World))
		{
			Fail(Out, TEXT("SnapshotWorld reported failure - see the Output Log for the engine's own ")
						  TEXT("reason (LogLevelSnapshots). The package was created but the asset was ")
						  TEXT("never registered, so it will not appear in find_assets."));
			return;
		}

		FAssetRegistryModule::AssetCreated(Snapshot);
		Package->MarkPackageDirty();

		EmitSnapshotSummary(Out, Snapshot);
		UE_LOG(LogMifBridge, Log, TEXT("create_level_snapshot: %s (%d actors, world %s)"),
			*Snapshot->GetPathName(), Snapshot->GetNumSavedActors(), *World->GetPathName());
	}

	// --- describe_level_snapshot ------------------------------------------------------------------
	//   in:  { path }
	//   out: { assetPath, numSavedActors, mapPath, captureTime, snapshotName, description }
	// READ-ONLY: reports the same summary create_level_snapshot returns, read back from a SEPARATE
	// LoadObject rather than trusted from memory - proves the asset round-trips, the same discipline
	// describe_dynamic_mesh applies to create_procedural_mesh.
	void H_describe_level_snapshot(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath") },
			TEXT("path (alias: assetPath) - a LevelSnapshot asset"),
			{}))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required (a LevelSnapshot asset)"));
			return;
		}
		ULevelSnapshot* Snapshot = LoadObject<ULevelSnapshot>(nullptr, *Path);
		if (!Snapshot)
		{
			Fail(Out, FString::Printf(TEXT("no LevelSnapshot at '%s'"), *Path));
			return;
		}

		EmitSnapshotSummary(Out, Snapshot);
	}

	// --- apply_level_snapshot --------------------------------------------------------------------
	//   in:  { path }
	//   out: { assetPath, numSavedActors, mapPath, appliedToWorld }
	// Restores every captured property to the CURRENT editor world. Refuses if the snapshot's own
	// recorded MapPath does not match the world currently open - the safety check
	// ApplySnapshotToWorld's own engine implementation does not perform itself (it only null-checks).
	void H_apply_level_snapshot(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath") },
			TEXT("path (alias: assetPath) - a LevelSnapshot asset to restore into the CURRENT editor world"),
			{}))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required (a LevelSnapshot asset)"));
			return;
		}
		ULevelSnapshot* Snapshot = LoadObject<ULevelSnapshot>(nullptr, *Path);
		if (!Snapshot)
		{
			Fail(Out, FString::Printf(TEXT("no LevelSnapshot at '%s'"), *Path));
			return;
		}

		UWorld* World = EditorWorld();
		if (!World)
		{
			Fail(Out, TEXT("no editor world is open. NOTHING was restored."));
			return;
		}

		const FSoftObjectPath CurrentWorldPath(World);
		if (Snapshot->GetMapPath() != CurrentWorldPath)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' was captured in '%s', but the currently open world is '%s' - refusing rather ")
				TEXT("than apply a snapshot to a different level than it was taken in. NOTHING was restored."),
				*Path, *Snapshot->GetMapPath().ToString(), *CurrentWorldPath.ToString()));
			return;
		}

		ULevelSnapshotsFunctionLibrary::ApplySnapshotToWorld(World, Snapshot, nullptr);

		Out->SetStringField(TEXT("assetPath"), Snapshot->GetPathName());
		Out->SetNumberField(TEXT("numSavedActors"), Snapshot->GetNumSavedActors());
		Out->SetStringField(TEXT("mapPath"), Snapshot->GetMapPath().ToString());
		Out->SetBoolField(TEXT("appliedToWorld"), true);
		UE_LOG(LogMifBridge, Log, TEXT("apply_level_snapshot: %s -> world %s"),
			*Snapshot->GetPathName(), *World->GetPathName());
	}
#endif
}
