// GeometryScript — procedural mesh creation and geometry introspection.
//
// The first of the "everything else on demand" family from docs/13_COMPETITOR_GAP_MAP.md to actually
// get built, at Andre's direct request for parity with the Fab marketplace competitor that document
// analyses. "Procedural mesh work" was that doc's own one-line description of the value here, and it
// is a genuinely distinctive capability MifBridge had NOTHING for before this file: every existing
// mesh-touching endpoint (get_collision, add_simplified_collision, duplicate_asset) reads or copies an
// EXISTING mesh. Nothing generates one from nothing. Curfew is a roguelike; procedural generation is
// not a niche use case for that project, it is close to the point of it.
//
// GUARDED BY MIF_WITH_GEOMETRYSCRIPT, same pattern as GameFeatures: registered on every engine,
// compiling a named refusal where the plugin is absent. UDynamicMesh/FDynamicMesh3 themselves are
// UNCONDITIONAL dependencies (GeometryFramework/GeometryCore, added to Build.cs alongside this file) -
// they are engine RUNTIME modules, not the GeometryScripting PLUGIN, so they cannot be the thing
// missing. Only the Blueprint-function-library wrappers (AppendBox, CopyMeshToStaticMesh, ...) live in
// the optional plugin.
//
// ============================================================================================
// THE VERSION SPLIT IN THIS FILE
// ============================================================================================
// CopyMeshToStaticMesh grew a bUseSectionMaterials parameter in 5.5 (MeshAssetFunctions.h; the OLD
// 6-arg overload is UE_DEPRECATED(5.5, ...) on 5.7, not removed - it still compiles, just warns).
// AppendBox and AppendSphereLatLong are IDENTICAL in both trees; only the asset-write call needs the
// guard. Verified by reading both headers directly, not assumed from the deprecation tag alone.
//
// WHY A NEW STATIC MESH RATHER THAN EDITING AN EXISTING ONE. CopyMeshToStaticMesh happily accepts a
// FRESH NewObject<UStaticMesh>() with zero prior SourceModel setup - it calls SetNumSourceModels,
// CreateMeshDescription, CommitMeshDescription and PostEditChange itself (verified by reading
// MeshAssetFunctions.cpp, not assumed). That is the OPPOSITE of the duplicate_asset/
// add_simplified_collision crash class this project has hit twice before: those crashed because a
// COOKED mesh's MeshDescription was already stripped and something dereferenced it anyway. Here there
// is no prior state to be missing - a brand-new mesh legitimately has none, and CopyMeshToStaticMesh
// is written to create it. It also refuses to touch a `/Engine/` built-in asset outright, which this
// endpoint does not need since it only ever writes to a freshly created, caller-named path.
//
// SCRATCH-SAFE BY DESIGN. Nothing here is saved - the created StaticMesh is registered
// (FAssetRegistryModule::AssetCreated) exactly like create_datatable/create_struct, and vanishes on
// editor restart the same way, matching this whole project's standing invariant.

#include "MifBridgeHandlers.h"
#include "MifBridgeVersion.h"
#include "MifBridgeLog.h"

#if MIF_WITH_GEOMETRYSCRIPT
#include "UDynamicMesh.h"
#include "GeometryScript/MeshPrimitiveFunctions.h"
#include "GeometryScript/MeshAssetFunctions.h"
#include "GeometryScript/MeshBooleanFunctions.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Engine/StaticMesh.h"
#include "Misc/PackageName.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_GEOMETRYSCRIPT
	static void MifNoGeometryScript(const TSharedRef<FJsonObject>& Out)
	{
		Fail(Out, TEXT("this engine build has no GeometryScripting plugin, so there is nothing to "
					   "generate or read. The endpoint exists on every build deliberately - a missing "
					   "endpoint would tell you nothing, while this tells you the plugin is what is "
					   "missing."));
	}
	void H_create_procedural_mesh(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoGeometryScript(Out);
	}
	void H_describe_dynamic_mesh(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoGeometryScript(Out);
	}
	void H_create_mesh_boolean(const TSharedRef<FJsonObject>&, const TSharedRef<FJsonObject>& Out)
	{
		MifNoGeometryScript(Out);
	}
