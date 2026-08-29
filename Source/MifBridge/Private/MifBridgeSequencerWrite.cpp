// MifBridge — SEQUENCER: see what a LevelSequence binds, bind an actor into it, add a track.
//
// The write half, reopened 2026-08-27. The decline was "DDS2 contains exactly 4 LevelSequence assets
// against 3771 SoundWaves" - true, and evidence about DDS2 rather than about UE5. A LevelSequence
// cannot be authored without the editor, so this passes the test that declined .cpp reading: there is
// no file to edit instead.
//
// THE READ CAME FIRST FOR A REASON. describe_level_sequence already existed and reports COUNTS -
// "bindings: 2, possessables: 2, sections: 2" - and not what any of them ARE. Authoring against that
// is blind: you cannot add a track to a binding you cannot name. list_sequence_bindings is therefore
// part of this change rather than a later one.
//
// Verified in BOTH trees before writing:
//   UMovieScene::AddPossessable(const FString&, UClass*)        MOVIESCENE_API, identical
//   UMovieScene::AddTrack(TSubclassOf<UMovieSceneTrack>, FGuid) MOVIESCENE_API, identical
//   UMovieScene::FindPossessable(const FGuid&)                  identical
//   ULevelSequence::BindPossessableObject(FGuid, UObject&, UObject*)  identical
//   FMovieSceneBinding::GetObjectGuid() / GetTracks()           identical
//
// TWO DEPRECATIONS IN 5.7, both avoided rather than worked around:
//
//   UMovieScene::GetBindings() NON-CONST is UE_DEPRECATED(5.7, "Getting non-const access ... is no
//   longer allowed. Please use const GetBindings()"). Everything here reads through a const pointer so
//   the const overload is selected on both engines.
//
//   FMovieSceneBinding::GetName() returns BindingName_DEPRECATED in 5.7 - the name moved off the
//   binding. So names come from FMovieScenePossessable::GetName(), which is NOT deprecated in either
//   tree. Reading the binding's own name would compile, return something plausible on 5.3, and be
//   empty or stale on 5.7 - a silent wrong answer rather than a build error.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "LevelSequence.h"
#include "MovieScene.h"
#include "MovieSceneBinding.h"
#include "MovieScenePossessable.h"
#include "MovieSceneSpawnable.h"
#include "MovieSceneTrack.h"
#include "Subsystems/EditorActorSubsystem.h"
#include "Editor.h"
#include "UObject/Package.h"

