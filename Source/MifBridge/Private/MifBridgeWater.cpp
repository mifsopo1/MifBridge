// MifBridge - WATER BODIES: rivers, lakes, oceans and custom bodies.
//
// WHY THIS EXISTS, and why it is not a DDS2 feature. Andre: "my curfew project needs the new 5.7
// water endpoints... we need all parity we can find". The Water plugin has been LINKED since the
// breadth pass (MIF_WITH_WATER in Build.cs) and nothing has ever used it - the dependency was added
// and the endpoints were never written, which is the worst of both: build cost, no capability.
//
// This is the first work judged for CURFEW rather than for DDS2. The feature spec's judging rule
// used to be "value for cooked-game modding", and that rule is what declined this whole category.
// MifBridge serves both projects, so the rule has been changed rather than worked around.
//
// PORTABILITY. Verified in BOTH trees before writing, per docs/02_GOTCHAS.md section 14 - and this
// family is unusually clean, which is worth recording so nobody re-checks:
//
//   AWaterBody::GetWaterBodyType()       5.3 and 5.7, identical, non-virtual dispatch to the component
//   AWaterBody::GetWaterBodyComponent()  5.3 and 5.7, identical
//   AWaterBody::GetWaterSpline()         5.3 and 5.7, identical
//   AWaterBody::GetWaterBodyIndex()      5.3 and 5.7, identical
//   AWaterBody::GetWaterZone()           both; 5.7 adds WATER_API on the member, which is
//                                        declaration-side only and changes nothing for callers
//   EWaterBodyType                       same four values in both: River, Lake, Ocean, Transition
//
// The one trap here is the ENUM SPELLING. The fourth value is `Transition` in C++ and displays as
// "Custom" in the editor (UMETA(DisplayName = "Custom")). Reporting the C++ name alone would have a
// caller searching the Water docs for a body type that is not named there, so both are reported.
//
// Water is an EXPERIMENTAL plugin in both engines (Plugins/Experimental/Water), so it is absent
// wherever it has not been enabled - the same contract as every other MIF_WITH_*: the endpoints stay
// REGISTERED and compile a named refusal, because a missing endpoint tells a caller nothing.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#if MIF_WITH_WATER
#include "WaterBodyActor.h"            // AWaterBody, EWaterBodyType
#include "WaterBodyComponent.h"        // UWaterBodyComponent - the material and rendering side
#include "WaterSplineComponent.h"      // UWaterSplineComponent - the shape
#include "WaterZoneActor.h"            // AWaterZone - what a body belongs to
// The FOUR concrete body classes. There is no "set the type" - the type IS the class, so authoring
// means picking one of these at spawn time.
#include "WaterBodyRiverActor.h"
#include "WaterBodyLakeActor.h"
#include "WaterBodyOceanActor.h"
#include "WaterBodyCustomActor.h"

#include "Editor.h"                                    // GEditor->FindActorFactoryForActorClass
#include "ActorFactories/ActorFactory.h"               // UActorFactory::CreateActor
#include "Components/SplineComponent.h"
#include "Engine/World.h"
#include "EngineUtils.h"               // TActorIterator
#include "Materials/MaterialInterface.h"
#endif

namespace MifBridge
{
#if !MIF_WITH_WATER
	namespace
	{
		/** One message for every water endpoint on a build without the plugin. */
		void WaterUnavailable(const TSharedRef<FJsonObject>& Out, const TCHAR* What)
		{
			Fail(Out, FString::Printf(
				TEXT("%s is unavailable: this MifBridge was built against an engine with no Water "
					 "plugin. Water lives in Engine/Plugins/Experimental/Water and is off by default, "
					 "so enable it for the project and rebuild. The endpoint stays registered so that "
					 "this answer is possible at all."), What));
		}
	}
#else
	namespace
	{
		/** C++ name and editor name. They differ for exactly one value and that difference is a real
		 *  trap: EWaterBodyType::Transition is shown as "Custom" everywhere in the editor UI. */
		void WaterTypeNames(EWaterBodyType Type, FString& OutName, FString& OutDisplay)
		{
			switch (Type)
			{
			case EWaterBodyType::River:      OutName = TEXT("River");      OutDisplay = TEXT("River");  break;
			case EWaterBodyType::Lake:       OutName = TEXT("Lake");       OutDisplay = TEXT("Lake");   break;
			case EWaterBodyType::Ocean:      OutName = TEXT("Ocean");      OutDisplay = TEXT("Ocean");  break;
			case EWaterBodyType::Transition: OutName = TEXT("Transition"); OutDisplay = TEXT("Custom"); break;
			default:                         OutName = TEXT("Unknown");    OutDisplay = TEXT("Unknown"); break;
			}
		}

