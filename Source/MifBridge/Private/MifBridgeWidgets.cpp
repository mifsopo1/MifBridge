// MifBridge — UWidgetBlueprint asset endpoints: "Is Variable" flag, property bindings,
// and widget-tree add/remove. All are transaction-safe (Modify + mutate + MarkStructural);
// none full-compile inline — RunEndpoint's FScopedTransaction wraps them and a full compile
// inside a transaction reinstances the class (crash). Mirrors the engine designer handlers,
// which likewise end at MarkBlueprintAsStructurallyModified.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "WidgetBlueprint.h"                      // UWidgetBlueprint, FDelegateEditorBinding
#include "Blueprint/WidgetTree.h"                 // UWidgetTree::ConstructWidget/FindWidget/RemoveWidget/RootWidget
#include "Blueprint/WidgetBlueprintGeneratedClass.h" // EBindingKind
#include "Components/Widget.h"                     // UWidget::bIsVariable
#include "Components/PanelWidget.h"                // UPanelWidget::AddChild
#include "Components/CanvasPanel.h"                // UCanvasPanel::AddChildToCanvas
#include "Components/CanvasPanelSlot.h"            // UCanvasPanelSlot layout setters
#include "Engine/Blueprint.h"                      // UBlueprint::GetGuidFromClassByFieldName
#include "Engine/BlueprintGeneratedClass.h"        // UBlueprintGeneratedClass (SkeletonGeneratedClass cast)
#include "UObject/UObjectGlobals.h"                // MakeUniqueObjectName
#include "WidgetBlueprintEditorUtils.h"            // Export/ImportWidgetsFromText - the headless copy/paste path
#include "Components/PanelSlot.h"                  // UPanelSlot returned by AddChild/InsertChildAt
#include "UObject/UObjectIterator.h"               // TObjectIterator - find tree-owned widgets the walk misses
#include "Animation/WidgetAnimation.h"              // UWidgetAnimation, FWidgetAnimationBinding
#include "MovieScene.h"                             // UMovieScene: display rate, tick resolution, playback range
#include "Kismet2/BlueprintEditorUtils.h"            // ReplaceVariableReferences - graph refs to the old name
#include "MovieScenePossessable.h"                   // renaming the possessable behind an animation binding
#include "Animation/MovieScene2DTransformTrack.h"    // UMovieScene2DTransformTrack (RenderTransform)
#include "Animation/MovieScene2DTransformSection.h"  // FMovieSceneFloatChannel Translation[2]
#include "Tracks/MovieSceneFloatTrack.h"             // RenderOpacity
#include "Sections/MovieSceneFloatSection.h"         // GetChannel()
#include "Tracks/MovieSceneColorTrack.h"             // ColorAndOpacity
#include "Sections/MovieSceneColorSection.h"         // GetRed/Green/Blue/AlphaChannel()
#include "Tracks/MovieSceneVisibilityTrack.h"        // Visibility (a BOOL track, not a float one)
#include "Sections/MovieSceneBoolSection.h"          // GetChannel() -> FMovieSceneBoolChannel
#include "Channels/MovieSceneBoolChannel.h"          // Reset / AddKeys / GetTimes / GetValues
#include "Channels/MovieSceneFloatChannel.h"         // AddCubicKey / AddLinearKey / AddConstantKey
#include "Tracks/MovieScenePropertyTrack.h"          // SetPropertyNameAndPath
#include "MovieSceneBinding.h"                       // FMovieSceneBinding::GetTracks - object-bound
                                                     // tracks are NOT in UMovieScene::GetTracks()

namespace MifBridge
{
	namespace
	{
		// Seconds -> FFrameNumber in TICK space. The single most load-bearing conversion in this
		// group: a MovieScene stores times as ticks (typically 60000/1), NOT as display frames and
		// NOT as seconds. At 20fps display and 60000 tick resolution, 0.95s is 57000 ticks and frame
		// 19. Feeding it 0.95, or 19, puts the key somewhere else entirely and reports success.
		FFrameNumber SecondsToTicks(const UMovieScene* MovieScene, double Seconds)
		{
			return (Seconds * MovieScene->GetTickResolution()).FrameNumber;
		}

		double TicksToSeconds(const UMovieScene* MovieScene, FFrameNumber Ticks)
		{
			return MovieScene->GetTickResolution().AsSeconds(FFrameTime(Ticks));
		}

		UWidgetAnimation* FindAnimation(UWidgetBlueprint* WBP, const FString& Name)
		{
			for (UWidgetAnimation* Anim : WBP->Animations)
			{
				if (Anim && (Anim->GetFName() == FName(*Name) || Anim->GetDisplayLabel() == Name))
				{
					return Anim;
				}
			}
			return nullptr;
		}