#else

	namespace
	{
		// Same shape as create_datatable/create_struct's own local validator (MifBridgeUserTypes.cpp) -
		// each domain in this codebase keeps its own, rather than a shared one nobody owns the contract
		// of. Not a new pattern, the established one.
		bool ValidateNewMeshAssetPath(const FString& Path, FString& OutAssetName, FString& OutError,
			const TCHAR* CallerName = TEXT("create_procedural_mesh"))
		{
			if (Path.IsEmpty())
			{
				OutError = TEXT("path is required (must start with /Game/), e.g. \"/Game/Meshes/SM_MyBox\"");
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
			// NOT plain FPackageName::DoesPackageExist - live-tested and confirmed to give the WRONG
			// answer in this cooked-editor mod-kit setup, the same pitfall H_create_asset's own
			// destination check already documents (MifBridgeUserTypes.cpp): it consults the
			// IoDispatcher/pak container as well as the filesystem, so a scratch mesh created earlier
			// THIS SESSION and never saved reads as "does not exist" - and this endpoint went on to
			// silently overwrite it, verified live before this fix. Two checks instead: a real file on
			// disk, or an object already loaded in memory at this path (which covers the in-session,
			// never-saved case create_procedural_mesh itself produces).
			const bool bOnDisk = FPackageName::DoesPackageExistEx(
				FPackagePath::FromPackageNameChecked(Path),
				FPackageName::EPackageLocationFilter::FileSystem) != FPackageName::EPackageLocationFilter::None;
			UObject* Existing = FindObject<UObject>(nullptr, *(Path + TEXT(".") + OutAssetName));
			if (bOnDisk || Existing)
			{
				OutError = FString::Printf(
					TEXT("'%s' is already taken (%s) - %s never overwrites. ")
					TEXT("delete_asset the existing one first or pick another path. NOTHING was created."),
					*Path, bOnDisk ? TEXT("a package file exists on disk")
								   : TEXT("an object is already loaded there"), CallerName);
				return false;
			}
			return true;
		}

		// Reads the messages GeometryScript's own Debug object collected, regardless of whether the
		// call reported Success or Failure - a Success can still carry warnings worth surfacing, the
		// same reasoning list_ik_rig's own problems[] array is built on.
		void AppendDebugMessages(const UGeometryScriptDebug* Debug, TArray<TSharedPtr<FJsonValue>>& OutArr)
		{
			if (!Debug) { return; }
			for (const FGeometryScriptDebugMessage& Msg : Debug->Messages)
			{
				OutArr.Add(MakeShared<FJsonValueString>(Msg.Message.ToString()));
			}
		}

		// Shared by create_mesh_boolean's target/tool reads - both need the identical
		// LoadObject-then-CopyMeshFromStaticMesh sequence describe_dynamic_mesh already established.
		// Not shared WITH describe_dynamic_mesh itself: that handler's response shape (lod, isClosed)
		// differs enough that factoring it in too would trade one duplication for a worse one.
		bool ReadStaticMeshIntoDynamicMesh(const FString& Path, UDynamicMesh* DynMesh,
			UGeometryScriptDebug* Debug, FString& OutError)
		{
			UStaticMesh* SourceMesh = LoadObject<UStaticMesh>(nullptr, *Path);
			if (!SourceMesh)
			{
				OutError = FString::Printf(TEXT("no StaticMesh at '%s'"), *Path);
				return false;
			}
			FGeometryScriptCopyMeshFromAssetOptions ReadOptions;
			FGeometryScriptMeshReadLOD ReadLOD;
			EGeometryScriptOutcomePins Outcome = EGeometryScriptOutcomePins::Failure;
			UGeometryScriptLibrary_StaticMeshFunctions::CopyMeshFromStaticMesh(
				SourceMesh, DynMesh, ReadOptions, ReadLOD, Outcome, Debug);
			if (Outcome != EGeometryScriptOutcomePins::Success)
			{
				OutError = FString::Printf(
					TEXT("CopyMeshFromStaticMesh reported failure reading '%s' - a cooked mesh's ")
					TEXT("editor-only MeshDescription is stripped, which is the usual cause."), *Path);
				return false;
			}
			return true;
		}
	}

	// --- create_procedural_mesh ----------------------------------------------------------------
	//   in:  { path, shape: box|sphere, dimensionX/Y/Z? (box), radius? (sphere), stepsPhi?/stepsTheta?
	//          (sphere), steps? (box subdivision, same value on all three axes) }
	//   out: { assetPath, shape, vertexCount, triangleCount, bounds }
	void H_create_procedural_mesh(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("shape"),
			  TEXT("dimensionX"), TEXT("dimensionY"), TEXT("dimensionZ"), TEXT("steps"),
			  TEXT("radius"), TEXT("stepsPhi"), TEXT("stepsTheta"),
			  TEXT("height"), TEXT("radialSteps"), TEXT("heightSteps"), TEXT("capped"),
			  TEXT("baseRadius"), TEXT("topRadius"),
			  TEXT("majorRadius"), TEXT("minorRadius"), TEXT("majorSteps"), TEXT("minorSteps") },
			TEXT("path (alias: assetPath) - where to create the new StaticMesh; ")
			TEXT("shape (box|sphere|cylinder|cone|torus); ")
			TEXT("box: dimensionX/Y/Z (default 100 each), steps (subdivision on all three axes, default 0); ")
			TEXT("sphere: radius (default 50), stepsPhi/stepsTheta (default 10/16); ")
			TEXT("cylinder: radius (default 50), height (default 100), radialSteps (default 12), ")
			TEXT("heightSteps (default 0), capped (default true); ")
			TEXT("cone: baseRadius (default 50), topRadius (default 5), height (default 100), ")
			TEXT("radialSteps (default 12), heightSteps (default 4), capped (default true); ")
			TEXT("torus: majorRadius (default 50), minorRadius (default 25), majorSteps (default 16), ")
			TEXT("minorSteps (default 8)"),
			{ { TEXT("class"), TEXT("this always creates a StaticMesh - there is no other class here") },
			  { TEXT("size"), TEXT("box takes dimensionX/dimensionY/dimensionZ, not a single size") } }))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath") });
		FString AssetName, PathError;
		if (!ValidateNewMeshAssetPath(Path, AssetName, PathError))
		{
			Fail(Out, PathError);
			return;
		}

		const FString Shape = JStr(In, TEXT("shape")).ToLower();
		static const TCHAR* KnownShapes[] = { TEXT("box"), TEXT("sphere"), TEXT("cylinder"), TEXT("cone"), TEXT("torus") };
		bool bKnownShape = false;
		for (const TCHAR* S : KnownShapes) { if (Shape == S) { bKnownShape = true; break; } }
		if (!bKnownShape)
		{
			Fail(Out, FString::Printf(
				TEXT("shape '%s' is not one of box, sphere, cylinder, cone, torus. NOTHING was created."), *Shape));
			return;
		}

		// Transient: this is scratch working memory for the generator, never the asset itself.
		UDynamicMesh* DynMesh = NewObject<UDynamicMesh>(GetTransientPackage(), NAME_None, RF_Transient);
		if (!DynMesh)
		{
			Fail(Out, TEXT("failed to allocate a working UDynamicMesh"));
			return;
		}

		UGeometryScriptDebug* Debug = NewObject<UGeometryScriptDebug>(GetTransientPackage(), NAME_None, RF_Transient);
		FGeometryScriptPrimitiveOptions PrimitiveOptions;
		EGeometryScriptOutcomePins Outcome = EGeometryScriptOutcomePins::Failure;

		if (Shape == TEXT("box"))
		{
			const double DimX = JNum(In, TEXT("dimensionX"), 100.0);
			const double DimY = JNum(In, TEXT("dimensionY"), 100.0);
			const double DimZ = JNum(In, TEXT("dimensionZ"), 100.0);
			const int32 Steps = FMath::Clamp(JInt(In, TEXT("steps"), 0), 0, 64);
			if (DimX <= 0.0 || DimY <= 0.0 || DimZ <= 0.0)
			{
				Fail(Out, TEXT("dimensionX/Y/Z must all be greater than 0. NOTHING was created."));
				return;
			}
			UGeometryScriptLibrary_MeshPrimitiveFunctions::AppendBox(
				DynMesh, PrimitiveOptions, FTransform::Identity,
				(float)DimX, (float)DimY, (float)DimZ, Steps, Steps, Steps,
				EGeometryScriptPrimitiveOriginMode::Center, Debug);
		}
		else if (Shape == TEXT("sphere"))
		{
			const double Radius = JNum(In, TEXT("radius"), 50.0);
			const int32 StepsPhi = FMath::Clamp(JInt(In, TEXT("stepsPhi"), 10), 3, 128);
			const int32 StepsTheta = FMath::Clamp(JInt(In, TEXT("stepsTheta"), 16), 3, 128);
			if (Radius <= 0.0)
			{
				Fail(Out, TEXT("radius must be greater than 0. NOTHING was created."));
				return;
			}
			UGeometryScriptLibrary_MeshPrimitiveFunctions::AppendSphereLatLong(
				DynMesh, PrimitiveOptions, FTransform::Identity,
				(float)Radius, StepsPhi, StepsTheta,
				EGeometryScriptPrimitiveOriginMode::Center, Debug);
		}
		else if (Shape == TEXT("cylinder"))
		{
			const double Radius = JNum(In, TEXT("radius"), 50.0);
			const double Height = JNum(In, TEXT("height"), 100.0);
			const int32 RadialSteps = FMath::Clamp(JInt(In, TEXT("radialSteps"), 12), 3, 128);
			const int32 HeightSteps = FMath::Clamp(JInt(In, TEXT("heightSteps"), 0), 0, 64);
			const bool bCapped = JBool(In, TEXT("capped"), true);
			if (Radius <= 0.0 || Height <= 0.0)
			{
				Fail(Out, TEXT("radius and height must both be greater than 0. NOTHING was created."));
				return;
			}
			UGeometryScriptLibrary_MeshPrimitiveFunctions::AppendCylinder(
				DynMesh, PrimitiveOptions, FTransform::Identity,
				(float)Radius, (float)Height, RadialSteps, HeightSteps, bCapped,
				EGeometryScriptPrimitiveOriginMode::Center, Debug);
		}
		else if (Shape == TEXT("cone"))
		{
			const double BaseRadius = JNum(In, TEXT("baseRadius"), 50.0);
			const double TopRadius = JNum(In, TEXT("topRadius"), 5.0);
			const double Height = JNum(In, TEXT("height"), 100.0);
			const int32 RadialSteps = FMath::Clamp(JInt(In, TEXT("radialSteps"), 12), 3, 128);
			const int32 HeightSteps = FMath::Clamp(JInt(In, TEXT("heightSteps"), 4), 1, 64);
			const bool bCapped = JBool(In, TEXT("capped"), true);
			if (BaseRadius < 0.0 || TopRadius < 0.0 || Height <= 0.0)
			{
				Fail(Out, TEXT("baseRadius/topRadius must not be negative and height must be greater than 0. NOTHING was created."));
				return;
			}
			if (BaseRadius == 0.0 && TopRadius == 0.0)
			{
				Fail(Out, TEXT("baseRadius and topRadius cannot both be 0 - that is a degenerate line, not a cone. NOTHING was created."));
				return;
			}
			UGeometryScriptLibrary_MeshPrimitiveFunctions::AppendCone(
				DynMesh, PrimitiveOptions, FTransform::Identity,
				(float)BaseRadius, (float)TopRadius, (float)Height, RadialSteps, HeightSteps, bCapped,
				EGeometryScriptPrimitiveOriginMode::Center, Debug);
		}
		else // torus
		{
			const double MajorRadius = JNum(In, TEXT("majorRadius"), 50.0);
			const double MinorRadius = JNum(In, TEXT("minorRadius"), 25.0);
			const int32 MajorSteps = FMath::Clamp(JInt(In, TEXT("majorSteps"), 16), 3, 128);
			const int32 MinorSteps = FMath::Clamp(JInt(In, TEXT("minorSteps"), 8), 3, 128);
			if (MajorRadius <= 0.0 || MinorRadius <= 0.0)
			{
				Fail(Out, TEXT("majorRadius and minorRadius must both be greater than 0. NOTHING was created."));
				return;
			}
			if (MinorRadius >= MajorRadius)
			{
				Fail(Out, FString::Printf(
					TEXT("minorRadius (%f) must be less than majorRadius (%f) - otherwise the tube overlaps ")
					TEXT("itself through the center. NOTHING was created."), MinorRadius, MajorRadius));
				return;
			}
			FGeometryScriptRevolveOptions RevolveOptions;
			UGeometryScriptLibrary_MeshPrimitiveFunctions::AppendTorus(
				DynMesh, PrimitiveOptions, FTransform::Identity, RevolveOptions,
				(float)MajorRadius, (float)MinorRadius, MajorSteps, MinorSteps,
				EGeometryScriptPrimitiveOriginMode::Center, Debug);
		}

		// Append* has no Outcome/failure pin of its own (generating a primitive procedurally cannot
		// fail the way copying from an external asset can) - verified by reading the header signature,
		// not assumed. The real fallibility in this endpoint is entirely in the write below.
		int32 VertCount = 0, TriCount = 0;
		FBox Bounds(EForceInit::ForceInit);
		DynMesh->ProcessMesh([&](const UE::Geometry::FDynamicMesh3& Mesh)
		{
			VertCount = Mesh.VertexCount();
			TriCount = Mesh.TriangleCount();
			const UE::Geometry::FAxisAlignedBox3d B = Mesh.GetBounds();
			Bounds = FBox(FVector(B.Min), FVector(B.Max));
		});
		if (VertCount == 0 || TriCount == 0)
		{
			TArray<TSharedPtr<FJsonValue>> Msgs;
			AppendDebugMessages(Debug, Msgs);
			Out->SetArrayField(TEXT("debugMessages"), Msgs);
			Fail(Out, FString::Printf(
				TEXT("the generator produced an empty mesh (vertices=%d, triangles=%d) - see debugMessages. ")
				TEXT("NOTHING was created."), VertCount, TriCount));
			return;
		}

		UPackage* Package = CreatePackage(*Path);
		if (!Package)
		{
			Fail(Out, FString::Printf(TEXT("failed to create package '%s'"), *Path));
			return;
		}
		UStaticMesh* TargetMesh = NewObject<UStaticMesh>(
			Package, FName(*AssetName), RF_Public | RF_Standalone | RF_Transactional);
		if (!TargetMesh)
		{
			Fail(Out, TEXT("failed to allocate the new StaticMesh"));
			return;
		}

		FGeometryScriptCopyMeshToAssetOptions CopyOptions;
		FGeometryScriptMeshWriteLOD WriteLOD;
		EGeometryScriptOutcomePins WriteOutcome = EGeometryScriptOutcomePins::Failure;