		/** Replace a body's spline from a JSON point array. Shared by create_water_body and
		 *  set_water_body_spline so the two cannot disagree about what a valid point list is.
		 *
		 *  ResetSpline is the ONLY engine entry point that rebuilds the body's derived data from a
		 *  point list - poking the USplineComponent directly leaves the water body's own caches stale,
		 *  which renders as a river that is the wrong shape with no error anywhere. Verified public in
		 *  both trees: WaterSplineComponent.h:74 (5.3) and :70 (5.7).
		 *
		 *  WORLD space in, LOCAL space to the engine: ResetSpline takes points relative to the
		 *  component, and every other spatial endpoint in this bridge speaks world space. Converting
		 *  here rather than making each caller do it is the difference between a river where they asked
		 *  for one and a river offset by the actor location. */
		bool WaterApplySpline(AWaterBody* Body, const TArray<TSharedPtr<FJsonValue>>& In,
							  int32& OutCount, FString& OutError)
		{
			OutCount = 0;
			UWaterSplineComponent* Spline = Body ? Body->GetWaterSpline() : nullptr;
			if (!Spline)
			{
				OutError = TEXT("this water body has no spline component.");
				return false;
			}
			// Two points is the minimum that describes a shape. One point is a degenerate spline the
			// engine will accept and render as nothing, which is exactly the silent-success shape this
			// project keeps finding - so it is refused here instead.
			if (In.Num() < 2)
			{
				OutError = FString::Printf(
					TEXT("a water body spline needs at least 2 points and %d %s given. One point is a "
						 "degenerate spline: the engine accepts it and renders nothing."),
					In.Num(), In.Num() == 1 ? TEXT("was") : TEXT("were"));
				return false;
			}

			const FTransform ToLocal = Spline->GetComponentTransform();
			TArray<FVector> Points;
			Points.Reserve(In.Num());
			for (int32 i = 0; i < In.Num(); ++i)
			{
				const TSharedPtr<FJsonObject>* Obj = nullptr;
				if (!In[i].IsValid() || !In[i]->TryGetObject(Obj) || Obj == nullptr)
				{
					OutError = FString::Printf(
						TEXT("points[%d] is not an object - each point must be {\"x\":..,\"y\":..,\"z\":..}."), i);
					return false;
				}
				const TSharedRef<FJsonObject> P = Obj->ToSharedRef();
				const FVector World(JNum(P, TEXT("x"), 0.0), JNum(P, TEXT("y"), 0.0), JNum(P, TEXT("z"), 0.0));
				Points.Add(ToLocal.InverseTransformPosition(World));
			}

			Body->Modify();
			Spline->Modify();
			Spline->ResetSpline(Points);
			OutCount = Points.Num();
			return true;
		}

		/** The fields every water read shares. Kept in one place so list_ and describe_ cannot drift
		 *  into disagreeing about the same body - the house rule that a read is the verification path
		 *  only holds if two reads of the same thing agree. */
		void WaterBodySummary(AWaterBody* Body, const TSharedRef<FJsonObject>& J)
		{
			FString TypeName, TypeDisplay;
			WaterTypeNames(Body->GetWaterBodyType(), TypeName, TypeDisplay);

			J->SetStringField(TEXT("actorPath"), Body->GetPathName());
			J->SetStringField(TEXT("label"), Body->GetActorLabel());
			J->SetStringField(TEXT("class"), Body->GetClass()->GetName());
			J->SetStringField(TEXT("waterBodyType"), TypeName);
			J->SetStringField(TEXT("waterBodyTypeDisplayName"), TypeDisplay);
			// waterBodyIndex and the zone live on the COMPONENT, not the actor - verified at
			// WaterBodyComponent.h:210 and :385 in 5.3 after assuming otherwise and being told off by
			// the compiler. Grepping a plugin's whole Public/ folder finds a member; it does not tell
			// you which class owns it.
			UWaterBodyComponent* BodyComp = Body->GetWaterBodyComponent();
			J->SetNumberField(TEXT("waterBodyIndex"), BodyComp ? BodyComp->GetWaterBodyIndex() : -1);

			const FVector Loc = Body->GetActorLocation();
			TSharedRef<FJsonObject> L = MakeShared<FJsonObject>();
			L->SetNumberField(TEXT("x"), Loc.X);
			L->SetNumberField(TEXT("y"), Loc.Y);
			L->SetNumberField(TEXT("z"), Loc.Z);
			J->SetObjectField(TEXT("location"), L);

			// The spline IS the shape of a river or lake, so its point count is the first thing worth
			// knowing - a body with 0 or 1 points is authored-but-empty and renders nothing.
			if (UWaterSplineComponent* Spline = Body->GetWaterSpline())
			{
				J->SetNumberField(TEXT("splinePoints"), Spline->GetNumberOfSplinePoints());
				J->SetBoolField(TEXT("splineClosedLoop"), Spline->IsClosedLoop());
			}
			else
			{
				J->SetNumberField(TEXT("splinePoints"), 0);
				J->SetBoolField(TEXT("splineClosedLoop"), false);
			}

			// A body with no zone does not render in 5.1+. Reporting it as a plain null would leave a
			// caller guessing whether that is normal.
			//
			// UpdateWaterZones() FIRST: GetWaterZone() only returns the CACHED OwningWaterZone field,
			// which nothing here has asked to recompute. VERIFIED 2026-08-28 (create_water_zone's own
			// coverage count, same file): a body created moments before a covering zone can still read
			// back with a stale zone reference matching neither the new zone nor null, because the
			// recompute this forces is otherwise left to whatever OTHER tick happens to run it, whenever
			// that is. This function is shared by create_water_body, describe_water_body and
			// list_water_bodies, so a caller reading a body right after changing the zone situation
			// around it is exactly the case this closes.
			if (BodyComp) { BodyComp->UpdateWaterZones(); }
			if (AWaterZone* Zone = BodyComp ? BodyComp->GetWaterZone() : nullptr)
			{
				J->SetStringField(TEXT("waterZone"), Zone->GetPathName());
			}
			else
			{
				J->SetStringField(TEXT("waterZone"), FString());
				J->SetStringField(TEXT("waterZoneNote"),
					TEXT("this body belongs to NO water zone. Since UE 5.1 a water body renders only "
						 "inside an AWaterZone, so this one is authored but invisible - place a "
						 "WaterZone actor covering it."));
			}
		}
	}
#endif

