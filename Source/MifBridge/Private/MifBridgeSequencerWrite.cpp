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
#include "MovieSceneSection.h"
#include "Tracks/MovieSceneCameraCutTrack.h"
#include "MovieSceneObjectBindingID.h"
#include "MovieSceneSequenceID.h"
#include "Channels/MovieSceneChannelProxy.h"
#include "Channels/MovieSceneDoubleChannel.h"
#include "Channels/MovieSceneFloatChannel.h"
#include "Channels/MovieSceneBoolChannel.h"
#include "Channels/MovieSceneIntegerChannel.h"
#include "Channels/MovieSceneStringChannel.h"       // MovieSceneTracks module
#include "Channels/MovieSceneObjectPathChannel.h"   // GetPropertyClass - the constraint
#include "ScopedTransaction.h"
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
			  TEXT("trackClass"), TEXT("confirm"), TEXT("root"), TEXT("cameraCut"), TEXT("time") },
			TEXT("path (the LevelSequence); trackClass - a UMovieSceneTrack class path such as ")
			TEXT("/Script/MovieSceneTracks.MovieScene3DTransformTrack; confirm:true. THREE SCOPES: ")
			TEXT("by default the track hangs off an object binding and needs guid (alias: binding) ")
			TEXT("from list_sequence_bindings; root:true adds a track to the SEQUENCE itself (Audio, ")
			TEXT("Fade, LevelVisibility, Subsequence) and takes no guid; cameraCut:true adds a camera ")
			TEXT("cut pointing at the camera bound to guid, at time (seconds)"),
			{ { TEXT("actorPath"), TEXT("bind the actor first with add_sequence_possessable, then pass its guid here") },
			  { TEXT("master"), TEXT("spell it root - AddMasterTrack was deprecated in 5.2 and is gone entirely from 5.7; AddTrack is the replacement") } }))
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

		UMovieScene* SceneEarly = Seq->GetMovieScene();
		if (!SceneEarly) { Fail(Out, TEXT("this sequence has no MovieScene.")); return; }

		// ---------------------------------------------------------------- root (sequence-level)
		//
		// A track that hangs off the SEQUENCE rather than off an object binding: Audio, Fade,
		// LevelVisibility, Subsequence. UMovieScene::AddTrack has a no-guid overload for exactly
		// this. NOT AddMasterTrack - that was UE_DEPRECATED(5.2) on 5.3 and is GONE ENTIRELY from
		// 5.7, so writing the older spelling would build here and fail to compile there.
		if (JBool(In, TEXT("root"), false))
		{
			const FString RootClassPath = JStr(In, TEXT("trackClass"));
			UClass* RootClass = RootClassPath.IsEmpty() ? nullptr
				: LoadClass<UMovieSceneTrack>(nullptr, *RootClassPath, nullptr,
											  LOAD_NoWarn | LOAD_Quiet, nullptr);
			if (!RootClass)
			{
				Fail(Out, FString::Printf(
					TEXT("trackClass is required for root:true and must be a UMovieSceneTrack - ")
					TEXT("'%s' did not resolve. NOTHING was changed."), *RootClassPath));
				return;
			}
			const int32 RootBefore = SceneEarly->GetTracks().Num();
			FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_AddRootTrack", "Add Root Track"));
			SceneEarly->Modify();
			UMovieSceneTrack* RootTrack = SceneEarly->AddTrack(RootClass);
			if (!RootTrack || !SceneEarly->GetTracks().Contains(RootTrack))
			{
				Fail(Out, FString::Printf(
					TEXT("AddTrack returned nothing usable for '%s' - some track types are only ")
					TEXT("valid on a binding, not on the sequence itself. NOTHING was changed."),
					*RootClass->GetName()));
				return;
			}
			if (UPackage* Pkg = Seq->GetOutermost()) { Pkg->MarkPackageDirty(); }
			Out->SetStringField(TEXT("scope"), TEXT("root"));
			Out->SetStringField(TEXT("trackClass"), RootClass->GetName());
			Out->SetNumberField(TEXT("rootTracksBefore"), RootBefore);
			Out->SetNumberField(TEXT("rootTrackCount"), SceneEarly->GetTracks().Num());
			Out->SetStringField(TEXT("note"),
				TEXT("a SEQUENCE-level track, not bound to any object. It is EMPTY - add a section "
					 "with add_sequence_section (pass trackIndex, since it has no binding guid) "
					 "before it does anything. Nothing was saved."));
			return;
		}

		const FString GuidStr = JStrAny(In, { TEXT("guid"), TEXT("binding") });
		FGuid Guid;
		if (GuidStr.IsEmpty() || !FGuid::Parse(GuidStr, Guid))
		{
			Fail(Out, TEXT("guid is required and must be a binding guid from list_sequence_bindings. "
						   "NOTHING was changed."));
			return;
		}

		// ---------------------------------------------------------------- camera cut
		//
		// WITHOUT A CAMERA CUT A LEVELSEQUENCE DRIVES NO CAMERA, so an agent cannot author a
		// cutscene at all - which is why this belongs here rather than in a later pass.
		//
		// AND IT CAN ASSERT-CRASH THE EDITOR, guarded before the engine is touched.
		// AddNewCameraCut calls FindEndTimeForCameraCut, whose FIRST act is
		//     DiscreteExclusiveUpper(OwnerScene->GetPlaybackRange())
		// and that inline opens with `check(!InUpperBound.IsOpen())`
		// (MovieSceneTimeHelpers.h:64). A LevelSequence whose playback range is unbounded on the
		// upper end therefore fails a check() inside the engine - a dead editor, not an error
		// return. This is not hypothetical here: describe_level_sequence already DETECTS and
		// reports that exact state ("the playback range is unbounded on at least one end, so it has
		// no duration"), so the bridge has been able to see it and would have walked straight into
		// it. Refused by name instead.
		if (JBool(In, TEXT("cameraCut"), false))
		{
			const TRange<FFrameNumber> Playback = SceneEarly->GetPlaybackRange();
			if (Playback.GetUpperBound().IsOpen() || Playback.GetLowerBound().IsOpen())
			{
				Fail(Out, TEXT("this sequence's PLAYBACK RANGE IS UNBOUNDED, and adding a camera cut ")
					TEXT("to it would CRASH the editor: AddNewCameraCut reaches ")
					TEXT("DiscreteExclusiveUpper(GetPlaybackRange()), which opens with ")
					TEXT("check(!InUpperBound.IsOpen()) - a failed check is fatal, not an error. ")
					TEXT("describe_level_sequence reports this same state. Give the sequence a ")
					TEXT("playback range first. NOTHING was changed."));
				return;
			}

			bool bFound = false;
			for (const FMovieSceneBinding& B : const_cast<const UMovieScene*>(SceneEarly)->GetBindings())
			{
				if (B.GetObjectGuid() == Guid) { bFound = true; break; }
			}
			if (!bFound)
			{
				Fail(Out, TEXT("no binding with that guid - a camera cut has to point at a CAMERA ")
					TEXT("that is bound into this sequence. Bind it with add_sequence_possessable ")
					TEXT("first. NOTHING was changed."));
				return;
			}

			const double CutSec = JNum(In, TEXT("time"), 0.0);
			const FFrameRate CutTick = SceneEarly->GetTickResolution();
			const FFrameNumber CutFrame = (CutSec * CutTick).RoundToFrame();

			FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_AddCameraCut", "Add Camera Cut"));
			SceneEarly->Modify();
			UMovieSceneTrack* CutTrackBase = SceneEarly->GetCameraCutTrack();
			if (!CutTrackBase)
			{
				CutTrackBase = SceneEarly->AddCameraCutTrack(UMovieSceneCameraCutTrack::StaticClass());
			}
			UMovieSceneCameraCutTrack* CutTrack = Cast<UMovieSceneCameraCutTrack>(CutTrackBase);
			if (!CutTrack)
			{
				Fail(Out, TEXT("could not get or create the camera cut track. NOTHING was changed."));
				return;
			}
			const int32 CutsBefore = CutTrack->GetAllSections().Num();
			const FMovieSceneObjectBindingID BindingID(
				UE::MovieScene::FFixedObjectBindingID(Guid, MovieSceneSequenceID::Root));
			CutTrack->AddNewCameraCut(BindingID, CutFrame);

			// READ BACK - AddNewCameraCut returns a section pointer on some versions and the count
			// is the honest measure either way.
			const int32 CutsNow = CutTrack->GetAllSections().Num();
			if (CutsNow <= CutsBefore)
			{
				Fail(Out, FString::Printf(
					TEXT("AddNewCameraCut ran and the cut count is still %d. NOTHING usable was ")
					TEXT("produced."), CutsNow));
				return;
			}
			if (UPackage* Pkg = Seq->GetOutermost()) { Pkg->MarkPackageDirty(); }
			Out->SetStringField(TEXT("scope"), TEXT("cameraCut"));
			Out->SetStringField(TEXT("guid"), Guid.ToString());
			Out->SetNumberField(TEXT("cutsBefore"), CutsBefore);
			Out->SetNumberField(TEXT("cutCount"), CutsNow);
			Out->SetNumberField(TEXT("timeTick"), CutFrame.Value);
			Out->SetNumberField(TEXT("time"), CutSec);
			Out->SetStringField(TEXT("note"),
				TEXT("the sequence now drives this camera from that time. A camera cut is what makes "
					 "a LevelSequence control the view at all - without one it animates objects and "
					 "nothing looks through a camera. Nothing was saved."));
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

		Out->SetStringField(TEXT("scope"), TEXT("binding"));
		Out->SetStringField(TEXT("guid"), Guid.ToString());
		Out->SetStringField(TEXT("trackClass"), TrackClass->GetName());
		Out->SetNumberField(TEXT("trackCount"), TrackCount);
		Out->SetStringField(TEXT("note"),
			TEXT("the track exists and is EMPTY - it has no sections, so it animates nothing yet. "
				 "Nothing was saved."));
		UE_LOG(LogMifBridge, Log, TEXT("add_sequence_track: %s on %s"),
			*TrackClass->GetName(), *Seq->GetName());
	}

	// =======================================================================
	// SECTIONS AND KEYS - the half that makes the other four endpoints real
	// =======================================================================
	//
	// add_sequence_track's own closing note says it: "the track exists and is EMPTY - it has no
	// sections, so it animates nothing yet." That is true of the whole write chain.
	// add_sequence_possessable binds an actor, add_sequence_track gives it a track, and the result
	// animates NOTHING. Those endpoints are not half a feature, they are dead weight until a section
	// with keys exists. This is that half.
	//
	// GENERIC, NOT PER-TRACK-TYPE. Channels are addressed by their EDITOR NAME - "Location.X",
	// "Intensity" - through the section's FMovieSceneChannelProxy, so one pair of endpoints keys
	// transform tracks, float and bool property tracks, and anything a plugin registers. The
	// alternative, which MifBridgeWidgets.cpp uses for widget animation, is a per-section-class
	// Cast<> ladder; it cannot be lifted here and would need a new arm per track type forever.
	//
	// SCOPED TO THE NUMERIC AND BOOL CHANNELS for this pass - float, double, bool, integer, byte.
	// Those cover transforms, most property tracks and visibility, which is the great majority of
	// what anyone keys. Object-path and string channels are declared unsupported BY NAME rather
	// than silently ignored, and are filed as follow-up.
	//
	// TIME IS IN SECONDS at this boundary and ticks internally. UMovieScene::GetTickResolution is
	// the conversion and describe_level_sequence already reports it, so both halves agree.

	UMovieSceneTrack* SeqFindTrack(UMovieScene* Scene, const FGuid& Guid, const FString& ClassName,
								   int32 TrackIndex, FString& OutError)
	{
		for (const FMovieSceneBinding& B : const_cast<const UMovieScene*>(Scene)->GetBindings())
		{
			if (B.GetObjectGuid() != Guid) { continue; }
			const TArray<UMovieSceneTrack*>& Tracks = B.GetTracks();
			if (TrackIndex >= 0)
			{
				if (!Tracks.IsValidIndex(TrackIndex))
				{
					OutError = FString::Printf(
						TEXT("trackIndex %d is outside this binding's %d track(s)."),
						TrackIndex, Tracks.Num());
					return nullptr;
				}
				return Tracks[TrackIndex];
			}
			for (UMovieSceneTrack* T : Tracks)
			{
				if (T && (T->GetClass()->GetName() == ClassName
						  || T->GetClass()->GetPathName() == ClassName))
				{
					return T;
				}
			}
			OutError = FString::Printf(
				TEXT("this binding has %d track(s) and none is a '%s'. Add one with "
					 "add_sequence_track, or pass trackIndex."), Tracks.Num(), *ClassName);
			return nullptr;
		}
		OutError = TEXT("no binding with that guid - list_sequence_bindings shows them.");
		return nullptr;
	}

	/** Every channel on a section, by editor name, with its type and key count. */
	TArray<TSharedPtr<FJsonValue>> SeqChannelRows(UMovieSceneSection* Section)
	{
		TArray<TSharedPtr<FJsonValue>> Rows;
		if (!Section) { return Rows; }
		const FMovieSceneChannelProxy& Proxy = Section->GetChannelProxy();
		for (const FMovieSceneChannelEntry& Entry : Proxy.GetAllEntries())
		{
			const TArrayView<FMovieSceneChannel* const> Channels = Entry.GetChannels();
#if WITH_EDITOR
			const TArrayView<const FMovieSceneChannelMetaData> Meta = Entry.GetMetaData();
#endif
			for (int32 i = 0; i < Channels.Num(); ++i)
			{
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
#if WITH_EDITOR
				if (Meta.IsValidIndex(i))
				{
					J->SetStringField(TEXT("name"), Meta[i].Name.ToString());
					J->SetStringField(TEXT("displayName"), Meta[i].DisplayText.ToString());
				}
#endif
				J->SetStringField(TEXT("type"), Entry.GetChannelTypeName().ToString());
				J->SetNumberField(TEXT("keyCount"), Channels[i] ? Channels[i]->GetNumKeys() : 0);
				Rows.Add(MakeShared<FJsonValueObject>(J));
			}
		}
		return Rows;
	}

	// --- add_sequence_section -----------------------------------------------
	void H_add_sequence_section(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("sequence"), TEXT("guid"), TEXT("binding"),
			  TEXT("trackClass"), TEXT("trackIndex"), TEXT("startTime"), TEXT("endTime"),
			  TEXT("rowIndex"), TEXT("confirm") },
			TEXT("path (the LevelSequence); guid (alias: binding) from list_sequence_bindings; ")
			TEXT("trackClass OR trackIndex to pick the track on that binding; startTime and endTime ")
			TEXT("in SECONDS; rowIndex (default 0); confirm:true"),
			{ { TEXT("startFrame"), TEXT("times here are SECONDS - the tick conversion is done for ")
									TEXT("you from the sequence's own tick resolution") },
			  { TEXT("duration"), TEXT("pass startTime and endTime, not a duration") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("add_sequence_section needs confirm:true. NOTHING was changed."));
			return;
		}
		ULevelSequence* Seq = SeqResolve(In, Out, TEXT("add_sequence_section"));
		if (!Seq) { return; }
		UMovieScene* Scene = Seq->GetMovieScene();
		if (!Scene) { Fail(Out, TEXT("this sequence has no MovieScene.")); return; }

		FGuid Guid;
		const FString GuidStr = JStrAny(In, { TEXT("guid"), TEXT("binding") });
		if (GuidStr.IsEmpty() || !FGuid::Parse(GuidStr, Guid))
		{
			Fail(Out, TEXT("guid is required (from list_sequence_bindings). NOTHING was changed."));
			return;
		}
		FString FindError;
		UMovieSceneTrack* Track = SeqFindTrack(Scene, Guid, JStr(In, TEXT("trackClass")),
											   JInt(In, TEXT("trackIndex"), -1), FindError);
		if (!Track)
		{
			Fail(Out, FindError + TEXT(" NOTHING was changed."));
			return;
		}

		if (!In->HasField(TEXT("startTime")) || !In->HasField(TEXT("endTime")))
		{
			Fail(Out, TEXT("startTime and endTime are required, in SECONDS. NOTHING was changed."));
			return;
		}
		const double StartSec = JNum(In, TEXT("startTime"), 0.0);
		const double EndSec = JNum(In, TEXT("endTime"), 0.0);
		if (EndSec <= StartSec)
		{
			Fail(Out, FString::Printf(
				TEXT("endTime (%.4f) must be greater than startTime (%.4f) - a section with no ")
				TEXT("duration animates nothing. NOTHING was changed."), EndSec, StartSec));
			return;
		}
		const FFrameRate Tick = Scene->GetTickResolution();
		const FFrameNumber StartFrame = (StartSec * Tick).FloorToFrame();
		const FFrameNumber EndFrame = (EndSec * Tick).CeilToFrame();

		const int32 Before = Track->GetAllSections().Num();
		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_AddSequenceSection",
												 "Add Sequence Section"));
		Track->Modify();
		UMovieSceneSection* Section = Track->CreateNewSection();
		if (!Section)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' returned no section from CreateNewSection - some track types do not ")
				TEXT("support sections at all. NOTHING was changed."), *Track->GetClass()->GetName()));
			return;
		}
		Section->SetRange(TRange<FFrameNumber>(StartFrame, EndFrame));
		Section->SetRowIndex(JInt(In, TEXT("rowIndex"), 0));
		Track->AddSection(*Section);

		// READ BACK through the track, not the pointer - AddSection is void and some tracks refuse
		// a section they consider overlapping.
		const TArray<UMovieSceneSection*>& Now = Track->GetAllSections();
		const int32 Index = Now.IndexOfByKey(Section);
		if (Index == INDEX_NONE)
		{
			Fail(Out, FString::Printf(
				TEXT("the section was created and '%s' does not list it afterwards - AddSection is ")
				TEXT("void and some track types refuse an overlapping section silently. NOTHING ")
				TEXT("usable was produced."), *Track->GetClass()->GetName()));
			return;
		}
		if (UPackage* Pkg = Seq->GetOutermost()) { Pkg->MarkPackageDirty(); }

		Out->SetStringField(TEXT("guid"), Guid.ToString());
		Out->SetStringField(TEXT("trackClass"), Track->GetClass()->GetName());
		Out->SetNumberField(TEXT("sectionIndex"), Index);
		Out->SetStringField(TEXT("sectionClass"), Section->GetClass()->GetName());
		Out->SetNumberField(TEXT("sectionsBefore"), Before);
		Out->SetNumberField(TEXT("sectionsNow"), Now.Num());
		Out->SetNumberField(TEXT("startTick"), StartFrame.Value);
		Out->SetNumberField(TEXT("endTick"), EndFrame.Value);
		Out->SetNumberField(TEXT("startTime"), StartSec);
		Out->SetNumberField(TEXT("endTime"), EndSec);
		Out->SetNumberField(TEXT("tickResolution"), Tick.AsDecimal());
		Out->SetArrayField(TEXT("channels"), SeqChannelRows(Section));
		Out->SetStringField(TEXT("note"),
			TEXT("the section exists and its channels are EMPTY - it still animates nothing until "
				 "keys are written. channels[] above lists them by the name set_sequence_keys takes. "
				 "Nothing was saved."));
	}

	// --- set_sequence_keys --------------------------------------------------
	void H_set_sequence_keys(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("sequence"), TEXT("guid"), TEXT("binding"),
			  TEXT("trackClass"), TEXT("trackIndex"), TEXT("sectionIndex"), TEXT("channel"),
			  TEXT("keys"), TEXT("replace"), TEXT("confirm") },
			TEXT("path; guid (alias: binding); trackClass or trackIndex; sectionIndex (from ")
			TEXT("add_sequence_section); channel - the channel NAME from that response, e.g. ")
			TEXT("'Location.X'; keys - [{time (SECONDS), value, interp: cubic|linear|constant}]; ")
			TEXT("replace (default false - true clears the channel first); confirm:true"),
			{ { TEXT("frame"), TEXT("key times are SECONDS - the tick conversion is done for you") },
			  { TEXT("channelName"), TEXT("spell it channel") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("set_sequence_keys needs confirm:true. NOTHING was changed."));
			return;
		}
		ULevelSequence* Seq = SeqResolve(In, Out, TEXT("set_sequence_keys"));
		if (!Seq) { return; }
		UMovieScene* Scene = Seq->GetMovieScene();
		if (!Scene) { Fail(Out, TEXT("this sequence has no MovieScene.")); return; }

		FGuid Guid;
		const FString GuidStr = JStrAny(In, { TEXT("guid"), TEXT("binding") });
		if (GuidStr.IsEmpty() || !FGuid::Parse(GuidStr, Guid))
		{
			Fail(Out, TEXT("guid is required. NOTHING was changed."));
			return;
		}
		FString FindError;
		UMovieSceneTrack* Track = SeqFindTrack(Scene, Guid, JStr(In, TEXT("trackClass")),
											   JInt(In, TEXT("trackIndex"), -1), FindError);
		if (!Track) { Fail(Out, FindError + TEXT(" NOTHING was changed.")); return; }

		const int32 SectionIndex = JInt(In, TEXT("sectionIndex"), 0);
		const TArray<UMovieSceneSection*>& Sections = Track->GetAllSections();
		if (!Sections.IsValidIndex(SectionIndex))
		{
			Fail(Out, FString::Printf(
				TEXT("sectionIndex %d is outside this track's %d section(s). Create one with ")
				TEXT("add_sequence_section. NOTHING was changed."), SectionIndex, Sections.Num()));
			return;
		}
		UMovieSceneSection* Section = Sections[SectionIndex];

		const FString ChannelName = JStr(In, TEXT("channel"));
		if (ChannelName.IsEmpty())
		{
			Fail(Out, TEXT("channel is required - the name from add_sequence_section's channels[]. ")
				TEXT("NOTHING was changed."));
			return;
		}

		// Find the channel BY NAME through the proxy, and report what IS there when it misses -
		// a channel name that does not exist is the most likely mistake and a bare failure would
		// leave the caller guessing.
		const FMovieSceneChannelProxy& Proxy = Section->GetChannelProxy();
		FMovieSceneChannel* Channel = nullptr;
		FName ChannelType;
		{
			const FName Wanted(*ChannelName);
			for (const FMovieSceneChannelEntry& Entry : Proxy.GetAllEntries())
			{
				const TArrayView<FMovieSceneChannel* const> Channels = Entry.GetChannels();
#if WITH_EDITOR
				const TArrayView<const FMovieSceneChannelMetaData> Meta = Entry.GetMetaData();
				for (int32 i = 0; i < Channels.Num(); ++i)
				{
					if (Meta.IsValidIndex(i) && Meta[i].Name == Wanted)
					{
						Channel = Channels[i];
						ChannelType = Entry.GetChannelTypeName();
						break;
					}
				}
#endif
				if (Channel) { break; }
			}
		}
		if (!Channel)
		{
			TArray<TSharedPtr<FJsonValue>> Have = SeqChannelRows(Section);
			Out->SetArrayField(TEXT("channelsAvailable"), Have);
			Fail(Out, FString::Printf(
				TEXT("this section has no channel named '%s'. channelsAvailable in this response ")
				TEXT("lists the %d it does have. NOTHING was changed."), *ChannelName, Have.Num()));
			return;
		}

		const TArray<TSharedPtr<FJsonValue>>* KeysJson = nullptr;
		if (!In->TryGetArrayField(TEXT("keys"), KeysJson) || !KeysJson || KeysJson->Num() == 0)
		{
			Fail(Out, TEXT("keys must be a non-empty array of {time, value}. NOTHING was changed."));
			return;
		}

		const FFrameRate Tick = Scene->GetTickResolution();
		const int32 KeysBefore = Channel->GetNumKeys();

		FScopedTransaction Transaction(NSLOCTEXT("MifBridge", "MifBridge_SetSequenceKeys",
												 "Set Sequence Keys"));
		Section->Modify();
		if (JBool(In, TEXT("replace"), false))
		{
			Channel->Reset();
		}

		// TYPED DISPATCH. Every channel type has its own AddKey signature and its own JSON coercion;
		// an unsupported one is named rather than skipped, because a key silently not written is the
		// worst outcome here - the section looks authored and animates nothing.
		int32 Written = 0;
		FString TypeError;
		for (const TSharedPtr<FJsonValue>& KV : *KeysJson)
		{
			const TSharedPtr<FJsonObject>* KO = nullptr;
			if (!KV.IsValid() || !KV->TryGetObject(KO) || !KO) { continue; }
			const TSharedRef<FJsonObject> K = KO->ToSharedRef();
			const double TimeSec = JNum(K, TEXT("time"), 0.0);
			const FFrameNumber Frame = (TimeSec * Tick).RoundToFrame();
			const FString Interp = JStr(K, TEXT("interp")).ToLower();

			if (ChannelType == FMovieSceneDoubleChannel::StaticStruct()->GetFName())
			{
				FMovieSceneDoubleChannel* C = static_cast<FMovieSceneDoubleChannel*>(Channel);
				const double V = JNum(K, TEXT("value"), 0.0);
				if (Interp == TEXT("linear")) { C->AddLinearKey(Frame, V); }
				else if (Interp == TEXT("constant")) { C->AddConstantKey(Frame, V); }
				else { C->AddCubicKey(Frame, V); }
				++Written;
			}
			else if (ChannelType == FMovieSceneFloatChannel::StaticStruct()->GetFName())
			{
				FMovieSceneFloatChannel* C = static_cast<FMovieSceneFloatChannel*>(Channel);
				const float V = static_cast<float>(JNum(K, TEXT("value"), 0.0));
				if (Interp == TEXT("linear")) { C->AddLinearKey(Frame, V); }
				else if (Interp == TEXT("constant")) { C->AddConstantKey(Frame, V); }
				else { C->AddCubicKey(Frame, V); }
				++Written;
			}
			else if (ChannelType == FMovieSceneBoolChannel::StaticStruct()->GetFName())
			{
				FMovieSceneBoolChannel* C = static_cast<FMovieSceneBoolChannel*>(Channel);
				C->GetData().UpdateOrAddKey(Frame, JBool(K, TEXT("value"), false));
				++Written;
			}
			else if (ChannelType == FMovieSceneIntegerChannel::StaticStruct()->GetFName())
			{
				FMovieSceneIntegerChannel* C = static_cast<FMovieSceneIntegerChannel*>(Channel);
				C->GetData().UpdateOrAddKey(Frame, static_cast<int32>(JNum(K, TEXT("value"), 0.0)));
				++Written;
			}
			else if (ChannelType == FMovieSceneStringChannel::StaticStruct()->GetFName())
			{
				FMovieSceneStringChannel* C = static_cast<FMovieSceneStringChannel*>(Channel);
				C->GetData().UpdateOrAddKey(Frame, JStr(K, TEXT("value")));
				++Written;
			}
			else if (ChannelType == FMovieSceneObjectPathChannel::StaticStruct()->GetFName())
			{
				FMovieSceneObjectPathChannel* C = static_cast<FMovieSceneObjectPathChannel*>(Channel);
				const FString ObjPath = JStr(K, TEXT("value"));

				// AN EMPTY PATH IS A REAL KEY. "no object" is what clears a slot, so it is accepted;
				// a path that fails to LOAD is refused. Keying null because someone mistyped a path
				// is the silent wrong answer this endpoint exists to refuse.
				UObject* Obj = nullptr;
				if (!ObjPath.IsEmpty())
				{
					Obj = StaticLoadObject(UObject::StaticClass(), nullptr, *ObjPath);
					if (!Obj)
					{
						TypeError = FString::Printf(
							TEXT("key at %.4fs names '%s', which did not load. An EMPTY value is ")
							TEXT("accepted and keys 'no object'; a path that does not resolve is ")
							TEXT("refused, because keying null for a mistyped path is exactly the ")
							TEXT("silent wrong answer this endpoint refuses elsewhere."),
							TimeSec, *ObjPath);
						break;
					}

					// THE CONSTRAINT. The channel knows what class the bound property expects, and
					// nothing in the engine stops a key of any other class going in - the key value
					// takes a bare UObject*. A section keyed with the wrong class looks authored and
					// resolves at runtime to something the property cannot accept.
					if (UClass* Expected = C->GetPropertyClass())
					{
						if (!Obj->IsA(Expected))
						{
							TypeError = FString::Printf(
								TEXT("key at %.4fs names '%s', which is a %s, but channel '%s' ")
								TEXT("expects a %s. The engine would accept it - the key value takes ")
								TEXT("a bare UObject* - and the section would look authored while ")
								TEXT("resolving to something the property cannot use."),
								TimeSec, *ObjPath, *Obj->GetClass()->GetName(), *ChannelName,
								*Expected->GetName());
							break;
						}
					}
				}
				C->GetData().UpdateOrAddKey(Frame, FMovieSceneObjectPathChannelKeyValue(Obj));
				++Written;
			}
			else
			{
				TypeError = FString::Printf(
					TEXT("channel '%s' is a %s, which this endpoint does not key yet - it handles ")
					TEXT("double, float, bool, integer, string and object-path channels. That ")
					TEXT("covers transforms, most property tracks and visibility. Named rather than ")
					TEXT("skipped, because a key silently not written leaves a section that looks ")
					TEXT("authored and animates nothing."), *ChannelName, *ChannelType.ToString());
				break;
			}
		}
		if (!TypeError.IsEmpty())
		{
			Fail(Out, TypeError + TEXT(" NOTHING was changed."));
			return;
		}

		const int32 KeysAfter = Channel->GetNumKeys();
		if (UPackage* Pkg = Seq->GetOutermost()) { Pkg->MarkPackageDirty(); }

		Out->SetStringField(TEXT("guid"), Guid.ToString());
		Out->SetStringField(TEXT("channel"), ChannelName);
		Out->SetStringField(TEXT("channelType"), ChannelType.ToString());
		Out->SetNumberField(TEXT("sectionIndex"), SectionIndex);
		Out->SetNumberField(TEXT("keysRequested"), KeysJson->Num());
		Out->SetNumberField(TEXT("keysWritten"), Written);
		Out->SetNumberField(TEXT("keysBefore"), KeysBefore);
		// keysAfter is read from the channel, not counted from the request - UpdateOrAddKey REPLACES
		// a key at the same time, so writing three keys at one time leaves one, and reporting the
		// request back would be a number that is not true.
		Out->SetNumberField(TEXT("keysAfter"), KeysAfter);
		Out->SetNumberField(TEXT("tickResolution"), Tick.AsDecimal());
		if (KeysAfter == KeysBefore && !JBool(In, TEXT("replace"), false))
		{
			Out->SetStringField(TEXT("note"),
				TEXT("the key count did not change. UpdateOrAddKey REPLACES a key at the same frame, "
					 "so writing over existing times is not an error - but if you expected new keys, "
					 "check the times against what is already there."));
		}
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the sequence is dirty and NOTHING has been saved."));
	}
}
