// MifBridge — ASSET EXPORT. The read-side of round-tripping. Until this file existed, content
// could come IN (MifBridgeImport.cpp) but nothing could get OUT: a grep of every MIF_DECL found
// import_texture / import_asset / reimport_asset / set_texture_settings and no export of any kind.
// A mesh could not leave the editor, so any workflow that edits geometry in an external DCC
// (Blender, in the case this was written for) was blocked at step one.
//
//   export_asset — write ONE asset to a disk file through UExporter::RunAssetExportTask.
//                  StaticMesh -> FBX is the verified path; anything else UExporter::FindExporter
//                  resolves (Texture -> PNG/TGA, SoundWave -> WAV, Object/Level -> T3D, StaticMesh
//                  -> OBJ) is passed through and WARNED about rather than silently blessed.
//
// Bucket: READ-ONLY (registered in MifBridgeCommon.cpp's IsReadOnlyEndpoint). It writes a FILE and
// mutates no UObject — exactly the render_thumbnail / capture_camera precedent one bucket up from
// this one's entry. Consequences, both wanted: RunEndpoint opens no FScopedTransaction, so
// exporting in a loop does not push an empty entry per call onto the undo stack; and because
// IsCompileHeavyEndpoint derives from IsSelfManagedEndpoint, staying read-only keeps export_asset
// usable inside `batch`. The FBX SDK instance is created and destroyed per export
// (FScopedFbxExporterInstance, EditorExporters.cpp:96-111) and the exporter restores its own
// options override (EditorExporters.cpp:2182), so a call leaves no editor state behind either.
//
// ============================================================================================
// FOUR HAZARDS import_asset DOES NOT HAVE. Every one is fatal if a later edit drops it.
// ============================================================================================
//
// (1) THE FBX EXPORTER OPENS A MODAL UNLESS BOTH GATES ARE SET, AND GIsRunningUnattendedScript
//     DOES NOT SUPPRESS IT.
//     UStaticMeshExporterFBX::ExportBinary (EditorExporters.cpp:2153-2183) asks
//     GetAutomatedExportOptionsFbx() for options; that function is
//         if (ExportTask && ExportTask->bAutomated) { return Cast<UFbxExportOption>(ExportTask->Options); }
//         return nullptr;
//     (EditorExporters.cpp:2129-2136) — so bAutomated:true with a NULL or WRONG-TYPED Options falls
//     through to the else branch, which calls FFbxExporter::FillExportOptions, which calls
//     FSlateApplication::AddModalWindow (FbxMainExport.cpp:218). A modal on the game thread freezes
//     the HTTP ticker this server runs on and takes the bridge down with it, with no agent able to
//     click OK. Note WHICH flag FillExportOptions actually tests to early-return: `!bShowOptionDialog
//     || GIsAutomationTesting || FApp::IsUnattended()` (FbxMainExport.cpp:188). It does NOT test
//     GIsRunningUnattendedScript, which is the guard import_asset relies on — so the import file's
//     mitigation is not transferable and neither is its reasoning.
//     THEREFORE, three things, belt and braces:
//         Task->bAutomated = true;                 // gate 1
//         Task->Options    = <a real UFbxExportOption>;   // gate 2 — bAutomated alone is NOT enough
//         Exporter->SetShowExportOption(false);    // belt: makes FillExportOptions early-return even
//                                                  // if the Cast above ever fails
//     The default is unsafe: UExporter's constructor sets ShowExportOption = true
//     (UnrealExporter.cpp:54). This is the direct analogue of import_asset's "Task->Factory is ALWAYS
//     set explicitly" invariant, and for the same reason — the wrong default routes into machinery
//     this server cannot survive.
//
// (2) RunAssetExportTask RETURNS TRUE ON THREE PATHS THAT WRITE NO FILE.
//     Runtime/Engine/Private/UnrealExporter.cpp, line numbers read off D:/UE532 on 2026-08-09:
//         :320-323  text path, `if ( StringBuffer.Len() == 0 ) { // non-fatal \n return true; }`
//         :394-397  binary path, `if (!Task->bWriteEmptyFiles && !Buffer.Num()) { return true; }`
//         :364-407  binary path, ExportToArchive returning false skips the whole loop body and the
//                   function still `return true;` at :407
//     A bare `if (bOk)` would therefore answer ok:true over a missing or empty file. That is the
//     exact bug class MifBridgeImport.cpp's file header exists to kill ("an endpoint that answered
//     ok:true over an empty texture would be the exact bug it was written to kill"), so this file
//     inherits the same rule and is equally loud about SIZE: every success re-stats the file and
//     reports fileExists + fileSizeBytes, and a zero-or-missing file is a FAILURE with Task->Errors
//     copied verbatim into the message.
//
// (3) A STAT WITH NO PRE-IMAGE CANNOT TELL "JUST WRITTEN" FROM "LEFT OVER FROM LAST TIME".
//     This is (2)'s second half and it is the one that actually bites, because nothing in
//     RunAssetExportTask ever DELETES the destination on any of those three return-true paths. The
//     default output path is deterministic (MifExportRootDir()/<AssetName>.<ext>) and overwrite
//     defaults TRUE, so from the second call onwards there is always a pre-existing file — and a
//     failed export would stat yesterday's bytes and report ok:true, fileExists:true,
//     fileSizeBytes:<stale>. Downstream, mif_mesh_roundtrip would then import yesterday's FBX into
//     Blender, pass its fidelity gate against it, and declare the trip clean.
//     THEREFORE every expected output path is PHOTOGRAPHED BEFORE the export — existence, timestamp
//     and size — and a file counts as written only if it did not exist before, or its timestamp
//     advanced, or its size changed. Two things make that test sound rather than hopeful:
//       * Task->bReplaceIdentical is TRUE, so the two "Not replacing %s because identical" early
//         returns (UnrealExporter.cpp:334-335 text, :382-383 binary) are unreachable. A file that did
//         not move therefore means nothing was written, not "the engine skipped an identical write".
//       * The remaining false-positive is a real write of byte-identical content completing inside
//         the filesystem's timestamp granularity. That is refused rather than blessed — a refusal is
//         the safe direction, and the message says how to distinguish it (delete the file and retry).
//     DO NOT "fix" this by deleting the target first: that throws away a good previous export every
//     time a new one fails, which is the one moment the old file is worth most.
//
// (4) RunAssetExportTask DOES NOT NECESSARILY WRITE Task->Filename.
//     When GetFileCount() > 1 it writes Exporter->GetUniqueFilename(Object, *Task->Filename, i,
//     FileCount) once per index (UnrealExporter.cpp:366 and :372). Two exporters reachable from this
//     endpoint override that: UTextureExporterGeneric::GetFileCount returns NumBlocks * NumLayers,
//     i.e. > 1 for a UDIM or a layered virtual texture, and names the files MyTexture.1001.png /
//     MyTexture.L0.png (Editor/UnrealEd/Private/Factories/EditorFactories.cpp:4849 and :4864); and
//     USoundSurroundExporterWAV::GetFileCount returns SPEAKER_Count = 8, naming them _fl/_fr/_fc/...
//     (Editor/UnrealEd/Private/EditorExporters.cpp:253 and :258). Statting only Task->Filename would
//     report "produced no usable file" over a completely successful UDIM or surround export — both of
//     which this endpoint's own MCP docstring advertises. So the expected-path SET is enumerated from
//     the exporter, every member is verified, and the response carries files[].
//     Surround is also why the success rule is "at least one expected file was written" and not
//     "all of them": USoundSurroundExporterWAV always claims 8 files but its ExportBinary returns
//     false for a channel with no data (`bResult = Sound->ChannelSizes[FileIndex] != 0;`,
//     EditorExporters.cpp:296), so a 5.1 sound legitimately
//     produces 6. The ones that did not appear are named in a warning rather than passed over.
//
// THE INVERSE FBX TRAP — bWriteEmptyFiles MUST STAY FALSE. It reads like the fix for (2) and is the
// opposite. UStaticMeshExporterFBX writes the file ITSELF (`WriteToFile(*UExporter::CurrentFilename)`,
// EditorExporters.cpp:2181) and hands RunAssetExportTask an EMPTY FArchive. With bWriteEmptyFiles:true
// the caller at UnrealExporter.cpp:394-399 would then SaveArrayToFile an empty buffer OVER the real
// FBX the exporter just wrote. False is correct; the verify-after-write below is what covers (2).
//
// WHY UExporter::FindExporter AND NOT A NAMED EXPORTER CLASS. UStaticMeshExporterFBX carries no
// UNREALED_API (StaticMeshExporterFBX.h:15 is a bare UCLASS()), so referencing its StaticClass()
// from this module would be a link error. FindExporter (Exporter.h:186, ENGINE_API) resolves it by
// reflection over registered exporter CDOs instead (UnrealExporter.cpp:100-146) — the same
// reflection-over-CDOs discipline MifBridgeImport.cpp already chose for import factories, and it
// also means a format this file never heard of works the day a plugin registers an exporter for it.
// Likewise UFbxExportOption is MinimalAPI (FbxExportOption.h:27): NewObject<> links (the
// UAssetImportTask precedent at MifBridgeImport.cpp:1281 is the same UCLASS specifier in the same
// module), but its SaveOptions/LoadOptions/ResetToDefault carry no UNREALED_API and would NOT — so
// every option below is set as a plain UPROPERTY write and none of those functions is called.
//
// IAssetTools::ExportAssets IS DELIBERATELY NOT USED. UAssetToolsImpl::ExportAssetsInternal opens a
// directory picker when ExportPath is empty and runs a GWarn slow task (AssetTools.cpp:3808-3848),
// and never sets bAutomated — i.e. it walks straight into hazard (1).
//
// SYNCHRONOUS, SINGLE TICK, NO MODALS — the same three constraints MifBridgeImport.cpp lists.
// RunAssetExportTask is fully synchronous; there is no async variant and therefore no job slot and
// nothing to poll. A large mesh makes ONE long frame, which is legal; work that SPANS frames is not.
//
// MODULE DEPENDENCIES: none added. "Engine" (public) covers UExporter
// (Runtime/Engine/Classes/Exporters/Exporter.h), UAssetExportTask
// (Runtime/Engine/Public/AssetExportTask.h) and UStaticMesh; "UnrealEd" (private, MifBridge.Build.cs)
// covers UFbxExportOption and links the FBX SDK plus every UExporter subclass. The
// "Exporters/FbxExportOption.h" include is the same Editor/UnrealEd/Classes/ convention
// MifBridgeImport.cpp:90 already uses for "Factories/Factory.h".
//
// NO BUILD MANIFEST EDIT IS NEEDED FOR THIS FILE. UnrealBuildTool globs the module's Private/
// directory; there is no source list in MifBridge.Build.cs and no .vcxproj checked in that enumerates
// files. Dropping this .cpp into Source/MifBridge/Private/ is the whole of "adding" it.
//
// NAMING: every free helper here carries the MifExport prefix. The module is a UNITY build — a free
// function name duplicated across two .cpp in the same Module.MifBridge.N.cpp blob is C2084 even with
// internal linkage, and blob membership moves on its own as file sizes change, so "they're in
// different blobs today" is not a defence (MifBridgeImport.cpp:114-121 records the same rule after it
// broke the build once).
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetExportTask.h"                     // UAssetExportTask (Runtime/Engine/Public), fields :20-:65
#include "Engine/StaticMesh.h"                   // UStaticMesh + FStaticMaterial (mesh facts below)
#include "Exporters/Exporter.h"                  // UExporter::FindExporter / RunAssetExportTask
#include "Exporters/FbxExportOption.h"           // UFbxExportOption + EFbxExportCompatibility (UnrealEd/Classes)
#include "HAL/FileManager.h"                     // IFileManager::FileExists / FileSize — the anti-stub numbers
#include "HAL/PlatformFileManager.h"             // CreateDirectoryTree
#include "Materials/MaterialInterface.h"         // FStaticMaterial::MaterialInterface->GetPathName()
#include "Misc/DateTime.h"                       // FDateTime + FDateTime::MinValue (DateTime.h:668) — the pre-image stamp
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "UObject/GCObjectScopeGuard.h"          // FGCObjectScopeGuard = TGCObjectScopeGuard<const UObject>
#include "UObject/ObjectMacros.h"                // PKG_DisallowExport (ObjectMacros.h:140)
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"              // LoadObject / NewObject
#include "UObject/UObjectIterator.h"             // exporter-CDO sweep for the "no exporter" message