	// --- list_water_bodies ---------------------------------------------------------------------
	//   in:  { type? = "" (River|Lake|Ocean|Transition|Custom), nameContains? = "" }
	//   out: { count, waterBodies[{ actorPath, label, class, waterBodyType, ... }], worldType }
	// Bucket: read-only. Nothing in this plugin could see water at all before this.
	void H_list_water_bodies(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("type"), TEXT("waterBodyType"), TEXT("nameContains") },
			TEXT("type (alias: waterBodyType) - River, Lake, Ocean, or Transition (aka Custom); "
				 "nameContains (substring filter on the actor label)"),
			{ { TEXT("path"), TEXT("this lists every water body in the OPEN level; describe_water_body takes a path") },
			  { TEXT("zone"), TEXT("filtering by water zone is not supported - each body reports its waterZone and you can filter on that") } }))
		{
			return;
		}
#if !MIF_WITH_WATER
		WaterUnavailable(Out, TEXT("list_water_bodies"));
#else
		UWorld* World = EditorWorld();
		if (!World)
		{
			Fail(Out, TEXT("no editor world is open"));
			return;
		}

		// "Custom" is what the editor calls Transition, so accept BOTH spellings rather than making a
		// caller who read it off the details panel guess the C++ name.
		FString WantType = JStrAny(In, { TEXT("type"), TEXT("waterBodyType") });
		if (WantType.Equals(TEXT("Custom"), ESearchCase::IgnoreCase)) { WantType = TEXT("Transition"); }
		const FString WantName = JStr(In, TEXT("nameContains"));

		TArray<TSharedPtr<FJsonValue>> Bodies;
		int32 Considered = 0;
		for (TActorIterator<AWaterBody> It(World); It; ++It)
		{
			AWaterBody* Body = *It;
			if (!IsValid(Body)) { continue; }
			++Considered;

			FString TypeName, TypeDisplay;
			WaterTypeNames(Body->GetWaterBodyType(), TypeName, TypeDisplay);
			if (!WantType.IsEmpty() && !TypeName.Equals(WantType, ESearchCase::IgnoreCase)) { continue; }
			if (!WantName.IsEmpty() && !Body->GetActorLabel().Contains(WantName)) { continue; }

			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			WaterBodySummary(Body, J);
			Bodies.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetArrayField(TEXT("waterBodies"), Bodies);
		Out->SetNumberField(TEXT("count"), Bodies.Num());
		// Reported separately so a filter that matches nothing is distinguishable from a level with no
		// water at all - the two need completely different next steps.
		Out->SetNumberField(TEXT("totalInLevel"), Considered);
		if (Bodies.Num() == 0 && Considered > 0)
		{
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("%d water body/bodies exist in this level but none matched the filter. Note that "
					 "the editor's \"Custom\" type is spelled Transition in C++; both are accepted here."),
				Considered));
		}
		UE_LOG(LogMifBridge, Log, TEXT("list_water_bodies: %d of %d"), Bodies.Num(), Considered);
#endif
	}

	// --- describe_water_body -------------------------------------------------------------------
	//   in:  { path (alias: actorPath) }
	//   out: everything list_water_bodies reports, plus material and per-spline-point detail
	// Bucket: read-only.
	void H_describe_water_body(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("actorPath"), TEXT("includeSplinePoints") },
			TEXT("path (alias: actorPath) - a water body actor; includeSplinePoints (default true)"),
			{ { TEXT("name"), TEXT("pass the actor PATH - list_water_bodies reports actorPath for each") } }))
		{
			return;
		}
