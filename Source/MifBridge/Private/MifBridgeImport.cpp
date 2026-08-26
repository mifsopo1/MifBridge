// MifBridge — SOURCE MEDIA INGEST. The bridge could author assets but never bring BYTES in.
//
//   import_texture       — mint (or refill) a UTexture2D from an image. TWO ingest modes:
//                          {sourcePath} a file on disk, and {base64} raw bytes posted inline.
//                          Both decode through IImageWrapper and write FTextureSource directly,
//                          so ONE code path produces the pixels and one verification proves them.
//   import_asset         — general source media (fbx, wav, psd, ...) via UAssetImportTask +
//                          IAssetTools::ImportAssetTasks, forced non-interactive.
//   reimport_asset       — re-pull an asset from its recorded source file(s).
//   set_texture_settings — CompressionSettings / SRGB / LODGroup / NeverStream / MipGenSettings /
//                          Filter. Without it import_texture is only half a solution: an icon
//                          imported with world-texture defaults is blurry, mip-chained and
//                          streamed, which reads to a human as "the import failed".
//
// WHY THIS FILE EXISTS (the live defect): 42 shop icon textures in the user's mod are 4.7 KB
// .uasset stubs — no .uexp, no .ubulk, no source PNG anywhere on disk. They have a UTexture2D
// object header and NOTHING ELSE, so the shop renders black. No path through the bridge could fix
// them, and reimport cannot: there is no source file to re-pull. base64 ingest is the route that
// closes it — an agent that GENERATED an icon holds the bytes and has no file to point at.
//
// Because of that defect this file is unusually loud about SIZE. Every response reports the source
// payload bytes, the built platform dimensions/format, and (when saved) the on-disk file size, so a
// caller can tell a real texture from another 4.7 KB stub without opening the editor. An endpoint
// that answered ok:true over an empty texture would be the exact bug it was written to kill.
//
// OVERWRITE MUTATES IN PLACE, IT DOES NOT REPLACE. The 42 stubs are already referenced by the shop
// widgets; delete-and-recreate would break every one of those references. With overwrite:true and a
// UTexture2D already at destPath, import_texture re-Inits the EXISTING object's FTextureSource.
// Same object, same path, same GUID, new pixels — the references survive.
//
// Transaction buckets (registered in MifBridgeCommon.cpp — see the registryLines report):
//   import_texture, import_asset — SELF-MANAGED: CreatePackage + NewObject/factory import +
//     FAssetRegistryModule::AssetCreated + MarkPackageDirty, exactly the create_material /
//     create_material_instance precedent (MifBridgeCommon.cpp IsSelfManagedEndpoint), plus a
//     texture/mesh DDC build. Asset-lifecycle work must never ride RunEndpoint's blanket
//     transaction, and MarkPackageDirty inside one records an FPackageRecord for a package that has
//     never existed on disk.
//   reimport_asset — SELF-MANAGED: FReimportManager replaces an asset's entire payload and runs
//     factory code MifBridge did not write, which may open its own FScopedTransaction. Same hazard
//     class as invoke_editor_command.
//   set_texture_settings — SELF-MANAGED: UTexture::PostEditChange tears down and rebuilds the
//     texture resource and runs an FMaterialUpdateContext over every dependent material
//     (Texture.cpp:783-818). Shader/resource teardown captured by an undo step is the crash family
//     recompile_material is self-managed for.
//
// SYNCHRONOUS, SINGLE TICK, NO MODALS — the three hard constraints of this server:
//   * Every path here completes inside one FHttpServerModule tick. Nothing is deferred, so no job
//     slot / status endpoint is needed (contrast the kr_* one-slot pattern). Large imports make ONE
//     long frame, which is legal; work that SPANS frames is not.
//   * bAsync is forced FALSE on every UAssetImportTask. UAssetImportTask::GetObjects() is
//     documented to BLOCK until async results are ready (AssetImportTask.h:78) — calling it after an
//     async import is exactly the cross-frame stall the rule forbids.
//   * bAutomated is forced TRUE. UAssetToolsImpl::ImportAssetsInternal wraps the whole import in
//     TGuardValue<bool>(GIsRunningUnattendedScript, GIsRunningUnattendedScript || Params.bAutomated)
//     (AssetTools.cpp:3045), which is what genuinely suppresses factory option dialogs. This file
//     ALSO sets that guard itself around reimport, which does not go through AssetTools.
//   * Task->Factory is ALWAYS set explicitly. Interchange is bypassed only when a factory is
//     specified — `IsInterchangeImportEnabled() && (SpecifiedFactory == nullptr)`
//     (AssetTools.cpp:3068-3071). Leave Factory null and a PNG or FBX can route to ASYNC
//     Interchange, which would span frames. This is the single most load-bearing line in
//     import_asset.
//   * Specifying the factory also skips UAssetToolsImpl's `NewFactory->ConfigureProperties()` call
//     (AssetTools.cpp:3140) — ConfigureProperties is where a factory is allowed to raise UI.
//
// MODULE DEPENDENCY REQUIRED — NOT ADDED HERE (MifBridge.Build.cs is not this agent's file):
//   "ImageWrapper"  — IImageWrapperModule / IImageWrapper. It is NOT reachable today: Engine lists
//     it under PrivateIncludePathModuleNames (Engine.Build.cs:44) and UnrealEd under its PRIVATE
//     list (UnrealEd.Build.cs:118), and neither is transitive. Without it IImageWrapperModule.h does
//     not resolve and this file does not compile.
//   "AssetTools" is ALREADY a dependency (MifBridge.Build.cs:50, added for headless
//     rename/duplicate) — no change needed for ImportAssetTasks.
//   No AudioEditor dependency is needed even though wav import works: the factory is resolved by
//   REFLECTION over loaded UFactory CDOs (the same sweep UAssetToolsImpl does at
//   AssetTools.cpp:3095-3147), never by naming USoundFactory in C++. That deliberately diverges
//   from docs/audit/work/B_assets_registry.md's per-format allowlist, which would have cost a new
//   module dependency for wav alone and would have silently excluded every format the allowlist did
//   not anticipate.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetImportTask.h"                     // UAssetImportTask (UnrealEd), fields :36-:93
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetToolsModule.h"                    // FAssetToolsModule::GetModule().Get()
#include "CoreGlobals.h"                         // GIsRunningUnattendedScript
#include "EditorFramework/AssetImportData.h"     // UAssetImportData::ExtractFilenames / Update
#include "EditorReimportHandler.h"               // FReimportManager (UnrealEd)
#include "Engine/StreamableRenderAsset.h"        // NeverStream lives HERE, not on UTexture (:299)
#include "Engine/Texture.h"                      // FTextureSource, UTexture settings
#include "Engine/Texture2D.h"
#include "Engine/TextureDefines.h"               // TextureCompressionSettings / TextureGroup / ...
#include "Factories/Factory.h"                   // UFactory::GetSupportedFileExtensions (UNREALED_API)
#include "Factories/SceneImportFactory.h"        // excluded from the factory sweep, as AssetTools does
#include "HAL/FileManager.h"                     // IFileManager::FileSize — the anti-stub number
#include "IAssetTools.h"
#include "IImageWrapper.h"                       // NEW MODULE DEP: ImageWrapper
#include "IImageWrapperModule.h"
#include "Misc/Base64.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/PackagePath.h"
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "Modules/ModuleManager.h"
#include "PixelFormat.h"                         // GetPixelFormatString (CORE_API, PixelFormat.h:467)
#include "Templates/UnrealTemplate.h"            // TGuardValue (GIsRunningUnattendedScript)
#include "TextureCompiler.h"                     // FTextureCompilingManager::FinishCompilation
#include "UObject/Package.h"
#include "UObject/ObjectRedirector.h"
#include "UObject/SavePackage.h"                 // FSavePackageArgs
#include "UObject/UObjectGlobals.h"
#include "UObject/UObjectIterator.h"

namespace MifBridge
{
	namespace
	{
		// ============================================================================
		// NAMING: every free helper in this file carries the MifImport prefix.
		// The module is a UNITY build: a free function name duplicated across two .cpp
		// is C2084 even with internal linkage. MifBridgeCooked.cpp already owns a
		// `static bool IsContainerOnlyPackage(FName)` (MifBridgeCooked.cpp:44) — the
		// prefixed twin below is a deliberate copy, not an oversight: promoting it would
		// mean editing MifBridgeHandlers.h, which a later integrator owns.
		// ============================================================================

		// True when the package EXISTS and its only copy is inside a mounted IoStore container.
		//
		// The existence test is load-bearing, and its absence was a shipped bug. The original was a
		// verbatim twin of MifBridgeCooked.cpp:44-49, which asks only "is there no loose file?" — and a
		// package that does not exist AT ALL trivially has no loose file, so it answered TRUE for every
		// brand-new path. In a cooked-editor modkit project the whole of /Game/ is mounted from a .pak,
		// so import_texture refused EVERY new destPath under /Game/ with "resolves to a container-only
		// package". The endpoint could not create a single texture in the project it ships with.
		//
		// Caught by running it, not by reading it: the name says "IsContainerOnly" and the body says
		// "HasNoLooseFile", and those differ on exactly one input — the absent package, which is also
		// the only input a create path ever passes.
		//
		// NOTE: MifBridgeCooked.cpp:44 still carries the original. It is correct THERE because it is
		// only ever asked about packages already known to exist; the same helper on a create path would
		// be wrong. Do not "unify" them without re-reading both call sites.
		bool MifImportIsContainerOnlyPackage(FName PackageName)
		{
			const FPackagePath Path = FPackagePath::FromPackageNameUnchecked(PackageName);
			const bool bExistsAnywhere = FPackageName::DoesPackageExistEx(
				Path, FPackageName::EPackageLocationFilter::Any) != FPackageName::EPackageLocationFilter::None;
			if (!bExistsAnywhere)
			{
				return false; // absent is not container-only — it is free real estate
			}
			return FPackageName::DoesPackageExistEx(
				Path, FPackageName::EPackageLocationFilter::FileSystem) == FPackageName::EPackageLocationFilter::None;
		}

