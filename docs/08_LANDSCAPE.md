# Landscape authoring

Source: `Source/MifBridge/Private/MifBridgeLandscape.cpp`
Endpoints: `create_landscape`, `sculpt_landscape`, `paint_landscape`, `bind_landscape_rvt`,
`landscape_info`

## Why this exists

Four separate attempts at "ground" failed, each differently, for the same underlying reason —
MifBridge could not author a Landscape, so every attempt was a workaround built out of static meshes:

| Attempt | Symptom | Actual cause |
|---|---|---|
| `/Engine/BasicShapes/Plane` scaled 16–300× | blurred, smeared corners | one UV set stretched over 30,000 units |
| 4 ground materials mixed randomly per tile | *more* obvious grid — a checkerboard | randomising the material emphasises the tile boundary instead of hiding it |
| Grid of planes at moderate scale | visible seams | adjacent tiles restart their UVs at the edge |
| 2,116 `sm_GroundRocks_01_01` at native scale | gaps showing sky | that mesh is an irregular decal patch, **not** a tileable tile |

The shipped game does the obvious thing: an `ALandscape` with `DDS2_Landscape_IslaSombra` and painted
weight layers. Confirmed from the asset registry — 64 `LandscapeStreamingProxy` assets and a full set
of layer infos under `/Game/Maps/IslaSombra/IslaSombra_sharedassets/`.

**The lesson generalises:** when repeated attempts at the same goal each fail *differently*, the
problem is usually a missing capability, not a bad parameter. Stop tuning and go add the endpoint.

## The two coordinate spaces

Mixing these up is the whole difficulty of the file.

- **Vertex space** — integer `(X, Y)` indices into the heightmap, `0..SizeX-1`. Every engine edit API
  uses it.
- **World space** — what every other MifBridge endpoint speaks, and what a caller wants to say
  ("flatten a 4,000-unit pad at the town centre").

`LandscapeActorToWorld()` converts. One landscape quad is one unit in the actor's local space, so
local X/Y *are* vertex indices. **All four endpoints take and return world units only** — the
conversion never leaks out to the caller.

## Height encoding

`uint16`, where `32768` == the landscape actor's Z. One step is `DrawScale.Z / 128`
(`LANDSCAPE_ZSCALE`) world units, so at the default scale of 100 a step is 0.78125 units and the
usable relief is roughly ±25,600 units.

`HeightToWorld` / `WorldToHeight` in the anonymous namespace are the only places that constant
appears. `WorldToHeight` clamps — a request for ±40,000 units of relief would otherwise wrap the
`uint16` and produce spikes instead of an error.

## Gotchas encoded in the handlers

- **`quadsPerSection` must be 7/15/31/63/127/255.** Any other value builds fine and renders with
  cracks between sections, because the LOD chain assumes power-of-two-minus-one. The handler rejects
  it up front rather than letting you discover it visually.
- **A layered landscape material with nothing painted renders as its fallback** — black, for
  `DDS2_Landscape_IslaSombra`. So `create_landscape` fills the *first* layer to full weight by
  default. A landscape you can see is the useful default; pass explicit `weight` values to override.
- **`bCanHaveLayersContent = false` is set before `Import`.** With edit layers *on*, the direct
  writes that `sculpt_landscape`/`paint_landscape` perform via `FLandscapeEditDataInterface` land in
  a layer that is never composited — the calls succeed and nothing changes.
- **`CreateLandscapeInfo()` + `UpdateLayerInfoMap()` after `Import`.** Without them the landscape
  renders but has no `ULandscapeInfo`, and every later sculpt/paint call reports "no landscape".
- **Falloff defaults to 0.5, not 0.** A hard-edged flatten brush produces a mesa with vertical walls.
- **Collision is cooked from the heightfield separately.** After sculpting, components get
  `UpdateCachedBounds()` + `MarkRenderStateDirty()`, or the visible surface moves and the surface the
  player walks on does not.
- **Stride `0`** in the `FLandscapeEditDataInterface` calls means "one row is `X2-X1+1` samples". It
  is not a mistake.

## Transaction policy

- `create_landscape` → **self-managed**. `ALandscape::Import` builds and registers heightmap and
  weightmap *textures*. Undoing that mid-flight leaves components pointing at freed textures — the
  same class of hazard as compiling a Blueprint inside an `FScopedTransaction`.
- `sculpt_landscape`, `paint_landscape` → transacted (this is what the landscape editor itself does).
- `landscape_info` → read-only.

See `00_ARCHITECTURE.md` for why that three-way split exists at all.

## Project assets