#if !MIF_WITH_WATER
		WaterUnavailable(Out, TEXT("describe_water_body"));
#else
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("actorPath") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - list_water_bodies reports actorPath for every body"));
			return;
		}

		AWaterBody* Body = FindObject<AWaterBody>(nullptr, *Path);
		if (!Body)
		{
			// Resolving by label as a fallback would be guessing, and two bodies can share a label.
			Fail(Out, FString::Printf(
				TEXT("no water body at '%s'. list_water_bodies reports the actorPath of every body in "
					 "the open level; note this resolves by PATH, not by label."), *Path));
			return;
		}

		WaterBodySummary(Body, Out);

		if (UWaterBodyComponent* Comp = Body->GetWaterBodyComponent())
		{
			UMaterialInterface* Mat = Comp->GetWaterMaterial();
			Out->SetStringField(TEXT("waterMaterial"), Mat ? Mat->GetPathName() : FString());
			if (!Mat)
			{
				Out->SetStringField(TEXT("waterMaterialNote"),
					TEXT("no water material is assigned, so this body renders nothing regardless of "
						 "its spline."));
			}
		}

		if (JBool(In, TEXT("includeSplinePoints"), true))
		{
			TArray<TSharedPtr<FJsonValue>> Points;
			if (UWaterSplineComponent* Spline = Body->GetWaterSpline())
			{
				const int32 N = Spline->GetNumberOfSplinePoints();
				for (int32 i = 0; i < N; ++i)
				{
					// WORLD space deliberately. Every other spatial read in this bridge answers in
					// world coordinates, and a caller comparing a river against a landscape or a
					// placed actor needs them in the same frame.
					const FVector P = Spline->GetLocationAtSplinePoint(i, ESplineCoordinateSpace::World);
					TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
					J->SetNumberField(TEXT("index"), i);
					J->SetNumberField(TEXT("x"), P.X);
					J->SetNumberField(TEXT("y"), P.Y);
					J->SetNumberField(TEXT("z"), P.Z);
					Points.Add(MakeShared<FJsonValueObject>(J));
				}
			}
			Out->SetArrayField(TEXT("splinePointsWorld"), Points);
		}
		UE_LOG(LogMifBridge, Log, TEXT("describe_water_body: %s"), *Body->GetActorLabel());
#endif
	}

	// --- create_water_body ----------------------------------------------------------------------
	//   in:  { type (River|Lake|Ocean|Custom|Transition), label?, x?, y?, z?,
	//          points? [ {x,y,z}, ... ] }
	//   out: { actorPath, label, waterBodyType, splinePoints, waterZone, ... }
	// Bucket: MUTATES the open level. Nothing is saved - the actor exists in the editor world only,
	// same contract as spawn_actor_in_level.
	void H_create_water_body(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("type"), TEXT("waterBodyType"), TEXT("label"),
			  TEXT("x"), TEXT("y"), TEXT("z"), TEXT("points") },
			TEXT("type (alias: waterBodyType) - River, Lake, Ocean, or Custom (aka Transition); "
				 "label; x, y, z (the actor's location); points (optional spline, world space)"),
			{ { TEXT("spline"), TEXT("spell it points - an array of {x,y,z} in WORLD space") },
			  { TEXT("class"), TEXT("pass type instead - the actor class is derived from it, because the four water body classes are not interchangeable") },
			  { TEXT("zone"), TEXT("a body finds its own AWaterZone by overlap; create the zone separately with create_water_zone") } }))
		{
			return;
		}
#if !MIF_WITH_WATER
		WaterUnavailable(Out, TEXT("create_water_body"));
