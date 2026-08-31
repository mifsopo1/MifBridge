// MifBridge — LANDSCAPE: creating, sculpting and painting real terrain.
//
// Why this file exists: every earlier attempt at "ground" was a workaround. Stretched
// /Engine/BasicShapes/Plane gave blurred corners (one UV set smeared over 30000 units); a grid of
// tiles gave a visible checkerboard; instanced rock meshes at native scale gave gaps, because
// sm_GroundRocks_01_01 is an irregular decal patch, not a tileable tile. The shipped game does the
// obvious thing instead — ALandscape with DDS2_Landscape_IslaSombra and painted weight layers — and
// until MifBridge could author one, no amount of cleverness with static meshes was going to match it.
//
// Two coordinate spaces matter here and mixing them is the whole difficulty:
//   * VERTEX space — integer (X,Y) indices into the heightmap, 0..SizeX-1. All the edit APIs use it.
//   * WORLD space — what every other MifBridge endpoint speaks, and what a caller wants to say
//     ("flatten a 4000-unit pad at the town centre").
// LandscapeActorToWorld() converts between them; one landscape quad is one unit in the actor's local
// space, so local X/Y ARE vertex indices. Callers only ever pass world units.
//
// Height encoding: uint16, 32768 == the actor's Z. One step is DrawScale.Z/128 world units
// (LANDSCAPE_ZSCALE), so with the default scale of 100 a step is 0.78125 units and the usable range
// is roughly +/-25600. HeightToWorld/WorldToHeight below are the only places that constant appears.
#include "MifBridgeHandlers.h"
#include "HAL/FileManager.h"
#include "IImageWrapper.h"
#include "IImageWrapperModule.h"
#include "Misc/FileHelper.h"
#include "Misc/Base64.h"
#include "MifBridgeVersion.h"                   // the 5.7 EditorApplySpline guard change
#include "Components/SplineComponent.h"
#include "Subsystems/EditorActorSubsystem.h"
#include "MifBridgeVersion.h"   // MIF_ENGINE_5_7_PLUS - Import gained a parameter
#include "MifBridgeLog.h"

#include "Editor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Landscape.h"
#include "LandscapeProxy.h"
#include "LandscapeInfo.h"
#include "LandscapeEdit.h"
#include "LandscapeLayerInfoObject.h"
#if MIF_ENGINE_AT_LEAST(5, 6)
#include "LandscapeEditLayer.h"                 // ULandscapeEditLayerBase - 5.6+ only
#endif
#include "LandscapeComponent.h"
#include "Materials/Material.h"
#include "Materials/MaterialInterface.h"
#include "UObject/UObjectGlobals.h"
#include "Components/RuntimeVirtualTextureComponent.h"
#include "RuntimeVirtualTextureSetBounds.h"
#include "VT/RuntimeVirtualTexture.h"
#include "VT/RuntimeVirtualTextureVolume.h"

namespace MifBridge
{
	namespace
	{
		// One height step in world units, for a landscape whose DrawScale.Z is ZScale.
		constexpr double kLandscapeZScale = 1.0 / 128.0;
		FORCEINLINE double HeightStepWorld(double ZScale) { return ZScale * kLandscapeZScale; }

		// 32768 is "at the actor's Z". Clamped because a caller asking for +/-40000 units of relief
		// would otherwise wrap around the uint16 and produce spikes rather than an error.
		uint16 WorldToHeight(double WorldOffset, double ZScale)
		{
			const double Steps = WorldOffset / HeightStepWorld(ZScale);
			return (uint16)FMath::Clamp(32768.0 + Steps, 0.0, 65535.0);
		}
		double HeightToWorld(uint16 Height, double ZScale)
		{
			return ((double)Height - 32768.0) * HeightStepWorld(ZScale);
		}

		// LandWorld() was another spelling of "the editor world"; it is MifBridge::EditorWorld() now
		// (declared in MifBridgeHandlers.h, defined once in MifBridgeCommon.cpp). Deliberately the
		// EDITOR world and NOT the PIE-preferring ActiveWorld(): landscapes are authored in the editor
		// world only, and PIE inherits a copy that is thrown away on stop, so editing that copy would
		// silently discard the work.

		// The sculpt EDIT LAYER names, in stack order. ONE reader for the two places that need
		// them - landscape_info reports them, and import_landscape_heightmap names them in its
		// refusal - so the engine-version split lives in exactly one place.
		//
		// 5.6 moved the per-layer data onto ULandscapeEditLayerBase and deprecated the index API.
		// The deprecated spellings still work on 5.7 (GetLayerCount really does return
		// LandscapeEditLayers.Num(); it is not one of the empty-bodied ones), but there is no
		// reason to write against a deprecation that will eventually be emptied.
		struct FMifEditLayer
		{
			FString Name;
			FString Guid;
			bool bVisible = false;
			bool bLocked = false;
		};

		TArray<FMifEditLayer> ReadEditLayers(ALandscape* Landscape)
		{
			TArray<FMifEditLayer> Out;
			if (!Landscape) { return Out; }
#if MIF_ENGINE_AT_LEAST(5, 6)
			for (const ULandscapeEditLayerBase* EL : Landscape->GetEditLayersConst())
			{
				if (!EL) { continue; }
				Out.Add({ EL->GetName().ToString(), EL->GetGuid().ToString(),
						  EL->IsVisible(), EL->IsLocked() });
			}
#else
			for (int32 i = 0; i < static_cast<int32>(Landscape->GetLayerCount()); ++i)
			{
				const FLandscapeLayer* EL = Landscape->GetLayer(i);
				if (!EL) { continue; }
				Out.Add({ EL->Name.ToString(), EL->Guid.ToString(), EL->bVisible, EL->bLocked });
			}
#endif
			return Out;
		}

		TArray<FString> EditLayerNames(ALandscape* Landscape)
		{
			TArray<FString> Names;
			for (const FMifEditLayer& L : ReadEditLayers(Landscape)) { Names.Add(L.Name); }
			return Names;
		}

		// REFUSE A MERGED-HEIGHTMAP WRITE ON A LANDSCAPE THAT HAS EDIT LAYERS, because it cannot
		// survive. Used by sculpt_landscape and import_landscape_heightmap - both call
		// FLandscapeEditDataInterface::SetHeightData with no FScopedSetLandscapeEditingLayer, so
		// they write the merged composite and the next edit-layer update regenerates that
		// composite from the layers and throws the write away.
		//
		// Measured 2026-08-31 on a landscape with two edit layers, identically for both: ok:true
		// (import also reported ZERO mismatches from its own read-back), an export immediately
		// afterwards differed, and an export two seconds later was byte-identical to the one taken
		// BEFORE the write. Each endpoint's postcondition is real and simply runs before the thing
		// that undoes it, so no amount of reading back at the right moment would catch this.
		//
		// NOT for apply_spline_to_landscape. That one goes THROUGH the layer - the engine opens
		// FScopedSetLandscapeEditingLayer around the rasterize - so its write persists and only its
		// MEASUREMENT was early. It calls ForceUpdateLayersContent() before sampling instead.
		// "landscape writer + edit layers" is not the bug; writing the merged result is.
		//
		// Succeeding and then silently reverting is the worst outcome available, so this refuses.
		bool RefuseIfEditLayers(ALandscape* Landscape, const TSharedRef<FJsonObject>& Out)
		{
			if (!Landscape || !Landscape->HasLayersContent()) { return false; }
			const TArray<FString> Names = EditLayerNames(Landscape);
			Fail(Out, FString::Printf(
				TEXT("'%s' has sculpt EDIT LAYERS (%s), and this endpoint writes the MERGED ")
				TEXT("heightmap directly - with no FScopedSetLandscapeEditingLayer around the edit, ")
				TEXT("the next edit-layer update regenerates the composite from the layers and ")
				TEXT("DISCARDS the write. Measured: ok:true, an export immediately after differs, ")
				TEXT("and two seconds later it is byte-identical to the heightmap from BEFORE. A ")
				TEXT("read-back cannot catch this because it runs before the composite does. Use ")
				TEXT("apply_spline_to_landscape, which writes through the layer, or create_landscape ")
				TEXT("(which leaves edit layers OFF) for a landscape this can edit. See ")
				TEXT("landscape_info's editLayers[]. NOTHING was changed."),
				*Landscape->GetActorLabel(), *FString::Join(Names, TEXT(", "))));
			return true;
		}

		ALandscape* FindLandscape(UWorld* World, const FString& Query)
		{
			if (!World) { return nullptr; }
			ALandscape* First = nullptr;
			for (TActorIterator<ALandscape> It(World); It; ++It)
			{
				ALandscape* L = *It;
				if (!L || !IsValid(L)) { continue; }
				if (!First) { First = L; }
				if (!Query.IsEmpty() &&
					(L->GetPathName() == Query || L->GetName() == Query || L->GetActorLabel() == Query))
				{
					return L;
				}
			}
			// No query means "the landscape" — the overwhelmingly common case is exactly one.
			return Query.IsEmpty() ? First : nullptr;
		}

		// Vertex extent of a landscape, derived from its component grid. Import fixed these at
		// creation; ULandscapeInfo caches them as the inclusive min/max the edit APIs expect.
		bool LandscapeExtent(ALandscape* Landscape, int32& MinX, int32& MinY, int32& MaxX, int32& MaxY)
		{
			ULandscapeInfo* Info = Landscape ? Landscape->GetLandscapeInfo() : nullptr;
			return Info && Info->GetLandscapeExtent(MinX, MinY, MaxX, MaxY);
		}

		ULandscapeLayerInfoObject* LoadLayerInfo(const FString& Path)
		{
			if (Path.IsEmpty()) { return nullptr; }
			return LoadObject<ULandscapeLayerInfoObject>(nullptr, *Path);
		}

		// ULandscapeLayerInfoObject::LayerName is UE_DEPRECATED(5.7, "Property will be made private.
		// Use public Getters/Setter instead.") - GetLayerName() just returns the same field
		// (LandscapeLayerInfoObject.h:140), so this is forward-compat only, not a behaviour change
		// like FStaticMeshBatchRelevance::LODIndex was. No getter exists on 5.3 (confirmed by grep of
		// D:/UE532's LandscapeLayerInfoObject.h - the field there is plain, no deprecation).
		FName MifLayerInfoName(const ULandscapeLayerInfoObject* Info)
		{
#if MIF_ENGINE_AT_LEAST(5, 7)
			return Info->GetLayerName();
#else
			return Info->LayerName;
#endif
		}
	}