| Purpose | Path |
|---|---|
| Landscape material | `/Game/Landscape/Materials/DDS2_Landscape_IslaSombra` |
| Master material | `/Game/Landscape/Materials/DDS2_Landscape_MasterMat` |
| Layer infos | `/Game/Maps/IslaSombra/IslaSombra_sharedassets/{A,B,C,D,E,Automaterial}_LayerInfo` |

## Road kit (measured, not guessed)

Sizes came from spawning each mesh and reading `get_actor_bounds`.

| Mesh | Size (X × Y × Z) | Use |
|---|---|---|
| `Roads/SM_RoadSidewalk` | 400 × 700 × 35 | Paved street segment, curbs **both** sides |
| `Roads/SM_RoadSidewalk_Left` / `_Right` | 400 × 553 × 35 | One-sided variants |
| `Roads/RoadAsphaltSplineSegment` | 400 × 440 × 44 | Asphalt without curbs |
| `SM_Road_Dirt_Wide` | 1000 × 847 × 19 | Dirt track |
| `SM_Road_Dirt_Narrow` | 500 × 478 × 51 | Narrow dirt track |
| `Roads/SM_Road_Bricks` | 500 × 478 × 58 | Brick paving |
| `UrbanDistrict/.../sm_Curb_01_01` | 433 × 38 × 13 | Standalone curb |

All under `/Game/StaticMeshes/Enviro/RoadsAndBridges/` unless noted. Repeat `SM_RoadSidewalk` every
400 units along X for a continuous street.

**Paint dirt under the road corridor.** Laying road meshes on unpainted grass leaves them visibly
floating on the wrong surface; a `paint_landscape` pass along the corridor is what sells it.

## Runtime Virtual Textures — the black-terrain trap

`DDS2_Landscape_MasterMat` samples a runtime virtual texture for its base colour. A landscape using it
needs **two** things wired up, and missing either renders the terrain black:

1. the RVT present in the landscape's `RuntimeVirtualTextures` array — *what to draw into*
2. an `ARuntimeVirtualTextureVolume` in the level bounding the region — *where it applies*

`bind_landscape_rvt` does both. It has to, because the editor's "Create Volumes" button is pure UI:
`ALandscapeProxy::bSetCreateRuntimeVirtualTextureVolumes` is a `Transient` `VisibleAnywhere`
placeholder with **no engine-side behaviour** — nothing reads it. The real work lives in
`FLandscapeProxyUIDetails::CreateRuntimeVirtualTextureVolume`, which the endpoint mirrors:

```cpp
NewVolume->VirtualTextureComponent->SetVirtualTexture(RVT);
NewVolume->VirtualTextureComponent->SetBoundsAlignActor(Landscape);  // align FIRST
RuntimeVirtualTexture::SetBounds(NewVolume->VirtualTextureComponent); // then fit
```

Order matters: `SetBounds` reads the align actor, so reversing those two lines yields a volume
covering nothing — which looks exactly like having no volume at all.

Project RVTs: `/Game/Maps/IslaSombra/RVT/RVT_IslaSombraLandscape` and `..._IslaSombraLandscapeHeight`.

### Reading the symptom

The failure has a distinctive look worth recognising, because it is *not* any of the usual material
failures:

| Look | Means |
|---|---|
| Magenta / bright pink | shader compile error |
| Checkerboard grey | missing material |
| Flat lavender | **collision view mode** — not a material problem at all |
| Black with white speckle | base colour missing but detail/grass still draws |

The speckle is the tell: geometry is perfect, detail elements render, only the base albedo is missing.

**Do not reach for `bind_landscape_rvt` on this symptom.** Binding an RVT that has no valid pages does
not restore the terrain, and it turns every *other* RVT-sampling material (buildings, roads) blown
white. See `01_POSTMORTEMS.md`. In a scratch level the right fix is a landscape material that does not
sample an RVT; `set_property` on `LandscapeMaterial` applies in place without a rebuild.

**Fastest diagnostic for any "wrong colour" scene:** spawn `/Engine/BasicShapes/Cube` with
`BasicShapeMaterial` next to the offending object. If the cube looks right, exposure, lighting and the
capture path are all fine and the problem is that object's material.

`landscape_info` reports `runtimeVirtualTextures`, `materialLayers` and `componentsWithoutWeightmap`
precisely so this is one call instead of a guessing game.

## Diagnosing a wrong-looking landscape

Ask `landscape_info` first, and read it in this order:

1. **`componentsWithoutWeightmap` > 0** — painted layer data never landed; every blend weight is zero.
2. **`materialLayers` vs painted `layers`** — painting a layer the material does not declare succeeds
   and changes nothing. The two lists must intersect.
3. **`runtimeVirtualTextures` empty** — black base colour if the material samples an RVT.
4. Only then look at pixels.