#else
		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world is open")); return; }

		FString TypeStr = JStrAny(In, { TEXT("type"), TEXT("waterBodyType") });
		if (TypeStr.IsEmpty())
		{
			Fail(Out, TEXT("type is required - River, Lake, Ocean, or Custom. NOTHING was created."));
			return;
		}
		// "Custom" is the editor's name for Transition, and it is the name a caller will have read off
		// the placement menu. Accept it rather than making them find the C++ spelling.
		if (TypeStr.Equals(TEXT("Transition"), ESearchCase::IgnoreCase)) { TypeStr = TEXT("Custom"); }

		// THE FOUR CLASSES ARE NOT INTERCHANGEABLE. Each has its own component with its own
		// GetWaterBodyType(), and the type is not a settable property - it is which class you spawned.
		// So this maps a name to a class rather than spawning a base AWaterBody and setting a field,
		// which is what a caller coming from set_property would expect and which cannot work.
		UClass* SpawnClass = nullptr;
		if (TypeStr.Equals(TEXT("River"),  ESearchCase::IgnoreCase)) { SpawnClass = AWaterBodyRiver::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Lake"),   ESearchCase::IgnoreCase)) { SpawnClass = AWaterBodyLake::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Ocean"),  ESearchCase::IgnoreCase)) { SpawnClass = AWaterBodyOcean::StaticClass(); }
		else if (TypeStr.Equals(TEXT("Custom"), ESearchCase::IgnoreCase)) { SpawnClass = AWaterBodyCustom::StaticClass(); }
		if (!SpawnClass)
		{
			Fail(Out, FString::Printf(
				TEXT("unknown water body type '%s'. Use River, Lake, Ocean, or Custom (the editor's "
					 "name for the C++ 'Transition'). NOTHING was created."), *TypeStr));
			return;
		}

		const FVector Loc(JNum(In, TEXT("x"), 0.0), JNum(In, TEXT("y"), 0.0), JNum(In, TEXT("z"), 0.0));
		// THROUGH THE ACTOR FACTORY, never World->SpawnActor.
		//
		// The first version of this raw-spawned, and shipped a water body with NO MATERIALS. Its own
		// response note admitted the symptom - "a water body needs a water material assigned to its
		// component" - without my realising the engine assigns them for free through the factory.
		// I documented the hole instead of not digging it.
		//
		// UWaterBodyActorFactory::PostSpawnActor is where the defaults come from
		// (Water/Source/Editor/Private/WaterBodyActorFactory.cpp - 5.3 :43-46, 5.7 :44-47):
		// WaterMaterial, WaterStaticMeshMaterial, HLODMaterial, UnderwaterPostProcessMaterial, plus
		// the river-to-lake and river-to-ocean transition materials (5.3 :99-100, 5.7 :102-103) and a
		// sensible starting spline (5.3 :103, 5.7 :106). UWaterBodyComponent's constructor sets none
		// of them.
		//
		// WORSE ON 5.7: SetWaterInfoMaterial is called from the factory (:49) with NO 5.3 counterpart
		// and no constructor fallback, so a raw spawn there leaves it null outright.
		//
		// The overload matters. 5.3 declares TWO CreateActor overloads - ActorFactory.h:62 takes
		// EObjectFlags, :63 takes FActorSpawnParameters - and 5.7 kept only the FActorSpawnParameters
		// form at :65. Using the FActorSpawnParameters one is the only spelling that compiles on both.
		FActorSpawnParameters Params;
		Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

		UActorFactory* Factory = GEditor ? GEditor->FindActorFactoryForActorClass(SpawnClass) : nullptr;
		if (!Factory)
		{
			// REFUSE rather than falling back to a raw spawn. A fallback would silently reproduce
			// exactly the bug this comment is about, and the caller would get ok:true and an invisible
			// river. The factory is registered by FWaterEditorModule::StartupModule, so its absence
			// means the Water editor module did not load - which is worth knowing, not papering over.
			Fail(Out, FString::Printf(
				TEXT("no actor factory is registered for %s, which means the Water EDITOR module is "
					 "not loaded. Spawning one directly would produce a body with no water material, "
					 "no HLOD material and no underwater post-process - it would exist and render "
					 "nothing. NOTHING was created."), *SpawnClass->GetName()));
			return;
		}

		AWaterBody* Body = Cast<AWaterBody>(
			Factory->CreateActor(nullptr, World->GetCurrentLevel(), FTransform(Loc), Params));
		if (!Body)
		{
			Fail(Out, FString::Printf(
				TEXT("the actor factory for %s returned nothing and reported no reason. NOTHING was "
					 "created."), *SpawnClass->GetName()));
			return;
		}

		const FString Label = JStr(In, TEXT("label"));
		if (!Label.IsEmpty()) { Body->SetActorLabel(Label); }

		// Optional shape in the same call. A river with no spline is not a river, so letting the
		// caller do it in one round trip is worth the extra parameter.
		const TArray<TSharedPtr<FJsonValue>>* PointsArr = nullptr;
		int32 PointsSet = 0;
		if (JArray(In, TEXT("points"), PointsArr) && PointsArr)
		{
			FString Why;
			if (!WaterApplySpline(Body, *PointsArr, PointsSet, Why))
			{
				// The actor EXISTS at this point, so saying only "failed" would strand it unnamed in
				// the level with nobody knowing it is there.
				Out->SetStringField(TEXT("actorPath"), Body->GetPathName());
				Fail(Out, FString::Printf(
					TEXT("the %s was created at %s but its spline was NOT set: %s. The actor is in the "
						 "level - delete it or fix the points and call set_water_body_spline."),
					*TypeStr, *Body->GetActorLabel(), *Why));
				return;
			}
		}

		WaterBodySummary(Body, Out);
		Out->SetNumberField(TEXT("splinePointsSet"), PointsSet);
		// NOT an unconditional "it still needs a zone" claim - that was wrong often enough to remove.
		// VERIFIED 2026-08-28: the actor factory frequently auto-spawns its own default AWaterZone
		// covering a body created with none nearby, so "still needs one" is sometimes already false by
		// the time this response is built. WaterBodySummary just reported the ACTUAL state above -
		// waterZone if one covers it (auto-spawned or otherwise), waterZoneNote if genuinely none does
		// - so this note only adds the parts that are true unconditionally.
		Out->SetStringField(TEXT("note"),
			TEXT("nothing was saved - this body exists in the open level only. Materials come from the "
				 "engine's own water actor factory, so this body has them. Whether it renders depends on "
				 "waterZone above, not on anything this call does separately - see waterZoneNote if it "
				 "is empty."));
		UE_LOG(LogMifBridge, Log, TEXT("create_water_body: %s '%s' with %d spline point(s)"),
			*TypeStr, *Body->GetActorLabel(), PointsSet);
