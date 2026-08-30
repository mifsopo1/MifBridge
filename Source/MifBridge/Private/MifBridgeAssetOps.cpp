// MifBridge — asset lifecycle: delete_asset, rename_asset, duplicate_asset.
// The rest of the plugin edits INSIDE an asset (graph nodes, variables, DataTable rows); nothing
// could act on the asset itself — no way to clean up a scratch/test asset, reorganize content, or
// clone one. All three are /Game/-only (refuse to touch engine/plugin content) and destructive ops
// (delete/rename) are confirm-gated, matching remove_node/remove_variable/etc. All go through the
// headless (no-dialog) engine entry points, matching the rest of the plugin's no-popup design.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetRegistry/ARFilter.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "CoreGlobals.h"                         // GIsRunningUnattendedScript
#include "Misc/PackageName.h"
#include "Templates/UnrealTemplate.h"            // TGuardValue (GIsRunningUnattendedScript)
#include "Modules/ModuleManager.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Editor.h"
#include "Toolkits/IToolkit.h"
#include "ObjectTools.h"
#include "UObject/Package.h"      // UPackage - duplicate_asset reads newPackageName off GetOutermost()
#include "UObject/UObjectGlobals.h"
#include "UObject/WeakObjectPtrTemplates.h"

namespace MifBridge
{
	// Accept either a bare package path ("/Game/Foo/Bar") or an "asset.asset" path
	// ("/Game/Foo/Bar.Bar") and normalize to the package path.
	// NOT static, and declared in MifBridgeHandlers.h: MifBridgeCollision.cpp needs both of these,
	// and while it happened to compile as a file-local static (the unity build merged the two .cpp
	// into one translation unit), that is luck, not linkage — UBT regroups the unity blobs whenever
	// files are added or removed. Same reasoning as EmitAssetIdentity, which was promoted to the
	// header after duplicate file-local statics collided as C2084.
	FString NormalizePackagePath(const FString& InPath)
	{
		FString P = InPath; P.TrimStartAndEndInline();
		FString PackageOnly, AssetOnly;
		if (P.Split(TEXT("."), &PackageOnly, &AssetOnly))
		{
			return PackageOnly;
		}
		return P;
	}

