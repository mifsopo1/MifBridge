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
					TEXT("a water body spline needs at least 2 points and %d were given. One point is a "
						 "degenerate spline: the engine accepts it and renders nothing."), In.Num());
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
		FActorSpawnParameters Params;
		Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
		AWaterBody* Body = Cast<AWaterBody>(World->SpawnActor(SpawnClass, &Loc, nullptr, Params));
		if (!Body)
		{
			Fail(Out, FString::Printf(
				TEXT("spawning %s returned nothing and the engine reported no reason. NOTHING was "
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
		Out->SetStringField(TEXT("note"),
			TEXT("nothing was saved - this body exists in the open level only. A body needs an "
				 "AWaterZone covering it to render at all (see waterZone above), and a water material "
				 "assigned to its component."));
		UE_LOG(LogMifBridge, Log, TEXT("create_water_body: %s '%s' with %d spline point(s)"),
			*TypeStr, *Body->GetActorLabel(), PointsSet);
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