namespace MifBridge
{
	namespace
	{
		// ============================================================================
		// Every helper below is MifExport-prefixed. See the NAMING note in the header.
		// ============================================================================

		/** <ProjectSaved>/MifBridge/Export — the one place bridge-produced export files land, the
		 *  same root convention render_thumbnail uses for its images (MifBridgeThumbnail.cpp:620-628)
		 *  so a caller has one directory to look in. Created on demand: RunAssetExportTask creates a
		 *  directory only on the TEXT path with bForceFileOperations (UnrealExporter.cpp:290), and the
		 *  FBX SDK's WriteToFile does not create one at all, so a missing folder is a silent no-file. */
		FString MifExportRootDir()
		{
			const FString Dir = FPaths::ProjectSavedDir() / TEXT("MifBridge") / TEXT("Export");
			return FPaths::ConvertRelativePathToFull(Dir);
		}

		/** Every format any loaded exporter can write for THIS object, e.g. "FBX, OBJ, T3D". Makes a
		 *  "no exporter" refusal actionable instead of a dead end — the MifImportSupportedExtensionList
		 *  idea applied to the export side. Swept over UExporter CDOs by reflection for the same reason
		 *  FindExporter does: naming exporter classes in C++ would be a link error (see the header). */
		FString MifExportFormatsForObject(UObject* Object)
		{
			TSet<FString> All;
			for (TObjectIterator<UClass> ClassIt; ClassIt; ++ClassIt)
			{
				UClass* Candidate = *ClassIt;
				if (!Candidate->IsChildOf(UExporter::StaticClass())
					|| Candidate->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated | CLASS_NewerVersionExists))
				{
					continue;
				}
				UExporter* CDO = Cast<UExporter>(Candidate->GetDefaultObject());
				if (!CDO || !CDO->SupportsObject(Object))
				{
					continue;
				}
				for (const FString& Ext : CDO->FormatExtension)
				{
					if (!Ext.IsEmpty() && Ext != TEXT("*")) { All.Add(Ext.ToUpper()); }
				}
			}
			TArray<FString> Sorted = All.Array();
			Sorted.Sort();
			return Sorted.Num() ? FString::Join(Sorted, TEXT(", ")) : TEXT("(none)");
		}