#endif
	}

	// --- create_water_zone ----------------------------------------------------------------------
	//   in:  { x?, y?, z?, extentX?, extentY?, label? }
	//   out: { actorPath, label, zoneExtent:{x,y}, bodiesNowCovered, bodiesStillWithoutZone, ... }
	// Bucket: MUTATES the open level, same contract as create_water_body - nothing is saved.
	//
	// WHY THIS EXISTS. create_water_body's own advice named this endpoint - "create the zone
	// separately with create_water_zone" - and it did not exist. That was not a typo but the visible
	// end of a real gap: since UE 5.1 a water body OUTSIDE any AWaterZone does not render at all, so
	// the write half of this family could author water that could never be seen, said so in a note,
	// and offered no way to fix it. Found by tools/audit_message_endpoints.py.
	void H_create_water_zone(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("x"), TEXT("y"), TEXT("z"), TEXT("extentX"), TEXT("extentY"), TEXT("label") },
			TEXT("x, y, z (the zone's location); extentX, extentY (its size in world units - both or neither); label"),
			{ { TEXT("extent"), TEXT("pass extentX and extentY - a zone's extent is a 2D size, and one number would have to guess whether you meant a square or a diameter") },
			  { TEXT("bodies"), TEXT("a zone does not take a body list - each AWaterBody finds its zone by OVERLAP, so place the zone over them and the response reports which ones it picked up") },
			  { TEXT("resolution"), TEXT("render target resolution comes from the engine's Water editor settings through the actor factory; there is no override here") } }))
		{
			return;
		}
#if !MIF_WITH_WATER
		WaterUnavailable(Out, TEXT("create_water_zone"));