		// --- destination path validation -------------------------------------------------
		// Splits /Game/Dir/Asset into its package path and asset name and proves both are legal
		// BEFORE anything is created, so a bad path never leaves a half-made package behind.
		bool MifImportValidateAssetPath(const FString& InPath, const TSharedRef<FJsonObject>& Out,
			FString& OutPackagePath, FString& OutAssetName)
		{
			FString P = InPath;
			P.TrimStartAndEndInline();
			if (P.IsEmpty())
			{
				Fail(Out, TEXT("destPath is required (a /Game/... asset path, e.g. /Game/Mod/Icons/T_Apple)"));
				return false;
			}
			// Accept the object-path spelling /Game/A/T_Foo.T_Foo as well as the bare package path,
			// mirroring the resolver rule the rest of the plugin uses.
			int32 DotIndex = INDEX_NONE;
			if (P.FindChar(TEXT('.'), DotIndex))
			{
				P = P.Left(DotIndex);
			}
			P.RemoveFromEnd(TEXT("/"));
			if (!P.StartsWith(TEXT("/Game/")))
			{
				Fail(Out, FString::Printf(
					TEXT("destPath must start with /Game/ (got '%s'). Mod content lives under /Game; ")
					TEXT("engine and plugin roots are not writable targets for import."), *InPath));
				return false;
			}
			if (!FPackageName::IsValidLongPackageName(P))
			{
				Fail(Out, FString::Printf(
					TEXT("destPath '%s' is not a valid long package name (no spaces, no trailing slash, ")
					TEXT("no double slashes, must name the ASSET not just the folder)"), *InPath));
				return false;
			}
			OutPackagePath = P;
			OutAssetName = FPackageName::GetLongPackageAssetName(P);
			FText NameReason;
			// FName::IsValidObjectName takes an FText& and fills in WHY (NameTypes.h:788). Surfacing
			// that text is the difference between "invalid name" and an actionable error.
			if (OutAssetName.IsEmpty() || !FName(*OutAssetName).IsValidObjectName(NameReason))
			{
				Fail(Out, FString::Printf(
					TEXT("destPath '%s' does not end in a valid asset name%s"), *InPath,
					NameReason.IsEmpty() ? TEXT("") : *FString::Printf(TEXT(" — %s"), *NameReason.ToString())));
				return false;
			}
			return true;
		}

		// --- enum parsing by reflection ---------------------------------------------------
		// Texture settings are namespace-scope UENUMs (TextureDefines.h:25/:110/:344/:437), so
		// StaticEnum<T>() reaches them. Parsing by reflection instead of a hand-written table means
		// the accepted set can never drift from the engine's, and an unknown value gets the SAME
		// near-miss treatment the rest of the plugin gives unknown names.
		bool MifImportParseEnum(UEnum* Enum, const TCHAR* Prefix, const FString& InText,
			const TCHAR* FieldName, int64& OutValue, FString& OutError)
		{
			if (!Enum)
			{
				OutError = FString::Printf(TEXT("%s: reflected enum unavailable"), FieldName);
				return false;
			}
			FString Text = InText;
			Text.TrimStartAndEndInline();
			if (Text.IsEmpty())
			{
				OutError = FString::Printf(TEXT("%s was supplied but empty"), FieldName);
				return false;
			}

			TArray<FString> Accepted;
			const int32 NumEnums = Enum->NumEnums();
			for (int32 i = 0; i < NumEnums; ++i)
			{
				const FString Full = Enum->GetNameStringByIndex(i);
				if (Full.IsEmpty() || Full.EndsWith(TEXT("_MAX")) || Full.EndsWith(TEXT("MAX")))
				{
					continue;
				}
				FString Short = Full;
				Short.RemoveFromStart(Prefix, ESearchCase::IgnoreCase);
				Accepted.Add(Short);
				if (Text.Equals(Full, ESearchCase::IgnoreCase) || Text.Equals(Short, ESearchCase::IgnoreCase))
				{
					OutValue = Enum->GetValueByIndex(i);
					return true;
				}
			}

			// SECOND PASS: DISPLAY names. The authored name is what the reflection system stores
			// (TC_EditorIcon -> "EditorIcon"), but the DETAILS PANEL shows the display name
			// ("UserInterface2D (RGBA)"), and that is what a person reading the editor UI will type.
			// This endpoint's own help text recommends compressionSettings:UserInterface2D for icon
			// content in four places, and the parser refused it - so following the endpoint's advice
			// produced an error. write_thumbnail_texture had already solved this for itself with a
			// hand-written table carrying the alias, which is exactly the kind of second
			// implementation that drifts.
			//
			// Run only AFTER the authored-name pass, so an authored name can never be shadowed by
			// some other entry's display name, and nothing that works today changes.
			//
			// Normalised because a display name is written for humans: spaces are dropped and a
			// trailing parenthetical is cut, so "UserInterface2D (RGBA)" matches "UserInterface2D"
			// and "User Interface 2D".
			auto Normalise = [](const FString& Raw)
			{
				FString N = Raw;
				int32 Paren = INDEX_NONE;
				if (N.FindChar(TEXT('('), Paren)) { N.LeftInline(Paren); }
				N.ReplaceInline(TEXT(" "), TEXT(""));
				N.TrimStartAndEndInline();
				return N;
			};
			const FString WantedNorm = Normalise(Text);
			if (!WantedNorm.IsEmpty())
			{
				for (int32 i = 0; i < NumEnums; ++i)
				{
					const FString Full = Enum->GetNameStringByIndex(i);
					if (Full.IsEmpty() || Full.EndsWith(TEXT("_MAX")) || Full.EndsWith(TEXT("MAX")))
					{
						continue;
					}
					const FString Display = Normalise(Enum->GetDisplayNameTextByIndex(i).ToString());
					if (!Display.IsEmpty() && WantedNorm.Equals(Display, ESearchCase::IgnoreCase))
					{
						OutValue = Enum->GetValueByIndex(i);
						return true;
					}
				}
			}

			const FString Near = NearMissSuggestion(Accepted, Text, 5);
			OutError = FString::Printf(TEXT("unknown %s '%s'%s — accepted (the %s prefix is optional, and the ")
				TEXT("name shown in the Details panel is accepted too): %s"),
				FieldName, *Text,
				Near.IsEmpty() ? TEXT("") : *FString::Printf(TEXT(" (did you mean %s?)"), *Near),
				Prefix, *FString::Join(Accepted, TEXT(", ")));
			return false;
		}

		// --- texture setting application + verification ------------------------------------
		// Records what the caller ASKED for so the verify pass can prove each field stuck.
		struct FMifImportTextureSettings
		{
			bool bHasCompression = false;   TextureCompressionSettings Compression = TC_Default;
			bool bHasSRGB = false;          bool bSRGB = true;
			bool bHasLODGroup = false;      TextureGroup LODGroup = TEXTUREGROUP_World;
			bool bHasNeverStream = false;   bool bNeverStream = false;
			bool bHasMipGen = false;        TextureMipGenSettings MipGen = TMGS_FromTextureGroup;
			bool bHasFilter = false;        TextureFilter Filter = TF_Default;

			bool Any() const
			{
				return bHasCompression || bHasSRGB || bHasLODGroup || bHasNeverStream || bHasMipGen || bHasFilter;
			}
		};

		// Reads the six setting fields off the request. Returns false (and fails Out) on the first
		// unparseable value, BEFORE anything has been written — a rejected settings block must leave
		// the texture exactly as it was.
		bool MifImportReadTextureSettings(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
			FMifImportTextureSettings& S)
		{
			FString Error;
			int64 Value = 0;

			const FString CompText = JStrAny(In, { TEXT("compressionSettings"), TEXT("compression") });
			if (!CompText.IsEmpty())
			{
				if (!MifImportParseEnum(StaticEnum<TextureCompressionSettings>(), TEXT("TC_"), CompText,
					TEXT("compressionSettings"), Value, Error))
				{
					Fail(Out, Error);
					return false;
				}
				S.bHasCompression = true;
				S.Compression = static_cast<TextureCompressionSettings>(Value);
			}

			const FString GroupText = JStrAny(In, { TEXT("lodGroup"), TEXT("textureGroup") });
			if (!GroupText.IsEmpty())
			{
				if (!MifImportParseEnum(StaticEnum<TextureGroup>(), TEXT("TEXTUREGROUP_"), GroupText,
					TEXT("lodGroup"), Value, Error))
				{
					Fail(Out, Error);
					return false;
				}
				S.bHasLODGroup = true;
				S.LODGroup = static_cast<TextureGroup>(Value);
			}

			const FString MipText = JStrAny(In, { TEXT("mipGenSettings"), TEXT("mipGen") });
			if (!MipText.IsEmpty())
			{
				if (!MifImportParseEnum(StaticEnum<TextureMipGenSettings>(), TEXT("TMGS_"), MipText,
					TEXT("mipGenSettings"), Value, Error))
				{
					Fail(Out, Error);
					return false;
				}
				S.bHasMipGen = true;
				S.MipGen = static_cast<TextureMipGenSettings>(Value);
			}

			const FString FilterText = JStr(In, TEXT("filter"));
			if (!FilterText.IsEmpty())
			{
				if (!MifImportParseEnum(StaticEnum<TextureFilter>(), TEXT("TF_"), FilterText,
					TEXT("filter"), Value, Error))
				{
					Fail(Out, Error);
					return false;
				}
				S.bHasFilter = true;
				S.Filter = static_cast<TextureFilter>(Value);
			}

			// JHasAny distinguishes "caller explicitly passed false" from "caller omitted the field".
			// Without it, srgb:false would be indistinguishable from srgb absent, and the verify pass
			// would silently skip the one field the caller cared about.
			if (JHasAny(In, { TEXT("srgb"), TEXT("sRGB") }))
			{
				S.bHasSRGB = true;
				S.bSRGB = JBoolAny(In, { TEXT("srgb"), TEXT("sRGB") }, true);
			}
			if (JHasAny(In, { TEXT("neverStream") }))
			{
				S.bHasNeverStream = true;
				S.bNeverStream = JBool(In, TEXT("neverStream"), false);
			}
			return true;
		}

		// Writes the requested fields. NOTHING is validated or rebuilt here — the caller runs one
		// PostEditChange afterwards, then MifImportVerifyTextureSettings proves the read-back.
		void MifImportApplyTextureSettings(UTexture2D* Texture, const FMifImportTextureSettings& S)
		{
			if (S.bHasCompression)  { Texture->CompressionSettings = S.Compression; }
			if (S.bHasSRGB)         { Texture->SRGB = S.bSRGB ? 1 : 0; }
			if (S.bHasLODGroup)     { Texture->LODGroup = S.LODGroup; }
			if (S.bHasNeverStream)  { Texture->NeverStream = S.bNeverStream ? 1 : 0; }
			if (S.bHasFilter)       { Texture->Filter = S.Filter; }
#if WITH_EDITORONLY_DATA
			if (S.bHasMipGen)       { Texture->MipGenSettings = S.MipGen; }
#endif
		}

		FString MifImportEnumText(UEnum* Enum, const TCHAR* Prefix, int64 Value)
		{
			if (!Enum) { return FString::Printf(TEXT("%lld"), (long long)Value); }
			FString Name = Enum->GetNameStringByValue(Value);
			if (Name.IsEmpty()) { return FString::Printf(TEXT("%lld"), (long long)Value); }
			Name.RemoveFromStart(Prefix, ESearchCase::IgnoreCase);
			return Name;
		}