namespace MifBridge
{
	namespace
	{
		/** The sequence, or a populated failure. */
		ULevelSequence* SeqResolve(const TSharedRef<FJsonObject>& In,
								   const TSharedRef<FJsonObject>& Out, const TCHAR* Endpoint)
		{
			const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("sequence") });
			if (Path.IsEmpty())
			{
				Fail(Out, FString::Printf(
					TEXT("%s: path is required - a LevelSequence asset. list_level_sequences reports "
						 "them."), Endpoint));
				return nullptr;
			}
			ULevelSequence* Seq = LoadObject<ULevelSequence>(nullptr, *Path, nullptr,
															 LOAD_NoWarn | LOAD_Quiet);
			if (!Seq)
			{
				Fail(Out, FString::Printf(TEXT("%s: no LevelSequence at '%s'."), Endpoint, *Path));
				return nullptr;
			}
			if (!Seq->GetMovieScene())
			{
				// Reachable and worth its own message: a sequence asset without a movie scene is
				// broken rather than empty, and every call below would otherwise null-deref.
				Fail(Out, FString::Printf(
					TEXT("%s: '%s' has no MovieScene. The asset exists but is malformed."),
					Endpoint, *Path));
				return nullptr;
			}
			return Seq;
		}
	}

	// --- list_sequence_bindings -----------------------------------------------------------------
	//   in:  { path (aliases: assetPath, sequence) }
	//   out: { bindings[ { guid, name, kind, class, tracks[] } ], count }
	// Bucket: READ.
	void H_list_sequence_bindings(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("sequence") },
			TEXT("path (aliases: assetPath, sequence) - a LevelSequence asset"),
			{ { TEXT("binding"), TEXT("this lists ALL bindings; filter the result") } }))
		{
			return;
		}
		ULevelSequence* Seq = SeqResolve(In, Out, TEXT("list_sequence_bindings"));
		if (!Seq) { return; }

		// CONST pointer, deliberately: it selects the const GetBindings() overload, and the non-const
		// one is UE_DEPRECATED(5.7). See the file header.
		const UMovieScene* Scene = Seq->GetMovieScene();
		UMovieScene* MutableScene = Seq->GetMovieScene();

		TArray<TSharedPtr<FJsonValue>> Json;
		for (const FMovieSceneBinding& B : Scene->GetBindings())
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("guid"), B.GetObjectGuid().ToString());

			// The NAME comes from the possessable or spawnable, never from the binding - the binding's
			// own name field is deprecated in 5.7 and would read empty there.
			FString Name;
			FString Kind = TEXT("unknown");
			FString ClassName;
			if (const FMovieScenePossessable* P = MutableScene->FindPossessable(B.GetObjectGuid()))
			{
				Name = P->GetName();
				Kind = TEXT("possessable");
				// A possessable NEED NOT RECORD ITS CLASS. PossessedObjectClass is set when the
				// binding is created with an explicit class - which add_sequence_possessable does -
				// and is commonly null for bindings authored through the editor's own drag-and-drop.
				// Verified against a real DDS2 sequence: both of LobbyLevelSequence's bindings have a
				// name and a transform track and no class at all.
				//
				// So the empty case is NAMED rather than returned as "". A blank field reads as a
				// bug in the reader; "the binding does not record one" is the actual answer.
				if (const UClass* C = P->GetPossessedObjectClass())
				{
					ClassName = C->GetName();
				}
				else
				{
					J->SetBoolField(TEXT("classRecorded"), false);
				}
			}
			else if (const FMovieSceneSpawnable* S = MutableScene->FindSpawnable(B.GetObjectGuid()))
			{
				Name = S->GetName();
				Kind = TEXT("spawnable");
			}
			J->SetStringField(TEXT("name"), Name);
			J->SetStringField(TEXT("kind"), Kind);
			J->SetStringField(TEXT("class"), ClassName);
			if (!ClassName.IsEmpty()) { J->SetBoolField(TEXT("classRecorded"), true); }

			TArray<TSharedPtr<FJsonValue>> Tracks;
			for (const UMovieSceneTrack* T : B.GetTracks())
			{
				if (!T) { continue; }
				TSharedRef<FJsonObject> TJ = MakeShared<FJsonObject>();
				TJ->SetStringField(TEXT("trackClass"), T->GetClass()->GetName());
				TJ->SetStringField(TEXT("trackPath"), T->GetClass()->GetPathName());
				Tracks.Add(MakeShared<FJsonValueObject>(TJ));
			}
			J->SetArrayField(TEXT("tracks"), Tracks);
			J->SetNumberField(TEXT("trackCount"), Tracks.Num());
			Json.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetStringField(TEXT("assetPath"), Seq->GetPathName());
		Out->SetArrayField(TEXT("bindings"), Json);
		Out->SetNumberField(TEXT("count"), Json.Num());
		Out->SetStringField(TEXT("note"),
			TEXT("trackClass is the full class NAME and trackPath its class path - pass trackPath to "
				 "add_sequence_track. Binding names come from the possessable/spawnable, not from the "
				 "binding itself, whose name field is deprecated in UE 5.7. classRecorded:false means "
				 "the binding does not store a class - normal for one authored by dragging an actor "
				 "into the sequencer - not that the class could not be read."));
	}

	// --- add_sequence_possessable ---------------------------------------------------------------
	//   in:  { path, actorPath, confirm }
	//   out: { guid, name, class }
	// Bucket: MUTATES the sequence asset in memory. Nothing is saved.
	void H_add_sequence_possessable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("sequence"), TEXT("actorPath"), TEXT("actor"),
			  TEXT("confirm") },
			TEXT("path (the LevelSequence); actorPath (an actor in the OPEN level); confirm:true"),
			{ { TEXT("class"), TEXT("the class is taken from the actor - bind the actor you mean") },
			  { TEXT("name"), TEXT("the name is taken from the actor's label, so the sequence matches the outliner") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("add_sequence_possessable needs confirm:true - it modifies a shared "
						   "sequence asset. NOTHING was changed."));
			return;
		}
		ULevelSequence* Seq = SeqResolve(In, Out, TEXT("add_sequence_possessable"));
		if (!Seq) { return; }

		UEditorActorSubsystem* Sub = GEditor
			? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
		if (!Sub) { Fail(Out, TEXT("no UEditorActorSubsystem.")); return; }
		AActor* Actor = ResolveActor(Sub, In, Out);
		if (!Actor) { return; }

		UMovieScene* Scene = Seq->GetMovieScene();

		// ALREADY BOUND? Checked first, because AddPossessable happily creates a SECOND binding for the
		// same actor and the sequence then drives it twice - a silent duplicate that is tedious to
		// find in the editor and impossible to see in a count.
		for (const FMovieSceneBinding& B : const_cast<const UMovieScene*>(Scene)->GetBindings())
		{
			if (const FMovieScenePossessable* P = Scene->FindPossessable(B.GetObjectGuid()))
			{
				if (P->GetName() == Actor->GetActorLabel())
				{
					Fail(Out, FString::Printf(
						TEXT("'%s' already has a binding in this sequence (guid %s). Adding another "
							 "would create a DUPLICATE that drives the same actor twice. NOTHING was "
							 "changed."), *Actor->GetActorLabel(), *B.GetObjectGuid().ToString()));
					return;
				}
			}
		}

		// REFUSE BEFORE MUTATING. ULevelSequence::BindPossessableObject's ENTIRE body is
		// `if (Context) { BindingReferences.AddBinding(...); }` (LevelSequence.cpp:424-430) - if
		// Context is null it is a silent no-op, void, with no way to report it. Context here is
		// Actor->GetWorld(), so checking it BEFORE AddPossessable means a doomed bind never gets as
		// far as creating an orphaned slot - "TWO STEPS, and missing the second is the classic
		// sequencer mistake" (the comment this replaces) described the risk correctly and then did not
		// guard it. Found by audit_postconditions.py, 2026-08-29.
		if (!Actor->GetWorld())
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no World (BindPossessableObject silently no-ops without one). NOTHING ")
				TEXT("was changed."), *Actor->GetActorLabel()));
			return;
		}

		Scene->Modify();
		const FGuid Guid = Scene->AddPossessable(Actor->GetActorLabel(), Actor->GetClass());
		if (!Guid.IsValid())
		{
			Fail(Out, TEXT("AddPossessable returned an invalid guid and the engine reported no "
						   "reason. NOTHING was changed."));
			return;
		}
		Seq->BindPossessableObject(Guid, *Actor, Actor->GetWorld());

		if (UPackage* Pkg = Seq->GetOutermost()) { Pkg->MarkPackageDirty(); }

		Out->SetStringField(TEXT("guid"), Guid.ToString());
		Out->SetStringField(TEXT("name"), Actor->GetActorLabel());
		Out->SetStringField(TEXT("class"), Actor->GetClass()->GetName());
		Out->SetStringField(TEXT("assetPath"), Seq->GetPathName());
		Out->SetStringField(TEXT("note"),
			TEXT("the binding exists and the actor is attached to it. Nothing was saved. Add tracks "
				 "with add_sequence_track using the guid above."));
		UE_LOG(LogMifBridge, Log, TEXT("add_sequence_possessable: %s -> %s"),
			*Actor->GetActorLabel(), *Seq->GetName());
	}

	// --- add_sequence_track ---------------------------------------------------------------------
	//   in:  { path, guid, trackClass, confirm }
	//   out: { guid, trackClass, trackCount }
	// Bucket: MUTATES the sequence asset in memory.
	void H_add_sequence_track(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("sequence"), TEXT("guid"), TEXT("binding"),
			  TEXT("trackClass"), TEXT("confirm") },
			TEXT("path (the LevelSequence); guid (alias: binding) from list_sequence_bindings; "
				 "trackClass - a UMovieSceneTrack class path such as "
				 "/Script/MovieSceneTracks.MovieScene3DTransformTrack; confirm:true"),
			{ { TEXT("actorPath"), TEXT("bind the actor first with add_sequence_possessable, then pass its guid here") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("add_sequence_track needs confirm:true. NOTHING was changed."));
			return;
		}
		ULevelSequence* Seq = SeqResolve(In, Out, TEXT("add_sequence_track"));
		if (!Seq) { return; }

		const FString GuidStr = JStrAny(In, { TEXT("guid"), TEXT("binding") });
		FGuid Guid;
		if (GuidStr.IsEmpty() || !FGuid::Parse(GuidStr, Guid))
		{
			Fail(Out, TEXT("guid is required and must be a binding guid from list_sequence_bindings. "
						   "NOTHING was changed."));
			return;
		}

		const FString ClassPath = JStr(In, TEXT("trackClass"));
		if (ClassPath.IsEmpty())
		{
			Fail(Out, TEXT("trackClass is required - a UMovieSceneTrack class path, for example "
						   "/Script/MovieSceneTracks.MovieScene3DTransformTrack. NOTHING was "
						   "changed."));
			return;
		}
		UClass* TrackClass = LoadClass<UMovieSceneTrack>(nullptr, *ClassPath, nullptr,
														 LOAD_NoWarn | LOAD_Quiet, nullptr);
		if (!TrackClass)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' does not resolve to a UMovieSceneTrack class. Pass a full class path; "
					 "list_sequence_bindings reports trackPath for tracks that already exist. NOTHING "
					 "was changed."), *ClassPath));
			return;
		}

		UMovieScene* Scene = Seq->GetMovieScene();
		// The guid must name a REAL binding. AddTrack does not check, and a track added against a
		// stray guid lands in the asset attached to nothing.
		bool bFound = false;
		for (const FMovieSceneBinding& B : const_cast<const UMovieScene*>(Scene)->GetBindings())
		{
			if (B.GetObjectGuid() == Guid) { bFound = true; break; }
		}
		if (!bFound)
		{
			Fail(Out, FString::Printf(
				TEXT("no binding with guid %s in this sequence. AddTrack would not have complained - "
					 "the track would exist attached to nothing. NOTHING was changed."),
				*Guid.ToString()));
			return;
		}

		Scene->Modify();
		UMovieSceneTrack* Track = Scene->AddTrack(TrackClass, Guid);
		if (!Track)
		{
			Fail(Out, FString::Printf(
				TEXT("AddTrack refused %s for this binding and reported no reason. Most track types "
					 "only accept certain object classes. NOTHING was changed."),
				*TrackClass->GetName()));
			return;
		}
		if (UPackage* Pkg = Seq->GetOutermost()) { Pkg->MarkPackageDirty(); }

		// READ BACK through the binding rather than trusting the returned pointer - the house rule.
		int32 TrackCount = 0;
		for (const FMovieSceneBinding& B : const_cast<const UMovieScene*>(Scene)->GetBindings())
		{
			if (B.GetObjectGuid() == Guid) { TrackCount = B.GetTracks().Num(); break; }
		}

		Out->SetStringField(TEXT("guid"), Guid.ToString());
		Out->SetStringField(TEXT("trackClass"), TrackClass->GetName());
		Out->SetNumberField(TEXT("trackCount"), TrackCount);
		Out->SetStringField(TEXT("note"),
			TEXT("the track exists and is EMPTY - it has no sections, so it animates nothing yet. "
				 "Nothing was saved."));
		UE_LOG(LogMifBridge, Log, TEXT("add_sequence_track: %s on %s"),
			*TrackClass->GetName(), *Seq->GetName());
	}
}