	// --- create_landscape ---------------------------------------------------
	//   in:  { location?:{x,y,z}, scale?:{x,y,z}, componentsX?, componentsY?,
	//          quadsPerSection? (7|15|31|63|127|255), sectionsPerComponent? (1|2),
	//          material?, layers?:[{name, layerInfo, weight?}],
	//          heightMode? ("flat"|"rolling"|"island"), amplitude?, frequency?, seed?, label? }
	//   out: { actorPath, vertsX, vertsY, worldSizeX, worldSizeY, components, layers[] }
	//
	// Self-managed: Import() builds components, heightmap and weightmap TEXTURES and registers them.
	// An outer FScopedTransaction over that is the same class of hazard as compiling a Blueprint
	// inside one — undo would leave half-registered components pointing at freed textures.
	void H_create_landscape(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("location"), TEXT("scale"), TEXT("componentsX"), TEXT("componentsY"),
			  TEXT("quadsPerSection"), TEXT("sectionsPerComponent"), TEXT("material"),
			  TEXT("landscapeMaterial"), TEXT("layers"), TEXT("heightMode"), TEXT("amplitude"),
			  TEXT("frequency"), TEXT("seed"), TEXT("label"), TEXT("folder") },
			TEXT("location {x,y,z}, scale {x,y,z}, componentsX, componentsY, quadsPerSection (7|15|31|63|127|255), ")
			TEXT("sectionsPerComponent (1|2), material (alias: landscapeMaterial), ")
			TEXT("layers [{layerInfo (aliases: info, path), weight}], heightMode (\"flat\"|\"rolling\"|\"island\"), ")
			TEXT("amplitude, frequency, seed, label, folder"),
			{ { TEXT("name"), TEXT("use label - it sets the actor's display label") },
			  { TEXT("position"), TEXT("use location {x,y,z}") },
			  { TEXT("layerInfo"), TEXT("layers is an ARRAY of objects - pass layers:[{layerInfo:\"/Game/.../X_LayerInfo\", weight:0..1}]") },
			  { TEXT("heightmap"), TEXT("importing a heightmap file is not supported - use heightMode (flat|rolling|island) with amplitude, frequency and seed") },
			  { TEXT("rotation"), TEXT("not supported - the landscape is always spawned axis-aligned") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		// --- shape of the grid -------------------------------------------------
		// QuadsPerSection must be one of the power-of-two-minus-one sizes the renderer's LOD chain
		// assumes; anything else builds but renders with cracks between sections.
		int32 QuadsPerSection = JInt(In, TEXT("quadsPerSection"), 63);
		static const TArray<int32> Allowed = { 7, 15, 31, 63, 127, 255 };
		if (!Allowed.Contains(QuadsPerSection))
		{
			Fail(Out, FString::Printf(
				TEXT("quadsPerSection must be one of 7/15/31/63/127/255 (got %d) — other values crack between sections"),
				QuadsPerSection));
			return;
		}
		const int32 SectionsPerComponent = FMath::Clamp(JInt(In, TEXT("sectionsPerComponent"), 1), 1, 2);
		const int32 ComponentsX = FMath::Clamp(JInt(In, TEXT("componentsX"), 8), 1, 32);
		const int32 ComponentsY = FMath::Clamp(JInt(In, TEXT("componentsY"), 8), 1, 32);

		const int32 QuadsPerComponent = QuadsPerSection * SectionsPerComponent;
		const int32 VertsX = ComponentsX * QuadsPerComponent + 1;
		const int32 VertsY = ComponentsY * QuadsPerComponent + 1;

		FVector Loc(0, 0, 0);
		const TSharedPtr<FJsonObject>* LocObj = nullptr;
		if (In->TryGetObjectField(TEXT("location"), LocObj) && LocObj)
		{
			const TSharedRef<FJsonObject> O = LocObj->ToSharedRef();
			Loc = FVector(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
		}
		FVector Scale(100, 100, 100);
		const TSharedPtr<FJsonObject>* ScaleObj = nullptr;
		if (In->TryGetObjectField(TEXT("scale"), ScaleObj) && ScaleObj)
		{
			const TSharedRef<FJsonObject> O = ScaleObj->ToSharedRef();
			Scale = FVector(JNum(O, TEXT("x"), 100), JNum(O, TEXT("y"), 100), JNum(O, TEXT("z"), 100));
		}

		// --- height field ------------------------------------------------------
		// Generated rather than imported from a file: the point is that an agent can ask for terrain
		// in one call. "flat" is the honest default; "rolling"/"island" exist because a dead-flat
		// plane reads as a test level no matter how good the material is.
		const FString HeightMode = JStr(In, TEXT("heightMode"), TEXT("flat")).ToLower();
		const double Amplitude = JNum(In, TEXT("amplitude"), 0.0);
		const double Frequency = JNum(In, TEXT("frequency"), 2.0);
		const double Seed = JNum(In, TEXT("seed"), 0.0);

		TArray<uint16> HeightData;
		HeightData.SetNumUninitialized(VertsX * VertsY);

		const double HalfX = (double)(VertsX - 1) * 0.5;
		const double HalfY = (double)(VertsY - 1) * 0.5;
		for (int32 Y = 0; Y < VertsY; ++Y)
		{
			for (int32 X = 0; X < VertsX; ++X)
			{
				double Offset = 0.0;
				if (HeightMode != TEXT("flat") && Amplitude > 0.0)
				{
					// Normalised -1..1 across the sheet so frequency means "hills across the whole
					// landscape", independent of the component count the caller picked.
					const double U = (X - HalfX) / FMath::Max(HalfX, 1.0);
					const double V = (Y - HalfY) / FMath::Max(HalfY, 1.0);
					// Two octaves: a broad swell plus a smaller ripple. Enough to stop the silhouette
					// reading as a plane without pretending to be real noise.
					const double Broad = FMath::Sin((U * Frequency + Seed) * PI) * FMath::Cos((V * Frequency + Seed) * PI);
					const double Fine  = FMath::Sin((U * Frequency * 2.7 + Seed) * PI) * FMath::Cos((V * Frequency * 2.3 + Seed) * PI);
					Offset = Amplitude * (Broad * 0.7 + Fine * 0.3);

					if (HeightMode == TEXT("island"))
					{
						// Radial falloff: high in the middle, dropping below the actor's Z at the rim
						// so the edges can sit under water instead of ending in a visible cliff.
						const double R = FMath::Clamp(FMath::Sqrt(U * U + V * V), 0.0, 1.0);
						const double Falloff = FMath::Cos(R * HALF_PI); // 1 at centre, 0 at the rim
						Offset = Offset * Falloff + Amplitude * (Falloff - 0.55);
					}
				}
				HeightData[Y * VertsX + X] = WorldToHeight(Offset, Scale.Z);
			}
		}

		// --- weight layers -----------------------------------------------------
		// A layered landscape material with NOTHING painted renders as the material's fallback, which
		// for DDS2_Landscape_IslaSombra is black. So the first layer is filled to full weight unless
		// the caller says otherwise — a landscape you can actually see is the useful default.
		TArray<FLandscapeImportLayerInfo> ImportLayers;
		TArray<TSharedPtr<FJsonValue>> LayersOut;
		const TArray<TSharedPtr<FJsonValue>>* LayerArr = nullptr;
		if (JArray(In, TEXT("layers"), LayerArr) && LayerArr)
		{
			bool bFirst = true;
			for (const TSharedPtr<FJsonValue>& Val : *LayerArr)
			{
				const TSharedPtr<FJsonObject>* Obj = nullptr;
				if (!Val.IsValid() || !Val->TryGetObject(Obj) || !Obj) { continue; }
				const TSharedRef<FJsonObject> O = Obj->ToSharedRef();

				const FString InfoPath = JStrAny(O, { TEXT("layerInfo"), TEXT("info"), TEXT("path") });
				ULandscapeLayerInfoObject* Info = LoadLayerInfo(InfoPath);
				if (!Info)
				{
					Fail(Out, FString::Printf(TEXT("could not load LandscapeLayerInfoObject '%s'"), *InfoPath));
					return;
				}

				FLandscapeImportLayerInfo Import(MifLayerInfoName(Info));
				Import.LayerInfo = Info;
				Import.SourceFilePath = TEXT("");

				// Default: first layer fully painted, the rest empty. That gives a visible surface
				// immediately and leaves the others to paint_landscape.
				const double Weight = JNum(O, TEXT("weight"), bFirst ? 1.0 : 0.0);
				const uint8 Fill = (uint8)FMath::Clamp(FMath::RoundToInt(Weight * 255.0), 0, 255);
				Import.LayerData.Init(Fill, VertsX * VertsY);

				TSharedRef<FJsonObject> LOut = MakeShared<FJsonObject>();
				LOut->SetStringField(TEXT("name"), MifLayerInfoName(Info).ToString());
				LOut->SetStringField(TEXT("layerInfo"), Info->GetPathName());
				LOut->SetNumberField(TEXT("weight"), Weight);
				LayersOut.Add(MakeShared<FJsonValueObject>(LOut));

				ImportLayers.Add(MoveTemp(Import));
				bFirst = false;
			}
		}

		// --- spawn + import ----------------------------------------------------
		FActorSpawnParameters Params;
		Params.ObjectFlags = RF_Transactional;
		ALandscape* Landscape = World->SpawnActor<ALandscape>(Loc, FRotator::ZeroRotator, Params);
		if (!Landscape) { Fail(Out, TEXT("failed to spawn ALandscape")); return; }

		Landscape->SetActorRelativeScale3D(Scale);
		// Edit layers OFF: this endpoint writes heights and weights directly, and the direct-edit path
		// (FLandscapeEditDataInterface) is what sculpt_landscape/paint_landscape use. Turning layers on
		// here would make those writes land in a layer that is never composited.
		// The FIELD is bCanHaveLayersContent_DEPRECATED in 5.7 (Landscape.h:664) and assigning it no
		// longer compiles. The getter/toggle pair exists in BOTH trees, so no version guard is needed
		// here - CanHaveLayersContent at LandscapeProxy.h:1444 (5.3) / :1578 (5.7), and
		// ToggleCanHaveLayersContent at Landscape.h:345 (5.3) / :543 (5.7).
		//
		// Toggle rather than set, because that is the only mutator offered - hence the guard: calling
		// it unconditionally would ENABLE edit layers on a landscape that already had them off.
		if (Landscape->CanHaveLayersContent())
		{
			Landscape->ToggleCanHaveLayersContent();
		}
		Landscape->SetLandscapeGuid(FGuid::NewGuid());

		const FString MaterialPath = JStrAny(In, { TEXT("material"), TEXT("landscapeMaterial") });
		if (!MaterialPath.IsEmpty())
		{
			UMaterialInterface* Mat = LoadObject<UMaterialInterface>(nullptr, *MaterialPath);
			if (!Mat)
			{
				Landscape->Destroy();
				Fail(Out, FString::Printf(TEXT("could not load landscape material '%s'"), *MaterialPath));
				return;
			}
			Landscape->LandscapeMaterial = Mat;
		}

		TMap<FGuid, TArray<uint16>> HeightPerLayer;
		HeightPerLayer.Add(FGuid(), MoveTemp(HeightData));
		TMap<FGuid, TArray<FLandscapeImportLayerInfo>> LayerPerLayer;
		LayerPerLayer.Add(FGuid(), MoveTemp(ImportLayers));

		// ALandscapeProxy::Import's trailing parameter differs in BOTH type and defaultedness:
		//   5.3 LandscapeProxy.h:1220  const TArray<FLandscapeLayer>* InImportLayers = nullptr
		//   5.7 LandscapeProxy.h:1398  const TArrayView<const FLandscapeLayer>& InImportLayers
		// 5.3 has a default so eleven arguments compile; 5.7 has none and wants a TArrayView. No single
		// spelling satisfies both, so this is one of the few places a real version guard is unavoidable.
		//
		// EMPTY is correct: edit layers are switched off immediately above, so there are none to pass.
#if MIF_ENGINE_5_7_PLUS
		Landscape->Import(
			Landscape->GetLandscapeGuid(),
			0, 0, VertsX - 1, VertsY - 1,
			SectionsPerComponent, QuadsPerSection,
			HeightPerLayer, nullptr,
			LayerPerLayer, ELandscapeImportAlphamapType::Additive,
			TArrayView<const FLandscapeLayer>());
#else
		Landscape->Import(
			Landscape->GetLandscapeGuid(),
			0, 0, VertsX - 1, VertsY - 1,
			SectionsPerComponent, QuadsPerSection,
			HeightPerLayer, nullptr,
			LayerPerLayer, ELandscapeImportAlphamapType::Additive);
#endif

		// Mirrors what the editor's New Landscape tool does after importing — without this the
		// landscape renders but has no ULandscapeInfo, and every later edit call finds nothing.
		ULandscapeInfo* Info = Landscape->CreateLandscapeInfo();
		if (Info) { Info->UpdateLayerInfoMap(Landscape); }
		Landscape->RegisterAllComponents();
		// Build collision from the imported heights straight away. Import creates the collision
		// COMPONENTS but a freshly imported landscape can still trace flat until this runs, and
		// "renders as hills, traces as a plane" is a genuinely confusing thing to debug downstream.
		Landscape->RecreateCollisionComponents();
		Landscape->PostEditChange();

		{
			FString ActualLabel, LabelNote;
			SetActorLabelChecked(Landscape, JStr(In, TEXT("label"), TEXT("Landscape")), ActualLabel, LabelNote);
			Out->SetStringField(TEXT("labelActual"), ActualLabel);
			if (!LabelNote.IsEmpty()) { Out->SetStringField(TEXT("labelNote"), LabelNote); }
		}
		const FString Folder = JStr(In, TEXT("folder"));
		if (!Folder.IsEmpty()) { Landscape->SetFolderPath(FName(*Folder)); }

		Out->SetStringField(TEXT("actorPath"), Landscape->GetPathName());
		Out->SetStringField(TEXT("label"), Landscape->GetActorLabel());
		Out->SetNumberField(TEXT("vertsX"), VertsX);
		Out->SetNumberField(TEXT("vertsY"), VertsY);
		Out->SetNumberField(TEXT("worldSizeX"), (VertsX - 1) * Scale.X);
		Out->SetNumberField(TEXT("worldSizeY"), (VertsY - 1) * Scale.Y);
		Out->SetNumberField(TEXT("components"), ComponentsX * ComponentsY);
		Out->SetNumberField(TEXT("quadsPerSection"), QuadsPerSection);
		Out->SetNumberField(TEXT("sectionsPerComponent"), SectionsPerComponent);
		Out->SetStringField(TEXT("heightMode"), HeightMode);
		Out->SetArrayField(TEXT("layers"), LayersOut);
		Out->SetBoolField(TEXT("hasLandscapeInfo"), Info != nullptr);

		if (LayersOut.Num() == 0 && !MaterialPath.IsEmpty())
		{
			Out->SetStringField(TEXT("warning"),
				TEXT("a layered landscape material with no painted layers renders as its fallback (usually black) — pass layers[] or call paint_landscape"));
		}
	}

	// --- sculpt_landscape ---------------------------------------------------
	//   in:  { landscape?, center:{x,y}, radius, mode ("raise"|"lower"|"flatten"|"smooth"),
	//          amount? (world units), falloff? (0..1 of radius that is feathered), targetZ? }
	//   out: { verticesTouched, area:{minX,minY,maxX,maxY} }
	//
	// World units in, world units out. This is what carves a flat pad for a town or a ditch for a
	// road; without a feathered falloff the result is a mesa with vertical walls, so falloff
	// defaults to half the radius.
	void H_sculpt_landscape(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("landscape"), TEXT("actorPath"), TEXT("center"), TEXT("radius"), TEXT("mode"),
			  TEXT("amount"), TEXT("falloff"), TEXT("targetZ") },
			TEXT("landscape (alias: actorPath; omit when there is only one), center {x,y} in WORLD units, ")
			TEXT("radius (world units), mode (\"raise\"|\"lower\"|\"flatten\"|\"smooth\"), ")
			TEXT("amount (world units, raise/lower ONLY), targetZ (a world Z, flatten ONLY), ")
			TEXT("falloff (0..1 of the radius that is feathered)"),
			{ { TEXT("strength"), TEXT("use amount (world units) with mode raise/lower") },
			  { TEXT("height"), TEXT("use targetZ (a world Z) with mode flatten, or amount with mode raise/lower") },
			  { TEXT("brushSize"), TEXT("use radius (world units)") },
			  { TEXT("target"), TEXT("use targetZ - it is a world Z, not a vertex height") },
			  { TEXT("z"), TEXT("center is an object - pass center:{x,y}; a flatten target is targetZ") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		ALandscape* Landscape = FindLandscape(World, JStrAny(In, { TEXT("landscape"), TEXT("actorPath") }));
		if (!Landscape) { Fail(Out, TEXT("no landscape found — call create_landscape first")); return; }
		ULandscapeInfo* Info = Landscape->GetLandscapeInfo();
		if (!Info) { Fail(Out, TEXT("landscape has no ULandscapeInfo — it was not imported correctly")); return; }

		const TSharedPtr<FJsonObject>* CenterObj = nullptr;
		if (!In->TryGetObjectField(TEXT("center"), CenterObj) || !CenterObj)
		{
			Fail(Out, TEXT("center:{x,y} is required (world units)")); return;
		}
		const TSharedRef<FJsonObject> C = CenterObj->ToSharedRef();
		const FVector CenterWorld(JNum(C, TEXT("x")), JNum(C, TEXT("y")), JNum(C, TEXT("z")));

		const double RadiusWorld = JNum(In, TEXT("radius"), 1000.0);
		if (RadiusWorld <= 0.0) { Fail(Out, TEXT("radius must be > 0")); return; }
		const FString Mode = JStr(In, TEXT("mode"), TEXT("flatten")).ToLower();
		const double Amount = JNum(In, TEXT("amount"), 0.0);
		const double Falloff = FMath::Clamp(JNum(In, TEXT("falloff"), 0.5), 0.0, 1.0);

		// Validate mode BEFORE the per-vertex loop. The refusal used to live INSIDE the loop, so a
		// brush that covered zero vertices (radius smaller than one quad) never reached it and an
		// invalid mode answered ok:true, verticesTouched:0 — a typo'd verb reported as a successful
		// no-op. Also name the argument/mode combinations that are silently ignored: `amount` is read
		// only by raise/lower and `targetZ` only by flatten, so passing one to the wrong mode used to
		// do nothing at all without saying so.
		{
			static const TCHAR* const kModes[] = { TEXT("raise"), TEXT("lower"), TEXT("flatten"), TEXT("smooth") };
			bool bKnownMode = false;
			for (const TCHAR* M : kModes) { if (Mode == M) { bKnownMode = true; break; } }
			if (!bKnownMode)
			{
				Fail(Out, FString::Printf(TEXT("unknown mode '%s' — use raise, lower, flatten or smooth"), *Mode));
				return;
			}
			const bool bUsesAmount  = (Mode == TEXT("raise") || Mode == TEXT("lower"));
			const bool bUsesTargetZ = (Mode == TEXT("flatten"));
			if (In->HasField(TEXT("amount")) && !bUsesAmount)
			{
				Fail(Out, FString::Printf(
					TEXT("amount is only used by mode raise/lower; mode '%s' would have ignored it. ")
					TEXT("Use %s, or drop amount."), *Mode, bUsesTargetZ ? TEXT("targetZ") : TEXT("no height argument")));
				return;
			}
			if (In->HasField(TEXT("targetZ")) && !bUsesTargetZ)
			{
				Fail(Out, FString::Printf(
					TEXT("targetZ is only used by mode flatten; mode '%s' would have ignored it. ")
					TEXT("Use %s, or drop targetZ."), *Mode, bUsesAmount ? TEXT("amount") : TEXT("no height argument")));
				return;
			}
			if (bUsesAmount && Amount == 0.0)
			{
				Fail(Out, FString::Printf(TEXT("mode '%s' needs a non-zero amount (world units) — with amount 0 every vertex would be written back unchanged and reported as touched"), *Mode));
				return;
			}
		}

		const FTransform ToWorld = Landscape->LandscapeActorToWorld();
		const FVector Local = ToWorld.InverseTransformPosition(CenterWorld);
		const FVector ActorScale = Landscape->GetActorScale3D();
		// One quad == one local unit, so a world radius becomes a vertex radius by dividing by the
		// XY draw scale. Non-uniform XY would make this an ellipse; take the smaller so the brush
		// never reaches outside the requested distance.
		const double VertRadius = RadiusWorld / FMath::Max(FMath::Min(ActorScale.X, ActorScale.Y), KINDA_SMALL_NUMBER);

		int32 MinX, MinY, MaxX, MaxY;
		if (!LandscapeExtent(Landscape, MinX, MinY, MaxX, MaxY))
		{
			Fail(Out, TEXT("could not read landscape extent")); return;
		}

		const int32 X1 = FMath::Clamp(FMath::FloorToInt(Local.X - VertRadius), MinX, MaxX);
		const int32 X2 = FMath::Clamp(FMath::CeilToInt (Local.X + VertRadius), MinX, MaxX);
		const int32 Y1 = FMath::Clamp(FMath::FloorToInt(Local.Y - VertRadius), MinY, MaxY);
		const int32 Y2 = FMath::Clamp(FMath::CeilToInt (Local.Y + VertRadius), MinY, MaxY);
		if (X2 < X1 || Y2 < Y1)
		{
			Fail(Out, TEXT("brush falls entirely outside the landscape — check center against its world bounds (landscape_info)"));
			return;
		}

		const int32 W = X2 - X1 + 1;
		const int32 H = Y2 - Y1 + 1;

		FLandscapeEditDataInterface Edit(Info);
		TArray<uint16> Data;
		Data.SetNumUninitialized(W * H);
		// Stride 0 means "one row is X2-X1+1 samples", the FLandscapeEditDataInterface convention.
		Edit.GetHeightDataFast(X1, Y1, X2, Y2, Data.GetData(), 0);

		// "flatten" needs a target. Default to the height already under the brush centre so a caller
		// can say "make this area level" without first querying what level means here.
		double TargetOffset = JNum(In, TEXT("targetZ"), TNumericLimits<double>::Lowest());
		const bool bTargetGiven = TargetOffset != TNumericLimits<double>::Lowest();
		if (Mode == TEXT("flatten") && !bTargetGiven)
		{
			const int32 CX = FMath::Clamp(FMath::RoundToInt(Local.X), X1, X2) - X1;
			const int32 CY = FMath::Clamp(FMath::RoundToInt(Local.Y), Y1, Y2) - Y1;
			TargetOffset = HeightToWorld(Data[CY * W + CX], ActorScale.Z);
		}
		else if (bTargetGiven)
		{
			// targetZ arrives as a WORLD Z; convert to an offset from the landscape actor's own Z.
			TargetOffset = TargetOffset - ToWorld.GetLocation().Z;
		}

		int32 Touched = 0;
		for (int32 Y = 0; Y < H; ++Y)
		{
			for (int32 X = 0; X < W; ++X)
			{
				const double DX = (X1 + X) - Local.X;
				const double DY = (Y1 + Y) - Local.Y;
				const double Dist = FMath::Sqrt(DX * DX + DY * DY);
				if (Dist > VertRadius) { continue; }

				// 1 across the solid core, easing to 0 at the rim. Falloff==0 gives a hard edge.
				double Alpha = 1.0;
				const double Inner = VertRadius * (1.0 - Falloff);
				if (Dist > Inner && VertRadius > Inner)
				{
					const double T = (Dist - Inner) / (VertRadius - Inner);
					Alpha = FMath::SmoothStep(0.0, 1.0, 1.0 - T);
				}

				const int32 Idx = Y * W + X;
				const double Current = HeightToWorld(Data[Idx], ActorScale.Z);
				double Result = Current;

				if (Mode == TEXT("raise"))        { Result = Current + Amount * Alpha; }
				else if (Mode == TEXT("lower"))   { Result = Current - Amount * Alpha; }
				else if (Mode == TEXT("flatten")) { Result = FMath::Lerp(Current, TargetOffset, Alpha); }
				else if (Mode == TEXT("smooth"))
				{
					// 4-neighbour average, clamped to the block we read so we never index outside it.
					const int32 XM = FMath::Max(X - 1, 0), XP = FMath::Min(X + 1, W - 1);
					const int32 YM = FMath::Max(Y - 1, 0), YP = FMath::Min(Y + 1, H - 1);
					const double Avg = 0.25 * (
						HeightToWorld(Data[Y * W + XM], ActorScale.Z) +
						HeightToWorld(Data[Y * W + XP], ActorScale.Z) +
						HeightToWorld(Data[YM * W + X], ActorScale.Z) +
						HeightToWorld(Data[YP * W + X], ActorScale.Z));
					Result = FMath::Lerp(Current, Avg, Alpha);
				}
				// Mode is validated BEFORE this loop now (see the pre-flight check above); this branch
				// remains as a belt-and-braces assertion and can only be reached if the allowlist and
				// this dispatch drift apart.
				else
				{
					Fail(Out, FString::Printf(TEXT("unknown mode '%s' — use raise/lower/flatten/smooth"), *Mode));
					return;
				}

				Data[Idx] = WorldToHeight(Result, ActorScale.Z);
				++Touched;
			}
		}

		// Nothing above this line has written anything - the samples were read and the new heights
		// computed into a local array, so refusing here still means NOTHING was changed.
		if (RefuseIfEditLayers(Landscape, Out)) { return; }

		Edit.SetHeightData(X1, Y1, X2, Y2, Data.GetData(), 0, /*InCalcNormals*/ true);
		Edit.Flush();
		// Collision is cooked from the heightfield SEPARATELY. Marking render state dirty updates what
		// you see; it does not touch what you walk on. Without UpdateCollisionData the visual surface
		// moves and every trace still hits the old one — terrain that renders as hills but reports
		// dead flat, which sends anything placed by tracing to the wrong height.
		for (ULandscapeComponent* Comp : Landscape->LandscapeComponents)
		{
			if (!Comp) { continue; }
			Comp->UpdateCachedBounds();
			Comp->MarkRenderStateDirty();
		}
		// ULandscapeComponent::UpdateCollisionData is declared without LANDSCAPE_API, so it cannot be
		// called from another module. RecreateCollisionComponents() is the exported equivalent — it
		// rebuilds the heightfield collision wholesale, which is heavier but is the only supported way
		// to keep the walkable surface in step with the visible one from outside the Landscape module.
		Landscape->RecreateCollisionComponents();
		Landscape->PostEditChange();

		Out->SetNumberField(TEXT("verticesTouched"), Touched);
		Out->SetStringField(TEXT("mode"), Mode);
		TSharedRef<FJsonObject> Area = MakeShared<FJsonObject>();
		Area->SetNumberField(TEXT("minX"), X1); Area->SetNumberField(TEXT("minY"), Y1);
		Area->SetNumberField(TEXT("maxX"), X2); Area->SetNumberField(TEXT("maxY"), Y2);
		Out->SetObjectField(TEXT("area"), Area);
		if (Touched == 0)
		{
			Out->SetStringField(TEXT("warning"),
				TEXT("no vertices were inside the brush — radius is probably smaller than one quad (one quad = the landscape's XY scale in world units)"));
		}
	}

	// --- paint_landscape ----------------------------------------------------
	//   in:  { landscape?, layerInfo, center:{x,y}, radius, weight? (0..1), falloff? }
	//   out: { verticesTouched, layer }
	//
	// This is what makes a road corridor read as dirt while the verge stays grass. Weights are
	// normalised across layers by SetAlphaData's weight-adjust, so painting one layer up implicitly
	// pushes the others down — which is what you want and is why there is no "erase" mode.
	void H_paint_landscape(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("landscape"), TEXT("actorPath"), TEXT("layerInfo"), TEXT("layer"), TEXT("info"),
			  TEXT("center"), TEXT("radius"), TEXT("weight"), TEXT("falloff") },
			TEXT("landscape (alias: actorPath; omit when there is only one), ")
			TEXT("layerInfo (aliases: layer, info) - a LandscapeLayerInfoObject ASSET PATH, ")
			TEXT("center {x,y} in WORLD units, radius (world units), weight (0..1), ")
			TEXT("falloff (0..1 of the radius that is feathered)"),
			{ { TEXT("layerName"), TEXT("pass the LandscapeLayerInfoObject asset path as layerInfo - landscape_info lists the legal ones") },
			  { TEXT("strength"), TEXT("use weight (0..1)") },
			  { TEXT("alpha"), TEXT("use weight (0..1)") },
			  { TEXT("brushSize"), TEXT("use radius (world units)") },
			  { TEXT("erase"), TEXT("there is no erase mode - weights normalise across layers, so paint a DIFFERENT layer up to push this one down") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		ALandscape* Landscape = FindLandscape(World, JStrAny(In, { TEXT("landscape"), TEXT("actorPath") }));
		if (!Landscape) { Fail(Out, TEXT("no landscape found — call create_landscape first")); return; }
		ULandscapeInfo* Info = Landscape->GetLandscapeInfo();
		if (!Info) { Fail(Out, TEXT("landscape has no ULandscapeInfo")); return; }

		const FString InfoPath = JStrAny(In, { TEXT("layerInfo"), TEXT("layer"), TEXT("info") });
		ULandscapeLayerInfoObject* LayerInfo = LoadLayerInfo(InfoPath);
		if (!LayerInfo)
		{
			Fail(Out, FString::Printf(
				TEXT("could not load LandscapeLayerInfoObject '%s' — it must be one of the layers this landscape's material declares"),
				*InfoPath));
			return;
		}

		// The message above PROMISED this check and nothing performed it: LoadLayerInfo will happily
		// LoadObject any ULandscapeLayerInfoObject from any path. Painting an unregistered layer does
		// NOT no-op — FLandscapeEditDataInterface::SetAlphaData takes the UpdateLayerIdx == INDEX_NONE
		// branch (LandscapeEditInterface.cpp:2797) and ALLOCATES A NEW WEIGHTMAP CHANNEL, and because
		// bWeightAdjust normalises across layers it pushes the real layers' weights down. A later
		// FixupWeightmaps deletes the allocation with a MapCheck warning (LandscapeEdit.cpp:929-931),
		// so the paint appears, dims the layers you WERE using, and then vanishes — all under
		// ok:true, verticesTouched:N. Same shape as the RVT postmortem: the endpoint succeeded and
		// broke something the caller was not looking at.
		if (Info->GetLayerInfoIndex(LayerInfo) == INDEX_NONE)
		{
			TArray<FString> Known;
			for (const FLandscapeInfoLayerSettings& L : Info->Layers)
			{
				if (L.LayerInfoObj) { Known.Add(L.LayerInfoObj->GetPathName()); }
				else if (L.LayerName != NAME_None) { Known.Add(FString::Printf(TEXT("%s (no LayerInfo asset assigned)"), *L.LayerName.ToString())); }
			}
			Fail(Out, FString::Printf(
				TEXT("layer '%s' is not one of this landscape's layers, so painting it would allocate a stray weightmap ")
				TEXT("channel, dim the layers that ARE in use, and then be garbage-collected by the next weightmap fixup. ")
				TEXT("This landscape declares: %s. (landscape_info lists them.)"),
				*InfoPath,
				Known.Num() ? *FString::Join(Known, TEXT(", ")) : TEXT("<none — assign layers on the landscape material first>")));
			return;
		}

		const TSharedPtr<FJsonObject>* CenterObj = nullptr;
		if (!In->TryGetObjectField(TEXT("center"), CenterObj) || !CenterObj)
		{
			Fail(Out, TEXT("center:{x,y} is required (world units)")); return;
		}
		const TSharedRef<FJsonObject> C = CenterObj->ToSharedRef();
		const FVector CenterWorld(JNum(C, TEXT("x")), JNum(C, TEXT("y")), JNum(C, TEXT("z")));

		const double RadiusWorld = JNum(In, TEXT("radius"), 1000.0);
		if (RadiusWorld <= 0.0) { Fail(Out, TEXT("radius must be > 0")); return; }
		const double Weight = FMath::Clamp(JNum(In, TEXT("weight"), 1.0), 0.0, 1.0);
		const double Falloff = FMath::Clamp(JNum(In, TEXT("falloff"), 0.5), 0.0, 1.0);

		const FTransform ToWorld = Landscape->LandscapeActorToWorld();
		const FVector Local = ToWorld.InverseTransformPosition(CenterWorld);
		const FVector ActorScale = Landscape->GetActorScale3D();
		const double VertRadius = RadiusWorld / FMath::Max(FMath::Min(ActorScale.X, ActorScale.Y), KINDA_SMALL_NUMBER);

		int32 MinX, MinY, MaxX, MaxY;
		if (!LandscapeExtent(Landscape, MinX, MinY, MaxX, MaxY))
		{
			Fail(Out, TEXT("could not read landscape extent")); return;
		}
		const int32 X1 = FMath::Clamp(FMath::FloorToInt(Local.X - VertRadius), MinX, MaxX);
		const int32 X2 = FMath::Clamp(FMath::CeilToInt (Local.X + VertRadius), MinX, MaxX);
		const int32 Y1 = FMath::Clamp(FMath::FloorToInt(Local.Y - VertRadius), MinY, MaxY);
		const int32 Y2 = FMath::Clamp(FMath::CeilToInt (Local.Y + VertRadius), MinY, MaxY);
		if (X2 < X1 || Y2 < Y1) { Fail(Out, TEXT("brush falls entirely outside the landscape")); return; }

		const int32 W = X2 - X1 + 1;
		const int32 H = Y2 - Y1 + 1;

		FLandscapeEditDataInterface Edit(Info);
		TArray<uint8> Data;
		Data.SetNumUninitialized(W * H);
		Edit.GetWeightDataFast(LayerInfo, X1, Y1, X2, Y2, Data.GetData(), 0);

		int32 Touched = 0;
		for (int32 Y = 0; Y < H; ++Y)
		{
			for (int32 X = 0; X < W; ++X)
			{
				const double DX = (X1 + X) - Local.X;
				const double DY = (Y1 + Y) - Local.Y;
				const double Dist = FMath::Sqrt(DX * DX + DY * DY);
				if (Dist > VertRadius) { continue; }

				double Alpha = 1.0;
				const double Inner = VertRadius * (1.0 - Falloff);
				if (Dist > Inner && VertRadius > Inner)
				{
					const double T = (Dist - Inner) / (VertRadius - Inner);
					Alpha = FMath::SmoothStep(0.0, 1.0, 1.0 - T);
				}

				const int32 Idx = Y * W + X;
				const double Current = (double)Data[Idx] / 255.0;
				const double Result = FMath::Lerp(Current, Weight, Alpha);
				Data[Idx] = (uint8)FMath::Clamp(FMath::RoundToInt(Result * 255.0), 0, 255);
				++Touched;
			}
		}

		Edit.SetAlphaData(LayerInfo, X1, Y1, X2, Y2, Data.GetData(), 0);
		Edit.Flush();
		Landscape->PostEditChange();

		Out->SetNumberField(TEXT("verticesTouched"), Touched);
		Out->SetStringField(TEXT("layer"), MifLayerInfoName(LayerInfo).ToString());
		Out->SetStringField(TEXT("layerInfo"), LayerInfo->GetPathName());
	}

	// --- bind_landscape_rvt -------------------------------------------------
	//   in:  { landscape?, runtimeVirtualTextures:[assetPath,...], createVolumes? (default true) }
	//   out: { bound[], volumesCreated[], alreadyPresent[] }
	//
	// A landscape material that writes/reads a runtime virtual texture needs TWO things wired up, and
	// missing either one renders the terrain black:
	//   1. the RVT listed in the landscape's RuntimeVirtualTextures array (what to draw into), and
	//   2. an ARuntimeVirtualTextureVolume in the level bounding the region (where it applies).
	// The editor exposes (2) as a "Create Volumes" button in the landscape details panel, which is
	// pure UI — ALandscapeProxy::bSetCreateRuntimeVirtualTextureVolumes is a transient placeholder
	// with no engine-side behaviour, so setting it does nothing. This mirrors what that button runs
	// (FLandscapeProxyUIDetails::CreateRuntimeVirtualTextureVolume).
	void H_bind_landscape_rvt(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("landscape"), TEXT("actorPath"), TEXT("runtimeVirtualTextures"), TEXT("createVolumes") },
			TEXT("landscape (alias: actorPath; omit when there is only one), ")
			TEXT("runtimeVirtualTextures [assetPath,...], createVolumes (bool, default true)"),
			{ { TEXT("runtimeVirtualTexture"), TEXT("the key is PLURAL and takes an array - runtimeVirtualTextures:[assetPath], even for one") },
			  { TEXT("rvt"), TEXT("use runtimeVirtualTextures:[assetPath,...]") },
			  { TEXT("createVolume"), TEXT("the key is PLURAL - createVolumes (bool)") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		ALandscape* Landscape = FindLandscape(World, JStrAny(In, { TEXT("landscape"), TEXT("actorPath") }));
		if (!Landscape) { Fail(Out, TEXT("no landscape found — call create_landscape first")); return; }

		const TArray<TSharedPtr<FJsonValue>>* Paths = nullptr;
		if (!JArray(In, TEXT("runtimeVirtualTextures"), Paths) || !Paths || Paths->Num() == 0)
		{
			Fail(Out, TEXT("runtimeVirtualTextures:[assetPath,...] is required"));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Bound;
		TArray<URuntimeVirtualTexture*> Wanted;
		for (const TSharedPtr<FJsonValue>& Val : *Paths)
		{
			FString Path;
			if (!Val.IsValid() || !Val->TryGetString(Path) || Path.IsEmpty()) { continue; }
			URuntimeVirtualTexture* RVT = LoadObject<URuntimeVirtualTexture>(nullptr, *Path);
			if (!RVT)
			{
				Fail(Out, FString::Printf(TEXT("could not load RuntimeVirtualTexture '%s'"), *Path));
				return;
			}
			Wanted.AddUnique(RVT);
			Bound.Add(MakeShared<FJsonValueString>(RVT->GetPathName()));
		}
		if (Wanted.Num() == 0) { Fail(Out, TEXT("no valid RuntimeVirtualTexture paths given")); return; }

		Landscape->Modify();
		for (URuntimeVirtualTexture* RVT : Wanted)
		{
			Landscape->RuntimeVirtualTextures.AddUnique(RVT);
		}
		Landscape->PostEditChange();

		TArray<TSharedPtr<FJsonValue>> Created, Existing;
		if (JBool(In, TEXT("createVolumes"), true))
		{
			// One volume per RVT. Reuse an existing volume already pointing at the same RVT rather
			// than stacking duplicates — two volumes for one RVT fight over the same pages.
			for (URuntimeVirtualTexture* RVT : Wanted)
			{
				bool bFound = false;
				for (TActorIterator<ARuntimeVirtualTextureVolume> It(World); It; ++It)
				{
					ARuntimeVirtualTextureVolume* Vol = *It;
					if (Vol && Vol->VirtualTextureComponent &&
						Vol->VirtualTextureComponent->GetVirtualTexture() == RVT)
					{
						bFound = true;
						Existing.Add(MakeShared<FJsonValueString>(Vol->GetPathName()));
						break;
					}
				}
				if (bFound) { continue; }

				ARuntimeVirtualTextureVolume* NewVolume = World->SpawnActor<ARuntimeVirtualTextureVolume>();
				if (!NewVolume || !NewVolume->VirtualTextureComponent) { continue; }
				NewVolume->VirtualTextureComponent->SetVirtualTexture(RVT);
				// Align to the landscape, THEN fit — SetBounds reads the align actor, so the order
				// matters and reversing it produces a volume covering nothing.
				NewVolume->VirtualTextureComponent->SetBoundsAlignActor(Landscape);
				RuntimeVirtualTexture::SetBounds(NewVolume->VirtualTextureComponent);
				NewVolume->SetActorLabel(FString::Printf(TEXT("RVTVolume_%s"), *RVT->GetName()));
				Created.Add(MakeShared<FJsonValueString>(NewVolume->GetPathName()));
			}
		}

		Out->SetArrayField(TEXT("bound"), Bound);
		Out->SetArrayField(TEXT("volumesCreated"), Created);
		Out->SetArrayField(TEXT("alreadyPresent"), Existing);
		Out->SetStringField(TEXT("landscape"), Landscape->GetPathName());
		Out->SetStringField(TEXT("note"),
			TEXT("verify with landscape_info — runtimeVirtualTextures must be non-empty AND a volume must exist per RVT"));
		Out->SetStringField(TEXT("warning"),
			TEXT("an RVT is a SCENE-WIDE contract: binding one that has no valid pages does not fix a black terrain, "
				 "and it turns every other material that samples that RVT (buildings, roads) blown-out white. "
				 "In a scratch level prefer a landscape material that does not sample an RVT."));
	}

	// --- landscape_info -----------------------------------------------------
	//   out: { landscapes:[{ actorPath, label, vertsX, vertsY, worldMin, worldMax, scale,
	//                        material, layers[], components }] }
	// Read-only. Exists so the sculpt/paint calls can be aimed: every one of their world-space
	// arguments only makes sense against the bounds reported here.
	void H_landscape_info(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {}, TEXT("(none - this endpoint takes no parameters)"),
			{ { TEXT("landscape"), TEXT("not supported - this endpoint always reports EVERY landscape in the editor world; filter the landscapes[] array by actorPath or label") },
			  { TEXT("limit"), TEXT("not supported - every landscape is reported") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (TActorIterator<ALandscape> It(World); It; ++It)
		{
			ALandscape* L = *It;
			if (!L || !IsValid(L)) { continue; }

			TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetStringField(TEXT("actorPath"), L->GetPathName());
			O->SetStringField(TEXT("label"), L->GetActorLabel());

			const FVector S = L->GetActorScale3D();
			TSharedRef<FJsonObject> Scale = MakeShared<FJsonObject>();
			Scale->SetNumberField(TEXT("x"), S.X); Scale->SetNumberField(TEXT("y"), S.Y); Scale->SetNumberField(TEXT("z"), S.Z);
			O->SetObjectField(TEXT("scale"), Scale);

			int32 MinX, MinY, MaxX, MaxY;
			if (LandscapeExtent(L, MinX, MinY, MaxX, MaxY))
			{
				O->SetNumberField(TEXT("vertsX"), MaxX - MinX + 1);
				O->SetNumberField(TEXT("vertsY"), MaxY - MinY + 1);

				const FTransform ToWorld = L->LandscapeActorToWorld();
				const FVector WMin = ToWorld.TransformPosition(FVector(MinX, MinY, 0));
				const FVector WMax = ToWorld.TransformPosition(FVector(MaxX, MaxY, 0));
				TSharedRef<FJsonObject> Mn = MakeShared<FJsonObject>();
				Mn->SetNumberField(TEXT("x"), WMin.X); Mn->SetNumberField(TEXT("y"), WMin.Y); Mn->SetNumberField(TEXT("z"), WMin.Z);
				TSharedRef<FJsonObject> Mx = MakeShared<FJsonObject>();
				Mx->SetNumberField(TEXT("x"), WMax.X); Mx->SetNumberField(TEXT("y"), WMax.Y); Mx->SetNumberField(TEXT("z"), WMax.Z);
				O->SetObjectField(TEXT("worldMin"), Mn);
				O->SetObjectField(TEXT("worldMax"), Mx);
			}

			// COUNT THE STREAMING PROXIES TOO, or a World Partition landscape reports zero.
			//
			// ALandscape derives from ALandscapeProxy, and under World Partition the terrain's
			// components live on ALandscapeStreamingProxy actors rather than on the parent - so
			// L->LandscapeComponents is genuinely EMPTY for the parent of a partitioned landscape.
			// Reporting that alone said `components: 0` for a 2017x2017 terrain: true, and it reads as
			// a broken landscape. diagnose_landscape iterates ALandscapeProxy and saw 896 components
			// in the same world where this endpoint reported ~640 across 11 actors, and neither
			// response said which question it had answered. Filed as issue 11.
			//
			// Matched on LandscapeGuid, which every proxy of one landscape shares
			// (LandscapeProxy.h:936), so a world holding several landscapes attributes each proxy to
			// the right parent instead of summing them all onto the first.
			const int32 OwnComponents = L->LandscapeComponents.Num();
			int32 ProxyComponents = 0;
			int32 ProxyCount = 0;
			// Counted in the same pass, because componentsWithoutWeightmap below has to answer about
			// the same set of components as `components` does or the two disagree in a new way.
			int32 ProxyNoWeightmap = 0;
			{
				const FGuid ThisGuid = L->GetLandscapeGuid();
				for (TActorIterator<ALandscapeProxy> PIt(World); PIt; ++PIt)
				{
					ALandscapeProxy* P = *PIt;
					if (!P || !IsValid(P) || P == L) { continue; }
					if (!ThisGuid.IsValid() || P->GetLandscapeGuid() != ThisGuid) { continue; }
					++ProxyCount;
					ProxyComponents += P->LandscapeComponents.Num();
					for (ULandscapeComponent* PComp : P->LandscapeComponents)
					{
						if (PComp && PComp->GetWeightmapTextures().Num() == 0) { ++ProxyNoWeightmap; }
					}
				}
			}

			O->SetNumberField(TEXT("components"), OwnComponents);
			O->SetNumberField(TEXT("proxyCount"), ProxyCount);
			O->SetNumberField(TEXT("proxyComponents"), ProxyComponents);
			O->SetNumberField(TEXT("totalComponents"), OwnComponents + ProxyComponents);
			// Say which question was answered, rather than leaving two plausible numbers side by side.
			O->SetStringField(TEXT("componentScope"), ProxyCount > 0
				? TEXT("partitioned - `components` is this actor's own; `totalComponents` includes its streaming proxies")
				: TEXT("all components belong to this actor; there are no streaming proxies"));
			if (OwnComponents == 0 && ProxyComponents > 0)
			{
				O->SetStringField(TEXT("componentsNote"), FString::Printf(
					TEXT("components:0 is correct and does NOT mean the landscape is broken - this is a World Partition ")
					TEXT("landscape and all %d of its components live on %d streaming proxy actor(s). Use totalComponents."),
					ProxyComponents, ProxyCount));
			}
			O->SetStringField(TEXT("material"),
				L->LandscapeMaterial ? L->LandscapeMaterial->GetPathName() : TEXT(""));

			// The layer names the MATERIAL declares, as opposed to the ones actually painted below.
			// Painting a layer the material does not declare succeeds and changes nothing visible —
			// comparing these two lists is the only way to catch that.
			TArray<TSharedPtr<FJsonValue>> MatLayers;
			for (const FName& LayerName : L->GetLayersFromMaterial())
			{
				MatLayers.Add(MakeShared<FJsonValueString>(LayerName.ToString()));
			}
			O->SetArrayField(TEXT("materialLayers"), MatLayers);

			// A landscape material that samples a runtime virtual texture renders its base colour
			// BLACK when nothing is bound here — leaving only whatever the material draws outside the
			// RVT path (detail meshes, grass), which reads as speckle over a black surface.
			TArray<TSharedPtr<FJsonValue>> RVTs;
			for (const TObjectPtr<URuntimeVirtualTexture>& RVT : L->RuntimeVirtualTextures)
			{
				if (RVT) { RVTs.Add(MakeShared<FJsonValueString>(RVT->GetPathName())); }
			}
			O->SetArrayField(TEXT("runtimeVirtualTextures"), RVTs);

			// Components carrying no weightmap at all. Non-zero means the painted-layer data never
			// landed, so every blend weight in the material is zero.
			int32 NoWeightmap = 0;
			for (ULandscapeComponent* Comp : L->LandscapeComponents)
			{
				if (Comp && Comp->GetWeightmapTextures().Num() == 0) { ++NoWeightmap; }
			}
			O->SetNumberField(TEXT("componentsWithoutWeightmap"), NoWeightmap);
			// Same scope split as `components` above. A ratio out of nothing is not a diagnosis, so
			// when the parent owns no components say so instead of letting a bare 0 read as "healthy".
			O->SetNumberField(TEXT("proxyComponentsWithoutWeightmap"), ProxyNoWeightmap);
			O->SetNumberField(TEXT("totalComponentsWithoutWeightmap"), NoWeightmap + ProxyNoWeightmap);
			if (OwnComponents == 0)
			{
				O->SetStringField(TEXT("componentsWithoutWeightmapNote"), ProxyComponents > 0
					? TEXT("componentsWithoutWeightmap:0 is out of ZERO components and means nothing here - ")
					  TEXT("read totalComponentsWithoutWeightmap, which covers the streaming proxies.")
					: TEXT("componentsWithoutWeightmap:0 is out of ZERO components and means nothing - ")
					  TEXT("this landscape actor has no components at all."));
			}

			TArray<TSharedPtr<FJsonValue>> Layers;
			if (ULandscapeInfo* Info = L->GetLandscapeInfo())
			{
				for (const FLandscapeInfoLayerSettings& Setting : Info->Layers)
				{
					TSharedRef<FJsonObject> LO = MakeShared<FJsonObject>();
					LO->SetStringField(TEXT("name"), Setting.GetLayerName().ToString());
					LO->SetStringField(TEXT("layerInfo"),
						Setting.LayerInfoObj ? Setting.LayerInfoObj->GetPathName() : TEXT(""));
					Layers.Add(MakeShared<FJsonValueObject>(LO));
				}
			}
			O->SetArrayField(TEXT("layers"), Layers);

			// SCULPT EDIT LAYERS - a DIFFERENT stack from `layers` above, and the confusion
			// between them was a real hole. `layers` is FLandscapeInfoLayerSettings: PAINT
			// layers, the weightmap ones. `materialLayers` is the material's. Neither is the
			// Landscape Edit Layers panel, and until now NOTHING reported that panel - while
			// apply_spline_to_landscape and import_landscape_heightmap both refuse on a
			// landscape that has edit layers with "Pass editLayer naming one that exists".
			// A caller was told to name something no endpoint could enumerate, so their only
			// options were to guess or to open the editor UI. This is that missing read.
			TArray<TSharedPtr<FJsonValue>> EditLayers;
			for (const FMifEditLayer& EL : ReadEditLayers(L))
			{
				TSharedRef<FJsonObject> EO = MakeShared<FJsonObject>();
				EO->SetStringField(TEXT("name"), EL.Name);
				EO->SetStringField(TEXT("guid"), EL.Guid);
				EO->SetBoolField(TEXT("visible"), EL.bVisible);
				EO->SetBoolField(TEXT("locked"), EL.bLocked);
				EditLayers.Add(MakeShared<FJsonValueObject>(EO));
			}
			O->SetArrayField(TEXT("editLayers"), EditLayers);
			// apply_spline_to_landscape refuses when this is non-empty and no editLayer was
			// named, so say which field to read rather than leaving the caller to infer it.
			// It is the ONLY endpoint taking editLayer - checked, rather than assumed from the
			// fact that import_landscape_heightmap also writes heights.
			O->SetStringField(TEXT("editLayersNote"), EditLayers.Num() > 0
				? TEXT("this landscape HAS sculpt edit layers, so apply_spline_to_landscape needs ")
				  TEXT("editLayer set to one of the names in editLayers[] - these are NOT the same ")
				  TEXT("as `layers` (paint/weightmap) or `materialLayers` (the material's)")
				: TEXT("no sculpt edit layers - apply_spline_to_landscape works without an ")
				  TEXT("editLayer here. `layers` above is the unrelated paint/weightmap list."));

			Arr.Add(MakeShared<FJsonValueObject>(O));
		}

		Out->SetArrayField(TEXT("landscapes"), Arr);
		Out->SetNumberField(TEXT("count"), Arr.Num());
		if (Arr.Num() == 0)
		{
			Out->SetStringField(TEXT("note"), TEXT("no ALandscape in the editor world — call create_landscape"));
		}
	}

	// --- apply_spline_to_landscape ------------------------------------------
	//   in:  { landscape?, splineActor, component?, startWidth, endWidth, startSideFalloff,
	//          endSideFalloff, startRoll, endRoll, subdivisions, raiseHeights, lowerHeights,
	//          paintLayer?, editLayer? }
	//   out: { landscape, spline, splineLength, verticesChanged, ... }
	//
	// WHY IT IS WORTH AN ENDPOINT. sculpt_landscape and paint_landscape are CIRCULAR BRUSHES, so
	// cutting a 400 m road today is dozens to hundreds of round trips whose overlapping circles never
	// produce a clean corridor with consistent width, falloff or banking. EditorApplySpline is the
	// engine's own road/riverbed operation and it does the whole run in one call.
	//
	// TWO GUARDS, AND BOTH ARE MANDATORY RATHER THAN DEFENSIVE POLISH.
	//
	// 1. A HARD CRASH ON A COOKED LANDSCAPE. EditorApplySpline opens with
	//        if (ALandscape* Landscape = GetLandscapeInfo()->LandscapeActor.Get())
	//    - dereferencing GetLandscapeInfo() with NO null check, in 5.3.2 and 5.7 alike
	//    (LandscapeBlueprintSupport.cpp). A cooked landscape has no ULandscapeInfo, so this is not a
	//    no-op, it is a null dereference that takes the editor down. Checked before the call.
	//
	// 2. A SILENT NO-OP ON 5.7 THAT DOES NOT EXIST ON 5.3, and it hits the landscapes this very
	//    plugin creates. The guard inside EditorApplySpline changed:
	//
	//      5.3.2 / 5.6:  const FLandscapeLayer* Layer = Landscape->GetLayer(EditLayerName);
	//                    if (Landscape->HasLayersContent() && (Layer == nullptr)) return;
	//      5.7:          const ULandscapeEditLayerBase* EditLayer = Landscape->GetEditLayerConst(EditLayerName);
	//                    if (EditLayer == nullptr) return;          <-- UNCONDITIONAL
	//
	//    GetEditLayerConst returns null whenever the layer is not found, which on a landscape with
	//    NO edit layers is always. So on 5.7 EditorApplySpline returns early on every non-layered
	//    landscape - logging an engine Error and returning void, so the caller sees nothing. And
	//    create_landscape in this very file deliberately toggles bCanHaveLayersContent OFF, which
	//    means every landscape MifBridge makes would hit it.
	//
	//    There is no bypass: LandscapeSplineRaster.h is a Private header in both trees, so
	//    Pointify/RasterizeSegmentPoints cannot be called directly. So this is a REFUSAL with the
	//    reason, not a workaround.
	//
	// AND THE POSTCONDITION IS MEASURED. EditorApplySpline returns void, so heights are sampled
	// through FLandscapeEditDataInterface before and after - the same interface sculpt_landscape
	// uses - and the response reports how many actually moved. "It ran" and "it changed the terrain"
	// are different claims and only the second is worth having.
	void H_apply_spline_to_landscape(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("landscape"), TEXT("actorPath"), TEXT("splineActor"), TEXT("spline"),
			  TEXT("component"), TEXT("startWidth"), TEXT("endWidth"), TEXT("startSideFalloff"),
			  TEXT("endSideFalloff"), TEXT("startRoll"), TEXT("endRoll"), TEXT("subdivisions"),
			  TEXT("raiseHeights"), TEXT("lowerHeights"), TEXT("paintLayer"), TEXT("editLayer") },
			TEXT("splineActor (alias: spline) - an actor with a USplineComponent; landscape (alias: ")
			TEXT("actorPath, omit when the level has one); component - which spline component if the ")
			TEXT("actor has several; startWidth/endWidth (default 200uu); startSideFalloff/")
			TEXT("endSideFalloff (default 200uu); startRoll/endRoll (degrees, default 0); ")
			TEXT("subdivisions (default 20); raiseHeights/lowerHeights (default true); paintLayer - ")
			TEXT("a LandscapeLayerInfoObject path; editLayer - REQUIRED on a landscape with edit ")
			TEXT("layers"),
			{ { TEXT("width"), TEXT("spell it startWidth and endWidth - a spline can taper") },
			  { TEXT("falloff"), TEXT("spell it startSideFalloff and endSideFalloff") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }
		// FindLandscape, the same resolver paint_landscape and sculpt_landscape use - it accepts a
		// name or path and falls back to the single landscape in the level when omitted.
		ALandscape* Landscape = FindLandscape(World, JStrAny(In, { TEXT("landscape"), TEXT("actorPath") }));
		if (!Landscape)
		{
			Fail(Out, TEXT("no landscape found - name one with landscape/actorPath, or call "
				TEXT("create_landscape first. NOTHING was changed.")));
			return;
		}

		// GUARD 1 - the crash. EditorApplySpline dereferences GetLandscapeInfo() unchecked.
		ULandscapeInfo* Info = Landscape->GetLandscapeInfo();
		if (!Info)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no ULandscapeInfo, which is what a COOKED landscape looks like. ")
				TEXT("EditorApplySpline dereferences GetLandscapeInfo() with NO null check ")
				TEXT("(LandscapeBlueprintSupport.cpp), so calling it here would CRASH the editor ")
				TEXT("rather than fail. Refused before the engine was touched. diagnose_landscape is ")
				TEXT("the read-only route for cooked terrain. NOTHING was changed."),
				*Landscape->GetActorLabel()));
			return;
		}

		// The spline.
		UEditorActorSubsystem* ActorSys = GEditor ? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
		if (!ActorSys)
		{
			Fail(Out, TEXT("no EditorActorSubsystem."));
			return;
		}
		TSharedRef<FJsonObject> SplineIn = MakeShared<FJsonObject>();
		SplineIn->SetStringField(TEXT("actorPath"),
			JStrAny(In, { TEXT("splineActor"), TEXT("spline") }));
		AActor* SplineActor = ResolveActor(ActorSys, SplineIn, Out);
		if (!SplineActor) { return; }

		TArray<USplineComponent*> Splines;
		SplineActor->GetComponents(Splines);
		if (Splines.Num() == 0)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no USplineComponent. NOTHING was changed."),
				*SplineActor->GetActorLabel()));
			return;
		}
		USplineComponent* Spline = Splines[0];
		const FString WantComp = JStr(In, TEXT("component"));
		if (!WantComp.IsEmpty())
		{
			Spline = nullptr;
			for (USplineComponent* S : Splines)
			{
				if (S && S->GetName() == WantComp) { Spline = S; break; }
			}
			if (!Spline)
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' has no spline component named '%s' (it has %d). NOTHING was changed."),
					*SplineActor->GetActorLabel(), *WantComp, Splines.Num()));
				return;
			}
		}
		if (Spline->GetNumberOfSplinePoints() < 2)
		{
			Fail(Out, FString::Printf(
				TEXT("this spline has %d point(s) - a corridor needs at least 2. NOTHING was ")
				TEXT("changed."), Spline->GetNumberOfSplinePoints()));
			return;
		}