		// VERIFY-AFTER-WRITE for the settings block. UTexture::PostEditChange runs
		// ValidateSettingsAfterImportOrEdit (Texture.cpp:741), which is ALLOWED to overrule what was
		// written — e.g. SRGB is forced off for float source formats. A coerced field is work the
		// caller asked for and did not get, so it is an ERROR, not a warning: reporting ok:true over
		// it is precisely the defect class this plugin exists to kill. The response still carries the
		// applied values of every field, so the caller can see exactly what the engine settled on.
		bool MifImportVerifyTextureSettings(UTexture2D* Texture, const FMifImportTextureSettings& S,
			const TSharedRef<FJsonObject>& Out)
		{
			TArray<FString> Mismatches;

			if (S.bHasCompression && Texture->CompressionSettings != S.Compression)
			{
				Mismatches.Add(FString::Printf(TEXT("compressionSettings requested %s, engine applied %s"),
					*MifImportEnumText(StaticEnum<TextureCompressionSettings>(), TEXT("TC_"), (int64)S.Compression),
					*MifImportEnumText(StaticEnum<TextureCompressionSettings>(), TEXT("TC_"), (int64)Texture->CompressionSettings.GetValue())));
			}
			if (S.bHasSRGB && (Texture->SRGB != 0) != S.bSRGB)
			{
				Mismatches.Add(FString::Printf(TEXT("srgb requested %s, engine applied %s"),
					S.bSRGB ? TEXT("true") : TEXT("false"), Texture->SRGB ? TEXT("true") : TEXT("false")));
			}
			if (S.bHasLODGroup && Texture->LODGroup != S.LODGroup)
			{
				Mismatches.Add(FString::Printf(TEXT("lodGroup requested %s, engine applied %s"),
					*MifImportEnumText(StaticEnum<TextureGroup>(), TEXT("TEXTUREGROUP_"), (int64)S.LODGroup),
					*MifImportEnumText(StaticEnum<TextureGroup>(), TEXT("TEXTUREGROUP_"), (int64)Texture->LODGroup.GetValue())));
			}
			if (S.bHasNeverStream && (Texture->NeverStream != 0) != S.bNeverStream)
			{
				Mismatches.Add(FString::Printf(TEXT("neverStream requested %s, engine applied %s"),
					S.bNeverStream ? TEXT("true") : TEXT("false"), Texture->NeverStream ? TEXT("true") : TEXT("false")));
			}
			if (S.bHasFilter && Texture->Filter != S.Filter)
			{
				Mismatches.Add(FString::Printf(TEXT("filter requested %s, engine applied %s"),
					*MifImportEnumText(StaticEnum<TextureFilter>(), TEXT("TF_"), (int64)S.Filter),
					*MifImportEnumText(StaticEnum<TextureFilter>(), TEXT("TF_"), (int64)Texture->Filter.GetValue())));
			}
#if WITH_EDITORONLY_DATA
			if (S.bHasMipGen && Texture->MipGenSettings != S.MipGen)
			{
				Mismatches.Add(FString::Printf(TEXT("mipGenSettings requested %s, engine applied %s"),
					*MifImportEnumText(StaticEnum<TextureMipGenSettings>(), TEXT("TMGS_"), (int64)S.MipGen),
					*MifImportEnumText(StaticEnum<TextureMipGenSettings>(), TEXT("TMGS_"), (int64)Texture->MipGenSettings.GetValue())));
			}
#endif

			if (Mismatches.Num() > 0)
			{
				Fail(Out, FString::Printf(
					TEXT("%d requested setting(s) did not stick — UTexture::PostEditChange runs ")
					TEXT("ValidateSettingsAfterImportOrEdit (Texture.cpp:741) which overrules values that are ")
					TEXT("invalid for this texture's source format (e.g. SRGB is forced off for float sources, ")
					TEXT("and compression that cannot represent the source is downgraded). The values below are ")
					TEXT("what the asset now HOLDS — the write was not rolled back. Mismatches: %s"),
					Mismatches.Num(), *FString::Join(Mismatches, TEXT("; "))));
				return false;
			}
			return true;
		}

		// --- fact emission -----------------------------------------------------------------
		// The anti-stub report. Everything a caller needs to distinguish a real texture from a
		// 4.7 KB header-only stub, without opening the editor.
		void MifImportEmitTextureFacts(UTexture2D* Texture, const TSharedRef<FJsonObject>& Out)
		{
#if WITH_EDITORONLY_DATA
			UEnum* SourceFormatEnum = StaticEnum<ETextureSourceFormat>();
			const FTextureSource& Source = Texture->Source;
			const bool bSourceValid = Source.IsValid();
			Out->SetBoolField(TEXT("sourceValid"), bSourceValid);
			Out->SetNumberField(TEXT("sourceWidth"), (double)Source.GetSizeX());
			Out->SetNumberField(TEXT("sourceHeight"), (double)Source.GetSizeY());
			Out->SetNumberField(TEXT("sourceNumMips"), Source.GetNumMips());
			Out->SetStringField(TEXT("sourceFormat"),
				MifImportEnumText(SourceFormatEnum, TEXT("TSF_"), (int64)Source.GetFormat()));
			// BulkData payload size. THE number that separates a real import from a stub: a
			// header-only 4.7 KB .uasset reports 0 here.
			Out->SetNumberField(TEXT("sourceDataBytes"), (double)Source.GetSizeOnDisk());
#endif

			// Built platform data. Valid only after the texture build has finished — every caller
			// below runs FTextureCompilingManager::FinishCompilation first, so these are never the
			// "still compiling" zeroes that would read as a failed import.
			Out->SetNumberField(TEXT("sizeX"), Texture->GetSizeX());
			Out->SetNumberField(TEXT("sizeY"), Texture->GetSizeY());
			Out->SetNumberField(TEXT("numMips"), Texture->GetNumMips());
			Out->SetStringField(TEXT("pixelFormat"), GetPixelFormatString(Texture->GetPixelFormat(0)));

			Out->SetStringField(TEXT("compressionSettings"),
				MifImportEnumText(StaticEnum<TextureCompressionSettings>(), TEXT("TC_"), (int64)Texture->CompressionSettings.GetValue()));
			Out->SetBoolField(TEXT("srgb"), Texture->SRGB != 0);
			Out->SetStringField(TEXT("lodGroup"),
				MifImportEnumText(StaticEnum<TextureGroup>(), TEXT("TEXTUREGROUP_"), (int64)Texture->LODGroup.GetValue()));
			Out->SetBoolField(TEXT("neverStream"), Texture->NeverStream != 0);
			Out->SetStringField(TEXT("filter"),
				MifImportEnumText(StaticEnum<TextureFilter>(), TEXT("TF_"), (int64)Texture->Filter.GetValue()));
#if WITH_EDITORONLY_DATA
			Out->SetStringField(TEXT("mipGenSettings"),
				MifImportEnumText(StaticEnum<TextureMipGenSettings>(), TEXT("TMGS_"), (int64)Texture->MipGenSettings.GetValue()));
#endif
		}

		// Forces the async texture build to completion so the numbers above are real and so a save
		// writes actual bulk data. Texture compilation is async by default in 5.3
		// (FTextureCompilingManager, TextureCompiler.h:41); saving mid-build is one of the ways a
		// .uasset ends up on disk WITHOUT its .ubulk. This blocks, inside one tick, deliberately.
		void MifImportFinishTextureBuild(UTexture* Texture)
		{
			if (Texture)
			{
				TArray<UTexture*> One;
				One.Add(Texture);
				FTextureCompilingManager::Get().FinishCompilation(One);
			}
		}

		// --- saving ------------------------------------------------------------------------
		// Optional, but ON by default for import_texture — deliberately unlike create_material,
		// which leaves the package dirty. The defect this file addresses is a set of BAD FILES ON
		// DISK; an in-memory-only success would leave the 4.7 KB stub exactly where it was and still
		// answer ok:true. fileSizeBytes is reported so the caller can see the difference.
		void MifImportSavePackage(UPackage* Package, const TSharedRef<FJsonObject>& Out)
		{
			if (!Package) { return; }
			const FString FileName = FPackageName::LongPackageNameToFilename(
				Package->GetName(),
				Package->ContainsMap() ? FPackageName::GetMapPackageExtension() : FPackageName::GetAssetPackageExtension());

			FSavePackageArgs SaveArgs;
			SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
			SaveArgs.SaveFlags = SAVE_NoError;

			if (UPackage::SavePackage(Package, nullptr, *FileName, SaveArgs))
			{
				Out->SetStringField(TEXT("savedTo"), FileName);
				Out->SetNumberField(TEXT("fileSizeBytes"), (double)IFileManager::Get().FileSize(*FileName));
			}
			else
			{
				AddWarning(Out, FString::Printf(
					TEXT("asset was written in memory but SavePackage failed for %s — it is dirty and unsaved; ")
					TEXT("retry with save_package, and check the file is not read-only or checked out elsewhere"),
					*Package->GetName()));
			}
		}

		// --- image decode -------------------------------------------------------------------
		// One decode for BOTH ingest modes. Result is raw pixels in an FTextureSource-compatible
		// layout, plus the facts worth echoing back.
		struct FMifImportDecodedImage
		{
			TArray64<uint8> Raw;
			int32 Width = 0;
			int32 Height = 0;
			int32 SourceBitDepth = 0;
			ETextureSourceFormat TextureFormat = TSF_BGRA8;
			FString ImageFormatName;
		};

		const TCHAR* MifImportImageFormatName(EImageFormat Format)
		{
			switch (Format)
			{
			case EImageFormat::PNG:           return TEXT("PNG");
			case EImageFormat::JPEG:          return TEXT("JPEG");
			case EImageFormat::GrayscaleJPEG: return TEXT("GrayscaleJPEG");
			case EImageFormat::BMP:           return TEXT("BMP");
			case EImageFormat::TGA:           return TEXT("TGA");
			case EImageFormat::ICO:           return TEXT("ICO");
			case EImageFormat::EXR:           return TEXT("EXR");
			case EImageFormat::ICNS:          return TEXT("ICNS");
			case EImageFormat::HDR:           return TEXT("HDR");
			case EImageFormat::TIFF:          return TEXT("TIFF");
			case EImageFormat::DDS:           return TEXT("DDS");
			default:                          return TEXT("Invalid");
			}
		}

