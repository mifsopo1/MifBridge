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

				FLandscapeImportLayerInfo Import(Info->LayerName);
				Import.LayerInfo = Info;
				Import.SourceFilePath = TEXT("");

				// Default: first layer fully painted, the rest empty. That gives a visible surface
				// immediately and leaves the others to paint_landscape.
				const double Weight = JNum(O, TEXT("weight"), bFirst ? 1.0 : 0.0);
				const uint8 Fill = (uint8)FMath::Clamp(FMath::RoundToInt(Weight * 255.0), 0, 255);
				Import.LayerData.Init(Fill, VertsX * VertsY);

				TSharedRef<FJsonObject> LOut = MakeShared<FJsonObject>();
				LOut->SetStringField(TEXT("name"), Info->LayerName.ToString());
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
		Out->SetStringField(TEXT("layer"), LayerInfo->LayerName.ToString());
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
			Arr.Add(MakeShared<FJsonValueObject>(O));
		}

		Out->SetArrayField(TEXT("landscapes"), Arr);
		Out->SetNumberField(TEXT("count"), Arr.Num());
		if (Arr.Num() == 0)
		{
			Out->SetStringField(TEXT("note"), TEXT("no ALandscape in the editor world — call create_landscape"));
		}
	}
}