		/** fbxCompatibility by REFLECTION over EFbxExportCompatibility (FbxExportOption.h:14-25), never
		 *  a hand-written table — same rule as MifImportParseEnum: the accepted set cannot drift from
		 *  the engine's. The FBX_ prefix is optional, so both "FBX_2020" and "2020" are accepted, and an
		 *  unknown value gets the same near-miss treatment the rest of the plugin gives unknown names. */
		bool MifExportParseFbxCompatibility(const FString& InText, EFbxExportCompatibility& OutValue, FString& OutError)
		{
			UEnum* Enum = StaticEnum<EFbxExportCompatibility>();
			if (!Enum)
			{
				OutError = TEXT("fbxCompatibility: the reflected enum EFbxExportCompatibility is unavailable in this ")
					TEXT("editor build — omit fbxCompatibility to use THIS ENDPOINT's default, FBX_2020. (That is ")
					TEXT("deliberately not the engine's: UFbxExportOption's constructor sets FBX_2013, ")
					TEXT("Editor/UnrealEd/Private/Fbx/FbxExportOption.cpp:21, and this handler overwrites it on ")
					TEXT("every export. The value actually written is echoed back as fbxCompatibility in the ")
					TEXT("response, so a version mismatch in Blender is diagnosable from the payload.)");
				return false;
			}
			FString Text = InText;
			Text.TrimStartAndEndInline();
			if (Text.IsEmpty())
			{
				OutError = TEXT("fbxCompatibility was supplied but empty");
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
				Short.RemoveFromStart(TEXT("FBX_"), ESearchCase::IgnoreCase);
				Accepted.Add(Full);
				if (Text.Equals(Full, ESearchCase::IgnoreCase) || Text.Equals(Short, ESearchCase::IgnoreCase))
				{
					OutValue = (EFbxExportCompatibility)Enum->GetValueByIndex(i);
					return true;
				}
			}

			const FString Near = NearMissSuggestion(Accepted, Text, 3);
			OutError = FString::Printf(
				TEXT("unknown fbxCompatibility '%s'%s — accepted (the FBX_ prefix is optional): %s"),
				*Text,
				Near.IsEmpty() ? TEXT("") : *FString::Printf(TEXT(" (did you mean %s?)"), *Near),
				*FString::Join(Accepted, TEXT(", ")));
			return false;
		}

		/** The reflected NAME of the compatibility value that was actually written, for the response.
		 *  Reported because this endpoint's default (FBX_2020) is NOT the engine's — UFbxExportOption's
		 *  constructor sets FBX_2013 (FbxExportOption.cpp:21) and this handler overwrites it — so a
		 *  caller chasing an FBX-version problem in Blender must not have to infer the value from the
		 *  absence of a parameter. Falls back to the raw number rather than to a plausible-looking
		 *  name if reflection is unavailable: an unreflected build should say so, not guess. */
		FString MifExportFbxCompatibilityName(EFbxExportCompatibility Value)
		{
			if (const UEnum* Enum = StaticEnum<EFbxExportCompatibility>())
			{
				// UEnum::GetNameStringByValue, Class.h:2060 (COREUOBJECT_API). Returns "" on no match.
				const FString Name = Enum->GetNameStringByValue((int64)Value);
				if (!Name.IsEmpty()) { return Name; }
			}
			return FString::Printf(TEXT("(unreflected EFbxExportCompatibility value %d)"), (int32)Value);
		}

		/** ONE EXPECTED OUTPUT FILE, photographed before the export and re-stated after it.
		 *
		 *  This exists because a stat with no pre-image cannot tell "just written" from "left over from
		 *  the last run" — hazard (3) in the file header. bPreExisted/PreStamp/PreBytes are captured
		 *  BEFORE RunAssetExportTask; WasWritten() is the only thing allowed to answer "did the export
		 *  actually produce this".
		 *
		 *  NOTE the deliberate asymmetry in WasWritten(): a file that did not exist before and exists
		 *  now with bytes in it is written, no timestamp reasoning required. Only a PRE-EXISTING file
		 *  has to prove it moved. */
		struct FMifExportFileImage
		{
			FString   Path;

			bool      bPreExisted = false;
			FDateTime PreStamp    = FDateTime::MinValue();
			int64     PreBytes    = -1;

			bool      bPostExists = false;
			FDateTime PostStamp   = FDateTime::MinValue();
			int64     PostBytes   = -1;

			void CaptureBefore()
			{
				IFileManager& FM = IFileManager::Get();
				bPreExisted = FM.FileExists(*Path);
				PreStamp    = bPreExisted ? FM.GetTimeStamp(*Path) : FDateTime::MinValue();
				PreBytes    = bPreExisted ? FM.FileSize(*Path)     : -1;
			}

			void CaptureAfter()
			{
				IFileManager& FM = IFileManager::Get();
				bPostExists = FM.FileExists(*Path);
				PostStamp   = bPostExists ? FM.GetTimeStamp(*Path) : FDateTime::MinValue();
				PostBytes   = bPostExists ? FM.FileSize(*Path)     : -1;
			}

			bool WasWritten() const
			{
				if (!bPostExists || PostBytes <= 0) { return false; }
				if (!bPreExisted)                   { return true; }
				return PostStamp != PreStamp || PostBytes != PreBytes;
			}

			/** One word for the response and for the failure breakdown. "stale" is the whole point of
			 *  the struct: it is the state an ok:true would previously have been reported over. */
			const TCHAR* Verdict() const
			{
				if (!bPostExists)   { return TEXT("missing"); }
				if (PostBytes <= 0) { return TEXT("empty"); }
				if (!WasWritten())  { return TEXT("stale"); }
				return TEXT("written");
			}

			FString Describe() const
			{
				return FString::Printf(TEXT("%s [%s, %lld bytes%s]"),
					*Path, Verdict(), (long long)PostBytes,
					bPreExisted
						? *FString::Printf(TEXT("; before: %lld bytes at %s"),
							(long long)PreBytes, *PreStamp.ToString())
						: TEXT("; did not exist before"));
			}
		};

		/** One {x,y,z} object in unreal units. The round-trip this endpoint exists for asserts on these
		 *  numbers, so they are emitted as numbers, never as a formatted string. */
		TSharedRef<FJsonObject> MifExportVectorJson(const FVector& V)
		{
			TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetNumberField(TEXT("x"), V.X);
			O->SetNumberField(TEXT("y"), V.Y);
			O->SetNumberField(TEXT("z"), V.Z);
			return O;
		}

