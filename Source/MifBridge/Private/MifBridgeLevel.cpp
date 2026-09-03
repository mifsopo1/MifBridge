// MifBridge — LEVEL and ACTOR editing (the placed-actor half of the editor, not the Blueprint half).
//
// Everything else in the bridge edits Blueprint ASSETS. This file edits the current level: what is
// actually placed in the world, where it sits, and its per-instance property overrides.
//
// The audit finding that motivated this: set_property ALREADY edits a placed actor correctly once
// you have its path — moving, retargeting and reconfiguring a placed actor all work today. What was
// missing was DISCOVERY. ULevel::Actors has no UPROPERTY, so generic reflection cannot enumerate the
// world, and the only route was `MAP LOAD` + `obj list` through run_console, scraping the log for
// names that came back without paths or transforms. So the whole value here is returning actorPath.
//
// UEditorActorSubsystem is UnrealEd/Public — already a dependency, no Build.cs change.
//
// SCOPE: this operates on the level currently open in the editor. It does not load maps (use
// run_console "MAP LOAD" for that) and it will not touch base-game COOKED maps, which cannot be
// resaved — see docs/02_GOTCHAS.md.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Editor.h"                                   // GEditor
#include "EditorActorFolders.h"
#include "Components/StaticMeshComponent.h"            // spawn_actor_in_level's mesh:/staticMesh: param
#include "Engine/Level.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "Components/SceneComponent.h"   // GetAttachSocketName / DoesSocketExist for attach_actor
#include "Subsystems/EditorActorSubsystem.h"
#include "Editor/GroupActor.h"                   // group_actors: AGroupActor
#include "ActorGroupingUtils.h"                  // group_actors: the engine's own verb
#include "ScopedTransaction.h"
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
	namespace
	{
		UEditorActorSubsystem* ActorSubsystem(const TSharedRef<FJsonObject>& Out)
		{
			UEditorActorSubsystem* Subsystem = GEditor ? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
			if (!Subsystem)
			{
				Fail(Out, TEXT("EditorActorSubsystem unavailable (no editor?)"));
			}
			return Subsystem;
		}

		// Actors are addressed by their full object path. GetActorReference resolves one; it is the
		// counterpart to the actorPath every endpoint here returns.
	}   // end anonymous namespace - ResolveActor is SHARED (declared in MifBridgeHandlers.h) so that
		// there is exactly one actor resolver. MifBridgeStreaming's Data Layer membership needs it,
		// and a second copy written without this function's hard-won fallback silently fails on every
		// World Partition actor path list_level_actors reports.

	AActor* ResolveActor(UEditorActorSubsystem* Subsystem, const TSharedRef<FJsonObject>& In,
		const TSharedRef<FJsonObject>& Out)
	{
			const FString Path = JStrAny(In, { TEXT("actorPath"), TEXT("actor"), TEXT("path") });
			if (Path.IsEmpty())
			{
				Fail(Out, TEXT("actorPath is required (from list_level_actors / spawn_actor_in_level)"));
				return nullptr;
			}
			AActor* Actor = Subsystem->GetActorReference(Path);
			if (!Actor)
			{
				// Fall back to a label/name scan — a caller who has only seen the World Outliner will
				// naturally reach for the label, and failing on that is a needless round trip.
				for (AActor* Candidate : Subsystem->GetAllLevelActors())
				{
					// GetPathName() MUST be here: list_level_actors emits full paths, and without this
					// the very paths it hands you could not be resolved back — delete/transform by
					// path silently failed while the same call by label worked.
					if (Candidate && (Candidate->GetPathName() == Path
						|| Candidate->GetActorLabel() == Path || Candidate->GetName() == Path))
					{
						return Candidate;
					}
				}
			Fail(Out, FString::Printf(
				TEXT("actor not found: '%s' (expects the full actorPath; label/name also accepted if unique)"), *Path));
		}
		return Actor;
	}

	namespace
	{

		void SerializeTransformValueInto(const TSharedRef<FJsonObject>& J, const FTransform& T)
		{
			const FVector Loc = T.GetLocation();
			const FRotator Rot = T.Rotator();
			const FVector Scale = T.GetScale3D();
			auto Vec = [](double X, double Y, double Z)
			{
				TSharedRef<FJsonObject> V = MakeShared<FJsonObject>();
				V->SetNumberField(TEXT("x"), X); V->SetNumberField(TEXT("y"), Y); V->SetNumberField(TEXT("z"), Z);
				return V;
			};
			J->SetObjectField(TEXT("location"), Vec(Loc.X, Loc.Y, Loc.Z));
			J->SetObjectField(TEXT("rotation"), Vec(Rot.Pitch, Rot.Yaw, Rot.Roll));
			J->SetObjectField(TEXT("scale"), Vec(Scale.X, Scale.Y, Scale.Z));
		}

		void SerializeTransformInto(const TSharedRef<FJsonObject>& J, const AActor* Actor)
		{
			const FVector Loc = Actor->GetActorLocation();
			const FRotator Rot = Actor->GetActorRotation();
			const FVector Scale = Actor->GetActorScale3D();
			auto Vec = [](double X, double Y, double Z)
			{
				TSharedRef<FJsonObject> V = MakeShared<FJsonObject>();
				V->SetNumberField(TEXT("x"), X); V->SetNumberField(TEXT("y"), Y); V->SetNumberField(TEXT("z"), Z);
				return V;
			};
			J->SetObjectField(TEXT("location"), Vec(Loc.X, Loc.Y, Loc.Z));
			J->SetObjectField(TEXT("rotation"), Vec(Rot.Pitch, Rot.Yaw, Rot.Roll));
			J->SetObjectField(TEXT("scale"), Vec(Scale.X, Scale.Y, Scale.Z));
		}

		TSharedRef<FJsonObject> SerializeActor(const AActor* Actor)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("actorPath"), Actor->GetPathName());   // the handle everything else takes
			J->SetStringField(TEXT("name"), Actor->GetName());
			J->SetStringField(TEXT("label"), Actor->GetActorLabel());     // what the World Outliner shows
			J->SetStringField(TEXT("class"), Actor->GetClass()->GetPathName());
			const FName Folder = Actor->GetFolderPath();
			if (!Folder.IsNone())
			{
				J->SetStringField(TEXT("folder"), Folder.ToString());
			}
			SerializeTransformInto(J, Actor);

			// HIERARCHY. Added 2026-08-30 with attach_actor, and it belongs HERE rather than on one
			// endpoint because SerializeActor is the shared body of get_level_actor,
			// list_level_actors and four other responses - so every one of them gained the read half
			// at once. Without it an agent could attach actors and then had no way to see that it
			// had, which is the read/write asymmetry this project keeps finding on the other side.
			//
			// GetAttachParentActor walks up through components, so it answers "which ACTOR am I
			// parented to" rather than "which component", which is the question a caller holding
			// actorPaths is actually asking.
			if (const AActor* Parent = Actor->GetAttachParentActor())
			{
				J->SetStringField(TEXT("attachParent"), Parent->GetPathName());
				if (const USceneComponent* Root = Actor->GetRootComponent())
				{
					const FName Socket = Root->GetAttachSocketName();
					if (!Socket.IsNone())
					{
						J->SetStringField(TEXT("attachSocket"), Socket.ToString());
					}
				}
			}
			TArray<AActor*> Children;
			Actor->GetAttachedActors(Children);
			if (Children.Num() > 0)
			{
				TArray<TSharedPtr<FJsonValue>> Kids;
				for (const AActor* Child : Children)
				{
					if (Child)
					{
						Kids.Add(MakeShared<FJsonValueString>(Child->GetPathName()));
					}
				}
				J->SetArrayField(TEXT("attachedChildren"), Kids);
			}
			return J;
		}

		// The local ReadVector is GONE. It is MifBridge::ReadVectorField / ReadRotatorField /
		// ReadScaleField now (declared in MifBridgeHandlers.h, defined once in MifBridgeCommon.cpp).
		// It was the site of Batch L defect 1: `JNum(Obj, TEXT("x"), OutVec.X)` returned the fallback
		// for a component that was PRESENT but not a number, so
		// set_actor_transform {location:{"x":"not-a-number","y":123,"z":456}} answered ok:true and
		// left the actor at {700,123,456} — y and z applied, x quietly kept, and the response echoed
		// the mixture as if it had been asked for. The array branch was worse: FJsonValue::AsNumber()
		// returns 0.0 for a string and cannot report that it did. Three sibling copies existed
		// elsewhere (World/Components/Authoring); do not write a fourth.
	}

	// --- get_level_actor ----------------------------------------------------
	//   in:  { actorPath | actor | path }
	//   out: { actor:{ actorPath, name, label, class, location, rotation, scale, ... } }
	//
	// The plural lister existed and this did not, so re-reading an actor you already have a handle for
	// meant list_level_actors with nameContains and a client-side filter - a scan of every actor in
	// the level to answer a question about one of them. Same shape as one element of that listing.
	void H_get_level_actor(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("path") },
			TEXT("actorPath (aliases: actor, path)"),
			{ { TEXT("actorPaths"), TEXT("this reads ONE actor — for several, use list_level_actors, which is a single call over the whole level") },
			  { TEXT("nameContains"), TEXT("that is list_level_actors' filter; this endpoint takes one exact handle (a label or object name is accepted too, if unique)") } }))
		{
			return;
		}
		UEditorActorSubsystem* Subsystem = ActorSubsystem(Out);
		if (!Subsystem)
		{
			return;
		}
		AActor* Actor = ResolveActor(Subsystem, In, Out);
		if (!Actor)
		{
			return;   // ResolveActor has already said what it could not find
		}
		// The echoed actorPath is what disambiguates a label lookup: ResolveActor's fallback takes the
		// first actor whose label matches, and two actors may share a label. The caller can see which
		// one it got rather than having to trust that the label was unique.
		Out->SetObjectField(TEXT("actor"), SerializeActor(Actor));
	}

	// --- list_level_actors --------------------------------------------------
	//   in:  { classFilter?, nameContains?, folder?, selectedOnly?, limit? }
	//   out: { world, count, truncated, actors:[{actorPath, name, label, class, folder, location, rotation, scale}] }
	void H_list_level_actors(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// THE DEFAULT LIMIT IS NAMED IN THE SUMMARY ON PURPOSE, and this comment sits ABOVE the call
		// rather than between its arguments: harvest_param_table reads the accepted-summary argument
		// verbatim, so a comment placed inside the call is swept into the generated table in place of
		// the summary - which does not compile. Found by regenerating and reading the row.
		//
		// The response has always been honest - count, matched and truncated are all reported - but a
		// caller reading only actors[] gets a silently short list, and that once had a cleanup routine
		// report "cleared 200/200" while 43 actors remained (docs/06, second sequence, entry 3).
		// Saying it here puts it where describe_endpoint shows it, BEFORE the first call.
		if (RejectUnknownParams(In, Out,
			{ TEXT("classFilter"), TEXT("nameContains"), TEXT("folder"), TEXT("selectedOnly"), TEXT("limit") },
			TEXT("classFilter, nameContains, folder, selectedOnly, limit (DEFAULT 200 - the "
				 "response reports count, matched and truncated; reading only actors[] gives a "
				 "silently short list, so check truncated or raise limit)"),
			{ { TEXT("class"), TEXT("the filter key here is 'classFilter' — a substring matched against the whole ancestry, not an exact class path") },
			  { TEXT("labelContains"), TEXT("use nameContains — it matches the object name AND the Outliner label ('labelContains' is snap_actors_to_ground's key)") },
			  { TEXT("filter"), TEXT("use nameContains ('filter'/'nameFilter' are the property-listing endpoints' aliases, not this one's)") } }))
		{
			return;
		}
		UEditorActorSubsystem* Subsystem = ActorSubsystem(Out);
		if (!Subsystem)
		{
			return;
		}
		UWorld* World = EditorWorld();
		if (!World)
		{
			Fail(Out, TEXT("no editor world is open"));
			return;
		}

		const FString ClassFilter  = JStr(In, TEXT("classFilter"));
		const FString NameContains = JStr(In, TEXT("nameContains"));
		const FString FolderFilter = JStr(In, TEXT("folder"));
		const bool bSelectedOnly   = JBool(In, TEXT("selectedOnly"), false);
		const int32 Limit = FMath::Clamp(JInt(In, TEXT("limit"), 200), 1, 5000);

		// A class filter is matched by NAME against the whole ancestry, so classFilter="StaticMeshActor"
		// finds subclasses too without the caller needing an exact path.
		TArray<AActor*> Actors = bSelectedOnly ? Subsystem->GetSelectedLevelActors() : Subsystem->GetAllLevelActors();

		TArray<TSharedPtr<FJsonValue>> Arr;
		bool bTruncated = false;
		int32 Matched = 0;
		for (AActor* Actor : Actors)
		{
			if (!Actor || !IsValid(Actor))
			{
				continue;
			}
			if (!ClassFilter.IsEmpty())
			{
				bool bClassMatch = false;
				for (UClass* C = Actor->GetClass(); C; C = C->GetSuperClass())
				{
					if (C->GetName().Contains(ClassFilter)) { bClassMatch = true; break; }
				}
				if (!bClassMatch) { continue; }
			}
			if (!NameContains.IsEmpty()
				&& !Actor->GetName().Contains(NameContains)
				&& !Actor->GetActorLabel().Contains(NameContains))
			{
				continue;
			}
			if (!FolderFilter.IsEmpty() && !Actor->GetFolderPath().ToString().Contains(FolderFilter))
			{
				continue;
			}
			++Matched;
			if (Arr.Num() >= Limit)
			{
				bTruncated = true;
				continue;   // keep counting so the caller learns the real total
			}
			Arr.Add(MakeShared<FJsonValueObject>(SerializeActor(Actor)));
		}

		Out->SetStringField(TEXT("world"), World->GetName());
		// ALWAYS the editor world - this uses EditorWorld(), never the PIE one. Said explicitly because
		// trace_ground next door uses ActiveWorld() and answers about PIE while this answers about the
		// editor, and the two together read as a catastrophe when they simply describe different worlds.
		// The name cannot distinguish them: a PIE world is a duplicate and keeps the same name.
		Out->SetStringField(TEXT("worldType"), World->IsPlayInEditor() ? TEXT("pie") : TEXT("editor"));
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetNumberField(TEXT("matched"), Matched);   // never let a cap look like completeness
		Out->SetBoolField(TEXT("truncated"), bTruncated);
		Out->SetArrayField(TEXT("actors"), Arr);
	}

	// --- spawn_actor_in_level -----------------------------------------------
	//   in:  { actorClass (alias: class), location?, rotation?, scale?, mesh? (alias: staticMesh),
	//          label?, folder? }
	// Transforms are read STRICTLY and BEFORE the spawn: a supplied component that is not a number
	// fails the call rather than becoming 0, and fails it without leaving an actor behind.
	//   out: { actor:{...} }
	//
	// The in: line above used to omit `mesh`/`staticMesh` and the `class` alias for ~60 lines after the
	// code started reading them — the contract comment is what an agent greps, so an undocumented
	// parameter is an unusable one. And with no guard, `material:` was still silently dropped.
	void H_spawn_actor_in_level(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorClass"), TEXT("class"), TEXT("location"), TEXT("rotation"), TEXT("scale"),
			  TEXT("mesh"), TEXT("staticMesh"), TEXT("label"), TEXT("folder") },
			TEXT("actorClass (alias: class), location, rotation, scale, mesh (alias: staticMesh), label, folder"),
			{ { TEXT("material"), TEXT("not supported here — spawn the actor, then set_property on the mesh component's OverrideMaterials") },
			  { TEXT("name"), TEXT("an actor's display name is 'label'; its object name is assigned by the engine") } }))
		{
			return;
		}
		UEditorActorSubsystem* Subsystem = ActorSubsystem(Out);
		if (!Subsystem)
		{
			return;
		}
		// STRICT: no self-fallback. There is no blueprint context here anyway, but an empty class
		// must fail loudly rather than reach SpawnActorFromClass with null.
		FString ClassError;
		UClass* ActorClass = ResolveClassStrict(
			JStrAny(In, { TEXT("actorClass"), TEXT("class") }), nullptr, TEXT("actorClass"), ClassError);
		if (!ActorClass)
		{
			Fail(Out, ClassError);
			return;
		}
		if (!ActorClass->IsChildOf(AActor::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not an Actor class: '%s'"), *ActorClass->GetName()));
			return;
		}
		if (ActorClass->HasAnyClassFlags(CLASS_Abstract))
		{
			Fail(Out, FString::Printf(TEXT("'%s' is abstract and cannot be spawned"), *ActorClass->GetName()));
			return;
		}

		// Read and validate BEFORE spawning: a component the caller supplied and the bridge could not
		// read must never become a default, and failing after the spawn would leave an actor behind.
		FVector Location = FVector::ZeroVector;
		FRotator Rotation = FRotator::ZeroRotator;   // x/y/z = pitch/yaw/roll, as SerializeActor emits
		FVector Scale = FVector::OneVector;
		FString ReadError;
		if (ReadVectorField(In, TEXT("location"), Location, ReadError) == EJsonRead::Invalid
			|| ReadRotatorField(In, TEXT("rotation"), Rotation, ReadError) == EJsonRead::Invalid
			|| ReadScaleField(In, TEXT("scale"), Scale, ReadError) == EJsonRead::Invalid)
		{
			Fail(Out, FString::Printf(TEXT("%s Nothing was spawned."), *ReadError));
			return;
		}
		const bool bHasScale = In->HasField(TEXT("scale"));

		AActor* Actor = Subsystem->SpawnActorFromClass(ActorClass, Location, Rotation, /*bTransient*/ false);
		if (!Actor)
		{
			Fail(Out, FString::Printf(TEXT("SpawnActorFromClass returned null for '%s'"), *ActorClass->GetName()));
			return;
		}

		if (bHasScale)
		{
			Actor->SetActorScale3D(Scale);
		}

		// A mesh path used to be accepted and silently dropped, which spawned an EMPTY
		// StaticMeshActor and reported ok — the caller only found out when get_actor_bounds came
		// back hasBounds:false. Same silent-param-ignore class as the naming traps: honour it, or
		// say why it could not be honoured.
		const FString MeshPath = JStrAny(In, { TEXT("mesh"), TEXT("staticMesh") });
		if (!MeshPath.IsEmpty())
		{
			UStaticMeshComponent* MeshComp = Actor->FindComponentByClass<UStaticMeshComponent>();
			if (!MeshComp)
			{
				Actor->Destroy();
				Fail(Out, FString::Printf(
					TEXT("'%s' has no StaticMeshComponent, so mesh '%s' cannot be applied — spawn a StaticMeshActor instead"),
					*ActorClass->GetName(), *MeshPath));
				return;
			}
			UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, *MeshPath);
			if (!Mesh)
			{
				Actor->Destroy();
				Fail(Out, FString::Printf(TEXT("could not load static mesh '%s'"), *MeshPath));
				return;
			}
			// Spawned StaticMeshActors default to Static mobility, which refuses SetStaticMesh.
			const EComponentMobility::Type OldMobility = MeshComp->Mobility;
			MeshComp->SetMobility(EComponentMobility::Movable);
			MeshComp->SetStaticMesh(Mesh);
			MeshComp->SetMobility(OldMobility);
			if (MeshComp->GetStaticMesh() != Mesh)
			{
				// READ BACK rather than assume. SetStaticMesh returns a bool and refuses when dynamic
				// data changes are not allowed for the component's mobility; the mobility dance above
				// is what makes that usually work, not a guarantee that it did. Reading the mesh back
				// is stronger than testing the bool anyway - that also returns false when the mesh was
				// ALREADY the requested one, which is not a failure.
				//
				// This is the same check spawn_actor_at in MifBridgePIE.cpp already makes on a block
				// that is otherwise identical to this one - and the comment above records that a
				// silently dropped mesh here once produced an empty StaticMeshActor reported as ok.
				// That failure was closed for the ignored-parameter path and left open for the
				// refused-setter path, which lands the caller in exactly the same place.
				Actor->Destroy();
				Fail(Out, FString::Printf(
					TEXT("mesh '%s' did not take on '%s' (the component still holds a different mesh); the ")
					TEXT("actor was destroyed rather than left in the level as an empty one"),
					*MeshPath, *ActorClass->GetName()));
				return;
			}
		}

		const FString Label = JStr(In, TEXT("label"));
		if (!Label.IsEmpty())
		{
			// Report what the actor is really called - the engine may trim it, or refuse it outright
			// and leave the engine-assigned name in place. A caller that files away the label it
			// asked for will not find this actor again.
			FString ActualLabel, LabelNote;
			SetActorLabelChecked(Actor, Label, ActualLabel, LabelNote);
			Out->SetStringField(TEXT("labelRequested"), Label);
			Out->SetStringField(TEXT("labelActual"), ActualLabel);
			if (!LabelNote.IsEmpty()) { Out->SetStringField(TEXT("labelNote"), LabelNote); }
		}
		const FString Folder = JStr(In, TEXT("folder"));
		if (!Folder.IsEmpty())
		{
			Actor->SetFolderPath(FName(*Folder));
		}

		Out->SetObjectField(TEXT("actor"), SerializeActor(Actor));
		// The level is dirty now; it is NOT saved automatically.
		Out->SetStringField(TEXT("note"),
			TEXT("the level is modified but not saved — call save_package on the map's /Game/ path to persist"));
		UE_LOG(LogMifBridge, Log, TEXT("spawn_actor_in_level: %s -> %s"), *ActorClass->GetName(), *Actor->GetPathName());
	}

	// --- set_actor_transform ------------------------------------------------
	//   in:  { actorPath (actor|path), location?, rotation?, scale?, relative? }
	//   out: { actor:{...}, locationApplied, rotationApplied, scaleApplied, relative }
	// location/scale take {x,y,z} or [x,y,z]; rotation additionally takes {pitch,yaw,roll}; scale
	// additionally takes a bare number (uniform). Any omitted component keeps its current value, so
	// this doubles as "move only" — but a component that is SUPPLIED and is not a number is a hard
	// error, not a fallback (Batch L defect 1: {"x":"not-a-number","y":123,"z":456} used to answer
	// ok:true having applied y and z and kept the old x, echoing the mixture as if it were asked for).
	// `relative` applies to location and rotation ONLY — it is a delta, and there is no honest delta
	// for scale (additive and multiplicative are both defensible and mean opposite things at 0). The
	// combination used to be accepted with scale silently treated as absolute; it is refused now.
	void H_set_actor_transform(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		// This endpoint had no strict-params guard at all, which is how a misspelled key joined a
		// mistyped component in producing a transform nobody asked for.
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("location"), TEXT("rotation"),
			  TEXT("scale"), TEXT("relative") },
			TEXT("actorPath (aliases: actor, path), location, rotation, scale, relative"),
			{ { TEXT("transform"), TEXT("pass location / rotation / scale as separate keys") },
			  { TEXT("yaw"), TEXT("rotation accepts {pitch,yaw,roll} or {x,y,z} — there is no bare yaw here") } }))
		{
			return;
		}
		UEditorActorSubsystem* Subsystem = ActorSubsystem(Out);
		if (!Subsystem)
		{
			return;
		}
		AActor* Actor = ResolveActor(Subsystem, In, Out);
		if (!Actor)
		{
			return;
		}

		const FVector  CurLoc = Actor->GetActorLocation();
		const FRotator CurRot = Actor->GetActorRotation();
		const bool bRelative = JBool(In, TEXT("relative"), false);

		// SEEDING. Absolute mode seeds from the actor's CURRENT transform, so an omitted component
		// keeps its value ("move only"). Relative mode seeds from ZERO, because an omitted component
		// means "no delta" — it used to seed from current here too and then ADD current again, so
		// {relative:true, location:{"x":100}} doubled y and z. Same family as defect 1: a transform
		// the caller never asked for, reported back as though they had.
		FVector  Location = bRelative ? FVector::ZeroVector  : CurLoc;
		FRotator Rotation = bRelative ? FRotator::ZeroRotator : CurRot;
		FVector  Scale    = Actor->GetActorScale3D();

		// STRICT, and BEFORE Modify(): a supplied component that is not a number is a hard error
		// naming the field, the value and the expected type. It is never defaulted, and it never
		// half-applies — a partial transform is precisely what defect 1 produced.
		FString ReadError;
		const EJsonRead LocRead   = ReadVectorField(In, TEXT("location"), Location, ReadError);
		if (LocRead == EJsonRead::Invalid) { Fail(Out, FString::Printf(TEXT("%s The actor was NOT moved."), *ReadError)); return; }
		const EJsonRead RotRead   = ReadRotatorField(In, TEXT("rotation"), Rotation, ReadError);
		if (RotRead == EJsonRead::Invalid) { Fail(Out, FString::Printf(TEXT("%s The actor was NOT moved."), *ReadError)); return; }
		const EJsonRead ScaleRead = ReadScaleField(In, TEXT("scale"), Scale, ReadError);
		if (ScaleRead == EJsonRead::Invalid) { Fail(Out, FString::Printf(TEXT("%s The actor was NOT moved."), *ReadError)); return; }

		if (LocRead != EJsonRead::Read && RotRead != EJsonRead::Read && ScaleRead != EJsonRead::Read)
		{
			Fail(Out, TEXT("supply at least one of location / rotation / scale"));
			return;
		}

		if (bRelative)
		{
			if (In->HasField(TEXT("scale")))
			{
				Fail(Out, TEXT("relative:true applies to location and rotation only — it deltas them. There is no ")
					TEXT("unambiguous 'relative scale' (additive vs multiplicative differ), and scale was previously ")
					TEXT("applied as an ABSOLUTE here without saying so. Send scale in a separate call without relative."));
				return;
			}
			// Deltas, not absolutes — the common "nudge it 100 units" case.
			Location = CurLoc + Location;
			Rotation = CurRot + Rotation;
		}

		const FTransform NewTransform(Rotation, Location, Scale);
		Actor->Modify();
		if (!Subsystem->SetActorTransform(Actor, NewTransform))
		{
			Fail(Out, TEXT("SetActorTransform failed (actor may be locked or in a locked level)"));
			return;
		}

		// Which components the CALLER actually supplied, so the echoed transform below can never be
		// read as "all three were applied as requested".
		Out->SetBoolField(TEXT("locationApplied"), LocRead == EJsonRead::Read);
		Out->SetBoolField(TEXT("rotationApplied"), RotRead == EJsonRead::Read);
		Out->SetBoolField(TEXT("scaleApplied"),    ScaleRead == EJsonRead::Read);
		Out->SetBoolField(TEXT("relative"), bRelative);
		Out->SetObjectField(TEXT("actor"), SerializeActor(Actor));
	}

	// --- set_actor_label ----------------------------------------------------
	//   in:  { actorPath, label?, folder? }
	// The label is the World Outliner display name; it is NOT the object name and renaming it is safe.
	void H_set_actor_label(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("label"), TEXT("folder") },
			TEXT("actorPath (aliases: actor, path), label, folder"),
			{ { TEXT("name"), TEXT("the World Outliner display name is 'label'; the object name is engine-assigned and is not renamed here") },
			  { TEXT("newLabel"), TEXT("the key is 'label'") } }))
		{
			return;
		}
		UEditorActorSubsystem* Subsystem = ActorSubsystem(Out);
		if (!Subsystem)
		{
			return;
		}
		AActor* Actor = ResolveActor(Subsystem, In, Out);
		if (!Actor)
		{
			return;
		}
		const FString Label = JStr(In, TEXT("label"));
		const FString Folder = JStr(In, TEXT("folder"));
		if (Label.IsEmpty() && Folder.IsEmpty())
		{
			Fail(Out, TEXT("supply label and/or folder"));
			return;
		}
		Actor->Modify();

		// SetActorLabel is void: it trims the name, validates it, and on rejection logs a warning and
		// changes NOTHING. Echoing the requested label back would report the caller's own input as a
		// fact, and every later lookup by that label would miss. Read it back instead.
		FString ActualLabel, LabelNote;
		bool bLabelOk = true;
		if (!Label.IsEmpty())
		{
			bLabelOk = SetActorLabelChecked(Actor, Label, ActualLabel, LabelNote);
			Out->SetStringField(TEXT("labelRequested"), Label);
			Out->SetStringField(TEXT("labelActual"), ActualLabel);
			if (!LabelNote.IsEmpty()) { Out->SetStringField(TEXT("labelNote"), LabelNote); }
		}
		if (!Folder.IsEmpty()) { Actor->SetFolderPath(FName(*Folder)); }
		Out->SetObjectField(TEXT("actor"), SerializeActor(Actor));

		// Renaming IS this endpoint's job. Reporting ok for a rename that did not happen is the
		// defect; the folder change (if any) has already been applied and is left in place.
		if (!bLabelOk)
		{
			Fail(Out, LabelNote);
			return;
		}
	}

	// --- delete_level_actor -------------------------------------------------
	//   in:  { actorPath, confirm: true }
	void H_delete_level_actor(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("confirm") },
			TEXT("actorPath (aliases: actor, path), confirm (must be true)"),
			{ { TEXT("force"), TEXT("the confirmation key is 'confirm' and it must be true") },
			  { TEXT("actorPaths"), TEXT("this deletes ONE actor — call it once per actor; 'actorPaths' is select_level_actors' key") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("delete_level_actor requires confirm=true"));
			return;
		}
		UEditorActorSubsystem* Subsystem = ActorSubsystem(Out);
		if (!Subsystem)
		{
			return;
		}
		AActor* Actor = ResolveActor(Subsystem, In, Out);
		if (!Actor)
		{
			return;
		}
		const FString Path = Actor->GetPathName();
		const FString Label = Actor->GetActorLabel();
		if (!Subsystem->DestroyActor(Actor))
		{
			Fail(Out, TEXT("DestroyActor failed (actor may be locked, or in a locked/streamed-out level)"));
			return;
		}
		Out->SetStringField(TEXT("removed"), Path);
		Out->SetStringField(TEXT("label"), Label);
		UE_LOG(LogMifBridge, Log, TEXT("delete_level_actor: %s"), *Path);
	}

	// --- select_level_actors ------------------------------------------------
	//   in:  { actorPaths?: [...], clear?: true }
	// Selection drives the editor's own tooling (and gizmos), so being able to set it programmatically
	// is what lets a human take over mid-task without hunting for the actor.
	void H_select_level_actors(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPaths"), TEXT("clear") },
			TEXT("actorPaths (array of full actor paths), clear"),
			{ { TEXT("actorPath"), TEXT("the key here is the PLURAL 'actorPaths' and it takes an array — pass [path] for a single actor") },
			  { TEXT("actors"), TEXT("the key is 'actorPaths'") } }))
		{
			return;
		}
		UEditorActorSubsystem* Subsystem = ActorSubsystem(Out);
		if (!Subsystem)
		{
			return;
		}
		if (JBool(In, TEXT("clear"), false))
		{
			Subsystem->ClearActorSelectionSet();
		}

		const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
		int32 Selected = 0;
		TArray<TSharedPtr<FJsonValue>> Missing;
		if (JArray(In, TEXT("actorPaths"), Arr) && Arr)
		{
			for (const TSharedPtr<FJsonValue>& V : *Arr)
			{
				FString Path;
				if (!V.IsValid() || !V->TryGetString(Path) || Path.IsEmpty())
				{
					continue;
				}
				if (AActor* Actor = Subsystem->GetActorReference(Path))
				{
					Subsystem->SetActorSelectionState(Actor, true);
					++Selected;
				}
				else
				{
					Missing.Add(MakeShared<FJsonValueString>(Path));
				}
			}
		}

		Out->SetNumberField(TEXT("selected"), Selected);
		if (Missing.Num() > 0)
		{
			Out->SetArrayField(TEXT("notFound"), Missing);
		}
		TArray<TSharedPtr<FJsonValue>> Current;
		for (AActor* Actor : Subsystem->GetSelectedLevelActors())
		{
			if (Actor) { Current.Add(MakeShared<FJsonValueString>(Actor->GetPathName())); }
		}
		Out->SetArrayField(TEXT("selection"), Current);
	}

	// --- attach_actor -------------------------------------------------------
	//   in:  { child, parent, socket?, keepWorldTransform? }
	//   out: { child, parent, socket, keptWorldTransform, transformBefore, transformAfter, ... }
	//
	// WHAT WAS MISSING. An agent could spawn a door, a handle and a sign and place all three, and
	// had no way to make them one movable object - moving the parent left the children behind. The
	// read half was missing too: SerializeActor reported transform and folder and nothing about
	// hierarchy, so even an attachment made by hand in the Outliner was invisible over the bridge.
	// Both halves land together here.
	//
	// THERE WAS A WORKAROUND AND IT IS WORTH SAYING WHY IT IS NOT ENOUGH: select_level_actors plus
	// invoke_editor_command{command:"AttachSelectedActors"} does attach. But it cannot name a
	// SOCKET, it takes the parent IMPLICITLY from the last element of the selection set
	// (EditorActor.cpp), and when the engine refuses it there is nothing to report - the command
	// returns nothing either way. This is the same operation addressed by path, with the refusal
	// surfaced.
	//
	// TWO ENGINE ROUTES, and which one runs depends on keepWorldTransform - found by reading
	// EditorEngine.cpp rather than assuming one call covers both:
	//   keepWorldTransform:true (default) -> GEditor->ParentActors, the Outliner's own path. It
	//     hardcodes KeepWorldTransform internally, which is exactly what is wanted here.
	//   keepWorldTransform:false -> AActor::AttachToActor with SnapToTarget. ParentActors CANNOT
	//     express this; asking it to would silently keep the world transform and report success.
	//
	// CanParentActors is called FIRST purely to surface its ReasonText. ParentActors calls it
	// internally too and then silently NO-OPS on refusal - which is the failure mode this endpoint
	// exists to stop being silent.
	void H_attach_actor(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("child"), TEXT("parent"), TEXT("socket"), TEXT("keepWorldTransform") },
			TEXT("child (actorPath of the actor to be parented), parent (actorPath to parent it TO), ")
			TEXT("socket (optional socket or bone name on the parent), keepWorldTransform (default ")
			TEXT("true - the child stays where it is on screen; false snaps it onto the parent)"),
			{ { TEXT("actorPath"), TEXT("this endpoint takes TWO actors - spell them child and parent") },
			  { TEXT("attachTo"),  TEXT("spell it parent") },
			  { TEXT("target"),    TEXT("spell it parent") } }))
		{
			return;
		}

		UEditorActorSubsystem* Subsystem = GEditor ? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
		if (!Subsystem)
		{
			Fail(Out, TEXT("no EditorActorSubsystem. NOTHING was changed."));
			return;
		}

		const FString ChildPath = JStr(In, TEXT("child"));
		const FString ParentPath = JStr(In, TEXT("parent"));
		if (ChildPath.IsEmpty() || ParentPath.IsEmpty())
		{
			Fail(Out, TEXT("both child and parent are required, as actorPaths from list_level_actors. ")
				TEXT("NOTHING was changed."));
			return;
		}

		TSharedRef<FJsonObject> ChildIn = MakeShared<FJsonObject>();
		ChildIn->SetStringField(TEXT("actorPath"), ChildPath);
		AActor* Child = ResolveActor(Subsystem, ChildIn, Out);
		if (!Child) { return; }

		TSharedRef<FJsonObject> ParentIn = MakeShared<FJsonObject>();
		ParentIn->SetStringField(TEXT("actorPath"), ParentPath);
		AActor* Parent = ResolveActor(Subsystem, ParentIn, Out);
		if (!Parent) { return; }

		if (Child == Parent)
		{
			Fail(Out, FString::Printf(TEXT("'%s' cannot be attached to itself. NOTHING was changed."),
				*Child->GetActorLabel()));
			return;
		}

		// A CYCLE, checked here rather than left to the engine. CanParentActors does reject one, but
		// walking it ourselves lets the refusal name the actor that closes the loop instead of
		// reporting a generic reason.
		for (const AActor* Up = Parent; Up != nullptr; Up = Up->GetAttachParentActor())
		{
			if (Up == Child)
			{
				Fail(Out, FString::Printf(
					TEXT("attaching '%s' to '%s' would make a cycle - '%s' is already somewhere above ")
					TEXT("'%s' in the attachment chain. NOTHING was changed."),
					*Child->GetActorLabel(), *Parent->GetActorLabel(),
					*Child->GetActorLabel(), *Parent->GetActorLabel()));
				return;
			}
		}

		const FName Socket(*JStr(In, TEXT("socket")));
		if (!Socket.IsNone())
		{
			const USceneComponent* ParentRoot = Parent->GetRootComponent();
			if (!ParentRoot || !ParentRoot->DoesSocketExist(Socket))
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' has no socket or bone named '%s' on its root component. Attaching to a ")
					TEXT("socket that does not exist silently falls back to the component origin, ")
					TEXT("which looks like it worked. NOTHING was changed."),
					*Parent->GetActorLabel(), *Socket.ToString()));
				return;
			}
		}

		FText Reason;
		if (GEditor && !GEditor->CanParentActors(Parent, Child, &Reason))
		{
			Fail(Out, FString::Printf(
				TEXT("the editor refuses to parent '%s' to '%s': %s. NOTHING was changed."),
				*Child->GetActorLabel(), *Parent->GetActorLabel(),
				Reason.IsEmpty() ? TEXT("no reason given") : *Reason.ToString()));
			return;
		}

		const bool bKeepWorld = JBool(In, TEXT("keepWorldTransform"), true);
		const FTransform Before = Child->GetActorTransform();
		const AActor* PriorParent = Child->GetAttachParentActor();

		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_AttachActor", "Attach Actor"));
		Child->Modify();
		Parent->Modify();

		if (bKeepWorld)
		{
			GEditor->ParentActors(Parent, Child, Socket);
		}
		else
		{
			// ParentActors hardcodes KeepWorldTransform, so this path cannot go through it.
			Child->AttachToActor(Parent, FAttachmentTransformRules::SnapToTargetIncludingScale, Socket);
		}

		// READ BACK. Both routes return void, and ParentActors specifically no-ops in silence when
		// the engine declines - so the postcondition is asked for rather than assumed.
		const AActor* NowParent = Child->GetAttachParentActor();
		if (NowParent != Parent)
		{
			Fail(Out, FString::Printf(
				TEXT("the attach reported no error and '%s' is %s. NOTHING usable was produced."),
				*Child->GetActorLabel(),
				NowParent ? *FString::Printf(TEXT("attached to '%s' instead"), *NowParent->GetActorLabel())
				          : TEXT("still not attached to anything")));
			return;
		}

		const FTransform After = Child->GetActorTransform();
		Out->SetStringField(TEXT("child"), Child->GetPathName());
		Out->SetStringField(TEXT("parent"), Parent->GetPathName());
		Out->SetStringField(TEXT("socket"), Socket.IsNone() ? FString() : Socket.ToString());
		Out->SetBoolField(TEXT("keptWorldTransform"), bKeepWorld);
		Out->SetBoolField(TEXT("attached"), true);
		if (PriorParent)
		{
			Out->SetStringField(TEXT("reparentedFrom"), PriorParent->GetPathName());
		}
		TSharedRef<FJsonObject> BeforeJ = MakeShared<FJsonObject>();
		TSharedRef<FJsonObject> AfterJ = MakeShared<FJsonObject>();
		SerializeTransformValueInto(BeforeJ, Before);
		SerializeTransformValueInto(AfterJ, After);
		Out->SetObjectField(TEXT("transformBefore"), BeforeJ);
		Out->SetObjectField(TEXT("transformAfter"), AfterJ);
		if (bKeepWorld && !After.Equals(Before, 0.01f))
		{
			Out->SetStringField(TEXT("transformNote"),
				TEXT("keepWorldTransform was true but the world transform still moved. That is worth ")
				TEXT("looking at - it usually means the parent has a non-uniform scale, which cannot ")
				TEXT("be preserved exactly through an attachment."));
		}
		Out->SetStringField(TEXT("levelNote"),
			TEXT("the level is now dirty and NOTHING has been saved. On a cooked base-game map it ")
			TEXT("cannot be resaved at all - the attachment lives until the editor closes."));
	}

	// --- detach_actor -------------------------------------------------------
	//   in:  { actorPath, keepWorldTransform? }
	//   out: { actorPath, detachedFrom, transformAfter }
	void H_detach_actor(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("keepWorldTransform") },
			TEXT("actorPath (aliases: actor, path) of the CHILD to detach; keepWorldTransform ")
			TEXT("(default true - it stays where it is on screen rather than snapping back)"),
			{ { TEXT("child"), TEXT("spell it actorPath - detach takes only the child") },
			  { TEXT("parent"), TEXT("not accepted - detach_actor detaches the named actor from ")
			                    TEXT("whatever it is attached to") } }))
		{
			return;
		}

		UEditorActorSubsystem* Subsystem = GEditor ? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
		if (!Subsystem)
		{
			Fail(Out, TEXT("no EditorActorSubsystem. NOTHING was changed."));
			return;
		}
		AActor* Actor = ResolveActor(Subsystem, In, Out);
		if (!Actor) { return; }

		AActor* Parent = Actor->GetAttachParentActor();
		if (!Parent)
		{
			// Not a failure: the end state the caller asked for already holds. Same shape as
			// add_gameplay_tag's already-exists path - "it is detached" and "I detached it" stay
			// distinguishable through detached:false.
			Out->SetStringField(TEXT("actorPath"), Actor->GetPathName());
			Out->SetBoolField(TEXT("detached"), false);
			Out->SetBoolField(TEXT("wasAttached"), false);
			Out->SetStringField(TEXT("note"),
				TEXT("this actor was not attached to anything - nothing was done, and nothing needed ")
				TEXT("to be. detached:false with wasAttached:false means the end state you asked for ")
				TEXT("is already in place."));
			return;
		}

		const bool bKeepWorld = JBool(In, TEXT("keepWorldTransform"), true);
		const FTransform Before = Actor->GetActorTransform();

		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_DetachActor", "Detach Actor"));
		Actor->Modify();
		Actor->DetachFromActor(bKeepWorld ? FDetachmentTransformRules::KeepWorldTransform
		                                  : FDetachmentTransformRules::KeepRelativeTransform);

		if (Actor->GetAttachParentActor() != nullptr)
		{
			Fail(Out, FString::Printf(
				TEXT("DetachFromActor reported no error but '%s' is still attached to '%s'."),
				*Actor->GetActorLabel(), *Actor->GetAttachParentActor()->GetActorLabel()));
			return;
		}

		TSharedRef<FJsonObject> AfterJ = MakeShared<FJsonObject>();
		SerializeTransformValueInto(AfterJ, Actor->GetActorTransform());
		Out->SetStringField(TEXT("actorPath"), Actor->GetPathName());
		Out->SetBoolField(TEXT("detached"), true);
		Out->SetBoolField(TEXT("wasAttached"), true);
		Out->SetStringField(TEXT("detachedFrom"), Parent->GetPathName());
		Out->SetBoolField(TEXT("keptWorldTransform"), bKeepWorld);
		Out->SetObjectField(TEXT("transformAfter"), AfterJ);
		Out->SetStringField(TEXT("levelNote"),
			TEXT("the level is now dirty and NOTHING has been saved."));
	}

	// --- group_actors / ungroup_actors ---------------------------------------------------------
	//   in:  { actorPaths[] (alias actors), enableGrouping }
	//   out: { group, members[], memberCount, groupingWasActive, ... }
	//
	// WHAT THIS IS. The editor's Ctrl+G. An AGroupActor is an editor-only actor that owns a flat
	// list of other actors so a person can select and move a multi-part prop as one thing. An agent
	// could already spawn twenty pieces of a building and had no way to hand the result to a human
	// as one object - every piece stayed separately clickable, which is the difference between
	// delivering a prop and delivering a pile of parts.
	//
	// GROUPING IS NOT ATTACHMENT, and the distinction is why both exist. attach_actor builds a
	// parent/child transform hierarchy that survives cooking and drives runtime movement. A group
	// is flat, editor-only, and stripped from a cook - it changes what a click selects and nothing
	// else. Asking for one when you meant the other is silent in both directions.
	//
	// UActorGroupingUtils::GroupActors RETURNS nullptr AND SAYS NOTHING in four distinct cases, and
	// diagnosing which one is most of this endpoint. Read out of ActorGroupingUtils.cpp:51-100
	// rather than guessed:
	//
	//   * grouping mode is OFF        IsGroupingActive() is a global editor toggle. With it off the
	//                                 whole body is skipped and you get nullptr. This is the case
	//                                 that reads as "the bridge is broken" - nothing about the
	//                                 actors is wrong.
	//   * actors span two levels      the engine breaks out of its scan on the first mismatch.
	//   * fewer than two groupable    FinalActorList.Num() > 1 is required. A group of one is not
	//                                 something the editor makes.
	//   * every actor WAS a group     AGroupActor instances are filtered out of the candidate list,
	//                                 so passing only groups leaves nothing to group.
	//
	// Every one is checked HERE, before the engine call, so the refusal names the cause instead of
	// reporting that nothing happened.
	void H_group_actors(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPaths"), TEXT("actors"), TEXT("enableGrouping") },
			TEXT("actorPaths[] (alias actors) - two or more actors in the SAME level; ")
			TEXT("enableGrouping:true - switch the editor's grouping mode on if it is off. That is ")
			TEXT("a persistent editor setting, so it is never changed implicitly"),
			{ { TEXT("name"), TEXT("an AGroupActor is not named at creation - group first, then set_actor_label on the group this returns") },
			  { TEXT("group"), TEXT("that is ungroup_actors' key; this endpoint CREATES a group out of actorPaths[]") },
			  { TEXT("parent"), TEXT("grouping is not attachment - attach_actor is the parent/child verb and survives a cook, a group is a flat editor-only selection aid") },
			  { TEXT("folder"), TEXT("a folder is an Outliner tree path, not a group - the two are independent, and there is no endpoint that SETS one today; list_level_actors filters by folder but nothing assigns it") } }))
		{
			return;
		}

		const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
		if ((!In->TryGetArrayField(TEXT("actorPaths"), Arr)
			 && !In->TryGetArrayField(TEXT("actors"), Arr)) || !Arr || Arr->Num() == 0)
		{
			Fail(Out, TEXT("actorPaths[] is required and must be non-empty. list_level_actors ")
				TEXT("reports the paths this takes. NOTHING was grouped."));
			return;
		}

		UEditorActorSubsystem* ActorSub =
			GEditor ? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
		if (!ActorSub)
		{
			Fail(Out, TEXT("no EditorActorSubsystem - this is not a running editor. NOTHING was ")
				TEXT("grouped."));
			return;
		}

		// GROUPING MODE FIRST, because it is the only failure that has nothing to do with the
		// actors and would otherwise be diagnosed as one of the others.
		const bool bWasActive = UActorGroupingUtils::IsGroupingActive();
		Out->SetBoolField(TEXT("groupingWasActive"), bWasActive);
		if (!bWasActive)
		{
			if (!JBool(In, TEXT("enableGrouping"), false))
			{
				Fail(Out, TEXT("the editor's GROUPING MODE is off, so GroupActors is a no-op and ")
					TEXT("would have returned nothing with no error - the actors are fine. Pass ")
					TEXT("enableGrouping:true to switch it on. It is a persistent editor setting ")
					TEXT("that also governs whether clicking one member selects the whole group, ")
					TEXT("which is why this endpoint will not flip it behind you. NOTHING was ")
					TEXT("grouped."));
				return;
			}
			UActorGroupingUtils::SetGroupingActive(true);
			Out->SetBoolField(TEXT("groupingModeEnabled"), true);
		}

		TArray<AActor*> ToGroup;
		TArray<TSharedPtr<FJsonValue>> NotFound;
		TArray<TSharedPtr<FJsonValue>> WereGroups;
		for (const TSharedPtr<FJsonValue>& V : *Arr)
		{
			FString Path;
			if (!V.IsValid() || !V->TryGetString(Path) || Path.IsEmpty())
			{
				NotFound.Add(MakeShared<FJsonValueString>(TEXT("(non-string entry)")));
				continue;
			}
			TSharedRef<FJsonObject> One = MakeShared<FJsonObject>();
			One->SetStringField(TEXT("actorPath"), Path);
			AActor* Actor = ResolveActor(ActorSub, One, Out);
			if (!Actor)
			{
				NotFound.Add(MakeShared<FJsonValueString>(Path));
				continue;
			}
			// The engine drops these from the candidate list without comment. Counted separately so
			// "you passed three things and all three were groups" is a sentence this can say.
			if (Actor->IsA(AGroupActor::StaticClass()))
			{
				WereGroups.Add(MakeShared<FJsonValueString>(Path));
				continue;
			}
			ToGroup.AddUnique(Actor);
		}
		if (NotFound.Num() > 0)
		{
			Out->SetArrayField(TEXT("notFound"), NotFound);
		}
		if (WereGroups.Num() > 0)
		{
			Out->SetArrayField(TEXT("alreadyGroups"), WereGroups);
		}

		if (ToGroup.Num() < 2)
		{
			Fail(Out, FString::Printf(
				TEXT("grouping needs at least TWO groupable actors and resolved %d. %s%sThe engine ")
				TEXT("requires more than one and returns nothing otherwise. NOTHING was grouped."),
				ToGroup.Num(),
				NotFound.Num() ? *FString::Printf(TEXT("%d path(s) did not resolve - see notFound. "),
												  NotFound.Num()) : TEXT(""),
				WereGroups.Num() ? *FString::Printf(
					TEXT("%d were already AGroupActors, which the engine filters out of the ")
					TEXT("candidate list - see alreadyGroups. "), WereGroups.Num()) : TEXT("")));
			return;
		}

		// SAME LEVEL, checked here because the engine's own check breaks out of its loop and
		// returns nullptr without naming either level.
		ULevel* First = ToGroup[0]->GetLevel();
		for (AActor* A : ToGroup)
		{
			if (A->GetLevel() != First)
			{
				Fail(Out, FString::Printf(
					TEXT("every actor must live in the SAME level. '%s' is in %s and '%s' is in ")
					TEXT("%s. move_actors_to_level can bring them together first. NOTHING was ")
					TEXT("grouped."),
					*ToGroup[0]->GetActorLabel(),
					First ? *First->GetOutermost()->GetName() : TEXT("(none)"),
					*A->GetActorLabel(),
					A->GetLevel() ? *A->GetLevel()->GetOutermost()->GetName() : TEXT("(none)")));
				return;
			}
		}

		UActorGroupingUtils* Utils = UActorGroupingUtils::Get();
		if (!Utils)
		{
			Fail(Out, TEXT("no UActorGroupingUtils. NOTHING was grouped."));
			return;
		}

		const FScopedTransaction Transaction(
			NSLOCTEXT("MifBridge", "MifGroupActors", "Group Actors"));
		AGroupActor* Group = Utils->GroupActors(ToGroup);
		if (!Group)
		{
			Fail(Out, TEXT("GroupActors returned nothing, and the four conditions this endpoint ")
				TEXT("knows how to diagnose - grouping mode, actor count, mixed levels, actors ")
				TEXT("that were already groups - all passed. NOTHING was grouped."));
			return;
		}

		// POSTCONDITION, read back off the engine rather than assumed from a non-null return. A
		// group whose members do not point at it is the failure mode worth catching here: the
		// actor exists, the call succeeded, and selecting a member picks up nothing.
		TArray<AActor*> Actual;
		Group->GetGroupActors(Actual, /*bRecurse*/ false);
		TArray<TSharedPtr<FJsonValue>> Members;
		int32 Rooted = 0;
		for (AActor* A : Actual)
		{
			if (!A) { continue; }
			Members.Add(MakeShared<FJsonValueString>(A->GetPathName()));
			if (AGroupActor::GetRootForActor(A) == Group) { ++Rooted; }
		}
		Out->SetStringField(TEXT("group"), Group->GetPathName());
		Out->SetArrayField(TEXT("members"), Members);
		Out->SetNumberField(TEXT("memberCount"), Members.Num());
		Out->SetNumberField(TEXT("requested"), ToGroup.Num());
		Out->SetBoolField(TEXT("everyMemberRootsToThisGroup"), Rooted == Members.Num());
		if (Rooted != Members.Num())
		{
			Out->SetStringField(TEXT("membershipNote"), FString::Printf(
				TEXT("%d of %d members do NOT resolve back to this group through ")
				TEXT("GetRootForActor. Selecting one of those in the viewport will not pick up ")
				TEXT("the group."), Members.Num() - Rooted, Members.Num()));
		}
		Out->SetStringField(TEXT("cookNote"),
			TEXT("AGroupActor is EDITOR-ONLY and does not survive a cook. Grouping changes what a "
				 "click selects in the editor; it is not a runtime relationship. attach_actor is "
				 "the verb that builds one that ships."));
		Out->SetStringField(TEXT("levelNote"),
			TEXT("the level is now dirty and NOTHING has been saved."));
	}

	// --- ungroup_actors ------------------------------------------------------------------------
	//   in:  { actorPaths[] (alias actors, group) }
	//   out: { ungrouped[], freed[], ... }
	//
	// Takes either the GROUP itself or any of its members - UngroupActors resolves each actor to
	// its root group and disbands that, so both spellings are the same operation and refusing one
	// of them would be arbitrary. The group actor is destroyed; its members are left exactly where
	// they are, which is the whole difference from deleting a group.
	void H_ungroup_actors(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPaths"), TEXT("actors"), TEXT("group") },
			TEXT("actorPaths[] (aliases actors, group) - the group to disband, or any actor in it"),
			{ { TEXT("recursive"), TEXT("UngroupActors disbands the root group it finds for each actor; there is no partial-depth ungroup to ask for") },
			  { TEXT("delete"), TEXT("ungrouping never deletes members - it removes the AGroupActor and leaves every actor where it is. delete_level_actor is the destructive verb") } }))
		{
			return;
		}

		const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
		TArray<FString> Paths;
		if (In->TryGetArrayField(TEXT("actorPaths"), Arr) || In->TryGetArrayField(TEXT("actors"), Arr))
		{
			for (const TSharedPtr<FJsonValue>& V : *Arr)
			{
				FString P;
				if (V.IsValid() && V->TryGetString(P) && !P.IsEmpty()) { Paths.Add(P); }
			}
		}
		else
		{
			const FString One = JStr(In, TEXT("group"));
			if (!One.IsEmpty()) { Paths.Add(One); }
		}
		if (Paths.Num() == 0)
		{
			Fail(Out, TEXT("pass group (an AGroupActor path) or actorPaths[] naming the group or ")
				TEXT("any actor in it. list_level_actors reports both. NOTHING was ungrouped."));
			return;
		}

		UEditorActorSubsystem* ActorSub =
			GEditor ? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
		UActorGroupingUtils* Utils = UActorGroupingUtils::Get();
		if (!ActorSub || !Utils)
		{
			Fail(Out, TEXT("no editor actor/grouping subsystem - this is not a running editor. ")
				TEXT("NOTHING was ungrouped."));
			return;
		}

		TArray<AActor*> Targets;
		TArray<TSharedPtr<FJsonValue>> NotFound;
		TArray<TSharedPtr<FJsonValue>> NotInAnyGroup;
		TArray<AActor*> WillBeFreed;
		for (const FString& P : Paths)
		{
			TSharedRef<FJsonObject> One = MakeShared<FJsonObject>();
			One->SetStringField(TEXT("actorPath"), P);
			AActor* Actor = ResolveActor(ActorSub, One, Out);
			if (!Actor)
			{
				NotFound.Add(MakeShared<FJsonValueString>(P));
				continue;
			}
			AGroupActor* Root = Cast<AGroupActor>(Actor);
			if (!Root) { Root = AGroupActor::GetRootForActor(Actor); }
			if (!Root)
			{
				// SAYS SO rather than counting it as done. "ungrouped 3" when one of them was never
				// in a group is the shape of answer that gets believed.
				NotInAnyGroup.Add(MakeShared<FJsonValueString>(P));
				continue;
			}
			Targets.AddUnique(Actor);
			// bRecurse TRUE, and it matters for more than depth: AGroupActor::GetGroupActors calls
			// OutGroupActors.Empty() in its NON-recursive branch and does not in its recursive one
			// (GroupActor.cpp:GetGroupActors). So this accumulating loop is only correct with true;
			// passing false would silently keep just the LAST group's members and then report
			// "every member freed" about a set it had already discarded.
			Root->GetGroupActors(WillBeFreed, /*bRecurse*/ true);
		}
		if (NotFound.Num() > 0)     { Out->SetArrayField(TEXT("notFound"), NotFound); }
		if (NotInAnyGroup.Num() > 0) { Out->SetArrayField(TEXT("notInAnyGroup"), NotInAnyGroup); }

		if (Targets.Num() == 0)
		{
			Fail(Out, FString::Printf(
				TEXT("nothing to ungroup - %d path(s) did not resolve and %d resolved to actors ")
				TEXT("that are not in any group. NOTHING was ungrouped."),
				NotFound.Num(), NotInAnyGroup.Num()));
			return;
		}

		const FScopedTransaction Transaction(
			NSLOCTEXT("MifBridge", "MifUngroupActors", "Ungroup Actors"));
		Utils->UngroupActors(Targets);

		// POSTCONDITION: every actor that was in one of those groups must now root to nothing.
		TArray<TSharedPtr<FJsonValue>> Freed;
		int32 StillGrouped = 0;
		for (AActor* A : WillBeFreed)
		{
			if (!A) { continue; }
			if (AGroupActor::GetRootForActor(A) == nullptr)
			{
				Freed.Add(MakeShared<FJsonValueString>(A->GetPathName()));
			}
			else
			{
				++StillGrouped;
			}
		}
		Out->SetArrayField(TEXT("freed"), Freed);
		Out->SetNumberField(TEXT("freedCount"), Freed.Num());
		Out->SetNumberField(TEXT("stillGrouped"), StillGrouped);
		Out->SetBoolField(TEXT("everyMemberFreed"), StillGrouped == 0);
		Out->SetStringField(TEXT("memberNote"),
			TEXT("the members are untouched and still in the level - ungrouping removes the "
				 "AGroupActor, it does not delete anything."));
		Out->SetStringField(TEXT("levelNote"),
			TEXT("the level is now dirty and NOTHING has been saved."));
	}

}
