# Axis E — Geometry and meshes
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._

## Surface inventory

**GeometryScripting plugin** — located at `D:/UE532/Engine/Plugins/Experimental/GeometryScripting/GeometryScripting.uplugin`.
- `.uplugin` facts: NO `EnabledByDefault` key (⇒ defaults to **disabled**), `"IsBetaVersion": true`, `"IsExperimentalVersion": false`. Modules: `GeometryScriptingCore` (Runtime, PreDefault), `GeometryScriptingEditor` (Editor, Default). Plugin deps: GeometryProcessing, MeshModelingToolset (both enabled-by-reference).
- **Not project-enabled**: `D:/DDS2SDK/Game/DrugDealerSimulator2.uproject` has no Geometry* entry (grep verified).
- **ModelingToolsEditorMode does NOT force it on**: `D:/UE532/Engine/Plugins/Editor/ModelingToolsEditorMode/ModelingToolsEditorMode.uplugin` declares plugin deps MeshModelingToolset, MeshModelingToolsetExp, MeshLODToolset, ToolPresets — GeometryScripting is not in any of their dependency chains (checked MeshModelingToolsetExp.uplugin, MeshModelingToolset.uplugin, MeshLODToolset.uplugin, GeometryProcessing.uplugin).
- ⇒ **COST FLAG**: every GeometryScript-based endpoint requires enabling the plugin. Cheapest route: add `"Plugins": [{"Name":"GeometryScripting","Enabled":true}]` to `MifBridge.uplugin` (plugin-to-plugin reference force-enables it for this project only; no .uproject edit). Runtime module GeometryScriptingCore is cook-safe (it ships in runtime builds of stock UE), but to be safe for a mod-kit, reference it from the editor-only MifBridge plugin whose modules never cook.

**UGeometryScriptLibrary_* enumeration** (grep `class GEOMETRYSCRIPTINGCORE_API` over `GeometryScripting/Source/GeometryScriptingCore/Public/GeometryScript/*.h`) — 39 library classes, ~478 UFUNCTIONs total. Per-header UFUNCTION counts (grep -c UFUNCTION):

| Header (Public/GeometryScript/) | Class | UFUNCTIONs |
|---|---|---|
| CollisionFunctions.h:108 | UGeometryScriptLibrary_CollisionFunctions | 4 |
| ContainmentFunctions.h:88 | UGeometryScriptLibrary_ContainmentFunctions | 3 |
| ListUtilityFunctions.h:14 | UGeometryScriptLibrary_ListUtilityFunctions | 47 |
| MeshAssetFunctions.h:103 | UGeometryScriptLibrary_StaticMeshFunctions | 5 |
| MeshBakeFunctions.h:446 | UGeometryScriptLibrary_MeshBakeFunctions | 14 |
| MeshBasicEditFunctions.h:90 | UGeometryScriptLibrary_MeshBasicEditFunctions | 16 |
| MeshBoneWeightFunctions.h:192 | UGeometryScriptLibrary_MeshBoneWeightFunctions | 16 |
| MeshBooleanFunctions.h:117 | UGeometryScriptLibrary_MeshBooleanFunctions | 5 |
| MeshComparisonFunctions.h:57 | UGeometryScriptLibrary_MeshComparisonFunctions | 3 |
| MeshDecompositionFunctions.h:15 | UGeometryScriptLibrary_MeshDecompositionFunctions | 6 |
| MeshDeformFunctions.h:198 | UGeometryScriptLibrary_MeshDeformFunctions | 8 |
| MeshGeodesicFunctions.h:14 | UGeometryScriptLibrary_MeshGeodesicFunctions | 3 |
| MeshMaterialFunctions.h:15 | UGeometryScriptLibrary_MeshMaterialFunctions | 15 |
| MeshModelingFunctions.h:269 | UGeometryScriptLibrary_MeshModelingFunctions | 10 |
| MeshNormalsFunctions.h:69 | UGeometryScriptLibrary_MeshNormalsFunctions | 16 |
| MeshPolygroupFunctions.h:15 | UGeometryScriptLibrary_MeshPolygroupFunctions | 14 |
| MeshPrimitiveFunctions.h:152 | UGeometryScriptLibrary_MeshPrimitiveFunctions | 26 |
| MeshQueryFunctions.h:14 | UGeometryScriptLibrary_MeshQueryFunctions | 46 |
| MeshRemeshFunctions.h:145 | UGeometryScriptLibrary_RemeshingFunctions | 1 |
| MeshRepairFunctions.h:163 | UGeometryScriptLibrary_MeshRepairFunctions | 8 |
| MeshSamplingFunctions.h:97 | UGeometryScriptLibrary_MeshSamplingFunctions | 3 |
| MeshSelectionFunctions.h:27 | UGeometryScriptLibrary_MeshSelectionFunctions | 18 |
| MeshSelectionQueryFunctions.h:12 | UGeometryScriptLibrary_MeshSelectionQueryFunctions | 2 |
| MeshSimplifyFunctions.h:83 | UGeometryScriptLibrary_MeshSimplifyFunctions | 5 |
| MeshSpatialFunctions.h:58 | UGeometryScriptLibrary_MeshSpatial | 8 |
| MeshSubdivideFunctions.h:51 | UGeometryScriptLibrary_MeshSubdivideFunctions | 3 |
| MeshTransformFunctions.h:15 | UGeometryScriptLibrary_MeshTransformFunctions | 9 |
| MeshUVFunctions.h:144 | UGeometryScriptLibrary_MeshUVFunctions | 18 |
| MeshVertexColorFunctions.h:50 | UGeometryScriptLibrary_MeshVertexColorFunctions | 7 |
| MeshVoxelFunctions.h:117 | UGeometryScriptLibrary_MeshVoxelFunctions | 2 |
| PolyPathFunctions.h:41 | UGeometryScriptLibrary_PolyPathFunctions | 21 |
| PolygonFunctions.h:93,182 | UGeometryScriptLibrary_SimplePolygonFunctions, _PolygonListFunctions | 37 |
| SceneUtilityFunctions.h:32 | UGeometryScriptLibrary_SceneUtilityFunctions | 4 |
| ShapeFunctions.h:14,65,189 | _TransformFunctions, _RayFunctions, _BoxFunctions | 33 |
| TextureMapFunctions.h:43 | UGeometryScriptLibrary_TextureMapFunctions | 2 |
| VectorMathFunctions.h:19 | UGeometryScriptLibrary_VectorMathFunctions | 19 |
| MeshBooleanFunctions/ContainmentFunctions etc. | (see above) | — |

All 39 classes carry class-level `GEOMETRYSCRIPTINGCORE_API` — every static UFUNCTION is directly linkable C++ (no reflection needed).

**GeometryScriptingEditor module** (`GeometryScripting/Source/GeometryScriptingEditor/Public/GeometryScript/`): 4 headers — CreateNewAssetUtilityFunctions.h (UGeometryScriptLibrary_CreateNewAssetFunctions:107 — CreateNewStaticMeshAssetFromMesh:140, CreateNewStaticMeshAssetFromMeshLODs:153, CreateNewSkeletalMeshAssetFromMesh:166, CreateNewTexture2DAsset:190, CreateNewVolumeFromMesh:126), EditorDynamicMeshUtilityFunctions.h:42, EditorTextureMapFunctions.h:67, OpenSubdivUtilityFunctions.h:11. All `GEOMETRYSCRIPTINGEDITOR_API`.

**UDynamicMesh container + pool** — `Runtime/GeometryFramework/Public/UDynamicMesh.h` (engine **Runtime** module `GeometryFramework`, NOT part of the plugin — no plugin enable needed for the container itself):
- `UDynamicMesh` — UCLASS(BlueprintType, MinimalAPI), methods individually `GEOMETRYFRAMEWORK_API` exported: `Reset()` :113, `IsEmpty()` :135, `GetTriangleCount()` :141, `SetMesh()` :168, `ProcessMesh()` :176, `EditMesh()` :182.
- `UDynamicMeshPool` — UCLASS(BlueprintType, Transient, MinimalAPI) at :368, with `GEOMETRYFRAMEWORK_API` `RequestMesh()` :374, `ReturnMesh(UDynamicMesh*)` :378, `ReturnAllMeshes()` :382, `FreeAllMeshes()` :386. Failsafe: pool self-frees past CVar `geometry.DynamicMesh.MaxPoolSize` (header comment :354). MinimalAPI + exported methods ⇒ `NewObject<UDynamicMeshPool>()` links (MinimalAPI exports GetPrivateStaticClass) and all four pool methods link directly.

**UStaticMeshEditorSubsystem** — `Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystem.h:25`, `class STATICMESHEDITOR_API UStaticMeshEditorSubsystem : public UEditorSubsystem`. Lives in engine-source editor module **StaticMeshEditor** — no plugin required (EditorScriptingUtilities plugin is `"EnabledByDefault": false` and NOT needed; its EditorStaticMeshLibrary is just a deprecated forwarder to this subsystem).

**PCG** — `D:/UE532/Engine/Plugins/Experimental/PCG/PCG.uplugin`: `"EnabledByDefault": false`, VersionName 0.1, not project-enabled ⇒ out of scope for this audit cycle (see Negative results).

(Static/skeletal mesh + merge + physics-asset inventory continues below — see Coverage log.)

## Bridge architecture — the dynamic-mesh session model

GeometryScript is ~478 functions; one endpoint per function is the god-endpoint anti-pattern inverted (registry spam). Proposed architecture: **a named transient mesh pool + one tiered dispatcher + explicit commit/query endpoints** — 9 endpoints total expose the useful ~80% of the surface.