#if MIF_ENGINE_AT_LEAST(5, 5)
		// 5.5+ grew a bUseSectionMaterials parameter (MeshAssetFunctions.h); the pre-5.5 6-arg overload
		// still compiles here but is UE_DEPRECATED(5.5, ...) - call the new one explicitly instead of
		// letting this file start life with a fresh deprecation warning on 5.7.
		UGeometryScriptLibrary_StaticMeshFunctions::CopyMeshToStaticMesh(
			DynMesh, TargetMesh, CopyOptions, WriteLOD, WriteOutcome, /*bUseSectionMaterials*/ true, Debug);
#else
		UGeometryScriptLibrary_StaticMeshFunctions::CopyMeshToStaticMesh(
			DynMesh, TargetMesh, CopyOptions, WriteLOD, WriteOutcome, Debug);
#endif

		if (WriteOutcome != EGeometryScriptOutcomePins::Success)
		{
			TArray<TSharedPtr<FJsonValue>> Msgs;
			AppendDebugMessages(Debug, Msgs);
			Out->SetArrayField(TEXT("debugMessages"), Msgs);
			Fail(Out, TEXT("CopyMeshToStaticMesh reported failure - see debugMessages. The package was ")
						  TEXT("created but the asset was never registered, so it will not appear in ")
						  TEXT("find_assets."));
			return;
		}

		FAssetRegistryModule::AssetCreated(TargetMesh);
		Package->MarkPackageDirty();

		Out->SetStringField(TEXT("assetPath"), TargetMesh->GetPathName());
		Out->SetStringField(TEXT("shape"), Shape);
		Out->SetNumberField(TEXT("vertexCount"), VertCount);
		Out->SetNumberField(TEXT("triangleCount"), TriCount);
		TSharedRef<FJsonObject> BoundsJson = MakeShared<FJsonObject>();
		BoundsJson->SetNumberField(TEXT("sizeX"), Bounds.GetSize().X);
		BoundsJson->SetNumberField(TEXT("sizeY"), Bounds.GetSize().Y);
		BoundsJson->SetNumberField(TEXT("sizeZ"), Bounds.GetSize().Z);
		Out->SetObjectField(TEXT("bounds"), BoundsJson);
		UE_LOG(LogMifBridge, Log, TEXT("create_procedural_mesh: %s (%s, %d verts, %d tris)"),
			*TargetMesh->GetPathName(), *Shape, VertCount, TriCount);
	}

	// --- describe_dynamic_mesh -------------------------------------------------------------------
	//   in:  { path (a REAL StaticMesh asset), lod? (default 0) }
	//   out: { vertexCount, triangleCount, bounds, isClosed }
	// READ-ONLY: converts the asset's MeshDescription to a transient DynamicMesh purely to read it
	// back through FDynamicMesh3's own query methods. Nothing is written to the source asset - this
	// exercises CopyMeshFromStaticMesh, not CopyMeshToStaticMesh.
	void H_describe_dynamic_mesh(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("lod") },
			TEXT("path (alias: assetPath) - a StaticMesh asset; lod (default 0)"),
			{ { TEXT("mesh"), TEXT("the parameter is path/assetPath") } }))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required (a StaticMesh asset)"));
			return;
		}
		UStaticMesh* SourceMesh = LoadObject<UStaticMesh>(nullptr, *Path);
		if (!SourceMesh)
		{
			Fail(Out, FString::Printf(TEXT("no StaticMesh at '%s'"), *Path));
			return;
		}

		UDynamicMesh* DynMesh = NewObject<UDynamicMesh>(GetTransientPackage(), NAME_None, RF_Transient);
		UGeometryScriptDebug* Debug = NewObject<UGeometryScriptDebug>(GetTransientPackage(), NAME_None, RF_Transient);
		FGeometryScriptCopyMeshFromAssetOptions ReadOptions;
		FGeometryScriptMeshReadLOD ReadLOD;
		ReadLOD.LODIndex = FMath::Max(JInt(In, TEXT("lod"), 0), 0);
		EGeometryScriptOutcomePins Outcome = EGeometryScriptOutcomePins::Failure;

		UGeometryScriptLibrary_StaticMeshFunctions::CopyMeshFromStaticMesh(
			SourceMesh, DynMesh, ReadOptions, ReadLOD, Outcome, Debug);

		if (Outcome != EGeometryScriptOutcomePins::Success)
		{
			TArray<TSharedPtr<FJsonValue>> Msgs;
			AppendDebugMessages(Debug, Msgs);
			Out->SetArrayField(TEXT("debugMessages"), Msgs);
			Fail(Out, FString::Printf(
				TEXT("CopyMeshFromStaticMesh reported failure reading '%s' at LOD %d - see debugMessages. ")
				TEXT("A cooked mesh's editor-only MeshDescription is stripped, which is the usual cause."),
				*Path, ReadLOD.LODIndex));
			return;
		}

		int32 VertCount = 0, TriCount = 0;
		bool bClosed = false;
		FBox Bounds(EForceInit::ForceInit);
		DynMesh->ProcessMesh([&](const UE::Geometry::FDynamicMesh3& Mesh)
		{
			VertCount = Mesh.VertexCount();
			TriCount = Mesh.TriangleCount();
			bClosed = Mesh.IsClosed();
			const UE::Geometry::FAxisAlignedBox3d B = Mesh.GetBounds();
			Bounds = FBox(FVector(B.Min), FVector(B.Max));
		});

		Out->SetStringField(TEXT("assetPath"), SourceMesh->GetPathName());
		Out->SetNumberField(TEXT("lod"), ReadLOD.LODIndex);
		Out->SetNumberField(TEXT("vertexCount"), VertCount);
		Out->SetNumberField(TEXT("triangleCount"), TriCount);
		Out->SetBoolField(TEXT("isClosed"), bClosed);
		TSharedRef<FJsonObject> BoundsJson = MakeShared<FJsonObject>();
		BoundsJson->SetNumberField(TEXT("sizeX"), Bounds.GetSize().X);
		BoundsJson->SetNumberField(TEXT("sizeY"), Bounds.GetSize().Y);
		BoundsJson->SetNumberField(TEXT("sizeZ"), Bounds.GetSize().Z);
		Out->SetObjectField(TEXT("bounds"), BoundsJson);
	}

	// --- create_mesh_boolean ---------------------------------------------------------------------
	//   in:  { targetPath (alias: path), toolPath, operation: union|intersection|subtract, outputPath,
	//          toolOffsetX/Y/Z? (translation applied to toolPath before the op) }
	//   out: { assetPath, operation, vertexCount, triangleCount, bounds }
	// Combines two EXISTING StaticMesh assets (typically ones create_procedural_mesh made, since - same
	// limit as describe_dynamic_mesh - a real cooked mesh's SourceModel is usually stripped) into a
	// THIRD, new one. Reuses the exact read path describe_dynamic_mesh proved and the exact write path
	// create_procedural_mesh proved; the only new code here is the boolean call itself and the
	// two-input plumbing.
	void H_create_mesh_boolean(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("targetPath"), TEXT("path"), TEXT("toolPath"), TEXT("operation"), TEXT("outputPath"),
			  TEXT("toolOffsetX"), TEXT("toolOffsetY"), TEXT("toolOffsetZ") },
			TEXT("targetPath (alias: path) and toolPath - two existing StaticMesh assets; operation ")
			TEXT("(union|intersection|subtract); outputPath - where to create the result (must not ")
			TEXT("already exist); toolOffsetX/Y/Z (optional, default 0 - moves toolPath before the ")
			TEXT("operation so it actually overlaps targetPath)"),
			{ { TEXT("newPath"), TEXT("the parameter is outputPath") },
			  { TEXT("output"), TEXT("the parameter is outputPath") } }))
		{
			return;
		}

		const FString TargetPath = JStrAny(In, { TEXT("targetPath"), TEXT("path") });
		const FString ToolPath = JStr(In, TEXT("toolPath"));
		const FString OutputPath = JStr(In, TEXT("outputPath"));
		if (TargetPath.IsEmpty() || ToolPath.IsEmpty())
		{
			Fail(Out, TEXT("both targetPath and toolPath are required (existing StaticMesh assets). NOTHING was created."));
			return;
		}

		const FString OpStr = JStr(In, TEXT("operation")).ToLower();
		EGeometryScriptBooleanOperation Operation;
		if (OpStr == TEXT("union")) { Operation = EGeometryScriptBooleanOperation::Union; }
		else if (OpStr == TEXT("intersection")) { Operation = EGeometryScriptBooleanOperation::Intersection; }
		else if (OpStr == TEXT("subtract")) { Operation = EGeometryScriptBooleanOperation::Subtract; }
		else
		{
			Fail(Out, FString::Printf(
				TEXT("operation '%s' is not one of union, intersection, subtract. NOTHING was created."), *OpStr));
			return;
		}

		FString OutputAssetName, PathError;
		if (!ValidateNewMeshAssetPath(OutputPath, OutputAssetName, PathError, TEXT("create_mesh_boolean")))
		{
			Fail(Out, PathError);
			return;
		}

		UGeometryScriptDebug* Debug = NewObject<UGeometryScriptDebug>(GetTransientPackage(), NAME_None, RF_Transient);

		UDynamicMesh* TargetDynMesh = NewObject<UDynamicMesh>(GetTransientPackage(), NAME_None, RF_Transient);
		FString ReadError;
		if (!ReadStaticMeshIntoDynamicMesh(TargetPath, TargetDynMesh, Debug, ReadError))
		{
			TArray<TSharedPtr<FJsonValue>> Msgs;
			AppendDebugMessages(Debug, Msgs);
			Out->SetArrayField(TEXT("debugMessages"), Msgs);
			Fail(Out, FString::Printf(TEXT("reading targetPath failed: %s NOTHING was created."), *ReadError));
			return;
		}

		UDynamicMesh* ToolDynMesh = NewObject<UDynamicMesh>(GetTransientPackage(), NAME_None, RF_Transient);
		if (!ReadStaticMeshIntoDynamicMesh(ToolPath, ToolDynMesh, Debug, ReadError))
		{
			TArray<TSharedPtr<FJsonValue>> Msgs;
			AppendDebugMessages(Debug, Msgs);
			Out->SetArrayField(TEXT("debugMessages"), Msgs);
			Fail(Out, FString::Printf(TEXT("reading toolPath failed: %s NOTHING was created."), *ReadError));
			return;
		}

		const FVector ToolOffset(
			JNum(In, TEXT("toolOffsetX"), 0.0), JNum(In, TEXT("toolOffsetY"), 0.0), JNum(In, TEXT("toolOffsetZ"), 0.0));
		const FTransform ToolTransform(ToolOffset);

		FGeometryScriptMeshBooleanOptions BooleanOptions;
		UGeometryScriptLibrary_MeshBooleanFunctions::ApplyMeshBoolean(
			TargetDynMesh, FTransform::Identity, ToolDynMesh, ToolTransform, Operation, BooleanOptions, Debug);

		// THE REAL FAILURE SIGNAL, live-discovered by reproducing it, not read off the header alone.
		// ApplyMeshBoolean's own .cpp (MeshBooleanFunctions.cpp) treats an empty RESULT identically to a
		// computation ERROR: `bSuccess = (NewResultMesh.TriangleCount() > 0); if (!bSuccess) { AppendError
		// (...); return TargetMesh; }` - on EITHER case it appends an error message to Debug and returns
		// TargetMesh COMPLETELY UNCHANGED, not emptied. Confirmed live: subtracting a mesh from ITSELF (an
		// unambiguous empty result) came back as the untouched original target, 8 verts, unmodified bounds -
		// silently indistinguishable from success by vertex/triangle count alone. So the correct check here
		// is NOT "did the mesh come back empty" (it never does - it comes back UNCHANGED), it's "did the
		// engine record an error", which is the one signal this API actually gives honestly.
		bool bBooleanFailed = false;
		if (Debug)
		{
			for (const FGeometryScriptDebugMessage& Msg : Debug->Messages)
			{
				if (Msg.MessageType == EGeometryScriptDebugMessageType::ErrorMessage)
				{
					bBooleanFailed = true;
					break;
				}
			}
		}
		if (bBooleanFailed)
		{
			TArray<TSharedPtr<FJsonValue>> Msgs;
			AppendDebugMessages(Debug, Msgs);
			Out->SetArrayField(TEXT("debugMessages"), Msgs);
			Fail(Out, FString::Printf(
				TEXT("the %s operation failed - see debugMessages. The engine cannot distinguish a real ")
				TEXT("computation error from a legitimately empty result (a subtract that fully removes the ")
				TEXT("target, or a non-overlapping intersection) - either way NOTHING was created, and ")
				TEXT("targetPath/toolPath are UNCHANGED."), *OpStr));
			return;
		}

		int32 VertCount = 0, TriCount = 0;
		FBox Bounds(EForceInit::ForceInit);
		TargetDynMesh->ProcessMesh([&](const UE::Geometry::FDynamicMesh3& Mesh)
		{
			VertCount = Mesh.VertexCount();
			TriCount = Mesh.TriangleCount();
			const UE::Geometry::FAxisAlignedBox3d B = Mesh.GetBounds();
			Bounds = FBox(FVector(B.Min), FVector(B.Max));
		});
		if (VertCount == 0 || TriCount == 0)
		{
			TArray<TSharedPtr<FJsonValue>> Msgs;
			AppendDebugMessages(Debug, Msgs);
			Out->SetArrayField(TEXT("debugMessages"), Msgs);
			Fail(Out, FString::Printf(
				TEXT("the %s operation produced an empty mesh (vertices=%d, triangles=%d) with no error ")
				TEXT("recorded - see debugMessages. NOTHING was created."),
				*OpStr, VertCount, TriCount));
			return;
		}

		UPackage* Package = CreatePackage(*OutputPath);
		if (!Package)
		{
			Fail(Out, FString::Printf(TEXT("failed to create package '%s'"), *OutputPath));
			return;
		}
		UStaticMesh* TargetAsset = NewObject<UStaticMesh>(
			Package, FName(*OutputAssetName), RF_Public | RF_Standalone | RF_Transactional);
		if (!TargetAsset)
		{
			Fail(Out, TEXT("failed to allocate the new StaticMesh"));
			return;
		}

		FGeometryScriptCopyMeshToAssetOptions CopyOptions;
		FGeometryScriptMeshWriteLOD WriteLOD;
		EGeometryScriptOutcomePins WriteOutcome = EGeometryScriptOutcomePins::Failure;