		// GUARD 2 - the 5.7 silent no-op.
		const FName EditLayer(*JStr(In, TEXT("editLayer")));
		const bool bHasLayers = Landscape->HasLayersContent();
#if MIF_ENGINE_AT_LEAST(5, 7)
		if (Landscape->GetEditLayerConst(EditLayer) == nullptr)
		{
			Fail(Out, FString::Printf(
				TEXT("on UE 5.7 EditorApplySpline returns immediately unless the named edit layer ")
				TEXT("RESOLVES - `if (EditLayer == nullptr) return;`, unconditionally, where 5.3 only ")
				TEXT("did that when the landscape actually had edit layers. '%s' %s, so this call ")
				TEXT("would log an engine error and change NOTHING while reporting success. %s ")
				TEXT("NOTHING was changed."),
				*Landscape->GetActorLabel(),
				bHasLayers ? TEXT("has edit layers but no layer by that name")
				           : TEXT("has NO edit layers at all"),
				bHasLayers ? TEXT("Pass editLayer naming one from landscape_info's editLayers[] "
								  "for this landscape - NOT its `layers`, which are the unrelated "
								  "paint/weightmap layers.")
				           : TEXT("Enable edit layers on it first - note create_landscape "
								  "deliberately turns them OFF, so a landscape this bridge made "
								  "always needs that step on 5.7.")));
			return;
		}
#else
		if (bHasLayers && Landscape->GetLayer(EditLayer) == nullptr)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has edit layers, and EditorApplySpline refuses one it cannot name - it ")
				TEXT("would log an error and change nothing. Pass editLayer, naming one from ")
				TEXT("landscape_info's editLayers[] for this landscape (NOT its `layers`, which ")
				TEXT("are paint/weightmap layers). NOTHING was changed."),
				*Landscape->GetActorLabel()));
			return;
		}