		/** The PRE-IMAGE a round-trip is checked against: LOD count, LOD0 vert/tri counts, material slot
		 *  names IN ORDER, and the bounding box in uu. This is not decoration — the driving use case
		 *  (extrude a skirt onto a road mesh in Blender and bring it back) is only safe if the X length
		 *  is unchanged and the slot ORDER survived, and neither is checkable without a recorded before.
		 *
		 *  BOUNDS CAVEAT, stated because it would otherwise be a silent wrong number: GetBoundingBox()
		 *  is GetExtendedBounds().GetBox() (StaticMesh.cpp:3713-3716), i.e. the geometry bounds PLUS the
		 *  asset's Positive/NegativeBoundsExtension. Those are zero on almost every mesh, but when they
		 *  are not, the reported box is larger than the geometry — so they are emitted too and a warning
		 *  is raised, rather than letting an assert compare a tiling length against an inflated box. */
		void MifExportEmitStaticMeshFacts(UStaticMesh* Mesh, const TSharedRef<FJsonObject>& Out, bool bAllLODs)
		{
			TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
			const int32 NumLODs = Mesh->GetNumLODs();
			M->SetNumberField(TEXT("numLODs"), NumLODs);
			M->SetStringField(TEXT("lodExported"), bAllLODs ? TEXT("all") : TEXT("0"));
			M->SetNumberField(TEXT("numVertices"), Mesh->GetNumVertices(0));
			M->SetNumberField(TEXT("numTriangles"), Mesh->GetNumTriangles(0));

			TArray<TSharedPtr<FJsonValue>> Slots;
			for (const FStaticMaterial& Slot : Mesh->GetStaticMaterials())
			{
				TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
				Row->SetStringField(TEXT("slotName"), Slot.MaterialSlotName.ToString());
				Row->SetStringField(TEXT("material"),
					Slot.MaterialInterface ? Slot.MaterialInterface->GetPathName() : TEXT(""));
				Slots.Add(MakeShared<FJsonValueObject>(Row));
			}
			M->SetArrayField(TEXT("materialSlots"), Slots);

			const FBox Box = Mesh->GetBoundingBox();
			M->SetObjectField(TEXT("boundsMinUU"), MifExportVectorJson(Box.Min));
			M->SetObjectField(TEXT("boundsMaxUU"), MifExportVectorJson(Box.Max));
			M->SetObjectField(TEXT("boundsSizeUU"), MifExportVectorJson(Box.GetSize()));

			const FVector PosExt = Mesh->GetPositiveBoundsExtension();
			const FVector NegExt = Mesh->GetNegativeBoundsExtension();
			if (!PosExt.IsNearlyZero() || !NegExt.IsNearlyZero())
			{
				M->SetObjectField(TEXT("boundsExtensionPositiveUU"), MifExportVectorJson(PosExt));
				M->SetObjectField(TEXT("boundsExtensionNegativeUU"), MifExportVectorJson(NegExt));
				AddWarning(Out, TEXT("this mesh has a non-zero Positive/NegativeBoundsExtension, so boundsSizeUU is ")
					TEXT("LARGER than the exported geometry (GetBoundingBox is the EXTENDED bounds, ")
					TEXT("StaticMesh.cpp:3713). Subtract the reported extensions before asserting a round-trip ")
					TEXT("against these numbers."));
			}

			Out->SetObjectField(TEXT("mesh"), M);
		}
	} // namespace

	// ==========================================================================================
	// export_asset
	//   in:  { asset (aliases: path, assetPath, objectPath), file? (aliases: filename, outPath),
	//          format? (aliases: type, extension), overwrite?, fbxCompatibility?, ascii?,
	//          vertexColor?, levelOfDetail? (lod), collision?, exportSourceMesh?, forceFrontXAxis? }
	//   out: { objectPath, packageName, assetClass, format, exporterClass, file, fileExists,
	//          fileSizeBytes, fileCount, filesWritten, totalFileSizeBytes,
	//          files[{ file, written, verdict, existedBefore, fileSizeBytes, fileSizeBytesBefore }],
	//          elapsedMs, axis{...}?, fbxCompatibility?, mesh{ numLODs, lodExported, numVertices,
	//          numTriangles, materialSlots[], boundsMinUU, boundsMaxUU, boundsSizeUU }?, warnings[]? }
	//
	// `file`/`fileSizeBytes` describe a file this call PROVABLY WROTE (see hazard 3), never one that
	// merely exists. files[] is always present and is the full picture; for the verified
	// StaticMesh->FBX path it has exactly one entry and `file` is the path that was requested.
	// verdict is one of written | stale | missing | empty, and a response only ever reaches the caller
	// when at least one entry is `written`.
	// Bucket: READ-ONLY.
	//
	// bAutomated:true, a non-null UFbxExportOption in Task->Options, and bWriteEmptyFiles:false are
	// NOT parameters. They are the three invariants that keep this endpoint from hanging the editor or
	// clobbering its own output. See the file header.
	//
	// AXIS CONTRACT, reported in every FBX response because the round-trip depends on it and a caller
	// should not have to know engine internals to trust it: the exporter writes Up = +Z, Front = -Y,
	// right-handed, system unit centimetres (FbxMainExport.cpp:268-276), and UE's own importer skips
	// ConvertScene entirely when a file already declares that system (FbxMainImport.cpp:1500-1515). It
	// is also, bit for bit, Blender's native axis system — but that half is NOT verified here and the
	// warning below says so rather than implying this endpoint tested it.
	// ==========================================================================================
	void H_export_asset(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("asset"), TEXT("path"), TEXT("assetPath"), TEXT("objectPath"),
			  TEXT("file"), TEXT("filename"), TEXT("outPath"),
			  TEXT("format"), TEXT("type"), TEXT("extension"),
			  TEXT("overwrite"), TEXT("replaceExisting"),
			  TEXT("fbxCompatibility"), TEXT("ascii"), TEXT("vertexColor"),
			  TEXT("levelOfDetail"), TEXT("lod"), TEXT("collision"),
			  TEXT("exportSourceMesh"), TEXT("forceFrontXAxis") },
			TEXT("asset (aliases: path, assetPath, objectPath), file (aliases: filename, outPath), ")
			TEXT("format (aliases: type, extension), overwrite (alias: replaceExisting), ")
			TEXT("fbxCompatibility, ascii, vertexColor, levelOfDetail (alias: lod), collision, ")
			TEXT("exportSourceMesh, forceFrontXAxis"),
			{ { TEXT("destination"), TEXT("export_asset writes to a DISK path, not a /Game folder — spell it file. (destination means a /Game/... content folder in import_asset, and honouring it here would silently write a .fbx into a path that reads like a package.) Omit it entirely to get <ProjectSaved>/MifBridge/Export/<AssetName>.<ext>.") },
			  { TEXT("async"),       TEXT("not implemented and deliberately so — this server runs handlers synchronously inside the HTTP ticker. UExporter has no async export; a large mesh makes one long frame, which is legal, and work that SPANS frames is not.") },
			  { TEXT("selected"),    TEXT("not implemented — UAssetExportTask::bSelected filters an ACTOR SELECTION for level/object exports; this endpoint exports one named asset and always sends false.") },
			  { TEXT("options"),     TEXT("not implemented as a free-form object — the FBX option fields are exposed individually (fbxCompatibility, ascii, vertexColor, levelOfDetail, collision, exportSourceMesh, forceFrontXAxis). No other exporter's option object is wired, and passing a raw object would defeat the type check that keeps the FBX options MODAL shut (EditorExporters.cpp:2129).") },
			  { TEXT("base64"),      TEXT("not supported — export_asset writes a FILE and reports its path and byte size. Read the bytes off disk at the returned `file`.") },
			  { TEXT("batch"),       TEXT("not implemented — call once per asset. The FBX SDK instance is created and destroyed per export (EditorExporters.cpp:96-111), so batching inside one call would save nothing. export_asset IS read-only, so the `batch` ENDPOINT can drive several of these in one request.") },
			  { TEXT("save"),        TEXT("not a parameter — export_asset writes a disk file and never touches the asset or its package, so there is nothing to save. (It is read-only for exactly that reason.)") },
			  { TEXT("lodIndex"),    TEXT("not implemented — the FBX exporter takes a bool (levelOfDetail: all LODs, or LOD0 only), not an index. Export with levelOfDetail:false for LOD0.") } }))
		{
			return;
		}

		const double StartSeconds = FPlatformTime::Seconds();

		// --- asset ------------------------------------------------------------------------------
		const FString AssetPath = JStrAny(In, { TEXT("asset"), TEXT("path"), TEXT("assetPath"), TEXT("objectPath") });
		if (AssetPath.IsEmpty())
		{
			Fail(Out, TEXT("asset is required (a /Game/... object path, e.g. ")
				TEXT("/Game/StaticMeshes/Enviro/Roads/SM_Road_Dirt_Wide). Find one with find_assets."));
			return;
		}
		UObject* Asset = LoadObject<UObject>(nullptr, *AssetPath);
		if (!Asset)
		{
			Fail(Out, FString::Printf(
				TEXT("asset not found: %s. Pass the ASSET's object path, not a generated-class path — a ")
				TEXT("trailing _C names a UClass, which has no exporter. Locate it with find_assets."),
				*AssetPath));
			return;
		}

		UPackage* AssetPackage = Asset->GetOutermost();

		// FindExporter returns NULL for a PKG_DisallowExport package and says nothing about why
		// (UnrealExporter.cpp:104-106). Pre-empt it with a message that names the actual cause, so this
		// does not read as "no FBX exporter exists".
		if (AssetPackage->HasAnyPackageFlags(PKG_DisallowExport))
		{
			Fail(Out, FString::Printf(
				TEXT("package %s is flagged PKG_DisallowExport, so UExporter::FindExporter refuses every ")
				TEXT("format for it (UnrealExporter.cpp:104). That flag is only ever set by an explicit editor ")
				TEXT("command and propagated on duplicate — it is NOT set by cooking, so a cooked game asset ")
				TEXT("is normally exportable and this one has been marked deliberately."),
				*AssetPackage->GetName()));
			return;
		}

		UStaticMesh* StaticMesh = Cast<UStaticMesh>(Asset);

		// STATIC-MESH RENDER-DATA GUARD. This one prevents an editor CRASH, not a bad response:
		// UStaticMesh::GetLODForExport does check(GetRenderData()) (StaticMesh.cpp:8206-8212), an
		// assert, and the FBX exporter walks straight into it. GetNumLODs() is the null-safe way to
		// ask the same question (StaticMesh.cpp:3663-3671).
		if (StaticMesh && StaticMesh->GetNumLODs() == 0)
		{
			Fail(Out, FString::Printf(
				TEXT("static mesh %s has no render data (LOD count 0) — nothing to export, and the FBX ")
				TEXT("exporter would assert on UStaticMesh::GetLODForExport (StaticMesh.cpp:8208) rather than ")
				TEXT("fail. This is what a header-only / stripped mesh asset looks like."),
				*Asset->GetPathName()));
			return;
		}

		// --- format + output file -----------------------------------------------------------------
		// Both are derivable from the other, so resolve them together and refuse a contradiction rather
		// than picking a winner: RunAssetExportTask takes the exporter from `format` but derives the
		// TYPE string it passes to ExportBinary from the FILENAME's extension (UnrealExporter.cpp:236),
		// so a mismatch is a genuinely ambiguous request, not a typo to paper over.
		FString RequestedFile = JStrAny(In, { TEXT("file"), TEXT("filename"), TEXT("outPath") });
		RequestedFile.TrimStartAndEndInline();

		FString Format = JStrAny(In, { TEXT("format"), TEXT("type"), TEXT("extension") });
		Format.TrimStartAndEndInline();
		Format.RemoveFromStart(TEXT("."));
		Format.ToUpperInline();

		const FString FileExtension = RequestedFile.IsEmpty()
			? FString() : FPaths::GetExtension(RequestedFile).ToUpper();

		if (!RequestedFile.IsEmpty() && FileExtension.IsEmpty())
		{
			Fail(Out, FString::Printf(
				TEXT("file '%s' has no extension. RunAssetExportTask derives the export TYPE from the ")
				TEXT("filename (UnrealExporter.cpp:236), so the extension is required — e.g. ...\\%s.fbx."),
				*RequestedFile, *Asset->GetName()));
			return;
		}
		if (!Format.IsEmpty() && !FileExtension.IsEmpty() && Format != FileExtension)
		{
			Fail(Out, FString::Printf(
				TEXT("format '%s' contradicts the extension of file '%s' ('%s'). The exporter is chosen from ")
				TEXT("format but the export type is read back off the filename, so this would write a '%s' ")
				TEXT("payload into a '.%s' file. Pass one, or make them agree."),
				*Format, *RequestedFile, *FileExtension, *Format, *FileExtension.ToLower()));
			return;
		}
		if (Format.IsEmpty())
		{
			Format = FileExtension.IsEmpty() ? TEXT("FBX") : FileExtension;
		}

		FString FullOutPath;
		if (RequestedFile.IsEmpty())
		{
			FullOutPath = MifExportRootDir() / (FPaths::MakeValidFileName(Asset->GetName()) + TEXT(".") + Format.ToLower());
		}
		else if (FPaths::IsRelative(RequestedFile))
		{
			// A relative path is resolved against the bridge's own export root rather than the
			// process CWD, which in the editor is not where anyone thinks it is.
			FullOutPath = MifExportRootDir() / RequestedFile;
		}
		else
		{
			FullOutPath = RequestedFile;
		}
		FPaths::NormalizeFilename(FullOutPath);
		FullOutPath = FPaths::ConvertRelativePathToFull(FullOutPath);

		const bool bOverwrite = JBoolAny(In, { TEXT("overwrite"), TEXT("replaceExisting") }, true);
		if (!bOverwrite && IFileManager::Get().FileExists(*FullOutPath))
		{
			Fail(Out, FString::Printf(
				TEXT("%s already exists and overwrite:false. Pass overwrite:true or a different file."),
				*FullOutPath));
			return;
		}

		// Neither RunAssetExportTask's binary path nor the FBX SDK's WriteToFile creates the output
		// directory; without this a valid export silently produces no file.
		const FString OutDir = FPaths::GetPath(FullOutPath);
		if (!OutDir.IsEmpty() && !FPlatformFileManager::Get().GetPlatformFile().CreateDirectoryTree(*OutDir))
		{
			Fail(Out, FString::Printf(
				TEXT("could not create the output directory %s — check the path is valid and writable."), *OutDir));
			return;
		}

		// --- exporter -------------------------------------------------------------------------------
		UExporter* Exporter = UExporter::FindExporter(Asset, *Format);
		if (!Exporter)
		{
			Fail(Out, FString::Printf(
				TEXT("no exporter for class %s to '%s'. FindExporter matches a registered exporter CDO's ")
				TEXT("SupportsObject against its FormatExtension list (UnrealExporter.cpp:100-146). Formats ")
				TEXT("this editor can write for %s: %s"),
				*Asset->GetClass()->GetName(), *Format, *Asset->GetClass()->GetName(),
				*MifExportFormatsForObject(Asset)));
			return;
		}

		// GC LIFETIME, and it starts HERE — not at the Task rooting further down, which is what the
		// comment on that block used to imply. UExporter::FindExporter hands back a FRESH, UNROOTED
		// NewObject in the transient package (UnrealExporter.cpp:141) and nothing references it yet, so
		// a collection anywhere between that call and the Task->Exporter assignment would take it. The
		// window is not empty: NewObject<UAssetExportTask> allocates inside it. The engine's own
		// UExporter::ExportToFile guards its equivalent on the very next line for exactly this reason
		// (FGCObjectScopeGuard ExportTaskGuard(ExportTask), UnrealExporter.cpp:209-210), so this is the
		// engine's own remedy rather than an invention.
		FGCObjectScopeGuard ExporterGuard(Exporter);

		// BELT (hazard 1): even with bAutomated + a typed Options object, this makes
		// FFbxExporter::FillExportOptions early-return on its !bShowOptionDialog test
		// (FbxMainExport.cpp:188) if the Cast<UFbxExportOption> in GetAutomatedExportOptionsFbx ever
		// fails. UExporter's constructor defaults it to TRUE (UnrealExporter.cpp:54).
		Exporter->SetShowExportOption(false);

		const bool bAllLODs   = JBoolAny(In, { TEXT("levelOfDetail"), TEXT("lod") }, false);
		const bool bSourceMesh = JBool(In, TEXT("exportSourceMesh"), false);
		const bool bForceFrontX = JBool(In, TEXT("forceFrontXAxis"), false);
		const bool bIsFbx = (Format == TEXT("FBX"));

		// --- task -------------------------------------------------------------------------------
		// Created and ROOTED BEFORE the options object, not after it. That ordering is the point: the
		// options object can then be published into this rooted Task's UPROPERTY in the statement
		// immediately after its NewObject, so there is no window at all in which it is unreachable.
		// (Previously it was born, then had seven fields written, then a UAssetExportTask was allocated,
		// and only then was it assigned into Task — with a GC-capable allocation inside that window.)
		UAssetExportTask* Task = NewObject<UAssetExportTask>();
		Task->AddToRoot();
		ON_SCOPE_EXIT { Task->RemoveFromRoot(); };

		Task->Object            = Asset;
		Task->Exporter          = Exporter; // Exporter is reachable from HERE; ExporterGuard covers before
		Task->Filename          = FullOutPath;
		Task->bSelected         = false;
		Task->bReplaceIdentical = true;   // never the "not replacing because identical" early return
		                                  // (UnrealExporter.cpp:334-335 text, :382-383 binary) — which is
		                                  // also what makes the staleness test in hazard (3) sound
		Task->bPrompt           = false;  // INVARIANT — no GWarn->YesNof overwrite dialog (UnrealExporter.cpp:339/:387)
		Task->bAutomated        = true;   // INVARIANT — gate 1 of the FBX options modal. See the file header.
		Task->bUseFileArchive   = false;
		Task->bWriteEmptyFiles  = false;  // INVARIANT — true would clobber the real FBX with an empty buffer
		Task->Options           = nullptr;// INVARIANT — gate 2; filled in below for FBX. bAutomated ALONE
		                                  // is not enough, and a non-FBX exporter must see null, not a
		                                  // UFbxExportOption it would fail to Cast

		UFbxExportOption* Options = nullptr;
		if (bIsFbx)
		{
			// Parsed BEFORE anything is allocated, so a bad fbxCompatibility costs nothing.
			EFbxExportCompatibility Compatibility = EFbxExportCompatibility::FBX_2020;
			const FString CompatibilityText = JStr(In, TEXT("fbxCompatibility"));
			if (!CompatibilityText.IsEmpty())
			{
				FString Error;
				if (!MifExportParseFbxCompatibility(CompatibilityText, Compatibility, Error)) { Fail(Out, Error); return; }
			}

			// UFbxExportOption is MinimalAPI: NewObject links, its member FUNCTIONS do not. Every field
			// below is a plain UPROPERTY write; SaveOptions/LoadOptions/ResetToDefault are never called.
			// The publish into the already-rooted Task is the NEXT statement, before any field write and
			// before any other allocation — see the ordering note on the task block above.
			Options = NewObject<UFbxExportOption>();
			Task->Options = Options;

			Options->FbxExportCompatibility = Compatibility;
			Options->bASCII            = JBool(In, TEXT("ascii"), false);
			Options->bForceFrontXAxis  = bForceFrontX;                       // default false = -Y front
			Options->VertexColor       = JBool(In, TEXT("vertexColor"), true);
			Options->LevelOfDetail     = bAllLODs;
			Options->Collision         = JBool(In, TEXT("collision"), false);
			Options->bExportSourceMesh = bSourceMesh;
		}
		else if (JHasAny(In, { TEXT("fbxCompatibility"), TEXT("ascii"), TEXT("vertexColor"),
			TEXT("levelOfDetail"), TEXT("lod"), TEXT("collision"), TEXT("exportSourceMesh"),
			TEXT("forceFrontXAxis") }))
		{
			// Saying nothing here would be the silent-ignore bug class RejectUnknownParams exists to
			// kill: the keys ARE accepted, they just do not reach a non-FBX exporter.
			AddWarning(Out, FString::Printf(
				TEXT("FBX option fields were supplied but format is '%s' — they are UFbxExportOption fields and ")
				TEXT("only reach the FBX exporter. They had NO effect on this export."), *Format));
		}

		// --- expected output files (hazard 4) -------------------------------------------------------
		// FullOutPath is what we ASKED for; it is not necessarily what the engine writes. When
		// GetFileCount() > 1 the binary path writes GetUniqueFilename(Object, *Task->Filename, i,
		// FileCount) once per index (UnrealExporter.cpp:366 and :372). Both of those are PUBLIC non-API
		// virtuals with inline bodies (Exporter.h:139 and :144), so they link from this module without
		// naming any exporter class — the same reflection-not-linkage discipline as FindExporter.
		const int32 FileCount = Exporter->GetFileCount(Asset);
		if (FileCount < 1)
		{
			Fail(Out, FString::Printf(
				TEXT("exporter %s reported GetFileCount()=%d for %s. RunAssetExportTask's write loop would ")
				TEXT("run zero times and still return true (UnrealExporter.cpp:367-407), so the export would ")
				TEXT("produce nothing and say nothing. Refusing rather than reporting a success with no file."),
				*Exporter->GetClass()->GetName(), FileCount, *Asset->GetPathName()));
			return;
		}

		TArray<FMifExportFileImage> Files;
		if (FileCount == 1)
		{
			// The base GetUniqueFilename returns Filename unchanged for (0, 1) (Exporter.h:144-148), so
			// calling it here would be a no-op that only adds an assert to trip over. Skip it.
			FMifExportFileImage Image;
			Image.Path = FullOutPath;
			Files.Add(MoveTemp(Image));
		}
		else
		{
			// The base GetUniqueFilename does check(FileIndex == 0 && FileCount == 1) (Exporter.h:146),
			// so an exporter that returns > 1 without overriding it asserts here. That is NOT a new crash
			// class: RunAssetExportTask calls the identical function with the identical arguments a few
			// lines later (UnrealExporter.cpp:372), so such an exporter is already broken against the
			// engine — this only reaches the assert a moment sooner, with a stack that names MifBridge.
			int32 Collapsed = 0;
			for (int32 i = 0; i < FileCount; ++i)
			{
				FString Unique = Exporter->GetUniqueFilename(Asset, *FullOutPath, i, FileCount);
				FPaths::NormalizeFilename(Unique);
				Unique = FPaths::ConvertRelativePathToFull(Unique);

				const bool bDuplicate = Files.ContainsByPredicate(
					[&Unique](const FMifExportFileImage& Existing) { return Existing.Path == Unique; });
				if (bDuplicate) { ++Collapsed; continue; }

				FMifExportFileImage Image;
				Image.Path = MoveTemp(Unique);
				Files.Add(MoveTemp(Image));
			}
			if (Collapsed > 0)
			{
				// Not a refusal — the export still writes a real file, it just writes it repeatedly and
				// the last index wins. But it means N-1 of the "files" the exporter promised do not
				// exist, and silently summing one file N times would be a fabricated total.
				AddWarning(Out, FString::Printf(
					TEXT("exporter %s reported GetFileCount()=%d but GetUniqueFilename collapsed %d of those ")
					TEXT("indices onto paths already in the list. Each collapsed index overwrites the same file, ")
					TEXT("so only %d distinct file(s) can exist and only those are verified below."),
					*Exporter->GetClass()->GetName(), FileCount, Collapsed, Files.Num()));
			}
		}

		if (!bOverwrite && FileCount > 1)
		{
			// The overwrite check further up could only test FullOutPath; the real destinations are
			// knowable only now that the exporter has been resolved. Checking one of N and calling it an
			// overwrite guard would be the honest-looking half-check this file exists to avoid.
			for (const FMifExportFileImage& Image : Files)
			{
				if (IFileManager::Get().FileExists(*Image.Path))
				{
					Fail(Out, FString::Printf(
						TEXT("%s already exists and overwrite:false. This is a MULTI-FILE export (%s writes %d ")
						TEXT("files derived from the name you passed, UnrealExporter.cpp:366-372), so the file that ")
						TEXT("collides is not necessarily the one you named. Pass overwrite:true or a different file."),
						*Image.Path, *Exporter->GetClass()->GetName(), FileCount));
					return;
				}
			}
		}

		// ---------------------- PRE-IMAGE (hazard 3) ----------------------
		// Photograph every expected destination BEFORE the export. Without this the stat afterwards
		// cannot distinguish a file written just now from one left behind by an earlier run, and a
		// failed export over a deterministic path would answer ok:true with yesterday's byte count.
		for (FMifExportFileImage& Image : Files)
		{
			Image.CaptureBefore();
		}

		const bool bRan = UExporter::RunAssetExportTask(Task);

		for (FMifExportFileImage& Image : Files)
		{
			Image.CaptureAfter();
		}

		// ---------------------- VERIFY AFTER WRITE ----------------------
		// bRan is NOT the answer. RunAssetExportTask returns true on three paths that write nothing
		// (UnrealExporter.cpp:320-323, :394-397, :364-407) — hazard (2). And a bare stat is not the
		// answer either, because nothing on those paths deletes the destination and the default path is
		// deterministic, so a stale file from a previous run answers every question the same way a fresh
		// one does — hazard (3). The verdict is therefore "did each expected file MOVE", measured
		// against the pre-image captured above.
		int32 NumWritten = 0, NumStale = 0, NumMissing = 0, NumEmpty = 0;
		int64 TotalBytes = 0;
		const FMifExportFileImage* FirstWritten = nullptr;
		FString Breakdown;
		for (const FMifExportFileImage& Image : Files)
		{
			if (Image.WasWritten())
			{
				++NumWritten;
				TotalBytes += Image.PostBytes;
				if (!FirstWritten) { FirstWritten = &Image; }
			}
			else if (!Image.bPostExists) { ++NumMissing; }
			else if (Image.PostBytes <= 0) { ++NumEmpty; }
			else { ++NumStale; }

			Breakdown += TEXT("\n  - ") + Image.Describe();
		}

		if (!bRan || NumWritten == 0)
		{
			FString ExporterErrors;
			for (const FString& Error : Task->Errors)
			{
				ExporterErrors += TEXT("\n  - ") + Error;
			}

			// The stale case gets its own opening sentence because it is the one that used to be
			// reported as a success, and because its remedy is different: nothing is wrong with the
			// path or the permissions, the exporter simply did not write.
			const FString Lead = (NumStale > 0 && NumMissing == 0 && NumEmpty == 0)
				? FString::Printf(
					TEXT("export of %s to %s reported success but WROTE NOTHING — the file on disk is byte-for-byte ")
					TEXT("and timestamp-for-timestamp what it was before this call, i.e. it is left over from an ")
					TEXT("earlier export. RunAssetExportTask returns true on three paths that write no file and ")
					TEXT("deletes the destination on none of them (UnrealExporter.cpp:320-323, :394-397, :364-407). ")
					TEXT("bReplaceIdentical is true here, so the engine's 'not replacing because identical' early ")
					TEXT("returns are unreachable and this cannot be an intentional skip. The one remaining ")
					TEXT("possibility, if you believe the export really did run, is a rewrite of identical bytes ")
					TEXT("inside the filesystem's timestamp granularity — delete the file and call again to settle ")
					TEXT("it. This is reported as a FAILURE on purpose: answering ok:true over the previous run's ")
					TEXT("file is the exact bug this endpoint was written to prevent."),
					*Asset->GetPathName(), *FullOutPath)
				: FString::Printf(
					TEXT("export of %s to %s produced no usable file. RunAssetExportTask returns true even when it ")
					TEXT("writes nothing, so the FILE — not the return value — is the verdict. Common causes: the ")
					TEXT("exporter was matched by extension but cannot serialise this object, the mesh has no render ")
					TEXT("data, or the path is not writable."),
					*Asset->GetPathName(), *FullOutPath);

			Fail(Out, FString::Printf(
				TEXT("%s (RunAssetExportTask=%s, expected %d file(s) from %s: %d written, %d stale, %d missing, ")
				TEXT("%d empty). Expected files:%s%s"),
				*Lead,
				bRan ? TEXT("true") : TEXT("false"),
				Files.Num(), *Exporter->GetClass()->GetName(),
				NumWritten, NumStale, NumMissing, NumEmpty,
				*Breakdown,
				ExporterErrors.IsEmpty()
					? TEXT("\nThe exporter recorded no errors of its own — check the Output Log (LogExporter).")
					: *(TEXT("\nExporter errors:") + ExporterErrors)));
			return;
		}

		// --- response -------------------------------------------------------------------------------
		// objectPath + packageName through the ONE shared writer, so this endpoint cannot spell the
		// asset-identity fields differently from every other asset-emitting endpoint.
		EmitAssetIdentity(Out, Asset->GetPathName(), AssetPackage->GetName());
		Out->SetStringField(TEXT("assetClass"), Asset->GetClass()->GetName());
		Out->SetStringField(TEXT("format"), Format);
		Out->SetStringField(TEXT("exporterClass"), Exporter->GetClass()->GetName());

		// `file` names a file that was PROVABLY WRITTEN by this call, never merely one that exists. For
		// the single-file case — every StaticMesh->FBX export, which is the only path this endpoint
		// verifies end to end — that is FullOutPath and the shape is unchanged from before. For a
		// multi-file exporter it is the first written member of files[], which is not necessarily
		// index 0: a 5.1 surround sound writes no _fl file if that channel is empty.
		Out->SetStringField(TEXT("file"), FirstWritten->Path);
		Out->SetBoolField(TEXT("fileExists"), true);
		Out->SetNumberField(TEXT("fileSizeBytes"), (double)FirstWritten->PostBytes);
		Out->SetNumberField(TEXT("fileCount"), Files.Num());
		Out->SetNumberField(TEXT("filesWritten"), NumWritten);
		Out->SetNumberField(TEXT("totalFileSizeBytes"), (double)TotalBytes);

		// files[] is emitted ALWAYS, not only when there is more than one, so a consumer has a single
		// shape to parse and never has to infer the multi-file case from the absence of a key.
		{
			TArray<TSharedPtr<FJsonValue>> FileRows;
			for (const FMifExportFileImage& Image : Files)
			{
				TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
				Row->SetStringField(TEXT("file"), Image.Path);
				Row->SetBoolField(TEXT("written"), Image.WasWritten());
				Row->SetStringField(TEXT("verdict"), Image.Verdict());
				Row->SetBoolField(TEXT("existedBefore"), Image.bPreExisted);
				Row->SetNumberField(TEXT("fileSizeBytes"), (double)Image.PostBytes);
				Row->SetNumberField(TEXT("fileSizeBytesBefore"), (double)Image.PreBytes);
				FileRows.Add(MakeShared<FJsonValueObject>(Row));
			}
			Out->SetArrayField(TEXT("files"), FileRows);
		}

		if (NumWritten < Files.Num())
		{
			// Partial success. Real for surround audio and for a UDIM whose exporter skips a block; a
			// silent pass would leave the caller believing it has N files when it has fewer.
			AddWarning(Out, FString::Printf(
				TEXT("%s expected %d output file(s) but only %d were written this call (%d stale, %d missing, ")
				TEXT("%d empty). See files[] for the per-file verdict. A 'stale' entry is a file left over from ")
				TEXT("an earlier export that this call did NOT touch — do not treat it as output."),
				*Exporter->GetClass()->GetName(), Files.Num(), NumWritten, NumStale, NumMissing, NumEmpty));
		}

		Out->SetNumberField(TEXT("elapsedMs"), (FPlatformTime::Seconds() - StartSeconds) * 1000.0);

		if (bIsFbx)
		{
			TSharedRef<FJsonObject> Axis = MakeShared<FJsonObject>();
			Axis->SetStringField(TEXT("up"), TEXT("Z"));
			Axis->SetStringField(TEXT("front"), bForceFrontX ? TEXT("X") : TEXT("-Y"));
			Axis->SetStringField(TEXT("handedness"), TEXT("right"));
			Axis->SetStringField(TEXT("unit"), TEXT("cm"));
			Axis->SetStringField(TEXT("source"), TEXT("FbxMainExport.cpp:268-276"));
			Out->SetObjectField(TEXT("axis"), Axis);

			// The FBX version that was actually written. Reported unconditionally — including when the
			// caller omitted the parameter — because this endpoint's default (FBX_2020) is NOT the
			// engine's (UFbxExportOption's constructor sets FBX_2013, FbxExportOption.cpp:21). Without
			// this field the only way to know which one a file carries is to read this source.
			Out->SetStringField(TEXT("fbxCompatibility"),
				MifExportFbxCompatibilityName(Options->FbxExportCompatibility));
		}

		if (StaticMesh)
		{
			MifExportEmitStaticMeshFacts(StaticMesh, Out, bAllLODs);
		}
		else
		{
			// HONESTY, not a refusal. FindExporter resolved a real exporter for this class, so the export
			// is legitimate — but StaticMesh -> FBX is the only pair this endpoint was written and
			// reasoned about, and the caller should know the difference between "verified" and "passed
			// through". No mesh{} block is emitted for a non-mesh, so there is nothing to assert against.
			AddWarning(Out, FString::Printf(
				TEXT("%s is not a UStaticMesh. The export ran through %s and the file is on disk, but ")
				TEXT("StaticMesh->FBX is the only path this endpoint verifies: no mesh{} pre-image is reported, ")
				TEXT("no axis/unit guarantee is made, and per-format quirks are not handled."),
				*Asset->GetClass()->GetName(), *Exporter->GetClass()->GetName()));
		}

		// Per-format quirks the caller cannot see from the response alone.
		if (Format == TEXT("OBJ"))
		{
			AddWarning(Out, TEXT("OBJ is a lossy target for a round trip: UStaticMeshExporterOBJ swaps Y/Z ")
				TEXT("(EditorExporters.cpp:1971), de-indexes to three verts per triangle with no welding, writes ")
				TEXT("no vertex normals, and emits two extra sidecar files beside the one reported here. Use FBX ")
				TEXT("for anything that has to come back in."));
		}
		if (bForceFrontX)
		{
			AddWarning(Out, TEXT("forceFrontXAxis:true rotates the exported scene to an X-front axis system ")
				TEXT("(FbxMainExport.cpp:270). Re-importing it into UE, or tiling it along a spline, will be ")
				TEXT("rotated relative to every other asset unless the same flag is used consistently."));
		}
		if (bSourceMesh && StaticMesh)
		{
			AddWarning(Out, TEXT("exportSourceMesh:true exports the highest-LOD SOURCE data instead of render ")
				TEXT("data, and disables the LOD and collision options. It silently disables ITSELF on an asset ")
				TEXT("with no valid MeshDescription — which is every cooked mesh (FbxMainExport.cpp:4910-4913) — ")
				TEXT("so on cooked content this flag is a no-op, not an error."));
		}
		if (bAllLODs)
		{
			AddWarning(Out, TEXT("levelOfDetail:true writes every LOD into one FBX. Round-tripping that back ")
				TEXT("through import_asset will not reproduce a single-mesh asset cleanly — export with ")
				TEXT("levelOfDetail:false (LOD0 only) for an edit-and-reimport workflow."));
		}
		if (Task->Errors.Num() > 0)
		{
			// The export produced a real file, so this is a warning and not a Fail — but the exporter said
			// something and swallowing it would be exactly the silent-ignore failure mode this file argues
			// against in its own header.
			AddWarning(Out, FString::Printf(TEXT("the exporter recorded %d non-fatal message(s): %s"),
				Task->Errors.Num(), *FString::Join(Task->Errors, TEXT(" | "))));
		}

		UE_LOG(LogMifBridge, Log, TEXT("export_asset: %s -> %s via %s (%s, %d/%d file(s) written, %lld bytes)"),
			*Asset->GetPathName(), *FirstWritten->Path, *Exporter->GetClass()->GetName(), *Format,
			NumWritten, Files.Num(), (long long)TotalBytes);
	}
}
