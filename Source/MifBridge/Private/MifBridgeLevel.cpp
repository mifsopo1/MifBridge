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

		// Accept {x,y,z} or a bare [x,y,z]; absent leaves Fallback untouched.
		bool ReadVector(const TSharedRef<FJsonObject>& In, const TCHAR* Field, FVector& OutVec)
		{
			const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
			if (In->TryGetObjectField(Field, ObjPtr) && ObjPtr)
			{
				const TSharedRef<FJsonObject> Obj = ObjPtr->ToSharedRef();
				OutVec = FVector(JNum(Obj, TEXT("x"), OutVec.X), JNum(Obj, TEXT("y"), OutVec.Y), JNum(Obj, TEXT("z"), OutVec.Z));
				return true;
			}
			const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
			if (In->TryGetArrayField(Field, Arr) && Arr && Arr->Num() >= 3)
			{
				OutVec = FVector((*Arr)[0]->AsNumber(), (*Arr)[1]->AsNumber(), (*Arr)[2]->AsNumber());
				return true;
			}
			return false;
		}
	}

	// --- list_level_actors --------------------------------------------------
	//   in:  { classFilter?, nameContains?, folder?, selectedOnly?, limit? }
	//   out: { world, count, truncated, actors:[{actorPath, name, label, class, folder, location, rotation, scale}] }
	void H_list_level_actors(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UEditorActorSubsystem* Subsystem = ActorSubsystem(Out);
		if (!Subsystem)
		{
			return;
		}
		UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
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
	//   in:  { actorClass, location?, rotation?, scale?, label?, folder? }
	//   out: { actor:{...} }
	void H_spawn_actor_in_level(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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

		FVector Location = FVector::ZeroVector;
		FVector RotVec = FVector::ZeroVector;
		FVector Scale = FVector::OneVector;
		ReadVector(In, TEXT("location"), Location);
		const bool bHasRot = ReadVector(In, TEXT("rotation"), RotVec);
		const bool bHasScale = ReadVector(In, TEXT("scale"), Scale);
		// Rotation is read as x/y/z = pitch/yaw/roll, matching what SerializeActor emits.
		const FRotator Rotation = bHasRot ? FRotator(RotVec.X, RotVec.Y, RotVec.Z) : FRotator::ZeroRotator;

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
			Actor->SetActorLabel(Label);
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
	//   in:  { actorPath, location?, rotation?, scale?, relative? }
	// Any omitted component keeps its current value, so this doubles as "move only".
	void H_set_actor_transform(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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

		FVector Location = Actor->GetActorLocation();
		const FRotator CurRot = Actor->GetActorRotation();
		FVector RotVec(CurRot.Pitch, CurRot.Yaw, CurRot.Roll);
		FVector Scale = Actor->GetActorScale3D();

		const bool bAny = ReadVector(In, TEXT("location"), Location)
			| ReadVector(In, TEXT("rotation"), RotVec)
			| ReadVector(In, TEXT("scale"), Scale);
		if (!bAny)
		{
			Fail(Out, TEXT("supply at least one of location / rotation / scale"));
			return;
		}

		if (JBool(In, TEXT("relative"), false))
		{
			// Deltas, not absolutes — the common "nudge it 100 units" case.
			Location = Actor->GetActorLocation() + Location;
			RotVec = FVector(CurRot.Pitch, CurRot.Yaw, CurRot.Roll) + RotVec;
		}

		const FTransform NewTransform(FRotator(RotVec.X, RotVec.Y, RotVec.Z), Location, Scale);
		Actor->Modify();
		if (!Subsystem->SetActorTransform(Actor, NewTransform))
		{
			Fail(Out, TEXT("SetActorTransform failed (actor may be locked or in a locked level)"));
			return;
		}

		Out->SetObjectField(TEXT("actor"), SerializeActor(Actor));
	}

	// --- set_actor_label ----------------------------------------------------
	//   in:  { actorPath, label?, folder? }
	// The label is the World Outliner display name; it is NOT the object name and renaming it is safe.
	void H_set_actor_label(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
		if (!Label.IsEmpty())  { Actor->SetActorLabel(Label); }
		if (!Folder.IsEmpty()) { Actor->SetFolderPath(FName(*Folder)); }
		Out->SetObjectField(TEXT("actor"), SerializeActor(Actor));
	}

	// --- delete_level_actor -------------------------------------------------
	//   in:  { actorPath, confirm: true }
	void H_delete_level_actor(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
		if (In->TryGetArrayField(TEXT("actorPaths"), Arr) && Arr)
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
