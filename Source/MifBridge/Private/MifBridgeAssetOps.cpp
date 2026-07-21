// MifBridge — asset lifecycle: delete_asset, rename_asset, duplicate_asset.
// The rest of the plugin edits INSIDE an asset (graph nodes, variables, DataTable rows); nothing
// could act on the asset itself — no way to clean up a scratch/test asset, reorganize content, or
// clone one. All three are /Game/-only (refuse to touch engine/plugin content) and destructive ops
// (delete/rename) are confirm-gated, matching remove_node/remove_variable/etc. All go through the
// headless (no-dialog) engine entry points, matching the rest of the plugin's no-popup design.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

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
}
