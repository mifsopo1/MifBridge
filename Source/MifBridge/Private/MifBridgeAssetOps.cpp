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
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "ObjectTools.h"
#include "UObject/Package.h"      // UPackage - duplicate_asset reads newPackageName off GetOutermost()
#include "UObject/UObjectGlobals.h"
#include "UObject/WeakObjectPtrTemplates.h"

namespace MifBridge
{
	// Accept either a bare package path ("/Game/Foo/Bar") or an "asset.asset" path
	// ("/Game/Foo/Bar.Bar") and normalize to the package path.
	static FString NormalizePackagePath(const FString& InPath)
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
	static UObject* LoadAssetLenient(const FString& Path)
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

		const int32 NumDeleted = ObjectTools::DeleteAssets(AssetsToDelete, /*bShowConfirmation*/ false);
		Out->SetStringField(TEXT("path"), PackagePath);
		Out->SetStringField(TEXT("packageName"), PackagePath);   // same value, unambiguous spelling
		Out->SetNumberField(TEXT("numDeleted"), NumDeleted);
		Out->SetBoolField(TEXT("deleted"), NumDeleted > 0);
		if (NumDeleted == 0)
		{
			Fail(Out, FString::Printf(TEXT("delete reported 0 assets removed for '%s' (still referenced/in use?)"), *PackagePath));
			return;
		}
		UE_LOG(LogMifBridge, Log, TEXT("delete_asset: %s (numDeleted=%d)"), *PackagePath, NumDeleted);
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
		const bool bOk = AssetTools.RenameAssets(Renames);   // headless — no dialog, unlike RenameAssetsWithDialog

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
		if (RawPath.IsEmpty() || !RawPath.StartsWith(TEXT("/Game/")))
		{
			Fail(Out, TEXT("path required, must start with /Game/"));
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

		const FString NewPackagePath = FPackageName::GetLongPackagePath(NewPath);
		const FString NewAssetName = FPackageName::GetLongPackageAssetName(NewPath);
		if (!IsValidIdentifier(NewAssetName))
		{
			Fail(Out, FString::Printf(TEXT("invalid new asset name '%s' (from newPath '%s')"), *NewAssetName, *NewPath));
			return;
		}

		IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>(TEXT("AssetTools")).Get();
		UObject* NewAsset = AssetTools.DuplicateAsset(NewAssetName, NewPackagePath, Asset);   // headless — no dialog
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

	//   in:  { path: "/Game/..." }   (an object path is accepted and reduced to its package)
	//   out: { package, packageName, count, referencers[] }
	//        package == packageName == the PACKAGE path of the asset you asked about, and every
	//        entry of referencers[] is a PACKAGE path too — the registry's dependency graph is
	//        package-to-package, so there is no objectPath to give. Feed these straight back into
	//        get_referencers / audit_unused.excludeReferencers / describe_package.
	// Who points AT this asset.
	void H_get_referencers(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("path") }, TEXT("path")))
		{
			return;
		}
		const FString Pkg = NormalizePackagePath(JStr(In, TEXT("path")));
		if (Pkg.IsEmpty())
		{
			Fail(Out, TEXT("path is required"));
			return;
		}
		TArray<FName> Refs;
		Registry().GetReferencers(FName(*Pkg), Refs);

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (const FName& R : Refs)
		{
			Arr.Add(MakeShared<FJsonValueString>(R.ToString()));
		}
		Out->SetStringField(TEXT("package"), Pkg);
		Out->SetStringField(TEXT("packageName"), Pkg);   // same value, plugin-wide spelling
		Out->SetNumberField(TEXT("count"), Refs.Num());
		Out->SetArrayField(TEXT("referencers"), Arr);
	}

	//   in:  { path: "/Game/..." }   (an object path is accepted and reduced to its package)
	//   out: { package, packageName, count, dependencies[] }
	//        Same shape as get_referencers: package == packageName, and every dependencies[] entry
	//        is a PACKAGE path.
	// What this asset points at.
	void H_get_dependencies(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("path") }, TEXT("path")))
		{
			return;
		}
		const FString Pkg = NormalizePackagePath(JStr(In, TEXT("path")));
		if (Pkg.IsEmpty())
		{
			Fail(Out, TEXT("path is required"));
			return;
		}
		TArray<FName> Deps;
		Registry().GetDependencies(FName(*Pkg), Deps);

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (const FName& D : Deps)
		{
			Arr.Add(MakeShared<FJsonValueString>(D.ToString()));
		}
		Out->SetStringField(TEXT("package"), Pkg);
		Out->SetStringField(TEXT("packageName"), Pkg);   // same value, plugin-wide spelling
		Out->SetNumberField(TEXT("count"), Deps.Num());
		Out->SetArrayField(TEXT("dependencies"), Arr);
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