		bool MifImportDecodeImage(const TArray<uint8>& Compressed, const FString& FormatOverride,
			FMifImportDecodedImage& OutImage, FString& OutError)
		{
			if (Compressed.Num() == 0)
			{
				OutError = TEXT("image data is empty (0 bytes) — nothing to decode");
				return false;
			}

			IImageWrapperModule& Module = FModuleManager::LoadModuleChecked<IImageWrapperModule>(FName("ImageWrapper"));

			EImageFormat Format = EImageFormat::Invalid;
			if (!FormatOverride.IsEmpty())
			{
				// Accept "png", ".png", "PNG". GetImageFormatFromExtension is the engine's own mapping.
				FString Ext = FormatOverride;
				Ext.TrimStartAndEndInline();
				Ext.RemoveFromStart(TEXT("."));
				Format = Module.GetImageFormatFromExtension(*Ext);
				if (Format == EImageFormat::Invalid)
				{
					OutError = FString::Printf(
						TEXT("unknown format '%s' — accepted: png, jpg, jpeg, bmp, tga (omit `format` to auto-detect from the bytes)"),
						*FormatOverride);
					return false;
				}
			}
			else
			{
				Format = Module.DetectImageFormat(Compressed.GetData(), Compressed.Num());
				if (Format == EImageFormat::Invalid)
				{
					OutError = FString::Printf(
						TEXT("could not identify the image format from the first bytes of a %d-byte payload. ")
						TEXT("Supported by import_texture: PNG, JPEG, BMP, TGA. If the bytes came through base64, ")
						TEXT("check the string was not truncated or double-encoded; pass `format` to force a decoder."),
						Compressed.Num());
					return false;
				}
			}

			// DELIBERATE REFUSAL of the HDR/container formats. They decode to float or block-compressed
			// data that TSF_BGRA8/TSF_RGBA16 cannot represent, and guessing a conversion here would
			// produce a plausible-looking WRONG texture. import_asset routes them through
			// UTextureFactory, which handles them properly.
			if (Format != EImageFormat::PNG && Format != EImageFormat::JPEG
				&& Format != EImageFormat::GrayscaleJPEG && Format != EImageFormat::BMP
				&& Format != EImageFormat::TGA)
			{
				OutError = FString::Printf(
					TEXT("%s is not handled by import_texture (it decodes to float/HDR or block-compressed data ")
					TEXT("that this endpoint's 8/16-bit integer source path would silently misrepresent). ")
					TEXT("Use import_asset {file, destination} instead — it routes through UTextureFactory, ")
					TEXT("which imports %s correctly. import_texture handles PNG, JPEG, BMP and TGA."),
					MifImportImageFormatName(Format), MifImportImageFormatName(Format));
				return false;
			}

			TSharedPtr<IImageWrapper> Wrapper = Module.CreateImageWrapper(Format);
			if (!Wrapper.IsValid())
			{
				OutError = FString::Printf(TEXT("no image wrapper available for %s"), MifImportImageFormatName(Format));
				return false;
			}
			if (!Wrapper->SetCompressed(Compressed.GetData(), Compressed.Num()))
			{
				OutError = FString::Printf(
					TEXT("%s payload rejected by the decoder (%d bytes) — the header parsed as %s but the data is ")
					TEXT("malformed or truncated"),
					MifImportImageFormatName(Format), Compressed.Num(), MifImportImageFormatName(Format));
				return false;
			}

			const int64 Width64 = Wrapper->GetWidth();
			const int64 Height64 = Wrapper->GetHeight();
			const int32 BitDepth = Wrapper->GetBitDepth();
			if (Width64 <= 0 || Height64 <= 0)
			{
				OutError = FString::Printf(TEXT("decoded image has non-positive dimensions (%lldx%lld)"),
					(long long)Width64, (long long)Height64);
				return false;
			}
			// UE's own source-texture ceiling. Beyond it Source.Init would produce an asset the
			// texture build refuses, which surfaces later as an unexplained black texture.
			if (Width64 > 16384 || Height64 > 16384)
			{
				OutError = FString::Printf(
					TEXT("image is %lldx%lld — larger than the 16384 maximum texture source dimension. ")
					TEXT("Downscale before importing."), (long long)Width64, (long long)Height64);
				return false;
			}

			// BIT DEPTH MUST NOT BE COERCED. FPngImageWrapper::UncompressPNGData sizes its output
			// buffer from the REQUESTED bit depth ((InBitDepth * PixelChannels) / 8,
			// PngImageWrapper.cpp:428) but applies no PNG_TRANSFORM_STRIP_16 — asking a 16-bit PNG
			// for 8-bit data writes 16-bit pixels into a half-size buffer. Request what the file
			// actually holds, and pick the matching FTextureSource format.
			ERGBFormat RequestFormat = ERGBFormat::BGRA;
			int32 RequestBitDepth = 8;
			int64 BytesPerPixel = 4;
			OutImage.TextureFormat = TSF_BGRA8;
			if (BitDepth == 16)
			{
				RequestFormat = ERGBFormat::RGBA;   // TSF_RGBA16 is R,G,B,A order
				RequestBitDepth = 16;
				BytesPerPixel = 8;
				OutImage.TextureFormat = TSF_RGBA16;
			}

			if (!Wrapper->GetRaw(RequestFormat, RequestBitDepth, OutImage.Raw))
			{
				OutError = FString::Printf(
					TEXT("%s decode failed at %lldx%lld %d-bit (the decoder reported no raw data)"),
					MifImportImageFormatName(Format), (long long)Width64, (long long)Height64, BitDepth);
				return false;
			}

			// Belt-and-braces against the buffer-size hazard above: if the decoder ever hands back a
			// buffer that does not match the geometry, refuse rather than hand a short buffer to
			// FTextureSource::Init, which would read past its end.
			const int64 Expected = Width64 * Height64 * BytesPerPixel;
			if (OutImage.Raw.Num() != Expected)
			{
				OutError = FString::Printf(
					TEXT("decoder returned %lld bytes for a %lldx%lld %d-bit image, expected %lld — refusing to ")
					TEXT("initialise texture source from a mismatched buffer"),
					(long long)OutImage.Raw.Num(), (long long)Width64, (long long)Height64,
					RequestBitDepth, (long long)Expected);
				return false;
			}

			OutImage.Width = (int32)Width64;
			OutImage.Height = (int32)Height64;
			OutImage.SourceBitDepth = BitDepth;
			OutImage.ImageFormatName = MifImportImageFormatName(Format);
			return true;
		}

		// --- factory resolution for import_asset --------------------------------------------
		// Mirrors UAssetToolsImpl::ImportAssetsInternal's sweep (AssetTools.cpp:3095-3147): every
		// concrete non-scene UFactory CDO with bEditorImport, matched on file extension, highest
		// ImportPriority wins. Doing it here rather than letting AssetTools do it is what lets us
		// SET Task->Factory, which is the only thing that keeps Interchange (and its async path) out
		// of the picture — AssetTools.cpp:3068-3071.
		UClass* MifImportFindFactoryClassForExtension(const FString& Extension, TArray<FString>& OutCandidates)
		{
			UClass* Best = nullptr;
			int32 BestPriority = MIN_int32;
			for (TObjectIterator<UClass> ClassIt; ClassIt; ++ClassIt)
			{
				UClass* Candidate = *ClassIt;
				if (!Candidate->IsChildOf(UFactory::StaticClass())
					|| Candidate->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated | CLASS_NewerVersionExists)
					|| Candidate->IsChildOf(USceneImportFactory::StaticClass()))
				{
					continue;
				}
				UFactory* CDO = Cast<UFactory>(Candidate->GetDefaultObject());
				if (!CDO || !CDO->bEditorImport)
				{
					continue;
				}
				TArray<FString> Extensions;
				CDO->GetSupportedFileExtensions(Extensions);
				bool bMatches = false;
				for (const FString& Ext : Extensions)
				{
					if (Ext.Equals(Extension, ESearchCase::IgnoreCase)) { bMatches = true; break; }
				}
				if (!bMatches)
				{
					continue;
				}
				OutCandidates.Add(Candidate->GetName());
				if (CDO->ImportPriority > BestPriority)
				{
					BestPriority = CDO->ImportPriority;
					Best = Candidate;
				}
			}
			return Best;
		}

