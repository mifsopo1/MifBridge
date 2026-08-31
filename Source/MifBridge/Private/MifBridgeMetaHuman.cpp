// MifBridge — METAHUMAN CHARACTER creation and preview-actor spawn.
//
// WHY THIS EXISTS. Andre's call, 2026-08-27: build it now, unverified against real content, rather
// than wait for a project that has some. Checked before writing a line, and the check changed the
// plan - UMetaHumanCharacterEditorSubsystem (MetaHumanCharacterEditor module, 5.6+ only) is a real
// BlueprintCallable API for exactly the two things generic reflection cannot reach: INITIALISING a
// freshly created UMetaHumanCharacter asset, and SPAWNING a live preview actor bound to it.
//
// WHAT GENERIC ENDPOINTS ALREADY COVER, so this file does not re-cover it. Once MIF_WITH_METAHUMAN
// links the module, UMetaHumanCharacter becomes a resolvable UObject class like any other, so
// create_asset can already instantiate the bare object and get_property/set_property can already
// read and write every UPROPERTY on it. That is NOT what create_metahuman_character does here — a
// UMetaHumanCharacter minted by plain NewObject() is exactly the IK Rig file's warning at the top of
// itself: syntactically valid, semantically broken, ok:true. UMetaHumanCharacterFactoryNew (Epic's
// own "New MetaHuman Character" content-browser action, MetaHumanCharacterFactoryNew.cpp) does not
// stop at NewObject — it calls InitializeMetaHumanCharacter on the subsystem and asserts
// IsCharacterValid() before handing the asset back. That second step is a UFUNCTION on a SUBSYSTEM,
// not a UPROPERTY on the asset, so no generic endpoint can reach it. This file mirrors Epic's own
// factory path, using a checked FAIL instead of a fatal check().
//
// UNPROVEN, HONESTLY. Neither DDS2 (plugin absent) nor Curfew (plugin present but never enabled or
// used) has any MetaHuman content, so nothing here has been exercised against a hand-authored
// character — only against one this file mints for itself. create_metahuman_character IS fully
// round-trip testable this way (it makes its own test asset). spawn_metahuman_actor is tested against
// exactly that freshly-minted, default-identity asset — a real spawn, but not proof that every
// property combination a human might set on a character survives the pipeline. Treat both as REAL and
// RUN, not as compiled-but-never-executed — see tools/FEATURE_PARITY_SPEC.md for the honest status.
//
// PORTABILITY. Absent from the DDS2 5.3.2 fork entirely (MetaHuman Creator's plugin is UE 5.6+ only).
// Present on stock 5.7.4. Build.cs detects it and defines MIF_WITH_METAHUMAN; the endpoints stay
// REGISTERED either way, same contract as every other MIF_WITH_* family — a missing endpoint tells a
// caller nothing, a refusal naming the reason tells them everything.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Editor.h"                            // GEditor, GetEditorSubsystem<>
#include "GameFramework/Actor.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Misc/PackageName.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

#if MIF_WITH_METAHUMAN
#include "MetaHumanCharacter.h"
#include "MetaHumanCharacterEditorSubsystem.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_METAHUMAN
	namespace
	{
		/** One message for every MetaHuman endpoint on an engine without the plugin. */
		void MetaHumanUnavailable(const TSharedRef<FJsonObject>& Out, const TCHAR* What)
		{
			Fail(Out, FString::Printf(
				TEXT("%s is unavailable: this MifBridge was built against an engine with no MetaHuman ")
				TEXT("Character plugin (Engine/Plugins/MetaHuman/MetaHumanCharacter, UE 5.6+ only - ")
				TEXT("absent from the DDS2 5.3.2 fork entirely). The endpoint is still registered so ")
				TEXT("this answer is possible at all - rebuild against an engine that has it."), What));
		}
	}
#endif