#if MIF_ENGINE_AT_LEAST(5, 5)
		UGeometryScriptLibrary_StaticMeshFunctions::CopyMeshToStaticMesh(
			TargetDynMesh, TargetAsset, CopyOptions, WriteLOD, WriteOutcome, /*bUseSectionMaterials*/ true, Debug);
#else
		UGeometryScriptLibrary_StaticMeshFunctions::CopyMeshToStaticMesh(
			TargetDynMesh, TargetAsset, CopyOptions, WriteLOD, WriteOutcome, Debug);
#endif

		if (WriteOutcome != EGeometryScriptOutcomePins::Success)
		{
			TArray<TSharedPtr<FJsonValue>> Msgs;
			AppendDebugMessages(Debug, Msgs);
			Out->SetArrayField(TEXT("debugMessages"), Msgs);
			Fail(Out, TEXT("CopyMeshToStaticMesh reported failure - see debugMessages. The package was ")
						  TEXT("created but the asset was never registered, so it will not appear in ")
						  TEXT("find_assets."));
			return;
		}

		FAssetRegistryModule::AssetCreated(TargetAsset);
		Package->MarkPackageDirty();

		Out->SetStringField(TEXT("assetPath"), TargetAsset->GetPathName());
		Out->SetStringField(TEXT("operation"), OpStr);
		Out->SetNumberField(TEXT("vertexCount"), VertCount);
		Out->SetNumberField(TEXT("triangleCount"), TriCount);
		TSharedRef<FJsonObject> BoundsJson = MakeShared<FJsonObject>();
		BoundsJson->SetNumberField(TEXT("sizeX"), Bounds.GetSize().X);
		BoundsJson->SetNumberField(TEXT("sizeY"), Bounds.GetSize().Y);
		BoundsJson->SetNumberField(TEXT("sizeZ"), Bounds.GetSize().Z);
		Out->SetObjectField(TEXT("bounds"), BoundsJson);
		UE_LOG(LogMifBridge, Log, TEXT("create_mesh_boolean: %s = %s(%s, %s) (%d verts, %d tris)"),
			*TargetAsset->GetPathName(), *OpStr, *TargetPath, *ToolPath, VertCount, TriCount);
	}
#endif
}
