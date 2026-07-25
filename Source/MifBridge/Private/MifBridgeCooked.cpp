// MifBridge — cooked/mounted-content introspection.
//
// The rest of MifBridge assumes normal editable project assets. This file is about the OTHER half of a
// cooked-editor session: the base game's content, which lives in IoStore containers mounted at runtime by
// FPakPlatformFile::MountModKitGameContainers() (see Engine/Source/Runtime/PakFile) rather than as loose
// .uasset files on disk. None of that is visible through the normal endpoints - you can't tell what's
// mounted, whether a given package came from a container or from disk, or why a cooked asset behaves
// differently from a project one. These endpoints answer exactly those questions.
//
// Everything here is read-only.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetRegistry/ARFilter.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "HAL/PlatformFileManager.h"
#include "IO/IoDispatcher.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/PackagePath.h"
#include "Misc/Paths.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
	// True when this package has no loose file on disk, i.e. it can only have come from a mounted IoStore
	// container. Same test the modkit engine changes use (BlueprintActionDatabase.cpp, EditorPackageLoader.cpp)
	// to decide whether a package is container-only.
	static bool IsContainerOnlyPackage(FName PackageName)
	{
		return FPackageName::DoesPackageExistEx(
			FPackagePath::FromPackageNameUnchecked(PackageName),
			FPackageName::EPackageLocationFilter::FileSystem) == FPackageName::EPackageLocationFilter::None;
	}

	// Mirrors ModKit_GetGameContainerDir() in IPlatformFilePak.cpp - the project-root text file naming the
	// game install whose containers get mounted. Re-read here (rather than exposed from the engine) so this
	// stays a read-only observer with no engine-side API to keep in sync.
	static FString ReadGameInstallDirSetting(FString& OutConfigPath, bool& bOutFileFound)
	{
		OutConfigPath = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("GameInstallDirectory.txt"));
		FString Contents;
		bOutFileFound = FFileHelper::LoadFileToString(Contents, *OutConfigPath);
		if (!bOutFileFound)
		{
			return FString();
		}

		TArray<FString> Lines;
		Contents.ParseIntoArrayLines(Lines);
		for (FString& Line : Lines)
		{
			Line.TrimStartAndEndInline();
			if (!Line.IsEmpty() && !Line.StartsWith(TEXT("#")) && !Line.StartsWith(TEXT(";")))
			{
				FPaths::NormalizeDirectoryName(Line);
				return Line;
			}
		}
		return FString();
	}

	//   in:  {}
	//   out: { ioDispatcherInitialized, configPath, configFound, gameInstallDir, resolvedContainerDir,
	//          containers[{ file, sizeBytes }], assetCounts{ total, containerOnly, loose, loaded } }
	// Answers "what base-game content do I actually have mounted right now, and did the mount work" - the
	// IoStore containers are mounted straight through the IoDispatcher file backend, so they never show up
	// in the normal mounted-pak list and are otherwise invisible from inside the editor.
	void H_list_mounted_containers(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		Out->SetBoolField(TEXT("ioDispatcherInitialized"), FIoDispatcher::IsInitialized());

		FString ConfigPath;
		bool bConfigFound = false;
		const FString GameInstallDir = ReadGameInstallDirSetting(ConfigPath, bConfigFound);

		Out->SetStringField(TEXT("configPath"), ConfigPath);
		Out->SetBoolField(TEXT("configFound"), bConfigFound);
		Out->SetStringField(TEXT("gameInstallDir"), GameInstallDir);

		// Same candidate-directory walk MountModKitGameContainers() does, so what's reported here is what
		// the engine would actually have found and mounted.
		TArray<FString> CandidateDirs;
		if (!GameInstallDir.IsEmpty())
		{
			CandidateDirs.Add(GameInstallDir);
			CandidateDirs.Add(GameInstallDir / TEXT("Paks"));
			CandidateDirs.Add(GameInstallDir / TEXT("Content/Paks"));

			IPlatformFile& PlatformFile = FPlatformFileManager::Get().GetPlatformFile();
			if (PlatformFile.DirectoryExists(*GameInstallDir))
			{
				PlatformFile.IterateDirectory(*GameInstallDir, [&CandidateDirs](const TCHAR* Path, bool bIsDir) -> bool
				{
					if (bIsDir)
					{
						CandidateDirs.Add(FString(Path) / TEXT("Content/Paks"));
					}
					return true;
				});
			}
		}

		IPlatformFile& PlatformFile = FPlatformFileManager::Get().GetPlatformFile();
		TArray<TSharedPtr<FJsonValue>> ContainerArr;
		FString ResolvedDir;
		for (const FString& Dir : CandidateDirs)
		{
			if (!PlatformFile.DirectoryExists(*Dir))
			{
				continue;
			}
			TArray<FString> Utocs;
			PlatformFile.IterateDirectory(*Dir, [&Utocs](const TCHAR* Path, bool bIsDir) -> bool
			{
				if (!bIsDir)
				{
					FString File(Path);
					if (File.EndsWith(TEXT(".utoc")))
					{
						Utocs.Add(MoveTemp(File));
					}
				}
				return true;
			});

			if (Utocs.Num() > 0)
			{
				ResolvedDir = Dir;
				Utocs.Sort();
				for (const FString& Utoc : Utocs)
				{
					TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
					Json->SetStringField(TEXT("file"), FPaths::GetCleanFilename(Utoc));
					Json->SetStringField(TEXT("path"), Utoc);
					Json->SetNumberField(TEXT("sizeBytes"), static_cast<double>(PlatformFile.FileSize(*Utoc)));
					ContainerArr.Add(MakeShared<FJsonValueObject>(Json));
				}
				break;
			}
		}

		Out->SetStringField(TEXT("resolvedContainerDir"), ResolvedDir);
		Out->SetNumberField(TEXT("containerCount"), ContainerArr.Num());
		Out->SetArrayField(TEXT("containers"), ContainerArr);

		// The practical answer to "what's mounted": how much of the asset registry is content that exists
		// only inside a container. A healthy cooked-editor session has a large containerOnly count; zero
		// means nothing mounted (or the registry never picked the containers up).
		IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
		int32 Total = 0, ContainerOnly = 0, Loaded = 0;
		Registry.EnumerateAllAssets([&Total, &ContainerOnly, &Loaded](const FAssetData& Asset) -> bool
		{
			++Total;
			if (Asset.IsAssetLoaded()) { ++Loaded; }
			if (IsContainerOnlyPackage(Asset.PackageName)) { ++ContainerOnly; }
			return true;
		});

		TSharedRef<FJsonObject> Counts = MakeShared<FJsonObject>();
		Counts->SetNumberField(TEXT("total"), Total);
		Counts->SetNumberField(TEXT("containerOnly"), ContainerOnly);
		Counts->SetNumberField(TEXT("loose"), Total - ContainerOnly);
		Counts->SetNumberField(TEXT("loaded"), Loaded);
		Out->SetObjectField(TEXT("assetCounts"), Counts);
	}

	//   in:  { class?: "DataTable"|"/Script/Engine.DataTable", pathPrefix?: "/Game/Blueprints",
	//          nameContains?: "NPC", origin?: "container"|"loose"|"any", recursiveClasses?: bool (default true),
	//          limit?: int (default 100) }
	//   out: { count, truncated, assets[{ path, name, class, package, origin, loaded }] }
	// The exploration workhorse: query the asset registry for base-game content without needing to know
	// exact paths up front. Unlike list_blueprints (which only reports already-loaded UBlueprints), this
	// reads the registry directly, so it sees cooked container content that was never loaded - including
	// BlueprintGeneratedClass assets that have no UBlueprint wrapper at all.
	void H_find_assets(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString ClassName = JStr(In, TEXT("class"));
		const FString PathPrefix = JStr(In, TEXT("pathPrefix"));
		const FString NameContains = JStr(In, TEXT("nameContains"));
		const FString Origin = JStr(In, TEXT("origin"), TEXT("any"));
		const bool bRecursiveClasses = JBool(In, TEXT("recursiveClasses"), true);
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 100), 1, 5000);

		IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();

		FARFilter Filter;
		Filter.bRecursiveClasses = bRecursiveClasses;
		if (!ClassName.IsEmpty())
		{
			// Accept either a full /Script/Module.Class path or a bare class name resolved the same way
			// every other MifBridge endpoint resolves one.
			if (ClassName.StartsWith(TEXT("/")))
			{
				Filter.ClassPaths.Add(FTopLevelAssetPath(ClassName));
			}
			else if (UClass* Resolved = ResolveClass(ClassName, nullptr))
			{
				Filter.ClassPaths.Add(Resolved->GetClassPathName());
			}
			else
			{
				Fail(Out, FString::Printf(TEXT("could not resolve class '%s' - pass a full path like /Script/Engine.DataTable"), *ClassName));
				return;
			}
		}
		if (!PathPrefix.IsEmpty())
		{
			Filter.PackagePaths.Add(FName(*PathPrefix));
			Filter.bRecursivePaths = true;
		}

		TArray<FAssetData> Assets;
		if (Filter.IsEmpty())
		{
			Registry.GetAllAssets(Assets);
		}
		else
		{
			Registry.GetAssets(Filter, Assets);
		}

		TArray<TSharedPtr<FJsonValue>> Arr;
		int32 Matched = 0;
		bool bTruncated = false;
		for (const FAssetData& Asset : Assets)
		{
			if (!NameContains.IsEmpty() && !Asset.AssetName.ToString().Contains(NameContains))
			{
				continue;
			}
			const bool bContainerOnly = IsContainerOnlyPackage(Asset.PackageName);
			if (Origin == TEXT("container") && !bContainerOnly) { continue; }
			if (Origin == TEXT("loose") && bContainerOnly) { continue; }

			++Matched;
			if (Arr.Num() >= Limit)
			{
				bTruncated = true;
				continue;
			}

			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("path"), Asset.GetObjectPathString());
			Json->SetStringField(TEXT("name"), Asset.AssetName.ToString());
			Json->SetStringField(TEXT("class"), Asset.AssetClassPath.ToString());
			Json->SetStringField(TEXT("package"), Asset.PackageName.ToString());
			Json->SetStringField(TEXT("origin"), bContainerOnly ? TEXT("container") : TEXT("loose"));
			Json->SetBoolField(TEXT("loaded"), Asset.IsAssetLoaded());
			Arr.Add(MakeShared<FJsonValueObject>(Json));
		}

		Out->SetNumberField(TEXT("count"), Matched);
		Out->SetNumberField(TEXT("returned"), Arr.Num());
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("assets"), Arr);
	}

	//   in:  { package: "/Game/Blueprints/Pawns/BP_BaseNPC" }  (an object path also works)
	//   out: { package, origin, existsOnDisk, inRegistry, loaded, flags{...}, registryAssets[...], exports[...] }
	// Tells you what a package actually IS in this session: cooked or not, container or loose, loaded or
	// not, and what's inside it. This is the endpoint for "why does this base-game asset behave oddly".
	void H_describe_package(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		FString PackageName = JStr(In, TEXT("package"));
		if (PackageName.IsEmpty())
		{
			PackageName = JStr(In, TEXT("path"));
		}
		if (PackageName.IsEmpty())
		{
			Fail(Out, TEXT("package is required (e.g. /Game/Blueprints/Pawns/BP_BaseNPC)"));
			return;
		}
		PackageName.TrimStartAndEndInline();
		// Accept an object path (/Game/Foo/Bar.Bar) and reduce it to its package.
		int32 DotIndex = INDEX_NONE;
		if (PackageName.FindChar(TEXT('.'), DotIndex))
		{
			PackageName = PackageName.Left(DotIndex);
		}

		const FName PackageFName(*PackageName);
		Out->SetStringField(TEXT("package"), PackageName);

		const bool bContainerOnly = IsContainerOnlyPackage(PackageFName);
		Out->SetBoolField(TEXT("existsOnDisk"), !bContainerOnly);
		Out->SetStringField(TEXT("origin"), bContainerOnly ? TEXT("container") : TEXT("loose"));

		IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
		TArray<FAssetData> PackageAssets;
		Registry.GetAssetsByPackageName(PackageFName, PackageAssets, /*bIncludeOnlyOnDiskAssets*/ false);
		Out->SetBoolField(TEXT("inRegistry"), PackageAssets.Num() > 0);

		TArray<TSharedPtr<FJsonValue>> RegistryArr;
		for (const FAssetData& Asset : PackageAssets)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("path"), Asset.GetObjectPathString());
			Json->SetStringField(TEXT("name"), Asset.AssetName.ToString());
			Json->SetStringField(TEXT("class"), Asset.AssetClassPath.ToString());
			Json->SetBoolField(TEXT("loaded"), Asset.IsAssetLoaded());
			RegistryArr.Add(MakeShared<FJsonValueObject>(Json));
		}
		Out->SetArrayField(TEXT("registryAssets"), RegistryArr);

		// Only report live package state if it's already in memory - deliberately does NOT force a load,
		// so this stays safe to call on anything (loading arbitrary cooked packages is exactly what tends
		// to crash a cooked editor).
		UPackage* Package = FindPackage(nullptr, *PackageName);
		Out->SetBoolField(TEXT("loaded"), Package != nullptr);

		if (Package)
		{
			TSharedRef<FJsonObject> Flags = MakeShared<FJsonObject>();
			Flags->SetBoolField(TEXT("cooked"), Package->HasAnyPackageFlags(PKG_Cooked));
			Flags->SetBoolField(TEXT("filterEditorOnly"), Package->HasAnyPackageFlags(PKG_FilterEditorOnly));
			Flags->SetBoolField(TEXT("isCookedForEditor"), Package->bIsCookedForEditor);
			Flags->SetBoolField(TEXT("dirty"), Package->IsDirty());
			Out->SetObjectField(TEXT("flags"), Flags);

			TArray<UObject*> Objects;
			GetObjectsWithPackage(Package, Objects, /*bIncludeNestedObjects*/ false);
			TArray<TSharedPtr<FJsonValue>> ExportArr;
			for (UObject* Obj : Objects)
			{
				if (!Obj) { continue; }
				TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
				Json->SetStringField(TEXT("name"), Obj->GetName());
				Json->SetStringField(TEXT("class"), Obj->GetClass() ? Obj->GetClass()->GetPathName() : TEXT("<null>"));
				ExportArr.Add(MakeShared<FJsonValueObject>(Json));
			}
			Out->SetArrayField(TEXT("exports"), ExportArr);
		}
	}
}