#else
		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world is open")); return; }

		// BOTH OR NEITHER. A zone with one axis from the request and the other from a default is a
		// shape nobody asked for, and it would look deliberate.
		const bool bHasX = In->HasField(TEXT("extentX"));
		const bool bHasY = In->HasField(TEXT("extentY"));
		if (bHasX != bHasY)
		{
			Fail(Out, TEXT("pass BOTH extentX and extentY, or neither. Setting one axis and leaving "
						   "the other at the engine default produces a zone shape nobody asked for. "
						   "NOTHING was created."));
			return;
		}
		const double ExtentX = JNum(In, TEXT("extentX"), 0.0);
		const double ExtentY = JNum(In, TEXT("extentY"), 0.0);
		if (bHasX && (ExtentX <= 0.0 || ExtentY <= 0.0))
		{
			Fail(Out, FString::Printf(
				TEXT("extentX and extentY must both be positive; got %.2f x %.2f. A zero or negative "
					 "extent covers nothing, so every body would still be invisible. NOTHING was created."),
				ExtentX, ExtentY));
			return;
		}

		// THROUGH THE ACTOR FACTORY, for the same reason create_water_body is - and it is the same
		// mistake waiting to be made twice. UWaterZoneActorFactory::PostSpawnActor sets the
		// far-distance material, the far-distance mesh extent and the render target resolution from
		// UWaterEditorSettings, identically in 5.3 and 5.7. AWaterZone's constructor sets none of
		// them, so a raw World->SpawnActor gives a zone that exists, reports fine, and renders wrong.
		FActorSpawnParameters Params;
		Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
		UActorFactory* Factory = GEditor ? GEditor->FindActorFactoryForActorClass(AWaterZone::StaticClass()) : nullptr;
		if (!Factory)
		{
			Fail(Out, TEXT("no actor factory is registered for AWaterZone, so a zone cannot be created "
						   "with the engine's own defaults - it would have no far-distance material and "
						   "the wrong render target resolution. NOTHING was created."));
			return;
		}

		const FVector Loc(JNum(In, TEXT("x"), 0.0), JNum(In, TEXT("y"), 0.0), JNum(In, TEXT("z"), 0.0));
		AWaterZone* Zone = Cast<AWaterZone>(
			Factory->CreateActor(nullptr, World->GetCurrentLevel(), FTransform(Loc), Params));
		if (!Zone)
		{
			Fail(Out, TEXT("the AWaterZone actor factory returned nothing and reported no reason. "
						   "NOTHING was created."));
			return;
		}

		const FString Label = JStr(In, TEXT("label"));
		if (!Label.IsEmpty()) { Zone->SetActorLabel(Label); }
		if (bHasX) { Zone->SetZoneExtent(FVector2D(ExtentX, ExtentY)); }

		// Rebuild by FLAG NAME, never by value. EWaterZoneRebuildFlags::All is (~0) in both engines,
		// but the individual bits MOVED - UpdateWaterInfoTexture is (1 << 1) on 5.3 and (1 << 0) on
		// 5.7 - so anything that hardcodes a number here breaks silently on one of them.
		// One argument compiles on both: 5.3 takes only the flags, and 5.7's second parameter is a
		// defaulted debug object (5.7 WaterZoneActor.h:18).
		Zone->MarkForRebuild(EWaterZoneRebuildFlags::All);

		// READ BACK the extent rather than echoing what was asked for. SetZoneExtent is not a plain
		// field write, and this endpoint's whole purpose is coverage - reporting a requested size that
		// did not take would defeat the point of the call.
		const FVector2D Applied = Zone->GetZoneExtent();
		TSharedRef<FJsonObject> ExtentJson = MakeShared<FJsonObject>();
		ExtentJson->SetNumberField(TEXT("x"), Applied.X);
		ExtentJson->SetNumberField(TEXT("y"), Applied.Y);
		Out->SetObjectField(TEXT("zoneExtent"), ExtentJson);
		if (bHasX && (!FMath::IsNearlyEqual(Applied.X, ExtentX, 0.01)
					  || !FMath::IsNearlyEqual(Applied.Y, ExtentY, 0.01)))
		{
			Out->SetStringField(TEXT("extentWarning"), FString::Printf(
				TEXT("%.2f x %.2f was requested and the zone reports %.2f x %.2f. The zone EXISTS - "
					 "this is a size mismatch, not a failure to create."),
				ExtentX, ExtentY, Applied.X, Applied.Y));
		}

		// THE NUMBER THAT ANSWERS THE ACTUAL QUESTION. Nobody creates a zone for its own sake - they
		// create one so that bodies render. A body finds its zone by OVERLAP, so the only way to know
		// whether this zone did any good is to ask every body afterwards.
		//
		// GetWaterZone() is NOT live - it returns the CACHED OwningWaterZone field, which only changes
		// when UpdateWaterZones() runs (WaterBodyComponent.cpp:374-413: computes world bounds, calls
		// UWaterSubsystem::FindWaterZone, and only THEN writes OwningWaterZone). MarkForRebuild above
		// does not call it, so this forces the recompute per body rather than trust a cache that
		// something else might not have refreshed yet.
		//
		// THE REAL SURPRISE, found chasing what looked like that staleness (VERIFIED 2026-08-28,
		// isolated with TActorIterator<AWaterZone> logging directly in this handler on a level
		// confirmed to have ZERO pre-existing zones): create_water_body's own actor factory ALREADY
		// auto-spawns a default, unlabeled AWaterZone covering the new body if none exists yet - an
		// engine behaviour this file's own comments never mention. So a body created via
		// create_water_body and THEN covered by an explicit create_water_zone call is very often
		// already covered by that auto-spawned zone, not the new one. The two counters below used to
		// be the whole story - covered BY THIS ZONE, or ORPHANED - and a body in that third state
		// (covered, just not by this call) fell through BOTH silently: bodiesNowCovered:0 and
		// bodiesStillWithoutZone:0 together, which reads as "nothing happened" when the true state is
		// "already fine, for a reason unrelated to this call." That is the confidently-uninformative
		// answer this project keeps finding, so the third state is now named rather than left to be
		// inferred from two zeros that do not sum to the body count.
		int32 Covered = 0, Orphaned = 0, CoveredElsewhere = 0;
		TArray<TSharedPtr<FJsonValue>> StillOrphaned;
		TArray<TSharedPtr<FJsonValue>> CoveredElsewhereList;
		for (TActorIterator<AWaterBody> It(World); It; ++It)
		{
			AWaterBody* Body = *It;
			if (!Body) { continue; }
			UWaterBodyComponent* Comp = Body->GetWaterBodyComponent();
			if (Comp) { Comp->UpdateWaterZones(); }
			AWaterZone* Found = Comp ? Comp->GetWaterZone() : nullptr;
			if (Found == Zone) { ++Covered; }
			else if (Found == nullptr)
			{
				++Orphaned;
				StillOrphaned.Add(MakeShared<FJsonValueString>(Body->GetActorLabel()));
			}
			else
			{
				++CoveredElsewhere;
				CoveredElsewhereList.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("%s (zone: %s)"), *Body->GetActorLabel(), *Found->GetActorLabel())));
			}
		}
		Out->SetStringField(TEXT("actorPath"), Zone->GetPathName());
		Out->SetStringField(TEXT("label"), Zone->GetActorLabel());
		Out->SetNumberField(TEXT("bodiesNowCovered"), Covered);
		Out->SetNumberField(TEXT("bodiesStillWithoutZone"), Orphaned);
		Out->SetArrayField(TEXT("stillWithoutZone"), StillOrphaned);
		Out->SetNumberField(TEXT("bodiesCoveredByOtherZone"), CoveredElsewhere);
		Out->SetArrayField(TEXT("coveredByOtherZone"), CoveredElsewhereList);
		if (Orphaned > 0)
		{
			// NAMED, not counted. "three bodies are still invisible" without saying which leaves the
			// caller to go and find them, which is the read this endpoint could just have done.
			Out->SetStringField(TEXT("coverageWarning"), FString::Printf(
				TEXT("%d water body(ies) are still outside every zone and will not render - see "
					 "stillWithoutZone. Move this zone, or give it a larger extentX/extentY."),
				Orphaned));
		}
		if (CoveredElsewhere > 0)
		{
			Out->SetStringField(TEXT("coverageNote"), FString::Printf(
				TEXT("%d water body(ies) already render via a DIFFERENT zone - see coveredByOtherZone. "
					 "This is often create_water_body's own default zone, auto-spawned by the actor "
					 "factory when a body is created with none nearby. This new zone made no difference "
					 "to them; that is not a failure of this call, and bodiesNowCovered correctly "
					 "excludes them."),
				CoveredElsewhere));
		}
		Out->SetStringField(TEXT("note"),
			TEXT("nothing was saved - this zone exists in the open level only. Its far-distance "
				 "material and render target resolution come from the engine's Water editor settings "
				 "through the actor factory, so they are what the placement menu would give you."));
		UE_LOG(LogMifBridge, Log, TEXT("create_water_zone: %s extent %.0f x %.0f, %d body(ies) covered"),
			*Zone->GetActorLabel(), Applied.X, Applied.Y, Covered);
