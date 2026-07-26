# Axis D — Materials and rendering
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._

All engine citations are relative to `D:/UE532/Engine/Source`. Plugin citations are relative to
`D:/DDS2SDK/Game/Plugins/MifBridge`.

## Surface inventory

Headers actually opened and read (not from memory):

| Surface | Path | What was enumerated |
|---|---|---|
| UMaterialEditingLibrary FULL walk | `Editor/MaterialEditor/Public/MaterialEditingLibrary.h` (397 lines, all read) | `UCLASS` at line 56, `class MATERIALEDITOR_API UMaterialEditingLibrary` at line 57. ~50 UFUNCTIONs: expression create/delete/duplicate (94–123), usage (132–140), connect (149–159), recompile/layout (165–171), material default param getters (175–188), graph introspection getters (192–224), function editing (230–261), MIC editing (267–332), parameter-name enumeration (336–348), parameter source lookup (358–388), `GetStatistics` (392), `GetNaniteOverrideMaterial` (396). `FMaterialStatistics` USTRUCT lines 17–53 (8 numeric fields). |
| Material asset factories | `Editor/UnrealEd/Classes/Factories/` dir listing | 8 material-related factories found: MaterialFactoryNew, MaterialFunctionFactoryNew, MaterialFunctionInstanceFactory, MaterialFunctionMaterialLayerFactory, MaterialFunctionMaterialLayerBlendFactory, MaterialInstanceConstantFactoryNew, MaterialParameterCollectionFactoryNew, PhysicalMaterialFactoryNew. Read in full: MaterialFactoryNew.h, MaterialFunctionFactoryNew.h, MaterialParameterCollectionFactoryNew.h, MaterialInstanceConstantFactoryNew.h, Factory.h (base, lines 20–114). |
| UMaterialExpression subclass census | `Runtime/Engine/Classes/Materials/MaterialExpression*.h` | **244 headers**, **277 class declarations deriving (directly or transitively) from UMaterialExpression**, 8 of them `UCLASS(abstract…)`. Plus **8 landscape expression headers** in `Runtime/Landscape/Classes/Materials/` (LandscapeGrassOutput, LayerBlend, LayerCoords, LayerSample, LayerSwitch, LayerWeight, PhysicalMaterialOutput, VisibilityMask). Base class `UMaterialExpression` is `UCLASS(abstract, Optional, BlueprintType, hidecategories=Object, MinimalAPI)` — `Runtime/Engine/Classes/Materials/MaterialExpression.h:183-184`. **`Optional` means expression objects are STRIPPED from cooked packages** — the load-bearing cooked-content fact for this whole axis. |
| EMaterialProperty enum | `Runtime/Engine/Public/SceneTypes.h:159-200` | Full value list captured (see connect_material_property). |
| MPC | `Runtime/Engine/Classes/Materials/MaterialParameterCollection.h` lines 30–160 | `UCLASS(hidecategories=object, MinimalAPI, BlueprintType)` line 80. ScalarParameters/VectorParameters arrays, WITH_EDITOR setters (unexported — negative result), BlueprintCallable getters. |
| Textures | `Runtime/Engine/Classes/Engine/Texture.h` (UCLASS 1083, Source 1092–1096, settings 1235/1320/1336/1353, UpdateResource 1499), `Runtime/Engine/Classes/Engine/Texture2D.h:157` | Source validity constraint documented in-header. |
| Shader compilation | `Runtime/Engine/Public/ShaderCompiler.h` (class 589, methods 740–814, extern 928) | Poll surface verified. |
| Lighting / build | `Editor/UnrealEd/Public/EditorBuildUtils.h` (class 73, build ids 18–28, EditorBuild 149, IsBuildCurrentlyRunning 213), `Editor/UnrealEd/Classes/Editor/EditorEngine.h` (UCLASS 290–291, IsLightingBuildCurrentlyRunning 1585, BuildReflectionCaptures 2321), `Editor/UnrealEd/Public/Editor.h:30` (GEditor extern) | |
| Render targets | `Runtime/Engine/Classes/Kismet/KismetRenderingLibrary.h` (UCLASS 33; methods 49–157) | Class is MinimalAPI but every method carries method-level `ENGINE_API`. |
| Per-actor cull/LOD | `Runtime/Engine/Classes/Components/PrimitiveComponent.h` (class 262, MinDrawDistance 282, LDMaxDrawDistance 286, CachedMaxDrawDistance 293, bEnableAutoLODGeneration 317, SetCullDistance 2747), `Runtime/Engine/Classes/Components/StaticMeshComponent.h` (class 99, ForcedLodModel 105, MinLOD 116), `Runtime/Engine/Classes/Components/MeshComponent.h` (OverrideMaterials 33, GetNumMaterials 101, GetMaterial 102), `Runtime/Engine/Classes/Components/ActorComponent.h:922` (MarkRenderStateDirty), `Runtime/Engine/Classes/Engine/StaticMesh.h` (NaniteSettings 710, IsNaniteEnabled 870, GetNumLODs 1767) | |
| RVT | `Runtime/Engine/Classes/VT/RuntimeVirtualTexture.h` (UCLASS 14–15, props 25–47, getters 88–108), `Editor/VirtualTexturingEditor/Classes/RuntimeVirtualTextureFactory.h` (full), `Editor/VirtualTexturingEditor/Private/RuntimeVirtualTextureFactory.cpp` (full, 29 lines) | Factory unexported; cpp proves it is a trivial NewObject wrapper. |
| Material layers | `Runtime/Engine/Classes/Materials/MaterialLayersFunctions.h` (RuntimeData 122–130, FMaterialLayersFunctions 193–345), `Runtime/Engine/Classes/Materials/MaterialInstance.h` (SetMaterialLayers 505/785, GetMaterialLayers 766), `Runtime/Engine/Classes/Materials/MaterialInterface.h:553` | Viable on instances — see proposal 25. |
| UMaterial / UMaterialFunction object model | `Runtime/Engine/Classes/Materials/Material.h` (UCLASS 414–415, MaterialDomain 449, BlendMode 453, UMaterialEditorOnlyData 310, PostEditChangeProperty 1192, GetDefaultMaterial 1221, GetExpressions 1242), `Runtime/Engine/Classes/Materials/MaterialFunction.h` (UCLASS 32, GetExpressions 183) | |
| Expression key-property harvest | 20 expression headers read individually (table below) | |
| Plugin precedents read | `Source/MifBridge/Private/MifBridgeAuthoring.cpp:280-353` (create_material_instance — the factory-linkage precedent), `Source/MifBridge/Private/MifBridgeCooked.cpp:784-880` (H_diagnose_landscape_draws — the render-thread structured-report precedent), `Source/MifBridge/MifBridge.Build.cs` | MaterialEditor is NOT in the current dep list → new module dep, flagged per-endpoint. |
| MaterialEditingLibrary.cpp spot-check | `Editor/MaterialEditor/Private/MaterialEditingLibrary.cpp:797-806` | `GetMaterialPropertyInputNode` reads `Material->GetExpressionInputForProperty(Property)` directly — despite the header comment saying "from an active material editor", **no open editor window is required**. |

### Expression catalogue — the ~30 classes an add_material_expression endpoint should document
All paths relative to `Runtime/Engine/Classes/Materials/` unless prefixed `[Landscape]` =
`Runtime/Landscape/Classes/Materials/`. "Key UPROPERTYs" are the members the endpoint's
`properties{}` object must be able to set (all are plain UPROPERTY data members — settable via
the same FProperty-import machinery as set_property; data members need no export macro).

| Class (short name accepted by endpoint) | Header:line of class decl | Key UPROPERTYs (header:line) |
|---|---|---|
| TextureSample | MaterialExpressionTextureSample.h (derives TextureBase) | `Texture` (MaterialExpressionTextureBase.h:25), `SamplerType` (:28); `MipValueMode` (MaterialExpressionTextureSample.h:48), `ConstCoordinate` (:75), inputs `Coordinates`/`TextureObject`/`MipValue` (:22/:29/:33) |
| TextureObject | MaterialExpressionTextureObject.h:18 | `Texture`, `SamplerType` (inherited, TextureBase) |
| TextureSampleParameter2D | MaterialExpressionTextureSampleParameter2D.h:14 (→TextureSampleParameter.h:17) | `ParameterName` (MaterialExpressionParameter.h:22 pattern — parameter classes), `Texture` |
| ScalarParameter | MaterialExpressionScalarParameter.h | `DefaultValue` (:19), `SliderMin` (:30), `SliderMax` (:37); `ParameterName`/`Group`/`SortPriority` from base (MaterialExpressionParameter.h:22/:30/:34) |
| VectorParameter | MaterialExpressionVectorParameter.h | `DefaultValue` FLinearColor (:18); base parameter props as above |
| StaticSwitchParameter | MaterialExpressionStaticSwitchParameter.h:14 (→StaticBoolParameter.h:13) | `DefaultValue` (on StaticBoolParameter), `ParameterName` |
| Constant | MaterialExpressionConstant.h | `R` (:17, DisplayName "Value") |
| Multiply | MaterialExpressionMultiply.h | inputs `A`/`B` (:18/:21), `ConstA`/`ConstB` (:25/:29) |
| Add | MaterialExpressionAdd.h | `A`/`B` (:18/:21), `ConstA`/`ConstB` (:25/:29) |
| Subtract | MaterialExpressionSubtract.h:13 | same A/B/ConstA/ConstB pattern |
| Divide | MaterialExpressionDivide.h:13 | same A/B/ConstA/ConstB pattern |
| LinearInterpolate (alias Lerp) | MaterialExpressionLinearInterpolate.h | `A`/`B`/`Alpha` (:18/:21/:24), `ConstA`/`ConstB`/`ConstAlpha` (:28/:32/:36) |
| Power | MaterialExpressionPower.h:13 | Base/Exponent inputs, ConstExponent |
| Clamp | MaterialExpressionClamp.h:21 | Input/Min/Max inputs, MinDefault/MaxDefault |
| OneMinus | MaterialExpressionOneMinus.h:13 | single Input |
| Normalize | MaterialExpressionNormalize.h:13 | single VectorInput |
| Desaturation | MaterialExpressionDesaturation.h:13 | Input, Fraction, LuminanceFactors |
| AppendVector | MaterialExpressionAppendVector.h:13 | A/B inputs |
| ComponentMask | MaterialExpressionComponentMask.h | `Input` (:18), `R`/`G`/`B`/`A` bitfields (:21/:24/:27/:30) |
| Fresnel | MaterialExpressionFresnel.h | `Exponent` (:28), `BaseReflectFraction` (:38), `Normal` input (:42) |
| Panner | MaterialExpressionPanner.h | `SpeedX`/`SpeedY` (:27/:30), `Coordinate`/`Time`/`Speed` inputs (:18/:21/:24), `bFractionalPart` (:39) |
| TextureCoordinate (alias TexCoord) | MaterialExpressionTextureCoordinate.h | `CoordinateIndex` (:18), `UTiling`/`VTiling` (:22/:26) |
| WorldPosition | MaterialExpressionWorldPosition.h:31 | `WorldPositionShaderOffset` enum |
| VertexColor | MaterialExpressionVertexColor.h:12 | none needed |
| Time | MaterialExpressionTime.h:12 | bIgnorePause, bOverride_Period |
| RuntimeVirtualTextureSample | MaterialExpressionRuntimeVirtualTextureSample.h:61 | `VirtualTexture`, `MaterialType` |
| Comment | MaterialExpressionComment.h:14 | Text, SizeX/SizeY (graph organisation) |
| NamedRerouteDeclaration | MaterialExpressionNamedReroute.h:34 | Name |
| MaterialFunctionCall | MaterialExpressionMaterialFunctionCall.h (UCLASS :79) | `MaterialFunction` (:86); setter `ENGINE_API bool SetMaterialFunction(UMaterialFunctionInterface* NewMaterialFunction);` (:152) — use the setter, it refreshes FunctionInputs/FunctionOutputs |
| FunctionInput | MaterialExpressionFunctionInput.h:37 | `InputName` (:47), `InputType` (:62), `bUsePreviewValueAsDefault` (:70), `SortPriority` (:74) |
| FunctionOutput | MaterialExpressionFunctionOutput.h:16 | `OutputName` (:22) |
| CollectionParameter | MaterialExpressionCollectionParameter.h | `Collection` (:24), `ParameterName` (:28) |
| [Landscape] LandscapeLayerBlend | MaterialExpressionLandscapeLayerBlend.h:64 | `Layers` TArray<FLayerBlendInput> (:69); FLayerBlendInput (:27): `LayerName` (:32), `BlendType` (:35), `LayerInput`/`HeightInput` (:38/:41), `PreviewWeight` (:44) |
| [Landscape] LandscapeLayerCoords | MaterialExpressionLandscapeLayerCoords.h:35 | `MappingScale` (:49), `MappingRotation` (:53), `MappingPanU`/`MappingPanV` (:57/:61) |
| [Landscape] LandscapeLayerWeight | MaterialExpressionLandscapeLayerWeight.h:18 | ParameterName, PreviewWeight, Base/Layer inputs |
| [Landscape] LandscapeGrassOutput | MaterialExpressionLandscapeGrassOutput.h:41 | GrassTypes array |