**Object lifetime story** (the centerpiece):
- MifBridge owns ONE `UDynamicMeshPool` instance, created lazily on first `create_dynamic_mesh` via `NewObject<UDynamicMeshPool>(GetTransientPackage())` and **rooted** (`AddToRoot()`) so GC never collects the pool or the meshes it references (pool holds `UPROPERTY() TArray<TObjectPtr<UDynamicMesh>>` — UDynamicMesh.h:390-395 — so pooled meshes are GC-reachable through the rooted pool).
- Handles: MifBridge keeps a `TMap<FString /*handle*/, TObjectPtr<UDynamicMesh>>` (inside a small rooted UObject, or a TStrongObjectPtr map). Handles are caller-chosen names (`"m1"`, `"hull_tmp"`); `create_dynamic_mesh` errors on duplicate handle.
- `release_dynamic_mesh` ⇒ `Pool->ReturnMesh(Mesh)` (mesh's FDynamicMesh3 is `Reset()`, UObject container reused — UDynamicMesh.h:363-365) and removes the handle. `release_all` ⇒ `Pool->FreeAllMeshes()` + map clear.
- Meshes are **transient and editor-session-scoped**: never saved, never referenced by level actors (commit copies data OUT into assets). Documented invariant: a handle that survives a map load is still valid (transient package outlives worlds), but handles do NOT survive editor restart.
- Leak guard: `list_dynamic_meshes` reports per-handle triangle counts so an agent can see leaks; the pool's own MaxPoolSize failsafe (UDynamicMesh.h:354) is the backstop.

All GeometryScript calls take the pattern `Func(UDynamicMesh* Target, ..., UGeometryScriptDebug* Debug=nullptr)` and append errors to Debug. The dispatcher passes a fresh `UGeometryScriptDebug` (GeometryScriptTypes.h, `GEOMETRYSCRIPTINGCORE_API`) and converts `Debug->Messages` to the HTTP error payload — this is the error path for every op below.

## Proposed endpoints

### create_dynamic_mesh
**Purpose**: allocate a named transient UDynamicMesh working object — opens the entire GeometryScript pipeline to the agent.
**Engine API**:
```cpp
/** @return an available UDynamicMesh from the pool (possibly allocating a new mesh) */
UFUNCTION(BlueprintCallable, Category="Dynamic Mesh")
GEOMETRYFRAMEWORK_API UDynamicMesh* RequestMesh();
```
`Runtime/GeometryFramework/Public/UDynamicMesh.h:373-374` (UDynamicMeshPool). Pool class decl :367-368.
**Export**: `GEOMETRYFRAMEWORK_API` (method-level; class is MinimalAPI — NewObject works, exported methods link) | **Module**: `GeometryFramework` — runtime module, engine source, NEW dep for MifBridge.Build.cs | **Guards**: none
**Bucket**: self-managed — creates+roots transient UObjects; must NOT be undoable (Ctrl-Z killing the pool ⇒ dangling handles).
**Async**: no
**Params**: | name | aliases | type | default | required |
| handle | name, id | string | — | yes (strict: empty ⇒ error naming `handle`) |
| reset_to_cube | cube | bool | false | no (calls `ResetToCube()` UDynamicMesh.h:119-120 for a visible starter mesh) |
Unrecognised parameter ⇒ error naming it.
**Failure modes**: duplicate handle ⇒ `"handle 'm1' already exists — call release_dynamic_mesh first or pick another name"`; pool exceeded geometry.DynamicMesh.MaxPoolSize ⇒ warn field in response.
**Cooked**: works — transient objects, no asset I/O.
**Verify**: response returns `{handle, triangleCount:0}`; `list_dynamic_meshes` shows the handle with 0 tris (or 12 tris if reset_to_cube).
**Score**: U5 E2 R1 → tier 1 (gateway endpoint; nothing works without it)
**Phase-2 verdict**: CORRECTED — all citations verified verbatim (UDynamicMesh.h:99 MinimalAPI class; Reset :113, ResetToCube :119-120, IsEmpty :135, GetTriangleCount :141; pool class :367-368, RequestMesh :373-374, UPROPERTY mesh arrays :390-395). One load-bearing claim fixed: **the MaxPoolSize failsafe is NOT a usable backstop.** Implementation (Runtime/GeometryFramework/Private/UDynamicMesh.cpp): RequestMesh allocates via `NewObject<UDynamicMesh>()` into the transient package (:559) — pooled meshes are GC-reachable ONLY through the pool's UPROPERTY arrays, so AddToRoot on the pool + a strong handle map is required and sufficient (rooting story CONFIRMED). But on failsafe trip (default 1000, CVar at :546-549) RequestMesh does `AllCreatedMeshes.Reset()` + `GEngine->ForceGarbageCollection(true)` (:563-568); after that, `ReturnMesh` fails its `AllCreatedMeshes.Contains(Mesh)` ensure (:578) for every pre-trip mesh — handles held by the rooted map survive GC but can never be returned (permanent UObject-container leak + ensure spam). The bridge MUST enforce its own live-handle cap (e.g. refuse create above 256) and never rely on the CVar failsafe; update the entry's failure-mode wording accordingly.

### list_dynamic_meshes
**Purpose**: enumerate live mesh handles with numeric state — the leak detector and session inspector.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "Dynamic Mesh")
GEOMETRYFRAMEWORK_API UPARAM(DisplayName = "Triangle Count") int32 GetTriangleCount() const;
UFUNCTION(BlueprintCallable, Category = "Dynamic Mesh")
GEOMETRYFRAMEWORK_API bool IsEmpty() const;
```
`Runtime/GeometryFramework/Public/UDynamicMesh.h:140-141, 134-135`.
**Export**: `GEOMETRYFRAMEWORK_API` | **Module**: GeometryFramework (same NEW dep) | **Guards**: none
**Bucket**: read-only — pure query.
**Async**: no
**Params**: none. Unrecognised ⇒ error.
**Failure modes**: none (empty list is valid).
**Cooked**: works.
**Verify**: counts match sum of create/release calls made; triangleCount changes after each mesh_op.
**Score**: U3 E1 R1 → tier 1
**Phase-2 verdict**: CONFIRMED — GetTriangleCount UDynamicMesh.h:140-141 and IsEmpty :134-135 verbatim, both GEOMETRYFRAMEWORK_API method-level on the MinimalAPI class.

### release_dynamic_mesh
**Purpose**: return a mesh (or all meshes) to the pool — explicit lifetime end, prevents GC-rooted leaks.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "Dynamic Mesh")
GEOMETRYFRAMEWORK_API void ReturnMesh(UDynamicMesh* Mesh);
UFUNCTION(BlueprintCallable, Category = "Dynamic Mesh")
GEOMETRYFRAMEWORK_API void FreeAllMeshes();
```
`Runtime/GeometryFramework/Public/UDynamicMesh.h:377-378, 385-386`.
**Export**: `GEOMETRYFRAMEWORK_API` | **Module**: GeometryFramework | **Guards**: none
**Bucket**: self-managed — pool bookkeeping must not be transacted (undo would resurrect a released handle).
**Async**: no
**Params**: | name | aliases | type | default | required |
| handle | name, id | string | — | yes unless all=true |
| all | — | bool | false | no (FreeAllMeshes + clear map) |
**Failure modes**: unknown handle ⇒ `"no dynamic mesh 'm7' — see list_dynamic_meshes"`.
**Cooked**: works.
**Verify**: list_dynamic_meshes no longer contains the handle.
**Score**: U3 E1 R1 → tier 1
**Phase-2 verdict**: CONFIRMED — ReturnMesh UDynamicMesh.h:377-378 and FreeAllMeshes :385-386 verbatim. Implementation note: ReturnMesh is a silent no-op (failed ensure) for any mesh the pool no longer tracks (UDynamicMesh.cpp:578) — after a failsafe trip the only clean recovery is the `all=true` path (FreeAllMeshes + map clear), which this entry already provides.

### mesh_op
**Purpose**: single dispatcher applying one allowlisted GeometryScript operation to a named mesh — the workhorse that turns ~80 engine functions into one endpoint without a god-API (op registry is a static table: name → param schema → direct C++ call).
**Engine API** (one flagship signature per op family, all verbatim; every listed class is `class GEOMETRYSCRIPTINGCORE_API ... : public UBlueprintFunctionLibrary`):

Tier A — primitives (`MeshPrimitiveFunctions.h`, class :152):
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
AppendBox(
    UDynamicMesh* TargetMesh,
    FGeometryScriptPrimitiveOptions PrimitiveOptions,
    FTransform Transform,
    float DimensionX = 100, float DimensionY = 100, float DimensionZ = 100,
    int32 StepsX = 0, int32 StepsY = 0, int32 StepsZ = 0,
    EGeometryScriptPrimitiveOriginMode Origin = EGeometryScriptPrimitiveOriginMode::Base,
    UGeometryScriptDebug* Debug = nullptr);
```
`.../GeometryScriptingCore/Public/GeometryScript/MeshPrimitiveFunctions.h:161-173`
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
AppendSphereLatLong(
    UDynamicMesh* TargetMesh,
    FGeometryScriptPrimitiveOptions PrimitiveOptions,
    FTransform Transform,
    float Radius = 50, int32 StepsPhi = 10, int32 StepsTheta = 16,
    EGeometryScriptPrimitiveOriginMode Origin = EGeometryScriptPrimitiveOriginMode::Center,
    UGeometryScriptDebug* Debug = nullptr);
```
`MeshPrimitiveFunctions.h:195-204`
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
AppendCylinder(
    UDynamicMesh* TargetMesh,
    FGeometryScriptPrimitiveOptions PrimitiveOptions,
    FTransform Transform,
    float Radius = 50, float Height = 100,
    int32 RadialSteps = 12, int32 HeightSteps = 0, bool bCapped = true,
    EGeometryScriptPrimitiveOriginMode Origin = EGeometryScriptPrimitiveOriginMode::Base,
    UGeometryScriptDebug* Debug = nullptr);
```
`MeshPrimitiveFunctions.h:242-253`
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
AppendCone(
    UDynamicMesh* TargetMesh,
    FGeometryScriptPrimitiveOptions PrimitiveOptions,
    FTransform Transform,
    float BaseRadius = 50, float TopRadius = 5, float Height = 100,
    int32 RadialSteps = 12, int32 HeightSteps = 4, bool bCapped = true,
    EGeometryScriptPrimitiveOriginMode Origin = EGeometryScriptPrimitiveOriginMode::Base,
    UGeometryScriptDebug* Debug = nullptr);
```
`MeshPrimitiveFunctions.h:259-271`
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
AppendTorus(
    UDynamicMesh* TargetMesh,
    FGeometryScriptPrimitiveOptions PrimitiveOptions,
    FTransform Transform,
    FGeometryScriptRevolveOptions RevolveOptions,
    float MajorRadius = 50, float MinorRadius = 25,
    int32 MajorSteps = 16, int32 MinorSteps = 8,
    EGeometryScriptPrimitiveOriginMode Origin = EGeometryScriptPrimitiveOriginMode::Base,
    UGeometryScriptDebug* Debug = nullptr);
```
`MeshPrimitiveFunctions.h:277-288`. Also in tier A: AppendCapsule :227-236, AppendBoundingBox :180-188, AppendSphereBox :211-220, AppendRevolvePolygon :297, AppendSweepPolyline :353, AppendSimpleExtrudePolygon :373, AppendSimpleSweptPolygon :389, AppendSweepPolygon :415.

Tier B — booleans (`MeshBooleanFunctions.h`, class :117):
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
ApplyMeshBoolean(
    UDynamicMesh* TargetMesh,
    FTransform TargetTransform,
    UDynamicMesh* ToolMesh,
    FTransform ToolTransform,
    EGeometryScriptBooleanOperation Operation,
    FGeometryScriptMeshBooleanOptions Options,
    UGeometryScriptDebug* Debug = nullptr);
```
`MeshBooleanFunctions.h:132-140` (Operation: Union/Intersection/Subtract). Also ApplyMeshSelfUnion :146-150, ApplyMeshPlaneCut :160-165, ApplyMeshPlaneSlice :174-179, ApplyMeshMirror :188-193. Ops taking a second mesh take `tool_handle` resolved from the same pool.

Tier C — extrude/offset/shell (`MeshModelingFunctions.h`, class :269):
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
ApplyMeshLinearExtrudeFaces(
    UDynamicMesh* TargetMesh,
    FGeometryScriptMeshLinearExtrudeOptions Options,
    FGeometryScriptMeshSelection Selection,
    UGeometryScriptDebug* Debug = nullptr );
```
`MeshModelingFunctions.h:330-335`
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
ApplyMeshOffset(
    UDynamicMesh* TargetMesh,
    FGeometryScriptMeshOffsetOptions Options,
    UGeometryScriptDebug* Debug = nullptr );
```
`MeshModelingFunctions.h:307-311`. Also ApplyMeshShell :318-322, ApplyMeshOffsetFaces :342-347, ApplyMeshInsetOutsetFaces :353-358, ApplyMeshBevelSelection :367-370ff, ApplyMeshPolygroupBevel :381. (Empty Selection = whole mesh — header comment :338.)

Tier D — remesh/simplify (`MeshRemeshFunctions.h` class :145, `MeshSimplifyFunctions.h` class :83):
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
ApplyUniformRemesh(
    UDynamicMesh* TargetMesh,
    FGeometryScriptRemeshOptions RemeshOptions,
    FGeometryScriptUniformRemeshOptions UniformOptions,
    UGeometryScriptDebug* Debug = nullptr);
```
`MeshRemeshFunctions.h:155-160` (header warns: expensive, non-deterministic — dispatcher docs must repeat this)
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
ApplySimplifyToTriangleCount(
    UDynamicMesh* TargetMesh,
    int32 TriangleCount,
    FGeometryScriptSimplifyMeshOptions Options,
    UGeometryScriptDebug* Debug = nullptr);
```
`MeshSimplifyFunctions.h:115-120`. Also ApplySimplifyToVertexCount :126-131, ApplySimplifyToTolerance :138-142, ApplySimplifyToPlanar :93-97, ApplySimplifyToPolygroupTopology :104-109.

Tier E — UVs (`MeshUVFunctions.h`, class :144):
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
SetMeshUVsFromPlanarProjection(
    UDynamicMesh* TargetMesh,
    UPARAM(DisplayName = "UV Channel") int UVSetIndex,
    FTransform PlaneTransform,
    FGeometryScriptMeshSelection Selection,
    UGeometryScriptDebug* Debug = nullptr );
```
`MeshUVFunctions.h:229-235`
```cpp
static UPARAM(DisplayName = "Target Mesh") UDynamicMesh*
AutoGenerateXAtlasMeshUVs(
    UDynamicMesh* TargetMesh,
    UPARAM(DisplayName = "UV Channel") int UVSetIndex,
    FGeometryScriptXAtlasOptions Options,
    UGeometryScriptDebug* Debug = nullptr );