#endif
	}

	// --- set_water_body_spline ------------------------------------------------------------------
	//   in:  { path (alias: actorPath), points[ {x,y,z} ] }
	//   out: { actorPath, splinePoints, splinePointsWorld[] }
	// Bucket: MUTATES the open level. The spline IS the shape of a river or lake.
	void H_set_water_body_spline(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("actorPath"), TEXT("points") },
			TEXT("path (alias: actorPath) - a water body actor; points - an array of {x,y,z} in WORLD "
				 "space, REPLACING the existing spline"),
			{ { TEXT("index"), TEXT("this replaces the WHOLE spline; there is no single-point setter, because ResetSpline is the only engine entry point that rebuilds the body's derived data") },
			  { TEXT("add"), TEXT("there is no append - pass the full point list you want") } }))
		{
			return;
		}
#if !MIF_WITH_WATER
		WaterUnavailable(Out, TEXT("set_water_body_spline"));
#else
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("actorPath") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - list_water_bodies reports actorPath for every body. "
						   "NOTHING was changed."));
			return;
		}
		AWaterBody* Body = FindObject<AWaterBody>(nullptr, *Path);
		if (!Body)
		{
			Fail(Out, FString::Printf(
				TEXT("no water body at '%s'. This resolves by PATH, not by label. NOTHING was "
					 "changed."), *Path));
			return;
		}

		const TArray<TSharedPtr<FJsonValue>>* PointsArr = nullptr;
		if (!JArray(In, TEXT("points"), PointsArr) || !PointsArr)
		{
			Fail(Out, TEXT("points is required - an array of {x,y,z} in world space. NOTHING was "
						   "changed."));
			return;
		}

		int32 PointsSet = 0;
		FString Why;
		if (!WaterApplySpline(Body, *PointsArr, PointsSet, Why))
		{
			Fail(Out, FString::Printf(TEXT("%s NOTHING was changed."), *Why));
			return;
		}

		WaterBodySummary(Body, Out);
		Out->SetNumberField(TEXT("splinePointsSet"), PointsSet);
		// READ BACK rather than echo. ResetSpline rebuilds the body's derived data and the engine is
		// entitled to reject or collapse points; the house rule is that a mutation without a read-back
		// is not done.
		if (UWaterSplineComponent* Spline = Body->GetWaterSpline())
		{
			const int32 Actual = Spline->GetNumberOfSplinePoints();
			if (Actual != PointsSet)
			{
				Out->SetStringField(TEXT("splineNote"), FString::Printf(
					TEXT("%d point(s) were sent and the spline now holds %d. The engine rebuilds "
						 "derived data on ResetSpline and may collapse coincident points."),
					PointsSet, Actual));
			}
		}
		UE_LOG(LogMifBridge, Log, TEXT("set_water_body_spline: %s -> %d point(s)"),
			*Body->GetActorLabel(), PointsSet);
#endif
	}

}