		// Everything a caller needs to VERIFY the animation, not merely that one exists. The time
		// fields are emitted in both ticks and seconds on purpose - a wrong conversion is invisible
		// in one unit and obvious in two.
		TSharedRef<FJsonObject> SerializeAnimation(UWidgetAnimation* Anim)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), Anim->GetFName().ToString());
			J->SetStringField(TEXT("displayLabel"), Anim->GetDisplayLabel());
			// CONST, deliberately: this function only ever reads MS, and a const pointer selects the
			// const GetBindings() overload - the non-const one is UE_DEPRECATED(5.7, "Getting non-const
			// access ... is no longer allowed. Please use const GetBindings()"), same reasoning already
			// applied in MifBridgeSequencerWrite.cpp and MifBridgeSequencer.cpp's describe_level_sequence.
			const UMovieScene* MS = Anim->GetMovieScene();
			if (!MS)
			{
				// Reported rather than skipped: an animation without a MovieScene is exactly the
				// half-created state this endpoint family exists to make impossible.
				J->SetBoolField(TEXT("hasMovieScene"), false);
				return J;
			}
			J->SetBoolField(TEXT("hasMovieScene"), true);
			const FFrameRate Display = MS->GetDisplayRate();
			const FFrameRate Tick = MS->GetTickResolution();
			J->SetStringField(TEXT("displayRate"), FString::Printf(TEXT("%d/%d"), Display.Numerator, Display.Denominator));
			J->SetStringField(TEXT("tickResolution"), FString::Printf(TEXT("%d/%d"), Tick.Numerator, Tick.Denominator));
			const TRange<FFrameNumber> Range = MS->GetPlaybackRange();
			if (Range.HasLowerBound() && Range.HasUpperBound())
			{
				J->SetNumberField(TEXT("startTick"), Range.GetLowerBoundValue().Value);
				J->SetNumberField(TEXT("endTick"), Range.GetUpperBoundValue().Value);
				J->SetNumberField(TEXT("startTime"), TicksToSeconds(MS, Range.GetLowerBoundValue()));
				J->SetNumberField(TEXT("endTime"), TicksToSeconds(MS, Range.GetUpperBoundValue()));
			}
			// TRACKS LIVE IN TWO PLACES. UMovieScene::GetTracks() returns only the ROOT tracks; a
			// track bound to a widget hangs off that binding instead. Counting only the former
			// reported trackCount:0 for an animation with a working, keyed transform track - a false
			// zero in the very field a caller would use to verify the track exists.
			int32 TotalTracks = MS->GetTracks().Num();
			for (const FMovieSceneBinding& Binding : MS->GetBindings())
			{
				TotalTracks += Binding.GetTracks().Num();
			}
			J->SetNumberField(TEXT("trackCount"), TotalTracks);
			J->SetNumberField(TEXT("rootTrackCount"), MS->GetTracks().Num());
			J->SetNumberField(TEXT("possessableCount"), MS->GetPossessableCount());

			TArray<TSharedPtr<FJsonValue>> Bindings;
			for (const FWidgetAnimationBinding& B : Anim->GetBindings())
			{
				TSharedRef<FJsonObject> BJ = MakeShared<FJsonObject>();
				BJ->SetStringField(TEXT("widgetName"), B.WidgetName.ToString());
				BJ->SetStringField(TEXT("animationGuid"), B.AnimationGuid.ToString());
				BJ->SetBoolField(TEXT("isRootWidget"), B.bIsRootWidget);
				// Per-binding detail, so "which widget has which track" is answerable without a
				// second call - and so a track attached to the WRONG binding is visible.
				TArray<TSharedPtr<FJsonValue>> TrackNames;
				for (const FMovieSceneBinding& Binding : MS->GetBindings())
				{
					if (Binding.GetObjectGuid() != B.AnimationGuid) { continue; }
					for (UMovieSceneTrack* Track : Binding.GetTracks())
					{
						if (Track)
						{
							TrackNames.Add(MakeShared<FJsonValueString>(Track->GetClass()->GetName()));
						}
					}
				}
				BJ->SetNumberField(TEXT("trackCount"), TrackNames.Num());
				BJ->SetArrayField(TEXT("tracks"), TrackNames);
				Bindings.Add(MakeShared<FJsonValueObject>(BJ));
			}
			J->SetArrayField(TEXT("bindings"), Bindings);
			return J;
		}
	}

	// Resolve "blueprintId"/"path" and require it to be a UWidgetBlueprint. On failure
	// writes the error into Out and returns null (same convention as ResolveBlueprintField).
	static UWidgetBlueprint* ResolveWidgetBlueprintField(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UBlueprint* Blueprint = ResolveBlueprintField(In, Out);   // already Fail()ed if null
		if (!Blueprint)
		{
			return nullptr;
		}
		UWidgetBlueprint* WBP = Cast<UWidgetBlueprint>(Blueprint);
		if (!WBP)
		{
			Fail(Out, FString::Printf(TEXT("not a Widget Blueprint: '%s'"), *Blueprint->GetPathName()));
			return nullptr;
		}
		if (!WBP->WidgetTree)
		{
			Fail(Out, TEXT("widget blueprint has no WidgetTree"));
			return nullptr;
		}
		return WBP;
	}

	namespace
	{
		// WHAT CAN BE ANIMATED, and by which track. Named explicitly rather than pretending to be
		// generic: a caller asking for anything else is told so, instead of getting an endpoint that
		// appears to work and quietly handles only one track type.
		//
		// These three share FMovieSceneFloatChannel, which is the whole reason they can share one key
		// path. Visibility is deliberately ABSENT - it is a bool channel and needs a different one.
		struct FAnimProperty
		{
			const TCHAR* Name;          // what the caller passes
			const TCHAR* PropertyPath;  // what the MovieScene property track binds to
			const TCHAR* Channels;      // comma-separated, in engine order
			bool bBool;                 // a BOOL channel, so stepped: no interpolation, no tangents
			// WHICH SLOT OF UMovieScene2DTransformSection this property owns, or INDEX_NONE for the
			// properties that are not a 2D transform at all. The four transform families all live on
			// ONE section, so the channel a caller names ("X") is ambiguous without this - see the
			// resolver below, where getting it wrong means silently keying the wrong curve.
			int32 XFormBase;
		};

		// THE FOUR RenderTransform FAMILIES ARE ONE TRACK, NOT FOUR. UMovieScene2DTransformSection
		// carries all seven channels - Translation[2], Rotation, Scale[2], Shear[2]
		// (MovieScene2DTransformSection.h:136-151) - and they all bind to the single "RenderTransform"
		// property. So asking for Scale on a widget that already has a Translation track finds the
		// SAME section and reports createdTrack:false. That is correct rather than a failure, and the
		// endpoint says so explicitly instead of leaving it looking like nothing happened.
		//
		// The engine's own names for these channels are Translation.X/.Y, Angle, Scale.X/.Y and
		// Shear.X/.Y (MovieScene2DTransformSection.cpp:34-67), which is why the rotation family is
		// spelled Angle here and not Rotation.
		const FAnimProperty kAnimProperties[] = {
			{ TEXT("RenderTransform.Translation"), TEXT("RenderTransform"),  TEXT("X,Y"),     false, 0 },
			{ TEXT("RenderTransform.Angle"),       TEXT("RenderTransform"),  TEXT("value"),   false, 2 },
			{ TEXT("RenderTransform.Scale"),       TEXT("RenderTransform"),  TEXT("X,Y"),     false, 3 },
			{ TEXT("RenderTransform.Shear"),       TEXT("RenderTransform"),  TEXT("X,Y"),     false, 5 },
			{ TEXT("RenderOpacity"),               TEXT("RenderOpacity"),    TEXT("value"),   false, INDEX_NONE },
			{ TEXT("ColorAndOpacity"),             TEXT("ColorAndOpacity"),  TEXT("R,G,B,A"), false, INDEX_NONE },
			{ TEXT("Visibility"),                  TEXT("Visibility"),       TEXT("value"),   true,  INDEX_NONE },
		};

		const FAnimProperty* FindAnimProperty(const FString& Name)
		{
			for (const FAnimProperty& P : kAnimProperties)
			{
				if (Name.Equals(P.Name, ESearchCase::IgnoreCase)) { return &P; }
			}
			return nullptr;
		}

		FString SupportedPropertyList()
		{
			TArray<FString> Names;
			for (const FAnimProperty& P : kAnimProperties) { Names.Add(P.Name); }
			return FString::Join(Names, TEXT(", "));
		}

		UClass* TrackClassFor(const FAnimProperty& P)
		{
			const FString Name(P.Name);
			// All four transform families share this one track class, which is exactly why
			// FindPropertySection returns the same section for each of them.
			if (P.XFormBase != INDEX_NONE)                   { return UMovieScene2DTransformTrack::StaticClass(); }
			if (Name == TEXT("RenderOpacity"))               { return UMovieSceneFloatTrack::StaticClass(); }
			if (Name == TEXT("ColorAndOpacity"))             { return UMovieSceneColorTrack::StaticClass(); }
			if (Name == TEXT("Visibility"))                  { return UMovieSceneVisibilityTrack::StaticClass(); }
			return nullptr;
		}

		// The channel a caller named, on whichever section type this property uses. Returns null for
		// a channel that does not belong to this property, which is how a typo becomes a refusal
		// instead of a silent write to the wrong curve.
		// Visibility's counterpart to ResolveChannel. Separate on purpose: a bool channel has no
		// interpolation, so sharing one resolver would mean pretending a tangent mode applies to it.
		FMovieSceneBoolChannel* ResolveBoolChannel(UMovieSceneSection* Section, const FString& Channel)
		{
			UMovieSceneBoolSection* B = Cast<UMovieSceneBoolSection>(Section);
			if (!B) { return nullptr; }
			if (Channel.IsEmpty() || Channel.Equals(TEXT("value"), ESearchCase::IgnoreCase))
			{
				return &B->GetChannel();
			}
			return nullptr;
		}

		// The seven float channels of a 2D transform section, in the engine's own order. Indexed by
		// FAnimProperty::XFormBase plus the axis, and matching ImportEntityImpl's FloatChannel[0..6]
		// exactly (MovieScene2DTransformSection.cpp:261-267) so the mask bit checked below lines up
		// with the channel written.
		FMovieSceneFloatChannel* XFormChannelAt(UMovieScene2DTransformSection* T, int32 Index)
		{
			switch (Index)
			{
				case 0:  return &T->Translation[0];
				case 1:  return &T->Translation[1];
				case 2:  return &T->Rotation;
				case 3:  return &T->Scale[0];
				case 4:  return &T->Scale[1];
				case 5:  return &T->Shear[0];
				case 6:  return &T->Shear[1];
				default: return nullptr;
			}
		}

		// TAKES THE PROPERTY, NOT JUST THE SECTION, and that is the whole point of the change. All
		// four RenderTransform families live on one section, so "X" means Translation[0] for one
		// caller and Scale[0] for another. Resolving from the section alone - which is what this did
		// before Scale, Angle and Shear existed - would have silently keyed translation whenever
		// somebody asked for scale. That is the wrong-curve failure this function's own comment
		// warned about, and adding three properties is what made it reachable.
		FMovieSceneFloatChannel* ResolveChannel(UMovieSceneSection* Section, const FAnimProperty& P,
											   const FString& Channel, int32& OutIndex)
		{
			OutIndex = INDEX_NONE;
			if (UMovieScene2DTransformSection* T = Cast<UMovieScene2DTransformSection>(Section))
			{
				if (P.XFormBase == INDEX_NONE) { return nullptr; }
				// Angle is a single curve, so it takes the same empty-or-"value" spelling every other
				// single-channel property here uses rather than inventing a third convention.
				if (FCString::Strcmp(P.Channels, TEXT("value")) == 0)
				{
					if (Channel.IsEmpty() || Channel.Equals(TEXT("value"), ESearchCase::IgnoreCase))
					{
						OutIndex = P.XFormBase;
						return XFormChannelAt(T, OutIndex);
					}
					return nullptr;
				}
				if (Channel.Equals(TEXT("X"), ESearchCase::IgnoreCase)) { OutIndex = P.XFormBase + 0; }
				else if (Channel.Equals(TEXT("Y"), ESearchCase::IgnoreCase)) { OutIndex = P.XFormBase + 1; }
				else { return nullptr; }
				return XFormChannelAt(T, OutIndex);
			}
			if (UMovieSceneFloatSection* F = Cast<UMovieSceneFloatSection>(Section))
			{
				// A single-channel property. Accept the explicit name and the empty default, so a
				// caller does not have to pass a channel for something that only has one.
				if (Channel.IsEmpty() || Channel.Equals(TEXT("value"), ESearchCase::IgnoreCase))
				{
					return &F->GetChannel();
				}
				return nullptr;
			}
			if (UMovieSceneColorSection* Col = Cast<UMovieSceneColorSection>(Section))
			{
				if (Channel.Equals(TEXT("R"), ESearchCase::IgnoreCase)) { return &Col->GetRedChannel(); }
				if (Channel.Equals(TEXT("G"), ESearchCase::IgnoreCase)) { return &Col->GetGreenChannel(); }
				if (Channel.Equals(TEXT("B"), ESearchCase::IgnoreCase)) { return &Col->GetBlueChannel(); }
				if (Channel.Equals(TEXT("A"), ESearchCase::IgnoreCase)) { return &Col->GetAlphaChannel(); }
				return nullptr;
			}
			return nullptr;
		}

		UWidget* FindWidgetByName(UWidgetBlueprint* WBP, const FString& Name)
		{
			return WBP->WidgetTree ? WBP->WidgetTree->FindWidget(FName(*Name)) : nullptr;
		}

		// The binding a widget already has in this animation, or an invalid guid.
		FGuid ExistingBinding(UWidgetAnimation* Anim, const FString& WidgetName)
		{
			for (const FWidgetAnimationBinding& B : Anim->GetBindings())
			{
				if (B.WidgetName == FName(*WidgetName))
				{
					return B.AnimationGuid;
				}
			}
			return FGuid();
		}

		UMovieSceneTrack* FindPropertyTrack(UWidgetAnimation* Anim, const FGuid& Guid, UClass* TrackClass)
		{
			UMovieScene* MS = Anim->GetMovieScene();
			if (!MS || !Guid.IsValid() || !TrackClass) { return nullptr; }
			for (UMovieSceneTrack* Track : MS->FindTracks(TrackClass, Guid))
			{
				if (Track) { return Track; }
			}
			return nullptr;
		}

		UMovieSceneSection* FindPropertySection(UWidgetAnimation* Anim, const FGuid& Guid, UClass* TrackClass)
		{
			UMovieSceneTrack* Track = FindPropertyTrack(Anim, Guid, TrackClass);
			if (!Track) { return nullptr; }
			for (UMovieSceneSection* Section : Track->GetAllSections())
			{
				if (Section) { return Section; }
			}
			return nullptr;
		}
	}

	// --- add_widget_animation_track -----------------------------------------
	//   in:  { blueprintId | path, animationName, widgetName, property? }
	//   out: { bindingGuid, created, trackClass, sectionClass, ... }
	void H_add_widget_animation_track(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("animationName"), TEXT("widgetName"), TEXT("property") },
			TEXT("blueprintId (alias: path), animationName, widgetName, property "
				 "(RenderTransform.Translation | RenderTransform.Scale | RenderTransform.Angle | "
				 "RenderTransform.Shear | RenderOpacity | ColorAndOpacity; default "
				 "RenderTransform.Translation). The four RenderTransform families share ONE track, "
				 "so asking for a second of them on the same widget reports createdTrack:false - "
				 "the track is already there and carries all seven channels"),
			{ { TEXT("propertyPath"), TEXT("the parameter is 'property'") },
			  { TEXT("channel"), TEXT("a track carries BOTH translation channels; pick X or Y when you key it, in set_widget_animation_keys") },
			  { TEXT("widgetGuid"), TEXT("widgets are addressed by name here — list_tree_widgets shows them") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString AnimName = JStr(In, TEXT("animationName"));
		UWidgetAnimation* Anim = FindAnimation(WBP, AnimName);
		if (!Anim || !Anim->GetMovieScene())
		{
			Fail(Out, FString::Printf(
				TEXT("no animation named '%s' on this widget (list_widget_animations shows what is "
					 "there). NOTHING was created."), *AnimName));
			return;
		}

		const FString Property = JStr(In, TEXT("property"), TEXT("RenderTransform.Translation"));
		const FAnimProperty* PropDef = FindAnimProperty(Property);
		if (!PropDef)
		{
			Fail(Out, FString::Printf(
				TEXT("property '%s' is not supported. Authorable today: %s. (Visibility is a BOOL "
					 "channel and is deliberately absent rather than half-working.) NOTHING was created."),
				*Property, *SupportedPropertyList()));
			return;
		}
		UClass* TrackClass = TrackClassFor(*PropDef);
		if (!TrackClass)
		{
			Fail(Out, FString::Printf(
				TEXT("internal: no track class mapped for '%s'. NOTHING was created."), *Property));
			return;
		}

		const FString WidgetName = JStr(In, TEXT("widgetName"));
		UWidget* Widget = FindWidgetByName(WBP, WidgetName);
		if (!Widget)
		{
			Fail(Out, FString::Printf(
				TEXT("no widget named '%s' in this widget tree. NOTHING was created."), *WidgetName));
			return;
		}
		if (WBP->WidgetTree->RootWidget == Widget)
		{
			// The root branch of BindPossessableObject binds the PREVIEW UUserWidget, which does not
			// exist headless. Refuse rather than write a binding that means something else.
			Fail(Out, TEXT("binding the ROOT widget is not supported headless — the engine binds the "
						   "preview UUserWidget for that case and there is no preview widget here. "
						   "Animate a child widget instead. NOTHING was created."));
			return;
		}

		UMovieScene* MS = Anim->GetMovieScene();
		WBP->Modify();
		Anim->Modify();
		MS->Modify();

		FGuid Guid = ExistingBinding(Anim, WidgetName);
		const bool bNewBinding = !Guid.IsValid();
		if (bNewBinding)
		{
			Guid = MS->AddPossessable(WidgetName, Widget->GetClass());
			// Replicates UWidgetAnimation::BindPossessableObject's plain-widget branch
			// (WidgetAnimation.cpp:189-199) WITHOUT its CastChecked<UUserWidget>(Context) preamble,
			// which would terminate the editor when handed the null context we necessarily have.
			FWidgetAnimationBinding NewBinding;
			NewBinding.AnimationGuid = Guid;
			NewBinding.WidgetName = Widget->GetFName();
			NewBinding.bIsRootWidget = false;
			Anim->AnimationBindings.Add(NewBinding);
		}

		bool bCreatedTrack = false;
		UMovieSceneSection* Section = FindPropertySection(Anim, Guid, TrackClass);
		if (!Section)
		{
			UMovieSceneTrack* Track = MS->AddTrack(TrackClass, Guid);
			if (!Track)
			{
				Fail(Out, FString::Printf(
					TEXT("could not add a %s to that binding. WHAT IS LEFT BEHIND: the widget "
						 "binding, if this call created it — read it back with "
						 "list_widget_animations."), *TrackClass->GetName()));
				return;
			}
			// A property track that does not know its property animates nothing. This is the step
			// that makes the track point at RenderOpacity rather than at nothing in particular.
			if (UMovieScenePropertyTrack* PropTrack = Cast<UMovieScenePropertyTrack>(Track))
			{
				PropTrack->SetPropertyNameAndPath(FName(PropDef->PropertyPath), PropDef->PropertyPath);
			}
			Section = Track->CreateNewSection();
			if (!Section)
			{
				Fail(Out, TEXT("the track was created but produced no section. WHAT IS LEFT BEHIND: "
							   "an empty track on this binding."));
				return;
			}
			// A section with no range evaluates nowhere. Match the animation's playback range so
			// keys inside it actually play.
			Section->SetRange(MS->GetPlaybackRange());
			Track->AddSection(*Section);
			bCreatedTrack = true;
		}

		// Verify by re-finding through the MovieScene rather than trusting the pointers above.
		if (!FindPropertySection(Anim, Guid, TrackClass))
		{
			Fail(Out, TEXT("the track did not attach to the binding. Read the animation back with "
						   "list_widget_animations before retrying."));
			return;
		}

		MarkStructural(WBP);
		Out->SetStringField(TEXT("bindingGuid"), Guid.ToString());
		Out->SetStringField(TEXT("widgetName"), WidgetName);
		Out->SetBoolField(TEXT("createdBinding"), bNewBinding);
		Out->SetBoolField(TEXT("createdTrack"), bCreatedTrack);
		if (!bCreatedTrack && PropDef->XFormBase != INDEX_NONE)
		{
			// Without this a caller asking for Scale on a widget that already has Translation sees
			// createdTrack:false and reasonably concludes nothing happened.
			Out->SetStringField(TEXT("trackNote"),
				TEXT("no new track was needed: the four RenderTransform families - Translation, "
					 "Angle, Scale and Shear - are all channels of ONE UMovieScene2DTransformTrack, "
					 "and this widget already had it. The binding is ready to key with "
					 "set_widget_animation_keys."));
		}
		Out->SetStringField(TEXT("property"), PropDef->Name);
		Out->SetStringField(TEXT("channels"), PropDef->Channels);
		Out->SetStringField(TEXT("trackClass"), TrackClass->GetName());
		Out->SetObjectField(TEXT("animation"), SerializeAnimation(Anim));
	}

	// --- set_widget_animation_keys ------------------------------------------
	//   in:  { blueprintId | path, animationName, widgetName, channel, keys:[{time,value,interp?}], replace? }
	//   out: { channel, keysBefore, keysAfter, keys:[{timeTick,time,value,interp}] }
	void H_set_widget_animation_keys(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("animationName"), TEXT("widgetName"),
			  TEXT("property"), TEXT("channel"), TEXT("keys"), TEXT("replace") },
			TEXT("blueprintId (alias: path), animationName, widgetName, property "
				 "(default RenderTransform.Translation), channel (X/Y for translation, scale and "
				 "shear; omit or 'value' for RenderTransform.Angle and RenderOpacity; R/G/B/A for "
				 "ColorAndOpacity), keys:[{time (SECONDS), value, interp: cubic|linear|constant}], "
				 "replace (bool, default true — clears first). NOTE that X on RenderTransform.Scale "
				 "and X on RenderTransform.Translation are different curves on the same section, so "
				 "the property is what disambiguates them"),
			{ { TEXT("time"), TEXT("times go inside keys[], one per key, in seconds") },
			  { TEXT("tangent"), TEXT("interp:\"cubic\" uses the engine's Auto tangent, which is what the UMG designer produces") },
			  { TEXT("frame"), TEXT("keys are given in SECONDS and converted to tick space for you; list_widget_animations reports both") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString AnimName = JStr(In, TEXT("animationName"));
		UWidgetAnimation* Anim = FindAnimation(WBP, AnimName);
		if (!Anim || !Anim->GetMovieScene())
		{
			Fail(Out, FString::Printf(TEXT("no animation named '%s' on this widget."), *AnimName));
			return;
		}
		const FString Property = JStr(In, TEXT("property"), TEXT("RenderTransform.Translation"));
		const FAnimProperty* PropDef = FindAnimProperty(Property);
		if (!PropDef)
		{
			Fail(Out, FString::Printf(
				TEXT("property '%s' is not supported. Authorable today: %s. NOTHING was changed."),
				*Property, *SupportedPropertyList()));
			return;
		}
		const FString WidgetName = JStr(In, TEXT("widgetName"));
		const FGuid Guid = ExistingBinding(Anim, WidgetName);
		UMovieSceneSection* Section = FindPropertySection(Anim, Guid, TrackClassFor(*PropDef));
		if (!Section)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no %s track in animation '%s' — call add_widget_animation_track first. "
					 "NOTHING was changed."), *WidgetName, PropDef->Name, *AnimName));
			return;
		}

		// Default only makes sense per property: Y for a translation, the single curve for opacity.
		const FString ChannelStr = JStr(In, TEXT("channel"),
			FString(PropDef->Name).Equals(TEXT("RenderTransform.Translation")) ? TEXT("Y") : TEXT(""));

		// ---------------------------------------------------------------- BOOL properties
		// Visibility is a stepped bool channel. It gets its own path rather than being forced through
		// the float one, because "interp" genuinely does not apply and accepting it would be a lie.
		if (PropDef->bBool)
		{
			FMovieSceneBoolChannel* BoolChannel = ResolveBoolChannel(Section, ChannelStr);
			if (!BoolChannel)
			{
				Fail(Out, FString::Printf(
					TEXT("channel '%s' is not one of this property's channels (%s = %s). NOTHING was "
						 "changed."), *ChannelStr, PropDef->Name, PropDef->Channels));
				return;
			}

			const TArray<TSharedPtr<FJsonValue>>* BKeys = nullptr;
			if (!JArray(In, TEXT("keys"), BKeys) || !BKeys)
			{
				Fail(Out, TEXT("keys must be an array of {time, value}. NOTHING was changed."));
				return;
			}

			UMovieScene* BMS = Anim->GetMovieScene();
			const int32 BBefore = BoolChannel->GetNumKeys();
			TArray<FFrameNumber> Times;
			TArray<bool> Values;
			for (int32 i = 0; i < BKeys->Num(); ++i)
			{
				const TSharedPtr<FJsonObject>* Obj = nullptr;
				if (!(*BKeys)[i].IsValid() || !(*BKeys)[i]->TryGetObject(Obj) || !Obj)
				{
					Fail(Out, FString::Printf(
						TEXT("keys[%d] is not an object — each key is {time, value}. NOTHING was "
							 "changed."), i));
					return;
				}
				double Time = 0.0;
				if (!(*Obj)->TryGetNumberField(TEXT("time"), Time))
				{
					Fail(Out, FString::Printf(
						TEXT("keys[%d] needs a numeric 'time' in seconds. NOTHING was changed."), i));
					return;
				}
				// REFUSE interp rather than ignore it. A bool channel is stepped; there is no cubic
				// or linear, and quietly dropping a parameter the caller passed is the defect this
				// module keeps finding in itself.
				FString UnusedInterp;
				if ((*Obj)->TryGetStringField(TEXT("interp"), UnusedInterp))
				{
					Fail(Out, FString::Printf(
						TEXT("keys[%d] sets 'interp', but %s is a BOOL channel and is always stepped — "
							 "there is no cubic or linear here. Remove it. NOTHING was changed."),
						i, PropDef->Name));
					return;
				}
				bool bValue = false;
				double NumValue = 0.0;
				if (!(*Obj)->TryGetBoolField(TEXT("value"), bValue))
				{
					// A caller reaching for 1/0 is being reasonable; take it, and report back what
					// was actually stored so there is no ambiguity about the conversion.
					if (!(*Obj)->TryGetNumberField(TEXT("value"), NumValue))
					{
						Fail(Out, FString::Printf(
							TEXT("keys[%d] needs a boolean 'value' (true/false, or 1/0). NOTHING was "
								 "changed."), i));
						return;
					}
					bValue = (NumValue != 0.0);
				}
				Times.Add(SecondsToTicks(BMS, Time));
				Values.Add(bValue);
			}

			Section->Modify();
			if (JBool(In, TEXT("replace"), true))
			{
				BoolChannel->Reset();
			}
			BoolChannel->AddKeys(Times, Values);
			MarkStructural(WBP);

			TArray<TSharedPtr<FJsonValue>> BWritten;
			TArrayView<const FFrameNumber> BTimes = BoolChannel->GetTimes();
			TArrayView<const bool> BValues = BoolChannel->GetValues();
			for (int32 i = 0; i < BTimes.Num(); ++i)
			{
				TSharedRef<FJsonObject> KJ = MakeShared<FJsonObject>();
				KJ->SetNumberField(TEXT("timeTick"), BTimes[i].Value);
				KJ->SetNumberField(TEXT("time"), TicksToSeconds(BMS, BTimes[i]));
				KJ->SetBoolField(TEXT("value"), BValues[i]);
				BWritten.Add(MakeShared<FJsonValueObject>(KJ));
			}
			Out->SetStringField(TEXT("property"), PropDef->Name);
			Out->SetStringField(TEXT("channel"), TEXT("value"));
			Out->SetBoolField(TEXT("stepped"), true);
			Out->SetNumberField(TEXT("keysBefore"), BBefore);
			Out->SetNumberField(TEXT("keysAfter"), BoolChannel->GetNumKeys());
			Out->SetArrayField(TEXT("keys"), BWritten);
			return;
		}

		int32 XFormIndex = INDEX_NONE;
		FMovieSceneFloatChannel* ChannelPtr = ResolveChannel(Section, *PropDef, ChannelStr, XFormIndex);
		if (!ChannelPtr)
		{
			Fail(Out, FString::Printf(
				TEXT("channel '%s' is not one of this property's channels (%s = %s). NOTHING was "
					 "changed."), *ChannelStr, PropDef->Name, PropDef->Channels));
			return;
		}

		bool bWidenedMask = false;
		const TArray<TSharedPtr<FJsonValue>>* Keys = nullptr;
		if (!JArray(In, TEXT("keys"), Keys) || !Keys)
		{
			Fail(Out, TEXT("keys must be an array of {time, value, interp?}. NOTHING was changed."));
			return;
		}

		UMovieScene* MS = Anim->GetMovieScene();
		FMovieSceneFloatChannel& Channel = *ChannelPtr;
		const int32 Before = Channel.GetNumKeys();

		// PREFLIGHT the whole batch before touching the channel, so a bad key in the middle cannot
		// leave a half-keyed curve behind.
		struct FPendingKey { FFrameNumber Tick; double Time; float Value; FString Interp; };
		TArray<FPendingKey> Pending;
		for (int32 i = 0; i < Keys->Num(); ++i)
		{
			const TSharedPtr<FJsonValue>& V = (*Keys)[i];
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (!V.IsValid() || !V->TryGetObject(Obj) || !Obj)
			{
				Fail(Out, FString::Printf(
					TEXT("keys[%d] is not an object — each key is {time, value, interp?}. NOTHING was "
						 "changed."), i));
				return;
			}
			double Time = 0.0, Value = 0.0;
			if (!(*Obj)->TryGetNumberField(TEXT("time"), Time)
				|| !(*Obj)->TryGetNumberField(TEXT("value"), Value))
			{
				Fail(Out, FString::Printf(
					TEXT("keys[%d] needs a numeric 'time' (seconds) and 'value'. NOTHING was changed."), i));
				return;
			}
			FString Interp = TEXT("cubic");
			(*Obj)->TryGetStringField(TEXT("interp"), Interp);
			Interp = Interp.ToLower();
			if (Interp != TEXT("cubic") && Interp != TEXT("linear") && Interp != TEXT("constant"))
			{
				Fail(Out, FString::Printf(
					TEXT("keys[%d] interp '%s' is not one of cubic, linear, constant. NOTHING was "
						 "changed."), i, *Interp));
				return;
			}
			Pending.Add({ SecondsToTicks(MS, Time), Time, static_cast<float>(Value), Interp });
		}
		// WIDENED HERE, NOT EARLIER. This used to run before the keys array was even parsed, so
		// the four refusal paths above - each of which promises "NOTHING was changed" - left a
		// permanently widened mask and a dirty package behind. The preflight immediately above
		// exists so a bad key cannot leave a half-change; the mask block was added on top of it
		// and broke that guarantee one screen higher up. Nothing below this point can refuse.
		// A MASKED-OFF CHANNEL ACCEPTS KEYS AND ANIMATES NOTHING, which is the worst shape a bug can
		// take here: the write succeeds, the keys read back, and the widget does not move.
		// UMovieScene2DTransformSection::ImportEntityImpl builds its entity from
		//     EnumHasAnyFlags(Channels, ...ScaleX) && Scale[0].HasAnyData()
		// (MovieScene2DTransformSection.cpp:239-267), so a channel whose mask bit is clear is never
		// handed to the evaluation system at all.
		//
		// The section constructor defaults the mask to AllTransform (:126), so a section this plugin
		// created is always fine. One narrowed in the UMG designer is not. The mask is WIDENED rather
		// than refused, because a caller keying a channel has said plainly that they want it
		// animated, and leaving inert keys behind would be obeying the letter of the request while
		// defeating it - but it is widened LOUDLY, reported in the response, because it is a change
		// to the section beyond the keys that were asked for.
		if (UMovieScene2DTransformSection* XForm = Cast<UMovieScene2DTransformSection>(Section))
		{
			static const EMovieScene2DTransformChannel kBits[] = {
				EMovieScene2DTransformChannel::TranslationX, EMovieScene2DTransformChannel::TranslationY,
				EMovieScene2DTransformChannel::Rotation,
				EMovieScene2DTransformChannel::ScaleX,       EMovieScene2DTransformChannel::ScaleY,
				EMovieScene2DTransformChannel::ShearX,       EMovieScene2DTransformChannel::ShearY,
			};
			if (XFormIndex >= 0 && XFormIndex < UE_ARRAY_COUNT(kBits))
			{
				const EMovieScene2DTransformChannel Want = kBits[XFormIndex];
				FMovieScene2DTransformMask Mask = XForm->GetMask();
				if (!EnumHasAnyFlags((EMovieScene2DTransformChannel)Mask.GetChannels(), Want))
				{
					XForm->Modify();
					XForm->SetMask(FMovieScene2DTransformMask(
						(EMovieScene2DTransformChannel)Mask.GetChannels() | Want));
					// VERIFIED by reading the mask back, not by trusting SetMask - the entire reason
					// this block exists is that an unset bit is invisible in the keys themselves.
					if (!EnumHasAnyFlags(
							(EMovieScene2DTransformChannel)XForm->GetMask().GetChannels(), Want))
					{
						Fail(Out, FString::Printf(
							TEXT("channel '%s' of %s is masked off on this section and the mask could "
								 "not be widened, so any keys written would be accepted and would "
								 "animate NOTHING. NOTHING was changed."),
							*ChannelStr, PropDef->Name));
						return;
					}
					bWidenedMask = true;
				}
			}
		}


		Section->Modify();
		if (JBool(In, TEXT("replace"), true))
		{
			Channel.Reset();
		}
		for (const FPendingKey& K : Pending)
		{
			if (K.Interp == TEXT("linear"))        { Channel.AddLinearKey(K.Tick, K.Value); }
			else if (K.Interp == TEXT("constant")) { Channel.AddConstantKey(K.Tick, K.Value); }
			else                                   { Channel.AddCubicKey(K.Tick, K.Value, RCTM_Auto); }
		}

		MarkStructural(WBP);

		// Read the channel back rather than echoing the request. Times in BOTH units, because a wrong
		// conversion is invisible in one and obvious in two.
		TArray<TSharedPtr<FJsonValue>> Written;
		TArrayView<const FFrameNumber> Times = Channel.GetTimes();
		TArrayView<const FMovieSceneFloatValue> Values = Channel.GetValues();
		for (int32 i = 0; i < Times.Num(); ++i)
		{
			TSharedRef<FJsonObject> KJ = MakeShared<FJsonObject>();
			KJ->SetNumberField(TEXT("timeTick"), Times[i].Value);
			KJ->SetNumberField(TEXT("time"), TicksToSeconds(MS, Times[i]));
			KJ->SetNumberField(TEXT("value"), Values[i].Value);
			Written.Add(MakeShared<FJsonValueObject>(KJ));
		}
		Out->SetStringField(TEXT("property"), PropDef->Name);
		Out->SetStringField(TEXT("channel"), ChannelStr.IsEmpty() ? TEXT("value") : *ChannelStr);
		if (bWidenedMask)
		{
			Out->SetBoolField(TEXT("maskWidened"), true);
			Out->SetStringField(TEXT("maskNote"),
				TEXT("this section's transform MASK had that channel switched off, so the keys would "
					 "have been stored and would have animated nothing - the engine only hands a "
					 "channel to the evaluator when its mask bit is set. The bit was turned on. That "
					 "is a change to the section beyond the keys you asked for, which is why it is "
					 "reported rather than done quietly."));
		}
		Out->SetNumberField(TEXT("keysBefore"), Before);
		Out->SetNumberField(TEXT("keysAfter"), Channel.GetNumKeys());
		Out->SetArrayField(TEXT("keys"), Written);
	}

	// --- set_widget_animation_range ------------------------------------------
	//   in:  { blueprintId | path, animationName, startTime?, endTime?, displayRate? }
	//   out: { startTime, endTime, displayRate, keysUnchanged }
	//
	// Exists so that correcting an animation's length does not require removing and recreating it.
	// That sequence was the only way to change a range, and it is what surfaced the
	// remove-then-recreate crash on 2026-08-25 - the reporter wanted 0.5s to become 1.5s and nothing
	// could do it in place.
	void H_set_widget_animation_range(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("animationName"),
			  TEXT("startTime"), TEXT("endTime"), TEXT("displayRate") },
			TEXT("blueprintId (alias: path), animationName, startTime and/or endTime in SECONDS, "
				 "displayRate in frames per second"),
			{ { TEXT("length"), TEXT("give endTime; the range is absolute, not a duration") },
			  { TEXT("keys"), TEXT("this changes the RANGE only - key times are untouched. Use set_widget_animation_keys to move keys") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString AnimName = JStr(In, TEXT("animationName"));
		UWidgetAnimation* Anim = FindAnimation(WBP, AnimName);
		if (!Anim || !Anim->GetMovieScene())
		{
			Fail(Out, FString::Printf(
				TEXT("no animation named '%s' with a MovieScene on this widget. NOTHING was changed."),
				*AnimName));
			return;
		}
		UMovieScene* MS = Anim->GetMovieScene();

		const bool bHasStart = In->HasField(TEXT("startTime"));
		const bool bHasEnd = In->HasField(TEXT("endTime"));
		const bool bHasRate = In->HasField(TEXT("displayRate"));
		if (!bHasStart && !bHasEnd && !bHasRate)
		{
			Fail(Out, TEXT("nothing to change - give at least one of startTime, endTime or displayRate. "
						   "NOTHING was changed."));
			return;
		}

		// Everything is validated before anything is written, so a bad endTime cannot leave a
		// half-applied range behind.
		const FFrameRate OldRate = MS->GetDisplayRate();
		double NewFps = OldRate.AsDecimal();
		if (bHasRate)
		{
			NewFps = JNum(In, TEXT("displayRate"), NewFps);
			if (NewFps <= 0.0)
			{
				Fail(Out, TEXT("displayRate must be a positive number of frames per second. "
							   "NOTHING was changed."));
				return;
			}
		}
		const TRange<FFrameNumber> OldRange = MS->GetPlaybackRange();
		const double OldStart = TicksToSeconds(MS, OldRange.GetLowerBoundValue());
		const double OldEnd = TicksToSeconds(MS, OldRange.GetUpperBoundValue());
		const double NewStart = bHasStart ? JNum(In, TEXT("startTime"), OldStart) : OldStart;
		const double NewEnd = bHasEnd ? JNum(In, TEXT("endTime"), OldEnd) : OldEnd;
		if (NewEnd <= NewStart)
		{
			Fail(Out, FString::Printf(
				TEXT("endTime (%.4f) must be greater than startTime (%.4f). NOTHING was changed."),
				NewEnd, NewStart));
			return;
		}

		MS->Modify();
		if (bHasRate)
		{
			// Display rate is the frame grid the EDITOR shows. Key times live in the MovieScene's tick
			// resolution and are not touched by this, which is why keysUnchanged is reported below.
			MS->SetDisplayRate(FFrameRate(FMath::RoundToInt(NewFps), 1));
		}
		MS->SetPlaybackRange(TRange<FFrameNumber>(
			SecondsToTicks(MS, NewStart),
			SecondsToTicks(MS, NewEnd) + 1));    // +1 as the editor does - the end bound is exclusive
		MS->GetEditorData().WorkStart = NewStart;
		MS->GetEditorData().WorkEnd = NewEnd;

		// Read back through the MovieScene rather than echoing the request.
		const TRange<FFrameNumber> Now = MS->GetPlaybackRange();
		const double GotStart = TicksToSeconds(MS, Now.GetLowerBoundValue());
		const double GotEnd = TicksToSeconds(MS, Now.GetUpperBoundValue());
		if (FMath::Abs(GotStart - NewStart) > 0.001)
		{
			Fail(Out, FString::Printf(
				TEXT("asked for a start of %.4fs and the animation reports %.4fs afterwards. Read it "
					 "back with list_widget_animations before relying on it."), NewStart, GotStart));
			return;
		}

		MarkStructural(WBP);
		Out->SetStringField(TEXT("animation"), AnimName);
		Out->SetNumberField(TEXT("startTime"), GotStart);
		Out->SetNumberField(TEXT("endTime"), GotEnd);
		Out->SetNumberField(TEXT("displayRate"), MS->GetDisplayRate().AsDecimal());
		Out->SetNumberField(TEXT("previousStartTime"), OldStart);
		Out->SetNumberField(TEXT("previousEndTime"), OldEnd);
		// Said explicitly because it is the obvious wrong assumption: changing the range does not
		// rescale the animation, and changing the display rate does not move a single key.
		Out->SetBoolField(TEXT("keysUnchanged"), true);
		Out->SetStringField(TEXT("note"),
			TEXT("the RANGE changed; no key moved. Key times are stored in the MovieScene's tick "
				 "resolution, which is independent of displayRate, so neither a longer range nor a "
				 "different frame rate rescales existing keys - re-key with set_widget_animation_keys "
				 "if you wanted the motion stretched to fit."));
	}

	// --- remove_widget_animation --------------------------------------------
	//   in:  { blueprintId | path, animationName }
	//   out: { removed, remaining }
	//
	// No confirm flag, matching remove_variable and remove_node rather than delete_asset: this edits
	// a blueprint inside RunEndpoint's transaction, which Ctrl-Z undoes. It does not delete an asset.
	void H_remove_widget_animation(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("animationName") },
			TEXT("blueprintId (alias: path), animationName"),
			{ { TEXT("confirm"), TEXT("not needed — this is an undoable blueprint edit, not an asset deletion") },
			  { TEXT("name"), TEXT("the parameter is animationName, to match add_widget_animation_track and set_widget_animation_keys") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString AnimName = JStr(In, TEXT("animationName"));
		UWidgetAnimation* Anim = FindAnimation(WBP, AnimName);
		if (!Anim)
		{
			Fail(Out, FString::Printf(
				TEXT("no animation named '%s' on this widget. NOTHING was removed."), *AnimName));
			return;
		}

		WBP->Modify();

		// FREE THE NAME, not just the array slot. Removing from Animations leaves the UWidgetAnimation
		// ALIVE under the widget blueprint, still owning its object name - so a later
		// add_widget_animation with the same name renames onto a live object and CoreUObject asserts
		// (Obj.cpp:265), which kills the editor. Reported from QOLCrafting_P on 2026-08-25.
		//
		// This is the line the engine's own delete path has and this handler did not
		// (AnimationTabSummoner.cpp:823-829, comment: "Rename the animation and move it to the
		// transient package to avoid collisions"). A null name requests a fresh unique one. The
		// MovieScene is outered to the animation, so it moves with it and needs no separate handling.
		//
		// Before the removal, matching the engine's order: renaming an object still referenced by the
		// array is fine, whereas the reverse leaves a window where the array is short and the name is
		// still taken.
		Anim->Rename(nullptr, GetTransientPackage());
		WBP->Animations.Remove(Anim);

		// Verify by re-finding, not by trusting Remove's return.
		if (FindAnimation(WBP, AnimName))
		{
			Fail(Out, TEXT("the animation is still attached after removal. Read it back with "
						   "list_widget_animations."));
			return;
		}

		// The question a caller actually needs answered before recreating: is the NAME free? Detaching
		// and freeing the name are different things, and only one of them makes recreation safe.
		const bool bNameFree = FindObject<UObject>(WBP, *AnimName) == nullptr;

		MarkStructural(WBP);
		Out->SetStringField(TEXT("removed"), AnimName);
		Out->SetNumberField(TEXT("remaining"), WBP->Animations.Num());
		Out->SetBoolField(TEXT("removedFromAnimationsArray"), true);
		Out->SetBoolField(TEXT("objectNameReusable"), bNameFree);
		if (!bNameFree)
		{
			// Should not happen now, but a stale object of that name from some other route would make
			// recreation crash, and silence here is what made the original bug fatal.
			Out->SetStringField(TEXT("nameNote"), FString::Printf(
				TEXT("the animation was removed, but an object named '%s' still exists under this "
					 "widget, so add_widget_animation with that name would refuse. Something other than "
					 "this endpoint is holding the name."), *AnimName));
		}
	}

	// --- remove_widget_animation_track --------------------------------------
	//   in:  { blueprintId | path, animationName, widgetName, property?, removeBinding? }
	//   out: { removedTrack, removedBinding, animation:{...} }
	void H_remove_widget_animation_track(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("animationName"), TEXT("widgetName"),
			  TEXT("property"), TEXT("removeBinding") },
			TEXT("blueprintId (alias: path), animationName, widgetName, property (default "
				 "RenderTransform.Translation), removeBinding (bool, default false — also drops the "
				 "widget's possessable and AnimationBindings entry)"),
			{ { TEXT("channel"), TEXT("a track carries all of a property's channels; there is no per-channel removal — key it empty instead") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString AnimName = JStr(In, TEXT("animationName"));
		UWidgetAnimation* Anim = FindAnimation(WBP, AnimName);
		if (!Anim || !Anim->GetMovieScene())
		{
			Fail(Out, FString::Printf(
				TEXT("no animation named '%s' on this widget. NOTHING was removed."), *AnimName));
			return;
		}
		const FString Property = JStr(In, TEXT("property"), TEXT("RenderTransform.Translation"));
		const FAnimProperty* PropDef = FindAnimProperty(Property);
		if (!PropDef)
		{
			Fail(Out, FString::Printf(
				TEXT("property '%s' is not supported. Authorable today: %s. NOTHING was removed."),
				*Property, *SupportedPropertyList()));
			return;
		}
		const FString WidgetName = JStr(In, TEXT("widgetName"));
		const FGuid Guid = ExistingBinding(Anim, WidgetName);
		if (!Guid.IsValid())
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not bound in animation '%s'. NOTHING was removed."), *WidgetName, *AnimName));
			return;
		}
		UMovieSceneTrack* Track = FindPropertyTrack(Anim, Guid, TrackClassFor(*PropDef));
		if (!Track)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no %s track in animation '%s'. NOTHING was removed."),
				*WidgetName, PropDef->Name, *AnimName));
			return;
		}

		// THE FOUR TRANSFORM FAMILIES SHARE ONE TRACK, so removing "the Scale track" removes
		// Translation, Angle and Shear with it. That became true on 2026-08-30 when Scale, Angle and
		// Shear were added: before then only Translation mapped to UMovieScene2DTransformTrack and
		// removal was unambiguous. RemoveTrack would have destroyed three families of keys while the
		// response named one - silent data loss, introduced by the feature that made them share.
		//
		// Refused rather than warned, because keys cannot be recovered afterwards and no read
		// endpoint would have shown what went missing. Removing a track that carries only the named
		// family still works, and so does removing an empty one.
		if (PropDef->XFormBase != INDEX_NONE)
		{
			if (UMovieScene2DTransformSection* XForm =
					Cast<UMovieScene2DTransformSection>(
						FindPropertySection(Anim, Guid, TrackClassFor(*PropDef))))
			{
				static const TCHAR* kFamily[] = { TEXT("RenderTransform.Translation"),
												  TEXT("RenderTransform.Translation"),
												  TEXT("RenderTransform.Angle"),
												  TEXT("RenderTransform.Scale"),
												  TEXT("RenderTransform.Scale"),
												  TEXT("RenderTransform.Shear"),
												  TEXT("RenderTransform.Shear") };
				TArray<FString> Casualties;
				int32 CasualtyKeys = 0;
				for (int32 i = 0; i < 7; ++i)
				{
					if (kFamily[i] == FString(PropDef->Name)) { continue; }
					if (FMovieSceneFloatChannel* Ch = XFormChannelAt(XForm, i))
					{
						const int32 N = Ch->GetNumKeys();
						if (N > 0)
						{
							CasualtyKeys += N;
							Casualties.AddUnique(FString(kFamily[i]));
						}
					}
				}
				if (Casualties.Num() > 0)
				{
					Fail(Out, FString::Printf(
						TEXT("'%s' is one of FOUR channel families sharing a single "
							 "UMovieScene2DTransformTrack, and removing the track would also destroy "
							 "%d key(s) belonging to %s. That is unrecoverable and nothing would show "
							 "what went missing, so it is refused. Clear this family's channels with "
							 "set_widget_animation_keys and an empty keys array instead, or remove "
							 "the other families' keys first if you really want the track gone. "
							 "NOTHING was removed."),
						PropDef->Name, CasualtyKeys, *FString::Join(Casualties, TEXT(", "))));
					Out->SetNumberField(TEXT("wouldDestroyKeys"), CasualtyKeys);
					TArray<TSharedPtr<FJsonValue>> Arr;
					for (const FString& C : Casualties)
					{
						Arr.Add(MakeShared<FJsonValueString>(C));
					}
					Out->SetArrayField(TEXT("wouldDestroyFamilies"), Arr);
					return;
				}
			}
		}

		UMovieScene* MS = Anim->GetMovieScene();
		WBP->Modify();
		Anim->Modify();
		MS->Modify();
		MS->RemoveTrack(*Track);

		bool bRemovedBinding = false;
		if (JBool(In, TEXT("removeBinding"), false))
		{
			// BOTH halves, or the binding is half-gone: the possessable lives in the MovieScene and
			// the widget-name mapping lives in UWidgetAnimation::AnimationBindings. Removing one and
			// not the other is the same split that makes a half-created binding animate nothing.
			MS->RemovePossessable(Guid);
			const int32 BindingsDropped = Anim->AnimationBindings.RemoveAll(
				[&Guid](const FWidgetAnimationBinding& B) { return B.AnimationGuid == Guid; });

			// bRemovedBinding was set to true from the fact that the CALLER ASKED, not from anything that
			// happened - so a binding that was not there, or a possessable the MovieScene declined to
			// drop, still came back as removedBinding:true. RemovePossessable returns a bool (MovieScene.h
			// 5.3:447, 5.7:463) and RemoveAll returns a count; both were discarded.
			//
			// Re-queried rather than read off those returns, because the re-query is the BOTH HALVES check
			// the comment above promises: RemovePossessable returning false cannot distinguish "declined"
			// from "was never there", and the widget-name half is not in the MovieScene at all.
			const bool bPossessableGone = (MS->FindPossessable(Guid) == nullptr);
			bRemovedBinding = bPossessableGone && BindingsDropped > 0;
			if (!bRemovedBinding)
			{
				// Not a Fail: the TRACK removal above succeeded and was verified, and that is this
				// endpoint's primary product. Reported as a shortfall so it cannot pass for a clean removal.
				Out->SetStringField(TEXT("bindingWarning"), FString::Printf(
					TEXT("the track was removed, but the BINDING was not fully cleared: the possessable is %s "
						 "and %d widget binding(s) were dropped. A half-removed binding animates nothing and "
						 "looks fine in the designer - read it back with list_widget_animations."),
					bPossessableGone ? TEXT("gone") : TEXT("STILL PRESENT"), BindingsDropped));
			}
		}

		if (FindPropertyTrack(Anim, Guid, TrackClassFor(*PropDef)))
		{
			Fail(Out, TEXT("the track is still on the binding after removal. Read the animation back "
						   "with list_widget_animations."));
			return;
		}

		MarkStructural(WBP);
		Out->SetBoolField(TEXT("removedTrack"), true);
		Out->SetBoolField(TEXT("removedBinding"), bRemovedBinding);
		Out->SetStringField(TEXT("property"), PropDef->Name);
		Out->SetObjectField(TEXT("animation"), SerializeAnimation(Anim));
	}

	// --- rename_tree_widget --------------------------------------------------
	//   in:  { blueprintId | path, widgetName, newName }
	//   out: { renamed, bindingsUpdated, animationBindingsUpdated, possessablesRenamed, ... }
	//
	// Renaming the widget is one line; carrying the name through the five other places that store it
	// is the endpoint. Replicates FWidgetBlueprintEditorUtils::RenameWidget, which cannot be called
	// directly because it requires a live FWidgetBlueprintEditor - the asset open in the designer.
	void H_rename_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("name"), TEXT("newName") },
			TEXT("blueprintId (alias: path), widgetName (alias: name) — the widget to rename, newName"),
			{ { TEXT("oldName"), TEXT("the widget to rename is 'widgetName'; 'newName' is what to call it") },
			  { TEXT("rename"), TEXT("the parameter is newName") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString OldName = JStrAny(In, { TEXT("widgetName"), TEXT("name") });
		const FString NewName = JStr(In, TEXT("newName"));
		if (OldName.IsEmpty() || NewName.IsEmpty())
		{
			Fail(Out, TEXT("widgetName and newName are both required. NOTHING was renamed."));
			return;
		}
		if (OldName == NewName)
		{
			Fail(Out, TEXT("widgetName and newName are the same. NOTHING was renamed."));
			return;
		}
		if (!IsValidIdentifier(NewName))
		{
			Fail(Out, FString::Printf(
				TEXT("newName '%s' is not a valid identifier. NOTHING was renamed."), *NewName));
			return;
		}

		const FName OldFName(*OldName);
		const FName NewFName(*NewName);
		UWidget* Widget = WBP->WidgetTree->FindWidget(OldFName);
		if (!Widget)
		{
			Fail(Out, FString::Printf(
				TEXT("no widget named '%s' in this widget tree (list_tree_widgets shows them). "
					 "NOTHING was renamed."), *OldName));
			return;
		}
		// A collision would produce two widgets answering to one name, which is worse than a refusal.
		if (WBP->WidgetTree->FindWidget(NewFName))
		{
			Fail(Out, FString::Printf(
				TEXT("this widget tree already has a widget named '%s'. NOTHING was renamed."), *NewName));
			return;
		}

		WBP->Modify();
		Widget->Modify();
		WBP->WidgetTree->Modify();

		// 1. the object itself
		Widget->Rename(*NewName, nullptr, REN_DontCreateRedirectors);

		// 2. graph variable and event references. A widget marked IsVariable has a generated member
		// variable, and every Get/Set node and event in the graph refers to it by NAME.
		FBlueprintEditorUtils::ReplaceVariableReferences(WBP, OldFName, NewFName);

		// 3. property bindings, whose ObjectName is the widget name as a STRING
		int32 BindingsUpdated = 0;
		for (FDelegateEditorBinding& B : WBP->Bindings)
		{
			if (B.ObjectName == OldName)
			{
				B.ObjectName = NewName;
				++BindingsUpdated;
			}
		}

		// 4. animation bindings AND the possessables behind them. BOTH HALVES, or the animation
		// compiles, plays, and animates nothing - the same split add_widget_animation_track handles.
		int32 AnimBindingsUpdated = 0, PossessablesRenamed = 0;
		for (UWidgetAnimation* Anim : WBP->Animations)
		{
			if (!Anim) { continue; }
			for (FWidgetAnimationBinding& AB : Anim->AnimationBindings)
			{
				if (AB.WidgetName != OldFName) { continue; }
				AB.WidgetName = NewFName;
				++AnimBindingsUpdated;
				UMovieScene* MS = Anim->GetMovieScene();
				if (!MS) { continue; }
				MS->Modify();
				// Only when the binding is the WIDGET itself. A slot binding's possessable is named
				// for the slot, not the widget, and renaming it would be wrong.
				if (AB.SlotWidgetName == NAME_None)
				{
					if (FMovieScenePossessable* P = MS->FindPossessable(AB.AnimationGuid))
					{
						P->SetName(NewName);
						++PossessablesRenamed;
					}
				}
			}
		}

		// 5. the widget's own navigation bindings
		if (Widget->Navigation)
		{
			Widget->Navigation->TryToRenameBinding(OldFName, NewFName);
		}

		// Verify by re-finding through the tree rather than trusting the Rename call.
		if (!WBP->WidgetTree->FindWidget(NewFName))
		{
			Fail(Out, FString::Printf(
				TEXT("the widget was renamed but '%s' cannot be found in the tree afterwards - read it "
					 "back with list_tree_widgets before doing anything else."), *NewName));
			return;
		}

		MarkStructural(WBP);
		Out->SetBoolField(TEXT("renamed"), true);
		Out->SetStringField(TEXT("oldName"), OldName);
		Out->SetStringField(TEXT("newName"), NewName);
		Out->SetStringField(TEXT("widgetClass"), Widget->GetClass()->GetName());
		// Counts, so the caller can SEE the rename carried through rather than assume it.
		Out->SetNumberField(TEXT("bindingsUpdated"), BindingsUpdated);
		Out->SetNumberField(TEXT("animationBindingsUpdated"), AnimBindingsUpdated);
		Out->SetNumberField(TEXT("possessablesRenamed"), PossessablesRenamed);
		Out->SetStringField(TEXT("note"),
			TEXT("graph variable and event references were rewritten too. NOT done: the UMG designer's "
				 "preview widget and DesiredFocusWidget, both of which need the asset open in the "
				 "designer. Compile to see the rename take effect on the generated class."));
	}

	// --- list_widget_animations ---------------------------------------------
	//   in:  { blueprintId | path }
	//   out: { count, animations:[{name, displayRate, tickResolution, start/endTick, start/endTime, ...}] }
	void H_list_widget_animations(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path) of a Widget Blueprint"),
			{ { TEXT("animationName"), TEXT("this lists them all — there is no single-animation read; the listing carries the full detail for each") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UWidgetAnimation* Anim : WBP->Animations)
		{
			if (Anim)
			{
				Arr.Add(MakeShared<FJsonValueObject>(SerializeAnimation(Anim)));
			}
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("animations"), Arr);
	}

	// --- add_widget_animation -----------------------------------------------
	//   in:  { blueprintId | path, name, startTime?, endTime?, displayRate? }
	//   out: { animation:{...} }
	void H_add_widget_animation(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("name"),
			  TEXT("startTime"), TEXT("endTime"), TEXT("displayRate") },
			TEXT("blueprintId (alias: path), name, startTime (seconds, default 0), endTime (seconds, "
				 "default 1), displayRate (fps, default 20)"),
			{ { TEXT("fps"), TEXT("the parameter is displayRate, in frames per second") },
			  { TEXT("duration"), TEXT("give endTime instead — the range is start..end, not a length") },
			  { TEXT("tickResolution"), TEXT("not settable here; the engine's default is used and list_widget_animations reports it, because keys are authored in TICK space") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }

		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty() || !IsValidIdentifier(Name))
		{
			Fail(Out, FString::Printf(
				TEXT("name must be a valid identifier (got '%s'). NOTHING was created."), *Name));
			return;
		}
		if (FindAnimation(WBP, Name))
		{
			Fail(Out, FString::Printf(
				TEXT("this widget blueprint already has an animation named '%s'. NOTHING was created; "
					 "list_widget_animations shows what is there."), *Name));
			return;
		}

		const double StartTime = JNum(In, TEXT("startTime"), 0.0);
		const double EndTime = JNum(In, TEXT("endTime"), 1.0);
		const int32 Fps = JInt(In, TEXT("displayRate"), 20);
		if (EndTime <= StartTime)
		{
			Fail(Out, FString::Printf(
				TEXT("endTime (%f) must be greater than startTime (%f). NOTHING was created."),
				EndTime, StartTime));
			return;
		}
		if (Fps <= 0)
		{
			Fail(Out, TEXT("displayRate must be a positive number of frames per second. NOTHING was created."));
			return;
		}

		// REFUSE BEFORE MUTATING. Anim->Rename(*Name) below renames onto this outer, and renaming on
		// top of a live object is a CoreUObject assert (Obj.cpp:265) - a dead editor, not an error.
		// FindAnimation only searches WBP->Animations, so it cannot see an animation that was detached
		// from the array while its UObject stayed alive: exactly the debris an older
		// remove_widget_animation left, and what a hand-delete in the UMG designer can leave too.
		if (UObject* Occupant = FindObject<UObject>(WBP, *Name))
		{
			Fail(Out, FString::Printf(
				TEXT("an object named '%s' (a %s) already exists under this widget blueprint, so "
					 "creating one would rename on top of it and CRASH the editor. NOTHING was created. "
					 "If this is a live animation, remove it with remove_widget_animation first - that "
					 "frees the name - or pick another name. If list_widget_animations does NOT show "
					 "it, it is detached debris still holding the name; recreate under a different "
					 "name, or reload the asset to clear it."),
				*Name, *Occupant->GetClass()->GetName()));
			return;
		}

		WBP->Modify();

		// Mirrors AnimationTabSummoner.cpp:589. Order matters: the MovieScene is outered to the
		// ANIMATION, and the animation is not part of the asset until Animations.Add below.
		UWidgetAnimation* Anim = NewObject<UWidgetAnimation>(WBP, FName(), RF_Transactional);
		Anim->SetDisplayLabel(Name);
		Anim->Rename(*Name);
		Anim->MovieScene = NewObject<UMovieScene>(Anim, FName(*Name), RF_Transactional);
		Anim->MovieScene->SetDisplayRate(FFrameRate(Fps, 1));
		Anim->MovieScene->SetPlaybackRange(TRange<FFrameNumber>(
			SecondsToTicks(Anim->MovieScene, StartTime),
			SecondsToTicks(Anim->MovieScene, EndTime) + 1));   // +1 as the editor does — end is exclusive
		Anim->MovieScene->GetEditorData().WorkStart = StartTime;
		Anim->MovieScene->GetEditorData().WorkEnd = EndTime;

		// THE LINE THAT MAKES IT REAL. Without this the animation exists, compiles, and is not in
		// the widget - the failure mode this whole endpoint is written to avoid.
		WBP->Animations.Add(Anim);

		// Verify rather than assume, and specifically verify MEMBERSHIP by re-finding it through the
		// blueprint rather than reusing the pointer we already hold.
		UWidgetAnimation* ReadBack = FindAnimation(WBP, Name);
		if (!ReadBack || !ReadBack->GetMovieScene())
		{
			Fail(Out, TEXT("the animation was created but did not attach to the blueprint. WHAT IS "
						   "LEFT BEHIND: an orphaned UWidgetAnimation object; re-read with "
						   "list_widget_animations before retrying."));
			return;
		}

		MarkStructural(WBP);
		Out->SetObjectField(TEXT("animation"), SerializeAnimation(ReadBack));
	}

	// --- set_widget_is_variable --------------------------------------------
	// Flip UWidget::bIsVariable (public uint8:1 bitfield — there is NO setter; the designer
	// assigns it directly). MarkStructural runs a skeleton-only compile
	// (RegenerateSkeletonOnly) which is what actually synthesises the member FProperty named
	// after the widget's FName — a plain MarkBlueprintAsModified would NOT, and a
	// self-member Get built afterward would stay pinless. Skeleton regen is transaction-safe;
	// we do NOT full-compile here (see file header). Mirrors SWidgetDetailsView::HandleIsVariableChanged.
	void H_set_widget_is_variable(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("isVariable") },
			TEXT("blueprintId (alias: path), widgetName, isVariable (default true)"),
			{ { TEXT("name"), TEXT("the widget parameter is called widgetName — the widget's FName in the tree, not its display label") },
			  { TEXT("widget"), TEXT("spell it widgetName") },
			  { TEXT("variableName"), TEXT("not settable here — the generated member variable is ALWAYS named after the widget itself; rename the widget to rename the variable") } }))
		{
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		const FString WidgetName = JStr(In, TEXT("widgetName"));
		UWidget* Widget = WBP->WidgetTree->FindWidget(FName(*WidgetName));   // operate on TEMPLATE widget
		if (!Widget)
		{
			Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s'"), *WidgetName));
			return;
		}
		const bool bIsVariable = JBool(In, TEXT("isVariable"), true);

		Widget->Modify();
		Widget->bIsVariable = bIsVariable;   // public bitfield; no SetIsVariable exists
		MarkStructural(WBP);                 // MarkBlueprintAsStructurallyModified -> skeleton regen -> FProperty exists

		Out->SetStringField(TEXT("widgetName"), Widget->GetFName().ToString());
		Out->SetBoolField(TEXT("isVariable"), bIsVariable);
		// The generated variable name is ALWAYS Widget->GetFName() (never the display label).
		Out->SetStringField(TEXT("variableName"), Widget->GetFName().ToString());
	}

	// --- add_widget_binding -------------------------------------------------
	// Push an editor-time FDelegateEditorBinding (widget.PropertyName -> pure UFUNCTION
	// FunctionName on the UserWidget). Identity is (ObjectName, PropertyName) only —
	// operator== ignores FunctionName — so Remove-then-AddUnique replaces any existing
	// bind on that property (exactly the designer's OnAddBinding sequence). SourcePath is
	// left EMPTY so the runtime binds via ScriptDelegate->BindUFunction(FunctionName)
	// (the fallback path). MemberGuid is resolved for rename-safety but is optional.
	void H_add_widget_binding(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyName"), TEXT("functionName") },
			TEXT("blueprintId (alias: path), widgetName, propertyName, functionName - all four required"),
			{ { TEXT("property"), TEXT("spell it propertyName (the widget property to drive, e.g. \"Text\")") },
			  { TEXT("function"), TEXT("spell it functionName (a pure UFUNCTION on the user widget, e.g. \"GetText\")") },
			  { TEXT("widget"), TEXT("spell it widgetName") },
			  { TEXT("kind"), TEXT("not settable — this endpoint only writes function bindings (EBindingKind::Function)") },
			  { TEXT("sourcePath"), TEXT("not settable — SourcePath is deliberately left empty so the runtime binds via BindUFunction(functionName)") } }))
		{
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		const FString WidgetName   = JStr(In, TEXT("widgetName"));
		const FString PropertyName = JStr(In, TEXT("propertyName"));
		const FString FunctionName = JStr(In, TEXT("functionName"));
		if (WidgetName.IsEmpty() || PropertyName.IsEmpty() || FunctionName.IsEmpty())
		{
			Fail(Out, TEXT("widgetName, propertyName and functionName are all required"));
			return;
		}
		// Target widget must exist in the tree or SanitizeBindings silently drops the bind on compile.
		if (!WBP->WidgetTree->FindWidget(FName(*WidgetName)))
		{
			Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s' (binding would be dropped on compile)"), *WidgetName));
			return;
		}

		WBP->Modify();

		FDelegateEditorBinding Binding;
		Binding.ObjectName   = WidgetName;                 // Object->GetName() — the member variable name
		Binding.PropertyName = FName(*PropertyName);       // e.g. "Text"
		Binding.FunctionName = FName(*FunctionName);       // e.g. "GetText"
		Binding.Kind         = EBindingKind::Function;
		// Leave SourceProperty = NAME_None and SourcePath empty -> runtime BindUFunction(FunctionName).

		// Optional rename-safety GUID: resolve the function graph on the skeleton class.
		// Invalid/zero GUID is fine — ToRuntimeBinding then uses the literal FunctionName.
		if (UBlueprintGeneratedClass* Skel = Cast<UBlueprintGeneratedClass>(WBP->SkeletonGeneratedClass))
		{
			UBlueprint::GetGuidFromClassByFieldName<UFunction>(Skel, Binding.FunctionName, Binding.MemberGuid);
		}

		WBP->Bindings.Remove(Binding);      // clears any prior bind on (WidgetName, PropertyName)
		WBP->Bindings.AddUnique(Binding);
		MarkStructural(WBP);

		Out->SetStringField(TEXT("widgetName"), WidgetName);
		Out->SetStringField(TEXT("propertyName"), PropertyName);
		Out->SetStringField(TEXT("functionName"), FunctionName);
		Out->SetNumberField(TEXT("bindingCount"), WBP->Bindings.Num());
		// The runtime FDelegateRuntimeBinding materialises only at the next FULL compile/cook.
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// --- remove_widget_binding ----------------------------------------------
	// Remove by identity (ObjectName + PropertyName only — a stub with just those two set
	// matches via operator==). Mirrors OnRemoveBinding.
	void H_remove_widget_binding(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyName") },
			TEXT("blueprintId (alias: path), widgetName, propertyName - both required"),
			{ { TEXT("functionName"), TEXT("not part of the identity — a binding is removed by widgetName + propertyName alone, whatever function it points at") },
			  { TEXT("property"), TEXT("spell it propertyName") },
			  { TEXT("widget"), TEXT("spell it widgetName") } }))
		{
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		const FString WidgetName   = JStr(In, TEXT("widgetName"));
		const FString PropertyName = JStr(In, TEXT("propertyName"));
		if (WidgetName.IsEmpty() || PropertyName.IsEmpty())
		{
			Fail(Out, TEXT("widgetName and propertyName are required"));
			return;
		}

		WBP->Modify();

		FDelegateEditorBinding Key;
		Key.ObjectName   = WidgetName;
		Key.PropertyName = FName(*PropertyName);
		const int32 Removed = WBP->Bindings.Remove(Key);   // == ignores FunctionName/Kind/SourcePath
		MarkStructural(WBP);

		// REMOVING NOTHING MEANS THE CALLER NAMED SOMETHING THAT IS NOT THERE, and they should hear
		// about it. Not treated as harmless idempotence: FDelegateRuntimeBinding's operator== matches
		// on ObjectName and PropertyName only (it ignores FunctionName, Kind and SourcePath), so a
		// zero here is a widgetName or propertyName that matched no binding - a typo, or a widget that
		// was renamed - rather than a binding that was already gone.
		//
		// This project's other removers report a miss as a failure, and consistency matters more than
		// the abstract argument for idempotence: a caller who cannot tell "removed it" from "there was
		// nothing to remove" will assume the first.
		if (Removed == 0)
		{
			Fail(Out, FString::Printf(
				TEXT("no binding on widget '%s' for property '%s' - nothing was removed. Bindings are "
					 "matched on widget name and property name only, so check both spellings; "
					 "There is no endpoint that LISTS widget property bindings. %d binding(s) "
					 "remain, unchanged."),
				*WidgetName, *PropertyName, WBP->Bindings.Num()));
			return;
		}
		Out->SetNumberField(TEXT("removed"), Removed);
		Out->SetNumberField(TEXT("bindingCount"), WBP->Bindings.Num());
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// --- add_tree_widget ----------------------------------------------------
	// ConstructWidget into the tree, then either set it as RootWidget (asRoot / empty tree)
	// or AddChild it to an existing UPanelWidget parent. Mirrors the SHierarchyViewItem drop
	// path (SetFlags(RF_Transactional)+Modify on tree AND parent, AddChild, MarkStructural).
	// Runtime render requires a recompile (the compiler duplicates WidgetTree into the
	// generated class); the designer shows it live without one.
	void H_add_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetClass"), TEXT("class"),
			  TEXT("name"), TEXT("parentName"), TEXT("asRoot"),
			  TEXT("x"), TEXT("y"), TEXT("autoSize") },
			TEXT("blueprintId (alias: path), widgetClass (alias: class), name (optional, uniquified on collision), ")
			TEXT("parentName or asRoot, and canvas-slot placement x, y, autoSize (default true)"),
			{ { TEXT("widgetName"), TEXT("the NEW widget's name parameter is called name; widgetName is only a response field") },
			  { TEXT("className"), TEXT("the class parameter is called widgetClass (alias: class)") },
			  { TEXT("parent"), TEXT("spell it parentName — the FName of a UPanelWidget already in the tree") },
			  { TEXT("position"), TEXT("pass the canvas-slot position as separate numbers x and y") },
			  { TEXT("size"), TEXT("not implemented — the canvas slot is auto-sized; set the slot's Size with set_property after adding") },
			  { TEXT("slot"), TEXT("slot properties beyond x/y/autoSize are not settable here — use set_property on the created widget's Slot") } }))
		{
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		UWidgetTree* Tree = WBP->WidgetTree;

		// STRICT — an empty widgetClass used to resolve to the widget blueprint's OWN class, which
		// IS a UWidget subclass, so the guard passed and the tree got a self-referencing child.
		UClass* WidgetClass = ResolveClassStrictField(In, { TEXT("widgetClass"), TEXT("class") }, WBP, Out);
		if (!WidgetClass)
		{
			return;
		}
		if (!WidgetClass->IsChildOf(UWidget::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not a UWidget class: '%s'"), *WidgetClass->GetName()));
			return;
		}

		// Optional explicit name; uniquify against the tree if it collides (mirrors WidgetTemplateClass).
		FName WidgetName = NAME_None;
		const FString NameStr = JStr(In, TEXT("name"));
		if (!NameStr.IsEmpty())
		{
			WidgetName = FName(*NameStr);
			if (Tree->FindWidget(WidgetName))
			{
				WidgetName = MakeUniqueObjectName(Tree, WidgetClass, WidgetName);
			}
		}

		const bool bAsRoot = JBool(In, TEXT("asRoot"), false);
		const FString ParentName = JStr(In, TEXT("parentName"));

		// Decide placement BEFORE constructing, so we can fail cleanly.
		UPanelWidget* Parent = nullptr;
		const bool bRootCase = bAsRoot || (Tree->RootWidget == nullptr && ParentName.IsEmpty());
		if (bRootCase)
		{
			if (Tree->RootWidget != nullptr)
			{
				Fail(Out, TEXT("tree already has a root; pass parentName to add as a child, or remove the root first"));
				return;
			}
		}
		else
		{
			// THE CAST IS LOAD-BEARING, and it is a UE 5.7 build break without it.
			//
			// RootWidget is TObjectPtr<UWidget> in BOTH trees (WidgetTree.h:125 in 5.3, :142 in 5.7)
			// while FindWidget returns a raw UWidget*. 5.3's compiler settings accept the mixed
			// ternary; 5.7 rejects it outright:
			//     error C2445: result type of conditional expression is ambiguous:
			//     types 'TObjectPtr<UWidget>' and 'UWidget *' can be converted to multiple common types
			// Casting the first branch forces both to UWidget* and compiles on either.
			//
			// This is a THIRD direction for the trap in docs/02_GOTCHAS.md section 14. That section
			// covers symbols 5.7 deleted and symbols 5.7 added; this is neither - the code is identical
			// and legal in both, and 5.7 is simply STRICTER about it. Nothing warns on 5.3, so it can
			// only be found by building on 5.7, which is how Andre found it.
			UWidget* ParentWidget = ParentName.IsEmpty()
				? static_cast<UWidget*>(Tree->RootWidget)
				: Tree->FindWidget(FName(*ParentName));
			if (!ParentWidget)
			{
				Fail(Out, FString::Printf(TEXT("parent widget not found: '%s'"), *ParentName));
				return;
			}
			Parent = Cast<UPanelWidget>(ParentWidget);
			if (!Parent)
			{
				Fail(Out, FString::Printf(TEXT("parent '%s' is not a panel (cannot hold children)"), *ParentWidget->GetName()));
				return;
			}
		}

		Tree->SetFlags(RF_Transactional);
		Tree->Modify();

		// Placement keys are meaningful only on a canvas slot. Checked BEFORE the widget is constructed
		// so a request that cannot be honoured does not leave anything behind: adding to a VerticalBox
		// with x:100, y:50 used to return ok:true having ignored both.
		const bool bWantsPlacement = JHasAny(In, { TEXT("x"), TEXT("y"), TEXT("autoSize") });
		if (bWantsPlacement && bRootCase)
		{
			Fail(Out, TEXT("x/y/autoSize position a widget inside a CanvasPanel slot; the ROOT widget has no slot. ")
				TEXT("Add a CanvasPanel as the root first, then add this widget to it."));
			return;
		}
		UWidget* NewWidget = Tree->ConstructWidget<UWidget>(WidgetClass, WidgetName);
		if (!NewWidget)
		{
			Fail(Out, TEXT("ConstructWidget returned null"));
			return;
		}

		if (bRootCase)
		{
			Tree->RootWidget = NewWidget;                 // no SetRootWidget(); assign the public field
		}
		else
		{
			Parent->SetFlags(RF_Transactional);
			Parent->Modify();
			UPanelSlot* Slot = Parent->AddChild(NewWidget); // null if panel is single-child and full
			if (!Slot)
			{
				// ConstructWidget already ran, so failing here left an orphan UWidget in the tree's
				// outer. RunEndpoint cancels the transaction on ok:false, but the object itself is not
				// transaction-managed, so mark it garbage explicitly rather than relying on that.
				NewWidget->MarkAsGarbage();
				Fail(Out, FString::Printf(TEXT("AddChild failed on parent '%s' (single-child panel already full?)"), *Parent->GetName()));
				return;
			}
			// Optional canvas placement (a fresh UCanvasPanelSlot already defaults to top-left anchors).
			UCanvasPanelSlot* CSlot = Cast<UCanvasPanelSlot>(Slot);
			// The slot type is only knowable AFTER AddChild (UPanelWidget::GetSlotClass is protected,
			// so it cannot be asked in advance from outside the module). That made this check
			// non-atomic: it returned ok:false having already parented the widget, and the caller
			// found "BadLine" sitting in the tree after a failed call. Unwind before failing, so a
			// rejected request genuinely leaves nothing behind.
			if (!CSlot && bWantsPlacement)
			{
				Parent->RemoveChild(NewWidget);
				Tree->RemoveWidget(NewWidget);
			}
			if (!CSlot && bWantsPlacement)
			{
				NewWidget->MarkAsGarbage();
				Fail(Out, FString::Printf(
					TEXT("x/y/autoSize apply to a CanvasPanel slot, but '%s' is a %s and gave this child a %s — they would ")
					TEXT("have been ignored. Remove them, or parent this widget to a CanvasPanel. Nothing was created."),
					*Parent->GetName(), *Parent->GetClass()->GetName(), *Slot->GetClass()->GetName()));
				return;
			}
			if (CSlot)
			{
				CSlot->SetPosition(FVector2D(JNum(In, TEXT("x")), JNum(In, TEXT("y"))));
				if (JBool(In, TEXT("autoSize"), true))
				{
					CSlot->SetAutoSize(true);
				}
			}
		}

		MarkStructural(WBP);

		Out->SetStringField(TEXT("widgetName"), NewWidget->GetFName().ToString());
		Out->SetStringField(TEXT("widgetClass"), WidgetClass->GetPathName());
		Out->SetBoolField(TEXT("asRoot"), bRootCase);
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// --- remove_tree_widget -------------------------------------------------
	// UWidgetTree::RemoveWidget handles all three cases (child / root / named-slot).
	void H_remove_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("confirm") },
			TEXT("blueprintId (alias: path), widgetName, confirm=true - required because this removes ")
			TEXT("the widget's WHOLE SUBTREE in one call, same as every other remove_* endpoint's gate"),
			{ { TEXT("name"), TEXT("the widget parameter is called widgetName") },
			  { TEXT("widget"), TEXT("spell it widgetName") },
			  { TEXT("recursive"), TEXT("not a parameter — RemoveWidget always takes the widget's whole subtree with it") } }))
		{
			return;
		}
		// Added 2026-08-29, on Andre's explicit call: every other remover in this family
		// (remove_component, remove_variable, remove_function, remove_event_dispatcher) requires
		// confirm=true, and this one deletes a whole subtree - sometimes several widgets - in a
		// single call without it. Left as a known, flagged inconsistency for days
		// (tools/FEATURE_PARITY_SPEC.md's own "Deliberately not pursuing" entry) specifically because
		// it is a judgement call that could break an existing caller's script, not a bug to silently
		// fix - asked, and Andre said add it.
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_tree_widget requires confirm=true - it removes the widget's whole ")
						  TEXT("subtree in one call. Nothing was removed."));
			return;
		}

		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP)
		{
			return;
		}
		const FString WidgetName = JStr(In, TEXT("widgetName"));
		UWidget* Widget = WBP->WidgetTree->FindWidget(FName(*WidgetName));
		if (!Widget)
		{
			Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s'"), *WidgetName));
			return;
		}

		// WHAT ELSE GOES WITH IT. RemoveWidget takes the widget's whole SUBTREE - the reject-hint above
		// says so for anyone who passes recursive:true - but the response said only removed:true, so
		// removing one container and removing a container holding twelve widgets were indistinguishable
		// answers. duplicate_tree_widget in this same family reports `created` and `clonedCount`; the
		// destructive half should be at least as forthcoming as the constructive one.
		//
		// NAMES are collected, not pointers, and BEFORE the call: afterwards the widgets are out of the
		// tree and there is nothing left to walk.
		TArray<UWidget*> Doomed;
		UWidgetTree::GetChildWidgets(Widget, Doomed);   // includes Widget itself
		TArray<TSharedPtr<FJsonValue>> DoomedNames;
		DoomedNames.Add(MakeShared<FJsonValueString>(WidgetName));
		for (const UWidget* W : Doomed)
		{
			if (W && W != Widget)
			{
				DoomedNames.Add(MakeShared<FJsonValueString>(W->GetFName().ToString()));
			}
		}

		WBP->WidgetTree->SetFlags(RF_Transactional);
		WBP->WidgetTree->Modify();
		const bool bRemoved = WBP->WidgetTree->RemoveWidget(Widget);
		MarkStructural(WBP);

		Out->SetBoolField(TEXT("removed"), bRemoved);
		Out->SetStringField(TEXT("widgetName"), WidgetName);
		if (bRemoved)
		{
			Out->SetNumberField(TEXT("removedCount"), DoomedNames.Num());
			Out->SetArrayField(TEXT("removedWidgets"), DoomedNames);
			if (DoomedNames.Num() > 1)
			{
				Out->SetStringField(TEXT("note"), FString::Printf(
					TEXT("%d widget(s) were removed - '%s' and its whole subtree. RemoveWidget is always ")
					TEXT("recursive; there is no option to keep the children."),
					DoomedNames.Num(), *WidgetName));
			}
		}
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// ---------------------------------------------------------------------------
	// Tree topology: list, duplicate, wrap, move.
	//
	// These exist because the tree was effectively write-only: add created, remove deleted, and nothing
	// could read the shape or rearrange it. Callers were reduced to get_property "Slot" one widget at a
	// time, and only on Is-Variable-flagged widgets, since the rest have no member to address.
	//
	// The engine designer actions (FWidgetBlueprintEditorUtils::DuplicateWidgets / WrapWidgets) take a
	// TSharedRef<FWidgetBlueprintEditor> — an OPEN asset editor — so they are unusable from a headless
	// bridge. ExportWidgetsToText / ImportWidgetsFromText take only the UWidgetBlueprint, so duplicate
	// rides the engine real copy/paste path (carrying the whole subtree and every property value);
	// wrap and move do the panel surgery directly.
	// ---------------------------------------------------------------------------

	namespace
	{
		// True if Candidate is Root or anywhere beneath it. Re-parenting a panel into its own
		// descendant builds a cycle, and the next tree walk never terminates.
		static bool IsSelfOrDescendant(UWidget* Root, UWidget* Candidate)
		{
			if (!Root || !Candidate) { return false; }
			if (Root == Candidate) { return true; }
			UPanelWidget* Panel = Cast<UPanelWidget>(Root);
			if (!Panel) { return false; }
			for (int32 i = 0; i < Panel->GetChildrenCount(); ++i)
			{
				if (IsSelfOrDescendant(Panel->GetChildAt(i), Candidate)) { return true; }
			}
			return false;
		}

		// Detach from whatever holds the widget, reporting where it came from. Handles the ROOT case,
		// which is not a panel child and would otherwise be silently left in place.
		static void DetachFromParent(UWidgetTree* Tree, UWidget* Widget, FString& OutFromParent, int32& OutFromIndex)
		{
			OutFromIndex = INDEX_NONE;
			OutFromParent = TEXT("");
			if (UPanelWidget* Old = Widget->GetParent())
			{
				OutFromParent = Old->GetName();
				OutFromIndex = Old->GetChildIndex(Widget);
				Old->SetFlags(RF_Transactional);
				Old->Modify();
				Old->RemoveChild(Widget);
			}
			else if (Tree->RootWidget == Widget)
			{
				OutFromParent = TEXT("<root>");
				Tree->RootWidget = nullptr;
			}
		}
	}

	// --- list_tree_widgets --------------------------------------------------
	//   in:  { blueprintId | path }
	//   out: { root, count, widgets:[{name,class,parent,index,slotClass,isVariable,isPanel,childCount}] }
	// Read-only. The call that makes the rest of the tree addressable: every other tree endpoint takes a
	// widgetName, and before this there was no way to discover them.
	void H_list_tree_widgets(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path") },
			TEXT("blueprintId (alias: path)"),
			{ { TEXT("widgetName"), TEXT("this endpoint lists the WHOLE tree; there is no per-widget filter") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }
		UWidgetTree* Tree = WBP->WidgetTree;

		// Two sources, unioned, because neither alone tells the truth.
		//   * GetAllWidgets walks from RootWidget via ForWidgetAndChildren, which reads
		//     UPanelWidget::GetChildAt. A panel whose Slots carry a null Content therefore reports a
		//     non-zero GetChildrenCount while contributing NOTHING to the walk — the tree silently
		//     under-reports and a caller believes those widgets do not exist.
		//   * The tree also owns widgets that are not under the root at all (named-slot content,
		//     and anything orphaned by a failed edit).
		// So: take the walk, then sweep every UWidget outered to the tree and mark whatever the walk
		// missed as unreachable. A broken tree becomes a reported number instead of a silent absence.
		TArray<UWidget*> All;
		Tree->GetAllWidgets(All);
		TSet<UWidget*> Reachable(All);

		int32 NullSlots = 0;
		for (TObjectIterator<UWidget> It; It; ++It)
		{
			UWidget* W = *It;
			if (W && IsValid(W) && W->GetOuter() == Tree && !Reachable.Contains(W))
			{
				All.Add(W);
			}
		}

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (UWidget* W : All)
		{
			if (!W) { continue; }
			int32 ChildIndex = INDEX_NONE;
			UPanelWidget* Parent = UWidgetTree::FindWidgetParent(W, ChildIndex);

			TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetStringField(TEXT("name"), W->GetName());
			O->SetStringField(TEXT("class"), W->GetClass()->GetPathName());
			O->SetStringField(TEXT("classShort"), W->GetClass()->GetName());
			O->SetStringField(TEXT("parent"), Parent ? Parent->GetName()
				: (Tree->RootWidget == W ? TEXT("<root>") : TEXT("")));
			O->SetNumberField(TEXT("index"), ChildIndex);
			// The slot CLASS is the useful half: it says which layout properties even exist. A
			// UCanvasPanelSlot takes x/y; a UVerticalBoxSlot does not, which is the whole reason
			// add_tree_widget rejects x/y on a box parent.
			O->SetStringField(TEXT("slotClass"), W->Slot ? W->Slot->GetClass()->GetName() : TEXT(""));
			O->SetBoolField(TEXT("isVariable"), W->bIsVariable != 0);
			O->SetBoolField(TEXT("reachable"), Reachable.Contains(W));
			UPanelWidget* AsPanel = Cast<UPanelWidget>(W);
			O->SetBoolField(TEXT("isPanel"), AsPanel != nullptr);
			if (AsPanel)
			{
				const int32 SlotCount = AsPanel->GetChildrenCount();
				// Count slots whose Content is null. childCount counts SLOTS; a null Content is a slot
				// that exists with nothing in it, which is exactly what makes the walk skip a child.
				int32 Live = 0;
				for (int32 i = 0; i < SlotCount; ++i)
				{
					if (AsPanel->GetChildAt(i)) { ++Live; } else { ++NullSlots; }
				}
				O->SetNumberField(TEXT("childCount"), Live);
				O->SetNumberField(TEXT("slotCount"), SlotCount);
				if (Live != SlotCount) { O->SetNumberField(TEXT("emptySlots"), SlotCount - Live); }
				O->SetBoolField(TEXT("canHaveMultipleChildren"), AsPanel->CanHaveMultipleChildren());
			}
			else
			{
				O->SetNumberField(TEXT("childCount"), 0);
			}
			Arr.Add(MakeShared<FJsonValueObject>(O));
		}

		Out->SetStringField(TEXT("root"), Tree->RootWidget ? Tree->RootWidget->GetName() : TEXT(""));
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetNumberField(TEXT("reachableCount"), Reachable.Num());
		Out->SetArrayField(TEXT("widgets"), Arr);
		if (Arr.Num() != Reachable.Num())
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("%d widget(s) are owned by the tree but NOT reachable from the root - they are listed with reachable:false. They will not render."),
				Arr.Num() - Reachable.Num()));
		}
		if (NullSlots > 0)
		{
			Out->SetNumberField(TEXT("emptySlotTotal"), NullSlots);
			Out->SetStringField(TEXT("slotWarning"), FString::Printf(
				TEXT("%d panel slot(s) exist with NO content. childCount reports live children; slotCount reports slots. A gap between them means an edit left slots behind."),
				NullSlots));
		}
	}

	// --- duplicate_tree_widget ----------------------------------------------
	//   in:  { blueprintId | path, widgetName, parentName?, index? }
	//   out: { created:[...], primary, parent, index }
	// Clones a widget AND its whole subtree through the engine copy/paste text path, so property values
	// and child structure come along. parentName defaults to the source own parent — "duplicate beside
	// the original", which is what the Designer Duplicate does.
	void H_duplicate_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("parentName"), TEXT("index") },
			TEXT("blueprintId (alias: path), widgetName, parentName (optional - defaults to the source own parent), index (optional insert position)"),
			{ { TEXT("newName"), TEXT("the clone name is assigned by the engine paste path to keep it unique; rename afterwards if you need a specific one") },
			  { TEXT("widget"), TEXT("spell it widgetName") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }
		UWidgetTree* Tree = WBP->WidgetTree;

		const FString WidgetName = JStr(In, TEXT("widgetName"));
		if (WidgetName.IsEmpty()) { Fail(Out, TEXT("widgetName is required")); return; }
		UWidget* Source = Tree->FindWidget(FName(*WidgetName));
		if (!Source) { Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s'"), *WidgetName)); return; }

		// Resolve the destination parent BEFORE cloning, so a bad parentName leaves nothing behind.
		const FString ParentName = JStr(In, TEXT("parentName"));
		UPanelWidget* Dest = nullptr;
		if (!ParentName.IsEmpty())
		{
			UWidget* DestWidget = Tree->FindWidget(FName(*ParentName));
			if (!DestWidget) { Fail(Out, FString::Printf(TEXT("parent widget not found: '%s'"), *ParentName)); return; }
			Dest = Cast<UPanelWidget>(DestWidget);
			if (!Dest) { Fail(Out, FString::Printf(TEXT("parent '%s' is not a panel (cannot hold children)"), *ParentName)); return; }
		}
		else
		{
			Dest = Source->GetParent();
			if (!Dest)
			{
				Fail(Out, TEXT("source is the ROOT widget and has no parent to duplicate into - pass parentName"));
				return;
			}
		}
		if (!Dest->CanAddMoreChildren())
		{
			Fail(Out, FString::Printf(TEXT("parent '%s' is a single-child panel and is already full"), *Dest->GetName()));
			return;
		}

		Tree->SetFlags(RF_Transactional);
		Tree->Modify();
		WBP->Modify();

		// Export the SOURCE PLUS EVERY DESCENDANT. Exporting {Source} alone produced a clone whose
		// slots existed but whose Content was null - a VerticalBox reporting slotCount 3 and
		// childCount 0. The slot records travel with the panel; the child widgets only travel if they
		// are in the exported set, so the paste rebuilt the slots pointing at nothing.
		TArray<UWidget*> ToExport;
		UWidgetTree::GetChildWidgets(Source, ToExport);   // includes Source itself
		if (!ToExport.Contains(Source)) { ToExport.Insert(Source, 0); }

		FString Exported;
		FWidgetBlueprintEditorUtils::ExportWidgetsToText(ToExport, Exported);
		TSet<UWidget*> Imported;
		TMap<FName, UWidgetSlotPair*> SlotData;
		FWidgetBlueprintEditorUtils::ImportWidgetsFromText(WBP, Exported, Imported, SlotData);
		if (Imported.Num() == 0)
		{
			Fail(Out, FString::Printf(TEXT("copy/paste of '%s' produced no widgets"), *WidgetName));
			return;
		}

		// Import brings the whole subtree in, but only the TOP of it needs parenting - the children
		// already point at their cloned parents. The top is the one whose parent is outside the set.
		UWidget* Primary = nullptr;
		TArray<TSharedPtr<FJsonValue>> Created;
		for (UWidget* W : Imported)
		{
			if (!W) { continue; }
			Created.Add(MakeShared<FJsonValueString>(W->GetName()));
			if (!Primary && !Imported.Contains(W->GetParent())) { Primary = W; }
		}
		if (!Primary) { Primary = *Imported.CreateIterator(); }

		Dest->SetFlags(RF_Transactional);
		Dest->Modify();
		const int32 WantIndex = JHasAny(In, { TEXT("index") }) ? JInt(In, TEXT("index"), 0) : INDEX_NONE;
		UPanelSlot* Slot = (WantIndex >= 0 && WantIndex <= Dest->GetChildrenCount())
			? Dest->InsertChildAt(WantIndex, Primary)
			: Dest->AddChild(Primary);
		if (!Slot)
		{
			Fail(Out, FString::Printf(TEXT("failed to parent the clone under '%s'"), *Dest->GetName()));
			return;
		}

		MarkStructural(WBP);
		Out->SetArrayField(TEXT("created"), Created);
		Out->SetStringField(TEXT("primary"), Primary->GetName());
		Out->SetStringField(TEXT("parent"), Dest->GetName());
		Out->SetNumberField(TEXT("index"), Dest->GetChildIndex(Primary));
		Out->SetStringField(TEXT("slotClass"), Slot->GetClass()->GetName());
		Out->SetNumberField(TEXT("sourceSubtreeSize"), ToExport.Num());
		Out->SetNumberField(TEXT("clonedCount"), Created.Num());
		if (Created.Num() != ToExport.Num())
		{
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("cloned %d widget(s) from a %d-widget subtree - the clone is INCOMPLETE. Verify with list_tree_widgets (emptySlots)."),
				Created.Num(), ToExport.Num()));
		}
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// --- wrap_tree_widget ---------------------------------------------------
	//   in:  { blueprintId | path, widgetName, wrapperClass, wrapperName? }
	//   out: { wrapper, wrapped, parent, index, wasRoot }
	// The Designer "Wrap With": insert a new panel where the widget sits, then move the widget inside it.
	// Handles the ROOT case, which has no parent slot to inherit.
	void H_wrap_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("wrapperClass"), TEXT("wrapperName") },
			TEXT("blueprintId (alias: path), widgetName, wrapperClass (a UPanelWidget class), wrapperName (optional)"),
			{ { TEXT("class"), TEXT("spell it wrapperClass - the PANEL to wrap with, not the widget being wrapped") },
			  { TEXT("panelClass"), TEXT("spell it wrapperClass") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }
		UWidgetTree* Tree = WBP->WidgetTree;

		const FString WidgetName = JStr(In, TEXT("widgetName"));
		if (WidgetName.IsEmpty()) { Fail(Out, TEXT("widgetName is required")); return; }
		UWidget* Target = Tree->FindWidget(FName(*WidgetName));
		if (!Target) { Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s'"), *WidgetName)); return; }

		UClass* WrapperClass = ResolveClassStrictField(In, { TEXT("wrapperClass") }, nullptr, Out);
		if (!WrapperClass) { return; }
		if (!WrapperClass->IsChildOf(UPanelWidget::StaticClass()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is not a UPanelWidget - only a panel can wrap a widget (try CanvasPanel, VerticalBox, HorizontalBox, Overlay, SizeBox, Border)"),
				*WrapperClass->GetName()));
			return;
		}
		if (WrapperClass->HasAnyClassFlags(CLASS_Abstract))
		{
			Fail(Out, FString::Printf(TEXT("'%s' is abstract and cannot be constructed"), *WrapperClass->GetName()));
			return;
		}

		// Capture the original position BEFORE any mutation.
		UPanelWidget* OldParent = Target->GetParent();
		const bool bTargetIsRoot = (Tree->RootWidget == Target);
		const int32 OldIndex = OldParent ? OldParent->GetChildIndex(Target) : INDEX_NONE;
		if (!OldParent && !bTargetIsRoot)
		{
			Fail(Out, FString::Printf(TEXT("'%s' has no parent and is not the root - it is orphaned and cannot be wrapped"), *WidgetName));
			return;
		}

		Tree->SetFlags(RF_Transactional);
		Tree->Modify();
		WBP->Modify();

		const FString WrapperName = JStr(In, TEXT("wrapperName"));
		UPanelWidget* Wrapper = Cast<UPanelWidget>(WrapperName.IsEmpty()
			? Tree->ConstructWidget<UWidget>(WrapperClass)
			: Tree->ConstructWidget<UWidget>(WrapperClass, FName(*WrapperName)));
		if (!Wrapper) { Fail(Out, TEXT("failed to construct the wrapper panel")); return; }

		if (OldParent)
		{
			OldParent->SetFlags(RF_Transactional);
			OldParent->Modify();
			OldParent->RemoveChild(Target);
			// Put the wrapper back at the SAME index so sibling order survives.
			if (!OldParent->InsertChildAt(OldIndex, Wrapper))
			{
				OldParent->AddChild(Wrapper);
			}
		}
		else
		{
			Tree->RootWidget = Wrapper;
		}

		if (!Wrapper->AddChild(Target))
		{
			Fail(Out, FString::Printf(TEXT("wrapper '%s' refused the child (single-child panel already full?)"), *Wrapper->GetName()));
			return;
		}

		MarkStructural(WBP);
		Out->SetStringField(TEXT("wrapper"), Wrapper->GetName());
		Out->SetStringField(TEXT("wrapperClass"), Wrapper->GetClass()->GetName());
		Out->SetStringField(TEXT("wrapped"), Target->GetName());
		Out->SetStringField(TEXT("parent"), OldParent ? OldParent->GetName() : TEXT("<root>"));
		Out->SetNumberField(TEXT("index"), OldParent ? OldParent->GetChildIndex(Wrapper) : 0);
		Out->SetBoolField(TEXT("wasRoot"), bTargetIsRoot);
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}

	// --- move_tree_widget ---------------------------------------------------
	//   in:  { blueprintId | path, widgetName, parentName | asRoot, index? }
	//   out: { widget, fromParent, fromIndex, toParent, index }
	// Reparent an EXISTING widget. add_tree_widget creates and remove_tree_widget deletes; there was no
	// way to move one, so rearranging meant delete + recreate, losing every property already set.
	void H_move_tree_widget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("parentName"), TEXT("asRoot"), TEXT("index"),
			  TEXT("replaceRoot"), TEXT("confirm") },
			TEXT("blueprintId (alias: path), widgetName, parentName (the new parent panel) OR asRoot:true (+ replaceRoot:true if a root already exists), index (optional position within the new parent)"),
			{ { TEXT("newParent"), TEXT("spell it parentName") },
			  { TEXT("x"), TEXT("move changes PARENTAGE only; set slot layout afterwards with set_property on the widget Slot") },
			  { TEXT("y"), TEXT("move changes PARENTAGE only; set slot layout afterwards with set_property on the widget Slot") } }))
		{
			return;
		}
		UWidgetBlueprint* WBP = ResolveWidgetBlueprintField(In, Out);
		if (!WBP) { return; }
		UWidgetTree* Tree = WBP->WidgetTree;

		const FString WidgetName = JStr(In, TEXT("widgetName"));
		if (WidgetName.IsEmpty()) { Fail(Out, TEXT("widgetName is required")); return; }
		UWidget* Target = Tree->FindWidget(FName(*WidgetName));
		if (!Target) { Fail(Out, FString::Printf(TEXT("widget not found in tree: '%s'"), *WidgetName)); return; }

		const bool bAsRoot = JBool(In, TEXT("asRoot"), false);
		const FString ParentName = JStr(In, TEXT("parentName"));
		if (!bAsRoot && ParentName.IsEmpty())
		{
			Fail(Out, TEXT("pass parentName (the new parent panel) or asRoot:true"));
			return;
		}

		UPanelWidget* Dest = nullptr;
		if (!bAsRoot)
		{
			UWidget* DestWidget = Tree->FindWidget(FName(*ParentName));
			if (!DestWidget) { Fail(Out, FString::Printf(TEXT("parent widget not found: '%s'"), *ParentName)); return; }
			Dest = Cast<UPanelWidget>(DestWidget);
			if (!Dest) { Fail(Out, FString::Printf(TEXT("parent '%s' is not a panel (cannot hold children)"), *ParentName)); return; }

			// The check that matters: parenting a panel under itself or its own descendant builds a
			// cycle, and the next tree walk never returns.
			if (IsSelfOrDescendant(Target, Dest))
			{
				Fail(Out, FString::Printf(
					TEXT("cannot move '%s' into '%s' - that is itself or one of its own descendants, which would create a cycle"),
					*WidgetName, *ParentName));
				return;
			}
			if (Target->GetParent() != Dest && !Dest->CanAddMoreChildren())
			{
				Fail(Out, FString::Printf(TEXT("parent '%s' is a single-child panel and is already full"), *ParentName));
				return;
			}
		}
		else if (Tree->RootWidget == Target)
		{
			Fail(Out, TEXT("widget is already the root"));
			return;
		}
		else if (Tree->RootWidget != nullptr)
		{
			// Promoting to root DISPLACES whatever is there, and the displaced root plus its entire
			// subtree stop being part of the widget hierarchy - measured: a 12-widget tree became 5.
			// That is a delete wearing a move's clothing, so it needs the same explicit opt-in every
			// other destructive endpoint here demands. An earlier version only emitted a warning, and
			// the warning was wrong as well: it claimed the old root was "still in the tree object".
			if (!JBoolAny(In, { TEXT("replaceRoot"), TEXT("confirm") }, false))
			{
				TArray<UWidget*> Doomed;
				UWidgetTree::GetChildWidgets(Tree->RootWidget, Doomed);
				Fail(Out, FString::Printf(
					TEXT("asRoot would displace the current root '%s' and drop it and its %d-widget subtree out of the hierarchy. ")
					TEXT("Pass replaceRoot:true to accept that, or move '%s' under an existing panel instead."),
					*Tree->RootWidget->GetName(), Doomed.Num(), *WidgetName));
				return;
			}
		}

		Tree->SetFlags(RF_Transactional);
		Tree->Modify();
		WBP->Modify();
		Target->SetFlags(RF_Transactional);
		Target->Modify();

		// Size the displaced subtree BEFORE detaching. Measured after, the target has already left its
		// old parent, so promoting the only child of the root reported "0-widget subtree" on the accept
		// path while the refusal path (which runs first, undetached) correctly said 2.
		int32 DisplacedSize = 0;
		UWidget* PrevRootSnapshot = Tree->RootWidget;
		if (bAsRoot && PrevRootSnapshot && PrevRootSnapshot != Target)
		{
			TArray<UWidget*> Doomed;
			UWidgetTree::GetChildWidgets(PrevRootSnapshot, Doomed);
			DisplacedSize = Doomed.Num();
		}

		FString FromParent;
		int32 FromIndex = INDEX_NONE;
		DetachFromParent(Tree, Target, FromParent, FromIndex);

		int32 NewIndex = INDEX_NONE;
		if (bAsRoot)
		{
			// Whatever was root becomes orphaned unless the caller re-homes it. Say so rather than
			// silently dropping it out of the visible tree.
			UWidget* PrevRoot = Tree->RootWidget;
			const int32 Displaced = DisplacedSize;   // snapshot taken before detach
			Tree->RootWidget = Target;
			if (PrevRoot && PrevRoot != Target)
			{
				Out->SetStringField(TEXT("displacedRoot"), PrevRoot->GetName());
				Out->SetNumberField(TEXT("displacedSubtreeSize"), Displaced);
				Out->SetStringField(TEXT("warning"), FString::Printf(
					TEXT("'%s' and its %d-widget subtree are no longer in the hierarchy and will not render. This was accepted via replaceRoot."),
					*PrevRoot->GetName(), Displaced));
			}
			NewIndex = 0;
		}
		else
		{
			Dest->SetFlags(RF_Transactional);
			Dest->Modify();
			const int32 WantIndex = JHasAny(In, { TEXT("index") }) ? JInt(In, TEXT("index"), 0) : INDEX_NONE;
			UPanelSlot* Slot = (WantIndex >= 0 && WantIndex <= Dest->GetChildrenCount())
				? Dest->InsertChildAt(WantIndex, Target)
				: Dest->AddChild(Target);
			if (!Slot)
			{
				Fail(Out, FString::Printf(TEXT("failed to parent '%s' under '%s' - the widget is now DETACHED; re-add it"),
					*WidgetName, *Dest->GetName()));
				return;
			}
			NewIndex = Dest->GetChildIndex(Target);
			Out->SetStringField(TEXT("slotClass"), Slot->GetClass()->GetName());
		}

		MarkStructural(WBP);
		Out->SetStringField(TEXT("widget"), Target->GetName());
		Out->SetStringField(TEXT("fromParent"), FromParent);
		Out->SetNumberField(TEXT("fromIndex"), FromIndex);
		Out->SetStringField(TEXT("toParent"), bAsRoot ? TEXT("<root>") : Dest->GetName());
		Out->SetNumberField(TEXT("index"), NewIndex);
		Out->SetBoolField(TEXT("needsCompileToApply"), true);
	}
}