		// Every extension any loaded factory can import — used to make "unsupported file" actionable
		// instead of a dead end.
		FString MifImportSupportedExtensionList()
		{
			TSet<FString> All;
			for (TObjectIterator<UClass> ClassIt; ClassIt; ++ClassIt)
			{
				UClass* Candidate = *ClassIt;
				if (!Candidate->IsChildOf(UFactory::StaticClass())
					|| Candidate->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated | CLASS_NewerVersionExists)
					|| Candidate->IsChildOf(USceneImportFactory::StaticClass()))
				{
					continue;
				}
				UFactory* CDO = Cast<UFactory>(Candidate->GetDefaultObject());
				if (!CDO || !CDO->bEditorImport) { continue; }
				TArray<FString> Extensions;
				CDO->GetSupportedFileExtensions(Extensions);
				for (const FString& Ext : Extensions) { All.Add(Ext.ToLower()); }
			}
			TArray<FString> Sorted = All.Array();
			Sorted.Sort();
			if (Sorted.Num() > 60) { Sorted.SetNum(60); }
			return FString::Join(Sorted, TEXT(", "));
		}

		// --- shared source-file resolution --------------------------------------------------
		bool MifImportResolveSourceFile(const FString& InPath, FString& OutAbsolute, FString& OutError)
		{
			FString P = InPath;
			P.TrimStartAndEndInline();
			P.ReplaceInline(TEXT("\\"), TEXT("/"));
			if (P.IsEmpty())
			{
				OutError = TEXT("source file path is empty");
				return false;
			}
			OutAbsolute = FPaths::ConvertRelativePathToFull(P);
			if (!FPaths::FileExists(OutAbsolute))
			{
				OutError = FString::Printf(
					TEXT("source file not found: %s (checked as '%s'). Paths are resolved against the editor's ")
					TEXT("working directory when relative — pass an absolute path."), *InPath, *OutAbsolute);
				return false;
			}
			return true;
		}
	}   // anonymous namespace

	// ==========================================================================================
	// import_texture
	//   in:  { destPath (aliases: path, assetPath),
	//          sourcePath (aliases: file, filename)   XOR   base64 (aliases: data, bytes),
	//          format?, overwrite? (replaceExisting), save? = true,
	//          compressionSettings? (compression), srgb?, lodGroup? (textureGroup),
	//          neverStream?, mipGenSettings? (mipGen), filter? }
	//   out: { objectPath, packageName, texturePath, class, created, ingest, imageFormat,
	//          decodedWidth, decodedHeight, decodedBitDepth, sourceValid, sourceWidth, sourceHeight,
	//          sourceFormat, sourceNumMips, sourceDataBytes, sizeX, sizeY, numMips, pixelFormat,
	//          compressionSettings, srgb, lodGroup, neverStream, filter, mipGenSettings,
	//          savedTo?, fileSizeBytes?, warnings? }
	// Bucket: SELF-MANAGED.
	//
	// The two ingest modes converge after decode, so a base64 import and a file import produce
	// byte-identical texture source data and are verified by the same code. base64 is the mode that
	// matters for the 42 black icons: an agent that generated a PNG holds bytes, not a file.
	// ==========================================================================================
	void H_import_texture(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("destPath"), TEXT("path"), TEXT("assetPath"),
			  TEXT("sourcePath"), TEXT("file"), TEXT("filename"),
			  TEXT("base64"), TEXT("data"), TEXT("bytes"),
			  TEXT("format"), TEXT("overwrite"), TEXT("replaceExisting"), TEXT("save"),
			  TEXT("compressionSettings"), TEXT("compression"), TEXT("srgb"), TEXT("sRGB"),
			  TEXT("lodGroup"), TEXT("textureGroup"), TEXT("neverStream"),
			  TEXT("mipGenSettings"), TEXT("mipGen"), TEXT("filter") },
			TEXT("destPath (aliases: path, assetPath), sourcePath (aliases: file, filename) OR base64 ")
			TEXT("(aliases: data, bytes), format, overwrite (alias: replaceExisting), save, ")
			TEXT("compressionSettings (alias: compression), srgb, lodGroup (alias: textureGroup), ")
			TEXT("neverStream, mipGenSettings (alias: mipGen), filter"),
			{ { TEXT("width"),  TEXT("not a parameter — dimensions come from the image itself; import_texture never rescales") },
			  { TEXT("height"), TEXT("not a parameter — dimensions come from the image itself; import_texture never rescales") },
			  { TEXT("textureClass"), TEXT("not implemented — import_texture creates UTexture2D only (cubemaps/volumes/render targets are not source-media imports)") } }))
		{
			return;
		}

		// --- destination -------------------------------------------------------------------
		FString PackagePath, AssetName;
		if (!MifImportValidateAssetPath(JStrAny(In, { TEXT("destPath"), TEXT("path"), TEXT("assetPath") }),
			Out, PackagePath, AssetName))
		{
			return;
		}

		// --- ingest mode: exactly one ------------------------------------------------------
		const FString SourcePath = JStrAny(In, { TEXT("sourcePath"), TEXT("file"), TEXT("filename") });
		const bool bHasBase64 = JHasAny(In, { TEXT("base64"), TEXT("data"), TEXT("bytes") });
		FString Base64Text = JStrAny(In, { TEXT("base64"), TEXT("data"), TEXT("bytes") });

		if (!SourcePath.IsEmpty() && bHasBase64)
		{
			Fail(Out, TEXT("supply EITHER sourcePath (a file on disk) OR base64 (raw bytes inline), not both — ")
				TEXT("with both present there is no way to know which one the caller meant"));
			return;
		}
		if (SourcePath.IsEmpty() && !bHasBase64)
		{
			Fail(Out, TEXT("one ingest mode is required: sourcePath (a file on disk) or base64 (the image bytes ")
				TEXT("inline, for content that was generated and never written to a file)"));
			return;
		}

		TArray<uint8> Compressed;
		FString ResolvedSourceFile;
		if (!SourcePath.IsEmpty())
		{
			FString Error;
			if (!MifImportResolveSourceFile(SourcePath, ResolvedSourceFile, Error)) { Fail(Out, Error); return; }
			if (!FFileHelper::LoadFileToArray(Compressed, *ResolvedSourceFile))
			{
				Fail(Out, FString::Printf(TEXT("could not read %s (exists but is unreadable — locked or permission denied)"),
					*ResolvedSourceFile));
				return;
			}
			Out->SetStringField(TEXT("ingest"), TEXT("file"));
			Out->SetStringField(TEXT("sourceFile"), ResolvedSourceFile);
		}
		else
		{
			// Tolerate the shapes an agent actually produces: a data: URI, and base64 wrapped across
			// lines. FBase64::Decode rejects both outright, and "decode failed" over a perfectly good
			// PNG is the kind of dead end this plugin is meant not to have.
			Base64Text.TrimStartAndEndInline();
			int32 CommaIndex = INDEX_NONE;
			if (Base64Text.StartsWith(TEXT("data:")) && Base64Text.FindChar(TEXT(','), CommaIndex))
			{
				Base64Text.MidInline(CommaIndex + 1);
			}
			Base64Text.ReplaceInline(TEXT("\r"), TEXT(""));
			Base64Text.ReplaceInline(TEXT("\n"), TEXT(""));
			Base64Text.ReplaceInline(TEXT("\t"), TEXT(""));
			Base64Text.ReplaceInline(TEXT(" "), TEXT(""));
			if (Base64Text.IsEmpty())
			{
				Fail(Out, TEXT("base64 was supplied but is empty after stripping any data: URI prefix and whitespace"));
				return;
			}
			if (!FBase64::Decode(Base64Text, Compressed))
			{
				Fail(Out, FString::Printf(
					TEXT("base64 decode failed on a %d-character payload — the string is not valid standard ")
					TEXT("base64 (length must be a multiple of 4 after padding; URL-safe '-_' alphabets are not accepted). ")
					TEXT("A data: URI prefix and embedded whitespace/newlines are stripped automatically, so those are not the cause."),
					Base64Text.Len()));
				return;
			}
			Out->SetStringField(TEXT("ingest"), TEXT("base64"));
			Out->SetNumberField(TEXT("base64Chars"), Base64Text.Len());
		}
		Out->SetNumberField(TEXT("payloadBytes"), Compressed.Num());

		// --- settings parsed BEFORE anything is created ------------------------------------
		FMifImportTextureSettings Settings;
		if (!MifImportReadTextureSettings(In, Out, Settings))
		{
			return;
		}

		// --- decode BEFORE creating the package --------------------------------------------
		// A bad image must leave nothing behind. Creating the package first and failing after would
		// litter the project with empty packages — the same shape as the stub problem.
		FMifImportDecodedImage Image;
		{
			FString Error;
			if (!MifImportDecodeImage(Compressed, JStr(In, TEXT("format")), Image, Error))
			{
				Fail(Out, Error);
				return;
			}
		}
		Out->SetStringField(TEXT("imageFormat"), Image.ImageFormatName);
		Out->SetNumberField(TEXT("decodedWidth"), Image.Width);
		Out->SetNumberField(TEXT("decodedHeight"), Image.Height);
		Out->SetNumberField(TEXT("decodedBitDepth"), Image.SourceBitDepth);

		// --- existing asset -----------------------------------------------------------------
		const bool bOverwrite = JBoolAny(In, { TEXT("overwrite"), TEXT("replaceExisting") }, false);
		const FString ObjectPath = PackagePath + TEXT(".") + AssetName;

		UObject* Existing = StaticFindObject(UObject::StaticClass(), nullptr, *ObjectPath);
		if (!Existing && FPackageName::DoesPackageExist(PackagePath))
		{
			Existing = StaticLoadObject(UObject::StaticClass(), nullptr, *ObjectPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
		}
		if (UObjectRedirector* Redirector = Cast<UObjectRedirector>(Existing))
		{
			Existing = Redirector->DestinationObject;
		}

		UTexture2D* Texture = nullptr;
		bool bCreated = false;

		if (Existing)
		{
			if (!bOverwrite)
			{
				Fail(Out, FString::Printf(
					TEXT("an asset already exists at %s (%s). Pass overwrite:true to refill it IN PLACE — ")
					TEXT("import_texture re-initialises the existing UTexture2D rather than replacing the object, ")
					TEXT("so everything already referencing it keeps working. Use a different destPath if you ")
					TEXT("wanted a new asset."),
					*ObjectPath, *Existing->GetClass()->GetName()));
				return;
			}
			Texture = Cast<UTexture2D>(Existing);
			if (!Texture)
			{
				Fail(Out, FString::Printf(
					TEXT("%s exists but is a %s, not a Texture2D — import_texture will not change an asset's class. ")
					TEXT("Delete it with delete_asset first, or pick a different destPath."),
					*ObjectPath, *Existing->GetClass()->GetName()));
				return;
			}
			if (MifImportIsContainerOnlyPackage(FName(*PackagePath)))
			{
				// Not refused: writing a loose override that shadows a mounted container package IS
				// the modkit's normal workflow (see save_package in MifBridgeIntrospect.cpp). But it
				// is surprising enough that answering silently would be dishonest.
				AddWarning(Out, FString::Printf(
					TEXT("%s currently comes from a mounted container (no loose file on disk). Saving writes a ")
					TEXT("LOOSE override that shadows the container copy for this project; the container itself is unchanged."),
					*PackagePath));
			}
			Texture->Modify();
			Texture->PreEditChange(nullptr);
		}
		else
		{
			if (MifImportIsContainerOnlyPackage(FName(*PackagePath)))
			{
				Fail(Out, FString::Printf(
					TEXT("destPath %s resolves to a container-only package (present in a mounted IoStore container, ")
					TEXT("no loose file). Creating a new asset there would shadow-mount ambiguously. Pick a destPath ")
					TEXT("that does not collide with container content."), *PackagePath));
				return;
			}
			UPackage* NewPackage = CreatePackage(*PackagePath);
			if (!NewPackage)
			{
				Fail(Out, FString::Printf(TEXT("failed to create package %s"), *PackagePath));
				return;
			}
			Texture = NewObject<UTexture2D>(NewPackage, FName(*AssetName),
				RF_Public | RF_Standalone | RF_Transactional);
			if (!Texture)
			{
				Fail(Out, FString::Printf(TEXT("failed to create UTexture2D %s in package %s"), *AssetName, *PackagePath));
				return;
			}
			bCreated = true;
		}

		UPackage* Package = Texture->GetOutermost();

#if WITH_EDITORONLY_DATA
		// THE WRITE. Init copies the raw pixels into the texture's editor bulk data — this is the
		// step whose absence makes a 4.7 KB stub.
		Texture->Source.Init(Image.Width, Image.Height, /*NumSlices*/ 1, /*NumMips*/ 1,
			Image.TextureFormat, Image.Raw.GetData());

		// Record the source file so reimport_asset works later. Only meaningful for file ingest —
		// base64 has no file, and inventing one would make reimport_asset fail confusingly instead of
		// saying "this asset has no source".
		if (!ResolvedSourceFile.IsEmpty())
		{
			if (!Texture->AssetImportData)
			{
				Texture->AssetImportData = NewObject<UAssetImportData>(Texture, NAME_None, RF_NoFlags);
			}
			Texture->AssetImportData->Update(ResolvedSourceFile);
		}
#else
		Fail(Out, TEXT("import_texture requires editor-only texture source data, which this build does not have"));
		return;
#endif

		MifImportApplyTextureSettings(Texture, Settings);

		// One PostEditChange with NO property. That deliberately avoids the LODGroup special case in
		// UTexture::PostEditChangeProperty (Texture.cpp:729-745), which — when told specifically that
		// LODGroup changed — silently rewrites CompressionSettings/SRGB/Filter/MipGenSettings for the
		// 8BitData and 16BitData groups. With no property it runs the same validation and the same
		// UpdateResource, and read-back matches what the caller asked for.
		Texture->PostEditChange();

		// Force the async DDC build to completion so the numbers reported below are real and so the
		// save writes actual bulk data rather than a header.
		MifImportFinishTextureBuild(Texture);

		if (bCreated)
		{
			FAssetRegistryModule::AssetCreated(Texture);
		}
		Package->MarkPackageDirty();

		// ---------------------- VERIFY AFTER WRITE ----------------------
		if (!MifImportVerifyTextureSettings(Texture, Settings, Out))
		{
			MifImportEmitTextureFacts(Texture, Out);
			return;
		}

#if WITH_EDITORONLY_DATA
		if (!Texture->Source.IsValid() || Texture->Source.GetSizeOnDisk() <= 0)
		{
			Fail(Out, FString::Printf(
				TEXT("texture source is EMPTY after import (%lld payload bytes) — the asset at %s would be another ")
				TEXT("header-only stub. The decode produced %dx%d and %lld raw bytes, so the failure is in ")
				TEXT("FTextureSource::Init, not the image."),
				(long long)Texture->Source.GetSizeOnDisk(), *ObjectPath,
				Image.Width, Image.Height, (long long)Image.Raw.Num()));
			MifImportEmitTextureFacts(Texture, Out);
			return;
		}
		if (Texture->Source.GetSizeX() != Image.Width || Texture->Source.GetSizeY() != Image.Height)
		{
			Fail(Out, FString::Printf(
				TEXT("texture source is %lldx%lld but the decoded image was %dx%d — refusing to report success over ")
				TEXT("a mismatch"),
				(long long)Texture->Source.GetSizeX(), (long long)Texture->Source.GetSizeY(),
				Image.Width, Image.Height));
			MifImportEmitTextureFacts(Texture, Out);
			return;
		}
#endif
		if (Texture->GetSizeX() <= 0 || Texture->GetSizeY() <= 0
			|| Texture->GetPixelFormat(0) == PF_Unknown)
		{
			Fail(Out, FString::Printf(
				TEXT("texture source was written but the platform build produced no usable data for %s ")
				TEXT("(sizeX=%d sizeY=%d pixelFormat=%s). The asset would render black. Check the Output Log for ")
				TEXT("texture build errors, and try compressionSettings:UserInterface2D for icon content."),
				*ObjectPath, Texture->GetSizeX(), Texture->GetSizeY(),
				GetPixelFormatString(Texture->GetPixelFormat(0))));
			MifImportEmitTextureFacts(Texture, Out);
			return;
		}

		// ---------------------- REPORT ----------------------
		EmitAssetIdentity(Out, Texture->GetPathName(), Package->GetName());
		Out->SetStringField(TEXT("texturePath"), Texture->GetPathName());
		Out->SetStringField(TEXT("class"), Texture->GetClass()->GetName());
		Out->SetBoolField(TEXT("created"), bCreated);
		MifImportEmitTextureFacts(Texture, Out);

		// Save is ON by default here, unlike create_material. See MifImportSavePackage.
		if (JBool(In, TEXT("save"), true))
		{
			MifImportSavePackage(Package, Out);
		}
		else
		{
			AddWarning(Out, TEXT("save:false — the texture exists in memory only and the file on disk is unchanged. ")
				TEXT("Call save_package before cooking or the old stub is what ships."));
		}

		// Icon-shaped content imported with world-texture defaults looks wrong in a way that reads as
		// a failed import. Say so once, only when the caller left both knobs alone.
		if (!Settings.bHasLODGroup && !Settings.bHasCompression)
		{
			Out->SetStringField(TEXT("hint"),
				TEXT("imported with default texture settings. For UI/shop icons call set_texture_settings with ")
				TEXT("lodGroup:UI, compressionSettings:UserInterface2D, mipGenSettings:NoMipmaps, neverStream:true ")
				TEXT("(or pass those same fields to import_texture directly)."));
		}

		UE_LOG(LogMifBridge, Log, TEXT("import_texture: %s %dx%d %s (%s ingest, %d payload bytes)"),
			*Texture->GetPathName(), Image.Width, Image.Height, *Image.ImageFormatName,
			ResolvedSourceFile.IsEmpty() ? TEXT("base64") : TEXT("file"), Compressed.Num());
	}

	// ==========================================================================================
	// import_asset
	//   in:  { file (aliases: filename, sourcePath), destination (aliases: destinationPath, path),
	//          name? (destinationName), factory?, replaceExisting? (overwrite),
	//          replaceExistingSettings?, save? }
	//   out: { destination, sourceFile, factory, numImported, imported[ { objectPath, packageName,
	//          class, sizeX?, sizeY?, pixelFormat?, sourceDataBytes? } ], savedTo?, fileSizeBytes? }
	// Bucket: SELF-MANAGED.
	//
	// bAutomated:true and bAsync:false are NOT parameters. They are the two invariants that keep this
	// endpoint from taking the editor down: interactive imports raise factory option dialogs (an FBX
	// import options window), and a modal freezes the game thread this HTTP ticker runs on, taking
	// the bridge with it — with no agent able to click OK. See the file header.
	// ==========================================================================================
	void H_import_asset(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("file"), TEXT("filename"), TEXT("sourcePath"),
			  TEXT("destination"), TEXT("destinationPath"), TEXT("path"),
			  TEXT("name"), TEXT("destinationName"), TEXT("factory"),
			  TEXT("replaceExisting"), TEXT("overwrite"), TEXT("replaceExistingSettings"), TEXT("save") },
			TEXT("file (aliases: filename, sourcePath), destination (aliases: destinationPath, path), ")
			TEXT("name (alias: destinationName), factory, replaceExisting (alias: overwrite), ")
			TEXT("replaceExistingSettings, save"),
			{ { TEXT("async"),    TEXT("not implemented and deliberately so — this server runs handlers synchronously inside the HTTP ticker, and UAssetImportTask::GetObjects() BLOCKS on an async import (AssetImportTask.h:78). Imports here always run bAsync:false, one long frame.") },
			  { TEXT("skeletal"), TEXT("not implemented — forcing static-vs-skeletal FBX needs a UFbxImportUI options object wired into the task; today the FBX factory's own detection decides. Import, then adjust, or pass an explicit factory.") },
			  { TEXT("options"),  TEXT("not implemented — per-factory option objects (UFbxImportUI etc.) are not exposed yet") },
			  { TEXT("base64"),   TEXT("not supported here — import_asset imports a FILE through a UFactory. For inline image bytes use import_texture {base64, destPath}.") } }))
		{
			return;
		}

		// --- source file --------------------------------------------------------------------
		FString ResolvedFile;
		{
			FString Error;
			const FString Requested = JStrAny(In, { TEXT("file"), TEXT("filename"), TEXT("sourcePath") });
			if (Requested.IsEmpty())
			{
				Fail(Out, TEXT("file is required (an absolute path to the source media on disk). For image bytes ")
					TEXT("held in memory rather than a file, use import_texture {base64, destPath}."));
				return;
			}
			if (!MifImportResolveSourceFile(Requested, ResolvedFile, Error)) { Fail(Out, Error); return; }
		}

		const int64 SourceFileSize = IFileManager::Get().FileSize(*ResolvedFile);
		if (SourceFileSize <= 0)
		{
			Fail(Out, FString::Printf(TEXT("source file %s is empty (%lld bytes) — nothing to import"),
				*ResolvedFile, (long long)SourceFileSize));
			return;
		}

		// --- destination folder --------------------------------------------------------------
		FString Destination = JStrAny(In, { TEXT("destination"), TEXT("destinationPath"), TEXT("path") });
		Destination.TrimStartAndEndInline();
		Destination.RemoveFromEnd(TEXT("/"));
		if (Destination.IsEmpty())
		{
			Fail(Out, TEXT("destination is required (a /Game/... FOLDER, e.g. /Game/Mod/Meshes — not an asset path)"));
			return;
		}
		if (!Destination.StartsWith(TEXT("/Game/")) && Destination != TEXT("/Game"))
		{
			Fail(Out, FString::Printf(
				TEXT("destination must start with /Game/ (got '%s')"), *Destination));
			return;
		}

		FString AssetName = JStrAny(In, { TEXT("name"), TEXT("destinationName") });
		AssetName.TrimStartAndEndInline();
		if (AssetName.IsEmpty())
		{
			AssetName = FPaths::GetBaseFilename(ResolvedFile);
		}
		// ObjectTools sanitises names during import, but validating here means a bad name errors
		// BEFORE the factory has half-built something.
		FText NameReason;
		if (!FName(*AssetName).IsValidObjectName(NameReason))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a valid asset name (derived from the filename when `name` is omitted)%s — ")
				TEXT("pass `name` explicitly"), *AssetName,
				NameReason.IsEmpty() ? TEXT("") : *FString::Printf(TEXT(": %s"), *NameReason.ToString())));
			return;
		}

		const FString ExpectedPackage = Destination + TEXT("/") + AssetName;
		if (!FPackageName::IsValidLongPackageName(ExpectedPackage))
		{
			Fail(Out, FString::Printf(TEXT("destination + name produce an invalid package path: %s"), *ExpectedPackage));
			return;
		}
		// docs/audit/work/B_assets_registry.md, "Cooked-editor caveat for import destination
		// collisions": importing onto a container-only path shadow-mounts ambiguously. Refuse rather
		// than let FPackageName resolution pick a winner.
		if (MifImportIsContainerOnlyPackage(FName(*ExpectedPackage)))
		{
			Fail(Out, FString::Printf(
				TEXT("%s already exists as a container-only package (mounted from an IoStore container, no loose file). ")
				TEXT("Importing there shadow-mounts ambiguously. Choose another destination or name."),
				*ExpectedPackage));
			return;
		}

		// --- factory ---------------------------------------------------------------------------
		const FString Extension = FPaths::GetExtension(ResolvedFile);
		UClass* FactoryClass = nullptr;
		TArray<FString> Candidates;

		const FString FactoryName = JStr(In, TEXT("factory"));
		if (!FactoryName.IsEmpty())
		{
			for (TObjectIterator<UClass> ClassIt; ClassIt; ++ClassIt)
			{
				if (ClassIt->IsChildOf(UFactory::StaticClass())
					&& !ClassIt->HasAnyClassFlags(CLASS_Abstract)
					&& ClassIt->GetName().Equals(FactoryName, ESearchCase::IgnoreCase))
				{
					FactoryClass = *ClassIt;
					break;
				}
			}
			if (!FactoryClass)
			{
				TArray<FString> AllFactories;
				MifImportFindFactoryClassForExtension(Extension, AllFactories);
				Fail(Out, FString::Printf(
					TEXT("factory class '%s' not found. Factories that accept '.%s': %s"),
					*FactoryName, *Extension,
					AllFactories.Num() ? *FString::Join(AllFactories, TEXT(", ")) : TEXT("(none)")));
				return;
			}
		}
		else
		{
			FactoryClass = MifImportFindFactoryClassForExtension(Extension, Candidates);
			if (!FactoryClass)
			{
				Fail(Out, FString::Printf(
					TEXT("no import factory in this editor accepts '.%s' (from %s). Supported extensions: %s"),
					*Extension, *ResolvedFile, *MifImportSupportedExtensionList()));
				return;
			}
			if (Candidates.Num() > 1)
			{
				AddWarning(Out, FString::Printf(
					TEXT("%d factories accept '.%s' (%s); highest ImportPriority won: %s. Pass `factory` to choose."),
					Candidates.Num(), *Extension, *FString::Join(Candidates, TEXT(", ")), *FactoryClass->GetName()));
			}
		}

		UFactory* Factory = NewObject<UFactory>(GetTransientPackage(), FactoryClass);
		if (!Factory)
		{
			Fail(Out, FString::Printf(TEXT("could not instantiate factory %s"), *FactoryClass->GetName()));
			return;
		}

		// --- task -------------------------------------------------------------------------------
		UAssetImportTask* Task = NewObject<UAssetImportTask>();
		Task->Filename = ResolvedFile;
		Task->DestinationPath = Destination;
		Task->DestinationName = AssetName;
		Task->bReplaceExisting = JBoolAny(In, { TEXT("replaceExisting"), TEXT("overwrite") }, false);
		Task->bReplaceExistingSettings = JBool(In, TEXT("replaceExistingSettings"), false);
		Task->bAutomated = true;    // INVARIANT — see file header. Suppresses every factory dialog.
		Task->bAsync = false;       // INVARIANT — no cross-frame work on this thread.
		Task->bSave = false;        // Saving is done below, AFTER texture builds are forced to finish,
		                            // so a texture asset never lands on disk without its bulk data.
		Task->Factory = Factory;    // INVARIANT — non-null keeps Interchange (and its async path) out.

		// Keep both alive across the import: the task and factory are plain NewObject and a GC inside
		// the factory's own work would collect them out from under ImportAssetTasks.
		Task->AddToRoot();
		Factory->AddToRoot();
		ON_SCOPE_EXIT
		{
			Task->RemoveFromRoot();
			Factory->RemoveFromRoot();
		};

		IAssetTools& AssetTools = FAssetToolsModule::GetModule().Get();
		{
			// AssetTools sets this itself from bAutomated (AssetTools.cpp:3045); setting it here too
			// covers any code the factory reaches that AssetTools' guard does not span.
			TGuardValue<bool> UnattendedGuard(GIsRunningUnattendedScript, true);
			TArray<UAssetImportTask*> Tasks;
			Tasks.Add(Task);
			AssetTools.ImportAssetTasks(Tasks);
		}

		// ---------------------- VERIFY AFTER WRITE ----------------------
		const TArray<UObject*>& Imported = Task->GetObjects();
		if (Imported.Num() == 0)
		{
			Fail(Out, FString::Printf(
				TEXT("import produced NO assets from %s using %s. The factory rejected the file — check the ")
				TEXT("Output Log (LogAssetTools / the factory's own category) for the reason. Common causes: the ")
				TEXT("extension is right but the contents are not (a renamed file), an existing asset blocked the ")
				TEXT("write (pass replaceExisting:true), or the file is a format variant the factory does not read."),
				*ResolvedFile, *FactoryClass->GetName()));
			return;
		}

		Out->SetStringField(TEXT("sourceFile"), ResolvedFile);
		Out->SetNumberField(TEXT("sourceFileBytes"), (double)SourceFileSize);
		Out->SetStringField(TEXT("destination"), Destination);
		Out->SetStringField(TEXT("factory"), FactoryClass->GetName());
		Out->SetNumberField(TEXT("numImported"), Imported.Num());

		const bool bSave = JBool(In, TEXT("save"), false);
		TArray<TSharedPtr<FJsonValue>> Rows;
		TSet<UPackage*> PackagesToSave;

		for (UObject* Object : Imported)
		{
			if (!Object) { continue; }
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			UPackage* ObjectPackage = Object->GetOutermost();
			EmitAssetIdentity(Row, Object->GetPathName(), ObjectPackage->GetName());
			Row->SetStringField(TEXT("class"), Object->GetClass()->GetName());

			// Same anti-stub reporting as import_texture for anything that came in as a texture.
			if (UTexture2D* Texture = Cast<UTexture2D>(Object))
			{
				MifImportFinishTextureBuild(Texture);
				MifImportEmitTextureFacts(Texture, Row);
			}

			ObjectPackage->MarkPackageDirty();
			PackagesToSave.Add(ObjectPackage);
			Rows.Add(MakeShared<FJsonValueObject>(Row));
		}
		Out->SetArrayField(TEXT("imported"), Rows);

		if (bSave)
		{
			TArray<TSharedPtr<FJsonValue>> Saved;
			for (UPackage* ObjectPackage : PackagesToSave)
			{
				TSharedRef<FJsonObject> SaveRow = MakeShared<FJsonObject>();
				SaveRow->SetStringField(TEXT("packageName"), ObjectPackage->GetName());
				MifImportSavePackage(ObjectPackage, SaveRow);
				Saved.Add(MakeShared<FJsonValueObject>(SaveRow));
			}
			Out->SetArrayField(TEXT("saved"), Saved);
		}
		else
		{
			AddWarning(Out, TEXT("save:false — imported assets exist in memory and are dirty, but nothing was ")
				TEXT("written to disk. Call save_package (or re-run with save:true) before cooking."));
		}

		UE_LOG(LogMifBridge, Log, TEXT("import_asset: %s -> %s via %s (%d asset(s))"),
			*ResolvedFile, *Destination, *FactoryClass->GetName(), Imported.Num());
	}

	// ==========================================================================================
	// reimport_asset
	//   in:  { path (aliases: assetPath, objectPath), sourceFile? (aliases: file, newFile),
	//          sourceFileIndex?, forceNewFile?, save? }
	//   out: { objectPath, packageName, class, sourceFiles[], reimportedFrom, changed,
	//          before{...}, after{...}, savedTo?, fileSizeBytes? }
	// Bucket: SELF-MANAGED.
	//
	// THE CASE THIS ENDPOINT CANNOT FIX, and says so: an asset with no recorded source file (or one
	// whose source is gone from disk) cannot be reimported. That is exactly the 42 stub icons. The
	// refusal below names import_texture's base64 mode rather than leaving the caller to guess — a
	// reimport that silently succeeded over a missing file would be the worst possible answer here.
	// ==========================================================================================
	void H_reimport_asset(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("objectPath"),
			  TEXT("sourceFile"), TEXT("file"), TEXT("newFile"),
			  TEXT("sourceFileIndex"), TEXT("forceNewFile"), TEXT("save") },
			TEXT("path (aliases: assetPath, objectPath), sourceFile (aliases: file, newFile), ")
			TEXT("sourceFileIndex, forceNewFile, save"),
			{ { TEXT("askForNewFileIfMissing"), TEXT("not settable — it would open a file-picker MODAL, which freezes the editor and this bridge with it. Pass sourceFile instead.") },
			  { TEXT("showNotification"),       TEXT("not settable — always false; the response IS the notification") } }))
		{
			return;
		}

		FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("objectPath") });
		Path.TrimStartAndEndInline();
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required (the asset to reimport, e.g. /Game/Mod/Icons/T_Apple)"));
			return;
		}

		UObject* Asset = StaticLoadObject(UObject::StaticClass(), nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Asset && !Path.Contains(TEXT(".")))
		{
			const FString Full = Path + TEXT(".") + FPackageName::GetShortName(Path);
			Asset = StaticLoadObject(UObject::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
		}
		if (UObjectRedirector* Redirector = Cast<UObjectRedirector>(Asset))
		{
			Asset = Redirector->DestinationObject;
		}
		if (!Asset)
		{
			Fail(Out, FString::Printf(
				TEXT("asset not found: %s (bare package paths like /Game/A/T_Foo are accepted)"), *Path));
			return;
		}

		// --- can this asset be reimported at all? ---------------------------------------------
		// FReimportManager::CanReimport asks the registered handlers and hands back the source
		// filenames they hold (EditorReimportHandler.h:51) — the same question the editor's own
		// "Reimport" menu entry asks.
		TArray<FString> SourceFilenames;
		const bool bCanReimport = FReimportManager::Instance()->CanReimport(Asset, &SourceFilenames);

		TArray<TSharedPtr<FJsonValue>> SourceRows;
		bool bAnySourceOnDisk = false;
		for (const FString& Filename : SourceFilenames)
		{
			const bool bExists = !Filename.IsEmpty() && FPaths::FileExists(Filename);
			bAnySourceOnDisk |= bExists;
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("file"), Filename);
			Row->SetBoolField(TEXT("existsOnDisk"), bExists);
			SourceRows.Add(MakeShared<FJsonValueObject>(Row));
		}
		Out->SetArrayField(TEXT("sourceFiles"), SourceRows);

		// --- explicit replacement source -------------------------------------------------------
		FString PreferredFile;
		const FString RequestedSource = JStrAny(In, { TEXT("sourceFile"), TEXT("file"), TEXT("newFile") });
		if (!RequestedSource.IsEmpty())
		{
			FString Error;
			if (!MifImportResolveSourceFile(RequestedSource, PreferredFile, Error)) { Fail(Out, Error); return; }
		}

		if (!bCanReimport)
		{
			Fail(Out, FString::Printf(
				TEXT("no reimport handler claims %s (%s) — this asset type was not produced by an importer, ")
				TEXT("so there is nothing to re-pull. Assets authored in-editor (Blueprints, materials, data ")
				TEXT("tables) are never reimportable."),
				*Asset->GetPathName(), *Asset->GetClass()->GetName()));
			return;
		}

		if (PreferredFile.IsEmpty() && (SourceFilenames.Num() == 0 || !bAnySourceOnDisk))
		{
			// THE 42-ICON CASE. Be explicit about the only route that actually works.
			Fail(Out, FString::Printf(
				TEXT("%s has no usable source file (%s) so it cannot be reimported. Supply sourceFile to point it ")
				TEXT("at a file, or — if the content only exists as bytes you hold, with no file anywhere — use ")
				TEXT("import_texture {destPath:\"%s\", base64:\"...\", overwrite:true}, which refills the EXISTING ")
				TEXT("texture in place so nothing referencing it breaks."),
				*Asset->GetPathName(),
				SourceFilenames.Num() == 0
					? TEXT("no source path is recorded on the asset")
					: TEXT("every recorded source path is missing from disk"),
				*Asset->GetOutermost()->GetName()));
			return;
		}

		// --- before ---------------------------------------------------------------------------
		UTexture2D* Texture = Cast<UTexture2D>(Asset);
		TSharedRef<FJsonObject> Before = MakeShared<FJsonObject>();
		FGuid BeforeId;
		if (Texture)
		{
			MifImportFinishTextureBuild(Texture);
			MifImportEmitTextureFacts(Texture, Before);
#if WITH_EDITORONLY_DATA
			BeforeId = Texture->Source.GetId();
#endif
		}
		Out->SetObjectField(TEXT("before"), Before);

		const int32 SourceFileIndex = JHasAny(In, { TEXT("sourceFileIndex") })
			? JInt(In, TEXT("sourceFileIndex"), INDEX_NONE) : INDEX_NONE;
		const bool bForceNewFile = JBool(In, TEXT("forceNewFile"), false);

		// bAskForNewFileIfMissing MUST stay false — true opens a file-picker modal.
		// bShowNotification MUST stay false — a Slate toast from an unattended handler is noise the
		// caller cannot see anyway. bAutomated=true is what suppresses the factory option dialogs.
		//
		// NOTE ON BLOCKING: FReimportManager::Reimport calls ImportResult->WaitUntilDone()
		// (Editor.cpp:277). It blocks this tick until the reimport finishes — one long frame, which
		// is legal here — but a very large source file will stall the bridge for its duration.
		bool bReimported = false;
		{
			TGuardValue<bool> UnattendedGuard(GIsRunningUnattendedScript, true);
			bReimported = FReimportManager::Instance()->Reimport(
				Asset,
				/*bAskForNewFileIfMissing*/ false,
				/*bShowNotification*/       false,
				/*PreferredReimportFile*/   PreferredFile,
				/*SpecifiedReimportHandler*/nullptr,
				/*SourceFileIndex*/         SourceFileIndex,
				/*bForceNewFile*/           bForceNewFile,
				/*bAutomated*/              true);
		}

		if (!bReimported)
		{
			Fail(Out, FString::Printf(
				TEXT("reimport of %s failed%s. The handler reported an error — check the Output Log for the ")
				TEXT("factory's reason (unreadable file, format changed since the original import, or the asset ")
				TEXT("is checked out/read-only)."),
				*Asset->GetPathName(),
				PreferredFile.IsEmpty() ? TEXT("") : *FString::Printf(TEXT(" from %s"), *PreferredFile)));
			return;
		}

		// ---------------------- VERIFY AFTER WRITE ----------------------
		UPackage* Package = Asset->GetOutermost();
		Package->MarkPackageDirty();

		TSharedRef<FJsonObject> After = MakeShared<FJsonObject>();
		bool bChanged = false;
		if (Texture)
		{
			MifImportFinishTextureBuild(Texture);
			MifImportEmitTextureFacts(Texture, After);
#if WITH_EDITORONLY_DATA
			bChanged = (Texture->Source.GetId() != BeforeId);
			if (!Texture->Source.IsValid() || Texture->Source.GetSizeOnDisk() <= 0)
			{
				Out->SetObjectField(TEXT("after"), After);
				Fail(Out, FString::Printf(
					TEXT("the reimport handler reported success but %s now has EMPTY texture source data — it is a ")
					TEXT("stub. Do NOT save over the previous asset; re-run with an explicit sourceFile, or use ")
					TEXT("import_texture."), *Asset->GetPathName()));
				return;
			}
#endif
		}
		Out->SetObjectField(TEXT("after"), After);

		EmitAssetIdentity(Out, Asset->GetPathName(), Package->GetName());
		Out->SetStringField(TEXT("class"), Asset->GetClass()->GetName());
		Out->SetStringField(TEXT("reimportedFrom"),
			PreferredFile.IsEmpty()
				? (SourceFilenames.Num() ? SourceFilenames[0] : TEXT("(handler-resolved)"))
				: PreferredFile);
		if (Texture)
		{
			// Honest, not alarming: reimporting an unchanged file legitimately changes nothing.
			Out->SetBoolField(TEXT("changed"), bChanged);
			if (!bChanged)
			{
				AddWarning(Out, TEXT("the texture source is byte-identical to what was there before — the reimport ")
					TEXT("succeeded but the file on disk had not changed."));
			}
		}

		if (JBool(In, TEXT("save"), false))
		{
			MifImportSavePackage(Package, Out);
		}
		else
		{
			AddWarning(Out, TEXT("save:false — the asset is reimported in memory and dirty, but the file on disk ")
				TEXT("still holds the OLD content. Call save_package."));
		}

		UE_LOG(LogMifBridge, Log, TEXT("reimport_asset: %s"), *Asset->GetPathName());
	}

	// ==========================================================================================
	// set_texture_settings
	//   in:  { path (aliases: assetPath, objectPath, texturePath),
	//          compressionSettings? (compression), srgb?, lodGroup? (textureGroup),
	//          neverStream?, mipGenSettings? (mipGen), filter?, save? }
	//   out: { objectPath, packageName, texturePath, class, changed[], + every field
	//          MifImportEmitTextureFacts writes, savedTo?, fileSizeBytes? }
	// Bucket: SELF-MANAGED.
	//
	// Without this endpoint import_texture is half a solution. A shop icon imported with the default
	// world-texture settings gets DXT compression (colour-banded gradients and haloed alpha at icon
	// sizes), a full mip chain it never uses, and streaming — so at first paint it is a blurry
	// low mip, which looks exactly like a broken import. The UI-appropriate set is
	// lodGroup:UI, compressionSettings:UserInterface2D, mipGenSettings:NoMipmaps, neverStream:true.
	// ==========================================================================================
	void H_set_texture_settings(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), TEXT("texturePath"),
			  TEXT("compressionSettings"), TEXT("compression"), TEXT("srgb"), TEXT("sRGB"),
			  TEXT("lodGroup"), TEXT("textureGroup"), TEXT("neverStream"),
			  TEXT("mipGenSettings"), TEXT("mipGen"), TEXT("filter"), TEXT("save") },
			TEXT("path (aliases: assetPath, objectPath, texturePath), compressionSettings (alias: compression), ")
			TEXT("srgb, lodGroup (alias: textureGroup), neverStream, mipGenSettings (alias: mipGen), filter, save"),
			{ { TEXT("addressX"),   TEXT("not implemented — tiling/address modes are a separate concern from this endpoint's compression/streaming set") },
			  { TEXT("addressY"),   TEXT("not implemented — tiling/address modes are a separate concern from this endpoint's compression/streaming set") },
			  { TEXT("maxTextureSize"), TEXT("not implemented — use set_property on MaxTextureSize") },
			  { TEXT("lodBias"),    TEXT("not implemented — use set_property on LODBias") } }))
		{
			return;
		}

		FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), TEXT("texturePath") });
		Path.TrimStartAndEndInline();
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required (a Texture2D asset path, e.g. /Game/Mod/Icons/T_Apple)"));
			return;
		}

		UObject* Object = StaticLoadObject(UObject::StaticClass(), nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Object && !Path.Contains(TEXT(".")))
		{
			const FString Full = Path + TEXT(".") + FPackageName::GetShortName(Path);
			Object = StaticLoadObject(UObject::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
		}
		if (UObjectRedirector* Redirector = Cast<UObjectRedirector>(Object))
		{
			Object = Redirector->DestinationObject;
		}
		if (!Object)
		{
			Fail(Out, FString::Printf(
				TEXT("asset not found: %s (bare package paths like /Game/A/T_Foo are accepted)"), *Path));
			return;
		}

		UTexture2D* Texture = Cast<UTexture2D>(Object);
		if (!Texture)
		{
			Fail(Out, FString::Printf(
				TEXT("%s is a %s, not a Texture2D — set_texture_settings only edits 2D textures"),
				*Object->GetPathName(), *Object->GetClass()->GetName()));
			return;
		}

		FMifImportTextureSettings Settings;
		if (!MifImportReadTextureSettings(In, Out, Settings))
		{
			return;
		}
		if (!Settings.Any())
		{
			Fail(Out, TEXT("no settings supplied — pass at least one of compressionSettings, srgb, lodGroup, ")
				TEXT("neverStream, mipGenSettings, filter. For UI/shop icons: lodGroup:UI, ")
				TEXT("compressionSettings:UserInterface2D, mipGenSettings:NoMipmaps, neverStream:true."));
			return;
		}

		// Record what changed so the response distinguishes "applied" from "was already that value".
		TArray<FString> Changed;
		if (Settings.bHasCompression && Texture->CompressionSettings != Settings.Compression) { Changed.Add(TEXT("compressionSettings")); }
		if (Settings.bHasSRGB && (Texture->SRGB != 0) != Settings.bSRGB)                      { Changed.Add(TEXT("srgb")); }
		if (Settings.bHasLODGroup && Texture->LODGroup != Settings.LODGroup)                  { Changed.Add(TEXT("lodGroup")); }
		if (Settings.bHasNeverStream && (Texture->NeverStream != 0) != Settings.bNeverStream) { Changed.Add(TEXT("neverStream")); }
		if (Settings.bHasFilter && Texture->Filter != Settings.Filter)                        { Changed.Add(TEXT("filter")); }
#if WITH_EDITORONLY_DATA
		if (Settings.bHasMipGen && Texture->MipGenSettings != Settings.MipGen)                { Changed.Add(TEXT("mipGenSettings")); }

		// A settings change rebuilds platform data FROM the source. With no source there is nothing
		// to rebuild, so the endpoint would "succeed" and leave a black texture black. Say so.
		if (!Texture->Source.IsValid() || Texture->Source.GetSizeOnDisk() <= 0)
		{
			Fail(Out, FString::Printf(
				TEXT("%s has no texture source data (%lld bytes) — it is a header-only stub, and changing its ")
				TEXT("settings cannot make it render. Give it pixels first with ")
				TEXT("import_texture {destPath:\"%s\", base64 or sourcePath, overwrite:true}, which refills this ")
				TEXT("exact object so existing references survive; the settings can be passed in that same call."),
				*Texture->GetPathName(), (long long)Texture->Source.GetSizeOnDisk(),
				*Texture->GetOutermost()->GetName()));
			return;
		}
#endif

		Texture->Modify();
		Texture->PreEditChange(nullptr);
		MifImportApplyTextureSettings(Texture, Settings);
		// PostEditChange with NO property — see the note in import_texture: naming LODGroup as the
		// changed property makes the engine silently rewrite four other fields for the 8BitData and
		// 16BitData groups (Texture.cpp:729-745), which would make the verify pass below a lie.
		Texture->PostEditChange();
		MifImportFinishTextureBuild(Texture);

		UPackage* Package = Texture->GetOutermost();
		Package->MarkPackageDirty();

		// ---------------------- VERIFY AFTER WRITE ----------------------
		if (!MifImportVerifyTextureSettings(Texture, Settings, Out))
		{
			MifImportEmitTextureFacts(Texture, Out);
			return;
		}
		if (Texture->GetSizeX() <= 0 || Texture->GetSizeY() <= 0 || Texture->GetPixelFormat(0) == PF_Unknown)
		{
			MifImportEmitTextureFacts(Texture, Out);
			Fail(Out, FString::Printf(
				TEXT("settings applied but the texture rebuild produced no usable platform data for %s ")
				TEXT("(sizeX=%d sizeY=%d pixelFormat=%s) — this combination of settings is not valid for this ")
				TEXT("source format. Check the Output Log."),
				*Texture->GetPathName(), Texture->GetSizeX(), Texture->GetSizeY(),
				GetPixelFormatString(Texture->GetPixelFormat(0))));
			return;
		}

		EmitAssetIdentity(Out, Texture->GetPathName(), Package->GetName());
		Out->SetStringField(TEXT("texturePath"), Texture->GetPathName());
		Out->SetStringField(TEXT("class"), Texture->GetClass()->GetName());
		TArray<TSharedPtr<FJsonValue>> ChangedRows;
		for (const FString& Field : Changed) { ChangedRows.Add(MakeShared<FJsonValueString>(Field)); }
		Out->SetArrayField(TEXT("changed"), ChangedRows);
		if (Changed.Num() == 0)
		{
			AddWarning(Out, TEXT("every requested setting already held the requested value — nothing changed. ")
				TEXT("The texture was still rebuilt and the package marked dirty."));
		}
		MifImportEmitTextureFacts(Texture, Out);

		if (JBool(In, TEXT("save"), false))
		{
			MifImportSavePackage(Package, Out);
		}
		else
		{
			AddWarning(Out, TEXT("save:false — settings are applied in memory and the package is dirty, but the ")
				TEXT("file on disk still holds the old settings. Call save_package."));
		}

		UE_LOG(LogMifBridge, Log, TEXT("set_texture_settings: %s (%d field(s) changed)"),
			*Texture->GetPathName(), Changed.Num());
	}
}