Class-name resolution rule for the endpoint: accept short name ("TextureSample"), full class
name ("MaterialExpressionTextureSample"), or path ("/Script/Engine.MaterialExpressionTextureSample");
resolve via `FindObject<UClass>` + `IsChildOf(UMaterialExpression::StaticClass())`; unknown class ⇒
error naming the string and suggesting the catalogue (ResolveClassStrict precedent).

### Linkage pattern note (applies to every factory endpoint below)
`UFactory` is `UCLASS(abstract, MinimalAPI)` (`Editor/UnrealEd/Classes/Factories/Factory.h:21-22`)
and `FactoryCreateNew` is a virtual declared inline in the header (Factory.h:109-112). MinimalAPI
exports `StaticClass()`, so `NewObject<UXFactoryNew>()` links; the `FactoryCreateNew` call is
virtual dispatch through the vtable, which needs no import. This exact pattern already compiles
and links in this plugin: `MifBridgeAuthoring.cpp:307-311` (create_material_instance calls
`UMaterialInstanceConstantFactoryNew::FactoryCreateNew` — that one also happens to have a
method-level `UNREALED_API`, MaterialInstanceConstantFactoryNew.h:23, but the virtual-dispatch
route works for the factories that lack it).

## Proposed endpoints

### create_material
**Purpose**: Mint a new UMaterial asset (master material) — the missing half of the Tier-0
material-authoring gap (bridge can currently only create INSTANCES via create_material_instance).
**Engine API**:
```cpp
// Editor/UnrealEd/Classes/Factories/MaterialFactoryNew.h:14-24
UCLASS(hidecategories=Object, collapsecategories, MinimalAPI)
class UMaterialFactoryNew : public UFactory
{
	/** An initial texture to place in the newly created material */
	UPROPERTY()
	TObjectPtr<class UTexture> InitialTexture;
	virtual UObject* FactoryCreateNew(UClass* Class,UObject* InParent,FName Name,EObjectFlags Flags,UObject* Context,FFeedbackContext* Warn) override;
};
// Runtime/Engine/Classes/Materials/Material.h:449,453 — set after creation, direct UPROPERTY write:
TEnumAsByte<EMaterialDomain> MaterialDomain;
TEnumAsByte<EBlendMode> BlendMode;
```
`Editor/UnrealEd/Classes/Factories/MaterialFactoryNew.h:14-26`; `Runtime/Engine/Classes/Materials/Material.h:449,453`
**Export**: `MinimalAPI` class; call via virtual dispatch (see linkage pattern note). UMaterial itself `UCLASS(hidecategories=Object, MinimalAPI, BlueprintType)` Material.h:414-415; its UPROPERTYs are data members (no export needed). | **Module**: none — UnrealEd + Engine already linked | **Guards**: none (MifBridge is editor-only)
**Bucket**: self-managed — creates+registers a new package/UObject and triggers initial shader compile; must not sit inside a blanket transaction (create_material_instance precedent runs untransacted with explicit `FAssetRegistryModule::AssetCreated` + `MarkPackageDirty`).
**Async**: no for creation itself; the implied shader compile is async — response should include a hint to poll `shader_compile_status`.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| path | assetPath | string (/Game/…) | — | yes |
| domain | materialDomain | string enum: Surface, DeferredDecal, LightFunction, Volume, PostProcess, UI | Surface | no |
| blendMode | — | string enum: Opaque, Masked, Translucent, Additive, Modulate, AlphaComposite, AlphaHoldout | Opaque | no |
| initialTexture | — | string asset path | none | no (sets UMaterialFactoryNew::InitialTexture → factory auto-adds a TextureSample) |
Unrecognised parameter ⇒ error naming it.
**Failure modes**: path not under /Game/ ⇒ `"path must start with /Game/"`; package already exists ⇒ `"asset already exists at <path> — use a new path or delete_asset first"`; invalid domain/blendMode string ⇒ error listing accepted values; initialTexture not found ⇒ error naming the path.
**Cooked**: fully works — creates a NEW loose asset; never touches cooked content.
**Verify**: response returns materialPath; `list_material_expressions` on it returns numExpressions=0 (or 1 with initialTexture); `get_property path=<mat> property=MaterialDomain` echoes the domain; `find_assets` shows the package.
**Score**: U5 E2 R2 → tier 0 (named Tier-0 gap: material graph authoring, step 1)
**Phase-2 verdict**: CONFIRMED — all citations re-read verbatim (MaterialFactoryNew.h:14-26; Material.h:414-415/449/453). Enum lists independently verified: domain list matches the non-hidden EMaterialDomain values (`Runtime/Engine/Public/MaterialDomain.h:12-30`; MD_RuntimeVirtualTexture is Hidden/deprecated, correctly excluded); blendMode list matches non-Substrate EBlendMode values (`Runtime/Engine/Classes/Engine/EngineTypes.h:249-263`). Factory linkage precedent re-verified against MifBridgeAuthoring.cpp:307-311.

### create_material_function
**Purpose**: Mint a UMaterialFunction asset so reusable graph fragments can be authored and
called from materials (pairs with add_material_expression class=MaterialFunctionCall).
**Engine API**:
```cpp
// Editor/UnrealEd/Classes/Factories/MaterialFunctionFactoryNew.h:14-22
UCLASS(MinimalAPI, hidecategories=Object, collapsecategories)
class UMaterialFunctionFactoryNew : public UFactory
{
	virtual UObject* FactoryCreateNew(UClass* Class,UObject* InParent,FName Name,EObjectFlags Flags,UObject* Context,FFeedbackContext* Warn) override;
};
```
`Editor/UnrealEd/Classes/Factories/MaterialFunctionFactoryNew.h:14-22`
**Export**: MinimalAPI; virtual-dispatch route (linkage pattern note). | **Module**: none — UnrealEd already linked | **Guards**: none
**Bucket**: self-managed — new package/UObject creation (same as create_material).
**Params**: | path | assetPath | string /Game/… | — | yes | . Optional: | description | — | string | "" | no | (UMaterialFunction::Description via set_property afterwards — or fold in here since it is one write). Unrecognised ⇒ error.
**Async**: no
**Failure modes**: same path rules as create_material; existing asset ⇒ same error.
**Cooked**: fully works — new loose asset.
**Verify**: `list_material_expressions path=<fn>` returns 0; add FunctionInput/FunctionOutput expressions then re-count (expects 2).
**Score**: U4 E2 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — MaterialFunctionFactoryNew.h:14-22 verbatim. Phase-2 resolved the UNVERIFIED description question: `FString Description` is at `Runtime/Engine/Classes/Materials/MaterialFunction.h:52` and `uint8 bExposeToLibrary:1` at `:60` — both plain UPROPERTY data members, safe for the optional description/exposeToLibrary params.

