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

	//   in:  { path: "/Game/...", confirm: true }
	//   out: { path, numDeleted, deleted: bool }
	// Headless equivalent of Content Browser delete: ObjectTools::DeleteAssets with
	// bShowConfirmation=false so it can't block on a modal no one is there to click.
	void H_delete_asset(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
	//   out: { oldPath, newPath, renamed: bool }
	// newPath's final segment is BOTH the destination folder and the new asset name (UE convention:
	// PackagePath/AssetName.AssetName) — same as the Content Browser's F2 rename / drag-to-folder.
	void H_rename_asset(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
		Out->SetBoolField(TEXT("renamed"), true);
		UE_LOG(LogMifBridge, Log, TEXT("rename_asset: %s -> %s"), *RawPath, *NewPath);
	}

	//   in:  { path: "/Game/...", newPath: "/Game/NewDir/NewName" }
	//   out: { sourcePath, newPath, duplicated: bool }
	// Not confirm-gated — purely additive, never destroys or overwrites existing data (fails instead
	// of clobbering if newPath is already taken).
	void H_duplicate_asset(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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

		Out->SetStringField(TEXT("sourcePath"), NormalizePackagePath(RawPath));
		Out->SetStringField(TEXT("newPath"), NewAsset->GetPathName());
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

	//   in:  { path: "/Game/..." }
	//   out: { package, count, referencers[] }
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
		Out->SetNumberField(TEXT("count"), Refs.Num());
		Out->SetArrayField(TEXT("referencers"), Arr);
	}

	//   in:  { path: "/Game/..." }
	//   out: { package, count, dependencies[] }
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
		Out->SetNumberField(TEXT("count"), Deps.Num());
		Out->SetArrayField(TEXT("dependencies"), Arr);
	}

	//   in:  { pathPrefix: "/Game/MODS/MyMod", class?: "/Script/Engine.StaticMesh",
	//          includeAll?: bool (default false - only report the unreferenced),
	//          limit?: int (default 4000), rescan?: bool (default false) }
	//   out: { scanned, unusedCount, truncated,
	//          assets[{ path, name, class, folder, refs, extRefs }] }
	// The whole "what are we not shipping" audit in ONE call. For every asset under pathPrefix it
	// reports how many packages reference it (refs) and how many of those live OUTSIDE its own folder
	// (extRefs). extRefs is the interesting number: a cluster of assets that only reference each other
	// - a mesh used solely by its own material, say - has refs>0 but extRefs==0, and is just as unused
	// by the mod as something with no references at all.
	void H_audit_unused(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("pathPrefix"), TEXT("class"), TEXT("includeAll"), TEXT("limit"), TEXT("rescan") },
			TEXT("pathPrefix, class, includeAll, limit, rescan")))
		{
			return;
		}
		const FString Prefix = JStr(In, TEXT("pathPrefix"));
		if (Prefix.IsEmpty() || !Prefix.StartsWith(TEXT("/")))
		{
			Fail(Out, TEXT("pathPrefix is required and must start with / (e.g. /Game/MODS/MyMod)"));
			return;
		}
		const FString ClassName = JStr(In, TEXT("class"));
		const bool bIncludeAll = JBool(In, TEXT("includeAll"), false);
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 4000), 1, 20000);

		IAssetRegistry& Reg = Registry();
		if (JBool(In, TEXT("rescan"), false))
		{
			// Force the folder to be re-scanned first, so a freshly-created asset is not reported dead.
			Reg.ScanPathsSynchronous({ Prefix }, true);
		}
		Reg.WaitForCompletion();

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

		TArray<TSharedPtr<FJsonValue>> Arr;
		int32 UnusedCount = 0;
		bool bTruncated = false;
		for (const FAssetData& A : Assets)
		{
			const FString PkgName = A.PackageName.ToString();
			const FString Folder = FPackageName::GetLongPackagePath(PkgName);

			TArray<FName> Refs;
			Reg.GetReferencers(A.PackageName, Refs);

			int32 Ext = 0;
			for (const FName& R : Refs)
			{
				const FString RS = R.ToString();
				if (RS == PkgName)
				{
					continue;                       // never count self
				}
				if (FPackageName::GetLongPackagePath(RS) != Folder)
				{
					++Ext;
				}
			}
			const int32 Total = Refs.Num();
			if (Total == 0)
			{
				++UnusedCount;
			}
			if (!bIncludeAll && Total != 0)
			{
				continue;
			}
			if (Arr.Num() >= Limit)
			{
				bTruncated = true;
				break;
			}
			TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetStringField(TEXT("path"), PkgName);
			O->SetStringField(TEXT("name"), A.AssetName.ToString());
			O->SetStringField(TEXT("class"), A.AssetClassPath.ToString());
			O->SetStringField(TEXT("folder"), Folder);
			O->SetNumberField(TEXT("refs"), Total);
			O->SetNumberField(TEXT("extRefs"), Ext);
			Arr.Add(MakeShared<FJsonValueObject>(O));
		}

		Out->SetNumberField(TEXT("scanned"), Assets.Num());
		Out->SetNumberField(TEXT("unusedCount"), UnusedCount);
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("assets"), Arr);
		UE_LOG(LogMifBridge, Log, TEXT("audit_unused: %s -> %d scanned, %d unreferenced"),
			*Prefix, Assets.Num(), UnusedCount);
	}
}