#endif

		// A NON-LAYERED LANDSCAPE ON 5.3: WARNED, NOT REFUSED, AND THE HONEST REASON IS THAT I
		// COULD NOT MAKE IT WORK. Tested live 2026-08-30 against a freshly created landscape with
		// edit layers off (which is what create_landscape produces): every combination tried
		// returned verticesChanged 0 - splines spanning 6000uu of a 12600uu landscape, widths from
		// 800 to 2000, falloffs to 800, subdivisions to 40, spline Z at and below the terrain, and
		// overlap confirmed against the landscape's own reported worldMin/worldMax. The sampler is
		// not at fault: sculpt_landscape moved 736 vertices through the same
		// FLandscapeEditDataInterface in the same session.
		//
		// The likely cause is the FScopedSetLandscapeEditingLayer the engine opens with an INVALID
		// FGuid when there is no layer - and 5.7 tightening this exact path into an unconditional
		// refusal is consistent with the non-layered case never having been supported, only silent.
		// That is a hypothesis, so this WARNS rather than refuses: refusing on an unproven theory
		// would block a case that may work on someone else's landscape, and this endpoint's whole
		// contract is that verticesChanged tells you the truth either way.
		const bool bWarnNoLayers = !bHasLayers;

		// The paint layer, validated BEFORE anything is written. The engine's own comment on this
		// function says "The landscape must be configured with the same layer info in one of its
		// layers or this will do nothing" - the same silent no-op paint_landscape already closes.
		ULandscapeLayerInfoObject* PaintLayer = nullptr;
		const FString PaintLayerPath = JStr(In, TEXT("paintLayer"));
		if (!PaintLayerPath.IsEmpty())
		{
			PaintLayer = Cast<ULandscapeLayerInfoObject>(LoadAssetLenient(PaintLayerPath));
			if (!PaintLayer)
			{
				Fail(Out, FString::Printf(
					TEXT("paintLayer '%s' is not a LandscapeLayerInfoObject. NOTHING was changed."),
					*PaintLayerPath));
				return;
			}
			if (Info->GetLayerInfoIndex(PaintLayer) == INDEX_NONE)
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is not one of this landscape's layer infos. The engine's own note on ")
					TEXT("EditorApplySpline says it 'must be configured with the same layer info in ")
					TEXT("one of its layers or this will do nothing' - so this is refused rather ")
					TEXT("than reported as a successful paint that painted nothing. NOTHING was ")
					TEXT("changed."), *PaintLayerPath));
				return;
			}
		}

		const float StartWidth = static_cast<float>(JNum(In, TEXT("startWidth"), 200.0));
		const float EndWidth = static_cast<float>(JNum(In, TEXT("endWidth"), 200.0));
		const float StartFall = static_cast<float>(JNum(In, TEXT("startSideFalloff"), 200.0));
		const float EndFall = static_cast<float>(JNum(In, TEXT("endSideFalloff"), 200.0));
		const float StartRoll = static_cast<float>(JNum(In, TEXT("startRoll"), 0.0));
		const float EndRoll = static_cast<float>(JNum(In, TEXT("endRoll"), 0.0));
		const int32 Subdivisions = FMath::Clamp(JInt(In, TEXT("subdivisions"), 20), 1, 500);
		const bool bRaise = JBool(In, TEXT("raiseHeights"), true);
		const bool bLower = JBool(In, TEXT("lowerHeights"), true);
		if (!bRaise && !bLower && !PaintLayer)
		{
			Fail(Out, TEXT("raiseHeights and lowerHeights are both false and no paintLayer was ")
				TEXT("given, so there is nothing for this call to do. NOTHING was changed."));
			return;
		}

		// SAMPLE BEFORE. EditorApplySpline is void, so this is the only way to know it did anything.
		int32 X1 = 0, Y1 = 0, X2 = 0, Y2 = 0;
		const bool bHaveExtent = LandscapeExtent(Landscape, X1, Y1, X2, Y2);
		TArray<uint16> Before;
		if (bHaveExtent)
		{
			Before.SetNumZeroed((X2 - X1 + 1) * (Y2 - Y1 + 1));
			FLandscapeEditDataInterface Edit(Info);
			Edit.GetHeightDataFast(X1, Y1, X2, Y2, Before.GetData(), 0);
		}

		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_ApplySplineToLandscape",
												 "Apply Spline To Landscape"));
		Landscape->Modify();
		Landscape->EditorApplySpline(Spline, StartWidth, EndWidth, StartFall, EndFall,
									 StartRoll, EndRoll, Subdivisions, bRaise, bLower,
									 PaintLayer, EditLayer);

		// FLUSH THE EDIT LAYER COMPOSITE BEFORE MEASURING, or the measurement is a lie.
		//
		// EditorApplySpline rasterizes inside FScopedSetLandscapeEditingLayer, and that scope's
		// destructor only REQUESTS a content update - RequestLayersContentUpdate(Update_All). On a
		// landscape with edit layers the write lands in the layer and the composited heightmap is
		// rebuilt on a later tick, so sampling here read the PRE-deformation heights and reported
		// verticesChanged 0 for a deformation that had genuinely happened.
		//
		// Measured 2026-08-31 on a 2017x2017 partitioned landscape with two edit layers: the
		// endpoint returned 0, and a re-export one second later differed from the one taken before.
		// That is the worst shape of answer this endpoint can give - its whole reason for counting
		// vertices is that EditorApplySpline returns void, and a caller checking verticesChanged > 0
		// would conclude nothing happened while the terrain HAD moved under them.
		//
		// Called with NO ARGUMENT deliberately. 5.3 and 5.6 declare
		// ForceUpdateLayersContent(bool bIntermediateRender = false); 5.7 splits it into a plain
		// ForceUpdateLayersContent() plus a DEPRECATED (bool) overload. No-arg binds the default on
		// the old engines and the non-deprecated overload on 5.7 - passing an explicit false would
		// pick the deprecated one there.
		if (Landscape->HasLayersContent())
		{
			Landscape->ForceUpdateLayersContent();
		}

		int32 Changed = -1;
		if (bHaveExtent)
		{
			TArray<uint16> After;
			After.SetNumZeroed(Before.Num());
			FLandscapeEditDataInterface Edit(Info);
			Edit.GetHeightDataFast(X1, Y1, X2, Y2, After.GetData(), 0);
			Changed = 0;
			for (int32 i = 0; i < Before.Num() && i < After.Num(); ++i)
			{
				if (Before[i] != After[i]) { ++Changed; }
			}
		}

		Out->SetStringField(TEXT("landscape"), Landscape->GetPathName());
		Out->SetStringField(TEXT("spline"), Spline->GetPathName());
		Out->SetNumberField(TEXT("splinePoints"), Spline->GetNumberOfSplinePoints());
		Out->SetNumberField(TEXT("splineLength"), Spline->GetSplineLength());
		Out->SetNumberField(TEXT("startWidth"), StartWidth);
		Out->SetNumberField(TEXT("endWidth"), EndWidth);
		Out->SetNumberField(TEXT("subdivisions"), Subdivisions);
		Out->SetBoolField(TEXT("raiseHeights"), bRaise);
		Out->SetBoolField(TEXT("lowerHeights"), bLower);
		if (PaintLayer) { Out->SetStringField(TEXT("paintLayer"), PaintLayer->GetPathName()); }
		Out->SetNumberField(TEXT("verticesChanged"), Changed);

		Out->SetBoolField(TEXT("landscapeHasEditLayers"), bHasLayers);
		if (Changed == 0 && bWarnNoLayers)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("NOT ONE height sample changed, and this landscape has NO EDIT LAYERS - which "
					 "is the case that could not be made to work at all when this endpoint was "
					 "written. Tested live on 5.3.2 across widths 800-2000, falloffs to 800, "
					 "subdivisions to 40, spline Z at and below the terrain, and confirmed overlap: "
					 "always zero. The measurement is sound - sculpt_landscape moved 736 vertices "
					 "through the same interface in the same session. UE 5.7 turned this path into "
					 "an unconditional refusal, which suggests the non-layered case was never "
					 "supported, only silent. ENABLE EDIT LAYERS on the landscape and pass "
					 "editLayer. Note create_landscape deliberately turns them off."));
		}
		else if (Changed == 0 && (bRaise || bLower))
		{
			Out->SetStringField(TEXT("note"),
				TEXT("the call ran and NOT ONE height sample changed. Measured, not assumed - "
					 "EditorApplySpline returns void, so this is sampled through "
					 "FLandscapeEditDataInterface before and after. Usual causes: the spline does "
					 "not pass over this landscape, or its width is smaller than one heightmap "
					 "quad. This is reported rather than returned as a plain success."));
		}
		else if (Changed < 0)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("the landscape's extent could not be read, so no before/after height comparison "
					 "was possible and verticesChanged is -1 rather than a number nobody measured."));
		}
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the landscape is now dirty and NOTHING has been saved."));
	}

	// =======================================================================
	// import_landscape_heightmap / export_landscape_heightmap
	// =======================================================================
	//
	// WHY THESE EXIST, measured rather than asserted. sculpt_landscape costs ~435 ms per CALL and
	// the cost does not vary with brush size - 37 vertices and 40,363 vertices both took ~435 ms on
	// 5.7.4. So the cost of sculpting a shape is the number of CALLS, and a 1450x1450 coastline
	// rastered sensibly is ~23,000 of them: 2.7 hours. An adaptive quadtree got that to 1,647 calls
	// and 11.2 minutes, which is close to the floor for a brush and still eleven minutes per
	// attempt.
	//
	// And a disc cannot draw a coastline whatever it costs. flatten with falloff 0 makes vertical
	// walls, so every water body is a pit with sheer sides; stamping discs along a boundary leaves
	// scalloped crescents, because that is what a row of overlapping circles is. The geometry came
	// out CORRECT - a transect agreed with the source classifier on 47 of 49 samples - and the shape
	// quality was still, in Andre's words, "very poor, and unlike natural terrain". That is not a
	// parameter problem.
	//
	// NO VERSION SPLIT IS NEEDED, contrary to the request's own hint. It pointed at
	// ALandscapeProxy::Import and warned about its parameter list changing before 5.7.
	// FLandscapeEditDataInterface::GetHeightDataFast / SetHeightData are already used by
	// sculpt_landscape a few hundred lines above, take the same rect, and have not changed shape -
	// so an import is the sculpt write with the samples coming from a file instead of a brush.
	//
	// THE DEFAULT IS A STRAIGHT COPY, and that is the honest one. Landscape height is stored as
	// uint16 natively: 32768 is the actor's own Z and one unit is ActorScale.Z/128 world units. With
	// no minZ/maxZ the samples go through unchanged, so export->import round-trips exactly. minZ and
	// maxZ are for the other case - a normalised 0..65535 image being mapped onto a world Z range -
	// and they are required together, because half a mapping is not one.

	namespace
	{
		/** Decode 16-bit samples from a .r16 blob or a 16-bit greyscale PNG. */
		bool MifDecodeHeightBytes(const TArray<uint8>& Bytes, const FString& Ext,
								  int32 ExpectW, int32 ExpectH,
								  TArray<uint16>& Out, FString& OutErr)
		{
			if (Ext == TEXT("png"))
			{
				IImageWrapperModule& Mod =
					FModuleManager::LoadModuleChecked<IImageWrapperModule>(TEXT("ImageWrapper"));
				TSharedPtr<IImageWrapper> Wrapper = Mod.CreateImageWrapper(EImageFormat::PNG);
				if (!Wrapper.IsValid() || !Wrapper->SetCompressed(Bytes.GetData(), Bytes.Num()))
				{
					OutErr = TEXT("the file is not a PNG this engine can decode.");
					return false;
				}
				const int32 W = Wrapper->GetWidth();
				const int32 H = Wrapper->GetHeight();
				if (W != ExpectW || H != ExpectH)
				{
					OutErr = FString::Printf(
						TEXT("the PNG is %dx%d and the target region is %dx%d. It is refused rather "
							 "than stretched, because a resampled heightmap is a different terrain."),
						W, H, ExpectW, ExpectH);
					return false;
				}
				TArray64<uint8> Raw;
				// G16 is the 16-bit greyscale the landscape tool itself imports. An 8-bit PNG will
				// be widened by the wrapper, which is lossy in a way the caller should know about -
				// so it is reported below rather than accepted silently.
				if (!Wrapper->GetRaw(ERGBFormat::Gray, 16, Raw))
				{
					OutErr = TEXT("the PNG decoded but not as 16-bit greyscale. Export a G16 PNG, "
								  "or use a raw .r16.");
					return false;
				}
				if (Raw.Num() != int64(W) * H * 2)
				{
					OutErr = FString::Printf(TEXT("decoded %lld bytes for %dx%d 16-bit samples, "
												  "expected %lld."),
											 (long long)Raw.Num(), W, H, (long long)W * H * 2);
					return false;
				}
				Out.SetNumUninitialized(W * H);
				FMemory::Memcpy(Out.GetData(), Raw.GetData(), Raw.Num());
				return true;
			}

			// Raw .r16: little-endian uint16, row-major, no header. The landscape tool's own format.
			const int64 Want = int64(ExpectW) * ExpectH * 2;
			if (Bytes.Num() != Want)
			{
				OutErr = FString::Printf(
					TEXT("a raw .r16 for %dx%d must be exactly %lld bytes (little-endian uint16, "
						 "row-major, no header) and this file is %d. Refused rather than guessed - "
						 "a wrong stride silently shears the terrain."),
					ExpectW, ExpectH, (long long)Want, Bytes.Num());
				return false;
			}
			Out.SetNumUninitialized(ExpectW * ExpectH);
			FMemory::Memcpy(Out.GetData(), Bytes.GetData(), Want);
			return true;
		}
	}

	void H_import_landscape_heightmap(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("landscape"), TEXT("actorPath"), TEXT("file"), TEXT("data"),
			  TEXT("width"), TEXT("height"), TEXT("x0"), TEXT("y0"),
			  TEXT("minZ"), TEXT("maxZ") },
			TEXT("landscape (alias actorPath); file - a 16-bit greyscale PNG or raw .r16 - OR data, "
				 "base64 little-endian uint16; width/height REQUIRED with data; x0/y0 for a region "
				 "write (default: the landscape's own origin); minZ/maxZ to map 0..65535 onto a "
				 "world Z range (both or neither - default is a straight copy, since the native "
				 "storage is already uint16)"),
			{ { TEXT("layer"), TEXT("edit layers are not supported here. Writing to a named layer "
									"needs FScopedSetLandscapeEditingLayer around the edit, and "
									"without it the write silently lands on the merged result "
									"instead - a wrong answer that looks like a right one. Sculpt "
									"the base layer, or ask for this as its own item") },
			  { TEXT("heights"), TEXT("a JSON array of floats is deliberately not accepted - "
									  "1450x1450 is 2.1M values and about 25 MB of request body. "
									  "Use file, or data as base64 uint16") },
			  { TEXT("format"), TEXT("the format is taken from the file extension - .png or .r16") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world. NOTHING was changed.")); return; }
		ALandscape* Landscape = FindLandscape(World, JStrAny(In, { TEXT("landscape"), TEXT("actorPath") }));
		if (!Landscape) { Fail(Out, TEXT("no landscape found - call create_landscape first. NOTHING was changed.")); return; }
		ULandscapeInfo* Info = Landscape->GetLandscapeInfo();
		if (!Info) { Fail(Out, TEXT("landscape has no ULandscapeInfo - it was not imported correctly. NOTHING was changed.")); return; }


		int32 MinX, MinY, MaxX, MaxY;
		if (!LandscapeExtent(Landscape, MinX, MinY, MaxX, MaxY))
		{
			Fail(Out, TEXT("could not read landscape extent. NOTHING was changed.")); return;
		}
		const int32 FullW = MaxX - MinX + 1;
		const int32 FullH = MaxY - MinY + 1;

		const FString File = JStr(In, TEXT("file"));
		const FString B64  = JStr(In, TEXT("data"));
		if (File.IsEmpty() == B64.IsEmpty())
		{
			Fail(Out, TEXT("pass exactly one of file (a .png or .r16 on disk) or data (base64 "
						   "little-endian uint16). NOTHING was changed."));
			return;
		}

		// Region defaults to the whole landscape. x0/y0 are in the SAME vertex space the extent and
		// sculpt_landscape's `area` report, so a caller can read one and write the other.
		const int32 X0 = In->HasField(TEXT("x0")) ? int32(JNum(In, TEXT("x0"))) : MinX;
		const int32 Y0 = In->HasField(TEXT("y0")) ? int32(JNum(In, TEXT("y0"))) : MinY;
		int32 W = In->HasField(TEXT("width"))  ? int32(JNum(In, TEXT("width")))  : FullW;
		int32 H = In->HasField(TEXT("height")) ? int32(JNum(In, TEXT("height"))) : FullH;
		if (!B64.IsEmpty() && (!In->HasField(TEXT("width")) || !In->HasField(TEXT("height"))))
		{
			Fail(Out, TEXT("width and height are required with data - base64 carries no dimensions, "
						   "and guessing them from the byte count would accept a transposed or "
						   "sheared image. NOTHING was changed."));
			return;
		}
		if (W <= 0 || H <= 0)
		{
			Fail(Out, TEXT("width and height must be positive. NOTHING was changed.")); return;
		}
		if (X0 < MinX || Y0 < MinY || X0 + W - 1 > MaxX || Y0 + H - 1 > MaxY)
		{
			Fail(Out, FString::Printf(
				TEXT("the region x0=%d y0=%d %dx%d falls outside the landscape, whose vertices run "
					 "x %d..%d and y %d..%d (%dx%d). Refused rather than clipped - a silently "
					 "clipped import writes a different terrain than the one you generated. "
					 "NOTHING was changed."),
				X0, Y0, W, H, MinX, MaxX, MinY, MaxY, FullW, FullH));
			return;
		}

		TArray<uint16> Samples;
		FString DecErr;
		if (!File.IsEmpty())
		{
			const FString Full = FPaths::ConvertRelativePathToFull(File);
			if (!FPaths::FileExists(Full))
			{
				Fail(Out, FString::Printf(TEXT("no file at '%s'. NOTHING was changed."), *Full));
				return;
			}
			TArray<uint8> Bytes;
			if (!FFileHelper::LoadFileToArray(Bytes, *Full))
			{
				Fail(Out, FString::Printf(TEXT("could not read '%s'. NOTHING was changed."), *Full));
				return;
			}
			const FString Ext = FPaths::GetExtension(Full).ToLower();
			if (Ext != TEXT("png") && Ext != TEXT("r16") && Ext != TEXT("raw"))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is not a supported heightmap extension - use .png (16-bit greyscale) "
						 "or .r16/.raw. NOTHING was changed."), *Ext));
				return;
			}
			if (!MifDecodeHeightBytes(Bytes, Ext, W, H, Samples, DecErr))
			{
				Fail(Out, DecErr + TEXT(" NOTHING was changed."));
				return;
			}
		}
		else
		{
			TArray<uint8> Bytes;
			if (!FBase64::Decode(B64, Bytes))
			{
				Fail(Out, TEXT("data is not valid base64. NOTHING was changed.")); return;
			}
			if (!MifDecodeHeightBytes(Bytes, TEXT("r16"), W, H, Samples, DecErr))
			{
				Fail(Out, DecErr + TEXT(" NOTHING was changed."));
				return;
			}
		}

		// minZ/maxZ are BOTH or NEITHER. Half a mapping is not a mapping, and defaulting the missing
		// half would silently rescale the terrain.
		const bool bHasMin = In->HasField(TEXT("minZ"));
		const bool bHasMax = In->HasField(TEXT("maxZ"));
		if (bHasMin != bHasMax)
		{
			Fail(Out, TEXT("minZ and maxZ must be given together - one alone would silently rescale "
						   "the terrain against a default you did not choose. Omit both for a "
						   "straight copy, which is lossless because the native storage is already "
						   "uint16. NOTHING was changed."));
			return;
		}
		const FVector ActorScale = Landscape->GetActorScale3D();
		const double ActorZ = Landscape->GetActorLocation().Z;
		if (bHasMin)
		{
			const double MinZ = JNum(In, TEXT("minZ"));
			const double MaxZ = JNum(In, TEXT("maxZ"));
			if (MaxZ <= MinZ)
			{
				Fail(Out, TEXT("maxZ must be greater than minZ. NOTHING was changed.")); return;
			}
			for (uint16& Sample : Samples)
			{
				const double T = double(Sample) / 65535.0;
				// WorldToHeight takes an OFFSET from the landscape actor, not an absolute Z.
				Sample = WorldToHeight(FMath::Lerp(MinZ, MaxZ, T) - ActorZ, ActorScale.Z);
			}
		}

		// ORDER MATTERS: every PARAMETER refusal above runs first. A malformed payload must be
		// told what is wrong with it whatever the landscape looks like - putting this check
		// straight after the landscape resolved meant 'both file and data', 'data with no
		// dimensions' and four other request errors all came back as the edit-layer message,
		// which is a worse answer than the one they used to get. This is a WORLD-STATE
		// refusal, so it belongs with the write it protects, not with the argument parsing.
		if (RefuseIfEditLayers(Landscape, Out)) { return; }

		FLandscapeEditDataInterface Edit(Info);
		const int32 X1 = X0, Y1 = Y0, X2 = X0 + W - 1, Y2 = Y0 + H - 1;
		Edit.SetHeightData(X1, Y1, X2, Y2, Samples.GetData(), 0, /*InCalcNormals*/ true);
		Edit.Flush();

		// The same tail sculpt_landscape runs, and for the same reason: heightfield collision is
		// cooked separately from the render surface, so without this the terrain renders as hills
		// and every trace still hits the old one.
		for (ULandscapeComponent* Comp : Landscape->LandscapeComponents)
		{
			if (!Comp) { continue; }
			Comp->UpdateCachedBounds();
			Comp->MarkRenderStateDirty();
		}
		Landscape->RecreateCollisionComponents();
		Landscape->PostEditChange();

		// POSTCONDITION, read back from the landscape rather than trusted. SetHeightData returns
		// void, so the only evidence the write landed is the height that is there now.
		TArray<uint16> Back;
		Back.SetNumUninitialized(W * H);
		Edit.GetHeightDataFast(X1, Y1, X2, Y2, Back.GetData(), 0);
		int64 Mismatch = 0;
		for (int32 i = 0; i < Samples.Num(); ++i)
		{
			if (Back[i] != Samples[i]) { ++Mismatch; }
		}
		if (Mismatch > 0)
		{
			Fail(Out, FString::Printf(
				TEXT("wrote %d samples and %lld read back different. The landscape has been changed "
					 "and does NOT match what was sent - re-export and compare before trusting it."),
				Samples.Num(), (long long)Mismatch));
			Out->SetNumberField(TEXT("mismatched"), double(Mismatch));
			return;
		}

		Out->SetStringField(TEXT("landscape"), Landscape->GetPathName());
		Out->SetNumberField(TEXT("samples"), Samples.Num());
		Out->SetBoolField(TEXT("remapped"), bHasMin);
		TSharedRef<FJsonObject> Area = MakeShared<FJsonObject>();
		Area->SetNumberField(TEXT("x0"), X1); Area->SetNumberField(TEXT("y0"), Y1);
		Area->SetNumberField(TEXT("width"), W); Area->SetNumberField(TEXT("height"), H);
		Out->SetObjectField(TEXT("area"), Area);
		Out->SetStringField(TEXT("verified"),
			TEXT("every sample was read back from the landscape and matches what was sent."));
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the level is dirty and NOTHING has been saved."));
	}

	void H_export_landscape_heightmap(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("landscape"), TEXT("actorPath"), TEXT("file"),
			  TEXT("x0"), TEXT("y0"), TEXT("width"), TEXT("height"), TEXT("asData") },
			TEXT("landscape (alias actorPath); file - .png (16-bit greyscale) or .r16, default "
				 "<ProjectSaved>/MifBridge/Export/<Landscape>.r16; x0/y0/width/height for a region; "
				 "asData:true to also return base64 little-endian uint16 instead of only a path"),
			{ { TEXT("format"), TEXT("the format is taken from the file extension - .png or .r16") },
			  { TEXT("minZ"), TEXT("an export is the raw uint16 the landscape stores, so there is "
								   "nothing to remap. The response reports the world Z that 0 and "
								   "65535 correspond to, which is what you would remap WITH") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world.")); return; }
		ALandscape* Landscape = FindLandscape(World, JStrAny(In, { TEXT("landscape"), TEXT("actorPath") }));
		if (!Landscape) { Fail(Out, TEXT("no landscape found.")); return; }
		ULandscapeInfo* Info = Landscape->GetLandscapeInfo();
		if (!Info) { Fail(Out, TEXT("landscape has no ULandscapeInfo.")); return; }

		int32 MinX, MinY, MaxX, MaxY;
		if (!LandscapeExtent(Landscape, MinX, MinY, MaxX, MaxY))
		{
			Fail(Out, TEXT("could not read landscape extent.")); return;
		}
		const int32 X0 = In->HasField(TEXT("x0")) ? int32(JNum(In, TEXT("x0"))) : MinX;
		const int32 Y0 = In->HasField(TEXT("y0")) ? int32(JNum(In, TEXT("y0"))) : MinY;
		const int32 W = In->HasField(TEXT("width"))  ? int32(JNum(In, TEXT("width")))  : (MaxX - MinX + 1);
		const int32 H = In->HasField(TEXT("height")) ? int32(JNum(In, TEXT("height"))) : (MaxY - MinY + 1);
		if (W <= 0 || H <= 0 || X0 < MinX || Y0 < MinY || X0 + W - 1 > MaxX || Y0 + H - 1 > MaxY)
		{
			Fail(Out, FString::Printf(
				TEXT("the region x0=%d y0=%d %dx%d falls outside the landscape, whose vertices run "
					 "x %d..%d and y %d..%d."), X0, Y0, W, H, MinX, MaxX, MinY, MaxY));
			return;
		}

		FLandscapeEditDataInterface Edit(Info);
		TArray<uint16> Samples;
		Samples.SetNumUninitialized(W * H);
		Edit.GetHeightDataFast(X0, Y0, X0 + W - 1, Y0 + H - 1, Samples.GetData(), 0);

		FString OutPath = JStr(In, TEXT("file"));
		if (OutPath.IsEmpty())
		{
			OutPath = FPaths::ProjectSavedDir() / TEXT("MifBridge") / TEXT("Export")
					/ (Landscape->GetName() + TEXT(".r16"));
		}
		const FString FullOut = FPaths::ConvertRelativePathToFull(OutPath);
		if (RefuseFileOutsideProject(FullOut, Out, TEXT("export_landscape_heightmap")))
		{
			return;
		}
		const FString Ext = FPaths::GetExtension(FullOut).ToLower();
		if (Ext != TEXT("png") && Ext != TEXT("r16") && Ext != TEXT("raw"))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a supported heightmap extension - use .png or .r16."), *Ext));
			return;
		}

		bool bWrote = false;
		if (Ext == TEXT("png"))
		{
			IImageWrapperModule& Mod =
				FModuleManager::LoadModuleChecked<IImageWrapperModule>(TEXT("ImageWrapper"));
			TSharedPtr<IImageWrapper> Wrapper = Mod.CreateImageWrapper(EImageFormat::PNG);
			if (Wrapper.IsValid()
				&& Wrapper->SetRaw(Samples.GetData(), int64(Samples.Num()) * 2, W, H,
								   ERGBFormat::Gray, 16))
			{
				const TArray64<uint8>& Png = Wrapper->GetCompressed();
				bWrote = FFileHelper::SaveArrayToFile(TArrayView64<const uint8>(Png.GetData(), Png.Num()), *FullOut);
			}
		}
		else
		{
			TArray<uint8> Bytes;
			Bytes.SetNumUninitialized(Samples.Num() * 2);
			FMemory::Memcpy(Bytes.GetData(), Samples.GetData(), Bytes.Num());
			bWrote = FFileHelper::SaveArrayToFile(Bytes, *FullOut);
		}
		if (!bWrote)
		{
			Fail(Out, FString::Printf(TEXT("could not write '%s'."), *FullOut)); return;
		}
		// POSTCONDITION: the file is on disk at the size the samples imply. SaveArrayToFile returns a
		// bool and a short write is exactly the failure that would be invisible otherwise.
		const int64 OnDisk = IFileManager::Get().FileSize(*FullOut);
		if (OnDisk <= 0 || (Ext != TEXT("png") && OnDisk != int64(Samples.Num()) * 2))
		{
			Fail(Out, FString::Printf(
				TEXT("wrote '%s' but it is %lld bytes on disk, not the %lld the samples require."),
				*FullOut, (long long)OnDisk, (long long)Samples.Num() * 2));
			return;
		}

		const FVector ActorScale = Landscape->GetActorScale3D();
		const double ActorZ = Landscape->GetActorLocation().Z;
		Out->SetStringField(TEXT("landscape"), Landscape->GetPathName());
		Out->SetStringField(TEXT("file"), FullOut);
		Out->SetNumberField(TEXT("bytes"), double(OnDisk));
		Out->SetNumberField(TEXT("samples"), Samples.Num());
		TSharedRef<FJsonObject> Area = MakeShared<FJsonObject>();
		Area->SetNumberField(TEXT("x0"), X0); Area->SetNumberField(TEXT("y0"), Y0);
		Area->SetNumberField(TEXT("width"), W); Area->SetNumberField(TEXT("height"), H);
		Out->SetObjectField(TEXT("area"), Area);
		// The mapping a caller needs to generate a replacement image against.
		Out->SetNumberField(TEXT("worldZAtZero"), ActorZ + HeightToWorld(0, ActorScale.Z));
		Out->SetNumberField(TEXT("worldZAtMax"), ActorZ + HeightToWorld(65535, ActorScale.Z));
		Out->SetStringField(TEXT("note"),
			TEXT("these are the RAW uint16 the landscape stores - 32768 is the actor's own Z. "
				 "Re-importing this file with no minZ/maxZ reproduces the terrain exactly, because "
				 "nothing is remapped in either direction."));
		if (JBool(In, TEXT("asData"), false))
		{
			TArray<uint8> Bytes;
			Bytes.SetNumUninitialized(Samples.Num() * 2);
			FMemory::Memcpy(Bytes.GetData(), Samples.GetData(), Bytes.Num());
			Out->SetStringField(TEXT("data"), FBase64::Encode(Bytes));
		}
	}
}
