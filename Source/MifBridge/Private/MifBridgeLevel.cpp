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
#include "Subsystems/EditorActorSubsystem.h"
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
		if (RejectUnknownParams(In, Out,
			{ TEXT("classFilter"), TEXT("nameContains"), TEXT("folder"), TEXT("selectedOnly"), TEXT("limit") },
			TEXT("classFilter, nameContains, folder, selectedOnly, limit"),
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
}