#if MIF_WITH_METAHUMAN
	namespace
	{
		/** Same contract as ValidateNewUserTypePath (MifBridgeUserTypes.cpp) — deliberately NOT
		 *  shared across files: that function is file-local there on purpose, so a second file
		 *  wanting the same shape writes its own rather than risking a unity-build name collision
		 *  with a different signature. IsValidIdentifier itself IS shared (MifBridgeHandlers.h). */
		bool ValidateNewMetaHumanPath(const FString& Path, FString& OutAssetName, FString& OutError)
		{
			if (Path.IsEmpty() || !Path.StartsWith(TEXT("/Game/")))
			{
				OutError = TEXT("path required, must start with /Game/ (e.g. /Game/MetaHumans/MH_Test)");
				return false;
			}
			OutAssetName = FPackageName::GetLongPackageAssetName(Path);
			if (!IsValidIdentifier(OutAssetName))
			{
				OutError = FString::Printf(TEXT("invalid asset name '%s' (from path '%s')"), *OutAssetName, *Path);
				return false;
			}
			const FString ObjectPath = Path + TEXT(".") + OutAssetName;
			// A DELETED OBJECT IS NOT AN EXISTING ASSET (docs/06 issue 28). delete_asset ->
			// ObjectTools::DeleteAssets unregisters the asset and clears RF_Public|RF_Standalone, but
			// the UObject stays resident until a GC pass. This lookup found that corpse and refused,
			// while delete_asset - which consults the REGISTRY - answered "no asset found at package".
			// So an agent told to "delete it first" was then told there was nothing to delete, and the
			// path stayed unusable for the rest of the editor session with no way out from the bridge.
			// Reproduced live on 2026-08-31 before this was touched. IsValid() is false for a garbage
			// object, which makes the two endpoints agree on what exists.
			if (IsValid(StaticLoadObject(UObject::StaticClass(), nullptr, *ObjectPath, nullptr, LOAD_NoWarn | LOAD_Quiet)))
			{
				OutError = FString::Printf(TEXT("an asset already exists at '%s' - pick a new path or delete it first"), *ObjectPath);
				return false;
			}
			return true;
		}

		/** /Game/A/B and /Game/A/B.B both resolve, same loose-loader shape used across this codebase
		 *  (e.g. LoadMetasoundLoose in MifBridgeMetasound.cpp) — kept local rather than shared for the
		 *  same reason ValidateNewMetaHumanPath is local. */
		UMetaHumanCharacter* LoadMetaHumanCharacter(const FString& InPath, FString& OutError)
		{
			FString Path = InPath;
			Path.TrimStartAndEndInline();
			if (Path.IsEmpty())
			{
				OutError = TEXT("characterPath is required");
				return nullptr;
			}
			UObject* Obj = StaticLoadObject(UMetaHumanCharacter::StaticClass(), nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Obj && !Path.Contains(TEXT(".")))
			{
				const FString Full = Path + TEXT(".") + FPackageName::GetShortName(Path);
				Obj = StaticLoadObject(UMetaHumanCharacter::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
			if (!Obj)
			{
				OutError = FString::Printf(TEXT("could not load a UMetaHumanCharacter at '%s'"), *Path);
				return nullptr;
			}
			return Cast<UMetaHumanCharacter>(Obj);
		}
	}
#endif

	// --- create_metahuman_character ------------------------------------------------------------------
	//   in:  { path }
	//   out: { path, name, class, valid, note }
	// Mirrors UMetaHumanCharacterFactoryNew::FactoryCreateNew (Epic's own "New MetaHuman Character"
	// content-browser action) exactly, minus its fatal check() — IsCharacterValid() is read back and
	// reported as a failure, not asserted. Round-trip TESTED: this endpoint makes its own asset.
	void H_create_metahuman_character(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path") },
			TEXT("path (/Game/... - must not already exist)"),
			{ { TEXT("name"), TEXT("the asset name comes from the last segment of path") } }))
		{
			return;
		}
#if !MIF_WITH_METAHUMAN
		MetaHumanUnavailable(Out, TEXT("create_metahuman_character"));
#else
		const FString Path = JStr(In, TEXT("path"));
		FString AssetName, PathError;
		if (!ValidateNewMetaHumanPath(Path, AssetName, PathError))
		{
			Fail(Out, PathError);
			return;
		}

		UMetaHumanCharacterEditorSubsystem* Subsystem =
			GEditor ? GEditor->GetEditorSubsystem<UMetaHumanCharacterEditorSubsystem>() : nullptr;
		if (!Subsystem)
		{
			Fail(Out, TEXT("UMetaHumanCharacterEditorSubsystem is not available - the module linked but "
						   "did not initialise. NOTHING was created."));
			return;
		}

		UPackage* Package = CreatePackage(*Path);
		if (!Package)
		{
			Fail(Out, FString::Printf(TEXT("failed to create package '%s'"), *Path));
			return;
		}
		// RF_Standalone deliberately withheld until IsCharacterValid() passes below - an invalid
		// attempt is then just an unreferenced object the GC reclaims, not a ghost asset needing
		// manual cleanup.
		UMetaHumanCharacter* Character = NewObject<UMetaHumanCharacter>(
			Package, FName(*AssetName), RF_Public | RF_Transactional);
		if (!Character)
		{
			Fail(Out, TEXT("NewObject<UMetaHumanCharacter> returned null"));
			return;
		}

		Subsystem->InitializeMetaHumanCharacter(Character);
		if (!Character->IsCharacterValid())
		{
			Fail(Out, FString::Printf(
				TEXT("InitializeMetaHumanCharacter completed but IsCharacterValid() is false for '%s'. ")
				TEXT("NOTHING was registered - the object is unreferenced and will be garbage collected."),
				*Path));
			return;
		}

		Character->SetFlags(RF_Standalone);
		FAssetRegistryModule::AssetCreated(Character);
		Package->MarkPackageDirty();

		Out->SetStringField(TEXT("path"), Character->GetPathName());
		Out->SetStringField(TEXT("name"), Character->GetName());
		Out->SetStringField(TEXT("class"), Character->GetClass()->GetPathName());
		Out->SetBoolField(TEXT("valid"), true);
		Out->SetStringField(TEXT("note"),
			TEXT("created with default/archetype identity - not saved. save_package or "
				 "save_dirty_packages to persist it. spawn_metahuman_actor spawns a preview actor "
				 "bound to this asset."));
		UE_LOG(LogMifBridge, Log, TEXT("create_metahuman_character: %s"), *Character->GetPathName());
#endif
	}

	// --- spawn_metahuman_actor -----------------------------------------------------------------------
	//   in:  { characterPath (aliases: path, character) }
	//   out: { actorPath, actorLabel, actorClass, characterPath, editingSessionOpen, note }
	// Calls TryAddObjectToEdit then SpawnMetaHumanActor - the same two calls the MetaHuman Character
	// editor UI makes on open. Leaves the character registered for editing afterward
	// (editingSessionOpen: true) rather than guessing whether the caller wants a live-updating actor or
	// a one-shot spawn - no remove-from-edit lifecycle is exposed yet; that is PIE-scenario-runner
	// territory (see tools/FEATURE_PARITY_SPEC.md), not this endpoint.
	void H_spawn_metahuman_actor(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("characterPath"), TEXT("path"), TEXT("character") },
			TEXT("characterPath (aliases: path, character) - a UMetaHumanCharacter asset")))
		{
			return;
		}