```
`MeshUVFunctions.h:304-309`. Also SetMeshUVsFromBoxProjection :242-249, SetMeshUVsFromCylinderProjection :255-262, RecomputeMeshUVs :270-276, RepackMeshUVs :282-287, AutoGeneratePatchBuilderMeshUVs :293-298.

Tier F — normals (`MeshNormalsFunctions.h`, class :69): RecomputeNormals :123, ComputeSplitNormals :150, plus FlipNormals/SetMeshToFlatShading (16 UFUNCTIONs in class).

Tier G — voxel/solidify (`MeshVoxelFunctions.h`, class :117): ApplyMeshSolidify :127, ApplyMeshMorphology :137.

Tier H — spline-to-mesh (`PolyPathFunctions.h`, class :41):
```cpp
static void ConvertSplineToPolyPath(const USplineComponent* Spline, FGeometryScriptPolyPath& PolyPath, FGeometryScriptSplineSamplingOptions SamplingOptions);
```
`PolyPathFunctions.h:122` — feeds AppendSweepPolyline/AppendSimpleSweptPolygon (tier A) for mesh-from-spline; `spline_actor`+`component` params resolve a placed USplineComponent (pairs with existing get_spline_points/set_spline_points endpoints).

Tier I — transforms (`MeshTransformFunctions.h`, class :15): TranslateMesh/RotateMesh/ScaleMesh (9 UFUNCTIONs).

Tier J — repair (`MeshRepairFunctions.h`, class :163): WeldMeshEdges, FillAllMeshHoles, CompactMesh etc. (8 UFUNCTIONs).

**Export**: `GEOMETRYSCRIPTINGCORE_API` on every class above (verbatim, one per header — see Surface inventory) | **Module**: `GeometryScriptingCore` — NEW dep; lives in GeometryScripting plugin (Runtime module, PreDefault) — **plugin must be enabled via MifBridge.uplugin reference** | **Guards**: none (runtime module)
**Bucket**: self-managed — operates only on transient pool meshes; transacting would push huge FDynamicMesh3 snapshots into the undo buffer for objects no user can see.
**Async**: no. Ops are synchronous; remesh/voxel ops on big meshes can take seconds — dispatcher enforces a documented per-op input-size cap (e.g. refuse ApplyUniformRemesh above N=2M input tris with a clear error) rather than going async in v1.
**Params**: | name | aliases | type | default | required |
| handle | target, mesh | string | — | yes (strict) |
| op | operation | string | — | yes; one of the registry names (`append_box`, `append_sphere`, `append_cylinder`, `append_cone`, `append_torus`, `append_capsule`, `boolean`, `plane_cut`, `mirror`, `extrude_faces`, `offset`, `shell`, `inset_outset`, `bevel`, `remesh_uniform`, `simplify_tricount`, `simplify_tolerance`, `uv_planar`, `uv_box`, `uv_cylinder`, `uv_xatlas`, `uv_repack`, `recompute_normals`, `split_normals`, `solidify`, `sweep_spline`, `translate`, `rotate`, `scale`, `weld_edges`, `fill_holes`, `compact`, …) — unknown op ⇒ error listing valid ops |
| tool_handle | tool | string | — | only for `boolean` |
| params | options | object | {} | per-op schema; each op's table documents its fields mapped 1:1 onto the options struct UPROPERTYs; unknown key ⇒ error naming key AND op |
| transform | xform | object {location,rotation,scale} | identity | ops taking FTransform |
**Failure modes**: unknown handle; unknown op (`"unknown op 'sphere' — did you mean 'append_sphere'?"`); GeometryScript Debug errors surfaced verbatim (e.g. boolean produced empty mesh); param type mismatch names the key; op-size cap exceeded names the cap.
**Cooked**: works — pure transient-mesh compute, no asset access.
**Verify**: response always returns `{triangleCountBefore, triangleCountAfter, boundingBox}` (from GetTriangleCount + GetMeshBoundingBox). append_box on empty mesh ⇒ 12 tris; boolean subtract of overlapping boxes ⇒ tri count changes and volume (mesh_query) drops; simplify_tricount 100 ⇒ triangleCountAfter ≤ 100.
**Score**: U5 E4 R2 → tier 1 (single biggest surface unlock in this audit)
**Phase-2 verdict**: CONFIRMED — every cited signature re-read verbatim from source. Primitives: class MeshPrimitiveFunctions.h:151-152; AppendBox :160-173, AppendBoundingBox :178-188, AppendSphereLatLong :194-204, AppendSphereBox :209-220, AppendCapsule :225-236, AppendCylinder :241-253, AppendCone :258-271, AppendTorus :276-288; pins :297/:353/:373/:389/:415 all exact. Booleans: class :116-117, ApplyMeshBoolean :131-140, SelfUnion :145-150, PlaneCut :159-165, PlaneSlice :173-179, Mirror :187-193. Modeling: class :268-269, LinearExtrudeFaces :329-335, Offset :306-311, Shell :317-322, OffsetFaces :341-347, InsetOutset :352-358, BevelSelection :366-373, PolygroupBevel :379-384; empty-Selection⇒whole-mesh comment at :338 and :350 confirmed. Remesh: class :144-145, ApplyUniformRemesh :154-160 (expensive/non-deterministic warning :152 confirmed). Simplify: class :82-83, ToPlanar :92-97, ToPolygroupTopology :103-109, ToTriangleCount :114-120, ToVertexCount :125-131, ToTolerance :137-143. UVs: class :143-144, planar :228-235, box :241-249, cylinder :254-262, recompute :269-276, repack :281-287, patch-builder :292-298, XAtlas :303-309. Normals: class :69, RecomputeNormals :123, ComputeSplitNormals :150. Voxel: class :117, Solidify :127, Morphology :137. PolyPath: class :41, ConvertSplineToPolyPath :122 verbatim. Transforms: class :15 (Translate :36, Rotate :46, Scale :57). Repair: class :163 (CompactMesh :173, WeldMeshEdges :192, FillAllMeshHoles :205). UGeometryScriptDebug is GEOMETRYSCRIPTINGCORE_API at GeometryScriptTypes.h:626-627.

### mesh_query
**Purpose**: numeric inspection of a pooled mesh — the verification story for every mesh_op (agent cannot see the viewport).
**Engine API** (`MeshQueryFunctions.h`, class `GEOMETRYSCRIPTINGCORE_API UGeometryScriptLibrary_MeshQueryFunctions` :14):
```cpp
static FString GetMeshInfoString( UDynamicMesh* TargetMesh );                       // :23
static UPARAM(DisplayName = "Bounding Box") FBox GetMeshBoundingBox( UDynamicMesh* TargetMesh );   // :42
static void GetMeshVolumeArea( UDynamicMesh* TargetMesh, float& SurfaceArea, float& Volume );      // :48
static void GetMeshVolumeAreaCenter(UDynamicMesh* TargetMesh, float& SurfaceArea, float& Volume, FVector& CenterOfMass); // :54
static UPARAM(DisplayName = "Num Loops") int32 GetNumOpenBorderLoops( UDynamicMesh* TargetMesh, bool& bAmbiguousTopologyFound ); // :66
static UPARAM(DisplayName = "Num Triangle IDs") int32 GetNumTriangleIDs( UDynamicMesh* TargetMesh ); // :88
```
plus GetIsClosedMesh :60, GetNumOpenBorderEdges :72, GetNumConnectedComponents :78, GetNumVertexIDs :183, GetVertexPosition :208 (46 UFUNCTIONs total in class).
**Export**: `GEOMETRYSCRIPTINGCORE_API` | **Module**: GeometryScriptingCore (same NEW dep as mesh_op) | **Guards**: none
**Bucket**: read-only — pure query.
**Async**: no
**Params**: | name | aliases | type | default | required |
| handle | target, mesh | string | — | yes |
| include_vertices | verts | bool | false | no (returns first `max_vertices` positions) |
| max_vertices | — | int | 100 | no |
**Failure modes**: unknown handle.
**Cooked**: works.
**Verify**: self-verifying (this IS the verifier). Cross-check: append_box 100³ ⇒ volume≈1,000,000, area≈60,000, closed=true, borderLoops=0.
**Score**: U5 E2 R1 → tier 1
**Phase-2 verdict**: CONFIRMED — all 12 cited lines exact (MeshQueryFunctions.h:14/:23/:42/:48/:54/:60/:66/:72/:78/:88/:183/:208), signatures verbatim.

### commit_dynamic_mesh
**Purpose**: write a pooled mesh's geometry into a UStaticMesh asset LOD — the bridge from procedural compute to real, placeable assets.
**Engine API** (`MeshAssetFunctions.h`, class `GEOMETRYSCRIPTINGCORE_API UGeometryScriptLibrary_StaticMeshFunctions` :103):
```cpp
UFUNCTION(BlueprintCallable, Category = "GeometryScript|StaticMesh", meta = (ExpandEnumAsExecs = "Outcome"))
static UPARAM(DisplayName = "Dynamic Mesh") UDynamicMesh*
CopyMeshToStaticMesh(
    UDynamicMesh* FromDynamicMesh,
    UStaticMesh* ToStaticMeshAsset,
    FGeometryScriptCopyMeshToAssetOptions Options,
    FGeometryScriptMeshWriteLOD TargetLOD,
    EGeometryScriptOutcomePins& Outcome,
    UGeometryScriptDebug* Debug = nullptr);
```
`MeshAssetFunctions.h:124-132`. Options struct verbatim fields (`FGeometryScriptCopyMeshToAssetOptions`, `GEOMETRYSCRIPTINGCORE_API`, :54-95):
```cpp
bool bEnableRecomputeNormals = false;      // :60
bool bEnableRecomputeTangents = false;     // :63
bool bEnableRemoveDegenerates = false;     // :66
bool bReplaceMaterials = false;            // :70
TArray<TObjectPtr<UMaterialInterface>> NewMaterials;   // :73
TArray<FName> NewMaterialSlotNames;        // :76
bool bApplyNaniteSettings = false;         // :80
FGeometryScriptNaniteOptions NaniteSettings; // :84 (DEPRECATED)
FMeshNaniteSettings NewNaniteSettings;     // :88
bool bEmitTransaction = true;              // :91
bool bDeferMeshPostEditChange = false;     // :94
```
**Export**: `GEOMETRYSCRIPTINGCORE_API` | **Module**: GeometryScriptingCore | **Guards**: CopyMeshToStaticMesh writes SourceModel/MeshDescription — editor-only data; MifBridge is editor-only so fine, but the handler should `#if WITH_EDITOR` for clarity.
**Bucket**: self-managed — pass `bEmitTransaction=true` (the function opens its own transaction, :91); wrapping in the blanket transaction would double-transact the asset edit. Triggers a static-mesh build (async in 5.3 — see build_static_mesh entry).
**Async**: the copy itself is synchronous; the post-edit mesh BUILD is async via FStaticMeshCompilingManager — response returns immediately and `asset_compile_status` is the poll pair.
**Params**: | name | aliases | type | default | required |
| handle | mesh | string | — | yes |
| asset_path | assetPath, path | string | — | yes (strict; must resolve to UStaticMesh) |
| lod | lodIndex | int | 0 | no |
| recompute_normals | — | bool | false | no |
| recompute_tangents | — | bool | false | no |
| replace_materials | — | bool | false | no |
| materials | — | array[string] | [] | with replace_materials |
| nanite_enabled | — | bool | absent | no (sets bApplyNaniteSettings + NewNaniteSettings.bEnabled) |
**Failure modes**: asset not found / not a UStaticMesh (name param + found class); Outcome==Failure ⇒ surface Debug messages; **cooked asset target ⇒ refuse** with `"'/Game/X' is cooked (no editable source model) — commit to a NEW asset created with create_static_mesh_asset instead"`; empty dynamic mesh ⇒ refuse (would produce a degenerate asset).
**Cooked**: refuses on .pak-mounted targets — cooked UStaticMesh has no MeshDescription/SourceModel to write. Works on loose/plugin-mounted assets and assets created via create_static_mesh_asset.
**Verify**: read back with mesh_asset_info (below): LOD0 triangle/vertex counts equal mesh_query counts of the committed handle (± degenerate removal); bounds match GetMeshBoundingBox.
**Score**: U5 E3 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — CopyMeshToStaticMesh MeshAssetFunctions.h:124-132 verbatim (UFUNCTION line :124); all 11 option-struct field lines exact (:60/:63/:66/:70/:73/:76/:80/:84/:88/:91/:94, struct :54-95). Self-transaction claim verified in implementation: MeshAssetFunctions.cpp opens the transaction at :257 (`Options.bEmitTransaction && GEditor`) and closes at :374; PostEditChange at :371 kicks the (async) rebuild. Hazard grep of MeshAssetFunctions.cpp clean: no dialogs, no modal prompts, no synchronous flush/wait calls.

### copy_from_static_mesh
**Purpose**: load an existing StaticMesh (or SkeletalMesh) LOD into a pooled dynamic mesh for measurement or derivation (collision hulls, LODs, booleans against level geometry).
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "GeometryScript|StaticMesh", meta = (ExpandEnumAsExecs = "Outcome"))
static UPARAM(DisplayName = "Dynamic Mesh") UDynamicMesh*
CopyMeshFromStaticMesh(
    UStaticMesh* FromStaticMeshAsset,
    UDynamicMesh* ToDynamicMesh,
    FGeometryScriptCopyMeshFromAssetOptions AssetOptions,
    FGeometryScriptMeshReadLOD RequestedLOD,
    EGeometryScriptOutcomePins& Outcome,
    UGeometryScriptDebug* Debug = nullptr);
```
`MeshAssetFunctions.h:111-119`. Options (`FGeometryScriptCopyMeshFromAssetOptions` :17-30): `bApplyBuildSettings=true` :23, `bRequestTangents=true` :26, `bIgnoreRemoveDegenerates=true` :29. Skeletal variant `CopyMeshFromSkeletalMesh` :151-159.
**Export**: `GEOMETRYSCRIPTINGCORE_API` | **Module**: GeometryScriptingCore | **Guards**: reading SourceModel is editor-only; **RenderData path**: FGeometryScriptMeshReadLOD has LODType incl. RenderData (GeometryScriptTypes.h) which DOES work on cooked meshes.
**Bucket**: self-managed — writes only into a transient pool mesh; no asset mutation, no undo entry wanted.
**Async**: no
**Params**: | name | aliases | type | default | required |
| handle | mesh | string | — | yes (target pool mesh; overwritten) |
| asset_path | assetPath, path | string | — | yes |
| lod | lodIndex | int | 0 | no |
| lod_type | — | string enum `max_available\|source_mesh\|render_data` | max_available | no |
| skeletal | — | bool | false | no (routes to CopyMeshFromSkeletalMesh) |
**Failure modes**: asset missing/wrong class; source-mesh read on cooked asset ⇒ error suggesting `lod_type:"render_data"`.
**Cooked**: degraded-but-works — cooked assets readable via `lod_type:"render_data"` (cooked render data survives in .pak); `source_mesh` refuses. THIS is the measurement route for base-game mesh geometry.
**Verify**: mesh_query counts >0 and bounds ≈ get_actor_bounds of a placed instance of the same asset.
**Score**: U5 E3 R2 → tier 1 (only path to numeric geometry of cooked base-game meshes)
**Phase-2 verdict**: CORRECTED — signature :111-119 and options struct :17-30 (fields :23/:26/:29) verbatim; CopyMeshFromSkeletalMesh :151-159 verbatim; EGeometryScriptLODType incl. RenderData confirmed (GeometryScriptTypes.h:40-46). Two implementation facts Phase 1 missed (MeshAssetFunctions.cpp): (1) the bAllowCPUAccess refusal is `#if !WITH_EDITOR` only (:133-139) — no CPU-access barrier in-editor, good. (2) **Crash hazard on cooked targets**: in WITH_EDITOR builds the RenderData path unconditionally reads `FromStaticMeshAsset->GetSourceModel(UseLODIndex).BuildSettings` for BuildScale (:155-158), and UseLODIndex is clamped against GetNumLODs (:141), NOT GetNumSourceModels — on a cooked mesh with zero source models this is a TArray range-check assert (crash). Handler MUST pre-check `GetNumSourceModels() > UseLODIndex` before routing through CopyMeshFromStaticMesh(render_data); when the check fails (cooked), bypass the wrapper and call `UE::Geometry::FStaticMeshLODResourcesToDynamicMesh::Convert` directly — `static MESHCONVERSIONENGINETYPES_API bool Convert(const FStaticMeshLODResources*, const ConversionOptions&, FDynamicMesh3&)`, Runtime/MeshConversionEngineTypes/Public/StaticMeshLODResourcesToDynamicMesh.h:39-42 (adds engine runtime module `MeshConversionEngineTypes` as a dep, which GeometryScriptingCore already links) with BuildScale left at 1. Cooked claim downgraded from "degraded-but-works" to "works only via the direct-converter guard path — the stock wrapper can assert on cooked meshes"; prove against a .pak-mounted mesh at implementation time.