### add_material_expression
**Purpose**: Add a node to a material or material-function graph — the atom of the Tier-0
material-graph-authoring gap.
**Engine API**:
```cpp
// Editor/MaterialEditor/Public/MaterialEditingLibrary.h:111-112
UFUNCTION(BlueprintCallable, Category = "MaterialEditing")
static UMaterialExpression* CreateMaterialExpression(UMaterial* Material, TSubclassOf<UMaterialExpression> ExpressionClass, int32 NodePosX=0, int32 NodePosY=0);
// Editor/MaterialEditor/Public/MaterialEditingLibrary.h:239-240
UFUNCTION(BlueprintCallable, Category = "MaterialEditing")
static UMaterialExpression* CreateMaterialExpressionInFunction(UMaterialFunction* MaterialFunction, TSubclassOf<UMaterialExpression> ExpressionClass, int32 NodePosX = 0, int32 NodePosY = 0);
// underlying Ex variant, Editor/MaterialEditor/Public/MaterialEditingLibrary.h:74-75
static UMaterialExpression* CreateMaterialExpressionEx(UMaterial* Material, UMaterialFunction* MaterialFunction, TSubclassOf<UMaterialExpression> ExpressionClass,
	UObject* SelectedAsset = nullptr, int32 NodePosX = 0, int32 NodePosY = 0, bool bAllowMarkingPackageDirty = true);
```
**Export**: `class MATERIALEDITOR_API UMaterialEditingLibrary` (MaterialEditingLibrary.h:57) — class-level export, all statics directly callable. | **Module**: **MaterialEditor — NEW dependency** (editor-only module in Engine/Source/Editor/MaterialEditor; safe for the editor-only MifBridge, must never leak runtime). | **Guards**: none beyond editor module.
**Bucket**: transacted — object creation inside an existing asset; no compile is triggered until recompile_material; undo of a node add is safe.
**Async**: no
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| path | material, materialPath | string — UMaterial OR UMaterialFunction asset | — | yes (dispatches to the right library call by class) |
| class | expressionClass, type | string — see Expression catalogue resolution rule | — | yes |
| x | nodePosX, posX | int | 0 | no |
| y | nodePosY, posY | int | 0 | no |
| properties | props | object {name: value} applied to the new expression via the set_property FProperty-import machinery (e.g. {"Texture": "/Game/T_Rock", "ParameterName": "Tiling", "DefaultValue": 4.0}) | {} | no |
| asset | selectedAsset | string asset path → CreateMaterialExpressionEx SelectedAsset (auto-wires Texture for TextureSample etc.) | none | no |
Unknown property name inside `properties` ⇒ error naming it and the expression class (silent-ignore is the #1 bug class). Returns `{ expressionName, expressionIndex, class, x, y }` — expressionName is the stable object name used by connect/delete.
**Failure modes**: asset is neither UMaterial nor UMaterialFunction ⇒ `"path must be a Material or MaterialFunction, got <class> — material instances have no graph; use set_material_parameter"`; unknown expression class ⇒ error + hint; property type mismatch ⇒ error naming property and expected type; cooked package ⇒ see below.
**Cooked**: **refuses** on cooked assets: UMaterialExpression is `UCLASS(abstract, Optional, …)` (MaterialExpression.h:183-184) — expression objects are stripped from cooked packages, so a cooked base-game material has no graph to extend. Error: `"material <path> is cooked (graph stripped) — author a NEW material with create_material instead"`. Detection: package `PKG_Cooked` flag (same check family the bridge's cooked handlers already use).
**Verify**: `list_material_expressions` count increments by exactly 1; `get_property` on the expression subobject echoes each `properties` value numerically.
**Score**: U5 E3 R2 → tier 0 (the named Tier-0 gap)
**Phase-2 verdict**: CONFIRMED — signatures verbatim at MaterialEditingLibrary.h:111-112, :239-240, :74-75; MATERIALEDITOR_API class-level export at :57 re-read. Implementation re-read (`MaterialEditingLibrary.cpp:511-596`): pure object-model NewObject + GetExpressionCollection().AddExpression, no editor window, no dialogs, no waits; `asset`/SelectedAsset auto-wiring confirmed for TextureBase (+AutoSetSampleType), MaterialFunctionCall (via SetMaterialFunction), and CollectionParameter (cpp:542-566). Cooked refusal is necessary, not just polite: cpp path touches the editor-only expression collection, which does not exist on PKG_Cooked packages.

### connect_material_expressions
**Purpose**: Wire expression output → expression input inside a material/function graph.
**Engine API**:
```cpp
// Editor/MaterialEditor/Public/MaterialEditingLibrary.h:158-159
UFUNCTION(BlueprintCallable, Category = "MaterialEditing")
static bool ConnectMaterialExpressions(UMaterialExpression* FromExpression, FString FromOutputName, UMaterialExpression* ToExpression, FString ToInputName);
// pin discovery, same header:
static TArray<FString> GetMaterialExpressionInputNames(UMaterialExpression* MaterialExpression);   // :203-204
static TArray<int32> GetMaterialExpressionInputTypes(UMaterialExpression* MaterialExpression);      // :207-208
```
**Export**: MATERIALEDITOR_API (class-level, :57) | **Module**: MaterialEditor — NEW (same dep as add_material_expression) | **Guards**: none
**Bucket**: transacted — pure in-asset pointer rewiring, undo-safe.
**Async**: no
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| path | material | string material/function asset | — | yes |
| from | fromExpression | string expression object name (from add/list) | — | yes |
| fromOutput | fromOutputName | string ("" = first output; masked outputs accept "R","G","B","A" — see GetExpressionOutputName, MaterialEditingLibrary.cpp:808-834) | "" | no |
| to | toExpression | string expression name | — | yes |
| toInput | toInputName | string ("" = first input) | "" | no |
**Failure modes**: expression name not found in that asset ⇒ error listing the first 20 valid names; ConnectMaterialExpressions returns false (bad pin name) ⇒ error echoing GetMaterialExpressionInputNames for the target so the agent can self-correct.
**Cooked**: refuses on cooked assets (no expressions exist — same rule and error as add_material_expression).
**Verify**: `list_material_expressions` reports the connection (to-node's input shows from-node's name); recompile + `get_material_stats` shows instruction count changed.
**Score**: U5 E2 R2 → tier 0
**Phase-2 verdict**: CONFIRMED — signatures verbatim (:158-159, :203-204, :207-208). Impl re-read (cpp:677-692): pure `Input->Connect` pointer wiring, no hazards. Masked-output pin names R/G/B/A confirmed at cpp:808-834 (static GetExpressionOutputName) as cited.

### connect_material_property
**Purpose**: Wire an expression output into a material OUTPUT pin (BaseColor, Roughness…) —
without this the graph never affects pixels.
**Engine API**:
```cpp
// Editor/MaterialEditor/Public/MaterialEditingLibrary.h:148-149
UFUNCTION(BlueprintCallable, Category = "MaterialEditing")
static bool ConnectMaterialProperty(UMaterialExpression* FromExpression, FString FromOutputName, EMaterialProperty Property);
```
EMaterialProperty (verbatim, `Runtime/Engine/Public/SceneTypes.h:159-200`, UENUM(BlueprintType)):
`MP_EmissiveColor=0, MP_Opacity, MP_OpacityMask, MP_DiffuseColor(Hidden), MP_SpecularColor(Hidden),
MP_BaseColor, MP_Metallic, MP_Specular, MP_Roughness, MP_Anisotropy, MP_Normal, MP_Tangent,
MP_WorldPositionOffset(Hidden), MP_WorldDisplacement_DEPRECATED, MP_TessellationMultiplier_DEPRECATED,
MP_SubsurfaceColor, MP_CustomData0(Hidden), MP_CustomData1(Hidden), MP_AmbientOcclusion,
MP_Refraction, MP_CustomizedUVs0..7(Hidden), MP_PixelDepthOffset(Hidden), MP_ShadingModel(Hidden),
MP_FrontMaterial(Hidden), MP_SurfaceThickness(Hidden), MP_Displacement(Hidden),
MP_MaterialAttributes(Hidden), MP_CustomOutput(Hidden), MP_MAX`.
**Export**: MATERIALEDITOR_API (:57) | **Module**: MaterialEditor — NEW | **Guards**: none
**Bucket**: transacted — same rationale as connect_material_expressions.
**Async**: no
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| path | material | string UMaterial asset (functions have no property pins — FunctionOutput expressions instead) | — | yes |
| from | fromExpression | string expression name | — | yes |
| fromOutput | fromOutputName | string | "" | no |
| property | materialProperty | string, MP_ prefix optional, case-insensitive. Accepted: EmissiveColor, Opacity, OpacityMask, BaseColor, Metallic, Specular, Roughness, Anisotropy, Normal, Tangent, WorldPositionOffset, SubsurfaceColor, ClearCoat→MP_CustomData0, ClearCoatRoughness→MP_CustomData1, AmbientOcclusion, Refraction, CustomizedUVs0..7, PixelDepthOffset, ShadingModel, Displacement | — | yes |
Deprecated/meta values (WorldDisplacement, TessellationMultiplier, MaterialAttributes, CustomOutput, MAX) ⇒ error `"property <x> is not connectable in 5.3 — accepted: …"`.
**Failure modes**: ConnectMaterialProperty returns false ⇒ error `"connect failed — check property is enabled for this material domain/blend mode (e.g. Opacity needs Translucent)"`; UMaterialFunction passed ⇒ error steering to FunctionOutput.
**Cooked**: refuses on cooked (as above).
**Verify**: `list_material_expressions` property-inputs block shows the property→expression binding (read back via `GetMaterialPropertyInputNode`, which works editor-window-free — verified at MaterialEditingLibrary.cpp:797-806); after recompile, `get_material_stats` NumPixelShaderInstructions rises above the empty-material baseline.
**Score**: U5 E2 R2 → tier 0
**Phase-2 verdict**: CONFIRMED — signature verbatim (:148-149); EMaterialProperty enum re-read verbatim (SceneTypes.h:159-200, matches). Impl re-read (cpp:656-676): requires `FromExpression->GetOuter()` to be the UMaterial — an expression living in a MaterialFunction makes it return false, which the entry's UMaterialFunction failure mode already covers; no hazards.

### delete_material_expression
**Purpose**: Remove one node (or all nodes) from a material/function graph — enables iterate-fix
loops instead of recreate-from-scratch.
**Engine API**:
```cpp
// Editor/MaterialEditor/Public/MaterialEditingLibrary.h:100-102
/** Delete a specific expression from a material. Will disconnect from other expressions. */
UFUNCTION(BlueprintCallable, Category = "MaterialEditing")
static void DeleteMaterialExpression(UMaterial* Material, UMaterialExpression* Expression);
// :97-98
static void DeleteAllMaterialExpressions(UMaterial* Material);
// :246-248
static void DeleteMaterialExpressionInFunction(UMaterialFunction* MaterialFunction, UMaterialExpression* Expression);
// :242-244
static void DeleteAllMaterialExpressionsInFunction(UMaterialFunction* MaterialFunction);
```
**Export**: MATERIALEDITOR_API (:57) | **Module**: MaterialEditor — NEW | **Guards**: none
**Bucket**: transacted — in-asset object removal, disconnection handled by the library, undo-safe.
**Async**: no
**Params**: | path | material | string | — | yes | ; | expression | name | string | — | yes unless all=true | ; | all | deleteAll | bool | false | no |. `all=true` + `expression` both set ⇒ error (ambiguous).
**Failure modes**: name not found ⇒ error listing valid names; cooked ⇒ refuse (as above).
**Verify**: `list_material_expressions` count decrements by 1 (or to 0 with all=true).
**Score**: U3 E1 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — all four signatures verbatim (:97-98, :100-102, :242-244, :246-248).

### list_material_expressions
**Purpose**: Read-back for the whole authoring loop: enumerate a graph's nodes, positions,
key parameters, connections, and property bindings — numbers the agent can assert against.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Materials/Material.h:1242
ENGINE_API TConstArrayView<TObjectPtr<UMaterialExpression>> GetExpressions() const;
// Runtime/Engine/Classes/Materials/MaterialFunction.h:183
ENGINE_API TConstArrayView<TObjectPtr<UMaterialExpression>> GetExpressions() const;
// MaterialEditingLibrary.h — per-node introspection (all MATERIALEDITOR_API statics):
static TArray<FString> GetMaterialExpressionInputNames(UMaterialExpression* MaterialExpression);            // :203-204
static TArray<int32> GetMaterialExpressionInputTypes(UMaterialExpression* MaterialExpression);              // :207-208
static TArray<UMaterialExpression*> GetInputsForMaterialExpression(UMaterial* Material, UMaterialExpression* MaterialExpression); // :211-212
static bool GetInputNodeOutputNameForMaterialExpression(UMaterialExpression* MaterialExpression, UMaterialExpression* InputNode, FString& OutputName); // :215-216
static void GetMaterialExpressionNodePosition(UMaterialExpression* MaterialExpression, int32& NodePosX, int32& NodePosY); // :219-220
static UMaterialExpression* GetMaterialPropertyInputNode(UMaterial* Material, EMaterialProperty Property);   // :195-196
static FString GetMaterialPropertyInputNodeOutputName(UMaterial* Material, EMaterialProperty Property);      // :199-200
static TArray<UTexture*> GetUsedTextures(UMaterial* Material);                                               // :223-224
```
**Export**: ENGINE_API on both GetExpressions; MATERIALEDITOR_API statics for the rest. | **Module**: MaterialEditor — NEW (Engine already linked) | **Guards**: `GetExpressions` walks editor-only data — fine in editor build; on a cooked asset it returns an empty view (data stripped), no crash.
**Bucket**: read-only — pure query, no transaction.
**Async**: no
**Params**: | path | material | string material or function | — | yes | ; | includeConnections | — | bool | true | no | ; | includeProperties | — | bool | true (dumps ParameterName/DefaultValue/Texture etc. per node via reflection) | no |
**Failure modes**: asset not found / wrong class ⇒ error naming it; material instance ⇒ error steering to list_object_properties.
**Cooked**: **degraded, honestly reported**: returns `numExpressions: 0, cooked: true` for cooked materials (Optional-class stripping) — response must carry the `cooked` flag so 0 is not mistaken for an empty graph.
**Verify**: is itself the verification endpoint for adds/connects/deletes; numbers: node count, per-node x/y, connection count, property-binding count.
**Score**: U5 E2 R1 → tier 0 (mutations without this read-back violate the house rule)
**Phase-2 verdict**: CONFIRMED — GetExpressions ENGINE_API verified at Material.h:1242 and MaterialFunction.h:183 (both inside `#if WITH_EDITORONLY_DATA` — call sites should carry the guard for hygiene, as the entry's Guards field already implies); all MaterialEditingLibrary introspection statics verbatim. One implementation caution found in Phase 2: `GetMaterialPropertyInputNode` (cpp:797-806) dereferences `GetExpressionInputForProperty`'s return without a null check — only query it for connectable MP_* values (the same list connect_material_property accepts), since non-connectable properties return nullptr and would crash.

### layout_material_expressions
**Purpose**: Auto-arrange nodes in a grid after programmatic authoring so a human opening the
asset sees a readable graph.
**Engine API**:
```cpp
// Editor/MaterialEditor/Public/MaterialEditingLibrary.h:170-171
static void LayoutMaterialExpressions(UMaterial* Material);
// :260-261
static void LayoutMaterialFunctionExpressions(UMaterialFunction* MaterialFunction);
```
**Export**: MATERIALEDITOR_API (:57) | **Module**: MaterialEditor — NEW | **Guards**: none
**Bucket**: transacted — only moves editor positions, trivially undoable.
**Async**: no
**Params**: | path | material | string material or function | — | yes |
**Failure modes**: wrong asset class ⇒ error.
**Cooked**: refuses (no expressions).
**Verify**: `list_material_expressions` positions change; no two nodes share identical (x,y).
**Score**: U2 E1 R1 → tier 2
**Phase-2 verdict**: CONFIRMED — signatures verbatim (:170-171, :260-261). Phase-2 also resolved the UNVERIFIED question: the impl (`MaterialEditingLibraryImpl::LayoutMaterialExpressions`, cpp:193-278) works directly on `MaterialExpressionEditorX/Y` — NO GraphNode/editor-window requirement. Caveat found: it only lays out expressions REACHABLE from material property inputs (or function inputs/outputs); disconnected nodes are not moved — so the "no two nodes share identical (x,y)" verify criterion only holds for the connected subgraph.

### recompile_material
**Purpose**: Apply graph/parameter edits: one endpoint that dispatches on asset class —
UMaterial → RecompileMaterial, UMaterialFunction(Interface) → UpdateMaterialFunction,
UMaterialInstanceConstant → UpdateMaterialInstance. Without it, none of the edits above reach
the renderer.
**Engine API**:
```cpp
// Editor/MaterialEditor/Public/MaterialEditingLibrary.h:164-165
UFUNCTION(BlueprintCallable, Category = "MaterialEditing")
static void RecompileMaterial(UMaterial* Material);
// :254-255
UFUNCTION(BlueprintCallable, Category = "MaterialEditing", meta = (HidePin = "PreviewMaterial"))
static void UpdateMaterialFunction(UMaterialFunctionInterface* MaterialFunction, UMaterial* PreviewMaterial = nullptr);
// :327-328
UFUNCTION(BlueprintCallable, Category = "MaterialEditing")
static void UpdateMaterialInstance(UMaterialInstanceConstant* Instance);
```
**Export**: MATERIALEDITOR_API (:57) | **Module**: MaterialEditor — NEW | **Guards**: none
**Bucket**: self-managed — RecompileMaterial regenerates shader maps and rebuilds dependent
instances; running it inside the blanket transaction would put shader-state teardown on the undo
stack (same crash family as full Blueprint compile inside an outer transaction).
**Async**: the CALL is synchronous and cheap (it enqueues compile jobs), but shader compilation
continues in the background; response returns `{ compiling: true, numRemainingJobs: N }` from
GShaderCompilingManager and the agent polls **shader_compile_status**. Never block in-handler.
**Params**: | path | material, asset | string — UMaterial, UMaterialFunction, or UMaterialInstanceConstant | — | yes |
**Failure modes**: unsupported class ⇒ `"path must be Material / MaterialFunction / MaterialInstanceConstant, got <class>"`; cooked asset ⇒ refuse: `"cooked material — shaders ship as fixed permutations, cannot recompile"`.
**Cooked**: refuses on cooked (no editor-only source to compile from).
**Verify**: `get_material_stats` after `shader_compile_status` reports compiling=false: instruction/sampler counts non-zero and change when the graph changes.
**Score**: U5 E2 R3 → tier 0 (nothing above lands without it)
**Phase-2 verdict**: CORRECTED — signatures verbatim (:164-165, :254-255, :327-328) and export/module claims hold, BUT the "call is synchronous and cheap (it enqueues compile jobs)" claim is FALSE for the UMaterial branch. `RecompileMaterial` ends with `FMaterialEditorUtilities::BuildTextureStreamingData(Material)` (MaterialEditingLibrary.cpp:731), which: (1) runs `CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS)` TWICE (MaterialEditorUtilities.cpp:789, :814) — a mid-handler GC, lethal to any unrooted UObject the bridge holds across calls; (2) opens `FScopedSlowTask SlowTask(...); SlowTask.MakeDialog(true)` (MaterialEditorUtilities.cpp:791-792) — a cancellable slow-task dialog that pumps UI mid-HTTP-handler; (3) calls `CompileDebugViewModeShaders(...)` which busy-waits (`while (PendingMaterials.Num() > 0) { Sleep(0.1); ProcessAsyncResults... }`, DebugViewModeHelpers.cpp:322-356) until the material's debug-view-mode shaders finish compiling — a synchronous shader-compile wait in the FlushShaderCompiles hazard class. (It does skip compilation for PKG_Cooked materials, MaterialEditorUtilities.cpp:798-800.) The UpdateMaterialFunction (cpp:985-1032) and UpdateMaterialInstance (cpp:1187-1202) branches are clean — PostEditChange/ForceRecompileForRendering enqueue only, no GC, no dialogs, no waits. Recommended implementation: for the UMaterial branch do NOT call RecompileMaterial; replicate its non-blocking core (cpp:697-728: `FMaterialUpdateContext` + `AddMaterial` + `PreEditChange(nullptr)`/`PostEditChange()` + `MarkPackageDirty` — FMaterialUpdateContext ctor/dtor/AddMaterial are all ENGINE_API, MaterialShared.h:2779+) and let shader_compile_status be the poll, exactly as this axis already designed. Score adjusted E2→E3 (must reimplement the core rather than one library call); self-managed bucket rationale unchanged and now doubly justified (GC + compile inside a transaction would be far worse).

### get_material_stats
**Purpose**: Numeric ground truth for a compiled material — 8 integers (instruction counts,
samplers, texture fetches, interpolators) that let an agent prove an edit did what it claims.
**Engine API**:
```cpp
// Editor/MaterialEditor/Public/MaterialEditingLibrary.h:390-392
/** Returns statistics about the given material */
UFUNCTION(BlueprintCallable, Category = "MaterialEditing")
static FMaterialStatistics GetStatistics(UMaterialInterface* Material);
// FMaterialStatistics fields (same header :17-53): NumVertexShaderInstructions,
// NumPixelShaderInstructions, NumSamplers, NumVertexTextureSamples, NumPixelTextureSamples,
// NumVirtualTextureSamples, NumUVScalars, NumInterpolatorScalars
// Bonus, same class: static UMaterialInterface* GetNaniteOverrideMaterial(UMaterialInterface* Material); // :394-396
```
**Export**: MATERIALEDITOR_API (:57) | **Module**: MaterialEditor — NEW | **Guards**: none
**Bucket**: read-only — pure query.
**Async**: no, but stats reflect the LAST finished compile; response includes the current
compiling flag so stale numbers are detectable.
**Params**: | path | material | string, any UMaterialInterface (material or instance) | — | yes |
**Failure modes**: not a material interface ⇒ error; stats all zero + compiling=true ⇒ response flags `"stats not ready — poll shader_compile_status"`.
**Cooked**: works only insofar as a cooked shader map exists for the editor's feature level; missing permutation ⇒ zeros — response carries `cooked: true` so the agent can distinguish.
**Verify**: empty-material baseline vs after TextureSample→BaseColor: NumPixelTextureSamples +1; NumPixelShaderInstructions strictly increases.
**Score**: U4 E1 R1 → tier 1
**Phase-2 verdict**: CORRECTED — signature and FMaterialStatistics fields verbatim (:17-53, :390-392), export/module hold, but the async characterization is wrong in the dangerous direction: `GetStatistics` does NOT return stale numbers — it force-submits the material's compile jobs at High priority and calls `Resource->FinishCompilation()` (MaterialEditingLibrary.cpp:1355-1362), a SYNCHRONOUS game-thread wait until that material's shaders finish. So "stats reflect the LAST finished compile" is false; the real hazard is an unbounded in-handler stall (seconds to minutes on cold DDC). Correction to the design: before calling GetStatistics, check `Material->GetMaterialResource(GMaxRHIFeatureLevel)->IsGameThreadShaderMapComplete()`; if incomplete, either return `{ pending: true }` steering the agent to shader_compile_status, or document that the endpoint blocks until the single material compiles (bounded to one material's jobs, unlike FinishAllCompilation). The "stats not ready" failure mode as written cannot occur. Score adjusted R1→R3 (synchronous compile wait); still read-only (no object mutation — the compile is a cache fill).

### set_material_instance_parent
**Purpose**: Re-parent an existing MaterialInstanceConstant (and optionally wipe its overrides) —
create_material_instance can only set the parent at birth.
**Engine API**:
```cpp
// Editor/MaterialEditor/Public/MaterialEditingLibrary.h:265-267
UFUNCTION(BlueprintCallable, Category = "MaterialEditing")
static void SetMaterialInstanceParent(UMaterialInstanceConstant* Instance, UMaterialInterface* NewParent);
// :269-271
static void ClearAllMaterialInstanceParameters(UMaterialInstanceConstant* Instance);
// :327-328 (called after): static void UpdateMaterialInstance(UMaterialInstanceConstant* Instance);
```
**Export**: MATERIALEDITOR_API (:57) | **Module**: MaterialEditor — NEW | **Guards**: none
**Bucket**: transacted — property-level mutations, undo-safe.
**Async**: no
**Params**: | path | instance | string MIC asset | — | yes | ; | parent | newParent | string material path | — | yes | ; | clearParameters | clear | bool | false | no |
**Failure modes**: instance not a MIC ⇒ reuse existing error text pattern (`MifBridgeAuthoring.cpp:366`); parent not found ⇒ error naming path; parent==instance or circular chain ⇒ `"parent chain would be circular"`.
**Cooked**: works when the MIC is loose; refuses when the MIC package is cooked (cannot save).
**Verify**: `get_property path=<mic> property=Parent` echoes the new parent; `get_material_stats` on the MIC tracks the new parent's stats.
**Score**: U3 E1 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — signatures verbatim (:265-267, :269-271, :327-328); MIC-error-text precedent re-verified at MifBridgeAuthoring.cpp:365-367. SetMaterialInstanceParent/ClearAllMaterialInstanceParameters impls are plain property work; pair with the (clean, non-blocking) UpdateMaterialInstance as designed.

### create_material_parameter_collection
**Purpose**: Mint a UMaterialParameterCollection asset — global scalar/vector parameters that
materials read via CollectionParameter expressions; the standard "one knob drives 50 materials"
mechanism, currently unreachable.
**Engine API**:
```cpp
// Editor/UnrealEd/Classes/Factories/MaterialParameterCollectionFactoryNew.h:14-22
UCLASS(MinimalAPI, hidecategories=Object, collapsecategories)
class UMaterialParameterCollectionFactoryNew : public UFactory
{
	virtual UObject* FactoryCreateNew(UClass* Class,UObject* InParent,FName Name,EObjectFlags Flags,UObject* Context,FFeedbackContext* Warn) override;
};
// Runtime/Engine/Classes/Materials/MaterialParameterCollection.h:89-93 — the data to seed:
UPROPERTY(EditAnywhere, Category=Material, Meta = (TitleProperty = "ParameterName"))
TArray<FCollectionScalarParameter> ScalarParameters;
UPROPERTY(EditAnywhere, Category=Material, Meta = (TitleProperty = "ParameterName"))
TArray<FCollectionVectorParameter> VectorParameters;
// FCollectionScalarParameter: FName ParameterName (:37, base) + float DefaultValue (:57)
// FCollectionVectorParameter: FLinearColor DefaultValue (:73)
```
**Export**: factory MinimalAPI → virtual-dispatch route (linkage pattern note); UMaterialParameterCollection `UCLASS(hidecategories=object, MinimalAPI, BlueprintType)` (:80-81) — arrays are data members, no export needed. | **Module**: none — UnrealEd + Engine linked | **Guards**: none
**Bucket**: self-managed — new package/UObject creation (create_material_instance precedent).
**Async**: no
**Params**: | path | assetPath | string /Game/… | — | yes | ; | scalars | — | object {name: float} | {} | no | ; | vectors | — | object {name: {r,g,b,a}} | {} | no |
**Failure modes**: path rules as create_material; duplicate parameter name within the request ⇒ error naming it.
**Cooked**: fully works — new loose asset.
**Verify**: `get_property path=<mpc> property=ScalarParameters` returns the seeded array with exact DefaultValues; count matches request.
**Score**: U4 E2 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — factory header verbatim (MaterialParameterCollectionFactoryNew.h:14-22); ScalarParameters/VectorParameters arrays and FCollectionScalarParameter/FCollectionVectorParameter fields re-read (ParameterName ~:37, float DefaultValue ~:57, FLinearColor DefaultValue ~:73) — all match.

### set_mpc_parameters
**Purpose**: Add/update/remove named parameters on an existing collection with PostEditChange
propagation (materials referencing the MPC recompile). Name-keyed addressing + post-edit side
effects is exactly the case the brief allows a dedicated endpoint over raw set_property.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Materials/MaterialParameterCollection.h:89-93 (arrays, direct data write)
// + Runtime/Engine/Classes/Materials/MaterialParameterCollection.h:140-144:
#if WITH_EDITOR
	using Super::PreEditChange;
	virtual void PreEditChange(FProperty* PropertyThatWillChange) override;
	virtual void PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent) override;
#endif // WITH_EDITOR
```
Call sequence: `Collection->PreEditChange(nullptr)` → mutate arrays → `Collection->PostEditChange()`
(reachable through exported UObject virtuals; the class's WITH_EDITOR overrides run via vtable).
The natural API `SetScalarParameterDefaultValue` (:100) is unexported — see Negative results; the
array route is the exported equivalent of what it does.
**Export**: data members + virtual dispatch — no import needed (class MinimalAPI :80-81). | **Module**: none — Engine linked | **Guards**: none required at the call site (UObject::PreEditChange/PostEditChange exist in editor builds).
**Bucket**: transacted — property edits on one asset; Modify() + blanket transaction is correct.
**Async**: dependent-material recompiles are queued by PostEditChange; poll shader_compile_status.
**Params**: | path | collection | string MPC asset | — | yes | ; | setScalars | scalars | object {name: float} (add-or-update) | {} | no | ; | setVectors | vectors | object {name:{r,g,b,a}} | {} | no | ; | remove | removeParameters | array of names | [] | no |. None of the three present ⇒ error `"nothing to do — pass setScalars, setVectors or remove"`.
**Failure modes**: remove name not present ⇒ error naming it (never silent); same name in setScalars and setVectors ⇒ error (type ambiguity).
**Cooked**: works on loose MPCs; refuses on cooked MPC assets (cannot save).
**Verify**: `get_property property=ScalarParameters` echoes exact floats; count delta matches adds/removes.
**Score**: U3 E2 R2 → tier 1
**Phase-2 verdict**: CONFIRMED — negative #1 (unexported setters) re-verified in-header; the array-write + PreEditChange/PostEditChange route re-verified INCLUDING the propagation implementation (`Runtime/Engine/Private/Materials/ParameterCollection.cpp:227-305`): PreEditChange caches parameter counts; PostEditChangeProperty sanitizes, and when counts CHANGED regenerates StateId, rebuilds the uniform buffer struct, and PostEditChange-recompiles every in-memory material referencing the collection via FMaterialUpdateContext (async enqueue — no blocking). Enrichment: value-only updates (no add/remove) intentionally skip the dependent-material recompile branch — correct engine behavior (uniform buffer values update without recompiles), so the response's `shader_compile_status` hint applies mainly to add/remove calls. Also note the engine clamps to 1024 scalars / 1024 vectors (cpp:245-257).

### create_rvt_asset
**Purpose**: Mint a URuntimeVirtualTexture asset with sizing/material-type set — the missing
producer for the already-existing `bind_landscape_rvt` consumer.
**Engine API**:
```cpp
// Runtime/Engine/Classes/VT/RuntimeVirtualTexture.h:14-15
UCLASS(ClassGroup = Rendering, BlueprintType, MinimalAPI)
class URuntimeVirtualTexture : public UObject
// key UPROPERTYs (same header): int32 TileCount = 8; // :26   int32 TileSize = 2; // :30
// int32 TileBorderSize = 2; // :34   ERuntimeVirtualTextureMaterialType MaterialType = ...BaseColor_Normal_Specular; // :38
// derived getters: int32 GetSize() const { return GetTileCount() * GetTileSize(); } // :105
// ENGINE_API int32 GetPageTableSize() const; // :108
```
Creation route: `NewObject<URuntimeVirtualTexture>(Package, Name, RF_Public|RF_Standalone|RF_Transactional)`
— justified because the engine's own factory does exactly and only this
(`Editor/VirtualTexturingEditor/Private/RuntimeVirtualTextureFactory.cpp:23-28`:
`URuntimeVirtualTexture* VirtualTexture = NewObject<URuntimeVirtualTexture>(InParent, Class, Name, Flags);`).
The factory CLASS itself is unexported (see Negative results) so we replicate its one line.
**Export**: URuntimeVirtualTexture MinimalAPI → NewObject links via exported StaticClass; property writes are data-member access; PostEditChange via UObject virtual. | **Module**: none — Engine linked (VirtualTexturingEditor also already a dep, but unused on this route) | **Guards**: none
**Bucket**: self-managed — new package/UObject creation.
**Async**: no
**Params**: | path | assetPath | string /Game/… | — | yes | ; | tileCount | — | int (log2, engine clamps 0..12) | 8 | no | ; | tileSize | — | int (log2, clamps 0..4) | 2 | no | ; | tileBorderSize | — | int (clamps 0..4) | 2 | no | ; | materialType | — | string enum (5.3.2 branch, non-hidden values of ERuntimeVirtualTextureMaterialType, `Runtime/Engine/Public/VT/RuntimeVirtualTextureEnum.h:35-45`): BaseColor, BaseColor_Normal_Roughness, BaseColor_Normal_Specular, BaseColor_Normal_Specular_YCoCg, BaseColor_Normal_Specular_Mask_YCoCg, WorldHeight | BaseColor_Normal_Specular | no |
**Failure modes**: path rules; unknown materialType ⇒ error listing accepted values.
**Cooked**: fully works — new loose asset.
**Verify**: response returns `size = GetSize()` and `pageTableSize = GetPageTableSize()`; `get_property property=TileCount` echoes input; then `bind_landscape_rvt` accepts the new asset (end-to-end pair).
**Score**: U3 E2 R2 → tier 1
**Phase-2 verdict**: CORRECTED — two fixes. (1) The proposed materialType list contained `Mask4` and `Displacement`, which DO NOT EXIST in this 5.3.2 branch (they are 5.4+ values); the real enum (`Runtime/Engine/Public/VT/RuntimeVirtualTextureEnum.h:35-45` — note Public/VT/, not Classes/VT/) is BaseColor, BaseColor_Normal_Roughness, BaseColor_Normal_Specular, BaseColor_Normal_Specular_YCoCg, BaseColor_Normal_Specular_Mask_YCoCg, WorldHeight (+2 hidden). Params table fixed in place; UNVERIFIED item resolved. (2) TileCount/TileSize/TileBorderSize/MaterialType are **protected** members (RuntimeVirtualTexture.h `protected:` block before :26) — "direct UPROPERTY write" from bridge code will not compile; write them via the FProperty-import machinery (as set_property does — reflection ignores C++ access) then PostEditChange. NewObject route, factory-cpp equivalence (RuntimeVirtualTextureFactory.cpp:23-28), and negative #2 (factory has NO API macro, RuntimeVirtualTextureFactory.h:16-17) all re-verified.

### refresh_texture
**Purpose**: Make texture-setting edits take effect: CompressionSettings/MipGenSettings/LODGroup/
SRGB are plain UPROPERTYs (set_property already covers the WRITE) — what is missing is the
rebuild via UpdateResource, plus an honest Source-validity report so agents stop guessing why
cooked textures won't rebuild.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Engine/Texture.h:1499
ENGINE_API virtual void UpdateResource();
// Runtime/Engine/Classes/Engine/Texture.h:1092-1096 (verbatim, incl. the in-engine warning):
#if WITH_EDITORONLY_DATA
	/* Dynamic textures will have ! Source.IsValid() ;
	Also in UEFN , Textures from the cooked-only texture library.  Always check Source.IsValid before using Source. */
	UPROPERTY()
	FTextureSource Source;
#endif
// settings UPROPERTYs (same header): MipGenSettings :1235, CompressionSettings :1320, LODGroup :1336, SRGB :1353
// Runtime/Engine/Classes/Engine/Texture2D.h:157: ENGINE_API int32 GetSizeX() const;
```
**Export**: method-level ENGINE_API on UpdateResource (UTexture is `UCLASS(abstract, MinimalAPI, BlueprintType)` Texture.h:1083-1084) | **Module**: none — Engine linked | **Guards**: Source access is WITH_EDITORONLY_DATA (always on in this editor-only module; wrap for hygiene).
**Bucket**: self-managed — rebuilds platform data / GPU resource; not meaningfully undoable, must not ride the blanket transaction.
**Async**: texture builds go through async asset compilation; endpoint returns immediately with `{ sourceValid, width, height }` per texture and states `"rebuild queued"`.
**Params**: | paths | path, texturePaths | array of strings (batch) | — | yes | ; | requireSource | — | bool: refuse per-texture when !Source.IsValid() instead of degraded-reporting | false | no |
**Failure modes**: non-texture asset ⇒ error naming its class; !Source.IsValid() with a compression-affecting pending change ⇒ per-entry `"cooked/dynamic texture — no Source data, settings change cannot rebuild pixels"`.
**Cooked**: **degraded and explicitly reported**: cooked .pak textures have no FTextureSource (engine's own header comment above) — UpdateResource re-creates the GPU resource from existing platform data, but compression/mip regeneration is impossible. Per-texture `sourceValid:false`.
**Verify**: `get_property property=CompressionSettings` echoes the new enum; `sourceValid` matches expectation; width/height integers non-zero for valid textures.
**Score**: U3 E2 R2 → tier 2
**Phase-2 verdict**: CONFIRMED — Texture.h citations verbatim (UCLASS :1083-1084, Source comment :1092-1096 including the engine's own warning text, settings :1235/:1320/:1336/:1353, UpdateResource ENGINE_API :1499); Texture2D.h:157 GetSizeX ENGINE_API verified. Phase-2 resolved the UNVERIFIED poll question: `ENGINE_API bool IsAsyncCacheComplete() const;` exists at Texture.h:1625 (and `IsDefaultTexture()` ENGINE_API :1615, which `IsCompiling()` wraps at :1725) — a v2 `texture_build_status` poll is viable; v1 queued-state-only stands.

### shader_compile_status
**Purpose**: THE poll endpoint for every material mutation above (and for editor-wide shader
churn after level loads) — closes the async rule for this axis.
**Engine API**:
```cpp
// Runtime/Engine/Public/ShaderCompiler.h:928
extern ENGINE_API FShaderCompilingManager* GShaderCompilingManager;
// class FShaderCompilingManager (ShaderCompiler.h:589):
ENGINE_API int32 GetNumPendingJobs() const;      // :746
ENGINE_API int32 GetNumOutstandingJobs() const;  // :747
// inline, calls the two exported getters (:770-773):
bool IsCompiling() const
{
	return GetNumOutstandingJobs() > 0 || HasShaderJobs() || GetNumPendingJobs() > 0 || NumExternalJobs > 0;
}
// inline (:798-801):
int32 GetNumRemainingJobs() const
{
	return GetNumOutstandingJobs() + NumExternalJobs;
}
```
**Export**: extern pointer ENGINE_API; underlying getters ENGINE_API; IsCompiling/GetNumRemainingJobs are header-inline (compile into MifBridge, no import needed). | **Module**: none — Engine linked | **Guards**: none
**Bucket**: read-only — pure query.
**Async**: IS the poll half; pairs with recompile_material, create_material, set_mpc_parameters, and level-load shader churn.
**Params**: none. Unrecognised ⇒ error.
**Failure modes**: GShaderCompilingManager null (never in editor, but defensive) ⇒ `"shader compiling manager unavailable"`.
**Cooked**: works — global editor state, independent of asset provenance.
**Verify**: immediately after recompile_material on a non-trivial material: numRemainingJobs > 0; after quiescence: compiling=false, numRemainingJobs=0; numbers strictly decrease over polls.
**Score**: U4 E1 R1 → tier 0 (async-rule infrastructure for the whole axis)
**Phase-2 verdict**: CONFIRMED — all citations verbatim (class :589, exported getters :746-747, inline IsCompiling :770-773 and GetNumRemainingJobs :798-801, extern ENGINE_API :928). Inline members compile into MifBridge as claimed; the exported getters they call resolve at link time. Name style matches nav_status/pie_status precedent; no collision with the 160-endpoint list.

### build_lighting
**Purpose**: Kick a Lightmass static-lighting build for the loaded level from the bridge —
currently only reachable by hand or via console hacks.
**Engine API**:
```cpp
// Editor/UnrealEd/Public/EditorBuildUtils.h:149 (class FEditorBuildUtils, :73)
static UNREALED_API bool EditorBuild( UWorld* InWorld, FName Id, const bool bAllowLightingDialog = true );
// build id (same header :21-22):
/** Build lighting */
UNREALED_API static const FName BuildLighting;
```
**Export**: `static UNREALED_API` on the method (class FEditorBuildUtils itself carries no macro — methods are individually exported, EditorBuildUtils.h:130-223 all follow this pattern) | **Module**: none — UnrealEd linked | **Guards**: none
**Bucket**: self-managed — kicks a multi-frame external build (Lightmass swarm); absolutely not transactable.
**Async**: request half. Call `EditorBuild(World, FBuildOptions::BuildLighting, /*bAllowLightingDialog=*/false)` and return immediately with `{ started: bool }`. Poll = **lighting_build_status**.
**Params**: | quality | lightingQuality | string enum: Preview, Medium, High, Production (maps to ELightingBuildQuality via LevelEditorMiscSettings before kick — v1 may omit and document editor-default) | Preview | no |
**Failure modes**: no editor world ⇒ `"no editor world"` (existing bridge phrasing); EditorBuild returns false ⇒ `"lighting build failed to start — check EditorCanBuild / another build running"` (EditorCanBuild, same header :138); build already running ⇒ same error citing lighting_build_status.
**Cooked**: works on levels the editor can SAVE; for cooked base-game maps the build may run but results cannot be persisted — response must warn `cookedMap: true` (editing/saving cooked maps is a documented impossible).
**Verify**: lighting_build_status flips running=true then false; map dirty flag set; built-lighting texture count observable via scene_report/console stats afterwards.
**Score**: U3 E2 R3 → tier 2
**Phase-2 verdict**: CORRECTED — three findings from re-reading the header AND `Editor/UnrealEd/Private/EditorBuildUtils.cpp:283-436`. (1) Wrong class qualifier: `BuildLighting` is a member of `struct FBuildOptions` (EditorBuildUtils.h:15-30), NOT FEditorBuildUtils — `FEditorBuildUtils::BuildLighting` would not compile; Async line fixed in place. EditorBuild/EditorCanBuild/IsBuildCurrentlyRunning signatures verbatim as cited. (2) The `quality` param IS implementable, resolving the UNVERIFIED item: EditorBuild reads `[LightingBuildOptions] QualityLevel` (int, clamped Quality_Preview..Quality_Production) from GEditorPerProjectIni at kick time (cpp:304-313) — write it via `GConfig->SetInt` before calling EditorBuild, no LevelEditorMiscSettings header needed. (3) Hazards the entry must document: `GWarn->ShowBuildProgressWindow()` always opens the build-progress window (cpp:364); if the level contains non-volume BSP brushes a synchronous `MAP REBUILD ALLVISIBLE` runs in-handler first (cpp:411-429); the Lightmass kick itself (`GUnrealEd->BuildLighting(LightingBuildOptions)`, cpp:431) is asynchronous as designed, so the request+poll split stands. No save/checkout prompts on this path (those live in EditorAutomatedBuildAndSubmit, a different function).

### lighting_build_status
**Purpose**: Poll half for build_lighting (the async rule).
**Engine API**:
```cpp
// Editor/UnrealEd/Classes/Editor/EditorEngine.h:1585 (UCLASS(config=Engine, transient, MinimalAPI) :290-291)
/** Checks to see if the asynchronous lighting build is running or not */
UNREALED_API bool IsLightingBuildCurrentlyRunning() const;
// Editor/UnrealEd/Public/EditorBuildUtils.h:213
static UNREALED_API bool IsBuildCurrentlyRunning();
// GEditor access: Editor/UnrealEd/Public/Editor.h:30
extern UNREALED_API class UEditorEngine* GEditor;
```
**Export**: method-level UNREALED_API on both; GEditor extern UNREALED_API. | **Module**: none — UnrealEd linked | **Guards**: none
**Bucket**: read-only — pure query.
**Async**: IS the poll half.
**Params**: none.
**Failure modes**: none beyond null GEditor (defensive).
**Cooked**: works — global editor state.
**Verify**: returns `{ lightingBuildRunning, anyEditorBuildRunning }` booleans; sequence true→false across polls brackets the build.
**Score**: U3 E1 R1 → tier 2 (ships with build_lighting)
**Phase-2 verdict**: CONFIRMED — all three citations verbatim (EditorEngine.h:1585 UNREALED_API, EditorBuildUtils.h:213 static UNREALED_API, Editor.h:30 extern UNREALED_API GEditor); UEditorEngine UCLASS MinimalAPI at :290-291 re-read.

### build_reflection_captures
**Purpose**: Re-capture all reflection captures in the world (Build → Reflection Captures) —
needed after material/lighting changes so specular ambience matches, and currently unreachable.
**Engine API**:
```cpp
// Editor/UnrealEd/Classes/Editor/EditorEngine.h:2318-2321
/**
* Update any outstanding reflection captures
*/
UNREALED_API void BuildReflectionCaptures(UWorld* World = GWorld);
```
**Export**: method-level UNREALED_API (UEditorEngine is MinimalAPI, :290-291) | **Module**: none — UnrealEd linked | **Guards**: none
**Bucket**: self-managed — GPU capture + encode of every capture in the world; not transactable.
**Async**: no — the call runs to completion on the game thread (it is the synchronous menu action). It can take seconds on large worlds; response should include elapsedMs and the endpoint doc must warn agents to raise their HTTP client timeout. It does NOT wait on game-thread ticks it cannot get (no deadlock class), it is just long.
**Params**: none (operates on the current editor world). Unrecognised ⇒ error.
**Failure modes**: no editor world ⇒ `"no editor world"`; zero captures in world ⇒ success with `capturesUpdated: 0` (count via TActorIterator<AReflectionCapture>/component census in the same handler — not an error).
**Cooked**: capture data is saved into the map package ⇒ effective only for savable (non-cooked) maps; on cooked maps report `cookedMap: true, resultsNotPersistable: true`.
**Verify**: response returns number of reflection-capture components found and world name; run twice — second run same count, no crash (idempotence check).
**Score**: U2 E1 R2 → tier 2
**Phase-2 verdict**: CORRECTED — declaration verbatim (EditorEngine.h:2318-2321, UNREALED_API) but the "no deadlock class, it is just long" characterization misses three concrete hazards found in the implementation (`Editor/UnrealEd/Private/EditorEngine.cpp:3969-3995`): (1) `FAssetCompilingManager::Get().FinishAllCompilation()` (:3981) — a synchronous wait for ALL outstanding asset compilation editor-wide (shaders AND textures AND meshes), unbounded right after a level load or material churn — the handler must pre-check shader_compile_status and refuse/warn when compiling, or the HTTP call stalls for minutes; (2) `GWarn->BeginSlowTask(..., true)` (:3974) — slow-task UI mid-handler; (3) `check(World->GetFeatureLevel() >= ERHIFeatureLevel::SM5)` (:3989) — a hard assert (crash, not error) if the editor runs below SM5; the handler must verify feature level first. Also refined: capture results dirty BuildData packages (Level->MapBuildData), not the ULevel package (engine's own comment :3971) — the cookedMap warning should reference MapBuildData persistence. Score adjusted E1→E2 R2→R3 for the mandatory pre-checks. Overlap: axis F proposes the same endpoint — reconcile at merge; this axis's version now carries the hazard analysis.

### create_render_target
**Purpose**: Mint a transient UTextureRenderTarget2D (and optionally bake it to a static
UTexture2D asset) — the canvas for scene captures and material-baking verification loops.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Kismet/KismetRenderingLibrary.h:49
static ENGINE_API UTextureRenderTarget2D* CreateRenderTarget2D(UObject* WorldContextObject, int32 Width = 256, int32 Height = 256, ETextureRenderTargetFormat Format = RTF_RGBA16f, FLinearColor ClearColor = FLinearColor::Black, bool bAutoGenerateMipMaps = false, bool bSupportUAVs = false);
// :90
static ENGINE_API UTexture2D* RenderTargetCreateStaticTexture2DEditorOnly(UTextureRenderTarget2D* RenderTarget, FString Name = "Texture", enum TextureCompressionSettings CompressionSettings = TC_Default, enum TextureMipGenSettings MipSettings = TMGS_FromTextureGroup);
```
**Export**: method-level ENGINE_API (class is `UCLASS(MinimalAPI, meta=(ScriptName="RenderingLibrary"))`, KismetRenderingLibrary.h:33) | **Module**: none — Engine linked | **Guards**: RenderTargetCreateStaticTexture2DEditorOnly is editor-only by contract (name suffix); fine here.
**Bucket**: self-managed — creates a registered UObject (transient); static-texture bake creates a package.
**Async**: no
**Params**: | name | id | string handle for later read/export calls (bridge keeps a name→object map, transient lifetime) | — | yes | ; | width | — | int | 256 | no | ; | height | — | int | 256 | no | ; | format | — | string enum (RTF_R8..RTF_RGBA32f; accepted list from ETextureRenderTargetFormat) | RTF_RGBA16f | no | ; | saveTo | bakePath | string /Game/… — if set, also bakes via RenderTargetCreateStaticTexture2DEditorOnly | none | no |
**Failure modes**: duplicate name ⇒ error naming it; width/height <1 or >8192 ⇒ error with bounds; bad format string ⇒ error listing accepted.
**Cooked**: N/A — transient objects; bake path creates loose assets.
**Verify**: read_render_target on the fresh target returns the clear color at (0,0) exactly.
**Score**: U3 E2 R2 → tier 2
**Phase-2 verdict**: CORRECTED — signatures verbatim (KismetRenderingLibrary.h:49, :90; class MinimalAPI :33 with method-level ENGINE_API confirmed), but the "bridge keeps a name→object map, transient lifetime" design has a GC hazard the entry must state: a transient UTextureRenderTarget2D referenced only from a native TMap is invisible to the GC and will be collected between HTTP calls — and this axis's own recompile_material path runs `CollectGarbage` twice (see its Phase-2 verdict), so the collection is not hypothetical. The handle map must hold `TStrongObjectPtr<UTextureRenderTarget2D>` (or the owner must AddToRoot/FGCObject), with an explicit release path (`release_render_target` action or `saveTo`+drop). With rooting specified, the rest stands.

### read_render_target
**Purpose**: Numeric pixel verification ("numbers for correctness, pixels for taste") — read
single pixels or areas from any render target (including scene-capture outputs), optionally
export to disk for offline diffing.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Kismet/KismetRenderingLibrary.h:111
static ENGINE_API FColor ReadRenderTargetPixel(UObject* WorldContextObject, UTextureRenderTarget2D* TextureRenderTarget, int32 X, int32 Y);
// :133
static ENGINE_API FLinearColor ReadRenderTargetRawPixel(UObject* WorldContextObject, UTextureRenderTarget2D* TextureRenderTarget, int32 X, int32 Y, bool bNormalize = true);
// :139
static ENGINE_API TArray<FLinearColor> ReadRenderTargetRawPixelArea(UObject* WorldContextObject, UTextureRenderTarget2D* TextureRenderTarget, int32 MinX, int32 MinY, int32 MaxX, int32 MaxY, bool bNormalize = true);
// :151
static ENGINE_API bool ReadRenderTargetRaw(UObject* WorldContextObject, UTextureRenderTarget2D* TextureRenderTarget, TArray<FLinearColor>& OutLinearSamples, bool bNormalize = true);
// :102-103
UFUNCTION(BlueprintCallable, Category = "Rendering", meta = (Keywords = "ExportRenderTarget", WorldContext = "WorldContextObject"))
static ENGINE_API void ExportRenderTarget(UObject* WorldContextObject, UTextureRenderTarget2D* TextureRenderTarget, const FString& FilePath, const FString& FileName);
```
**Export**: method-level ENGINE_API throughout | **Module**: none — Engine linked | **Guards**: none
**Bucket**: read-only — GPU readback, no object mutation (readback flushes rendering — cheap single-frame stall, acceptable in-handler; NOT a multi-frame wait).
**Async**: no
**Params**: | target | name, path | string — handle from create_render_target OR asset path of any UTextureRenderTarget2D | — | yes | ; | x,y | — | ints (single pixel) | — | one of pixel/area/all | ; | area | — | object {minX,minY,maxX,maxY} — returns stats {mean RGBA, min, max} plus optional raw array capped at 4096 samples | — | — | ; | export | exportPath | string absolute dir + | fileName | string | — | no (EXR/PNG per extension via ExportRenderTarget) |
**Failure modes**: unknown handle/path ⇒ error; x/y out of bounds ⇒ error with target dimensions; area larger than cap without stats-only ⇒ error stating the cap.
**Cooked**: works — render targets are runtime objects.
**Verify**: is itself a verification endpoint; sanity: cleared target reads back its ClearColor bit-exact (RTF_RGBA16f → within half-float epsilon; response documents epsilon).
**Score**: U4 E2 R1 → tier 1 (gives every rendering mutation a numeric oracle)
**Phase-2 verdict**: CONFIRMED — all five signatures verbatim (:111, :133, :139, :151, :102-103 ExportRenderTarget), every one method-level ENGINE_API. The engine's own doc-comments call the readbacks "Incredibly inefficient and slow" — consistent with the entry's single-frame-stall framing; cap-and-stats design is the right mitigation. Same rooting requirement as create_render_target when addressing by transient handle.

### validate_level_materials
**Purpose**: Tier-0 (mission): one read-only structured report over all mesh components in the
level — missing/null material slots, default-material fallbacks (the WorldGridMaterial symptom),
incomplete materials (missing cooked shader permutations), and LOD/Nanite mismatches. The
read-only report design follows the H_diagnose_landscape_draws precedent
(`Source/MifBridge/Private/MifBridgeCooked.cpp:789`).
**Engine API**:
```cpp
// Runtime/Engine/Classes/Components/MeshComponent.h:101-102 (class UMeshComponent : public UPrimitiveComponent, ENGINE_API-exported methods)
ENGINE_API virtual int32 GetNumMaterials() const override;
ENGINE_API virtual UMaterialInterface* GetMaterial(int32 ElementIndex) const override;
// Runtime/Engine/Classes/Components/MeshComponent.h:33
TArray<TObjectPtr<class UMaterialInterface>> OverrideMaterials;
// Runtime/Engine/Classes/Materials/Material.h:1221
ENGINE_API static UMaterial* GetDefaultMaterial(EMaterialDomain Domain);
// Runtime/Engine/Classes/Materials/MaterialInterface.h:997 (virtual — overridden per subclass, vtable dispatch)
virtual bool IsComplete() const { return true; }
// Runtime/Engine/Classes/Engine/StaticMesh.h:870, :1767, :710
ENGINE_API bool IsNaniteEnabled() const;
ENGINE_API int32 GetNumLODs() const;
FMeshNaniteSettings NaniteSettings;
// Runtime/Engine/Classes/Components/StaticMeshComponent.h:105 (see set_actor_render_overrides for context)
int32 ForcedLodModel;
```
**Export**: all ENGINE_API or virtual-dispatch as annotated | **Module**: none — Engine linked | **Guards**: none
**Bucket**: read-only — pure census, no transaction (else every audit pushes an empty undo entry).
**Async**: no (game-thread object walk; no render-thread hop needed for v1 — IsComplete is game-thread state).
**Params**: | filter | actorFilter | string substring on actor label/class | "" | no | ; | limit | — | int max detailed rows | 200 | no | ; | includeOk | — | bool include healthy rows | false | no |
**Failure modes**: no editor world ⇒ `"no editor world"`.
**Cooked**: WORKS — this is precisely the endpoint for cooked content: cooked materials that shipped without the needed permutation show up via IsComplete()==false / default-material fallback. Reports are counts + per-row {actor, component, slot, materialPath, issue}.
**Verify**: numbers: totalComponents, totalSlots, nullSlots, defaultMaterialSlots, incompleteMaterials, forcedLodOutOfRange (ForcedLodModel > GetNumLODs()), naniteWithForcedLod. Cross-check one known-bad actor by hand with list_object_properties.
**Score**: U5 E3 R1 → tier 0 (named Tier-0 gap: level-material assignment validation)
**Phase-2 verdict**: CONFIRMED (Tier-0, verified hard) — every citation re-opened: GetNumMaterials/GetMaterial ENGINE_API overrides (MeshComponent.h:101-102), OverrideMaterials :33 (whose in-header comment warns it "must NOT be set directly" — supports routing writes through SetMaterial, and validates this endpoint staying read-only), GetDefaultMaterial ENGINE_API static (Material.h:1221), IsComplete virtual inline default-true (MaterialInterface.h:997 — subclass overrides supply the real answer via vtable, as claimed), IsNaniteEnabled :870 / GetNumLODs :1767 ENGINE_API, NaniteSettings :710 (WITH_EDITORONLY_DATA — census code touching it needs the guard), ForcedLodModel StaticMeshComponent.h:105. Read-only bucket, cooked-content value proposition, and numeric verify plan all hold.

### set_actor_render_overrides
**Purpose**: Tier-0 (mission): batch per-actor cull/LOD control — force LODs, set draw
distances, exclude from HLOD — with post-edit render-state refresh. These are UPROPERTYs
(set_property could write them one component at a time) but the batch-per-actor addressing +
SetCullDistance side-effect + MarkRenderStateDirty is the added value the brief requires.
**Engine API**:
```cpp
// Runtime/Engine/Classes/Components/StaticMeshComponent.h:105 (class UStaticMeshComponent : public UMeshComponent, :99)
int32 ForcedLodModel;   // UPROPERTY: If 0, auto-select LOD level. If >0, force to (ForcedLodModel-1)
// :116
int32 MinLOD;
// Runtime/Engine/Classes/Components/PrimitiveComponent.h (class UPrimitiveComponent :262):
float MinDrawDistance;        // :282
float LDMaxDrawDistance;      // :286
float CachedMaxDrawDistance;  // :293
uint8 bEnableAutoLODGeneration : 1;  // :317  (HLOD inclusion flag — false = excluded from HLOD build)
// :2747
ENGINE_API void SetCullDistance(float NewCullDistance);
// Runtime/Engine/Classes/Components/ActorComponent.h:922
ENGINE_API void MarkRenderStateDirty();
```
**Export**: SetCullDistance and MarkRenderStateDirty method-level ENGINE_API; the rest are data members (no export needed). | **Module**: none — Engine linked | **Guards**: none
**Bucket**: transacted — plain property edits on placed components; Modify() before write; undo restores.
**Async**: no
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| actors | actorLabels, actorPaths | array of actor labels or paths | — | yes |
| forcedLod | forcedLodModel | int (0 = auto, N = force LOD N-1, engine convention) | unset | no |
| minLod | — | int | unset | no |
| minDrawDistance | — | float | unset | no |
| maxDrawDistance | cullDistance | float (applied via SetCullDistance → also updates CachedMaxDrawDistance) | unset | no |
| excludeFromHLOD | — | bool (writes bEnableAutoLODGeneration = !value) | unset | no |
| componentFilter | — | string substring on component name; default = every UPrimitiveComponent (LOD fields only where the component is a UStaticMeshComponent) | "" | no |
At least one override param required ⇒ else `"nothing to set"`. Unrecognised ⇒ error. Per-component: Modify → write → MarkRenderStateDirty.
**Failure modes**: actor not found ⇒ error naming it (batch continues, reports per-actor status); forcedLod > mesh LOD count ⇒ per-component error `"forcedLod N exceeds LOD count M of <mesh>"` (checked via UStaticMesh::GetNumLODs, StaticMesh.h:1767); LOD params on non-staticmesh component ⇒ skipped and REPORTED (never silent).
**Cooked**: works — placed level actors in a savable map; refuses only when the LEVEL is cooked (cannot persist).
**Verify**: `get_actor_render_info` (below) echoes every written value; PIE frame-time/draw-count deltas via run_console_captured `stat unit`/`stat scenerendering` for behavioural confirmation.
**Score**: U4 E2 R2 → tier 0 (named Tier-0 gap: per-actor cull/LOD overrides)
**Phase-2 verdict**: CORRECTED — citations all verified (UPrimitiveComponent UCLASS :261-262; MinDrawDistance/LDMaxDrawDistance/CachedMaxDrawDistance :282/:286/:293; bEnableAutoLODGeneration :317 "Whether to include this component in HLODs or not"; SetCullDistance ENGINE_API :2747; MarkRenderStateDirty ENGINE_API ActorComponent.h:922; ForcedLodModel/MinLOD StaticMeshComponent.h:105/:115-116), but the `minLod` param as specified would be SILENTLY IGNORED by the engine — `MinLOD` carries `meta=(editcondition = "bOverrideMinLOD")` (StaticMeshComponent.h:115) and the gate flag is `uint8 bOverrideMinLOD:1` at StaticMeshComponent.h:226. The endpoint MUST write `bOverrideMinLOD = true` whenever minLod is set (and expose `clearMinLodOverride`/set-false to undo), otherwise this entry ships the exact silent-parameter-ignore bug class the brief bans. Params spec amended by this note; everything else (batch semantics, per-component Modify→write→MarkRenderStateDirty, forcedLod range check against GetNumLODs) holds.

### get_actor_render_info
**Purpose**: Read-back pair for set_actor_render_overrides: per-actor, per-component render
facts — LOD forcing, draw distances, HLOD flag, mesh LOD count, Nanite, material slots.
**Engine API**: same citations as set_actor_render_overrides + StaticMesh.h:870 (IsNaniteEnabled), :1767 (GetNumLODs); MeshComponent.h:101-102 (materials).
```cpp
// reads only: ForcedLodModel, MinLOD, MinDrawDistance, LDMaxDrawDistance, CachedMaxDrawDistance,
// bEnableAutoLODGeneration, GetNumMaterials()/GetMaterial(i), GetNumLODs(), IsNaniteEnabled()
```
**Export**: as above — data members + ENGINE_API methods | **Module**: none | **Guards**: none
**Bucket**: read-only — pure query.
**Async**: no
**Params**: | actors | — | array of labels/paths | — | yes | ; | componentFilter | — | string | "" | no |
**Failure modes**: actor not found ⇒ error naming it.
**Cooked**: works fully — reading is always safe on cooked content.
**Verify**: is the verification endpoint for its pair; values are exact floats/ints, comparable to what set_actor_render_overrides wrote.
**Score**: U4 E1 R1 → tier 0 (pairs with the mutation, house rule)
**Phase-2 verdict**: CONFIRMED — shares set_actor_render_overrides' fully re-verified citation set; read-only census is safe on cooked content as claimed. Report should include bOverrideMinLOD alongside MinLOD (see the pair's Phase-2 correction) so agents can see whether a MinLOD value is actually live.

### set_material_instance_layers
**Purpose**: Author a material-layers stack (layer + blend functions) on a MaterialInstanceConstant
programmatically — 5.3's layer system IS reachable without UI (answering the mission's
"viable to author programmatically?" question: YES, on instances).
**Engine API**:
```cpp
// Runtime/Engine/Classes/Materials/MaterialLayersFunctions.h:122-130
struct FMaterialLayersFunctionsRuntimeData
{
	UPROPERTY(EditAnywhere, Category = MaterialLayers)
	TArray<TObjectPtr<class UMaterialFunctionInterface>> Layers;
	UPROPERTY(EditAnywhere, Category = MaterialLayers)
	TArray<TObjectPtr<class UMaterialFunctionInterface>> Blends;
};  // (fields verbatim at :127 and :130)
// :193 struct FMaterialLayersFunctions : public FMaterialLayersFunctionsRuntimeData
// :237 ENGINE_API int32 AppendBlendedLayer();
// :267 ENGINE_API void RemoveBlendedLayerAt(int32 Index);
// Runtime/Engine/Classes/Materials/MaterialInstance.h:785
ENGINE_API bool SetMaterialLayers(const FMaterialLayersFunctions& LayersValue);
// read-back: MaterialInstance.h:766
virtual ENGINE_API bool GetMaterialLayers(FMaterialLayersFunctions& OutLayers, TMicRecursionGuard RecursionGuard = TMicRecursionGuard()) const override;
```
Layer/blend asset creation: `UMaterialFunctionMaterialLayerFactory` / `UMaterialFunctionMaterialLayerBlendFactory`
exist (`Editor/UnrealEd/Classes/Factories/MaterialFunctionMaterialLayerFactory.h` /
`MaterialFunctionMaterialLayerBlendFactory.h`, dir-listed in inventory) — same MinimalAPI
virtual-dispatch route; create_material_function should accept a `kind: function|layer|layerBlend`
parameter instead of a separate endpoint.
**Export**: all cited methods ENGINE_API | **Module**: none — Engine linked (factories: UnrealEd) | **Guards**: FMaterialLayersFunctions editor-only members exist behind WITH_EDITORONLY_DATA (struct at :193 carries EditorOnly sibling :55) — construct with editor data present (editor build: yes).
**Bucket**: transacted for SetMaterialLayers on an instance; the parent material still needs a MaterialAttributeLayers expression + recompile (self-managed part lives in recompile_material).
**Async**: shader recompile follows — poll shader_compile_status.
**Params**: | path | instance | string MIC | — | yes | ; | layers | — | array of material-function paths | — | yes | ; | blends | — | array of blend-function paths, length = layers-1 | — | yes when layers>1 |
**Failure modes**: function asset is not a layer/blend-typed function ⇒ error naming it and its MaterialFunctionUsage; blends length mismatch ⇒ `"need exactly N-1 blends for N layers"`.
**Cooked**: refuses on cooked instances/functions (editor-only layer data stripped).
**Verify**: GetMaterialLayers round-trip: Layers.Num()/Blends.Num() match request exactly.
**Score**: U2 E4 R3 → tier 3 (exotic but verified viable — records the affirmative answer)
**Phase-2 verdict**: CONFIRMED — all citations re-opened: FMaterialLayersFunctionsRuntimeData Layers/Blends fields verbatim (MaterialLayersFunctions.h:~121-131), FMaterialLayersFunctions derivation :193, AppendBlendedLayer ENGINE_API :237, RemoveBlendedLayerAt ENGINE_API :267, FMaterialLayersFunctionsEditorOnlyData :54-55; UMaterialInstance::SetMaterialLayers ENGINE_API (MaterialInstance.h:~785, inside a WITH_EDITOR block — add the guard) and GetMaterialLayers virtual ENGINE_API override :766. Note MaterialInstance.h:505 is a different SetMaterialLayers (on FMaterialInstanceParameterUpdateContext) — the :785 UMaterialInstance method is the one to call. Tier-3 placement appropriate.

## Compositions (no new endpoint needed)

| Want | Composition over EXISTING endpoints |
|---|---|
| Change material domain / blend mode / shading model / two-sided | `set_property` on the UMaterial (UPROPERTYs `MaterialDomain` Material.h:449, `BlendMode` Material.h:453) → `recompile_material`. No dedicated endpoint: single dot-path write. |
| Write texture compression/mip/LODGroup/SRGB | `set_property` (Texture.h:1235/1320/1336/1353 are UPROPERTYs) → `refresh_texture` for the rebuild half. |
| Read MPC parameters | `get_property path=<mpc> property=ScalarParameters` / `VectorParameters` (arrays are UPROPERTY — MaterialParameterCollection.h:89-93). set_mpc_parameters exists only for the name-keyed WRITE + PostEditChange. |
| Material function inputs/outputs | `add_material_expression` with class=FunctionInput/FunctionOutput + properties {InputName, InputType} (MaterialExpressionFunctionInput.h:47/:62; FunctionOutput.h:22) — no separate add_function_io endpoint. |
| Edit an existing expression's fields (retile a TexCoord, rename a parameter) | `set_property` with objectPath = the expression subobject path returned by list_material_expressions → `recompile_material`. |
| Layer/blend function assets | create_material_function with kind=layer|layerBlend (factory variants, see set_material_instance_layers) — not separate endpoints. |
| MIC parameter values | already covered: `create_material_instance` / `set_material_parameter` (MifBridgeAuthoring.cpp) — MaterialEditingLibrary's SetMaterialInstance*ParameterValue family (MaterialEditingLibrary.h:276-324) adds nothing over the existing SetXParameterValueEditorOnly route except the Association parameter, which only matters once material layers ship (revisit with set_material_instance_layers). |
| Duplicate an expression | DuplicateMaterialExpression exists (MaterialEditingLibrary.h:122-123) but add_material_expression + properties replay covers it; defer. |
| Per-component render overrides one-at-a-time | `set_property` on the component object path; set_actor_render_overrides exists for BATCH + SetCullDistance side-effect + validation only. |
| Landscape RVT wiring | existing `bind_landscape_rvt` + new create_rvt_asset. |
| "Does this material sample too many textures?" gate | get_material_stats NumSamplers vs 16 — client-side check, no endpoint. |

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

1. **UMaterialParameterCollection::SetScalarParameterDefaultValue / SetVectorParameterDefaultValue
   are unexported and not UFUNCTIONs** — `Runtime/Engine/Classes/Materials/MaterialParameterCollection.h:95-107`
   (`#if WITH_EDITOR` block, no ENGINE_API, no UFUNCTION ⇒ neither direct link nor
   ProcessEvent). Exported workaround used by set_mpc_parameters: write the UPROPERTY arrays
   (:89-93) directly + PreEditChange/PostEditChange virtuals (:140-144). Same precedent family
   as ULandscapeComponent::UpdateCollisionData.
   **Phase-2: verified** — header block re-read (`#if WITH_EDITOR`, no ENGINE_API, no UFUNCTION,
   MaterialParameterCollection.h:95-107); Pre/PostEditChange overrides at :140-144 confirmed, and
   the propagation path they trigger was read (ParameterCollection.cpp:227-305, see
   set_mpc_parameters verdict).
2. **URuntimeVirtualTextureFactory is completely unexported** — `Editor/VirtualTexturingEditor/Classes/RuntimeVirtualTextureFactory.h:16-17`
   is `UCLASS(hidecategories = (Object))` with NO API macro (not even MinimalAPI) ⇒
   `NewObject<URuntimeVirtualTextureFactory>` cannot link from MifBridge. Workaround is safe
   because the factory body is one line (`RuntimeVirtualTextureFactory.cpp:23-28`) — replicate the
   NewObject call directly (create_rvt_asset).
   **Phase-2: verified** — UCLASS(hidecategories = (Object)) with no API macro re-read
   (RuntimeVirtualTextureFactory.h:16-17); factory cpp is the single NewObject line as claimed
   (RuntimeVirtualTextureFactory.cpp:23-28).
3. **Cooked materials have NO graph**: `UMaterialExpression` is `UCLASS(abstract, Optional, BlueprintType, hidecategories=Object, MinimalAPI)`
   (`Runtime/Engine/Classes/Materials/MaterialExpression.h:183-184`) — `Optional` classes are
   stripped from cooked packages, and UMaterial's expression collection lives in
   `UMaterialEditorOnlyData` (Material.h:310). Every graph endpoint on this axis REFUSES on
   PKG_Cooked materials; only create-new + instance-derivation work against base-game content.
   This is the axis-wide cooked constraint, stated once here and per-endpoint above.
   **Phase-2: verified** — `UCLASS(abstract, Optional, BlueprintType, hidecategories=Object,
   MinimalAPI)` re-read at MaterialExpression.h:183-184, and `UCLASS(MinimalAPI, Optional)` on
   UMaterialEditorOnlyData re-read at Material.h:309-310. Both Optional ⇒ stripped from cooked
   packages; the refusal rule stands.
4. **Cooked textures have no FTextureSource** — engine's own comment at Texture.h:1092-1095:
   "Always check Source.IsValid before using Source." Compression/mip-regeneration edits on
   .pak textures are impossible; refresh_texture reports sourceValid per texture instead of
   pretending.
5. **UMaterialFactoryNew / UMaterialFunctionFactoryNew / UMaterialParameterCollectionFactoryNew
   lack the method-level UNREALED_API** that UMaterialInstanceConstantFactoryNew::FactoryCreateNew
   has (MaterialInstanceConstantFactoryNew.h:23 vs MaterialFactoryNew.h:24,
   MaterialFunctionFactoryNew.h:20, MaterialParameterCollectionFactoryNew.h:20). Not a blocker —
   virtual dispatch through the UFactory vtable (Factory.h:109-112 declares the virtual inline) —
   but implementers must call through the factory pointer normally and NOT try `::FactoryCreateNew`
   qualified-name calls, which would need the missing import.
   **Phase-2: verified** — MaterialFactoryNew.h:24, MaterialFunctionFactoryNew.h:20,
   MaterialParameterCollectionFactoryNew.h:20 all bare `virtual ... override` (no macro);
   MaterialInstanceConstantFactoryNew.h:23 carries UNREALED_API; UFactory is
   `UCLASS(abstract, MinimalAPI)` with FactoryCreateNew declared inline-virtual at
   Factory.h:109-112. Virtual-dispatch route sound; qualified-call warning correct.
6. **GetMaterialSelectedNodes (MaterialEditingLibrary.h:191-192) genuinely requires an open
   material editor window** (it queries editor UI selection) — excluded from list_material_expressions.
   By contrast GetMaterialPropertyInputNode does NOT (verified in MaterialEditingLibrary.cpp:797-806
   — reads the material object directly); the header comments are misleading on both.
   **Phase-2: verified** — GetMaterialSelectedNodes impl re-read (cpp:781-795): gated on
   `FindMaterialEditorForAsset`, returns empty set with no editor open, exactly as claimed;
   GetMaterialPropertyInputNode impl re-read (cpp:797-806), no editor dependency (but see
   list_material_expressions' verdict for its null-deref caution).
7. **FMaterialStatistics/GetStatistics may lag compilation** — stats describe the last completed
   shader map; a get_material_stats immediately after recompile_material returns stale/zero
   numbers. Mitigated by bundling the compiling flag into the response (both endpoints), not
   fixable engine-side in 5.3.
   **Phase-2: OVERTURNED — the mechanism is the opposite.** `GetStatistics` never returns stale
   numbers: it submits the material's outstanding compile jobs at High priority and calls
   `Resource->FinishCompilation()` (MaterialEditingLibrary.cpp:1355-1362), synchronously blocking
   the game thread until that material's shaders are built. The gap is not staleness but an
   in-handler compile wait; see get_material_stats' Phase-2 verdict for the guard design. The
   "compiling flag in the response" mitigation is moot as written.
8. **Material layers on the BASE material** (root UMaterial with MaterialAttributeLayers
   expression + per-layer parameter association editing) is substantially more machinery than the
   instance route (FMaterialLayersFunctionsEditorOnlyData sync — MaterialLayersFunctions.h:55,
   plus editor-only tree state). Instance-level authoring (set_material_instance_layers) is the
   viable 5.3 route; base-material layer authoring is NOT proposed — cost outweighs value while
   regular expression graphs cover the same ground.
9. **BuildReflectionCaptures / EditorBuild(BuildLighting) results are unsavable on cooked maps**
   — pairs with the documented "editing/saving cooked base-game maps" impossible; endpoints must
   flag cookedMap in responses rather than fail silently.
   **Phase-2: verified with refinement** — reflection-capture results live in
   `Level->MapBuildData` (BuildData package), not the ULevel package (engine comment,
   EditorEngine.cpp:3971); the cookedMap flag should speak of MapBuildData persistence.
10. **No dedicated 5.3 API for per-actor HLOD exclusion beyond bEnableAutoLODGeneration**
   (PrimitiveComponent.h:317) — the World-Partition HLOD exclusion workflows of 5.4+ do not exist
   in this branch; set_actor_render_overrides exposes the 5.3 flag and nothing more.
   **Phase-2: flag citation verified** (uint8 bEnableAutoLODGeneration:1, "Whether to include
   this component in HLODs or not", PrimitiveComponent.h:315-317).

## UNVERIFIED

- ELightingBuildQuality plumbing for build_lighting's `quality` param — the setting lives in
  ULevelEditorMiscSettings/FLightingBuildOptions; I did not open those headers, so v1 documents
  editor-default quality only. (Missing: header read + export check.)
  **Phase-2: RESOLVED** — EditorBuild reads `[LightingBuildOptions] QualityLevel` from
  GEditorPerProjectIni at kick time (EditorBuildUtils.cpp:304-313); write it with GConfig->SetInt
  before the kick. See build_lighting's Phase-2 verdict.
- ETextureRenderTargetFormat full value list for create_render_target — enum cited by name from
  KismetRenderingLibrary.h:49 default (RTF_RGBA16f) but the enum header
  (Engine/TextureRenderTarget2D.h) was not opened; implementer should paste the value list.
  **Phase-2: RESOLVED** — TextureRenderTarget2D.h:20-44: RTF_R8, RTF_RG8, RTF_RGBA8,
  RTF_RGBA8_SRGB, RTF_R16f, RTF_RG16f, RTF_RGBA16f, RTF_R32f, RTF_RG32f, RTF_RGBA32f, RTF_RGB10A2.
- ERuntimeVirtualTextureMaterialType full value list — two members verified verbatim
  (RuntimeVirtualTexture.h:38,:47); full enum lives in VT/RuntimeVirtualTextureEnum.h (not opened).
  **Phase-2: RESOLVED** — `Runtime/Engine/Public/VT/RuntimeVirtualTextureEnum.h:35-45`; the
  proposed list was wrong for this branch (no Mask4/Displacement in 5.3.2) — fixed in
  create_rvt_asset's params, see its verdict.
- UMaterialFunction::Description / bExposeToLibrary UPROPERTY names for create_material_function's
  optional description param — MaterialFunction.h was grepped only for GetExpressions/UCLASS.
  **Phase-2: RESOLVED** — MaterialFunction.h:52 (`FString Description`) and :60
  (`uint8 bExposeToLibrary:1`); both plain UPROPERTYs.
- Whether UMaterialEditingLibrary::LayoutMaterialExpressions requires the material's GraphNode
  editor representations to exist (possible no-op on never-opened assets) — needs a runtime test;
  cpp not read for this one function.
  **Phase-2: RESOLVED** — impl works on MaterialExpressionEditorX/Y directly, no GraphNode needed
  (MaterialEditingLibrary.cpp:193-278); only lays out nodes reachable from property/function
  outputs. See layout_material_expressions' verdict.
- Texture async-build completion polling (IsAsyncCacheComplete family) for refresh_texture —
  method name not verified in Texture.h; v1 returns queued-state only.
  **Phase-2: RESOLVED** — `ENGINE_API bool IsAsyncCacheComplete() const;` Texture.h:1625;
  `IsDefaultTexture()` ENGINE_API :1615. A texture_build_status poll is viable in v2.

## Coverage log

Covered (headers opened, cited, proposals derived): UMaterialEditingLibrary full walk;
UnrealEd material factories (4 read in full + 8 dir-listed); UMaterialExpression census
(244 headers / 277 subclasses / 8 abstract / +8 landscape) with 34-class key-property catalogue;
EMaterialProperty enum verbatim; MPC class + factory; material layers structs + instance setters;
RVT asset + factory (incl. cpp); UTexture settings/Source/UpdateResource; GShaderCompilingManager;
FEditorBuildUtils + UEditorEngine lighting/reflection builds; UKismetRenderingLibrary render-target
create/read/export; UPrimitiveComponent/UStaticMeshComponent/UMeshComponent cull/LOD/material
surface; UStaticMesh LOD/Nanite; plugin precedents (create_material_instance,
H_diagnose_landscape_draws, Build.cs dep list). All 25 proposals diffed against the 159-endpoint
covered list; overlaps routed to Compositions.

Remaining for Phase-2 (not holes in the sweep, but depth cuts): scene-capture actor driving
(USceneCaptureComponent2D CaptureScene — pairs with create/read_render_target); post-process
volume settings census (FPostProcessSettings is one giant UPROPERTY struct — likely pure
set_property composition, needs confirmation); Nanite rebuild endpoints (UStaticMesh build paths);
material-baking via MaterialUtilities (FMaterialUtilities export check); the six UNVERIFIED lines
above.

### Phase-2 adversarial verification log (2026-07-26)

All 25 proposals re-verified against source: every cited header region re-opened; the cited .cpp
implementations of RecompileMaterial, GetStatistics, BuildTextureStreamingData,
CompileDebugViewModeShaders, EditorBuild, BuildReflectionCaptures, CreateMaterialExpressionEx,
Connect*, GetMaterialPropertyInputNode, GetMaterialSelectedNodes, LayoutMaterialExpressions,
UpdateMaterialFunction/Instance, and UMaterialParameterCollection::PostEditChangeProperty were
read for blocking/modal/GC hazards. Result: 18 CONFIRMED, 7 CORRECTED (recompile_material —
hidden GC×2 + slow-task dialog + debug-shader busy-wait tail; get_material_stats —
FinishCompilation synchronous wait; build_reflection_captures — FinishAllCompilation wait +
BeginSlowTask + SM5 check() assert; build_lighting — FBuildOptions:: qualifier fix + quality
plumbing found + progress-window/BSP-rebuild hazards; create_rvt_asset — 5.3.2 enum list fix +
protected-member write route; create_render_target — GC rooting requirement;
set_actor_render_overrides — bOverrideMinLOD silent-ignore gate), 0 DEMOTED. Negatives: 10
spot-verified, 1 OVERTURNED in mechanism (#7 — GetStatistics blocks rather than lags). All 6
UNVERIFIED items resolved with citations. No name collisions with the 160 covered endpoints;
handler names grepped against MifBridgeHandlers.h (0 hits). Cross-axis overlap:
build_reflection_captures is also proposed by axis F — this file's version carries the hazard
analysis; reconcile at merge. Build.cs re-read: MaterialEditor confirmed NOT in deps (the axis's
one new module dependency, editor-only, engine-core, no plugin gating).