#if !MIF_WITH_METAHUMAN
		MetaHumanUnavailable(Out, TEXT("spawn_metahuman_actor"));
#else
		const FString Path = JStrAny(In, { TEXT("characterPath"), TEXT("path"), TEXT("character") });
		FString LoadError;
		UMetaHumanCharacter* Character = LoadMetaHumanCharacter(Path, LoadError);
		if (!Character)
		{
			Fail(Out, LoadError);
			return;
		}

		UMetaHumanCharacterEditorSubsystem* Subsystem =
			GEditor ? GEditor->GetEditorSubsystem<UMetaHumanCharacterEditorSubsystem>() : nullptr;
		if (!Subsystem)
		{
			Fail(Out, TEXT("UMetaHumanCharacterEditorSubsystem is not available."));
			return;
		}

		UWorld* World = EditorWorld();
		if (!World)
		{
			Fail(Out, TEXT("no editor world - is a level open?"));
			return;
		}

		if (!Subsystem->TryAddObjectToEdit(Character))
		{
			Fail(Out, FString::Printf(
				TEXT("TryAddObjectToEdit refused '%s' - it may already be registered for editing "
					 "elsewhere."), *Path));
			return;
		}

		AActor* Actor = Subsystem->SpawnMetaHumanActor(Character);
		if (!Actor)
		{
			Subsystem->RemoveObjectToEdit(Character);
			Fail(Out, FString::Printf(
				TEXT("SpawnMetaHumanActor returned null for '%s'. The character was removed from the ")
				TEXT("editing session so it is not left half-open."), *Path));
			return;
		}

		Out->SetStringField(TEXT("actorPath"), Actor->GetPathName());
		Out->SetStringField(TEXT("actorLabel"), Actor->GetActorLabel());
		Out->SetStringField(TEXT("actorClass"), Actor->GetClass()->GetPathName());
		Out->SetStringField(TEXT("characterPath"), Character->GetPathName());
		Out->SetBoolField(TEXT("editingSessionOpen"), true);
		Out->SetStringField(TEXT("note"),
			TEXT("the character remains registered for editing (like the MetaHuman Character editor ")
			TEXT("leaving it open) so the actor keeps reflecting changes - there is no remove-from-edit ")
			TEXT("endpoint yet. Not saved: the actor is a normal placed actor, save_dirty_packages to ")
			TEXT("persist the level."));
		UE_LOG(LogMifBridge, Log, TEXT("spawn_metahuman_actor: %s -> %s"), *Path, *Actor->GetPathName());
#endif
	}
}