### create_static_mesh_asset
**Purpose**: create a brand-new UStaticMesh asset from a pooled mesh (the mod-content authoring path — no cooked-asset restriction).
**Engine API** (`GeometryScriptingEditor/Public/GeometryScript/CreateNewAssetUtilityFunctions.h`, class `GEOMETRYSCRIPTINGEDITOR_API UGeometryScriptLibrary_CreateNewAssetFunctions` :107):
```cpp
UFUNCTION(BlueprintCallable, Category = "GeometryScript|AssetManagement", meta = (ExpandEnumAsExecs = "Outcome"))
static UPARAM(DisplayName = "Static Mesh Asset") UStaticMesh*
CreateNewStaticMeshAssetFromMesh(
    UDynamicMesh* FromDynamicMesh,
    FString AssetPathAndName,
    FGeometryScriptCreateNewStaticMeshAssetOptions Options,
    EGeometryScriptOutcomePins& Outcome,
    UGeometryScriptDebug* Debug = nullptr);
```
`CreateNewAssetUtilityFunctions.h:140-145` (verbatim read of :136-146 region; LODs variant `CreateNewStaticMeshAssetFromMeshLODs` :153-158). Options struct `FGeometryScriptCreateNewStaticMeshAssetOptions` (`GEOMETRYSCRIPTINGEDITOR_API`) :46.
**Export**: `GEOMETRYSCRIPTINGEDITOR_API` | **Module**: `GeometryScriptingEditor` — NEW dep (editor module in GeometryScripting plugin; fine for editor-only MifBridge) | **Guards**: editor-only module already.
**Bucket**: self-managed — creates + registers a new package/asset (brief invariant: object creation at scale = self-managed).
**Async**: copy synchronous; build async (asset_compile_status pairs).
**Params**: | name | aliases | type | default | required |
| handle | mesh | string | — | yes |
| asset_path | assetPath, path | string | — | yes (e.g. `/Game/Mods/Meshes/SM_Custom`; must NOT exist) |
**Failure modes**: path exists ⇒ error suggesting commit_dynamic_mesh; invalid mount point; empty source mesh ⇒ refuse.
**Cooked**: works — creates NEW loose assets (in the mod plugin's mount); does not touch cooked content.
**Verify**: find_assets sees the new asset; mesh_asset_info LOD0 counts == mesh_query counts.
**Score**: U5 E2 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — CreateNewStaticMeshAssetFromMesh CreateNewAssetUtilityFunctions.h:138-145 verbatim (UFUNCTION :138; LODs variant :151-158; options struct :45-72; class :106-107). Hazard sweep of CreateNewAssetUtilityFunctions.cpp: no dialogs, no checkout prompts, no SavePackage — despite the header doc "Save the asset at the AssetPathAndName location", the asset is only created + registered (UE::AssetUtils::CreateStaticMeshAsset, .cpp:242), NOT saved to disk; response should remind callers to pair with save_package.

### set_static_mesh_collision_from_mesh
**Purpose**: generate simple collision (boxes/spheres/capsules/convex/swept hulls) for a StaticMesh asset from any pooled mesh — closes the "spawned meshes have no collision" gap end-to-end.
**Engine API** (`CollisionFunctions.h`, class `GEOMETRYSCRIPTINGCORE_API UGeometryScriptLibrary_CollisionFunctions` :108):
```cpp
static UPARAM(DisplayName = "Dynamic Mesh") UDynamicMesh*
SetStaticMeshCollisionFromMesh(
    UDynamicMesh* FromDynamicMesh,
    UStaticMesh* ToStaticMeshAsset,
    FGeometryScriptCollisionFromMeshOptions Options,
    UGeometryScriptDebug* Debug = nullptr);
```
`CollisionFunctions.h:117-122`. Options (`FGeometryScriptCollisionFromMeshOptions` :41-93, key fields verbatim): `bEmitTransaction=true` :46, `EGeometryScriptCollisionGenerationMethod Method = MinVolumeShapes` :49 (enum :15-24: AlignedBoxes/OrientedBoxes/MinimalSpheres/Capsules/ConvexHulls/SweptHulls/MinVolumeShapes), `MaxConvexHullsPerMesh=1` :70, `ConvexHullTargetFaceCount=25` :67, `MaxShapeCount=0` :92. Component-copy variant SetStaticMeshCollisionFromComponent :129-133.
**Export**: `GEOMETRYSCRIPTINGCORE_API` | **Module**: GeometryScriptingCore | **Guards**: collision setup writes UBodySetup on the asset — editor-only usage.
**Bucket**: self-managed — bEmitTransaction=true (function transacts itself).
**Async**: no (convex decomposition is synchronous; cap input tri count, error above cap).
**Params**: | name | aliases | type | default | required |
| handle | mesh | string | — | yes (collision source; use copy_from_static_mesh first to use the asset's own render mesh) |
| asset_path | assetPath, path | string | — | yes |
| method | — | string enum `aligned_boxes\|oriented_boxes\|min_spheres\|capsules\|convex_hulls\|swept_hulls\|min_volume` | min_volume | no |
| max_convex_hulls | — | int | 1 | no |
| hull_target_faces | — | int | 25 | no |
| max_shapes | — | int | 0 (=unlimited) | no |
**Failure modes**: cooked target ⇒ refuse (UBodySetup rebuild needs editor data); unknown method ⇒ error listing the seven values.
**Cooked**: refuses on cooked targets; works on new/loose assets.
**Verify**: mesh_asset_info returns `simpleCollisionShapeCounts {boxes, spheres, capsules, convex}` before/after (AggGeom element counts — numeric).
**Score**: U4 E3 R2 → tier 1 (collision is the #1 reason procedurally-committed meshes are unusable in PIE)
**Phase-2 verdict**: CONFIRMED — SetStaticMeshCollisionFromMesh CollisionFunctions.h:116-122 verbatim; method enum :14-24, options struct :40-93 with every cited field line exact (:46/:49/:67/:70/:92); component variant :127-133. Self-transaction verified in CollisionFunctions.cpp (:142/:200/:299 etc.); hazard grep clean (no dialogs, no slow-task).

### mesh_asset_info
**Purpose**: one read-only report of a StaticMesh asset's numeric state — LOD counts, per-LOD verts, UV channels, collision counts, Nanite settings, sockets. The verification endpoint every mutation below pairs with.
**Engine API** (`Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystem.h`, class `STATICMESHEDITOR_API UStaticMeshEditorSubsystem : public UEditorSubsystem` :25; all UFUNCTION(BlueprintCallable/Pure)):
```cpp
int32 GetLodCount(UStaticMesh* StaticMesh);                                   // :152
TArray<float> GetLodScreenSizes(UStaticMesh* StaticMesh);                     // :168
int32 GetNumberVerts(UStaticMesh* StaticMesh, int32 LODIndex);                // :376
int32 GetNumberMaterials(UStaticMesh* StaticMesh);                            // :380
int32 GetNumUVChannels(UStaticMesh* StaticMesh, int32 LODIndex);              // :395
int32 GetSimpleCollisionCount(UStaticMesh* StaticMesh);                       // :226
int32 GetConvexCollisionCount(UStaticMesh* StaticMesh);                       // :243
TEnumAsByte<ECollisionTraceFlag> GetCollisionComplexity(UStaticMesh* StaticMesh); // :234
FMeshNaniteSettings GetNaniteSettings(UStaticMesh* StaticMesh);               // :186
FName GetLODGroup(UStaticMesh* StaticMesh);                                   // :98
```
Sockets: `UStaticMesh::FindSocket` / socket array — `Runtime/Engine/Classes/Engine/StaticMesh.h:1895` (`UFUNCTION(BlueprintPure) ENGINE_API class UStaticMeshSocket* FindSocket(FName InSocketName) const;`).
**Export**: `STATICMESHEDITOR_API` (class-level) + `ENGINE_API` (UStaticMesh methods; UStaticMesh is MinimalAPI at StaticMesh.h:561-562, methods individually exported) | **Module**: `StaticMeshEditor` — NEW dep, engine-source **editor** module (no plugin!) | **Guards**: none at call site (subsystem is editor-only by module); NaniteSettings UPROPERTY is WITH_EDITORONLY_DATA (StaticMesh.h:710) but read via subsystem getter.
**Bucket**: read-only — pure query.
**Async**: no
**Params**: | name | aliases | type | default | required |
| asset_path | assetPath, path | string | — | yes (strict, must be UStaticMesh) |
Unrecognised ⇒ error.
**Failure modes**: not found / wrong class (error names param + actual class). LOD index handling: report all LODs, no index param to get wrong.
**Cooked**: **degraded** — GetLodCount/GetNumberVerts/GetNumUVChannels read RenderData and work on cooked assets; GetNaniteSettings returns defaults (editor-only data stripped); collision counts work (UBodySetup cooked in). Response includes `"cooked": true` flag so agents know which fields are trustworthy.
**Verify**: self-verifying reader. Numbers cross-check against mesh_query after copy_from_static_mesh (render_data LOD0 vert count matches GetNumberVerts(0)).
**Score**: U5 E2 R1 → tier 1 (the numeric backbone for this whole axis)
**Phase-2 verdict**: CONFIRMED — all ten subsystem lines exact (GetLodCount :151-152, GetLodScreenSizes :167-168, GetNumberVerts :375-376, GetNumberMaterials :379-380, GetNumUVChannels :394-395, GetSimpleCollisionCount :225-226, GetConvexCollisionCount :242-243, GetCollisionComplexity :233-234, GetNaniteSettings :185-186, GetLODGroup :97-98; class STATICMESHEDITOR_API :24-25); FindSocket ENGINE_API StaticMesh.h:1894-1895 on the MinimalAPI class :561. Failure mode Phase 1 missed: every UStaticMeshEditorSubsystem call runs `EditorScriptingHelpers::CheckIfInEditorAndPIE`, which returns false while PIE is running (EditorScriptingHelpers.cpp:182-186) — the getters then return 0/defaults, so this READ endpoint would silently report zeros during PIE. Handler must detect PIE and error "stop_pie first" instead. Bonus: the UNVERIFIED HasValidNaniteData item is resolved — `ENGINE_API bool HasValidNaniteData() const;` exists at StaticMesh.h:1777, so a `hasNaniteData` field is safe.

### set_static_mesh_lods
**Purpose**: generate reduction LOD chain for a StaticMesh with per-LOD triangle percentages and screen sizes — per-asset LOD authoring (pairs with known Tier-0 gap "per-actor cull/LOD overrides", which is set_property on the component; THIS is the asset side).
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "Static Mesh Utilities")
int32 SetLodsWithNotification(UStaticMesh* StaticMesh, const FStaticMeshReductionOptions& ReductionOptions, bool bApplyChanges);   // :44-45
UFUNCTION(BlueprintCallable, Category = "Editor Scripting | StaticMesh")
void SetLodReductionSettings(UStaticMesh* StaticMesh, const int32 LodIndex, const FMeshReductionSettings& ReductionOptions);        // :71-72
UFUNCTION(BlueprintCallable, Category = "Editor Scripting | StaticMesh")
bool SetLodScreenSizes(UStaticMesh* StaticMesh, const TArray<float>& ScreenSizes);                                                  // :177
UFUNCTION(BlueprintCallable, Category = "Static Mesh Utilities")
bool RemoveLods(UStaticMesh* StaticMesh);                                                                                           // :160
```
`Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystem.h` (line numbers per grep). Options struct (`Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystemHelpers.h`): `struct STATICMESHEDITOR_API FStaticMeshReductionSettings` :19 — `float PercentTriangles` :30, `float ScreenSize` :34; `struct STATICMESHEDITOR_API FStaticMeshReductionOptions` :38 — `bool bAutoComputeLODScreenSize` :49, `TArray<FStaticMeshReductionSettings> ReductionSettings` :53.
**Export**: `STATICMESHEDITOR_API` | **Module**: StaticMeshEditor (NEW, editor-only) | **Guards**: none extra (module is editor-only).
**Bucket**: self-managed — SetLodsWithNotification with bApplyChanges=true triggers a full mesh build (Modify+PostEditChange internally); wrapping the build in the blanket transaction risks giant undo snapshots. Handler calls it bare; returns before async build completes.
**Async**: build is async — response returns `lodCountRequested`; **poll with asset_compile_status**, then mesh_asset_info for final per-LOD verts.
**Params**: | name | aliases | type | default | required |
| asset_path | assetPath, path | string | — | yes |
| lods | reduction | array[{percent_triangles: float 0-1, screen_size: float 0-1}] | — | yes, len≥1; index 0 should be 1.0 (LOD0 kept) |
| auto_screen_size | — | bool | true | no |
| remove_existing_only | remove_lods | bool | false | no (calls RemoveLods instead; `lods` then optional) |
**Failure modes**: negative return ⇒ `"LOD generation failed (see editor log) — is the mesh reducible? Meshes without source model (cooked) cannot be reduced"`; empty lods array named; percent out of [0,1] named.
**Cooked**: **refuses** — reduction needs the source MeshDescription, stripped from cooked assets. Error says so and suggests copy_from_static_mesh(render_data)+simplify+create_static_mesh_asset as the workaround pipeline.
**Verify**: mesh_asset_info: lodCount == len(lods); GetNumberVerts(i) strictly decreasing; GetLodScreenSizes matches requested.
**Score**: U4 E3 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — SetLodsWithNotification :44-45, SetLodReductionSettings :71-72, SetLodScreenSizes :176-177, RemoveLods :159-160 all verbatim; helper struct lines exact (Helpers.h FStaticMeshReductionSettings :18-19, PercentTriangles :30, ScreenSize :34; FStaticMeshReductionOptions :37-38, bAutoComputeLODScreenSize :49, ReductionSettings :53). No dialogs on this path (the only slow-task dialogs in StaticMeshEditorSubsystem.cpp are in SetLODGroup :607 and the bulk convex path :1383 — neither called here). Added failure mode: refuses during PIE via CheckIfInEditorAndPIE (EditorScriptingHelpers.cpp:182-186) — returns negative; error text should tell the agent to stop_pie.
**Purpose**: set per-LOD build options — lightmap UV generation (bGenerateLightmapUVs + coordinate index), normals/tangents recompute, remove degenerates — then rebuild. Closes "lightmap UV generation" from the mission.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "Editor Scripting | StaticMesh")
void GetLodBuildSettings(const UStaticMesh* StaticMesh, const int32 LodIndex, FMeshBuildSettings& OutBuildOptions);   // :80-81
UFUNCTION(BlueprintCallable, Category = "Editor Scripting | StaticMesh")
void SetLodBuildSettings(UStaticMesh* StaticMesh, const int32 LodIndex, const FMeshBuildSettings& BuildOptions);      // :89-90
UFUNCTION(BlueprintCallable, Category = "Static Mesh Utilities")
bool SetGenerateLightmapUVs(UStaticMesh* StaticMesh, bool bGenerateLightmapUVs);                                      // :372
```
`Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystem.h`. FMeshBuildSettings is the reflected USTRUCT in `Runtime/Engine/Classes/Engine/EngineTypes.h` (fields incl. bRecomputeNormals, bRecomputeTangents, bGenerateLightmapUVs, MinLightmapResolution, SrcLightmapIndex, DstLightmapIndex — standard reflected struct, map JSON keys 1:1).
**Export**: `STATICMESHEDITOR_API` | **Module**: StaticMeshEditor | **Guards**: none extra.
**Bucket**: transacted — settings write is small; the (async) rebuild is triggered separately via build_static_mesh so undo only covers the settings.
**Async**: no (settings only; rebuild is a separate explicit endpoint).
**Params**: | name | aliases | type | default | required |
| asset_path | path | string | — | yes |
| lod | lodIndex | int | 0 | no |
| settings | build_settings | object (FMeshBuildSettings field names, e.g. bGenerateLightmapUVs, MinLightmapResolution) | — | yes; unknown key ⇒ error naming key |
**Failure modes**: lod out of range ⇒ names `lod` and reports GetLodCount; cooked asset ⇒ refuse (no source model).
**Cooked**: refuses (build settings live on FStaticMeshSourceModel, editor-only).
**Verify**: GetLodBuildSettings read-back equals requested (mesh_asset_info gains a `buildSettings` sub-object per LOD); after build_static_mesh, GetNumUVChannels increments if lightmap UVs were generated into a new channel.
**Score**: U3 E2 R2 → tier 2
**Phase-2 verdict**: CONFIRMED — GetLodBuildSettings :80-81, SetLodBuildSettings :89-90, SetGenerateLightmapUVs :371-372 all verbatim. Same PIE-guard failure mode as set_static_mesh_lods (CheckIfInEditorAndPIE — refuses during PIE).

### add_simple_collision
**Purpose**: add primitive simple-collision shapes (box/sphere/capsule/kDOP) to a StaticMesh — replicates "Collision > Add Simplified Collision" menu.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "Static Mesh Utilities")
int32 AddSimpleCollisionsWithNotification(UStaticMesh* StaticMesh, const EScriptCollisionShapeType ShapeType, bool bApplyChanges);   // :207-208
UFUNCTION(BlueprintCallable, Category = "Static Mesh Utilities")
bool RemoveCollisionsWithNotification(UStaticMesh* StaticMesh, bool bApplyChanges);                                                  // :298-299
```
`Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystem.h`. Enum verbatim (`StaticMeshEditorSubsystemHelpers.h:58-68`):
```cpp
enum class EScriptCollisionShapeType : uint8
{
    Box, Sphere, Capsule, NDOP10_X, NDOP10_Y, NDOP10_Z, NDOP18, NDOP26
};
```
**Export**: `STATICMESHEDITOR_API` | **Module**: StaticMeshEditor | **Guards**: none extra.
**Bucket**: transacted — small UBodySetup edit, undo is meaningful and safe.
**Async**: no
**Params**: | name | aliases | type | default | required |
| asset_path | path | string | — | yes |
| shape | shape_type | string enum `box|sphere|capsule|ndop10_x|ndop10_y|ndop10_z|ndop18|ndop26` | — | yes |
| replace | clear_existing | bool | false | no (RemoveCollisions first) |
**Failure modes**: negative return ⇒ `"collision add failed — asset may be cooked or have no render data"`; unknown shape lists the 8 values.
**Cooked**: refuses (UBodySetup rebuild requires editor path); error explains.
**Verify**: GetSimpleCollisionCount delta +1 (returned as before/after in response).
**Score**: U4 E1 R1 → tier 1
**Phase-2 verdict**: CONFIRMED — AddSimpleCollisionsWithNotification :207-208, RemoveCollisionsWithNotification :298-299, enum EScriptCollisionShapeType Helpers.h:57-68 all verbatim. Implementation sets GIsRunningUnattendedScript (suppresses modal prompts, .cpp:1424 region); no dialogs. PIE-guard failure mode applies (refuses during PIE).

### set_convex_collision
**Purpose**: auto-convex decomposition collision for one or many StaticMeshes (V-HACD) — the quality option beyond primitives.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "Static Mesh Utilities")
bool SetConvexDecompositionCollisionsWithNotification(UStaticMesh* StaticMesh, int32 HullCount, int32 MaxHullVerts, int32 HullPrecision, bool bApplyChanges);   // :256-257
UFUNCTION(BlueprintCallable, Category = "Static Mesh Utilities")
bool BulkSetConvexDecompositionCollisionsWithNotification(const TArray<UStaticMesh*>& StaticMeshes, int32 HullCount, int32 MaxHullVerts, int32 HullPrecision, bool bApplyChanges);   // :270-271
```
`Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystem.h` (doc comment: "Any existing collisions will be removed", "replicates Collision > Auto Convex Collision").
**Export**: `STATICMESHEDITOR_API` | **Module**: StaticMeshEditor | **Guards**: none extra.
**Bucket**: self-managed — V-HACD can take seconds on large meshes and internally notifies/rebuilds; keep out of blanket transaction. bApplyChanges=true.
**Async**: no, but handler enforces a documented input cap (refuse >500k tris per mesh with clear error) since decomposition is synchronous on the game thread.
**Params**: | name | aliases | type | default | required |
| asset_paths | asset_path, paths | array[string] or string | — | yes (bulk API when >1) |
| hull_count | hulls | int | 4 | no |
| max_hull_verts | — | int | 16 | no |
| hull_precision | precision | int | 100000 | no |
**Failure modes**: false return ⇒ error naming asset; cooked ⇒ refuse; cap exceeded names cap and tri count.
**Cooked**: refuses.
**Verify**: GetConvexCollisionCount before/after in response; expect ≤ hull_count.
**Score**: U4 E2 R2 → tier 1
**Phase-2 verdict**: CORRECTED — signatures :256-257 and :270-271 verbatim, but Phase 1 missed the implementation's UI/blocking behavior (StaticMeshEditorSubsystem.cpp): the single-mesh variant just forwards to the bulk path (:1417-1420), which (a) runs V-HACD on the thread pool but **blocks the game thread in a WaitFor(33ms) loop while pumping an FScopedSlowTask progress dialog** (Progress.MakeDialog :1383-1384, loop :1386-1391) until decomposition completes, and (b) closes any static-mesh editor tab open on the assets and REOPENS it afterwards via OpenEditorForAsset (:1409-1412). No modal input wait, but a mid-frame Slate pump + editor-window churn — the documented input-size cap is therefore mandatory, and the entry should state that a progress dialog will flash. PIE-guard failure mode applies (CheckIfInEditorAndPIE ⇒ false during PIE).
**Purpose**: enable/disable/tune Nanite on a StaticMesh asset (with build trigger) — per-asset Nanite control not reachable via set_property (WITH_EDITORONLY_DATA + build side effect).
**Engine API**:
```cpp
UFUNCTION(BlueprintPure, Category = "Static Mesh Utilities")
FMeshNaniteSettings GetNaniteSettings(UStaticMesh* StaticMesh);                                        // :185-186
UFUNCTION(BlueprintCallable, Category = "Static Mesh Utilities")
void SetNaniteSettings(UStaticMesh* StaticMesh, FMeshNaniteSettings NaniteSettings, bool bApplyChanges=true);   // :194-195
```
`Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystem.h`. FMeshNaniteSettings verbatim fields (`Runtime/Engine/Classes/Engine/EngineTypes.h:2814-2870`): `uint8 bEnabled:1` :2820, `uint8 bPreserveArea:1` :2824, `uint8 bExplicitTangents:1` :2828, `int32 PositionPrecision = MIN_int32` :2832, `int32 NormalPrecision = -1` :2836, `int32 TangentPrecision = -1` :2840, `uint32 TargetMinimumResidencyInKB = 0` :2844, `float KeepPercentTriangles = 1.0f` :2848, `float TrimRelativeError = 0.0f` :2852, `ENaniteFallbackTarget FallbackTarget = ENaniteFallbackTarget::Auto` :2856, `float FallbackPercentTriangles = 1.0f` :2860, `float FallbackRelativeError = 1.0f` :2864, `int32 DisplacementUVChannel = 0` :2868. UStaticMesh::NaniteSettings UPROPERTY at `StaticMesh.h:710` (WITH_EDITORONLY_DATA block).
**Export**: `STATICMESHEDITOR_API` | **Module**: StaticMeshEditor | **Guards**: property is editor-only data; subsystem handles it.
**Bucket**: self-managed — bApplyChanges=true triggers full Nanite build (potentially long, async).
**Async**: build async — pair with asset_compile_status.
**Params**: | name | aliases | type | default | required |
| asset_path | path | string | — | yes |
| enabled | nanite_enabled | bool | — | yes |
| fallback_percent_triangles | — | float | 1.0 | no |
| position_precision | — | int | auto | no |
| keep_percent_triangles | — | float | 1.0 | no |
**Failure modes**: cooked ⇒ refuse (no source data to build Nanite from); DDS2 note: game renderer must support Nanite for visual effect — setting still builds.
**Cooked**: refuses on cooked, works on loose/new assets.
**Verify**: GetNaniteSettings read-back (mesh_asset_info `nanite.bEnabled`); after build, RenderData Nanite resource presence reported by mesh_asset_info as `hasNaniteData` (UStaticMesh::HasValidNaniteData — check during implementation).
**Score**: U3 E2 R2 → tier 2
**Phase-2 verdict**: CONFIRMED — GetNaniteSettings :185-186, SetNaniteSettings :194-195 verbatim; every cited FMeshNaniteSettings field line exact (EngineTypes.h struct :2813-2814, fields :2820/:2824/:2828/:2832/:2836/:2840/:2844/:2848/:2852/:2856/:2860/:2864/:2868). NaniteSettings UPROPERTY confirmed inside the WITH_EDITORONLY_DATA block (StaticMesh.h:708-710, block closes :712). PIE-guard failure mode applies. Verify-step note: HasValidNaniteData exists (ENGINE_API, StaticMesh.h:1777).

### static_mesh_sockets
**Purpose**: list/create/remove sockets on a StaticMesh asset (attachment points for spawn logic) — socket transforms then editable via existing set_property on the returned socket object path.
**Engine API** (`Runtime/Engine/Classes/Engine/StaticMesh.h`):
```cpp
UFUNCTION(BlueprintCallable, Category = "StaticMesh")
ENGINE_API void AddSocket(UStaticMeshSocket* Socket);                                    // :1887-1888
UFUNCTION(BlueprintPure, Category = "StaticMesh")
ENGINE_API class UStaticMeshSocket* FindSocket(FName InSocketName) const;                // :1894-1895
UFUNCTION(BlueprintCallable, Category = "StaticMesh")
ENGINE_API void RemoveSocket(UStaticMeshSocket* Socket);                                 // :1900-1901
```
UStaticMesh is `UCLASS(... MinimalAPI, BlueprintType ...)` :561 — these methods are individually ENGINE_API so direct C++ calls link. Socket object: `NewObject<UStaticMeshSocket>(Mesh)` then set RelativeLocation/Rotation/Scale/SocketName (UStaticMeshSocket is reflected; properties via set_property or directly).
**Export**: `ENGINE_API` (method-level) | **Module**: none — Engine already linked | **Guards**: none (sockets are runtime data on UStaticMesh).
**Bucket**: transacted — small object add/remove, undo-safe.
**Async**: no
**Params**: | name | aliases | type | default | required |
| asset_path | path | string | — | yes |
| action | op | string enum `list|create|remove` | list | no |
| socket_name | name | string | — | create/remove |
| location / rotation / scale | — | vec3 | 0/0/1 | create |
**Failure modes**: create with existing name ⇒ error (FindSocket first); remove missing ⇒ error names socket_name and lists existing.
**Cooked**: **works** — sockets are runtime UPROPERTY data, present on cooked assets; but saving the modified cooked asset is blocked (document: edit is in-memory only on cooked; persistent only on loose assets).
**Verify**: list action returns array with names+transforms; count delta ±1 after create/remove.
**Score**: U3 E2 R1 → tier 2
**Phase-2 verdict**: CONFIRMED — AddSocket :1887-1888, FindSocket :1894-1895, RemoveSocket :1900-1901, all ENGINE_API method-level (class MinimalAPI :561), verbatim.

### build_static_mesh  (+ poll: asset_compile_status)
**Purpose**: explicit renderable-data rebuild after property-level edits (build settings, source model changes) — the missing "apply" step; plus a global async-compile poll endpoint.
**Engine API**:
```cpp
/**
 * Rebuilds renderable data for this static mesh, automatically made async if enabled.
 */
ENGINE_API void Build(bool bInSilent, TArray<FText>* OutErrors = nullptr);               // StaticMesh.h:1676
ENGINE_API static void BatchBuild(const TArray<UStaticMesh*>& InStaticMeshes, bool bInSilent, TFunction<bool(UStaticMesh*)> InProgressCallback = nullptr, TArray<FText>* OutErrors = nullptr);   // StaticMesh.h:1685
```
`Runtime/Engine/Classes/Engine/StaticMesh.h` — inside `#if WITH_EDITOR` block opened at :1549. Poll pair (`Runtime/Engine/Public/StaticMeshCompiler.h`, whole file `#if WITH_EDITOR` :10):
```cpp
class FStaticMeshCompilingManager : IAssetCompilingManager
{
public:
    ENGINE_API static FStaticMeshCompilingManager& Get();                                // :22
    ENGINE_API bool IsAsyncStaticMeshCompilationEnabled() const;                         // :27
    ENGINE_API int32 GetNumRemainingMeshes() const;                                      // :32
```
plus `FAssetCompilingManager::Get()` :108 / `GetNumRemainingAssets()` :128 (`Runtime/Engine/Public/AssetCompilingManager.h`) for the all-assets number (covers skeletal/textures too).
**Export**: `ENGINE_API` (method-level; both manager classes unexported but ALL cited methods carry ENGINE_API — verified) | **Module**: none — Engine linked | **Guards**: `#if WITH_EDITOR` around call sites (both APIs live in WITH_EDITOR blocks).
**Bucket**: self-managed — Build() must NOT run inside a transaction (reregisters components, swaps render data). IMPORTANT: do NOT pass OutErrors — header doc at StaticMesh.h:1672-1675 says providing OutErrors **prevents async compilation** (forces synchronous, blocks game thread mid-frame = invariant 3 violation). Call `Build(true)` silent, let it go async.
**Async**: request+poll. `build_static_mesh` kicks Build(true) and returns immediately; `asset_compile_status` (read-only, no params) reports `{remainingStaticMeshes: GetNumRemainingMeshes(), remainingAssetsTotal: GetNumRemainingAssets(), asyncEnabled: IsAsyncStaticMeshCompilationEnabled()}` — done when 0. This poll endpoint ALSO serves commit_dynamic_mesh, set_static_mesh_lods, set_nanite_settings.
**Params** (build_static_mesh): | name | aliases | type | default | required |
| asset_paths | asset_path, paths | array[string] or string | — | yes (BatchBuild when >1) |
asset_compile_status: none.
**Failure modes**: cooked asset ⇒ refuse `"'/Game/X' is cooked — no source data to build from"`; unsaved new asset builds fine (build ≠ save; remind to save_package).
**Cooked**: refuses (needs source model).
**Verify**: asset_compile_status reaches 0; mesh_asset_info counts change per the preceding edit (e.g. UV channel count after lightmap-UV enable).
**Score**: U4 E2 R2 → tier 1 (two endpoints, one registry pair)
**Phase-2 verdict**: CONFIRMED — Build :1676 and BatchBuild :1685 verbatim, inside the WITH_EDITOR region (opened at StaticMesh.h:1549); the OutErrors⇒synchronous trap is stated twice in source (FBuildParameters comment :1661-1662 and Build doc :1674) — confirmed. FStaticMeshCompilingManager::Get/IsAsyncStaticMeshCompilationEnabled/GetNumRemainingMeshes ENGINE_API method-level at StaticMeshCompiler.h:22/:27/:32 (file-wide `#if WITH_EDITOR` at :10, unexported class :19 as claimed); FAssetCompilingManager::Get :108 and GetNumRemainingAssets :128 ENGINE_API. **CROSS-AXIS COLLISION**: axis B proposes `get_asset_compilation_status` over the same counters — dedupe to ONE registry name before implementation (this pair's `asset_compile_status` also serves commit_dynamic_mesh / set_static_mesh_lods / set_nanite_settings / regenerate_skeletal_lods).

### generate_uv_channel
**Purpose**: asset-level UV projection (planar/cylindrical/box) + UV channel add/insert/remove on a StaticMesh LOD — no dynamic-mesh session needed for simple cases.
**Engine API** (`Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystem.h`, all UFUNCTION(BlueprintCallable), signatures verbatim from the 380-470 region read):
```cpp
bool AddUVChannel(UStaticMesh* StaticMesh, int32 LODIndex);
bool InsertUVChannel(UStaticMesh* StaticMesh, int32 LODIndex, int32 UVChannelIndex);
bool RemoveUVChannel(UStaticMesh* StaticMesh, int32 LODIndex, int32 UVChannelIndex);
bool GeneratePlanarUVChannel(UStaticMesh* StaticMesh, int32 LODIndex, int32 UVChannelIndex, const FVector& Position, const FRotator& Orientation, const FVector2D& Tiling);
bool GenerateCylindricalUVChannel(UStaticMesh* StaticMesh, int32 LODIndex, int32 UVChannelIndex, const FVector& Position, const FRotator& Orientation, const FVector2D& Tiling);
bool GenerateBoxUVChannel(UStaticMesh* StaticMesh, int32 LODIndex, int32 UVChannelIndex, const FVector& Position, const FRotator& Orientation, const FVector& Size);
```
Exact pins (Phase-2 verified verbatim): AddUVChannel :403-404, InsertUVChannel :413-414, RemoveUVChannel :423-424, GeneratePlanarUVChannel :436-437, GenerateCylindricalUVChannel :449-450, GenerateBoxUVChannel :462-463 (GetNumUVChannels :394-395 anchors the block).
**Export**: `STATICMESHEDITOR_API` | **Module**: StaticMeshEditor | **Guards**: none extra.
**Bucket**: transacted — MeshDescription UV write, undoable.
**Async**: no (rebuild afterwards via build_static_mesh).
**Params**: | name | aliases | type | default | required |
| asset_path | path | string | — | yes |
| action | op | string enum `add|insert|remove|planar|cylindrical|box` | — | yes |
| lod | lodIndex | int | 0 | no |
| channel | uv_channel | int | — | insert/remove/planar/cylindrical/box |
| position / orientation | — | vec3/rot | 0 | projection actions |
| tiling | — | vec2 | [1,1] | planar/cylindrical |
| size | — | vec3 | [100,100,100] | box |
**Failure modes**: false return ⇒ `"UV operation failed — channel index out of range (mesh has N channels, max 8) or cooked asset"`; unknown action lists the 6.
**Cooked**: refuses (writes MeshDescription).
**Verify**: GetNumUVChannels before/after; UV area via copy_from_static_mesh + UV-info queries in MeshUVFunctions.h (GetMeshUVSizeInfo region :312-316) if precision needed.
**Score**: U3 E2 R2 → tier 2
**Phase-2 verdict**: CORRECTED — the six signatures were verbatim-correct but only region-cited; exact line pins added in the Engine API block above (:403-463), resolving this entry's UNVERIFIED flag. PIE-guard failure mode applies (CheckIfInEditorAndPIE ⇒ functions return false during PIE).

### skeletal_mesh_info
**Purpose**: read-only numeric report on a SkeletalMesh asset — LOD count, per-LOD verts/sections, morph-target list (names + per-LOD delta counts), socket list. First skeletal visibility for agents (DDS2 is NPC-heavy).
**Engine API**: `Editor/SkeletalMeshEditor/Public/SkeletalMeshEditorSubsystem.h`, class `SKELETALMESHEDITOR_API USkeletalMeshEditorSubsystem : public UEditorSubsystem` :19:
```cpp
UFUNCTION(BlueprintCallable, Category = "Editor Scripting | SkeletalMesh")
static int32 GetLODCount(USkeletalMesh* SkeletalMesh);                          // :171-172
UFUNCTION(BlueprintPure, Category = "Skeletal Mesh Utilities")
int32 GetNumVerts(USkeletalMesh* SkeletalMesh, int32 LODIndex);                 // :45-46
UFUNCTION(BlueprintPure, Category = "Skeletal Mesh Utilities")
int32 GetNumSections(USkeletalMesh* SkeletalMesh, int32 LODIndex);              // :54-55
```
Sockets (`Runtime/Engine/Classes/Engine/SkeletalMesh.h`; USkeletalMesh is `UCLASS(hidecategories=Object, BlueprintType, MinimalAPI)` :421-422, methods individually exported):
```cpp
ENGINE_API int32 NumSockets() const;                                            // :2448
ENGINE_API USkeletalMeshSocket* GetSocketByIndex(int32 Index) const;            // :2452
ENGINE_API virtual USkeletalMeshSocket* FindSocket(FName InSocketName) const override;  // :2428
```
Morph targets: read the reflected UPROPERTY `TArray<TObjectPtr<UMorphTarget>> MorphTargets` (`SkeletalMesh.h:1784-1785`, BlueprintGetter=GetMorphTargetsPtrConv) **via FProperty reflection** — do NOT call the inline `GetMorphTargets()` accessor (:1795-1809), its body calls internal async-property gates not verified exported. Per-target vertex counts:
```cpp
ENGINE_API virtual const FMorphTargetDelta* GetMorphTargetDelta(int32 LODIndex, int32& OutNumDeltas) const;   // Runtime/Engine/Classes/Animation/MorphTarget.h:149
```
(`OutNumDeltas` = affected-vertex count; `FMorphTargetLODModel::NumBaseMeshVerts` MorphTarget.h:62 for the base count.)
**Export**: `SKELETALMESHEDITOR_API` (subsystem class-level) + `ENGINE_API` (method-level on MinimalAPI USkeletalMesh/UMorphTarget) | **Module**: `SkeletalMeshEditor` — NEW dep, engine-source editor module; Engine already linked for the rest | **Guards**: none at call sites (module editor-only).
**Bucket**: read-only.
**Async**: no
**Params**: | name | aliases | type | default | required |
| asset_path | path | string | — | yes (must be USkeletalMesh) |
| include_morphs | morphs | bool | true | no |
| include_sockets | sockets | bool | true | no |
**Failure modes**: wrong class (found class named); morph delta read on cooked asset may return 0 deltas if morph data cooked out — report `"morphDataStripped": true` rather than erroring.
**Cooked**: **degraded-but-useful** — LOD/vert/section counts read render data (work); morph target NAMES work (UPROPERTY survives cook); socket list works. Editor-only build settings absent.
**Verify**: self-verifying reader; GetNumVerts(0) cross-checks CopyMeshFromSkeletalMesh(render_data) + mesh_query vertex count.
**Score**: U4 E2 R1 → tier 1
**Phase-2 verdict**: CORRECTED — subsystem citations exact (class :18-19 SKELETALMESHEDITOR_API, GetLODCount :171-172, GetNumVerts :45-46, GetNumSections :54-55); socket methods NumSockets :2446-2448 / GetSocketByIndex :2450-2452 / FindSocket :2428 all ENGINE_API confirmed; MorphTarget.h pins exact (NumBaseMeshVerts :62, GetMorphTargetDelta :149, UMorphTarget MinimalAPI :124 with ENGINE_API methods). **Morph-route claim fixed**: `WaitUntilAsyncPropertyReleased` IS exported — `ENGINE_API void WaitUntilAsyncPropertyReleased(ESkeletalMeshAsyncProperties, ESkinnedAssetAsyncPropertyLockType) const;` SkeletalMesh.h:2708 — so the inline `GetMorphTargets()` accessor (:1795-1801) links fine from MifBridge and the FProperty-reflection detour is unnecessary (still valid as an alternative). Replacement caveat: the accessor synchronously WAITS if the skeletal mesh is mid-async-compilation — normally instant, but check the compile-status poll first when in doubt. Also added: subsystem GetNumVerts/GetNumSections return 0/INDEX_NONE during PIE (CheckIfInEditorAndPIE, EditorScriptingHelpers.cpp:182-186) — a read endpoint silently reporting zeros during PIE is a trap; detect PIE and error instead.

### skeletal_mesh_sockets
**Purpose**: create/rename/remove sockets on a SkeletalMesh asset (attach points for props/weapons — direct DDS2 modding use).
**Engine API**:
```cpp
ENGINE_API void AddSocket(USkeletalMeshSocket* InSocket, bool bAddToSkeleton=false);   // Runtime/Engine/Classes/Engine/SkeletalMesh.h:2421
```
plus rename via subsystem:
```cpp
UFUNCTION(BlueprintCallable, Category = "Skeletal Mesh Utilities", meta = (ScriptMethod))
static bool RenameSocket(USkeletalMesh* SkeletalMesh, FName OldName, FName NewName);   // SkeletalMeshEditorSubsystem.h:161-162
```
Socket removal: no exported RemoveSocket on USkeletalMesh — mutate the reflected `Sockets` UPROPERTY array (`SkeletalMesh.h:2236` `TArray<TObjectPtr<class USkeletalMeshSocket>> Sockets;`) via reflection + Modify(), then `ENGINE_API void RebuildSocketMap();` `SkeletalMesh.h:2463`.
**Export**: `ENGINE_API` / `SKELETALMESHEDITOR_API` | **Module**: SkeletalMeshEditor (rename only; rest Engine) | **Guards**: none.
**Bucket**: transacted — small reflected edits, undo-safe.
**Async**: no
**Params**: | name | aliases | type | default | required |
| asset_path | path | string | — | yes |
| action | op | string enum `list|create|rename|remove` | list | no |
| socket_name | name | string | — | create/rename/remove |
| new_name | — | string | — | rename |
| bone | bone_name, parent_bone | string | — | create (validated against reference skeleton; invalid ⇒ error listing candidate bone names) |
| location / rotation / scale | — | vec3 | 0/0/1 | create |
| add_to_skeleton | — | bool | false | create (AddSocket's bAddToSkeleton) |
**Failure modes**: duplicate name (FindSocket pre-check); unknown bone ⇒ error names `bone` and suggests candidates; RenameSocket false ⇒ error.
**Cooked**: sockets survive cook (runtime data) — edits apply in memory; persistent save only for loose assets (same caveat as static_mesh_sockets, stated in response).
**Verify**: skeletal_mesh_info socket count ±1; FindSocket returns the new transform values.
**Score**: U3 E2 R1 → tier 2
**Phase-2 verdict**: CORRECTED — AddSocket :2420-2421 and RenameSocket :161-162 verbatim; RebuildSocketMap :2463 ENGINE_API confirmed; `Sockets` UPROPERTY :2235-2236 confirmed and it is **private** (`private:` at :2230) — only the FProperty-reflection route works, exactly as proposed. Guards field fixed: USkeletalMesh::AddSocket sits inside `#if WITH_EDITOR` (block ends `#endif // WITH_EDITOR` at SkeletalMesh.h:2422) — the entry said "Guards: none"; call sites need WITH_EDITOR (moot for the editor-only module, but the field must be accurate). Verification caveat: NumSockets() counts mesh AND skeleton sockets (doc :2446) — use the reflected Sockets array length for the ±1 delta, not NumSockets().

### create_physics_asset
**Purpose**: generate a UPhysicsAsset for a SkeletalMesh "as if created through FBX import" — one call, no UI; makes imported/derived skeletal meshes simulate and collide.
**Engine API**:
```cpp
/**
 * This function creates a PhysicsAsset for the given SkeletalMesh with the same settings as if it were created through FBX import
 */
UFUNCTION(BlueprintCallable, Category = "Editor Scripting | SkeletalMesh")
static UPhysicsAsset* CreatePhysicsAsset(USkeletalMesh* SkeletalMesh);          // SkeletalMeshEditorSubsystem.h:219-220
```
Lower-level alternative (more knobs, if ever needed): `PHYSICSUTILITIES_API bool CreateFromSkeletalMesh(UPhysicsAsset* PhysicsAsset, USkeletalMesh* SkelMesh, const FPhysAssetCreateParams& Params, FText& OutErrorMessage, bool bSetToMesh = true);` — `Developer/PhysicsUtilities/Public/PhysicsAssetUtils.h:136` (namespace FPhysicsAssetUtils :124; module `PhysicsUtilities`, Developer, exported — verified).
**Export**: `SKELETALMESHEDITOR_API` (subsystem) / `PHYSICSUTILITIES_API` (alt) | **Module**: SkeletalMeshEditor (alt adds PhysicsUtilities) | **Guards**: none extra.
**Bucket**: self-managed — creates + registers a new asset package (invariant 2).
**Async**: no (body-setup generation synchronous; acceptable for typical humanoid bone counts).
**Params**: | name | aliases | type | default | required |
| asset_path | path | string | — | yes (source USkeletalMesh) |
**Failure modes**: nullptr return ⇒ `"physics asset creation failed — mesh may have no render data or degenerate bone extents (see log)"`; cooked source: bodies computed from runtime vertex data — flag needs-testing caveat rather than blanket refuse.
**Cooked**: works-with-caveat (uses runtime vertex data; the NEW asset is loose). The back-reference written into a cooked source mesh won't persist — response reports physics asset path + whether source link persisted.
**Verify**: response returns created asset path + body count (UPhysicsAsset::SkeletalBodySetups.Num()) + constraint count — ≥1 for any skinned mesh; find_assets sees the new asset.
**Score**: U4 E2 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — CreatePhysicsAsset :219-220 verbatim (incl. the FBX-import doc comment :215); FPhysicsAssetUtils::CreateFromSkeletalMesh PHYSICSUTILITIES_API at PhysicsAssetUtils.h:136 (namespace :124) verbatim. Hazard sweep of SkeletalMeshEditorSubsystem.cpp:803-848: NO dialogs — failures are UE_LOG only, and the failure-path cleanup is `ObjectTools::DeleteObjects({...}, /*bShowConfirmation=*/false)` (:846), non-modal. Implementation detail to encode: the destination is DERIVED, `<SkelMeshPackage>_PhysicsAsset` (.cpp:811-812) — there is no path parameter, so the response must return the derived path; for cooked source meshes that derived package sits in the cooked mount (reinforces the works-with-caveat stance). PIE-guard applies (CheckIfInEditorAndPIE). **CROSS-AXIS COLLISION**: axis G3 proposes the same `create_physics_asset` name — dedupe to one owner before implementation.

### regenerate_skeletal_lods
**Purpose**: (re)generate a skeletal mesh LOD chain via the built-in reducer.
**Engine API**:
```cpp
UFUNCTION(BlueprintCallable, Category = "Skeletal Mesh Utilities", meta = (ScriptMethod))
static bool RegenerateLOD(USkeletalMesh* SkeletalMesh, int32 NewLODCount = 0, bool bRegenerateEvenIfImported = false, bool bGenerateBaseLOD = false);   // SkeletalMeshEditorSubsystem.h:36-37
```
**Export**: `SKELETALMESHEDITOR_API` | **Module**: SkeletalMeshEditor | **Guards**: none extra.
**Bucket**: self-managed — triggers skeletal mesh rebuild (skinned-asset compilation is async in 5.3; SkeletalMeshCompiler.h exists — see UNVERIFIED).
**Async**: request; poll asset_compile_status (total-assets counter covers skinned assets).
**Params**: | name | aliases | type | default | required |
| asset_path | path | string | — | yes |
| lod_count | new_lod_count | int | 0 (=keep) | no |
| regenerate_imported | — | bool | false | no |
**Failure modes**: false return ⇒ `"LOD regeneration failed — mesh reduction module unavailable or asset is cooked (no source model)"`.
**Cooked**: refuses (reduction needs source data).
**Verify**: skeletal_mesh_info: lodCount == requested; GetNumVerts decreasing per LOD.
**Score**: U3 E2 R2 → tier 2
**Phase-2 verdict**: CONFIRMED — RegenerateLOD :36-37 verbatim. Implementation (.cpp:39-55) sets GIsRunningUnattendedScript (suppresses modal prompts) and calls FLODUtilities::RegenerateLOD synchronously — the REDUCTION itself runs on the game thread (seconds for dense meshes); the request/poll pairing only covers the post-change compile, so enforce an input-size cap or document the stall. PIE-guard applies (returns false during PIE, .cpp:43-46).

### merge_static_mesh_actors
**Purpose**: merge multiple placed StaticMeshComponents into ONE new StaticMesh asset (draw-call optimization, prop combining) — the editor "Merge Actors" tool, headless.
**Engine API** (`Developer/MeshMergeUtilities/Public/IMeshMergeUtilities.h`, `class MESHMERGEUTILITIES_API IMeshMergeUtilities` :38):
```cpp
virtual void MergeComponentsToStaticMesh(const TArray<UPrimitiveComponent*>& ComponentsToMerge, UWorld* World, const FMeshMergingSettings& InSettings, UMaterialInterface* InBaseMaterial, UPackage* InOuter, const FString& InBasePackageName, TArray<UObject*>& OutAssetsToSync, FVector& OutMergedActorLocation, const float ScreenSize, bool bSilent /*= false*/) const = 0;   // :67
```
Access via module interface (`Developer/MeshMergeUtilities/Public/MeshMergeModule.h:9-13`):
```cpp
class MESHMERGEUTILITIES_API IMeshMergeModule : public IModuleInterface
{
public:
    virtual const IMeshMergeUtilities& GetUtilities() const = 0;
```
⇒ `FModuleManager::LoadModuleChecked<IMeshMergeModule>("MeshMergeUtilities").GetUtilities().MergeComponentsToStaticMesh(...)`. FMeshMergingSettings is the reflected USTRUCT from `Runtime/Engine/Classes/Engine/MeshMerging.h` (included by StaticMeshEditorSubsystem.h:10) — map JSON keys 1:1 with unknown-key errors.
**Export**: `MESHMERGEUTILITIES_API` (interface class; calls are virtual through the module pointer) | **Module**: `MeshMergeUtilities` — NEW dep (Developer module; fine for editor-only MifBridge) | **Guards**: editor-only usage (material baking requires editor).
**Bucket**: self-managed — creates packages/assets, bakes materials, potentially seconds of work; must not sit in the blanket transaction.
**Async**: no (synchronous by design; document the stall and cap component count ≤ 64 per call, error above).
**Params**: | name | aliases | type | default | required |
| actors | actor_labels, names | array[string] | — | yes (resolve like select_level_actors; each contributes its StaticMeshComponents) |
| output_path | asset_path | string | — | yes (base package name, e.g. `/Game/Mods/Merged/SM_Block01`) |
| merge_materials | bake_materials | bool | false | no (FMeshMergingSettings::bMergeMaterials) |
| pivot_at_zero | — | bool | false | no |
| replace_source_actors | replace | bool | false | no (spawn merged actor + delete sources) |
**Failure modes**: <2 valid components ⇒ error naming resolved count; output path exists ⇒ error; cooked-source materials bake unreliably ⇒ warn field `"materialBakeUnreliable": true`.
**Cooked**: sources may be cooked (render-data path works); output is a NEW loose asset.
**Verify**: new asset exists; mesh_asset_info verts ≈ sum of source LOD0 verts (± welding); OutMergedActorLocation returned; scene_report actor count drops after replace_source_actors.
**Score**: U4 E4 R3 → tier 2 (valuable, needs design care around material baking)
**Phase-2 verdict**: CONFIRMED — IMeshMergeUtilities class :38 MESHMERGEUTILITIES_API and MergeComponentsToStaticMesh :67 verbatim; IMeshMergeModule MeshMergeModule.h:9-13 verbatim. Hazard sweep of MeshMergeUtilities.cpp: the only slow-task dialog (`SlowTask.MakeDialog()` :1501) is in CreateProxyMesh, NOT in MergeComponentsToStaticMesh — the merge path is dialog-free; still pass bSilent=true.

## Compositions (no new endpoint needed)

- **Per-actor cull/LOD overrides** (mission Tier-0 item): `set_property` on the placed StaticMeshComponent — `ForcedLodModel`, `MinDrawDistance`, `LDMaxDrawDistance`, `bOverrideMinLOD` are reflected UPROPERTYs on UStaticMeshComponent/UPrimitiveComponent. No new endpoint; document objectPaths in the cookbook. (Asset-side LOD authoring IS new — set_static_mesh_lods above.)
- **Assign material to a mesh asset slot**: `set_property` objectPath `<mesh>.StaticMaterials[i].MaterialInterface`. Per-LOD-section remap exists as `UStaticMeshEditorSubsystem::SetLODMaterialSlot` (StaticMeshEditorSubsystem.h:350) if reflection proves insufficient.
- **Place a committed mesh in the level**: existing `spawn_actor_in_level` with the new asset path; verify with existing `get_actor_bounds` (bounds should match mesh_query bbox × actor scale).
- **Mesh-from-spline**: existing `get_spline_points` locates the spline; `mesh_op` op=`sweep_spline` consumes the component directly (ConvertSplineToPolyPath + AppendSweepPolyline internally). No separate endpoint.
- **bAllowCPUAccess**: `SetAllowCPUAccess` (StaticMeshEditorSubsystem.h:384) ≈ set_property on the reflected `bAllowCPUAccess` — use set_property.
- **Read geometry of a placed component (any type)**: `CopyMeshFromComponent` (`SceneUtilityFunctions.h:55-60`, options struct :16-28) — exposed as a `source` variant inside copy_from_static_mesh (`actor` + `component` params) rather than a separate endpoint.
- **Nanite on new assets**: commit_dynamic_mesh already carries `bApplyNaniteSettings`/`NewNaniteSettings` (MeshAssetFunctions.h:80-88) — set_nanite_settings is only for EXISTING assets.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

1. **GeometryScripting plugin is disabled in this project** — `GeometryScripting.uplugin` has no `EnabledByDefault` key (= false), .uproject doesn't list it, and project-enabled ModelingToolsEditorMode does NOT chain-enable it (its .uplugin deps: MeshModelingToolset/Exp, MeshLODToolset, ToolPresets — none reference GeometryScripting; all four .uplugins read). Every GeometryScript endpoint carries a one-time enable cost: plugin reference in MifBridge.uplugin (recommended) or .uproject entry.
2. **PCG not viable this cycle** — `Experimental/PCG/PCG.uplugin`: `"EnabledByDefault": false`, VersionName 0.1, not project-enabled. Enabling an experimental 0.1 framework solely for bridge endpoints is cost without a mission driver; 5.3 PCG API churned heavily before 5.4. Negative entry only; revisit if the project adopts PCG content.
3. **Modeling Tools Editor Mode interactive tools are UI-locked** — the tools (UInteractiveTool subclasses in MeshModelingTools*) require a live UInteractiveToolManager + EdMode toolkit host + viewport input routing; no headless entry point. The exported utility that IS callable, `UEditorModelingObjectsCreationAPI` (`class MODELINGCOMPONENTSEDITORONLY_API UEditorModelingObjectsCreationAPI : public UModelingObjectsCreationAPI`, `Plugins/Runtime/MeshModelingToolset/Source/ModelingComponentsEditorOnly/Public/EditorModelingObjectsCreationAPI.h:29`, `CreateMeshObject` :35, `CreateStaticMeshAsset` :93), duplicates `CreateNewStaticMeshAssetFromMesh` (GeometryScriptingEditor) — documented as alternative, not proposed.
4. **UStaticMeshEditorSubsystem has NO socket API** — sockets go direct through `UStaticMesh::AddSocket/FindSocket/RemoveSocket` (ENGINE_API, StaticMesh.h:1888/1895/1901). Not a blocker, just a trap: old EditorStaticMeshLibrary docs suggest otherwise.
5. **USkeletalMesh has NO exported RemoveSocket** — removal must go through reflection on the `Sockets` UPROPERTY (SkeletalMesh.h:2236) + `RebuildSocketMap()` (:2463, ENGINE_API). Precedent-compatible (reflected-array edit) but the one non-obvious piece of skeletal_mesh_sockets.
6. **USkeletalMesh::GetMorphTargets() inline accessor is a link trap** — inline body (SkeletalMesh.h:1795-1809) calls `WaitUntilAsyncPropertyReleased` (internal async-property machinery, export not verified). Route around via FProperty reflection on the `MorphTargets` UPROPERTY (:1784-1785). Same finding class as the ULandscapeComponent::UpdateCollisionData precedent.
   **Phase-2: OVERTURNED — `ENGINE_API void WaitUntilAsyncPropertyReleased(ESkeletalMeshAsyncProperties AsyncProperties, ESkinnedAssetAsyncPropertyLockType LockType = ...) const;` exists at SkeletalMesh.h:2708 (and the USkinnedAsset base helper is ENGINE_API at SkinnedAsset.h:301). The inline GetMorphTargets() accessor therefore LINKS from external modules — it is NOT a link trap. Real caveat is behavioral, not linkage: the accessor synchronously waits if the mesh is mid-async-compilation. Reflection detour remains a valid alternative but is not required.**
7. **UStaticMesh::Build(bInSilent, OutErrors) forces SYNCHRONOUS build when OutErrors is non-null** (doc comment StaticMesh.h:1672-1675: "This will prevent async static mesh compilation") — a blocking multi-second game-thread stall = invariant-3 violation. build_static_mesh must never pass OutErrors; errors come from the compile-status poll + log.
8. **EditorScriptingUtilities plugin is EnabledByDefault:false in this fork** (`Editor/EditorScriptingUtilities/EditorScriptingUtilities.uplugin:13`) — do NOT route through UEditorStaticMeshLibrary/EditorLevelLibrary; every needed capability exists in engine-source subsystems (StaticMeshEditor / SkeletalMeshEditor modules) with zero plugin cost.
9. **Skin-weight editing deferred** — UGeometryScriptLibrary_MeshBoneWeightFunctions (MeshBoneWeightFunctions.h:192, 16 UFUNCTIONs) is exported and callable, but a correct end-to-end story needs CopyMeshToSkeletalMesh + skeleton compatibility + rebind + rebuild; high blast radius, no mission driver ⇒ tier 3, not proposed this cycle.
10. **Cooked-content wall for all source-model mutations** — LOD generation, convex decomposition, UV channel writes, Nanite builds, build settings all require FStaticMeshSourceModel/MeshDescription, stripped from .pak-mounted base-game meshes. The ONLY geometry route for base-game content is read-only: copy_from_static_mesh(lod_type=render_data) → derive → create_static_mesh_asset (new asset). Every mutating endpoint above declares refuse-on-cooked with that exact redirect.
   **Phase-2 addendum: the render_data read route itself needs a guard — see copy_from_static_mesh verdict (stock CopyMeshFromStaticMesh dereferences GetSourceModel(LOD) for BuildScale in WITH_EDITOR builds, MeshAssetFunctions.cpp:155-158, which asserts on source-model-less cooked meshes; direct FStaticMeshLODResourcesToDynamicMesh::Convert is the safe fallback).**

_Phase-2 verification of negatives: items 1–5 and 7–10 re-verified against source (GeometryScripting.uplugin re-read: no EnabledByDefault key, modules/deps as stated; PCG.uplugin `"EnabledByDefault": false` :13, VersionName 0.1 :4; EditorModelingObjectsCreationAPI.h class :29 / CreateMeshObject :35 / CreateStaticMeshAsset :93; zero "Socket" matches in StaticMeshEditorSubsystem.h; zero "RemoveSocket" matches in SkeletalMesh.h; OutErrors⇒sync doc at StaticMesh.h:1661+:1674; EditorScriptingUtilities.uplugin `"EnabledByDefault" : false` :13; MeshBoneWeightFunctions.h class :192). Item 6 OVERTURNED as marked above._

## UNVERIFIED

- `UStaticMesh::HasValidNaniteData()` referenced in set_nanite_settings verify step — presence/signature not read this sweep; implementer must grep StaticMesh.h first (fallback: NaniteSettings read-back only). **Phase-2: RESOLVED — `ENGINE_API bool HasValidNaniteData() const;` StaticMesh.h:1777.**
- generate_uv_channel exact line numbers for GeneratePlanar/Cylindrical/BoxUVChannel — signatures verbatim from a sed window without line prefixes (region ~:395-466); pin :NNN at implementation. **Phase-2: RESOLVED — pinned in the entry (:403-404/:413-414/:423-424/:436-437/:449-450/:462-463), all verbatim.**
- FSkeletalMeshCompilingManager (`Runtime/Engine/Public/SkeletalMeshCompiler.h`) — file exists (ls verified) but methods/exports not read; asset_compile_status v1 uses FAssetCompilingManager totals instead. **Phase-2: existence re-confirmed (SkinnedAssetCompiler.h also present); methods still unread — the FAssetCompilingManager-totals design stands.**
- MeshLODToolset "AutoLOD" (UGenerateStaticMeshLODProcess) — plugin chain-active via ModelingToolsEditorMode, would give one-call LOD-chain-with-collision generation; headers not read ⇒ tier-3 candidate for Phase 2.
- ADynamicMeshActor / UDynamicMeshComponent as LEVEL objects (spawn a dynamic mesh directly into the map, serialized in level) — GeometryFramework classes exist (DynamicMeshActor.h in module listing); lifetime/serialization/cook interaction with RamaSave + cooked maps unexamined.
- UGeometryScriptLibrary_MeshBakeFunctions (normal/AO baking to textures, 14 UFUNCTIONs at MeshBakeFunctions.h:446) — located, signatures not read; pairs with a future texture axis.

## Coverage log

**Covered this sweep (files actually read):**
- GeometryScripting.uplugin, ModelingToolsEditorMode.uplugin, MeshModelingToolset(+Exp)/MeshLODToolset/GeometryProcessing/PCG/EditorScriptingUtilities .uplugins — enabled-state chains.
- All 39 UGeometryScriptLibrary classes enumerated (grep over plugin Source) + per-header UFUNCTION counts; headers read in full or targeted regions: MeshAssetFunctions.h (full), MeshPrimitiveFunctions.h (:100-295), MeshBooleanFunctions.h (:100-196), MeshModelingFunctions.h (:296-370 + grep), MeshUVFunctions.h (:222-316), MeshQueryFunctions.h (:14-93), MeshRemeshFunctions.h (:140-162), MeshSimplifyFunctions.h (:83-142), CollisionFunctions.h (full), SceneUtilityFunctions.h (targeted), GeometryScriptTypes.h (targeted: EGeometryScriptLODType :40-46, UGeometryScriptDebug :627), CreateNewAssetUtilityFunctions.h (grep + :107-193 region).
- Runtime/GeometryFramework/Public/UDynamicMesh.h — FULL read (UDynamicMesh + UDynamicMeshPool).
- Editor/StaticMeshEditor/Public/StaticMeshEditorSubsystem.h (536 lines; reads :1-100, :180-260, :380-470 + full grep) + StaticMeshEditorSubsystemHelpers.h (structs :19-53, enum :58-68).
- Runtime/Engine/Classes/Engine/StaticMesh.h — targeted (:560-575 class decl, :700-715 Nanite, :1665-1700 Build, :1880-1905 sockets, WITH_EDITOR guard map).
- Runtime/Engine/Public/StaticMeshCompiler.h (:1-55), AssetCompilingManager.h (grep :66-128).
- Runtime/Engine/Classes/Engine/EngineTypes.h :2814-2870 (FMeshNaniteSettings verbatim).
- Editor/SkeletalMeshEditor/Public/SkeletalMeshEditorSubsystem.h — FULL read (221 lines).
- Runtime/Engine/Classes/Engine/SkeletalMesh.h — targeted (:415-424 class decl, :1783-1830 morphs, :2236-2480 sockets).
- Runtime/Engine/Classes/Animation/MorphTarget.h — targeted (:53-166).
- Developer/PhysicsUtilities/Public/PhysicsAssetUtils.h (:124-142), Developer/MeshMergeUtilities/Public/IMeshMergeUtilities.h (:38, :67) + MeshMergeModule.h (FULL).
- Plugins/Runtime/MeshModelingToolset/.../EditorModelingObjectsCreationAPI.h (grep :14-95).
- D:/DDS2SDK/Game/DrugDealerSimulator2.uproject — grep for geometry/PCG/EditorScripting entries (none).

**Remaining for Phase 2:** MeshBakeFunctions deep-dive; MeshLODToolset AutoLOD; ADynamicMeshActor-in-level design; skeletal skin-weight pipeline (tier 3); FSkeletalMeshCompilingManager exact status fields; ContainmentFunctions (convex hull/SDF) as extra mesh_op tier; exact line pins flagged in UNVERIFIED.

**Proposed endpoints (24 registry entries):** create_dynamic_mesh, list_dynamic_meshes, release_dynamic_mesh, mesh_op, mesh_query, commit_dynamic_mesh, copy_from_static_mesh, create_static_mesh_asset, set_static_mesh_collision_from_mesh, mesh_asset_info, set_static_mesh_lods, set_lod_build_settings, add_simple_collision, set_convex_collision, set_nanite_settings, static_mesh_sockets, build_static_mesh, asset_compile_status, generate_uv_channel, skeletal_mesh_info, skeletal_mesh_sockets, create_physics_asset, regenerate_skeletal_lods, merge_static_mesh_actors. (build_static_mesh/asset_compile_status form one request/poll pair; the dynamic-mesh session trio is one design unit.)

**Phase-2 adversarial verification (2026-07-26):** all 23 entry sections re-verified against D:/UE532 source — every cited signature re-opened; 17 CONFIRMED, 6 CORRECTED (create_dynamic_mesh pool-failsafe claim; copy_from_static_mesh cooked-RenderData assert guard; set_convex_collision hidden slow-task dialog + game-thread wait loop + editor reopen; generate_uv_channel line pins; skeletal_mesh_info morph-route fix; skeletal_mesh_sockets WITH_EDITOR guard), 0 DEMOTED. Systemic finding added across subsystem-backed entries: UStaticMeshEditorSubsystem/USkeletalMeshEditorSubsystem calls run EditorScriptingHelpers::CheckIfInEditorAndPIE (UNREALED_API, EditorScriptingHelpers.h:12; impl .cpp:170-187) which returns false during PIE — all such endpoints (including READS) must detect PIE and error explicitly. Negatives: 10 checked, #6 OVERTURNED (WaitUntilAsyncPropertyReleased is ENGINE_API, SkeletalMesh.h:2708). Cross-axis collisions flagged: create_physics_asset (axis G3), asset_compile_status vs axis B get_asset_compilation_status.