	// Load the asset at Path, accepting either a bare package path or an explicit "asset.asset" path.
	UObject* LoadAssetLenient(const FString& Path)
	{
		UObject* Asset = StaticLoadObject(UObject::StaticClass(), nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Asset && !Path.Contains(TEXT(".")))
		{
			const FString Full = Path + TEXT(".") + FPackageName::GetShortName(Path);
			Asset = StaticLoadObject(UObject::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
		}
		return Asset;
	}

	// ---------------------------------------------------------------- asset-row field naming
	// GAP 8 (user report): "find_assets returns package vs path inconsistently, so callers guess."
	// This file is the other half of that report: audit_unused."path" is a PACKAGE path while
	// find_assets."path" is an OBJECT path, and duplicate_asset."newPath" is an object path while
	// rename_asset."newPath" is a package path. Same key, different meaning, no way to tell from
	// the response — so a caller piping one endpoint into the next silently misses.
	//
	// ADDITIVE fix: every legacy key keeps its exact previous value, and every asset row also
	// carries these two, which mean the same thing in EVERY endpoint of this plugin:
	//
	//   objectPath  = /Game/X/Foo.Foo_C   the object inside the package. What set_property /
	//                                     get_property / open_blueprint / describe_class take.
	//   packageName = /Game/X/Foo         the package that holds it. What get_referencers /
	//                                     get_dependencies / describe_package / delete_asset take.
	//
	// MifBridgeCooked.cpp holds these same three lines; both copies carry the same EVICTION
	// CLAUSE — promote to MifBridgeHandlers.h (the plugin's contract surface) the next time that
	// header is edited, and if a THIRD file needs it, promote instead of copying again. Exactly
	// how RejectUnknownParams travelled from file-local to shared.

	//   in:  { path: "/Game/...", confirm: true }
	//   out: { path, packageName, numDeleted, deleted: bool }
	//        path == packageName here (both the PACKAGE path) — packageName is the spelling that
	//        means the same thing on every endpoint; `path` is kept unchanged for existing callers.
	//        No objectPath: a package can hold several objects and they are gone by now.
	// Headless equivalent of Content Browser delete: ObjectTools::DeleteAssets with
	// bShowConfirmation=false so it can't block on a modal no one is there to click.
	void H_delete_asset(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("confirm") },
			TEXT("path (a /Game/ package or object path), confirm (required true)"),
			{ { TEXT("packageName"), TEXT("spell it path - delete_asset takes the package under 'path'; an object path is accepted and reduced to its package") },
			  { TEXT("objectPath"), TEXT("spell it path - the whole PACKAGE is deleted, not one object inside it") },
			  { TEXT("force"), TEXT("there is no force - deletion is gated on confirm=true and still fails if the asset is still referenced") } }))
		{
			return;
		}

		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("delete_asset requires confirm=true"));
			return;
		}
		const FString RawPath = JStr(In, TEXT("path"));
		if (RawPath.IsEmpty() || !RawPath.StartsWith(TEXT("/Game/")))
		{
			Fail(Out, TEXT("path required, must start with /Game/ (refusing to touch engine/plugin content)"));
			return;
		}
		const FString PackagePath = NormalizePackagePath(RawPath);

		IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
		TArray<FAssetData> AssetsToDelete;
		Registry.GetAssetsByPackageName(FName(*PackagePath), AssetsToDelete);
		if (AssetsToDelete.Num() == 0)
		{
			Fail(Out, FString::Printf(TEXT("no asset found at package '%s'"), *PackagePath));
			return;
		}

		// bShowConfirmation:false is NOT enough on its own, for the same reason duplicate_asset's
		// "headless" DuplicateAsset was not: the flag reaches the confirmation prompt and nothing else.
		// ObjectTools::DeleteObjects opens an ungated FMessageDialog at ObjectTools.cpp:2833 when the
		// OnAssetsCanDelete delegate vetoes the delete - which happens in ordinary situations, such as
		// an asset editor still holding the asset open - and HandleFullyLoadingPackages can prompt too.
		// A modal there would block the game thread and take the whole bridge down with it.
		//
		// Under the guard that dialog logs and returns its default instead, DeleteObjects returns 0,
		// and this reports numDeleted:0 - a clean, readable failure rather than a hang. This endpoint
		// is DENY-listed in the fuzzer, so the sweep could never have found this; it came out of
		// auditing the pattern after duplicate_asset.
		int32 NumDeleted = 0;
		{
			TGuardValue<bool> UnattendedGuard(GIsRunningUnattendedScript, true);
			NumDeleted = ObjectTools::DeleteAssets(AssetsToDelete, /*bShowConfirmation*/ false);
		}
		Out->SetStringField(TEXT("path"), PackagePath);
		Out->SetStringField(TEXT("packageName"), PackagePath);   // same value, unambiguous spelling
		Out->SetNumberField(TEXT("numDeleted"), NumDeleted);
		Out->SetBoolField(TEXT("deleted"), NumDeleted > 0);
		if (NumDeleted == 0)
		{
			// "still referenced/in use?" was a shrug: it named four different blockers at once and left
			// the caller to guess which. Agents burned calls on GC, on invented console commands and on
			// unrelated editor tooling while the actual holder was an OPEN ASSET EDITOR. Say which it is.
			TArray<TSharedPtr<FJsonValue>> OpenEditors, Referencers, Rooted;
			UAssetEditorSubsystem* AES = GEditor ? GEditor->GetEditorSubsystem<UAssetEditorSubsystem>() : nullptr;
			for (const FAssetData& AD : AssetsToDelete)
			{
				UObject* Obj = AD.FastGetAsset(false);
				if (!Obj) { continue; }
				if (AES)
				{
					for (IAssetEditorInstance* Ed : AES->FindEditorsForAssetAndSubObjects(Obj))
					{
						if (Ed) { OpenEditors.Add(MakeShared<FJsonValueString>(Ed->GetEditorName().ToString())); }
					}
				}
				// IsRooted ONLY. RF_Standalone is set on essentially every loaded asset, so including it
				// made this list fire for the normal case and carry no information - a signal that is
				// always on is noise, and it would have sent a reader chasing "rooted" when the real
				// blocker was the referencer sitting right above it.
				if (Obj->IsRooted())
				{
					Rooted.Add(MakeShared<FJsonValueString>(Obj->GetPathName()));
				}
			}
			TArray<FName> Refs;
			Registry.GetReferencers(FName(*PackagePath), Refs);
			for (const FName& R : Refs) { Referencers.Add(MakeShared<FJsonValueString>(R.ToString())); }

			TSharedRef<FJsonObject> Why = MakeShared<FJsonObject>();
			Why->SetArrayField(TEXT("openAssetEditors"), OpenEditors);
			Why->SetArrayField(TEXT("registryReferencers"), Referencers);
			Why->SetArrayField(TEXT("rootedInMemory"), Rooted);
			Out->SetObjectField(TEXT("blockedBy"), Why);

			FString Hint;
			if (OpenEditors.Num() > 0)
			{
				Hint = FString::Printf(TEXT(" %d asset editor(s) still hold it open - call close_asset_editors first."),
					OpenEditors.Num());
			}
			else if (Referencers.Num() > 0)
			{
				Hint = FString::Printf(TEXT(" %d package(s) still reference it - see blockedBy.registryReferencers."),
					Referencers.Num());
			}
			else if (Rooted.Num() > 0)
			{
				Hint = TEXT(" the object is ROOTED, so garbage collection will not release it.");
			}
			else
			{
				// All three checks clean and it STILL refused. Say that plainly rather than implying a
				// cause we did not find: a transient in-memory handle is invisible from here.
				Hint = TEXT(" no open editor, no registry referencer and not rooted - the holder is an in-memory"
					TEXT(" handle this endpoint cannot see. An editor restart releases it."));
			}
			Fail(Out, FString::Printf(TEXT("delete reported 0 assets removed for '%s'.%s"), *PackagePath, *Hint));
			return;
		}
		UE_LOG(LogMifBridge, Log, TEXT("delete_asset: %s (numDeleted=%d)"), *PackagePath, NumDeleted);
	}


	// --- close_asset_editors ------------------------------------------------
	//   in:  { path (package or object path), confirm:true }
	//   out: { packageName, hadOpenEditor, editorsFound, editorsClosed, stillOpen, editors[] }
	//
	// Deliberately SEPARATE from delete_asset rather than folded into it. Closing an open asset editor
	// can discard unsaved work in that tab, and a delete that did it silently as a side effect would be
	// exactly the hidden destruction this codebase gates behind confirm everywhere else. delete_asset
	// therefore REPORTS the open editor and points here; the caller decides.
	void H_close_asset_editors(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("objectPath"), TEXT("assetPath"), TEXT("confirm") },
			TEXT("path (aliases: objectPath, assetPath) - a /Game/ package or object path; confirm (required true)"),
			{ { TEXT("all"), TEXT("closing EVERY asset editor is not offered - name the asset you mean") },
			  { TEXT("force"), TEXT("there is no force; this finds an open editor or reports that there is none") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("close_asset_editors requires confirm=true - an open editor may hold UNSAVED work and closing it discards that work without a prompt"));
			return;
		}
		const FString Raw = JStrAny(In, { TEXT("path"), TEXT("objectPath"), TEXT("assetPath") });
		if (Raw.IsEmpty() || !Raw.StartsWith(TEXT("/")))
		{
			Fail(Out, TEXT("path required (a /Game/ package or object path)"));
			return;
		}
		UAssetEditorSubsystem* AES = GEditor ? GEditor->GetEditorSubsystem<UAssetEditorSubsystem>() : nullptr;
		if (!AES) { Fail(Out, TEXT("no UAssetEditorSubsystem")); return; }

		// Reduce an object path to its package and take every asset in it - the same addressing
		// delete_asset uses, so "the thing I could not delete" and "the thing I am closing" cannot
		// disagree about what they mean.
		FString PackagePath = Raw;
		int32 Dot = INDEX_NONE;
		if (PackagePath.FindChar(TEXT('.'), Dot)) { PackagePath = PackagePath.Left(Dot); }

		IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
		TArray<FAssetData> Assets;
		Registry.GetAssetsByPackageName(FName(*PackagePath), Assets);
		if (Assets.Num() == 0)
		{
			Fail(Out, FString::Printf(TEXT("no asset found at package '%s'"), *PackagePath));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Names;
		int32 Found = 0, Closed = 0;
		for (const FAssetData& AD : Assets)
		{
			// bLoad=false on purpose: an asset that is not loaded cannot have an editor open, and
			// loading it here to ask would be a side effect nobody requested.
			UObject* Obj = AD.FastGetAsset(false);
			if (!Obj) { continue; }
			const TArray<IAssetEditorInstance*> Editors = AES->FindEditorsForAssetAndSubObjects(Obj);
			for (IAssetEditorInstance* Ed : Editors)
			{
				if (Ed) { ++Found; Names.Add(MakeShared<FJsonValueString>(Ed->GetEditorName().ToString())); }
			}
			if (Editors.Num() > 0) { Closed += AES->CloseAllEditorsForAsset(Obj); }
		}

		// Re-ask instead of assuming it took. CloseAllEditorsForAsset is a REQUEST and a toolkit can
		// decline it (an open modal will). Reporting "closed" without re-checking would be the same
		// ok-means-nothing failure this endpoint exists to end.
		int32 StillOpen = 0;
		for (const FAssetData& AD : Assets)
		{
			if (UObject* Obj = AD.FastGetAsset(false))
			{
				StillOpen += AES->FindEditorsForAssetAndSubObjects(Obj).Num();
			}
		}

		Out->SetStringField(TEXT("packageName"), PackagePath);
		Out->SetBoolField(TEXT("hadOpenEditor"), Found > 0);
		Out->SetNumberField(TEXT("editorsFound"), Found);
		Out->SetNumberField(TEXT("editorsClosed"), Closed);
		Out->SetNumberField(TEXT("stillOpen"), StillOpen);
		Out->SetArrayField(TEXT("editors"), Names);
		if (StillOpen > 0)
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("%d editor(s) refused to close - closing is a REQUEST a toolkit can decline (an open modal will do it). The asset is still held."),
				StillOpen));
		}
	}

	//   in:  { path: "/Game/...", newPath: "/Game/NewDir/NewName", confirm: true }
	//   out: { oldPath, oldPackageName, newPath, newPackageName, newObjectPath, renamed: bool }
	//        newPath has ALWAYS been the PACKAGE path here — and an OBJECT path in duplicate_asset,
	//        one endpoint below. That is GAP 8 in one word. Both legacy keys keep their old values;
	//        read newPackageName (/Game/NewDir/NewName) or newObjectPath (/Game/NewDir/NewName.NewName)
	//        to know which you are holding. newObjectPath is read back off the renamed UObject, so
	//        it is what the engine actually produced, not a string we reassembled.
	// newPath's final segment is BOTH the destination folder and the new asset name (UE convention:
	// PackagePath/AssetName.AssetName) — same as the Content Browser's F2 rename / drag-to-folder.
	void H_rename_asset(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("newPath"), TEXT("confirm") },
			TEXT("path, newPath (the destination - its last segment is BOTH the destination folder and the new asset name), confirm (required true)"),
			{ { TEXT("newName"), TEXT("there is no newName - put the whole destination in newPath (e.g. /Game/Foo/NewName); its last segment becomes the new asset name") },
			  { TEXT("newPackageName"), TEXT("spell it newPath - newPackageName is a RESPONSE field only") },
			  { TEXT("destination"), TEXT("spell it newPath") } }))
		{
			return;
		}

		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("rename_asset requires confirm=true"));
			return;
		}
		const FString RawPath = JStr(In, TEXT("path"));
		const FString NewPath = JStr(In, TEXT("newPath"));
		if (RawPath.IsEmpty() || !RawPath.StartsWith(TEXT("/Game/")))
		{
			Fail(Out, TEXT("path required, must start with /Game/"));
			return;
		}
		if (NewPath.IsEmpty() || !NewPath.StartsWith(TEXT("/Game/")))
		{
			Fail(Out, TEXT("newPath required, must start with /Game/ (e.g. /Game/Foo/NewName)"));
			return;
		}

		UObject* Asset = LoadAssetLenient(RawPath);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: %s"), *RawPath));
			return;
		}

		const FString NewPackagePath = FPackageName::GetLongPackagePath(NewPath);
		const FString NewAssetName = FPackageName::GetLongPackageAssetName(NewPath);
		if (!IsValidIdentifier(NewAssetName))
		{
			Fail(Out, FString::Printf(TEXT("invalid new asset name '%s' (from newPath '%s')"), *NewAssetName, *NewPath));
			return;
		}

		IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>(TEXT("AssetTools")).Get();
		TArray<FAssetRenameData> Renames;
		Renames.Add(FAssetRenameData(TWeakObjectPtr<UObject>(Asset), NewPackagePath, NewAssetName));

		// NOT "headless — no dialog", which is what this line used to claim. Choosing the
		// non-WithDialog entry point only suppresses the pickers; the VALIDATION inside AssetTools
		// still calls FMessageDialog::Open directly for a name clash or an invalid path, and a modal
		// on the game thread stops this bridge answering at all. See the long note on duplicate_asset
		// below, where the sweep caught exactly that.
		const bool bOk = [&]()
		{
			TGuardValue<bool> UnattendedGuard(GIsRunningUnattendedScript, true);
			return AssetTools.RenameAssets(Renames);
		}();

		if (!bOk)
		{
			Fail(Out, FString::Printf(TEXT("rename failed: %s -> %s (target may already exist, or asset is in use)"), *RawPath, *NewPath));
			return;
		}
		Out->SetStringField(TEXT("oldPath"), NormalizePackagePath(RawPath));
		Out->SetStringField(TEXT("newPath"), NewPackagePath / NewAssetName);
		Out->SetStringField(TEXT("oldPackageName"), NormalizePackagePath(RawPath));
		Out->SetStringField(TEXT("newPackageName"), NewPackagePath / NewAssetName);
		// Off the renamed object itself rather than reassembled from strings — RenameAssets can
		// uniquify, and a caller that pastes this into set_property must get what the engine made.
		Out->SetStringField(TEXT("newObjectPath"), Asset->GetPathName());
		Out->SetBoolField(TEXT("renamed"), true);
		UE_LOG(LogMifBridge, Log, TEXT("rename_asset: %s -> %s"), *RawPath, *NewPath);
	}

	//   in:  { path: "/Game/...", newPath: "/Game/NewDir/NewName" }
	//   out: { sourcePath, sourcePackageName, newPath, newPackageName, newObjectPath, duplicated: bool }
	//        WATCH OUT (kept, not fixed, because callers depend on it): `newPath` here is an OBJECT
	//        path (/Game/NewDir/NewName.NewName) while rename_asset's `newPath` is a PACKAGE path.
	//        newObjectPath/newPackageName are the unambiguous pair — newPath == newObjectPath.
	// Not confirm-gated — purely additive, never destroys or overwrites existing data (fails instead
	// of clobbering if newPath is already taken).
	void H_duplicate_asset(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("newPath") },
			TEXT("path (the source asset), newPath (the destination - its last segment is BOTH the destination folder and the new asset name)"),
			{ { TEXT("confirm"), TEXT("duplicate_asset needs no confirm - it never overwrites; it fails if newPath is already taken") },
			  { TEXT("newName"), TEXT("there is no newName - put the whole destination in newPath (e.g. /Game/Foo/CopyName)") },
			  { TEXT("overwrite"), TEXT("NOT supported - duplicate_asset fails rather than clobbering an existing asset; delete_asset the old one first") } }))
		{
			return;
		}

		const FString RawPath = JStr(In, TEXT("path"));
		const FString NewPath = JStr(In, TEXT("newPath"));
		// The SOURCE may live under any mounted root. Requiring /Game/ here made every engine-,
		// plugin- and GameFeature-mounted asset uncopyable, which blocks the normal modding move of
		// using a shipped asset as a starting point - copying
		// /DDS2Casino/GUI/Tutorials/DT_CasinoTutorial_RichText was refused for exactly this reason.
		// Only the DESTINATION is restricted (below); that is the guard that matters, because it is
		// the only one that writes. NOTE: rename_asset keeps its /Game/-only guard deliberately -
		// renaming a shipped asset in place is not the same thing as copying one.
		if (RawPath.IsEmpty() || !RawPath.StartsWith(TEXT("/")))
		{
			Fail(Out, TEXT("path required, must be a mounted object or package path ")
				TEXT("(e.g. /Game/Foo/Bar, /Engine/..., /DDS2Casino/...)"));
			return;
		}
		if (NewPath.IsEmpty() || !NewPath.StartsWith(TEXT("/Game/")))
		{
			Fail(Out, TEXT("newPath required, must start with /Game/ (e.g. /Game/Foo/CopyName)"));
			return;
		}

		UObject* Asset = LoadAssetLenient(RawPath);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: %s"), *RawPath));
			return;
		}

		// DUPLICATING A COOKED NIAGARA ASSET CRASHES THE EDITOR, inside Niagara's own code:
		//
		//   FVersionedNiagaraEmitterData::PostLoad -> UNiagaraEmitter::PostLoad
		//   -> UNiagaraSystem::PostLoad -> UpdateSystemAfterLoad
		//   EXCEPTION_ACCESS_VIOLATION reading 0x30
		//
		// Cook strips editor-only emitter data; duplication re-runs PostLoad on the copy, which
		// dereferences it. There is no MifBridge frame at the top of that stack, so it reads as a
		// spontaneous editor death rather than as something this endpoint did.
		//
		// READING a cooked Niagara system is fine - get_property walks its ExposedParameters and
		// add_function_call reaches the runtime surface. It is DUPLICATION that dies, because that is
		// what re-runs PostLoad. Same family as the cooked-struct guard in MifBridgeUserTypes.cpp.
		//
		// Checked by CLASS NAME rather than by type on purpose: recognising an asset in order to
		// REFUSE it does not justify taking a dependency on the whole Niagara plugin module, and a
		// string check keeps working in a build where Niagara is not compiled in at all.
		// DUPLICATING A COOKED STATIC MESH ALSO CRASHES THE EDITOR, same family, different subsystem.
		// Found live 2026-08-28 duplicating a real DDS2 mesh (S_Volcano_02):
		//
		//   AssetTools.DuplicateAsset -> rebuilds the new copy -> UStaticMesh::Build
		//   Assertion failed: Owner->IsMeshDescriptionValid(0) [StaticMesh.cpp:3086]
		//
		// Cook strips the editable MeshDescription bulk data (not needed at runtime, which reads the
		// baked render/collision data instead); the post-duplicate rebuild step unconditionally
		// expects it to be there. This is a hard assertion, not a caught exception, so - same as the
		// Niagara case - it takes the whole editor down rather than returning an error.
		//
		// READING a cooked StaticMesh is fine (get_property, bounds, LOD counts, materials). It is
		// DUPLICATION specifically that dies, because that is what triggers the rebuild.
		{
			const FString AssetClassName = Asset->GetClass()->GetName();
			const bool bNiagara = AssetClassName == TEXT("NiagaraSystem")
				|| AssetClassName == TEXT("NiagaraEmitter");
			const bool bStaticMesh = AssetClassName == TEXT("StaticMesh");
			const UPackage* SrcPackage = Asset->GetOutermost();
			const bool bCooked = SrcPackage && SrcPackage->HasAnyPackageFlags(PKG_Cooked);
			if ((bNiagara || bStaticMesh) && bCooked)
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is a COOKED %s, and duplicating one CRASHES the editor %s. Cook strips "
						 "the editor-only data the post-duplicate rebuild step then dereferences. "
						 "Refused rather than attempted. Reading it is safe - it is specifically "
						 "DUPLICATION that dies."),
					*RawPath, *AssetClassName,
					bNiagara
						? TEXT("inside Niagara's own PostLoad (EXCEPTION_ACCESS_VIOLATION reading 0x30 "
							   "in FVersionedNiagaraEmitterData::PostLoad) - get_property reaches "
							   "ExposedParameters and add_function_call reaches the runtime surface "
							   "instead")
						: TEXT("inside UStaticMesh::Build (Assertion failed: "
							   "Owner->IsMeshDescriptionValid(0), StaticMesh.cpp:3086) - get_property, "
							   "bounds and LOD/material reads all still work instead")));
				return;
			}
		}

		const FString NewPackagePath = FPackageName::GetLongPackagePath(NewPath);
		const FString NewAssetName = FPackageName::GetLongPackageAssetName(NewPath);
		if (!IsValidIdentifier(NewAssetName))
		{
			Fail(Out, FString::Printf(TEXT("invalid new asset name '%s' (from newPath '%s')"), *NewAssetName, *NewPath));
			return;
		}

		// REFUSE A TAKEN DESTINATION OURSELVES.
		//
		// This endpoint's own guard text promises it "never overwrites; it fails if newPath is already
		// taken". It did not fail. AssetTools opened a modal asking a human whether to replace the
		// existing object — "If you click 'Yes', the existing object will be deleted" — and the editor
		// sat on that dialog forever. Handlers run synchronously inline on the game thread, so the
		// whole bridge stopped answering and was reported as a crash.
		if (FPackageName::DoesPackageExist(NewPath) || FindObject<UObject>(nullptr, *NewPath))
		{
			Fail(Out, FString::Printf(
				TEXT("newPath '%s' is already taken. duplicate_asset never overwrites — delete_asset ")
				TEXT("the existing one first, or pick another name. NOTHING was created."), *NewPath));
			return;
		}

		IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>(TEXT("AssetTools")).Get();

		// NOT "headless — no dialog". DuplicateAsset does pass bWithDialog=false, but that flag only
		// reaches the OVERWRITE prompt in ObjectTools::DuplicateSingleObject. Before that,
		// PerformDuplicateAsset calls CanCreateAsset (AssetTools.cpp:4287), which opens
		// FMessageDialog::Open unconditionally for an invalid name, a clash with a map file, or an
		// existing destination — and opens another itself if the source object is null.
		//
		// The check above removes the case the sweep actually hit. This guard covers the rest:
		// FMessageDialog::Open shows UI only when !FApp::IsUnattended() && !GIsRunningUnattendedScript
		// (MessageDialog.cpp:172), and otherwise logs and returns the DEFAULT — No for a YesNo, so the
		// destructive "replace the existing object" answer is declined rather than blocked on. Same
		// guard MifBridgeImport.cpp:1303 uses, and the same one the engine itself applies at
		// AssetTools.cpp:3045.
		UObject* NewAsset = nullptr;
		{
			TGuardValue<bool> UnattendedGuard(GIsRunningUnattendedScript, true);
			NewAsset = AssetTools.DuplicateAsset(NewAssetName, NewPackagePath, Asset);
		}
		if (!NewAsset)
		{
			Fail(Out, FString::Printf(TEXT("duplicate failed: %s -> %s (target may already exist)"), *RawPath, *NewPath));
			return;
		}

		// Read back off the created object: DuplicateAsset uniquifies on collision, so the caller
		// must be told what the engine actually made, not what we asked for.
		const FString CreatedPackageName = NewAsset->GetOutermost()
			? NewAsset->GetOutermost()->GetName()
			: NewPackagePath / NewAssetName;

		Out->SetStringField(TEXT("sourcePath"), NormalizePackagePath(RawPath));
		Out->SetStringField(TEXT("newPath"), NewAsset->GetPathName());
		Out->SetStringField(TEXT("sourcePackageName"), NormalizePackagePath(RawPath));
		Out->SetStringField(TEXT("newObjectPath"), NewAsset->GetPathName());
		Out->SetStringField(TEXT("newPackageName"), CreatedPackageName);
		Out->SetBoolField(TEXT("duplicated"), true);
		UE_LOG(LogMifBridge, Log, TEXT("duplicate_asset: %s -> %s"), *RawPath, *NewAsset->GetPathName());
	}

	// ------------------------------------------------------------------ reference queries
	// Added 2026-07-28. Nothing in the plugin could answer "is this asset actually used?" - the only
	// options from outside were byte-scanning .uasset files, which is wrong in a specific way: UE
	// serialises a trailing _<digits> as a SEPARATE FName number, so "SM_Foo_3" is stored as base
	// "SM_Foo" + 4 and a literal search for the full name silently misses real references. The asset
	// registry already holds the true dependency graph, so we just expose it.
	//
	// CAVEAT worth knowing: the registry tracks references the package system can see - hard refs and
	// FSoftObjectPath/TSoftClassPtr. A path stored as a PLAIN FString in a DataTable cell is invisible
	// to it. So a zero here means "no asset-level reference", not automatically "dead".

	static IAssetRegistry& Registry()
	{
		return FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
	}

	namespace
	{
		// Is this package known to the registry at all? Cheap - metadata only, nothing is loaded.
		bool PackageIsKnown(const FString& PackageName)
		{
			if (PackageName.IsEmpty()) { return false; }
			TArray<FAssetData> Assets;
			Registry().GetAssetsByPackageName(FName(*PackageName), Assets, /*bIncludeOnlyOnDiskAssets*/ false);
			return Assets.Num() > 0;
		}
	}

	//   in:  { path: "/Game/..." }   (an object path is accepted and reduced to its package)
	//   out: { package, packageName, count, referencers[] }
	//        package == packageName == the PACKAGE path of the asset you asked about, and every
	//        entry of referencers[] is a PACKAGE path too — the registry's dependency graph is
	//        package-to-package, so there is no objectPath to give. Feed these straight back into
	//        get_referencers / audit_unused.excludeReferencers / describe_package.

	// =======================================================================
	// DEPENDENCY EDGE METADATA - hard vs soft, and the empty result that lies
	// =======================================================================
	//
	// A HARD dependency must load before its source does: it is what gets dragged into a cook and
	// what breaks a mod when it is absent. A SOFT one is loaded on demand, and a missing target is
	// survivable. Both endpoints used to answer with one undifferentiated list, so an agent asking
	// "is this safe to delete" or "why is my _P pak 400MB" could not tell them apart.
	//
	// THE MORE IMPORTANT HALF OF THIS CHANGE IS THE EMPTY RESULT, and it is a safety fix rather
	// than a feature. FAssetRegistrySerializationOptions::bSerializeDependencies defaults to FALSE
	// (AssetRegistryState.h:56 - only InitForDevelopment turns it on), and AssetRegistryState.cpp
	// skips writing depends-nodes when it is off. So on a cooked project the runtime registry
	// typically carries NO package dependency edges AT ALL for container packages.
	//
	// That meant get_referencers on any base-game asset returned count:0 with packageExists:true -
	// and count:0 is the standard justification for deleting something. "The graph was never
	// serialized" and "nothing points at this" were indistinguishable. The existing existsNote
	// block was written to stop exactly that confusion for a MISTYPED path; a container package has
	// the same shape and had no such guard. dependencyDataAvailable:false now says which case it is.

	/** Why a package has no dependency edges on disk, if it has none.
	 *
	 *  THREE STATES, NOT TWO, and conflating them produces a confidently wrong message. A loose
	 *  package has a real file and a real graph. A CONTAINER package (.pak/.utoc) is known to the
	 *  registry but its graph was probably never serialized. And an IN-MEMORY package - /Temp/,
	 *  or an asset created this session and not yet saved - has no file either, but calling that
	 *  "cooked" would be simply untrue. The first version of this said "lives in a COOKED
	 *  container" for a /Temp/ package, which is the kind of confident wrongness this note exists
	 *  to prevent. */
	enum class EMifDepSource : uint8 { Loose, Container, InMemory };

	static EMifDepSource MifDepSourceFor(const FString& PackageName)
	{
		FString FileName;
		if (FPackageName::DoesPackageExist(PackageName, &FileName)
			&& !FileName.IsEmpty() && FPaths::FileExists(FileName))
		{
			return EMifDepSource::Loose;
		}
		// No file. A loaded package with PKG_Cooked is genuinely cooked container content; anything
		// else with no file is transient or unsaved.
		if (const UPackage* Package = FindPackage(nullptr, *PackageName))
		{
			return Package->HasAnyPackageFlags(PKG_Cooked) ? EMifDepSource::Container
														   : EMifDepSource::InMemory;
		}
		// Not loaded and no file, but the registry knows it - that is container content.
		return EMifDepSource::Container;
	}

	/** package | manage | searchableName | all. Returns false and fails Out on an unknown name. */
	static bool MifParseDepCategory(const TSharedRef<FJsonObject>& In,
									const TSharedRef<FJsonObject>& Out,
									UE::AssetRegistry::EDependencyCategory& OutCat)
	{
		using namespace UE::AssetRegistry;
		const FString C = JStr(In, TEXT("category"), TEXT("package")).ToLower();
		if (C == TEXT("package"))        { OutCat = EDependencyCategory::Package;        return true; }
		if (C == TEXT("manage"))         { OutCat = EDependencyCategory::Manage;         return true; }
		if (C == TEXT("searchablename")) { OutCat = EDependencyCategory::SearchableName; return true; }
		if (C == TEXT("all"))            { OutCat = EDependencyCategory::All;            return true; }
		Fail(Out, FString::Printf(
			TEXT("unknown category '%s' - accepted: package (the default, and what you almost always "
				 "want), manage, searchableName, all."), *C));
		return false;
	}

	/** Build the flags query from `hard` and `includeEditorOnly`. */
	static UE::AssetRegistry::FDependencyQuery MifDepQuery(const TSharedRef<FJsonObject>& In,
														   bool& bOutFiltered)
	{
		using namespace UE::AssetRegistry;
		FDependencyQuery Query;
		bOutFiltered = false;
		if (In->HasField(TEXT("hard")))
		{
			bOutFiltered = true;
			if (JBool(In, TEXT("hard"), true)) { Query.Required |= EDependencyProperty::Hard; }
			else                               { Query.Excluded |= EDependencyProperty::Hard; }
		}
		if (In->HasField(TEXT("includeEditorOnly")) && !JBool(In, TEXT("includeEditorOnly"), true))
		{
			// An editor-only edge is one that is NOT Game. Excluding non-Game edges is spelled as
			// requiring Game, which is the engine's own encoding rather than a separate flag.
			bOutFiltered = true;
			Query.Required |= EDependencyProperty::Game;
		}
		return Query;
	}

	/** Shared by both endpoints - they differ only in direction and field name. */
	static void MifWriteDependencyEdges(const TSharedRef<FJsonObject>& In,
										const TSharedRef<FJsonObject>& Out,
										const FString& Pkg, bool bReferencers)
	{
		using namespace UE::AssetRegistry;
		EDependencyCategory Category;
		if (!MifParseDepCategory(In, Out, Category)) { return; }
		bool bFiltered = false;
		const FDependencyQuery Query = MifDepQuery(In, bFiltered);
		const TCHAR* ListField = bReferencers ? TEXT("referencers") : TEXT("dependencies");

		// THE FLAT ARRAY IS ALWAYS EMITTED, unchanged, because every existing caller reads it.
		TArray<FName> Flat;
		if (bReferencers) { Registry().GetReferencers(FName(*Pkg), Flat, Category, Query); }
		else              { Registry().GetDependencies(FName(*Pkg), Flat, Category, Query); }

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (const FName& N : Flat) { Arr.Add(MakeShared<FJsonValueString>(N.ToString())); }
		Out->SetStringField(TEXT("package"), Pkg);
		Out->SetStringField(TEXT("packageName"), Pkg);   // same value, plugin-wide spelling
		Out->SetNumberField(TEXT("count"), Flat.Num());
		Out->SetArrayField(ListField, Arr);
		Out->SetStringField(TEXT("category"),
			JStr(In, TEXT("category"), TEXT("package")).ToLower());

		// PER-EDGE DETAIL, and the counts that make "is this safe to delete" answerable. Only built
		// on request, because the FAssetDependency overload does more work than the FName one.
		if (JBool(In, TEXT("includeProperties"), false) || bFiltered)
		{
			TArray<FAssetDependency> Edges;
			if (bReferencers) { Registry().GetReferencers(FName(*Pkg), Edges, Category, Query); }
			else              { Registry().GetDependencies(FName(*Pkg), Edges, Category, Query); }

			int32 Hard = 0, EditorOnly = 0;
			TArray<TSharedPtr<FJsonValue>> Rows;
			for (const FAssetDependency& E : Edges)
			{
				const bool bHard = !!(E.Properties & EDependencyProperty::Hard);
				const bool bGame = !!(E.Properties & EDependencyProperty::Game);
				if (bHard) { ++Hard; }
				if (!bGame) { ++EditorOnly; }
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("package"), E.AssetId.PackageName.ToString());
				J->SetBoolField(TEXT("hard"), bHard);
				J->SetBoolField(TEXT("game"), bGame);
				J->SetBoolField(TEXT("build"), !!(E.Properties & EDependencyProperty::Build));
				// EditorOnly is the ABSENCE of Game, not a flag of its own - spelled out because
				// reading it as a flag is the obvious mistake.
				J->SetBoolField(TEXT("editorOnly"), !bGame);
				Rows.Add(MakeShared<FJsonValueObject>(J));
			}
			Out->SetArrayField(TEXT("edges"), Rows);
			Out->SetNumberField(TEXT("hardCount"), Hard);
			Out->SetNumberField(TEXT("softCount"), Edges.Num() - Hard);
			Out->SetNumberField(TEXT("editorOnlyCount"), EditorOnly);
		}

		// DOES THE PACKAGE EVEN EXIST? The registry answers an unknown package with an empty list,
		// not an error, so count:0 reads identically for "nothing points at this" and "there is no
		// such asset". Those lead to opposite actions.
		const bool bKnown = PackageIsKnown(Pkg);
		Out->SetBoolField(TEXT("packageExists"), bKnown);
		if (!bKnown)
		{
			Out->SetStringField(TEXT("existsNote"), FString::Printf(
				TEXT("no package '%s' is known to the asset registry, so count:0 means THE PATH DID "
					 "NOT RESOLVE - not that the asset is unreferenced. Do not treat this as "
					 "permission to delete anything. Check the path with find_assets."), *Pkg));
			return;
		}

		// AND THE SAME TRAP ONE STEP FURTHER IN. A container package is KNOWN to the registry, so
		// the check above passes - but a cooked registry usually carries no dependency edges at
		// all, because bSerializeDependencies defaults false. count:0 there means "never
		// recorded", and reading it as "unreferenced" is how something gets deleted.
		const EMifDepSource Source = MifDepSourceFor(Pkg);
		Out->SetStringField(TEXT("packageSource"),
			Source == EMifDepSource::Loose     ? TEXT("loose")
		  : Source == EMifDepSource::Container ? TEXT("container") : TEXT("inMemory"));
		const bool bSuspect = (Source != EMifDepSource::Loose) && Flat.Num() == 0;
		Out->SetBoolField(TEXT("dependencyDataAvailable"), !bSuspect);
		if (bSuspect)
		{
			Out->SetStringField(TEXT("dependencyDataNote"), Source == EMifDepSource::Container
				? FString::Printf(
					TEXT("'%s' lives in a COOKED container, and a cooked asset registry usually "
						 "carries NO package dependency edges at all - "
						 "FAssetRegistrySerializationOptions::bSerializeDependencies defaults to "
						 "false, so the graph was never written. count:0 here means THE DATA WAS "
						 "NOT RECORDED, not that nothing references this. Treating it as "
						 "'unreferenced' is how something in use gets deleted. Loose packages "
						 "saved by this editor do carry edges."), *Pkg)
				: FString::Printf(
					TEXT("'%s' is an IN-MEMORY package - transient, or created this session and "
						 "never saved - so it has no serialized dependency graph. count:0 means "
						 "there is nothing recorded yet, not that nothing references it. Save it "
						 "and ask again if you need real edges."), *Pkg));
		}
	}

	// Who points AT this asset.
	void H_get_referencers(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("category"), TEXT("hard"), TEXT("includeEditorOnly"),
			  TEXT("includeProperties") },
			TEXT("path; category (package|manage|searchableName|all, default package); hard (true = ")
			TEXT("hard only, false = SOFT only, omit for both); includeEditorOnly (default true); ")
			TEXT("includeProperties (per-edge hard/game/build detail)"),
			{ { TEXT("soft"), TEXT("spell it hard:false - one parameter with two states, rather than "
								   "two that can disagree") },
			  { TEXT("recursive"), TEXT("this is one hop. project_dependency_graph walks the graph") } }))
		{
			return;
		}
		const FString Pkg = NormalizePackagePath(JStr(In, TEXT("path")));
		if (Pkg.IsEmpty())
		{
			Fail(Out, TEXT("path is required"));
			return;
		}
		MifWriteDependencyEdges(In, Out, Pkg, /*bReferencers*/ true);
	}

	//   in:  { path: "/Game/..." }   (an object path is accepted and reduced to its package)
	//   out: { package, packageName, count, dependencies[] }
	//        Same shape as get_referencers: package == packageName, and every dependencies[] entry
	//        is a PACKAGE path.
	// What this asset points at.
	void H_get_dependencies(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("category"), TEXT("hard"), TEXT("includeEditorOnly"),
			  TEXT("includeProperties") },
			TEXT("path; category (package|manage|searchableName|all, default package); hard (true = ")
			TEXT("hard only, false = SOFT only, omit for both); includeEditorOnly (default true); ")
			TEXT("includeProperties (per-edge hard/game/build detail)"),
			{ { TEXT("soft"), TEXT("spell it hard:false - one parameter with two states, rather than "
								   "two that can disagree") },
			  { TEXT("recursive"), TEXT("this is one hop. project_dependency_graph walks the graph") } }))
		{
			return;
		}
		const FString Pkg = NormalizePackagePath(JStr(In, TEXT("path")));
		if (Pkg.IsEmpty())
		{
			Fail(Out, TEXT("path is required"));
			return;
		}
		MifWriteDependencyEdges(In, Out, Pkg, /*bReferencers*/ false);
	}

	// ---------------------------------------------------------------- excludeReferencers (GAP 4)
	// A dev-only test level that references everything makes every asset look used - which defeats
	// the endpoint. excludeReferencers names referencers whose reference DOES NOT COUNT toward
	// "used", so a genuinely dead asset stops hiding behind /Game/DevTest/L_Scratch.
	//
	// A pattern is EITHER an exact package path (/Game/DevTest/L_Scratch) OR a folder prefix
	// (/Game/DevTest/ - everything beneath). The trailing slash is optional and both forms match
	// beneath: guessing the caller's intent from one character would silently under-exclude, which
	// is the failure this parameter exists to remove. An object path is accepted and reduced to its
	// package, so a value pasted out of find_assets works unedited.
	//
	// A malformed entry is an ERROR naming it, never a skip: a dropped pattern reads as "nothing was
	// excluded", i.e. the exact masking the caller asked us to stop. Same rule as RejectUnknownParams.
	static bool ParseExcludeReferencers(const TSharedRef<FJsonObject>& In, TArray<FString>& OutPatterns, FString& OutError)
	{
		// PM-001 house pattern: accept the spellings a caller would plausibly use. Every one of
		// these is also on audit_unused's accepted-key list, or the guard would reject it first.
		static const TCHAR* const Spellings[] = { TEXT("excludeReferencers"), TEXT("excludeReferencer"), TEXT("ignoreReferencers") };

		for (const TCHAR* Key : Spellings)
		{
			const TSharedPtr<FJsonValue> Value = In->TryGetField(Key);
			if (!Value.IsValid() || Value->Type == EJson::Null)
			{
				continue;
			}

			// A single string is accepted as well as an array - excluding ONE test level should not
			// require remembering to wrap it, and a bare string silently ignored would mask again.
			TArray<TSharedPtr<FJsonValue>> Items;
			if (Value->Type == EJson::Array)
			{
				Items = Value->AsArray();
			}
			else
			{
				Items.Add(Value);
			}

			for (const TSharedPtr<FJsonValue>& Item : Items)
			{
				FString Pattern;
				if (!Item.IsValid() || !Item->TryGetString(Pattern))
				{
					OutError = FString::Printf(
						TEXT("%s entries must be strings - a package path like /Game/DevTest/L_Scratch or a folder prefix like /Game/DevTest/"),
						Key);
					return false;
				}
				Pattern.TrimStartAndEndInline();
				if (Pattern.IsEmpty())
				{
					OutError = FString::Printf(
						TEXT("%s contains an empty entry - remove it, or pass the package path you meant (an empty pattern would match nothing and silently exclude nothing)"),
						Key);
					return false;
				}
				Pattern = NormalizePackagePath(Pattern);      // /Game/X/Foo.Foo -> /Game/X/Foo
				if (!Pattern.StartsWith(TEXT("/")))
				{
					OutError = FString::Printf(
						TEXT("%s entry '%s' must start with / (e.g. /Game/DevTest/L_Scratch for one package, or /Game/DevTest/ for everything beneath)"),
						Key, *Pattern);
					return false;
				}
				// Stored without the trailing slash; FindExcludingPattern appends it for the prefix
				// test. Normalizing here is what makes "/Game/DevTest" and "/Game/DevTest/" identical
				// and keeps the echo one canonical spelling per pattern.
				while (Pattern.Len() > 1 && Pattern.EndsWith(TEXT("/")))
				{
					Pattern.LeftChopInline(1);
				}
				OutPatterns.AddUnique(Pattern);
			}
		}
		return true;
	}

	// Returns the pattern that excludes PackageName, or null. Returning the PATTERN (not a bool) is
	// what lets each result echo WHY a referencer stopped counting.
	static const FString* FindExcludingPattern(const TArray<FString>& Patterns, const FString& PackageName)
	{
		for (const FString& Pattern : Patterns)
		{
			if (PackageName == Pattern || PackageName.StartsWith(Pattern + TEXT("/")))
			{
				return &Pattern;
			}
		}
		return nullptr;
	}

	//   in:  { pathPrefix: "/Game/MODS/MyMod", class?: "/Script/Engine.StaticMesh",
	//          includeAll?: bool (default false - only report the unreferenced),
	//          excludeReferencers?: string | string[]   (aliases: excludeReferencer, ignoreReferencers)
	//              package paths and/or folder prefixes whose references DO NOT COUNT toward "used"
	//              - e.g. ["/Game/DevTest/", "/Game/Maps/L_Scratch"] - so a dev-only test level
	//              cannot mask a genuinely unused asset. Exact and prefix forms both supported; a
	//              trailing slash is optional; a malformed entry is an error, never a silent skip,
	//          limit?: int (default 4000), rescan?: bool (default false) }
	//   out: { scanned, unusedCount, unusedOnlyDueToExclusions, excludedReferencerCount,
	//          excludeReferencers[], excludeReferencerMatches{ pattern: count }, truncated,
	//          assets[{ objectPath, packageName, path, package, name, class, folder,
	//                   refs, refsTotal, extRefs, excludedRefs,
	//                   excludedReferencers[{ packageName, matchedPattern }] }] }
	//        `path` here has ALWAYS been the PACKAGE path - unlike find_assets, where `path` is the
	//        OBJECT path. That mismatch IS the reported GAP 8, so both rows now carry the explicit
	//        objectPath (/Game/X/Foo.Foo_C) and packageName (/Game/X/Foo); `path`/`package` are
	//        unchanged for existing callers. See the asset-row field naming block at the top.
	//        refs counts only referencers that COUNT (excluded ones removed) - so "refs == 0" keeps
	//        meaning "unused" with or without exclusions, and the pre-existing filter/unusedCount
	//        logic needs no caller change. refsTotal is the raw registry number, and
	//        refsTotal - refs == excludedRefs == excludedReferencers.length holds on every row, so
	//        the exclusion is numerically checkable rather than taken on trust.
	//        With no excludeReferencers passed, every one of these is 0/[] and refs == refsTotal:
	//        the response is byte-compatible with the previous shape plus the new keys.
	// BLOCKING HAZARDS (docs/02_GOTCHAS.md requires every endpoint spec to state these):
	//   - refuses when the asset registry is still scanning rather than calling WaitForCompletion(),
	//     which used to block unconditionally even when rescan was not asked for;
	//   - refuses rescan:true for a prefix of fewer than two path segments (ScanPathsSynchronous on a
	//     mount root is minutes of stopped ticker);
	//   - refuses a prefix matching more than 20000 assets, because GetReferencers runs per asset
	//     regardless of `limit`.
	// All three are hard Fails, not waits: a handler that blocks the game thread takes the entire
	// bridge offline for its duration, and a caller can retry an error but cannot cancel a stall.
	//
	// The whole "what are we not shipping" audit in ONE call. For every asset under pathPrefix it
	// reports how many packages reference it (refs) and how many of those live OUTSIDE its own folder
	// (extRefs). extRefs is the interesting number: a cluster of assets that only reference each other
	// - a mesh used solely by its own material, say - has refs>0 but extRefs==0, and is just as unused
	// by the mod as something with no references at all.
	void H_audit_unused(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("pathPrefix"), TEXT("class"), TEXT("includeAll"), TEXT("limit"), TEXT("rescan"),
			  TEXT("excludeReferencers"), TEXT("excludeReferencer"), TEXT("ignoreReferencers") },
			TEXT("pathPrefix, class, includeAll, limit, rescan, excludeReferencers (aliases: excludeReferencer, ignoreReferencers)")))
		{
			return;
		}
		const FString Prefix = JStr(In, TEXT("pathPrefix"));
		if (Prefix.IsEmpty() || !Prefix.StartsWith(TEXT("/")))
		{
			Fail(Out, TEXT("pathPrefix is required and must start with / (e.g. /Game/MODS/MyMod)"));
			return;
		}
		// BLOCKING-HAZARD GUARD. Every handler runs INLINE on the game thread inside the HTTP ticker
		// (MifBridgeServer.cpp), so anything unbounded here stops the ticker: the socket is not read
		// and EVERY other bridge call times out with no response — the docs/02_GOTCHAS.md §8 failure.
		// ScanPathsSynchronous(/Game, bForceRescan=true) is minutes on this project, and the only
		// validation used to be "non-empty and starts with /". A rescan is a targeted tool; refuse it
		// for a root that means "everything".
		{
			FString Trimmed = Prefix;
			Trimmed.RemoveFromEnd(TEXT("/"));
			TArray<FString> Segments;
			Trimmed.ParseIntoArray(Segments, TEXT("/"), true);
			if (JBool(In, TEXT("rescan"), false) && Segments.Num() < 2)
			{
				Fail(Out, FString::Printf(
					TEXT("rescan:true is refused for '%s': a forced synchronous re-scan of a mount root re-reads every ")
					TEXT("package under it, which blocks the game thread this HTTP server runs on for minutes and makes ")
					TEXT("the whole bridge unreachable. Pass a narrower pathPrefix (at least two segments, e.g. ")
					TEXT("/Game/MODS/MyMod), or drop rescan and rely on the registry's own watcher."), *Prefix));
				return;
			}
		}
		// Parsed BEFORE the registry work: a bad pattern must cost the caller an error, not a scan
		// whose numbers silently mean something other than what was asked for.
		TArray<FString> ExcludePatterns;
		FString ExcludeError;
		if (!ParseExcludeReferencers(In, ExcludePatterns, ExcludeError))
		{
			Fail(Out, ExcludeError);
			return;
		}
		const FString ClassName = JStr(In, TEXT("class"));
		const bool bIncludeAll = JBool(In, TEXT("includeAll"), false);
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 4000), 1, 20000);

		IAssetRegistry& Reg = Registry();
		// WaitForCompletion() used to run UNCONDITIONALLY, so even a call that asked for no rescan
		// blocked the game thread until the background registry scan finished — at editor start-up
		// that is the whole content tree. Answer honestly instead of hanging: the caller can retry.
		if (Reg.IsLoadingAssets())
		{
			Fail(Out, TEXT("the asset registry is still scanning, so reference counts would be wrong and waiting here would ")
				TEXT("block the game thread (and therefore this HTTP server) for as long as the scan takes. ")
				TEXT("Retry once it settles — find_assets on a small path is a cheap way to check."));
			return;
		}
		if (JBool(In, TEXT("rescan"), false))
		{
			// Force the folder to be re-scanned first, so a freshly-created asset is not reported dead.
			// Bounded by the >= 2 path segments enforced above.
			Reg.ScanPathsSynchronous({ Prefix }, true);
			Reg.WaitForCompletion();
		}

		FARFilter Filter;
		Filter.PackagePaths.Add(FName(*Prefix));
		Filter.bRecursivePaths = true;
		Filter.bRecursiveClasses = true;
		if (!ClassName.IsEmpty())
		{
			Filter.ClassPaths.Add(FTopLevelAssetPath(ClassName));
		}

		TArray<FAssetData> Assets;
		Reg.GetAssets(Filter, Assets);

		// `limit` caps only the OUTPUT array: GetReferencers is deliberately called for every asset
		// under the prefix ("continue, NOT break", below), so the real cost is O(assets under prefix)
		// no matter what limit says. Name the wall instead of walking into it — a /Game-wide sweep is
		// tens of thousands of registry queries with the ticker stopped the whole time.
		static const int32 kMaxAssetsScanned = 20000;
		if (Assets.Num() > kMaxAssetsScanned)
		{
			Fail(Out, FString::Printf(
				TEXT("pathPrefix '%s' matches %d assets. audit_unused queries referencers for EVERY match (limit caps only ")
				TEXT("the reported rows, not the work), and doing that for more than %d assets blocks the game thread — and ")
				TEXT("therefore this whole HTTP bridge — for minutes. Narrow pathPrefix, or add class to filter."),
				*Prefix, Assets.Num(), kMaxAssetsScanned));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Arr;
		int32 UnusedCount = 0;
		int32 NewlyUnusedCount = 0;
		int32 ExcludedTotal = 0;
		TMap<FString, int32> ExcludeHits;
		for (const FString& Pattern : ExcludePatterns)
		{
			ExcludeHits.Add(Pattern, 0);            // every pattern reports, so a typo shows as 0
		}
		bool bTruncated = false;
		for (const FAssetData& A : Assets)
		{
			const FString PkgName = A.PackageName.ToString();
			const FString Folder = FPackageName::GetLongPackagePath(PkgName);

			TArray<FName> Refs;
			Reg.GetReferencers(A.PackageName, Refs);

			int32 Ext = 0;
			int32 Excluded = 0;
			TArray<TSharedPtr<FJsonValue>> ExcludedArr;
			for (const FName& R : Refs)
			{
				const FString RS = R.ToString();
				if (RS == PkgName)
				{
					continue;                       // never count self
				}
				// GAP 4: an excluded referencer counts neither as a reference NOR as an external
				// one - a dev-only level is external to every folder, so leaving it in extRefs
				// would keep the mask in place under a different number.
				if (const FString* Matched = FindExcludingPattern(ExcludePatterns, RS))
				{
					++Excluded;
					++ExcludedTotal;
					ExcludeHits.FindOrAdd(*Matched)++;

					// Echoed per result, never silently dropped: this is the caller's ONLY way to
					// see why something is now reported unused, and to spot an over-broad pattern.
					TSharedRef<FJsonObject> X = MakeShared<FJsonObject>();
					X->SetStringField(TEXT("packageName"), RS);
					X->SetStringField(TEXT("matchedPattern"), *Matched);
					ExcludedArr.Add(MakeShared<FJsonValueObject>(X));
					continue;
				}
				if (FPackageName::GetLongPackagePath(RS) != Folder)
				{
					++Ext;
				}
			}
			// refs keeps meaning "references that COUNT", so refs==0 still means unused and no
			// existing caller-side test changes. refsTotal preserves the raw registry number, and
			// refsTotal - refs == excludedRefs by construction.
			const int32 RawTotal = Refs.Num();
			const int32 Total = RawTotal - Excluded;
			if (Total == 0)
			{
				++UnusedCount;
				if (RawTotal != 0)
				{
					++NewlyUnusedCount;             // was masked purely by excluded referencers
				}
			}
			if (!bIncludeAll && Total != 0)
			{
				continue;
			}
			if (Arr.Num() >= Limit)
			{
				// continue, NOT break: breaking stopped the scan, so a truncated run under-reported
				// unusedCount - a summary number that quietly disagreed with the same call at a
				// higher limit. find_assets already caps the array while finishing the count
				// (MifBridgeCooked.cpp); this endpoint now matches it, and excludedReferencerCount
				// would have had the same defect from birth.
				bTruncated = true;
				continue;
			}
			TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			EmitAssetIdentity(O, A.GetObjectPathString(), PkgName);
			O->SetStringField(TEXT("path"), PkgName);        // legacy key: == packageName HERE
			O->SetStringField(TEXT("name"), A.AssetName.ToString());
			O->SetStringField(TEXT("class"), A.AssetClassPath.ToString());
			O->SetStringField(TEXT("package"), PkgName);     // matches find_assets' `package`
			O->SetStringField(TEXT("folder"), Folder);
			O->SetNumberField(TEXT("refs"), Total);
			O->SetNumberField(TEXT("refsTotal"), RawTotal);
			O->SetNumberField(TEXT("extRefs"), Ext);
			O->SetNumberField(TEXT("excludedRefs"), Excluded);
			O->SetArrayField(TEXT("excludedReferencers"), ExcludedArr);
			Arr.Add(MakeShared<FJsonValueObject>(O));
		}

		// Echo of the EFFECTIVE patterns (normalized: object paths reduced, trailing slash dropped),
		// so the caller can see what was actually applied rather than what they typed.
		TArray<TSharedPtr<FJsonValue>> PatternArr;
		TSharedRef<FJsonObject> HitsObj = MakeShared<FJsonObject>();
		for (const FString& Pattern : ExcludePatterns)
		{
			PatternArr.Add(MakeShared<FJsonValueString>(Pattern));
			// Per-pattern hit count: a mistyped pattern is otherwise indistinguishable from one
			// that legitimately matched nothing, and both look like "the exclusion did nothing".
			HitsObj->SetNumberField(Pattern, ExcludeHits.FindRef(Pattern));
		}

		Out->SetNumberField(TEXT("scanned"), Assets.Num());
		// scanned:0 alongside unusedCount:0 reads as "nothing is unused". Say which it actually is -
		// the answer to "did my prefix match anything at all" changes what the caller does next, and
		// a mistyped prefix currently looks identical to a clean bill of health.
		if (Assets.Num() == 0)
		{
			Out->SetStringField(TEXT("scanNote"), FString::Printf(
				TEXT("no assets matched pathPrefix '%s', so unusedCount:0 means THE PREFIX FOUND "
					 "NOTHING - not that nothing is unused. Check the path with find_assets."),
				*Prefix));
		}
		Out->SetNumberField(TEXT("unusedCount"), UnusedCount);
		Out->SetNumberField(TEXT("unusedOnlyDueToExclusions"), NewlyUnusedCount);
		Out->SetNumberField(TEXT("excludedReferencerCount"), ExcludedTotal);
		Out->SetArrayField(TEXT("excludeReferencers"), PatternArr);
		Out->SetObjectField(TEXT("excludeReferencerMatches"), HitsObj);
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("assets"), Arr);
		UE_LOG(LogMifBridge, Log, TEXT("audit_unused: %s -> %d scanned, %d unreferenced (%d referencer(s) excluded by %d pattern(s), %d newly unused)"),
			*Prefix, Assets.Num(), UnusedCount, ExcludedTotal, ExcludePatterns.Num(), NewlyUnusedCount);
	}
}
