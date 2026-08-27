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
#include "Editor.h"                 // GEditor
#include "Engine/Texture2D.h"
#include "EngineUtils.h"            // TActorIterator
#include "HAL/PlatformFileManager.h"
#include "LandscapeComponent.h"
#include "LandscapeProxy.h"
#include "MaterialShared.h"
#include "Materials/MaterialInstanceConstant.h"   // ULandscapeComponent::MaterialInstances element type
#include "MeshPassProcessor.h"                    // FCachedMeshDrawCommandInfo, EMeshPass
#include "PrimitiveSceneInfo.h"                   // FPrimitiveSceneInfo::StaticMeshCommandInfos
#include "PrimitiveSceneProxy.h"                  // FPrimitiveSceneProxy::GetPrimitiveSceneInfo
#include "StaticMeshBatch.h"                      // FStaticMeshBatchRelevance::ScreenSize / LODIndex
#include "RenderingThread.h"                      // ENQUEUE_RENDER_COMMAND, FlushRenderingCommands
#include "Materials/MaterialInterface.h"
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

	// Strict unknown-param rejection was born HERE in Batch B (find_assets accepted
	// {"recursive": false} with ok:true and ignored it — docs/audit/03_GAPS_AND_RISKS.md §7.1)
	// and promoted to the shared RejectUnknownParams in MifBridgeHandlers.h/MifBridgeCommon.cpp
	// in Batch C, so every handler file rejects through ONE implementation. Callers below are
	// unchanged.

	// ---------------------------------------------------------------- asset-row field naming
	// GAP 8 (user report): "find_assets returns package vs path inconsistently, so callers guess."
	// It was worse than inconsistent — the SAME key meant different things in different endpoints:
	// find_assets."path" is an OBJECT path (/Game/X/Foo.Foo_C) while audit_unused."path" is a
	// PACKAGE path (/Game/X/Foo). Feeding one endpoint's "path" straight into the other yields a
	// silent "asset not found", and there was no key a caller could read blind and be sure of.
	//
	// The fix is ADDITIVE — every legacy key keeps its exact previous value, and every asset row
	// additionally carries these two, which mean the same thing in EVERY endpoint:
	//
	//   objectPath  = /Game/X/Foo.Foo_C   the object inside the package. What set_property /
	//                                     get_property / open_blueprint / describe_class take.
	//   packageName = /Game/X/Foo         the package that holds it. What get_referencers /
	//                                     get_dependencies / describe_package / delete_asset take.
	//
	// One writer, so no emitter can spell them differently or fill them in the wrong order.
	// MifBridgeAssetOps.cpp holds the same three lines for the reason RejectUnknownParams was
	// once duplicated: promoting means opening MifBridgeHandlers.h, the plugin's contract surface.
	// EVICTION CLAUSE — promote it there the next time that header is edited, and if a THIRD file
	// needs it, promote instead of copying again.

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
	//          containerCount, containers[{ file, filePath, path, sizeBytes }],
	//          assetCounts{ total, containerOnly, loose, loaded } }
	//        NOT asset rows, and deliberately so: a container is a .utoc FILE, so `path` here is a
	//        FILESYSTEM path (D:\...\pakchunk0.utoc), NOT a /Game/ package. `filePath` carries the
	//        same value under a name that says which kind of path it is — "path" meaning three
	//        different things across this file is exactly the GAP 8 complaint. See the asset-row
	//        field naming block above; objectPath/packageName do not apply to a container.
	//        (`containerCount` was already emitted and simply missing from this comment.)
	// Answers "what base-game content do I actually have mounted right now, and did the mount work" - the
	// IoStore containers are mounted straight through the IoDispatcher file backend, so they never show up
	// in the normal mounted-pak list and are otherwise invisible from inside the editor.
	void H_list_mounted_containers(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {}, TEXT("(none - this endpoint takes no parameters)")))
		{
			return;
		}
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
					// filePath is the self-describing spelling; `path` is the original key, kept
					// byte-identical so existing callers do not break.
					Json->SetStringField(TEXT("filePath"), Utoc);
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

	//   in:  { class?: "DataTable"|"/Script/Engine.DataTable" (aliases: className, type),
	//          pathPrefix?: "/Game/Blueprints",
	//          nameContains?: "NPC", origin?: "container"|"loose"|"any", recursiveClasses?: bool (default true),
	//          limit?: int (default 100) }   — any other key is rejected by name, never ignored
	//   out: { count, returned, truncated,
	//          assets[{ objectPath, packageName, path, package, name, class, origin, loaded }] }
	//        objectPath  = /Game/X/Foo.Foo_C — the object (feed this to set_property/open_blueprint)
	//        packageName = /Game/X/Foo       — its package (feed this to get_referencers/describe_package)
	//        `path` and `package` are the ORIGINAL keys, unchanged: path == objectPath and
	//        package == packageName here. They are kept only for existing callers — new code should
	//        read objectPath/packageName, because `path` does NOT mean the same thing in
	//        audit_unused (there it is the PACKAGE path). See the asset-row field naming block above.
	//        (`returned` was already emitted and simply missing from this comment.)
	// The exploration workhorse: query the asset registry for base-game content without needing to know
	// exact paths up front. Unlike list_blueprints (which only reports already-loaded UBlueprints), this
	// reads the registry directly, so it sees cooked container content that was never loaded - including
	// BlueprintGeneratedClass assets that have no UBlueprint wrapper at all.
	void H_find_assets(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("class"), TEXT("className"), TEXT("type"), TEXT("pathPrefix"), TEXT("nameContains"),
			  TEXT("origin"), TEXT("recursiveClasses"), TEXT("limit") },
			TEXT("class (aliases: className, type), pathPrefix, nameContains, origin, recursiveClasses, limit"),
			{{ TEXT("recursive"),
			   TEXT("not implemented - pathPrefix matching is ALWAYS recursive; recursiveClasses controls class-hierarchy matching") }}))
		{
			return;
		}
		// PM-001 house pattern: accept the spellings a caller would plausibly use for the class filter.
		// 'className' was live-guessed during the audit and silently matched nothing.
		const FString ClassName = JStrAny(In, { TEXT("class"), TEXT("className"), TEXT("type") });
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

			const FString AssetObjectPath = Asset.GetObjectPathString();
			const FString AssetPackageName = Asset.PackageName.ToString();

			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			EmitAssetIdentity(Json, AssetObjectPath, AssetPackageName);
			Json->SetStringField(TEXT("path"), AssetObjectPath);        // legacy key: == objectPath
			Json->SetStringField(TEXT("name"), Asset.AssetName.ToString());
			Json->SetStringField(TEXT("class"), Asset.AssetClassPath.ToString());
			Json->SetStringField(TEXT("package"), AssetPackageName);    // legacy key: == packageName
			Json->SetStringField(TEXT("origin"), bContainerOnly ? TEXT("container") : TEXT("loose"));
			Json->SetBoolField(TEXT("loaded"), Asset.IsAssetLoaded());
			Arr.Add(MakeShared<FJsonValueObject>(Json));
		}

		Out->SetNumberField(TEXT("count"), Matched);
		Out->SetNumberField(TEXT("returned"), Arr.Num());
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("assets"), Arr);

		// THE COOKED-BLUEPRINT TRAP, and the reason this block exists rather than a docs note.
		//
		// On a COOKED project a Blueprint asset is registered as its GENERATED CLASS -
		// BlueprintGeneratedClass, WidgetBlueprintGeneratedClass, AnimBlueprintGeneratedClass - and not
		// as Blueprint at all. Asking for the obvious class name therefore returns a SMALL NUMBER
		// rather than an error, which is the worst possible shape: measured on DDS2, /Game/Blueprints
		// holds 26 assets of class Blueprint and 915 of class BlueprintGeneratedClass. Under 3%, with
		// ok:true and nothing to suggest the answer was anywhere else.
		//
		// Worse still, the few that DO come back are mostly assets this bridge created itself in the
		// session, because anything newly authored is uncooked. So the caller gets a confident answer
		// composed almost entirely of their own scratch.
		//
		// Found while trying to establish whether DDS2 has any Chaos vehicles: find_assets for
		// class:"Blueprint" nameContains:"VehicleBoat" returned 0, and the same query against
		// BlueprintGeneratedClass returned 15.
		//
		// So the count is re-run against the generated-class spelling and the difference reported. Only
		// when it is genuinely bigger - a project that is not cooked pays one extra registry query and
		// hears nothing.
		{
			static const TCHAR* Editor[] = { TEXT("Blueprint"), TEXT("WidgetBlueprint"), TEXT("AnimBlueprint") };
			for (const TCHAR* Name : Editor)
			{
				if (!ClassName.Equals(Name, ESearchCase::IgnoreCase)) { continue; }
				// RESOLVED BY NAME, not built as "/Script/Engine.<X>GeneratedClass". That spelling was
				// written first and was wrong for the widget family: WidgetBlueprintGeneratedClass
				// lives in /Script/UMG, so the note fired for Blueprint and AnimBlueprint and stayed
				// silent for the one family with the widest gap (78 against 279). Caught by the test,
				// which is the only reason it is not still wrong.
				UClass* AltClass = ResolveClass(FString(Name) + TEXT("GeneratedClass"), nullptr);
				if (!AltClass) { break; }
				FARFilter Alt = Filter;
				Alt.ClassPaths.Empty();
				Alt.ClassPaths.Add(AltClass->GetClassPathName());
				TArray<FAssetData> AltAssets;
				Registry.GetAssets(Alt, AltAssets);
				int32 AltMatched = 0;
				for (const FAssetData& A : AltAssets)
				{
					if (!NameContains.IsEmpty() && !A.AssetName.ToString().Contains(NameContains)) { continue; }
					const bool bAltContainer = IsContainerOnlyPackage(A.PackageName);
					if (Origin == TEXT("container") && !bAltContainer) { continue; }
					if (Origin == TEXT("loose") && bAltContainer) { continue; }
					++AltMatched;
				}
				if (AltMatched > Matched)
				{
					Out->SetNumberField(TEXT("generatedClassCount"), AltMatched);
					Out->SetStringField(TEXT("cookedClassNote"), FString::Printf(
						TEXT("this filter matched %d asset(s) of class %s, but %d of class %sGeneratedClass. "
							 "On a COOKED project a blueprint is registered as its generated class, so the "
							 "%d here are the uncooked ones - typically only what has been authored in this "
							 "session. Re-run with class:\"%sGeneratedClass\" for the real answer."),
						Matched, Name, AltMatched, Name, Matched, Name));
				}
				break;
			}
		}
	}

	//   in:  { package: "/Game/Blueprints/Pawns/BP_BaseNPC" }  (an object path also works)  (alias: path)
	//   out: { package, packageName, origin, existsOnDisk, inRegistry, loaded, flags{...},
	//          registryAssets[{ objectPath, packageName, path, package, name, class, origin, loaded }],
	//          exports[{ objectPath, packageName, name, class }] }
	//        Top level: `packageName` is the plugin-wide spelling, `package` the original key —
	//        identical values, both are the PACKAGE path (/Game/X/Foo), never an object path.
	//        registryAssets rows now carry the same eight keys find_assets emits, so ONE caller-side
	//        parser handles both; `package`/`origin` are the additions. exports[] rows carry
	//        objectPath (= UObject::GetPathName(), including any ":Subobject" — hand it straight to
	//        set_property/get_property rather than rebuilding "<package>.<name>" yourself).
	//        See the asset-row field naming block above for objectPath vs packageName.
	// Tells you what a package actually IS in this session: cooked or not, container or loose, loaded or
	// not, and what's inside it. This is the endpoint for "why does this base-game asset behave oddly".
	void H_describe_package(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("package"), TEXT("path") }, TEXT("package (alias: path)")))
		{
			return;
		}
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
		// Same value under the plugin-wide spelling: a caller that reads packageName off ANY
		// endpoint's response gets a package path, with no per-endpoint lookup table.
		Out->SetStringField(TEXT("packageName"), PackageName);

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
			const FString AssetObjectPath = Asset.GetObjectPathString();
			const FString AssetPackageName = Asset.PackageName.ToString();

			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			EmitAssetIdentity(Json, AssetObjectPath, AssetPackageName);
			Json->SetStringField(TEXT("path"), AssetObjectPath);        // legacy key: == objectPath
			Json->SetStringField(TEXT("name"), Asset.AssetName.ToString());
			Json->SetStringField(TEXT("class"), Asset.AssetClassPath.ToString());
			// package + origin did not exist on this row and DO exist on find_assets rows. Adding
			// them (rather than leaving two near-identical shapes) is the point of GAP 8: a caller
			// should not need to know which endpoint produced a row to parse it.
			Json->SetStringField(TEXT("package"), AssetPackageName);    // legacy-compatible: == packageName
			Json->SetStringField(TEXT("origin"), bContainerOnly ? TEXT("container") : TEXT("loose"));
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
				// GetPathName() IS the objectPath every other endpoint accepts, subobject separator
				// and all. Emitting only `name` forced the caller to rebuild "<package>.<name>",
				// which is wrong for anything nested (":Mesh_GEN_VARIABLE" — docs/02_GOTCHAS.md §5d).
				EmitAssetIdentity(Json, Obj->GetPathName(), PackageName);
				Json->SetStringField(TEXT("name"), Obj->GetName());
				Json->SetStringField(TEXT("class"), Obj->GetClass() ? Obj->GetClass()->GetPathName() : TEXT("<null>"));
				ExportArr.Add(MakeShared<FJsonValueObject>(Json));
			}
			Out->SetArrayField(TEXT("exports"), ExportArr);
		}
	}

	//   in:  { limit?: int (default 40) }
	//   out: { world, proxyCount, componentCount, aggregate{...}, proxies[{...}],
	//          sampleMaterialsWithLandscapeVF[], sampleMaterialsWithoutLandscapeVF[], contrastProxies[] }
	//        These are COMPONENT rows, not asset rows, so objectPath/packageName (see the asset-row
	//        field naming block above) deliberately do not appear. Nothing here is keyed `path` or
	//        `package`: `material` holds a full object path and says so by its own name, and
	//        `proxy`/`component` are actor/component names. Left unchanged on purpose — GAP 8 is
	//        about keys whose MEANING varied by endpoint, and none of these do.
	// Live per-component state for every landscape proxy in the EDITOR world. Built for the cooked-editor case
	// where most LandscapeStreamingProxies never draw even though collision view shows the full terrain: the
	// actor is present and selectable, so the real question is which stage between "component exists" and
	// "pixels on screen" is failing, and what differs between a proxy that draws and one that doesn't.
	//
	// Per component it reports: whether a render-thread SceneProxy was created at all, whether the component is
	// registered and flagged visible, whether its heightmap texture has any mip actually resident, and whether a
	// material resolved. The heightmap residency is the interesting one - a heightmap with 0 resident mips
	// yields no geometry from the landscape vertex shader, while collision (which reads separate cooked
	// collision data, not the texture) stays perfect. That combination looks exactly like this bug.
	// Names every shader type actually present in a mesh shader map. There is no direct enumeration of
	// a map's contents by name, so walk the global shader type registry and probe each type/permutation.
	// Only worth running on a handful of maps - it is thousands of probes per call.
	static void CollectShaderTypeNames(const FMeshMaterialShaderMap* Map, TArray<FString>& OutNames)
	{
		if (!Map)
		{
			return;
		}
		for (TLinkedList<FShaderType*>::TIterator It(FShaderType::GetTypeList()); It; It.Next())
		{
			FShaderType* Type = *It;
			if (!Type)
			{
				continue;
			}
			const int32 NumPermutations = FMath::Max(1, Type->GetPermutationCount());
			for (int32 P = 0; P < NumPermutations; ++P)
			{
				if (Map->HasShader(Type, P))
				{
					OutNames.Add(NumPermutations > 1
						? FString::Printf(TEXT("%s [perm %d]"), Type->GetName(), P)
						: FString(Type->GetName()));
				}
			}
		}
		OutNames.Sort();
	}

	// Shader counts + type names for every entry in a component's MaterialInstances, not just slot 0.
	// Rendering picks AvailableMaterials[LODIndexToMaterialIndex[LOD]], and these components draw at
	// LOD 2-3, so slot 0 is not necessarily the material that actually draws.
	static TSharedRef<FJsonObject> DescribeMaterialSlot(UMaterialInterface* Mat, ERHIFeatureLevel::Type FeatureLevel, int32 SlotIndex, bool bWantNames)
	{
		TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
		J->SetNumberField(TEXT("slot"), SlotIndex);
		J->SetStringField(TEXT("material"), Mat ? Mat->GetPathName() : TEXT("<null>"));

		const FMaterialResource* Res = Mat ? Mat->GetMaterialResource(FeatureLevel) : nullptr;
		FMaterialShaderMap* ShaderMap = Res ? Res->GetGameThreadShaderMap() : nullptr;
		if (!ShaderMap)
		{
			J->SetNumberField(TEXT("landscapeVFShaders"), -1);
			return J;
		}

		static const FHashedName LandscapeVFName(TEXT("FLandscapeVertexFactory"));
		static const FHashedName LandscapeFixedGridVFName(TEXT("FLandscapeFixedGridVertexFactory"));
		const FMeshMaterialShaderMap* LandscapeVFMap = ShaderMap->GetMeshShaderMap(LandscapeVFName);

		J->SetNumberField(TEXT("landscapeVFShaders"), LandscapeVFMap ? (int32)LandscapeVFMap->GetNumShaders() : -1);
		J->SetBoolField(TEXT("hasFixedGridVF"), ShaderMap->GetMeshShaderMap(LandscapeFixedGridVFName) != nullptr);

		if (bWantNames)
		{
			TArray<FString> Names;
			CollectShaderTypeNames(LandscapeVFMap, Names);
			TArray<TSharedPtr<FJsonValue>> Arr;
			for (const FString& N : Names)
			{
				Arr.Add(MakeShared<FJsonValueString>(N));
			}
			J->SetArrayField(TEXT("shaderTypes"), Arr);
		}
		return J;
	}

	void H_diagnose_landscape(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("limit") }, TEXT("limit")))
		{
			return;
		}
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 40), 1, 1000);

		UWorld* World = EditorWorld();
		if (!World)
		{
			Fail(Out, TEXT("no editor world"));
			return;
		}
		Out->SetStringField(TEXT("world"), World->GetName());

		// Use the world's own feature level rather than GMaxRHIFeatureLevel (which lives in the RHI module and
		// would mean linking RHI into MifBridge just for this) - it's also the feature level these materials are
		// actually being rendered at.
		const ERHIFeatureLevel::Type WorldFeatureLevel = World->GetFeatureLevel();

		int32 ProxyCount = 0, ComponentCount = 0;
		int32 AggSceneProxy = 0, AggRegistered = 0, AggVisibleFlag = 0;
		int32 AggHeightmapNull = 0, AggHeightmapZeroResident = 0, AggMaterialNull = 0, AggOverrideMaterial = 0;
		int32 AggBadBounds = 0;
		int32 AggNoMatResource = 0, AggNoShaderMap = 0, AggShaderMapInvalid = 0;
		int32 AggWmTotal = 0, AggWmNone = 0, AggWmNull = 0, AggWmZeroResident = 0;
		int32 AggNoLandscapeVF = 0, AggNoFixedGridVF = 0, AggHasXYOffsetVF = 0;

		// Concrete material paths from each side of the landscape-VF split, so broken instances can be
		// named rather than only counted. Each proxy owns its own LandscapeMaterialInstanceConstant_N
		// objects, so the path identifies exactly which cooked instance is short of shaders.
		TArray<FString> MatWithVF, MatWithoutVF;

		// Per-component detail for the first few proxies that contain a mix of FixedGrid-capable and
		// non-capable components. Those proxies render exactly one of their four quadrants, so comparing
		// the four materials inside one proxy isolates what differs between a component that draws and
		// one that doesn't, with layer content and everything else held constant.
		TArray<TSharedPtr<FJsonValue>> ContrastArr;
		const int32 MaxContrastProxies = 4;

		TArray<TSharedPtr<FJsonValue>> ProxyArr;

		for (TActorIterator<ALandscapeProxy> It(World); It; ++It)
		{
			ALandscapeProxy* Proxy = *It;
			if (!Proxy)
			{
				continue;
			}
			++ProxyCount;

			int32 NumComp = 0, NumSceneProxy = 0, NumRegistered = 0, NumVisible = 0;
			int32 NumHmNull = 0, NumHmZeroResident = 0, NumMatNull = 0, NumOverride = 0, NumBadBounds = 0;
			int32 NumNoMatResource = 0, NumNoShaderMap = 0, NumShaderMapInvalid = 0;
			int32 NumWmTotal = 0, NumWmNone = 0, NumWmNull = 0, NumWmZeroResident = 0;
			int32 NumNoLandscapeVF = 0, NumNoFixedGridVF = 0, NumHasXYOffsetVF = 0;
			TArray<TSharedPtr<FJsonValue>> CompDetails;
			// Enumerating shader type names is expensive, so only gather it while we still might keep
			// this proxy as a contrast sample.
			const bool bWantDetail = (ContrastArr.Num() < MaxContrastProxies);
			FString WmFirstName; int32 WmFirstResident = -1, WmFirstMips = -1, WmFirstSizeX = -1;
			double BoundsRadius = -1.0;
			FVector BoundsOrigin = FVector::ZeroVector;
			FVector BoundsExtent = FVector::ZeroVector;
			// Details of the first heightmap found, as a representative sample for this proxy.
			FString HmName;
			int32 HmResident = -1, HmMips = -1, HmSizeX = -1;

			for (ULandscapeComponent* Comp : Proxy->LandscapeComponents)
			{
				if (!Comp)
				{
					continue;
				}
				++NumComp;
				++ComponentCount;

				if (Comp->SceneProxy)       { ++NumSceneProxy; ++AggSceneProxy; }
				if (Comp->IsRegistered())   { ++NumRegistered; ++AggRegistered; }
				if (Comp->GetVisibleFlag()) { ++NumVisible;    ++AggVisibleFlag; }
				if (Comp->OverrideMaterial) { ++NumOverride;   ++AggOverrideMaterial; }

				// Weightmaps drive COLOUR (which layer is painted where); the heightmap above only drives
				// geometry. Geometry being perfect while colour is flat/wrong on most plots points straight
				// here, and this is the one input never checked so far.
				const TArray<UTexture2D*>& Weightmaps = Comp->GetWeightmapTextures();
				NumWmTotal += Weightmaps.Num();
				AggWmTotal += Weightmaps.Num();
				if (Weightmaps.Num() == 0)
				{
					++NumWmNone;
					++AggWmNone;
				}
				for (UTexture2D* Wm : Weightmaps)
				{
					if (!Wm)
					{
						++NumWmNull;
						++AggWmNull;
						continue;
					}
					const int32 WmResident = Wm->GetNumResidentMips();
					if (WmResident <= 0)
					{
						++NumWmZeroResident;
						++AggWmZeroResident;
					}
					if (WmFirstResident < 0)
					{
						WmFirstName = Wm->GetName();
						WmFirstResident = WmResident;
						WmFirstMips = Wm->GetNumMips();
						WmFirstSizeX = Wm->GetSizeX();
					}
				}

				UTexture2D* Heightmap = Comp->GetHeightmap();
				if (!Heightmap)
				{
					++NumHmNull;
					++AggHeightmapNull;
				}
				else
				{
					const int32 Resident = Heightmap->GetNumResidentMips();
					if (Resident <= 0)
					{
						++NumHmZeroResident;
						++AggHeightmapZeroResident;
					}
					if (HmResident < 0)
					{
						HmName = Heightmap->GetName();
						HmResident = Resident;
						HmMips = Heightmap->GetNumMips();
						HmSizeX = Heightmap->GetSizeX();
					}
				}

				// Captured per component so the four components of one proxy can be compared side by side.
				FString CompMatPath;
				int32 CompLandscapeVFShaders = -1;
				int32 CompHasLandscapeVF = -1, CompHasFixedGridVF = -1, CompHasXYOffsetVF = -1;

				UMaterialInterface* MatIface = Comp->GetMaterialInstance(0, /*InDynamic*/ false);
				if (!MatIface)
				{
					++NumMatNull;
					++AggMaterialNull;
				}
				else
				{
					// The material object existing says nothing about whether it can actually DRAW. A cooked
					// material whose shader map didn't load in this editor (the "Missing shader resource" class
					// of problem) has a live UMaterialInstance but no usable shaders, so its draw produces
					// nothing while every CPU-side check above still looks perfectly healthy. This is the only
					// remaining thing that distinguishes a landscape component that renders from one that
					// doesn't, and it matches a freshly-compiled material fixing some plots.
					const FMaterialResource* Res = MatIface->GetMaterialResource(WorldFeatureLevel);
					if (!Res)
					{
						++NumNoMatResource;
						++AggNoMatResource;
					}
					else
					{
						FMaterialShaderMap* ShaderMap = Res->GetGameThreadShaderMap();
						if (!ShaderMap)
						{
							++NumNoShaderMap;
							++AggNoShaderMap;
						}
						else if (!ShaderMap->IsValidForRendering())
						{
							++NumShaderMapInvalid;
							++AggShaderMapInvalid;
						}
						else
						{
							// A shader map can be present and "valid for rendering" while still containing no
							// FMeshMaterialShaderMap for the vertex factory this component actually draws with.
							// When that happens the base pass mesh processor cannot resolve shaders and silently
							// drops the batch - no warning, no draw - which is indistinguishable from a healthy
							// component in every other check here. Look the factories up by name so this file
							// doesn't have to pull in LandscapeRender.h and the render-module link that implies.
							static const FHashedName LandscapeVFName(TEXT("FLandscapeVertexFactory"));
							static const FHashedName LandscapeXYOffsetVFName(TEXT("FLandscapeXYOffsetVertexFactory"));
							static const FHashedName LandscapeFixedGridVFName(TEXT("FLandscapeFixedGridVertexFactory"));

							// FLandscapeVertexFactory is the main pass; FLandscapeFixedGridVertexFactory is the
							// RVT path. If the RVT one cooked but the main-pass one didn't, that pins the cause
							// to what the cook decided this material would ever be drawn with.
							const FMeshMaterialShaderMap* LandscapeVFMap = ShaderMap->GetMeshShaderMap(LandscapeVFName);
							const bool bHasLandscapeVF = LandscapeVFMap != nullptr;
							const bool bHasXYOffsetVF = ShaderMap->GetMeshShaderMap(LandscapeXYOffsetVFName) != nullptr;
							const bool bHasFixedGridVF = ShaderMap->GetMeshShaderMap(LandscapeFixedGridVFName) != nullptr;

							// The map merely existing is not enough - ShouldCache gates per shader TYPE, so a map
							// can be present holding only depth/shadow shaders while the base pass permutation was
							// skipped. That is exactly a silent no-draw, and only the count reveals it.
							CompMatPath = MatIface->GetPathName();
							CompLandscapeVFShaders = LandscapeVFMap ? (int32)LandscapeVFMap->GetNumShaders() : -1;
							CompHasLandscapeVF = bHasLandscapeVF ? 1 : 0;
							CompHasFixedGridVF = bHasFixedGridVF ? 1 : 0;
							CompHasXYOffsetVF = bHasXYOffsetVF ? 1 : 0;

							if (!bHasLandscapeVF) { ++NumNoLandscapeVF; ++AggNoLandscapeVF; }
							if (!bHasFixedGridVF) { ++NumNoFixedGridVF; ++AggNoFixedGridVF; }
							if (bHasXYOffsetVF)   { ++NumHasXYOffsetVF; ++AggHasXYOffsetVF; }

							TArray<FString>& Bucket = bHasLandscapeVF ? MatWithVF : MatWithoutVF;
							if (Bucket.Num() < 8)
							{
								Bucket.AddUnique(MatIface->GetPathName());
							}
						}
					}
				}

				// Bounds are the one thing that culls a primitive BEFORE view relevance / mesh gathering, so a
				// component can be registered, visible, fully streamed and still never draw a pixel if these are
				// degenerate or in the wrong place. Nothing above would reveal that.
				const FBoxSphereBounds& B = Comp->Bounds;
				if (B.SphereRadius <= 0.0 || B.BoxExtent.IsNearlyZero())
				{
					++NumBadBounds;
					++AggBadBounds;
				}
				if (BoundsRadius < 0.0)
				{
					BoundsRadius = B.SphereRadius;
					BoundsOrigin = B.Origin;
					BoundsExtent = B.BoxExtent;
				}

				TSharedRef<FJsonObject> C = MakeShared<FJsonObject>();
				C->SetStringField(TEXT("component"), Comp->GetName());
				C->SetStringField(TEXT("sectionBase"), FIntPoint(Comp->SectionBaseX, Comp->SectionBaseY).ToString());
				C->SetStringField(TEXT("material"), CompMatPath);
				C->SetNumberField(TEXT("landscapeVFShaders"), CompLandscapeVFShaders);
				C->SetNumberField(TEXT("hasLandscapeVF"), CompHasLandscapeVF);
				C->SetNumberField(TEXT("hasFixedGridVF"), CompHasFixedGridVF);
				C->SetNumberField(TEXT("hasXYOffsetVF"), CompHasXYOffsetVF);

				if (bWantDetail)
				{
					FString LodMap;
					for (int32 i = 0; i < Comp->LODIndexToMaterialIndex.Num(); ++i)
					{
						LodMap += (i ? TEXT(",") : TEXT("")) + FString::FromInt(Comp->LODIndexToMaterialIndex[i]);
					}
					C->SetStringField(TEXT("lodIndexToMaterialIndex"), LodMap);

					TArray<TSharedPtr<FJsonValue>> SlotArr;
					for (int32 i = 0; i < Comp->MaterialInstances.Num(); ++i)
					{
						SlotArr.Add(MakeShared<FJsonValueObject>(
							DescribeMaterialSlot(Comp->MaterialInstances[i].Get(), WorldFeatureLevel, i, /*bWantNames*/ true)));
					}
					C->SetArrayField(TEXT("materialSlots"), SlotArr);
				}

				CompDetails.Add(MakeShared<FJsonValueObject>(C));
			}

			// Only interesting where the proxy is split - all-drawing or all-black proxies tell us nothing
			// about what distinguishes the two.
			const bool bMixedProxy = (NumHasXYOffsetVF > 0) && (NumHasXYOffsetVF < NumComp);
			if (bMixedProxy && ContrastArr.Num() < MaxContrastProxies)
			{
				TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
				P->SetStringField(TEXT("proxy"), Proxy->GetName());
				P->SetArrayField(TEXT("components"), CompDetails);
				ContrastArr.Add(MakeShared<FJsonValueObject>(P));
			}

			if (ProxyArr.Num() < Limit)
			{
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("name"), Proxy->GetName());
				J->SetNumberField(TEXT("components"), NumComp);
				J->SetNumberField(TEXT("withSceneProxy"), NumSceneProxy);
				J->SetNumberField(TEXT("registered"), NumRegistered);
				J->SetNumberField(TEXT("visibleFlag"), NumVisible);
				J->SetNumberField(TEXT("heightmapNull"), NumHmNull);
				J->SetNumberField(TEXT("heightmapZeroResident"), NumHmZeroResident);
				J->SetNumberField(TEXT("materialNull"), NumMatNull);
				J->SetNumberField(TEXT("overrideMaterial"), NumOverride);
				J->SetNumberField(TEXT("badBounds"), NumBadBounds);
				J->SetNumberField(TEXT("noMatResource"), NumNoMatResource);
				J->SetNumberField(TEXT("noShaderMap"), NumNoShaderMap);
				J->SetNumberField(TEXT("shaderMapInvalid"), NumShaderMapInvalid);
				J->SetNumberField(TEXT("noLandscapeVF"), NumNoLandscapeVF);
				J->SetNumberField(TEXT("noFixedGridVF"), NumNoFixedGridVF);
				J->SetNumberField(TEXT("hasXYOffsetVF"), NumHasXYOffsetVF);
				J->SetNumberField(TEXT("wmCount"), NumWmTotal);
				J->SetNumberField(TEXT("wmNone"), NumWmNone);
				J->SetNumberField(TEXT("wmNull"), NumWmNull);
				J->SetNumberField(TEXT("wmZeroResident"), NumWmZeroResident);
				J->SetStringField(TEXT("wmName"), WmFirstName);
				J->SetNumberField(TEXT("wmResidentMips"), WmFirstResident);
				J->SetNumberField(TEXT("wmNumMips"), WmFirstMips);
				J->SetNumberField(TEXT("wmSizeX"), WmFirstSizeX);
				J->SetNumberField(TEXT("boundsRadius"), BoundsRadius);
				J->SetStringField(TEXT("boundsOrigin"), BoundsOrigin.ToString());
				J->SetStringField(TEXT("boundsExtent"), BoundsExtent.ToString());
				J->SetStringField(TEXT("hmName"), HmName);
				J->SetNumberField(TEXT("hmResidentMips"), HmResident);
				J->SetNumberField(TEXT("hmNumMips"), HmMips);
				J->SetNumberField(TEXT("hmSizeX"), HmSizeX);
				J->SetBoolField(TEXT("actorHidden"), Proxy->IsHidden());
				ProxyArr.Add(MakeShared<FJsonValueObject>(J));
			}
		}

		Out->SetNumberField(TEXT("proxyCount"), ProxyCount);
		Out->SetNumberField(TEXT("componentCount"), ComponentCount);

		TSharedRef<FJsonObject> Agg = MakeShared<FJsonObject>();
		Agg->SetNumberField(TEXT("withSceneProxy"), AggSceneProxy);
		Agg->SetNumberField(TEXT("registered"), AggRegistered);
		Agg->SetNumberField(TEXT("visibleFlag"), AggVisibleFlag);
		Agg->SetNumberField(TEXT("heightmapNull"), AggHeightmapNull);
		Agg->SetNumberField(TEXT("heightmapZeroResident"), AggHeightmapZeroResident);
		Agg->SetNumberField(TEXT("materialNull"), AggMaterialNull);
		Agg->SetNumberField(TEXT("overrideMaterial"), AggOverrideMaterial);
		Agg->SetNumberField(TEXT("badBounds"), AggBadBounds);
		Agg->SetNumberField(TEXT("noMatResource"), AggNoMatResource);
		Agg->SetNumberField(TEXT("noShaderMap"), AggNoShaderMap);
		Agg->SetNumberField(TEXT("shaderMapInvalid"), AggShaderMapInvalid);
		Agg->SetNumberField(TEXT("wmTotal"), AggWmTotal);
		Agg->SetNumberField(TEXT("wmNone"), AggWmNone);
		Agg->SetNumberField(TEXT("wmNull"), AggWmNull);
		Agg->SetNumberField(TEXT("wmZeroResident"), AggWmZeroResident);
		Agg->SetNumberField(TEXT("noLandscapeVF"), AggNoLandscapeVF);
		Agg->SetNumberField(TEXT("noFixedGridVF"), AggNoFixedGridVF);
		Agg->SetNumberField(TEXT("hasXYOffsetVF"), AggHasXYOffsetVF);
		Out->SetObjectField(TEXT("aggregate"), Agg);

		auto ToJsonStrings = [](const TArray<FString>& In)
		{
			TArray<TSharedPtr<FJsonValue>> Arr;
			for (const FString& S : In)
			{
				Arr.Add(MakeShared<FJsonValueString>(S));
			}
			return Arr;
		};
		Out->SetArrayField(TEXT("sampleMaterialsWithLandscapeVF"), ToJsonStrings(MatWithVF));
		Out->SetArrayField(TEXT("sampleMaterialsWithoutLandscapeVF"), ToJsonStrings(MatWithoutVF));
		Out->SetArrayField(TEXT("contrastProxies"), ContrastArr);

		Out->SetArrayField(TEXT("proxies"), ProxyArr);
	}

	// Everything up to and including mesh-batch creation has been measured healthy for all 504 landscape
	// components, yet most never appear in the base pass. The one stage never inspected is what the renderer
	// actually caches for them: FPrimitiveSceneInfo::StaticMeshCommandInfos. A primitive with static meshes but
	// no BasePass entry there had its draw command dropped during CacheMeshDrawCommands - which is silent, and
	// is the only remaining explanation consistent with every other measurement.
	// BLOCKING HAZARD, declared (docs/02_GOTCHAS.md requires every endpoint to state these, and this
	// one stated none): the gather below ends in ENQUEUE_RENDER_COMMAND + FlushRenderingCommands(), a
	// hard game/render-thread sync per call with the HTTP ticker stopped. It is bounded, but on a
	// heavy landscape scene that is tens-to-hundreds of ms in which the bridge answers nothing at all.
	// The raw FPrimitiveSceneProxy* pointers captured for that command are safe ONLY because nothing
	// else runs on the game thread between the gather and the flush — do not introduce a yield, a
	// load, or a deferred step between them.
	void H_diagnose_landscape_draws(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("limit") }, TEXT("limit")))
		{
			return;
		}
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 40), 1, 1000);

		UWorld* World = EditorWorld();
		if (!World)
		{
			Fail(Out, TEXT("no editor world"));
			return;
		}
		Out->SetStringField(TEXT("world"), World->GetName());

		// One entry per registered static mesh: which LOD it is and the screen size the renderer compares
		// against when choosing. A component can hold valid commands for LODs the renderer never selects.
		struct FRel
		{
			int32 LODIndex = -1;
			float ScreenSize = -1.0f;
			bool bUseForMaterial = false;
			bool bRenderToVirtualTexture = false;
		};

		struct FEntry
		{
			FString Proxy;
			FString Component;
			FIntPoint SectionBase = FIntPoint::ZeroValue;
			FPrimitiveSceneProxy* SceneProxy = nullptr;
			int32 StaticMeshes = -1;
			int32 MdcTotal = -1;
			int32 MdcBasePass = -1;
			int32 MdcBasePassCached = -1;
			int32 MdcDepthPass = -1;
			TArray<FRel> Rels;
		};
		TArray<FEntry> Entries;

		for (TActorIterator<ALandscapeProxy> It(World); It; ++It)
		{
			ALandscapeProxy* Proxy = *It;
			if (!Proxy)
			{
				continue;
			}
			for (ULandscapeComponent* Comp : Proxy->LandscapeComponents)
			{
				if (!Comp || !Comp->SceneProxy)
				{
					continue;
				}
				FEntry E;
				E.Proxy = Proxy->GetName();
				E.Component = Comp->GetName();
				E.SectionBase = FIntPoint(Comp->SectionBaseX, Comp->SectionBaseY);
				E.SceneProxy = Comp->SceneProxy;
				Entries.Add(MoveTemp(E));
			}
		}

		// StaticMeshCommandInfos is render-thread state. Read it on that thread and block, rather than racing
		// the renderer from the game thread. Capturing by reference is safe because of the flush below.
		ENQUEUE_RENDER_COMMAND(MifBridgeLandscapeDraws)(
			[&Entries](FRHICommandListImmediate&)
			{
				for (FEntry& E : Entries)
				{
					FPrimitiveSceneInfo* Info = E.SceneProxy ? E.SceneProxy->GetPrimitiveSceneInfo() : nullptr;
					if (!Info)
					{
						continue;
					}
					E.StaticMeshes = Info->StaticMeshes.Num();
					E.MdcTotal = Info->StaticMeshCommandInfos.Num();
					E.MdcBasePass = 0;
					E.MdcBasePassCached = 0;
					E.MdcDepthPass = 0;
					for (const FCachedMeshDrawCommandInfo& C : Info->StaticMeshCommandInfos)
					{
						if (C.MeshPass == EMeshPass::BasePass)
						{
							++E.MdcBasePass;
							// A command is only reachable at render time if it landed in one of these two stores.
							if (C.CommandIndex != INDEX_NONE || C.StateBucketId != INDEX_NONE)
							{
								++E.MdcBasePassCached;
							}
						}
						else if (C.MeshPass == EMeshPass::DepthPass)
						{
							++E.MdcDepthPass;
						}
					}

					for (const FStaticMeshBatchRelevance& R : Info->StaticMeshRelevances)
					{
						FRel Rel;
						Rel.LODIndex = R.LODIndex;
						Rel.ScreenSize = R.ScreenSize;
						Rel.bUseForMaterial = R.bUseForMaterial != 0;
						Rel.bRenderToVirtualTexture = R.bRenderToVirtualTexture != 0;
						E.Rels.Add(Rel);
					}
				}
			});
		FlushRenderingCommands();

		int32 NoSceneInfo = 0, NoStaticMeshes = 0, NoBasePass = 0, NoBasePassCached = 0, HasBasePass = 0;
		TMap<FString, int32> ProxyNoBasePass, ProxyHasBasePass;
		for (const FEntry& E : Entries)
		{
			if (E.StaticMeshes < 0)          { ++NoSceneInfo; continue; }
			if (E.StaticMeshes == 0)         { ++NoStaticMeshes; }
			if (E.MdcBasePass == 0)          { ++NoBasePass;       ProxyNoBasePass.FindOrAdd(E.Proxy)++; }
			else                             { ++HasBasePass;      ProxyHasBasePass.FindOrAdd(E.Proxy)++; }
			if (E.MdcBasePassCached == 0)    { ++NoBasePassCached; }
		}

		TSharedRef<FJsonObject> Agg = MakeShared<FJsonObject>();
		Agg->SetNumberField(TEXT("componentsWithSceneProxy"), Entries.Num());
		Agg->SetNumberField(TEXT("noSceneInfo"), NoSceneInfo);
		Agg->SetNumberField(TEXT("noStaticMeshes"), NoStaticMeshes);
		Agg->SetNumberField(TEXT("withBasePassCommand"), HasBasePass);
		Agg->SetNumberField(TEXT("withoutBasePassCommand"), NoBasePass);
		Agg->SetNumberField(TEXT("basePassCommandNotCached"), NoBasePassCached);
		Out->SetObjectField(TEXT("aggregate"), Agg);

		// "Has at least one base pass command" is too coarse: landscape registers one static mesh per LOD and
		// the renderer submits only the LOD it selects, so a component holding a command for just one LOD can
		// still draw nothing. Report the spread of both counts.
		TMap<int32, int32> StaticMeshHisto, BasePassHisto;
		for (const FEntry& E : Entries)
		{
			if (E.StaticMeshes < 0) continue;
			StaticMeshHisto.FindOrAdd(E.StaticMeshes)++;
			BasePassHisto.FindOrAdd(E.MdcBasePass)++;
		}
		auto HistoToJson = [](const TMap<int32, int32>& H)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			for (const TPair<int32, int32>& P : H)
			{
				J->SetNumberField(FString::FromInt(P.Key), P.Value);
			}
			return J;
		};
		Out->SetObjectField(TEXT("staticMeshCountHistogram"), HistoToJson(StaticMeshHisto));
		Out->SetObjectField(TEXT("basePassCommandCountHistogram"), HistoToJson(BasePassHisto));

		// Emitted unconditionally: if every component turns out identical there is no "mixed" proxy to show,
		// and we would otherwise learn nothing about what the renderer has to choose from.
		auto DescribeEntry = [](const FEntry& E)
		{
			TSharedRef<FJsonObject> C = MakeShared<FJsonObject>();
			C->SetStringField(TEXT("component"), E.Component);
			C->SetStringField(TEXT("sectionBase"), E.SectionBase.ToString());
			C->SetNumberField(TEXT("staticMeshes"), E.StaticMeshes);
			C->SetNumberField(TEXT("mdcTotal"), E.MdcTotal);
			C->SetNumberField(TEXT("mdcBasePass"), E.MdcBasePass);
			C->SetNumberField(TEXT("mdcBasePassCached"), E.MdcBasePassCached);
			C->SetNumberField(TEXT("mdcDepthPass"), E.MdcDepthPass);
			TArray<TSharedPtr<FJsonValue>> RelArr;
			for (const FRel& R : E.Rels)
			{
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetNumberField(TEXT("lod"), R.LODIndex);
				J->SetNumberField(TEXT("screenSize"), R.ScreenSize);
				J->SetBoolField(TEXT("useForMaterial"), R.bUseForMaterial);
				J->SetBoolField(TEXT("rvt"), R.bRenderToVirtualTexture);
				RelArr.Add(MakeShared<FJsonValueObject>(J));
			}
			C->SetArrayField(TEXT("staticMeshRelevances"), RelArr);
			return C;
		};

		TArray<TSharedPtr<FJsonValue>> SampleArr;
		for (const FEntry& E : Entries)
		{
			if (SampleArr.Num() >= 8) break;
			if (E.StaticMeshes < 0) continue;
			TSharedRef<FJsonObject> C = DescribeEntry(E);
			C->SetStringField(TEXT("proxy"), E.Proxy);
			SampleArr.Add(MakeShared<FJsonValueObject>(C));
		}
		Out->SetArrayField(TEXT("sample"), SampleArr);

		// Proxies whose components disagree on how many base pass commands they got - same actor, same
		// terrain, different outcome. Compare on the count, not merely zero vs non-zero.
		TMap<FString, TSet<int32>> ProxyBasePassCounts;
		for (const FEntry& E : Entries)
		{
			if (E.StaticMeshes >= 0)
			{
				ProxyBasePassCounts.FindOrAdd(E.Proxy).Add(E.MdcBasePass);
			}
		}

		TArray<TSharedPtr<FJsonValue>> MixedArr;
		for (const TPair<FString, TSet<int32>>& Pair : ProxyBasePassCounts)
		{
			if (MixedArr.Num() >= 4 || Pair.Value.Num() < 2)
			{
				continue;
			}
			TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
			P->SetStringField(TEXT("proxy"), Pair.Key);
			TArray<TSharedPtr<FJsonValue>> Comps;
			for (const FEntry& E : Entries)
			{
				if (E.Proxy != Pair.Key || E.StaticMeshes < 0) continue;
				Comps.Add(MakeShared<FJsonValueObject>(DescribeEntry(E)));
			}
			P->SetArrayField(TEXT("components"), Comps);
			MixedArr.Add(MakeShared<FJsonValueObject>(P));
		}
		Out->SetArrayField(TEXT("mixedProxies"), MixedArr);

		TArray<TSharedPtr<FJsonValue>> ProxyArr;
		for (const TPair<FString, int32>& Pair : ProxyNoBasePass)
		{
			if (ProxyArr.Num() >= Limit) break;
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("proxy"), Pair.Key);
			J->SetNumberField(TEXT("componentsWithoutBasePass"), Pair.Value);
			J->SetNumberField(TEXT("componentsWithBasePass"), ProxyHasBasePass.FindRef(Pair.Key));
			ProxyArr.Add(MakeShared<FJsonValueObject>(J));
		}
		Out->SetArrayField(TEXT("proxies"), ProxyArr);
	}
}
